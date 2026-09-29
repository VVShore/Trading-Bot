"""
Phase 2 milestone: raw data -> normalizer -> closed 1M candles -> aggregator -> MarketContext
-> strategy, with no lookahead at any step.
"""
import csv
from datetime import datetime, timedelta

from backend.config.loader import load_config
from backend.context.builder import MarketContextBuilder
from backend.core.enums import SetupConditionStatus, Timeframe
from backend.data.normalizer import BarFieldMap, BarNormalizer
from backend.data.pipeline import MarketDataPipeline
from backend.data.provider import CsvBarProvider, InMemoryProvider
from backend.data.ticks import Tick, TickCandleBuilder
from backend.market.candles.aggregator import TimeframeAggregator
from backend.strategies.ny_continuation_v1 import NyAmHtfContinuationV1
from tests.helpers import NY

CONFIG = load_config()


def _write_csv(path, start: datetime, minutes: int, extra_rows=()):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "o", "h", "l", "c", "v", "final"])
        for k in range(minutes):
            t = start + timedelta(minutes=k)
            o = 18000.0 + k
            w.writerow([t.strftime("%Y-%m-%d %H:%M:%S"), o, o + 2, o - 1, o + 1, 10, "true"])
        for row in extra_rows:
            w.writerow(row)


def _pipeline(provider):
    fm = BarFieldMap(timestamp="ts", open="o", high="h", low="l", close="c", volume="v", is_closed="final")
    normalizer = BarNormalizer("MNQ", field_map=fm, source_tz="America/New_York")
    agg = TimeframeAggregator("MNQ", CONFIG.session)
    return MarketDataPipeline(provider, normalizer, agg, MarketContextBuilder(agg), CONFIG.session), agg


def test_csv_replay_builds_htf_context_without_lookahead(tmp_path):
    csv_path = tmp_path / "mnq_1m.csv"
    _write_csv(csv_path, datetime(2026, 1, 5, 8, 0), 150)  # 08:00 .. 10:29
    pipeline, agg = _pipeline(CsvBarProvider(csv_path))

    contexts = list(pipeline.run())
    assert len(contexts) == 150 and pipeline.stats.candles_ingested == 150

    # Every context, at every step, holds only closed bars that were already complete at as_of.
    for ctx in contexts:
        for bars in ctx.candles.values():
            assert all(b.is_closed and b.close_time <= ctx.as_of for b in bars)

    at_0935 = next(c for c in contexts if c.as_of == datetime(2026, 1, 5, 9, 35, tzinfo=NY))
    assert at_0935.candles["1h"][-1].open_time == datetime(2026, 1, 5, 8, 0, tzinfo=NY)  # NOT the 09:00 bar
    assert at_0935.candles["15m"][-1].open_time == datetime(2026, 1, 5, 9, 15, tzinfo=NY)
    assert at_0935.candles["5m"][-1].open_time == datetime(2026, 1, 5, 9, 30, tzinfo=NY)
    assert at_0935.candles["1m"][-1].close_time == at_0935.as_of
    assert at_0935.candles["4h"] == []  # no 4H bar is complete yet -> nothing exposed

    hour = at_0935.candles["1h"][-1]
    assert (hour.open, hour.close, hour.high, hour.low) == (18000.0, 18060.0, 18061.0, 17999.0)


def test_context_flows_into_the_strategy_inside_the_entry_window(tmp_path):
    csv_path = tmp_path / "mnq_1m.csv"
    _write_csv(csv_path, datetime(2026, 1, 5, 8, 0), 95)  # last candle 09:34 -> as_of 09:35
    pipeline, _ = _pipeline(CsvBarProvider(csv_path))
    ctx = list(pipeline.run())[-1]

    setup = NyAmHtfContinuationV1(config=CONFIG).evaluate(ctx)
    window = next(c for c in setup.required_conditions if c.name == "within_execution_window")
    assert window.status == SetupConditionStatus.PASS
    assert setup.decision == "NO_TRADE"  # detectors are Phase 3; the strategy fails closed
    assert setup.evaluated_at == ctx.as_of


