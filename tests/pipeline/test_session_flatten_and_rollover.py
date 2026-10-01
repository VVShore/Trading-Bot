"""Phase 6: R12 session flatten, multi-day rollover, stale intents across the halt."""
from datetime import date, datetime, timedelta

import pytest

from backend.config.loader import load_config
from backend.core.enums import OrderStatus, StrategyName, TradeSide, TradeStatus
from backend.core.models.setup import TradeSetup
from backend.core.models.target import Target
from backend.data.normalizer import BarFieldMap, BarNormalizer
from backend.data.provider import InMemoryProvider
from backend.decision_log.decisions import DecisionLogger
from backend.execution.events import EXIT_SESSION_FLATTEN, EXIT_STOP
from backend.pipeline import build_paper_orchestrator
from backend.strategies.base import MarketContext, Strategy
from tests.helpers import NY

FIELDS = BarFieldMap(timestamp="ts", open="o", high="h", low="l", close="c", volume="v", is_closed="final")
LONG = dict(side=TradeSide.LONG, entry=20000.0, stop=19990.0, targets=[20030.0])  # 15 contracts at $300


def t(day, hh, mm):
    return datetime(2026, 1, day, hh, mm, tzinfo=NY)


def rec(day, hh, mm, o=20000.0, h=20000.5, l=19999.5, c=20000.0):
    return {"ts": f"2026-01-{day:02d} {hh:02d}:{mm:02d}:00", "o": o, "h": h, "l": l, "c": c, "v": 10, "final": "true"}


def minutes(day, start, end, **kw):
    """Dense flat candles from start (hh, mm) up to and INCLUDING end (hh, mm)."""
    cur, stop = t(day, *start), t(day, *end)
    out = []
    while cur <= stop:
        out.append(rec(day, cur.hour, cur.minute, **kw))
        cur += timedelta(minutes=1)
    return out


class Scripted(Strategy):
    name, version = "SCRIPTED", "SCRIPTED-0"

    def __init__(self, config, triggers):
        super().__init__(config)
        self.triggers = triggers  # as_of -> spec, or a callable(as_of) -> spec | None

    def evaluate(self, context: MarketContext) -> TradeSetup:
        spec = self.triggers(context.as_of) if callable(self.triggers) else self.triggers.get(context.as_of)
        common = dict(setup_id=f"s-{context.as_of.strftime('%d-%H%M')}", strategy=StrategyName.NY_AM_HTF_CONTINUATION_V1,
                      strategy_version=self.version, symbol=context.symbol, evaluated_at=context.as_of)
        if spec is None:
            return TradeSetup(**common, decision="NO_TRADE", reason="no")
        return TradeSetup(**common, proposed_side=spec["side"], proposed_entry=spec["entry"], proposed_stop=spec["stop"],
                          targets=[Target(type="t", price=p, source="test") for p in spec["targets"]], decision="TRADE")


def make(tmp_path, records, triggers, **kw):
    config = kw.pop("config", None) or load_config()
    orch, broker, daily = build_paper_orchestrator(
        config=config, provider=InMemoryProvider(records),
        normalizer=BarNormalizer("MNQ", field_map=FIELDS, source_tz="America/New_York"),
        strategy=Scripted(config, triggers), log_path=tmp_path / "log.ndjson",
        trade_store_path=tmp_path / "trades.ndjson", **kw)
    return orch, broker, daily


def drive(orch):
    out = []
    while (r := orch.step()) is not None:
        out.append(r)
    return out


def kinds(tmp_path, setup_id):
    return [e["event_kind"] for e in DecisionLogger(tmp_path / "log.ndjson").read_trail(setup_id)]


