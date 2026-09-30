"""
PipelineOrchestrator, driven bar by bar from raw records through execution.
Uses only production wiring (`build_paper_orchestrator`) plus a small scripted Strategy defined here;
nothing from tests/stubs or the old e2e harness.
"""
import ast
from datetime import date, datetime
from pathlib import Path

import pytest

from backend.config.loader import load_config
from backend.core.enums import StrategyName, TradeSide, TradeStatus
from backend.core.models.order import OrderResult
from backend.core.models.setup import TradeSetup
from backend.core.models.target import Target
from backend.data.normalizer import BarFieldMap, BarNormalizer
from backend.data.provider import InMemoryProvider
from backend.data.ticks import Tick
from backend.decision_log.decisions import DecisionLogger
from backend.decision_log.trades import TradeStore
from backend.execution.events import EXIT_STOP_SAME_BAR, EXIT_TARGET
from backend.execution.paper import PaperBroker
from backend.pipeline import build_paper_orchestrator
from backend.strategies.base import MarketContext, Strategy
from datetime import timedelta
from tests.helpers import NY

FIELDS = BarFieldMap(timestamp="ts", open="o", high="h", low="l", close="c", volume="v", is_closed="final")
LONG = dict(side=TradeSide.LONG, entry=20000.0, stop=19990.0, targets=[20030.0])  # qty 15 at the $300 ceiling
DAY = date(2026, 1, 5)


def at(hh, mm):
    return datetime(2026, 1, 5, hh, mm, tzinfo=NY)


def rec(hh, mm, o, h, l, c, final="true"):
    return {"ts": f"2026-01-05 {hh:02d}:{mm:02d}:00", "o": o, "h": h, "l": l, "c": c, "v": 10, "final": final}


def flat(hh, mm):
    return rec(hh, mm, 20000.0, 20000.5, 19999.5, 20000.0)