def test_developing_and_halt_rows_never_reach_the_aggregator(tmp_path):
    csv_path = tmp_path / "mnq_1m.csv"
    rows = [
        ["2026-01-05 09:30:00", 1, 2, 1, 2, 1, "true"],
        ["2026-01-05 09:31:00", 1, 2, 1, 2, 1, "false"],   # developing update
        ["2026-01-05 17:10:00", 5, 5, 5, 5, 0, "true"],    # vendor filler inside the halt
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "o", "h", "l", "c", "v", "final"])
        w.writerows(rows)
    pipeline, agg = _pipeline(CsvBarProvider(csv_path))
    contexts = list(pipeline.run())
    assert len(contexts) == 1
    assert (pipeline.stats.developing_skipped, pipeline.stats.halt_skipped, pipeline.stats.candles_ingested) == (1, 1, 1)
    assert len(agg.closed_bars(Timeframe.M1)) == 1


def test_halt_crossing_replay_produces_clean_session_boundaries(tmp_path):
    csv_path = tmp_path / "mnq_1m.csv"
    _write_csv(csv_path, datetime(2026, 1, 5, 15, 0), 120)                     # 15:00..16:59
    with open(csv_path, "a", newline="") as f:
        w = csv.writer(f)
        for k in range(60):                                                   # vendor filler 17:00..17:59
            t = datetime(2026, 1, 5, 17, 0) + timedelta(minutes=k)
            w.writerow([t.strftime("%Y-%m-%d %H:%M:%S"), 1, 1, 1, 1, 0, "true"])
        for k in range(65):                                                   # 18:00..19:04 real session
            t = datetime(2026, 1, 5, 18, 0) + timedelta(minutes=k)
            w.writerow([t.strftime("%Y-%m-%d %H:%M:%S"), 20000 + k, 20001 + k, 19999 + k, 20000 + k, 5, "true"])
    pipeline, agg = _pipeline(CsvBarProvider(csv_path))
    contexts = list(pipeline.run())
    assert pipeline.stats.halt_skipped == 60
    last = contexts[-1]
    assert last.candles["4h"][-1].close_time == datetime(2026, 1, 5, 17, 0, tzinfo=NY)  # pre-halt bar ends at the halt
    assert last.candles["1h"][-1].open_time == datetime(2026, 1, 5, 18, 0, tzinfo=NY)   # first post-halt hour
    assert last.candles["1h"][-1].open == 20000.0                                       # unaffected by filler bars


def test_ticks_to_context_end_to_end():
    builder = TickCandleBuilder("MNQ")
    agg = TimeframeAggregator("MNQ", CONFIG.session)
    ctx_builder = MarketContextBuilder(agg)
    start = datetime(2026, 1, 5, 9, 0, tzinfo=NY)
    last_ctx = None
    for k in range(20):                           # 20 minutes of 3 ticks each
        for s, px in ((5, 100.0 + k), (25, 101.0 + k), (55, 100.5 + k)):
            for candle in builder.ingest(Tick(start + timedelta(minutes=k, seconds=s), px, 1.0)):
                agg.ingest(candle)
                last_ctx = ctx_builder.build()
    assert last_ctx is not None
    assert last_ctx.candles["1m"][-1].open_time == start + timedelta(minutes=18)   # minute 19 is still forming
    assert last_ctx.candles["5m"][-1].close_time <= last_ctx.as_of
    assert builder.developing_candle().open_time == start + timedelta(minutes=19)


def test_provider_is_swappable_without_touching_downstream():
    records = [{"ts": "2026-01-05 09:30:00", "o": 1, "h": 2, "l": 1, "c": 2, "v": 1, "final": "true"}]
    pipeline, _ = _pipeline(InMemoryProvider(records))
    assert len(list(pipeline.run())) == 1