# ----------------------------------------------------------------------------------------------
# R12: flatten at 14:57 ET
# ----------------------------------------------------------------------------------------------
def test_position_held_into_the_afternoon_is_market_closed_at_exactly_1457(tmp_path):
    # entry decided 09:35, filled at the 09:35 open; every later bar stays inside the bracket (19995..20025)
    records = minutes(5, (8, 0), (9, 34)) + [rec(5, 9, 35)] + minutes(5, (9, 36), (15, 2), o=20010.0, h=20012.0, l=20008.0, c=20010.0)
    orch, broker, daily = make(tmp_path, records, {t(5, 9, 35): LONG})
    results = drive(orch)

    flattens = [(r, e) for r in results for e in r.exits]
    assert len(flattens) == 1
    step, ev = flattens[0]
    assert step.candle.open_time == t(5, 14, 57)                     # the first bar at/after 14:57, not one minute earlier
    assert not any(r.exits for r in results if r.candle.open_time < t(5, 14, 57))
    assert ev.reason == EXIT_SESSION_FLATTEN and ev.trade.exit_reason == EXIT_SESSION_FLATTEN

    # exactly 14:57:00 ET, at that bar's open, as a MARKET order: adverse slippage is charged
    assert ev.trade.exits[0].time == t(5, 14, 57) and ev.result.timestamp == t(5, 14, 57)
    assert ev.result.fill_price == pytest.approx(20010.0 - 0.25)
    assert ev.result.slippage == pytest.approx(0.25 * 15 * 2)
    assert ev.trade.status == TradeStatus.CLOSED and broker.get_position("MNQ") is None

    order = broker.get_order_record(f"{ev.trade.trade_id}:flatten").order
    assert order.order_type.value == "market" and order.side == TradeSide.SHORT and order.quantity == 15
    # the bracket legs are gone
    assert broker.get_order_record(f"{ev.trade.trade_id}:stop").status == OrderStatus.CANCELLED
    assert broker.get_order_record(f"{ev.trade.trade_id}:target").status == OrderStatus.CANCELLED
    # booked in the daily state like any other close
    assert (daily.trades_taken, daily.wins) == (1, 1)
    assert "session_flatten" in kinds(tmp_path, "s-05-0935")


def test_flatten_does_not_fire_before_1457(tmp_path):
    records = minutes(5, (8, 0), (9, 34)) + [rec(5, 9, 35)] + minutes(5, (9, 36), (14, 56), o=20010.0, h=20012.0, l=20008.0, c=20010.0)
    orch, broker, _ = make(tmp_path, records, {t(5, 9, 35): LONG})
    assert not any(r.exits for r in drive(orch))
    assert broker.get_position("MNQ")["quantity"] == 15


def test_a_stop_in_the_1456_bar_beats_the_flatten(tmp_path):
    records = (minutes(5, (8, 0), (9, 34)) + [rec(5, 9, 35)] + minutes(5, (9, 36), (14, 55), o=20010.0, h=20012.0, l=20008.0, c=20010.0)
               + [rec(5, 14, 56, o=20000.0, h=20001.0, l=19985.0, c=19990.0), rec(5, 14, 57), rec(5, 14, 58)])
    orch, *_ = make(tmp_path, records, {t(5, 9, 35): LONG})
    (ev,) = [e for r in drive(orch) for e in r.exits]
    assert ev.reason == EXIT_STOP and ev.trade.exit_time == t(5, 14, 57)   # 14:56 bar closes at 14:57; no flatten needed


def test_flatten_uses_the_first_available_candle_when_1457_is_missing(tmp_path):
    records = minutes(5, (8, 0), (9, 34)) + [rec(5, 9, 35)] + minutes(5, (9, 36), (14, 50), o=20010.0, h=20012.0, l=20008.0, c=20010.0) \
        + [rec(5, 15, 3, o=20005.0, h=20006.0, l=20004.0, c=20005.0)]
    orch, *_ = make(tmp_path, records, {t(5, 9, 35): LONG})
    (ev,) = [e for r in drive(orch) for e in r.exits]
    assert ev.reason == EXIT_SESSION_FLATTEN and ev.trade.exits[0].time == t(5, 15, 3)
    assert ev.result.fill_price == pytest.approx(20005.0 - 0.25)