def history():  # 08:00..09:34
    return [flat(8 + k // 60, k % 60) for k in range(95)]


class ScriptedStrategy(Strategy):
    """Emits TRADE only when the context's as_of is in `triggers`; records what it was shown."""
    name = "SCRIPTED"
    version = "SCRIPTED-0"

    def __init__(self, config, triggers):
        super().__init__(config)
        self.triggers = triggers
        self.seen = []

    def evaluate(self, context: MarketContext) -> TradeSetup:
        self.seen.append(context)
        common = dict(setup_id=f"scripted-{context.as_of.strftime('%H%M')}", strategy=StrategyName.NY_AM_HTF_CONTINUATION_V1,
                      strategy_version=self.version, symbol=context.symbol, evaluated_at=context.as_of)
        spec = self.triggers.get(context.as_of)
        if spec is None:
            return TradeSetup(**common, decision="NO_TRADE", reason="not scripted")
        return TradeSetup(**common, proposed_side=spec["side"], proposed_entry=spec["entry"], proposed_stop=spec["stop"],
                          targets=[Target(type="t", price=p, source="test") for p in spec["targets"]], decision="TRADE")


def make(tmp_path, records, triggers, **kw):
    config = load_config()
    strategy = ScriptedStrategy(config, triggers)
    orch, broker, daily = build_paper_orchestrator(
        config=config, provider=InMemoryProvider(records),
        normalizer=BarNormalizer("MNQ", field_map=FIELDS, source_tz="America/New_York"),
        strategy=strategy, trading_day=DAY, log_path=tmp_path / "decisions.ndjson",
        trade_store_path=tmp_path / "trades.ndjson", **kw)
    return orch, broker, daily, strategy


def drive(orch, n=None):
    out = []
    while (n is None or len(out) < n) and (r := orch.step()) is not None:
        out.append(r)
    return out


def trail_kinds(tmp_path, setup_id):
    return [e["event_kind"] for e in DecisionLogger(tmp_path / "decisions.ndjson").read_trail(setup_id)]


# ----------------------------------------------------------------------------------------------
# Orchestrator integration: raw data -> execution, one step at a time
# ----------------------------------------------------------------------------------------------
def test_step_runs_raw_bars_through_to_a_closed_trade(tmp_path):
    records = history() + [
        rec(9, 35, 20000.0, 20010.0, 19995.0, 20008.0),   # entry bar: fills at this open, touches neither level
        rec(9, 36, 20008.0, 20031.0, 20001.0, 20030.0),   # reaches the target
        flat(9, 37),
    ]
    orch, broker, daily, strategy = make(tmp_path, records, {at(9, 35): LONG})

    steps = drive(orch, 94)                                   # candles 08:00..09:33
    assert all(s.setup.decision == "NO_TRADE" and s.risk is None for s in steps)

    s = orch.step()                                           # candle 09:34 closes -> decision
    assert s.candle.open_time == at(9, 34) and s.context.as_of == at(9, 35)
    assert s.setup.decision == "TRADE" and s.risk.approved and s.risk.intent.quantity == 15
    assert orch.has_pending_intent and broker.get_position("MNQ") is None   # nothing fills before the next bar

    s = orch.step()                                           # candle 09:35: fills at ITS open
    assert s.entry.filled and s.entry.fill_price == pytest.approx(20000.25) and s.exits == []
    assert broker.get_position("MNQ")["quantity"] == 15 and not orch.has_pending_intent
    assert [t.status for t in broker.open_trades()] == [TradeStatus.OPEN]

    s = orch.step()                                           # candle 09:36 hits the take-profit limit
    (ev,) = s.exits
    assert ev.reason == EXIT_TARGET and ev.result.fill_price == 20030.0 and ev.result.slippage == 0.0
    assert broker.get_position("MNQ") is None
    assert (daily.trades_taken, daily.wins) == (1, 1) and daily.realized_pnl_dollars == pytest.approx(870.3)
    assert TradeStore(tmp_path / "trades.ndjson").read_all() == [ev.trade]

    assert orch.step().skipped is False and orch.step() is None      # 09:37, then the provider is exhausted

    assert trail_kinds(tmp_path, "scripted-0935") == [
        "setup", "risk_decision", "fill_check", "order_result", "trade_opened", "order_result", "trade_closed", "daily_state"]
    trail = DecisionLogger(tmp_path / "decisions.ndjson").read_trail("scripted-0935")
    assert [e["at"] for e in trail] == sorted(e["at"] for e in trail)


def test_strategy_only_ever_sees_closed_bars_known_at_as_of(tmp_path):
    orch, *_ , strategy = make(tmp_path, history() + [flat(9, 35)], {})
    drive(orch)
    for ctx in strategy.seen:
        assert ctx.candles["1m"][-1].close_time == ctx.as_of
        assert all(b.is_closed and b.close_time <= ctx.as_of for bars in ctx.candles.values() for b in bars)


def test_run_yields_every_step_and_cancels_a_dangling_intent_at_the_end(tmp_path):
    records = history()[:95]                                  # data ends on the decision bar
    orch, broker, *_ = make(tmp_path, records, {at(9, 35): LONG})
    results = list(orch.run())
    assert len(results) == 95 and results[-1].risk.approved
    assert not orch.has_pending_intent and broker.get_position("MNQ") is None
    assert "intent_cancelled" in trail_kinds(tmp_path, "scripted-0935")


def test_dropped_rows_are_reported_as_skipped(tmp_path):
    records = [rec(9, 30, 1, 2, 1, 2), rec(9, 31, 1, 2, 1, 2, final="false"), rec(17, 10, 5, 5, 5, 5)]
    orch, *_ = make(tmp_path, records, {})
    results = drive(orch)
    assert [r.skipped for r in results] == [False, True, True]


def test_log_no_trade_can_be_switched_off(tmp_path):
    orch, *_ = make(tmp_path, history() + [flat(9, 35)], {at(9, 35): LONG}, log_no_trade=False)
    drive(orch)
    assert [s.setup_id for s in DecisionLogger(tmp_path / "decisions.ndjson").read_all()] == ["scripted-0935"]


def test_ticks_drive_the_same_cycle(tmp_path):
    orch, broker, *_ = make(tmp_path, [], {at(9, 35): LONG}, use_ticks=True)
    start = at(8, 0)
    results = []
    for k in range(98):                                        # one tick per minute, 08:00 .. 09:37
        results += orch.on_tick(Tick(start + timedelta(minutes=k, seconds=10), 20000.0, 1.0))
    decision = next(r for r in results if r.risk is not None)
    assert decision.context.as_of == at(9, 35) and decision.risk.approved
    assert broker.get_position("MNQ")["quantity"] == 15        # filled at the 09:35 candle's open, built from ticks
    with pytest.raises(RuntimeError):
        make(tmp_path, [], {})[0].on_tick(Tick(start, 1.0))    # no tick builder configured


def test_orchestrator_source_does_not_reach_into_tests():
    src = Path(__file__).resolve().parents[2] / "backend" / "pipeline"
    for path in src.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(m == "tests" or m.startswith("tests.") for m in mods), path.name


# ----------------------------------------------------------------------------------------------
# Limit target vs stop-market fills, through the whole pipeline
# ----------------------------------------------------------------------------------------------
def _one_trade(tmp_path, exit_bar):
    records = history() + [rec(9, 35, 20000.0, 20010.0, 19995.0, 20008.0), exit_bar]
    orch, broker, daily, _ = make(tmp_path, records, {at(9, 35): LONG})
    exits = [e for s in drive(orch) for e in s.exits]
    assert len(exits) == 1
    return exits[0], daily


def test_take_profit_fills_as_a_limit_with_zero_slippage_but_stop_includes_slippage(tmp_path):
    tp, _ = _one_trade(tmp_path / "tp", rec(9, 36, 20008.0, 20031.0, 20001.0, 20030.0))
    sl, _ = _one_trade(tmp_path / "sl", rec(9, 36, 20000.0, 20005.0, 19985.0, 19990.0))
    assert (tp.result.fill_price, tp.result.slippage) == (20030.0, 0.0)             # exactly the target, $0.00
    assert sl.result.fill_price == pytest.approx(19989.75)                            # stop 19990 less 0.25
    assert sl.result.slippage == pytest.approx(7.5)                                   # 0.25 pt x 15 x $2


def test_same_bar_touch_halts_the_session_and_blocks_the_next_signal(tmp_path):
    records = history() + [rec(9, 35, 20000.0, 20010.0, 19995.0, 20008.0), rec(9, 36, 20008.0, 20035.0, 19985.0, 20000.0),
                           flat(9, 37), flat(9, 38), flat(9, 39)]
    orch, broker, daily, _ = make(tmp_path, records, {at(9, 35): LONG, at(9, 38): LONG})
    results = drive(orch)
    (ev,) = [e for r in results for e in r.exits]
    assert ev.reason == EXIT_STOP_SAME_BAR and ev.trade.pnl_dollars == pytest.approx(-337.2)
    assert daily.locked_out and "SAME_BAR_SL_TP" in daily.lockout_reason
    blocked = next(r for r in results if r.setup.setup_id == "scripted-0938")
    assert not blocked.risk.approved and any("lockout" in x.lower() for x in blocked.risk.rejection_reasons)
    assert broker.get_position("MNQ") is None
    assert "safety_event" in trail_kinds(tmp_path, "scripted-0935")


# ----------------------------------------------------------------------------------------------
# settle_intent: no deadlock after ANY outcome of an approved intent
# ----------------------------------------------------------------------------------------------
def test_slot_is_free_again_after_a_fill_and_exit_but_not_while_the_position_is_open(tmp_path):
    records = history() + [
        rec(9, 35, 20000.0, 20010.0, 19995.0, 20008.0),   # fill bar
        rec(9, 36, 20008.0, 20031.0, 20001.0, 20030.0),   # target hit
        flat(9, 37), flat(9, 38), flat(9, 39)]
    triggers = {at(9, 35): LONG, at(9, 36): LONG, at(9, 38): LONG}
    orch, broker, daily, _ = make(tmp_path, records, triggers)
    results = drive(orch)

    during = next(r for r in results if r.setup.setup_id == "scripted-0936")       # first position still open
    assert not during.risk.approved and any("concurrent" in x.lower() for x in during.risk.rejection_reasons)
    after = next(r for r in results if r.setup.setup_id == "scripted-0938")         # position closed at 09:36
    assert after.risk.approved
    assert broker.get_position("MNQ")["quantity"] == 15                              # the 09:38 signal filled at the 09:38 bar open
    assert len(broker.closed_trades()) == 1 and len(broker.open_trades()) == 1


def test_slot_is_released_when_the_fill_check_rejects(tmp_path):
    records = history() + [
        rec(9, 35, 19985.0, 19990.0, 19980.0, 19988.0),   # opens THROUGH the stop: entry must be rejected
        flat(9, 36), flat(9, 37), flat(9, 38)]
    orch, broker, *_ = make(tmp_path, records, {at(9, 35): LONG, at(9, 37): LONG})
    results = drive(orch)
    assert broker.get_position("MNQ") is not None                                    # the LATER signal got in
    kinds = trail_kinds(tmp_path, "scripted-0935")
    assert "fill_check" in kinds and "order_result" not in kinds                     # first one never reached the broker
    first = DecisionLogger(tmp_path / "decisions.ndjson").read_trail("scripted-0935")
    assert [e for e in first if e["event_kind"] == "fill_check"][0]["payload"]["approved"] is False
    assert next(r for r in results if r.setup.setup_id == "scripted-0937").risk.approved


class FlakyBroker(PaperBroker):
    """Rejects (or raises on) the first entry, then behaves normally."""
    def __init__(self, mode, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode, self.calls = mode, 0

    def submit_intent(self, intent, reference_price=None, at=None):
        self.calls += 1
        if self.calls == 1:
            if self.mode == "raise":
                raise RuntimeError("simulated broker crash")
            return OrderResult(order_id=intent.entry_order_id, accepted=False, rejection_reason="simulated broker rejection")
        return super().submit_intent(intent, reference_price, at)


def _flaky(tmp_path, mode, records, triggers):
    base = PaperBroker.from_config(load_config())
    broker = FlakyBroker(mode, config=base.config, instruments=base._instruments, parameter_snapshot=base._parameter_snapshot)
    config = load_config()
    orch, broker, daily = build_paper_orchestrator(
        config=config, provider=InMemoryProvider(records),
        normalizer=BarNormalizer("MNQ", field_map=FIELDS, source_tz="America/New_York"),
        strategy=ScriptedStrategy(config, triggers), trading_day=DAY, log_path=tmp_path / "decisions.ndjson", broker=broker)
    return orch, broker


def test_slot_is_released_when_the_broker_rejects_the_order(tmp_path):
    records = history() + [flat(9, 35), flat(9, 36), flat(9, 37), flat(9, 38)]
    orch, broker = _flaky(tmp_path, "reject", records, {at(9, 35): LONG, at(9, 37): LONG})
    results = drive(orch)
    rejected = next(r for r in results if r.entry is not None and not r.entry.accepted)
    assert "simulated broker rejection" in rejected.entry.rejection_reason
    assert broker.calls == 2 and broker.get_position("MNQ")["quantity"] == 15        # the next signal entered


def test_slot_is_released_even_if_the_broker_raises(tmp_path):
    records = history() + [flat(9, 35), flat(9, 36), flat(9, 37), flat(9, 38)]
    orch, broker = _flaky(tmp_path, "raise", records, {at(9, 35): LONG, at(9, 37): LONG})
    drive(orch, 95)
    with pytest.raises(RuntimeError, match="simulated broker crash"):
        orch.step()                                                                   # candle 09:35: broker blows up
    assert not orch.has_pending_intent
    drive(orch)                                                                       # keep running: no deadlock
    assert broker.get_position("MNQ")["quantity"] == 15


def test_slot_is_released_when_a_stale_intent_is_cancelled(tmp_path):
    records = history() + [flat(9, 38), flat(9, 39), flat(9, 40)]                    # 09:35-09:37 never arrived
    orch, broker, *_ = make(tmp_path, records, {at(9, 35): LONG, at(9, 39): LONG})
    results = drive(orch)
    cancelled = next(r for r in results if r.cancelled_intent)
    assert cancelled.candle.open_time == at(9, 38) and cancelled.entry is None
    assert "intent_cancelled" in trail_kinds(tmp_path, "scripted-0935")
    assert broker.get_position("MNQ")["quantity"] == 15                              # the 09:39 signal filled at 09:39's bar


def test_finish_cancels_and_settles_a_pending_intent(tmp_path):
    records = history() + [flat(9, 35), flat(9, 36), flat(9, 37)]
    orch, broker, *_ = make(tmp_path, records, {at(9, 35): LONG, at(9, 36): LONG})
    drive(orch, 95)                                                                   # decision made, fill not yet processed
    assert orch.has_pending_intent
    orch.finish("manual stop")
    assert not orch.has_pending_intent and "intent_cancelled" in trail_kinds(tmp_path, "scripted-0935")
    results = drive(orch)                                                             # later signal must not be blocked
    assert next(r for r in results if r.setup.setup_id == "scripted-0936").risk.approved
