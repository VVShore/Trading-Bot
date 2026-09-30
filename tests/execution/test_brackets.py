"""PaperBroker brackets: limit target (exact, zero slippage) / stop-market (slippage) / OCO / TradeRecord lifecycle."""
import pytest

from backend.config.loader import load_config
from backend.core.enums import OrderStatus, OrderType, TradeSide, TradeStatus
from backend.core.models.candle import Candle
from backend.core.models.order import Order
from backend.core.models.target import Target
from backend.decision_log.trades import TradeStore
from backend.execution.events import EXIT_MANUAL, EXIT_STOP, EXIT_STOP_SAME_BAR, EXIT_TARGET
from backend.execution.paper import PaperBroker
from backend.management.bar_exit import SAME_BAR_SL_TP
from tests.helpers import DECISION_TIME, NY, m1, make_engine, make_setup

FILL_TIME = m1(2026, 1, 5, 9, 35).open_time  # simulated entry-bar open


def _intent(setup_id="s1", **setup_overrides):
    """A real RiskEngine-approved intent: entry 20000 / stop 19990 / target 20030 / qty 15 (long) by default."""
    broker_stub = PaperBroker.from_config(load_config())  # only for the position-count callable
    engine, _, _ = make_engine(open_positions=broker_stub.open_position_count)
    decision = engine.evaluate(make_setup(setup_id=setup_id, **setup_overrides))
    assert decision.approved, decision.rejection_reasons
    return decision.intent


def _broker(tmp_path=None):
    store = TradeStore(tmp_path / "trades.ndjson") if tmp_path else None
    return PaperBroker.from_config(load_config(), trade_store=store)


def _open(broker, intent=None, reference=20000.0):
    intent = intent or _intent()
    result = broker.submit_intent(intent, reference_price=reference, at=FILL_TIME)
    assert result.filled
    return intent, result


def bar(o, h, l, c, minute=36):
    return m1(2026, 1, 5, 9, minute, o=o, h=h, l=l, c=c)


# ----------------------------------------------------------------------------------------------
def test_intent_fill_opens_a_trade_and_registers_the_bracket():
    broker = _broker()
    intent, entry = _open(broker)
    assert entry.fill_price == pytest.approx(20000.25)  # entry is a market order: slippage applies

    (trade,) = broker.open_trades()
    assert trade.status == TradeStatus.OPEN and trade.trade_id == intent.intent_id
    assert (trade.side, trade.quantity, trade.entry_price) == (TradeSide.LONG, 15, pytest.approx(20000.25))
    assert trade.entry_time == FILL_TIME and trade.initial_stop == 19990.0
    assert trade.initial_risk_dollars == pytest.approx(300.0)
    assert trade.decision_log_ref == intent.setup_id and trade.parameter_snapshot  # mandatory snapshot present

    stop = broker.get_order_record(f"{intent.intent_id}:stop")
    target = broker.get_order_record(f"{intent.intent_id}:target")
    assert (stop.order.order_type, stop.order.stop_price, stop.order.side) == (OrderType.STOP, 19990.0, TradeSide.SHORT)
    assert (target.order.order_type, target.order.limit_price, target.order.side) == (OrderType.LIMIT, 20030.0, TradeSide.SHORT)
    assert stop.status == target.status == OrderStatus.SUBMITTED  # working
    assert stop.order.quantity == target.order.quantity == 15


def test_target_fills_as_a_limit_order_at_the_exact_price_with_zero_slippage():
    broker = _broker()
    intent, entry = _open(broker)
    (ev,) = broker.on_candle(bar(20008, 20031, 20001, 20030))

    assert ev.reason == EXIT_TARGET
    assert ev.result.fill_price == 20030.0            # exactly the target: no slippage, no price improvement
    assert ev.result.slippage == 0.0
    assert ev.result.commission == pytest.approx(11.1)
    assert ev.result.realized_pnl == pytest.approx((20030.0 - 20000.25) * 15 * 2)  # 892.50 gross
    assert ev.net_pnl == pytest.approx(892.5 - 11.1 - 11.1)

    trade = ev.trade
    assert trade.status == TradeStatus.CLOSED and trade.exit_reason == EXIT_TARGET
    assert trade.pnl_dollars == pytest.approx(870.3) and trade.r_multiple == pytest.approx(870.3 / 300)
    assert trade.exit_time == bar(20008, 20031, 20001, 20030).close_time
    assert [(x.price, x.quantity, x.reason) for x in trade.exits] == [(20030.0, 15, EXIT_TARGET)]
    assert broker.get_position("MNQ") is None and broker.open_trades() == [] and broker.closed_trades() == [trade]
    # OCO: the target leg filled, the stop leg was cancelled, nothing is left working
    assert broker.get_order_record(f"{intent.intent_id}:target").status == OrderStatus.FILLED
    assert broker.get_order_record(f"{intent.intent_id}:stop").status == OrderStatus.CANCELLED
    assert broker.active_bracket_order_ids(intent.intent_id) == []


