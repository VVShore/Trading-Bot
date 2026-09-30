"""
Phase 3 milestone: the first complete simulated trade, end to end.

  raw bars/ticks -> MarketDataProvider -> BarNormalizer -> closed 1M -> TimeframeAggregator
  -> MarketContext (+ detectors) -> TestTriggerStrategy -> TradeSetup(TRADE)
  -> RiskEngine (tick rounding, daily limits, sizing) -> OrderIntent
  -> next-bar-open fill check -> PaperBroker.submit_intent -> Position
  -> bar-by-bar exit check -> close -> DailyRiskState -> DecisionLogger (NDJSON trail)

The orchestration below is deliberately kept in this test (a prototype of the Phase 5/6 loop);
every component it calls is production code.
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from backend.config.loader import load_config
from backend.context.builder import MarketContextBuilder
from backend.core.enums import OrderType, TradeSide
from backend.core.models.order import Order
from backend.data.normalizer import BarFieldMap, BarNormalizer
from backend.data.pipeline import MarketDataPipeline
from backend.data.provider import InMemoryProvider
from backend.data.ticks import Tick, TickCandleBuilder
from backend.decision_log.decisions import DecisionLogger
from backend.detectors import DetectorStatus, default_detectors
from backend.execution.paper import PaperBroker
from backend.management.bar_exit import BarExitOutcome, enforce_safety_event, evaluate_bar_exit
from backend.market.candles.aggregator import TimeframeAggregator
from backend.risk.daily_limits import DailyRiskState
from backend.risk.engine import RiskEngine
from backend.strategies.base import MarketContext
from tests.helpers import NY
from tests.stubs.trigger_strategy import TestTriggerStrategy

DAY = date(2026, 1, 5)
FIELDS = BarFieldMap(timestamp="ts", open="o", high="h", low="l", close="c", volume="v", is_closed="final")

# Off-tick on purpose so the RiskEngine's rounding is exercised: entry->20000.0 (toward market),
# stop->19990.0 (away), target->20030.0 (toward entry).
ENTRY, STOP, TARGET = 20000.125, 19990.125, 20030.125


def rec(hh, mm, o, h, l, c, final="true"):
    return {"ts": f"2026-01-05 {hh:02d}:{mm:02d}:00", "o": o, "h": h, "l": l, "c": c, "v": 10, "final": final}


def flat_history():
    """08:00..09:34 flat at 20000 -> the decision context (as_of 09:35) has last close 20000.0."""
    return [rec(8 + k // 60, k % 60, 20000.0, 20000.5, 19999.5, 20000.0) for k in range(95)]


def scenario(fill_open=20000.0, exit_bar=(20008.0, 20031.0, 20001.0, 20030.0)):
    o, h, l, c = exit_bar
    return flat_history() + [
        rec(9, 35, fill_open, max(fill_open, 20010.0), 19995.0, 20008.0),  # the bar the order fills in (open = fill ref)
        rec(9, 36, o, h, l, c),                                              # exit bar
        rec(9, 37, 20000.0, 20001.0, 19999.0, 20000.0),
    ]


def at(hh, mm):
    return datetime(2026, 1, 5, hh, mm, tzinfo=NY)


class Harness:
    def __init__(self, tmp_path: Path, records):
        self.config = load_config()
        self.state = DailyRiskState(trading_day=DAY, config=self.config.risk)
        self.broker = PaperBroker.from_config(self.config)
        self.engine = RiskEngine(self.config, self.state, self.broker.open_position_count)
        self.log = DecisionLogger(tmp_path / "decision_log.ndjson")
        self.agg = TimeframeAggregator("MNQ", self.config.session)
        self.builder = MarketContextBuilder(self.agg, detectors=default_detectors())
        normalizer = BarNormalizer("MNQ", field_map=FIELDS, source_tz="America/New_York")
        self.pipeline = MarketDataPipeline(InMemoryProvider(records), normalizer, self.agg, self.builder, self.config.session)
        self._contexts = self.pipeline.run()
        self.strategy = TestTriggerStrategy(self.config, TradeSide.LONG, ENTRY, STOP, [TARGET])
        self.entry_result = None

    def context_at(self, hh, mm) -> MarketContext:
        return next(c for c in self._contexts if c.as_of == at(hh, mm))

    def next_context(self) -> MarketContext:
        return next(self._contexts)

    def event(self, kind, setup_id, ctx, payload):
        self.log.record_event(kind, setup_id, ctx.as_of, payload)

    def decide(self, ctx, use_market_price=True):
        setup = self.strategy.evaluate(ctx)
        self.log.record(setup)
        bars = ctx.candles.get("1m")
        market = bars[-1].close if use_market_price and bars else None
        risk = self.engine.evaluate(setup, market_price=market)
        self.event("risk_decision", setup.setup_id, ctx, risk.to_dict())
        return setup, risk

    def enter(self, risk, fill_ctx):
        intent = risk.intent
        fill_ref = fill_ctx.candles["1m"][-1].open  # decision at bar close -> earliest fill is the NEXT bar's open
        check = self.engine.reconcile_fill(intent, fill_ref)
        self.event("fill_check", intent.setup_id, fill_ctx, check.to_dict())
        if not check.approved:
            return None
        result = self.broker.submit_intent(check.intent, reference_price=fill_ref)
        self.engine.settle_intent(intent.intent_id)
        self.event("order_result", intent.setup_id, fill_ctx, result.model_dump(mode="json"))
        pos = self.broker.get_position("MNQ")
        self.event("position_open", intent.setup_id, fill_ctx, {**pos, "side": pos["side"].value})
        self.entry_result, self.live_intent = result, check.intent
        return result

    def manage(self, ctx):
        intent = self.live_intent
        candle = ctx.candles["1m"][-1]
        target = intent.targets[0].price
        res = evaluate_bar_exit(intent.side, intent.stop_price, target, candle)
        self.event("bar_exit", intent.setup_id, ctx, {"outcome": res.outcome.value, "candle_open": candle.open_time.isoformat()})
        if res.outcome == BarExitOutcome.NONE:
            return res
        ref = target if res.outcome == BarExitOutcome.TARGET else intent.stop_price
        pos = self.broker.get_position("MNQ")
        exit_order = Order(order_id=f"{intent.intent_id}:exit", symbol="MNQ", side=TradeSide.SHORT,
                           order_type=OrderType.MARKET, quantity=pos["quantity"], created_at=ctx.as_of)
        out = self.broker.submit_order(exit_order, reference_price=ref)
        net = out.realized_pnl - self.entry_result.commission - out.commission
        self.state.record_trade_result(net)
        if res.safety_event:
            self.event("safety_event", intent.setup_id, ctx, res.safety_event.to_dict())
        enforce_safety_event(res, self.state)
        self.event("order_result", intent.setup_id, ctx, out.model_dump(mode="json"))
        self.event("daily_state", intent.setup_id, ctx, {
            "trades_taken": self.state.trades_taken, "wins": self.state.wins, "losses": self.state.losses,
            "realized_pnl_dollars": self.state.realized_pnl_dollars, "locked_out": self.state.locked_out,
            "lockout_reason": self.state.lockout_reason, "net_pnl_after_costs": net,
        })
        self.exit_result, self.net = out, net
        return res

    @staticmethod
    def kinds(trail):
        return [e["event_kind"] for e in trail]


# ------------------------------------------------------------------------------------------------
# The full happy path
# ------------------------------------------------------------------------------------------------
def test_full_pipeline_from_bars_to_a_closed_paper_trade_and_logged_trail(tmp_path):
    h = Harness(tmp_path, scenario())

    # 1-2. bars -> provider -> normalizer -> aggregator -> context (no lookahead)
    ctx = h.context_at(9, 35)
    assert h.pipeline.stats.candles_ingested == 95
    assert ctx.candles["1m"][-1].open_time == at(9, 34)          # the 09:35 bar has not happened yet
    assert ctx.candles["1h"][-1].open_time == at(8, 0)           # closed 08:00 hour, not the developing 09:00 one
    assert all(r.status in (DetectorStatus.UNKNOWN, DetectorStatus.NOT_AVAILABLE) for r in ctx.detections.values())

    # 3. strategy -> TRADE setup
    setup, risk = h.decide(ctx)
    assert setup.decision == "TRADE" and setup.proposed_entry == ENTRY

    # 4. risk: rounding, limits, sizing -> OrderIntent
    assert risk.approved, risk.rejection_reasons
    intent = risk.intent
    assert (intent.entry_price, intent.stop_price, intent.targets[0].price) == (20000.0, 19990.0, 20030.0)
    assert intent.quantity == 15 and intent.approved_risk_dollars == pytest.approx(300.0)
    assert len(risk.adjustments) == 3

    # 5. next bar open -> fill check -> paper broker -> position
    fill_ctx = h.next_context()
    assert fill_ctx.as_of == at(9, 36)
    entry = h.enter(risk, fill_ctx)
    assert entry.filled and entry.fill_price == pytest.approx(20000.25) and entry.commission == pytest.approx(11.1)
    pos = h.broker.get_position("MNQ")
    assert (pos["side"], pos["quantity"], pos["entry_price"]) == (TradeSide.LONG, 15, pytest.approx(20000.25))
    assert h.state.trades_taken == 0  # nothing realized yet

    # manage: entry bar touches neither level, the next bar reaches the target
    assert h.manage(fill_ctx).outcome == BarExitOutcome.NONE
    assert h.manage(h.next_context()).outcome == BarExitOutcome.TARGET
    assert h.broker.get_position("MNQ") is None

    # 5b. DailyRiskState updated with the NET result (gross 885.00 - 22.20 commission)
    assert h.net == pytest.approx(862.8)
    assert (h.state.trades_taken, h.state.wins, h.state.realized_pnl_dollars) == (1, 1, pytest.approx(862.8))
    assert h.state.can_trade()

    # 6. the decision trail, in order, all tied to one setup id
    trail = h.log.read_trail()
    assert h.kinds(trail) == ["setup", "risk_decision", "fill_check", "order_result", "position_open",
                              "bar_exit", "bar_exit", "order_result", "daily_state"]
    assert {e["setup_id"] for e in trail} == {setup.setup_id}
    by_kind = {e["event_kind"]: e["payload"] for e in trail}
    assert by_kind["risk_decision"]["approved"] is True
    assert by_kind["risk_decision"]["intent"]["quantity"] == 15
    assert any("entry" in a for a in by_kind["risk_decision"]["adjustments"])
    assert by_kind["position_open"]["quantity"] == 15
    assert by_kind["daily_state"]["realized_pnl_dollars"] == pytest.approx(862.8)
    assert [e["at"] for e in trail] == sorted(e["at"] for e in trail)  # simulated time, monotonic

    raw_lines = (tmp_path / "decision_log.ndjson").read_text().splitlines()
    assert len(raw_lines) == len(trail) and all(json.loads(line) for line in raw_lines)  # valid NDJSON
    assert [s.setup_id for s in h.log.read_all()] == [setup.setup_id]  # setups still round-trip


def test_ticks_feed_the_same_pipeline(tmp_path):
    h = Harness(tmp_path, [])
    ticks = TickCandleBuilder("MNQ")
    start = at(8, 0)
    for k in range(96):  # one tick per minute 08:00..09:35; the 09:35 tick closes the 09:34 candle
        for candle in ticks.ingest(Tick(start + timedelta(minutes=k, seconds=10), 20000.0, 1.0)):
            h.agg.ingest(candle)
    ctx = h.builder.build()
    assert ctx.as_of == at(9, 35)
    assert ctx.candles["1m"][-1].open_time == at(9, 34)               # 09:35 is still developing -> not in context
    assert ticks.developing_candle().open_time == at(9, 35)

    _, risk = h.decide(ctx)
    assert risk.approved and risk.intent.quantity == 15


# ------------------------------------------------------------------------------------------------
# Fail-closed and edge paths through the same stack
# ------------------------------------------------------------------------------------------------
def test_daily_loss_lockout_stops_the_signal_before_any_order(tmp_path):
    h = Harness(tmp_path, scenario())
    h.state.record_trade_result(-2000.0)
    ctx = h.context_at(9, 35)
    setup, risk = h.decide(ctx)
    assert setup.decision == "TRADE" and not risk.approved and risk.intent is None
    assert any("Max daily loss" in r for r in risk.rejection_reasons)
    assert h.broker.get_position("MNQ") is None
    assert h.kinds(h.log.read_trail()) == ["setup", "risk_decision"]


def test_off_tick_entry_without_market_price_fails_closed(tmp_path):
    h = Harness(tmp_path, scenario())
    _, risk = h.decide(h.context_at(9, 35), use_market_price=False)
    assert not risk.approved and any("market price" in r for r in risk.rejection_reasons)
    assert h.broker.open_position_count() == 0


def test_gap_open_resizes_before_the_broker_sees_the_order(tmp_path):
    h = Harness(tmp_path, scenario(fill_open=20005.0))
    _, risk = h.decide(h.context_at(9, 35))
    assert risk.intent.quantity == 15                                    # sized at the planned entry 20000
    h.enter(risk, h.next_context())                                      # bar opens 20005: stop distance 10 -> 15 pts
    assert h.broker.get_position("MNQ")["quantity"] == 10                # floor(300 / (15*2))
    check = [e for e in h.log.read_trail() if e["event_kind"] == "fill_check"][0]["payload"]
    assert check["approved"] and "Re-sized at fill" in check["adjustments"][0]


def test_same_bar_stop_and_target_exits_at_stop_logs_event_and_halts_the_session(tmp_path):
    h = Harness(tmp_path, scenario(exit_bar=(20008.0, 20035.0, 19985.0, 20000.0)))
    _, risk = h.decide(h.context_at(9, 35))
    fill_ctx = h.next_context()
    h.enter(risk, fill_ctx)
    h.manage(fill_ctx)
    res = h.manage(h.next_context())

    assert res.outcome == BarExitOutcome.AMBIGUOUS_STOP_FIRST
    # stop assumed first: loss = (19989.75 - 20000.25) x 15 x $2 = -315, minus 22.20 commission
    assert h.net == pytest.approx(-337.2)
    assert h.net < -h.config.risk.resolved_risk_dollars  # exceeds the $300 ceiling through costs: accepted (resolution 2)
    assert not h.state.can_trade() and "SAME_BAR_SL_TP" in h.state.lockout_reason
    assert "safety_event" in h.kinds(h.log.read_trail())

    # the very next signal is refused for the rest of the session
    _, risk2 = h.decide(h.next_context())
    assert not risk2.approved and any("lockout" in r.lower() for r in risk2.rejection_reasons)
    assert h.broker.get_position("MNQ") is None


def test_no_trade_signal_never_becomes_an_order(tmp_path):
    h = Harness(tmp_path, [])
    empty = MarketContext(as_of=at(9, 35), symbol="MNQ")  # not populated
    setup, risk = h.decide(empty)
    assert setup.decision == "NO_TRADE" and not risk.approved and risk.intent is None


def test_second_signal_is_blocked_while_a_position_is_open(tmp_path):
    h = Harness(tmp_path, scenario())
    _, risk = h.decide(h.context_at(9, 35))
    fill_ctx = h.next_context()
    h.enter(risk, fill_ctx)
    _, second = h.decide(fill_ctx)  # a fresh signal one bar later, position still open
    assert not second.approved and any("concurrent" in r.lower() for r in second.rejection_reasons)
