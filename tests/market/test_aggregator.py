import random
from datetime import datetime, timedelta

import pytest

from backend.config.loader import load_config
from backend.context.builder import MarketContextBuilder
from backend.core.enums import Timeframe
from backend.market.candles.aggregator import (
    AggregationError,
    NotClosedCandleError,
    OutOfOrderCandleError,
    TimeframeAggregator,
)
from tests.helpers import NY, m1

TFS = (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4)


def _agg(**kw):
    return TimeframeAggregator("MNQ", load_config().session, **kw)


def run(start: datetime, minutes: int, agg=None, skip=()):
    """Feed `minutes` consecutive 1M candles opening at `start` (NY wall clock); deterministic prices."""
    agg = agg or _agg()
    closed = []
    for k in range(minutes):
        t = start + timedelta(minutes=k)
        if k in skip:
            continue
        o = 100.0 + k
        closed += agg.ingest(m1(t.year, t.month, t.day, t.hour, t.minute, o=o, h=o + 2, l=o - 1, c=o + 1, v=10.0))
    return agg, closed


# ---------------------------------------------------------------------------------------
# Mandatory: incomplete bar isolation
# ---------------------------------------------------------------------------------------
def test_incomplete_hourly_bar_is_not_visible_at_0935():
    agg, _ = run(datetime(2026, 1, 5, 8, 0, tzinfo=NY), 95)  # 08:00 .. 09:34 candles -> as_of 09:35
    assert agg.as_of == datetime(2026, 1, 5, 9, 35, tzinfo=NY)

    ctx = MarketContextBuilder(agg).build()
    hourly = ctx.candles["1h"]
    assert len(hourly) == 1
    bar = hourly[-1]
    assert bar.open_time == datetime(2026, 1, 5, 8, 0, tzinfo=NY)
    assert bar.close_time == datetime(2026, 1, 5, 9, 0, tzinfo=NY)
    assert bar.is_closed
    # the 09:00-10:00 bar exists only as the developing bar, and is not in the context
    dev = agg.developing_bar(Timeframe.H1)
    assert dev.open_time == datetime(2026, 1, 5, 9, 0, tzinfo=NY) and dev.is_closed is False
    assert all(b.open_time != dev.open_time for tf_bars in ctx.candles.values() for b in tf_bars if b.timeframe == Timeframe.H1)
    assert all(b.is_closed and b.close_time <= ctx.as_of for bars in ctx.candles.values() for b in bars)


def test_closed_hour_bar_has_exact_ohlcv_of_its_minutes():
    agg, _ = run(datetime(2026, 1, 5, 8, 0, tzinfo=NY), 95)
    bar = agg.last_closed(Timeframe.H1)
    # minutes k=0..59: open=100+k, high=open+2, low=open-1, close=open+1, volume 10
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (100.0, 161.0, 99.0, 160.0, 600.0)


def test_developing_bar_is_separate_and_partial():
    agg, _ = run(datetime(2026, 1, 5, 9, 0, tzinfo=NY), 11)  # 09:00..09:10
    assert [b.open_time.minute for b in agg.closed_bars(Timeframe.M5)] == [0, 5]
    dev = agg.developing_bar(Timeframe.M5)
    assert dev.open_time.minute == 10 and dev.is_closed is False and dev.volume == 10.0
    assert agg.closed_bars(Timeframe.H1) == [] and agg.developing_bar(Timeframe.H1) is not None


def test_bar_closes_exactly_when_its_last_minute_arrives():
    agg, closed = run(datetime(2026, 1, 5, 8, 0, tzinfo=NY), 59)  # through 08:58
    assert agg.closed_bars(Timeframe.H1) == []
    just = agg.ingest(m1(2026, 1, 5, 8, 59, o=200))
    assert [b.timeframe for b in just if b.timeframe == Timeframe.H1] == [Timeframe.H1]


# ---------------------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------------------
def test_5m_15m_1h_alignment():
    agg, closed = run(datetime(2026, 1, 5, 9, 0, tzinfo=NY), 61)
    starts = {tf: [b.open_time.strftime("%H:%M") for b in agg.closed_bars(tf)] for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1)}
    assert starts[Timeframe.M5][:3] == ["09:00", "09:05", "09:10"] and len(starts[Timeframe.M5]) == 12
    assert starts[Timeframe.M15] == ["09:00", "09:15", "09:30", "09:45"]
    assert starts[Timeframe.H1] == ["09:00"]