def test_stop_fills_as_stop_market_with_configured_slippage():
    broker = _broker()
    intent, _ = _open(broker)
    (ev,) = broker.on_candle(bar(20000, 20005, 19985, 19990))

    assert ev.reason == EXIT_STOP
    assert ev.result.fill_price == pytest.approx(19989.75)              # stop 19990.0 less 0.25 adverse slippage
    assert ev.result.slippage == pytest.approx(0.25 * 15 * 2)           # $7.50: slippage IS charged on stops
    assert ev.result.realized_pnl == pytest.approx((19989.75 - 20000.25) * 15 * 2)  # -315 gross
    assert ev.trade.pnl_dollars == pytest.approx(-315.0 - 22.2)
    assert ev.trade.r_multiple == pytest.approx(-337.2 / 300)
    assert broker.get_order_record(f"{intent.intent_id}:stop").status == OrderStatus.FILLED
    assert broker.get_order_record(f"{intent.intent_id}:target").status == OrderStatus.CANCELLED


def test_target_and_stop_slippage_differ_exactly_by_the_configured_slippage():
    tgt, stop = _broker(), _broker()
    _open(tgt); _open(stop)
    t = tgt.on_candle(bar(20008, 20031, 20001, 20030))[0].result
    s = stop.on_candle(bar(20000, 20005, 19985, 19990))[0].result
    assert (t.slippage, s.slippage > 0) == (0.0, True)


def test_gap_through_the_stop_fills_from_the_open_plus_slippage():
    broker = _broker()
    _open(broker)
    (ev,) = broker.on_candle(bar(19980, 19985, 19975, 19978))  # opens 10 points below the stop
    assert ev.result.fill_price == pytest.approx(19979.75)     # the open, not the stop, then slippage
    assert ev.reason == EXIT_STOP


def test_target_gap_grants_no_price_improvement():
    broker = _broker()
    _open(broker)
    (ev,) = broker.on_candle(bar(20040, 20045, 20038, 20042))  # opens above the target
    assert ev.result.fill_price == 20030.0 and ev.result.slippage == 0.0


def test_same_bar_touch_exits_at_the_stop_with_a_safety_event():
    broker = _broker()
    _open(broker)
    (ev,) = broker.on_candle(bar(20008, 20035, 19985, 20000))
    assert ev.reason == EXIT_STOP_SAME_BAR and ev.trade.exit_reason == EXIT_STOP_SAME_BAR
    assert ev.result.fill_price == pytest.approx(19989.75)
    assert ev.bar_result.safety_event.kind == SAME_BAR_SL_TP


def test_untouched_bar_produces_no_event_and_keeps_the_bracket():
    broker = _broker()
    intent, _ = _open(broker)
    assert broker.on_candle(bar(20005, 20020, 19995, 20010)) == []
    assert broker.get_position("MNQ")["quantity"] == 15 and len(broker.active_bracket_order_ids(intent.intent_id)) == 2


def test_short_side_mirrors_the_rules():
    kw = dict(proposed_side=TradeSide.SHORT, proposed_stop=20010.0, targets=[Target(type="t", price=19970.0, source="t")])
    tgt = _broker(); _open(tgt, _intent(**kw))
    (ev,) = tgt.on_candle(bar(19990, 19995, 19969, 19972))
    assert ev.reason == EXIT_TARGET and ev.result.fill_price == 19970.0 and ev.result.slippage == 0.0
    assert ev.result.realized_pnl == pytest.approx((19970.0 - 19999.75) * -1 * 15 * 2)  # +892.50

    stop = _broker(); _open(stop, _intent(**kw))
    (ev,) = stop.on_candle(bar(20005, 20015, 20000, 20012))
    assert ev.reason == EXIT_STOP and ev.result.fill_price == pytest.approx(20010.25)  # stop + adverse slippage
    assert ev.result.realized_pnl == pytest.approx((20010.25 - 19999.75) * -1 * 15 * 2)  # -315


def test_intent_without_a_target_gets_a_stop_only_bracket():
    broker = _broker()
    intent, _ = _open(broker, _intent(targets=[]))
    assert broker.get_order_record(f"{intent.intent_id}:target") is None
    assert broker.on_candle(bar(20005, 20100, 19995, 20050)) == []       # a big up-bar cannot exit a stop-only bracket
    (ev,) = broker.on_candle(bar(20000, 20001, 19985, 19988, minute=37))
    assert ev.reason == EXIT_STOP


def test_with_several_targets_the_highest_priority_one_is_bracketed():
    targets = [Target(type="tp2", price=20050.0, source="t", priority=2), Target(type="tp1", price=20030.0, source="t", priority=1)]
    broker = _broker()
    intent, _ = _open(broker, _intent(targets=targets))
    assert broker.get_order_record(f"{intent.intent_id}:target").order.limit_price == 20030.0