def test_a_position_that_survives_into_a_new_day_is_closed_at_the_first_candle(tmp_path):
    records = (minutes(5, (8, 0), (9, 34)) + [rec(5, 9, 35)] + minutes(5, (9, 36), (9, 40), o=20010.0, h=20012.0, l=20008.0, c=20010.0)
               + [rec(6, 9, 30, o=20002.0, h=20003.0, l=20001.0, c=20002.0)])       # data for the rest of day 1 is missing
    orch, broker, daily = make(tmp_path, records, {t(5, 9, 35): LONG})
    results = drive(orch)
    ev = next(e for r in results for e in r.exits)
    assert ev.reason == EXIT_SESSION_FLATTEN and ev.trade.exits[0].time == t(6, 9, 30)
    assert broker.get_position("MNQ") is None


def test_an_entry_cannot_fill_at_or_after_the_flatten_time(tmp_path):
    records = minutes(5, (14, 30), (15, 5))
    orch, broker, _ = make(tmp_path, records, {t(5, 14, 57): LONG})    # decided at 14:57 (candle 14:56 closed)
    results = drive(orch)
    cancelled = next(r for r in results if r.cancelled_intent)
    assert cancelled.candle.open_time == t(5, 14, 57) and broker.get_position("MNQ") is None
    events = DecisionLogger(tmp_path / "log.ndjson").read_trail("s-05-1457")
    assert any(e["event_kind"] == "intent_cancelled" and "flatten" in e["payload"]["reason"] for e in events)


# ----------------------------------------------------------------------------------------------
# R11: stale intent across the 17:00-18:00 halt
# ----------------------------------------------------------------------------------------------
def test_intent_generated_at_1559_is_cancelled_when_the_next_candle_is_1800(tmp_path):
    records = minutes(5, (15, 0), (15, 58)) + [rec(5, 18, 0), rec(5, 18, 1)]
    orch, broker, _ = make(tmp_path, records, {t(5, 15, 59): LONG})
    results = drive(orch)

    decision = next(r for r in results if r.risk is not None)
    assert decision.context.as_of == t(5, 15, 59) and decision.risk.approved      # the intent WAS generated at 15:59
    cancelled = next(r for r in results if r.cancelled_intent)
    assert cancelled.candle.open_time == t(5, 18, 0)                             # ... and died at the first 18:00 candle
    assert cancelled.entry is None and broker.get_position("MNQ") is None and broker.closed_trades() == []
    cancel_event = next(e for e in DecisionLogger(tmp_path / "log.ndjson").read_trail("s-05-1559")
                        if e["event_kind"] == "intent_cancelled")
    assert "stale" in cancel_event["payload"]["reason"]
    assert "order_result" not in kinds(tmp_path, "s-05-1559")


def test_the_slot_is_free_after_the_stale_cancellation(tmp_path):
    records = minutes(5, (15, 0), (15, 58)) + minutes(5, (18, 0), (18, 5))
    # an evening signal is still cancelled (after flatten time) but must not leave the RiskEngine blocked
    orch, *_ = make(tmp_path, records, {t(5, 15, 59): LONG, t(5, 18, 3): LONG})
    results = drive(orch)
    assert next(r for r in results if r.setup.setup_id == "s-05-1803").risk.approved


# ----------------------------------------------------------------------------------------------
# Multi-day rollover
# ----------------------------------------------------------------------------------------------
def loser(day, hh, mm):   # enters at 20000.0, trades through the stop (19990) without touching the 20030 target
    return rec(day, hh, mm, o=20000.0, h=20005.0, l=19985.0, c=19990.0)


def every_bar(_as_of):
    return LONG