def test_4h_bars_are_anchored_at_1800_et():
    agg, _ = run(datetime(2026, 1, 5, 10, 0, tzinfo=NY), 240)
    bar = agg.last_closed(Timeframe.H4)
    assert bar.open_time == datetime(2026, 1, 5, 10, 0, tzinfo=NY)
    assert bar.close_time == datetime(2026, 1, 5, 14, 0, tzinfo=NY)
    agg2, _ = run(datetime(2026, 1, 5, 18, 0, tzinfo=NY), 240)
    assert agg2.last_closed(Timeframe.H4).close_time == datetime(2026, 1, 5, 22, 0, tzinfo=NY)
    agg3, _ = run(datetime(2026, 1, 6, 2, 0, tzinfo=NY), 240)
    assert agg3.last_closed(Timeframe.H4).open_time.hour == 2


# ---------------------------------------------------------------------------------------
# Mandatory: session boundary across the 17:00-18:00 ET maintenance halt
# ---------------------------------------------------------------------------------------
def test_maintenance_halt_resets_boundaries_without_misalignment():
    agg = _agg()
    run(datetime(2026, 1, 5, 14, 0, tzinfo=NY), 180, agg)  # 14:00..16:59
    # 16:00 hour and the (halt-truncated) 14:00 4H bar closed the instant 16:59 arrived
    h1 = agg.closed_bars(Timeframe.H1)
    h4 = agg.closed_bars(Timeframe.H4)
    assert [b.open_time.hour for b in h1] == [14, 15, 16]
    assert len(h4) == 1
    assert h4[0].open_time == datetime(2026, 1, 5, 14, 0, tzinfo=NY)
    assert h4[0].close_time == datetime(2026, 1, 5, 17, 0, tzinfo=NY)  # ends at the halt, 3h long
    assert h4[0].volume == 180 * 10.0
    assert agg.developing_bar(Timeframe.H4) is None and agg.developing_bar(Timeframe.H1) is None

    run(datetime(2026, 1, 5, 18, 0, tzinfo=NY), 90, agg)  # 18:00..19:29, after the halt
    dev = agg.developing_bar(Timeframe.H4)
    assert dev.open_time == datetime(2026, 1, 5, 18, 0, tzinfo=NY)  # fresh 4H bar, nothing carried over
    assert dev.close_time == datetime(2026, 1, 5, 22, 0, tzinfo=NY)
    assert dev.open == 100.0  # its own first candle, not the pre-halt session
    assert agg.closed_bars(Timeframe.H1)[-1].open_time.hour == 18

    for tf in TFS:  # invariant: no bar spans the halt
        for b in agg.closed_bars(tf):
            assert not (b.open_time < datetime(2026, 1, 5, 17, 0, tzinfo=NY) < b.close_time)


def test_candle_inside_the_halt_is_rejected():
    agg = _agg()
    with pytest.raises(AggregationError, match="maintenance halt"):
        agg.ingest(m1(2026, 1, 5, 17, 30))


def test_weekend_gap_friday_close_to_sunday_open():
    agg = _agg()
    run(datetime(2026, 1, 9, 14, 0, tzinfo=NY), 180, agg)  # Friday 14:00..16:59
    assert len(agg.closed_bars(Timeframe.H4)) == 1
    run(datetime(2026, 1, 11, 18, 0, tzinfo=NY), 240, agg)  # Sunday 18:00 open
    h4 = agg.closed_bars(Timeframe.H4)
    assert len(h4) == 2
    assert h4[1].open_time == datetime(2026, 1, 11, 18, 0, tzinfo=NY)


def test_dst_spring_forward_keeps_wall_clock_alignment():
    agg = _agg()
    run(datetime(2026, 3, 6, 14, 0, tzinfo=NY), 180, agg)  # Friday, EST
    run(datetime(2026, 3, 8, 18, 0, tzinfo=NY), 240, agg)  # Sunday after the 02:00 clock change, EDT
    bar = agg.closed_bars(Timeframe.H4)[-1]
    assert bar.open_time.hour == 18 and bar.open_time.utcoffset() == timedelta(hours=-4)
    agg2, _ = run(datetime(2026, 3, 9, 6, 0, tzinfo=NY), 240)
    assert agg2.last_closed(Timeframe.H4).open_time.hour == 6