def test_entry_is_refused_while_a_position_is_open():
    broker = _broker()
    _open(broker)
    second = _intent(setup_id="s2")
    result = broker.submit_intent(second, reference_price=20000.0, at=FILL_TIME)
    assert not result.accepted and "flat position" in result.rejection_reason
    assert broker.get_position("MNQ")["quantity"] == 15 and len(broker.open_trades()) == 1


def test_manual_close_cancels_the_bracket_and_closes_the_trade():
    broker = _broker()
    intent, entry = _open(broker)
    out = broker.submit_order(Order(order_id="flatten", symbol="MNQ", side=TradeSide.SHORT, order_type=OrderType.MARKET,
                                    quantity=15, created_at=DECISION_TIME), reference_price=20010.0)
    (trade,) = broker.closed_trades()
    assert trade.status == TradeStatus.CLOSED and trade.exit_reason == EXIT_MANUAL
    assert trade.pnl_dollars == pytest.approx(out.realized_pnl - entry.commission - out.commission)
    assert broker.get_order_record(f"{intent.intent_id}:stop").status == OrderStatus.CANCELLED
    assert broker.get_order_record(f"{intent.intent_id}:target").status == OrderStatus.CANCELLED
    assert broker.on_candle(bar(20000, 20100, 19000, 20000)) == []       # nothing left to trigger


def test_manual_partial_reduce_then_target_closes_the_remainder():
    broker = _broker()
    _open(broker)
    broker.submit_order(Order(order_id="trim", symbol="MNQ", side=TradeSide.SHORT, order_type=OrderType.MARKET,
                              quantity=5, created_at=DECISION_TIME), reference_price=20010.0)  # fill 20009.75
    (partial,) = broker.open_trades()
    assert partial.status == TradeStatus.PARTIALLY_CLOSED and broker.get_position("MNQ")["quantity"] == 10

    (ev,) = broker.on_candle(bar(20008, 20031, 20001, 20030))
    assert ev.result.fill_quantity == 10                                   # bracket shrank to what is left
    assert [(x.quantity, x.reason) for x in ev.trade.exits] == [(5, EXIT_MANUAL), (10, EXIT_TARGET)]
    gross = (20009.75 - 20000.25) * 5 * 2 + (20030.0 - 20000.25) * 10 * 2   # 95.0 + 595.0
    assert ev.trade.pnl_dollars == pytest.approx(gross - 11.1 - 3.7 - 7.4)


def test_same_side_order_cannot_add_to_a_managed_trade():
    broker = _broker()
    _open(broker)
    r = broker.submit_order(Order(order_id="add", symbol="MNQ", side=TradeSide.LONG, order_type=OrderType.MARKET,
                                  quantity=1, created_at=DECISION_TIME), reference_price=20000.0)
    assert not r.accepted and broker.get_position("MNQ")["quantity"] == 15


def test_bracket_legs_cannot_be_cancelled_individually():
    broker = _broker()
    intent, _ = _open(broker)
    assert broker.cancel_order(f"{intent.intent_id}:stop") is False
    assert broker.cancel_order(f"{intent.intent_id}:target") is False
    assert len(broker.active_bracket_order_ids(intent.intent_id)) == 2


def test_only_closed_trades_reach_the_trade_store(tmp_path):
    broker = _broker(tmp_path)
    store = TradeStore(tmp_path / "trades.ndjson")
    _open(broker)
    assert store.read_all() == []                                          # OPEN trades are never persisted
    (ev,) = broker.on_candle(bar(20008, 20031, 20001, 20030))
    saved = store.read_all()
    assert saved == [ev.trade] and saved[0].status == TradeStatus.CLOSED and saved[0].pnl_dollars == pytest.approx(870.3)


def test_on_candle_requires_a_closed_one_minute_candle():
    broker = _broker()
    _open(broker)
    with pytest.raises(ValueError):
        broker.on_candle(m1(2026, 1, 5, 9, 36, closed=False))
    five = Candle(symbol="MNQ", timeframe=__import__("backend.core.enums", fromlist=["Timeframe"]).Timeframe.M5,
                  open_time=FILL_TIME, close_time=FILL_TIME.replace(minute=40), open=1, high=2, low=1, close=2)
    with pytest.raises(ValueError):
        broker.on_candle(five)


def test_other_symbols_candles_do_not_touch_the_bracket():
    broker = _broker()
    _open(broker)
    assert broker.on_candle(m1(2026, 1, 5, 9, 36, o=100, h=200, l=1, c=100, symbol="ES")) == []


def test_bracket_exits_are_deterministic():
    def run():
        b = _broker()
        _open(b)
        return b.on_candle(bar(20008, 20031, 20001, 20030))[0].trade.model_dump()
    assert run() == run()