def test_daily_loss_limit_halts_day_one_and_resets_on_day_two(tmp_path):
    day1 = minutes(5, (9, 0), (9, 29)) + [loser(5, 9, 30 + k) for k in range(9)]    # a stopped-out trade on every bar
    day2 = minutes(6, (9, 0), (9, 29)) + [loser(6, 9, 30 + k) for k in range(4)]
    orch, broker, daily = make(tmp_path, day1 + day2, every_bar)
    results = drive(orch)

    d1 = [r for r in results if r.candle.open_time.day == 5]
    d2 = [r for r in results if r.candle.open_time.day == 6]
    # day 1: six losers of -337.20 cross the $2,000 limit; every later signal is refused
    assert sum(len(r.exits) for r in d1) == 6
    refused = [r for r in d1 if r.risk is not None and not r.risk.approved and r.candle.open_time >= t(5, 9, 36)]
    assert refused and all(any("Max daily loss" in x for x in r.risk.rejection_reasons) for r in refused[:1])
    summary1 = orch.day_summaries()[0]
    assert summary1["trading_day"] == "2026-01-05" and summary1["locked_out"] is True
    assert summary1["realized_pnl_dollars"] == pytest.approx(-6 * 337.2) and summary1["trades_taken"] == 6

    # day 2: the first candle rolls the state over and trading is allowed again
    assert d2[0].new_day == date(2026, 1, 6)
    assert sum(len(r.exits) for r in d2) >= 1 and any(r.risk is not None and r.risk.approved for r in d2)
    current = orch.day_summaries()[-1]
    assert current["trading_day"] == "2026-01-06" and current["trades_taken"] >= 1
    assert daily.trading_day == date(2026, 1, 6)
    assert any(e["event_kind"] == "day_rollover" for e in DecisionLogger(tmp_path / "log.ndjson").read_trail("day-2026-01-05"))


def test_rollover_also_clears_a_safety_halt_and_the_trade_cap(tmp_path):
    day1 = minutes(5, (9, 0), (9, 34)) + [rec(5, 9, 35), rec(5, 9, 36, o=20008.0, h=20035.0, l=19985.0, c=20000.0), rec(5, 9, 37)]
    day2 = minutes(6, (9, 0), (9, 34)) + [rec(6, 9, 35), rec(6, 9, 36, o=20008.0, h=20031.0, l=20001.0, c=20030.0)]
    orch, broker, daily = make(tmp_path, day1 + day2, {t(5, 9, 35): LONG, t(5, 9, 37): LONG, t(6, 9, 35): LONG})
    results = drive(orch)
    assert any("SAME_BAR_SL_TP" in (x or "") for x in [orch.day_summaries()[0]["lockout_reason"]])   # day 1 was halted
    day1_blocked = next(r for r in results if r.setup.setup_id == "s-05-0937")
    assert not day1_blocked.risk.approved
    day2_trade = [r for r in results if r.candle.open_time.day == 6 and r.exits]
    assert day2_trade and day2_trade[0].exits[0].trade.pnl_dollars > 0                                 # a normal winner on day 2


def test_days_cannot_go_backwards():
    from backend.risk.daily_limits import DailyRiskState
    state = DailyRiskState(trading_day=date(2026, 1, 6), config=load_config().risk)
    with pytest.raises(ValueError):
        state.start_new_day(date(2026, 1, 5))
    with pytest.raises(ValueError):
        state.start_new_day(date(2026, 1, 6))


def test_start_new_day_resets_everything_in_place():
    from backend.risk.daily_limits import DailyRiskState
    state = DailyRiskState(trading_day=date(2026, 1, 5), config=load_config().risk)
    same_object = state
    for _ in range(6):
        state.record_trade_result(-337.2)
    state.halt_session("x")
    assert not state.can_trade()
    state.start_new_day(date(2026, 1, 6))
    assert same_object is state and state.can_trade() and state.trading_day == date(2026, 1, 6)
    assert (state.trades_taken, state.losses, state.realized_pnl_dollars, state.lockout_reason) == (0, 0, 0.0, None)
    assert state.snapshot()["trading_day"] == "2026-01-06"