# ---------------------------------------------------------------------------------------
# Gaps and input discipline
# ---------------------------------------------------------------------------------------
def test_missing_minutes_close_the_bar_when_a_later_bucket_arrives():
    agg = _agg()
    run(datetime(2026, 1, 5, 9, 0, tzinfo=NY), 57, agg)  # 09:00..09:56 (09:57-09:59 missing)
    assert agg.closed_bars(Timeframe.H1) == []
    agg.ingest(m1(2026, 1, 5, 10, 0, o=500))
    bar = agg.last_closed(Timeframe.H1)
    assert bar.open_time.hour == 9 and bar.close_time.hour == 10
    assert bar.close == 100.0 + 56 + 1  # last close it actually had, nothing invented


def test_out_of_order_and_duplicate_candles_rejected():
    agg = _agg()
    agg.ingest(m1(2026, 1, 5, 9, 5))
    with pytest.raises(OutOfOrderCandleError):
        agg.ingest(m1(2026, 1, 5, 9, 5))
    with pytest.raises(OutOfOrderCandleError):
        agg.ingest(m1(2026, 1, 5, 9, 4))


def test_developing_candle_is_rejected():
    with pytest.raises(NotClosedCandleError):
        _agg().ingest(m1(2026, 1, 5, 9, 5, closed=False))


def test_wrong_symbol_or_timeframe_rejected():
    agg = _agg()
    with pytest.raises(AggregationError):
        agg.ingest(m1(2026, 1, 5, 9, 5, symbol="ES"))
    bar5 = TimeframeAggregator  # noqa: F841
    from backend.core.models.candle import Candle
    five = Candle(symbol="MNQ", timeframe=Timeframe.M5, open_time=datetime(2026, 1, 5, 9, 0, tzinfo=NY),
                  close_time=datetime(2026, 1, 5, 9, 5, tzinfo=NY), open=1, high=2, low=1, close=2)
    with pytest.raises(AggregationError):
        agg.ingest(five)


def test_history_can_be_capped():
    agg = _agg(max_bars=3)
    run(datetime(2026, 1, 5, 9, 0, tzinfo=NY), 60, agg)
    assert len(agg.closed_bars(Timeframe.M5)) == 3 and len(agg.closed_bars(Timeframe.M1)) == 3


# ---------------------------------------------------------------------------------------
# No-lookahead properties
# ---------------------------------------------------------------------------------------
def _random_stream(seed=7):
    rnd = random.Random(seed)
    candles, price = [], 18000.0
    for day, (h0, h1) in ((5, (8, 12)), (5, (14, 17)), (5, (18, 24)), (6, (0, 3))):
        start = datetime(2026, 1, day, h0, 0, tzinfo=NY)
        for k in range((h1 - h0) * 60):
            if rnd.random() < 0.06:  # sporadic missing minutes
                continue
            t = start + timedelta(minutes=k)
            o = price
            c = o + rnd.uniform(-3, 3)
            candles.append(m1(t.year, t.month, t.day, t.hour, t.minute, o=o, h=max(o, c) + rnd.uniform(0, 2),
                              l=min(o, c) - rnd.uniform(0, 2), c=c, v=rnd.uniform(1, 20)))
            price = c
    return candles


def _closed_snapshot(agg):
    return {tf: agg.closed_bars(tf) for tf in TFS}


def test_prefix_stability_bars_never_depend_on_future_candles():
    stream = _random_stream()
    full = _agg()
    for c in stream:
        full.ingest(c)
    full_bars = _closed_snapshot(full)

    for cut in range(50, len(stream), 37):
        part = _agg()
        for c in stream[:cut]:
            part.ingest(c)
        for tf in TFS:
            got = part.closed_bars(tf)
            assert got == full_bars[tf][: len(got)], f"{tf} changed after more data arrived (cut={cut})"
            assert all(b.is_closed and b.close_time <= part.as_of for b in got)


def test_every_closed_bar_equals_a_batch_recomputation_from_its_minutes():
    stream = _random_stream(seed=11)
    agg = _agg()
    for c in stream:
        agg.ingest(c)
    for tf in TFS:
        for bar in agg.closed_bars(tf):
            inside = [c for c in stream if bar.open_time <= c.open_time < bar.close_time]
            assert inside, "closed bar with no source minutes"
            assert bar.open == inside[0].open and bar.close == inside[-1].close
            assert bar.high == max(c.high for c in inside) and bar.low == min(c.low for c in inside)
            assert bar.volume == pytest.approx(sum(c.volume for c in inside))
