"""Milestone 1: TradeSetup -> RiskEngine -> OrderIntent -> PaperBroker -> Position."""
from dataclasses import replace

import pytest

from backend.config.loader import load_config
from backend.core.enums import OrderStatus, OrderType, TradeSide
from backend.core.models.order import Order
from backend.core.models.order_intent import OrderIntent, UnauthorizedOrderIntentError
from backend.execution.base import LIVE_TRADING_ENABLED
from backend.execution.paper import PaperBroker
from backend.risk.engine import RiskEngine
from tests.helpers import make_daily_state, make_setup


def _stack():
    config = load_config()
    state = make_daily_state(config)
    return config, state, RiskEngine(config, state), PaperBroker.from_config(config)


def test_full_vertical_slice_opens_then_closes_position():
    config, state, engine, broker = _stack()

    decision = engine.evaluate(make_setup())
    assert decision.approved
    intent = decision.intent

    entry = broker.submit_intent(intent, reference_price=20000.00)
    assert entry.accepted and entry.filled
    assert broker.get_order_record(intent.entry_order_id).status == OrderStatus.FILLED
    pos = broker.get_position("MNQ")
    assert pos["side"] == TradeSide.LONG and pos["quantity"] == intent.quantity == 14

    # Opposite-side order closes it (manual close; exit management is a later phase).
    close = Order(order_id="close-1", symbol="MNQ", side=TradeSide.SHORT, order_type=OrderType.MARKET,
                  quantity=intent.quantity, created_at=intent.created_at)
    result = broker.submit_order(close, reference_price=20010.00)
    assert broker.get_position("MNQ") is None

    pnl = result.realized_pnl - entry.commission - result.commission
    state.record_trade_result(pnl)
    assert state.trades_taken == 1


def test_daily_loss_lockout_stops_the_pipeline_before_the_broker():
    config, state, engine, broker = _stack()
    state.record_trade_result(-1300.0)
    decision = engine.evaluate(make_setup())
    assert not decision.approved and decision.intent is None
    assert broker.get_position("MNQ") is None  # nothing could be submitted


def test_rejected_setup_yields_no_intent_to_submit():
    _, _, engine, broker = _stack()
    decision = engine.evaluate(make_setup(proposed_stop=None))
    assert decision.intent is None
    with pytest.raises((AttributeError, PermissionError, TypeError)):
        broker.submit_intent(decision.intent, reference_price=20000.0)


def test_strategy_side_cannot_forge_an_intent():
    with pytest.raises(UnauthorizedOrderIntentError):
        OrderIntent(intent_id="f", setup_id="f", strategy=make_setup().strategy, strategy_version="0",
                    symbol="MNQ", side=TradeSide.LONG, order_type=OrderType.MARKET, quantity=999,
                    entry_price=1.0, stop_price=0.5, targets=(), risk_per_contract_dollars=0.0,
                    approved_risk_dollars=0.0, created_at=make_setup().evaluated_at)


def test_broker_refuses_non_intent_objects():
    _, _, _, broker = _stack()
    with pytest.raises(PermissionError):
        broker.submit_intent(make_setup(), reference_price=20000.0)  # a TradeSetup is not an intent


def test_same_setup_twice_cannot_double_fill():
    _, _, engine, broker = _stack()
    intent = engine.evaluate(make_setup()).intent
    assert broker.submit_intent(intent, reference_price=20000.0).accepted
    assert not broker.submit_intent(intent, reference_price=20000.0).accepted
    assert broker.get_position("MNQ")["quantity"] == intent.quantity


def test_live_guard_stays_hard_off_through_the_pipeline():
    config, _, _, broker = _stack()
    assert LIVE_TRADING_ENABLED is False
    assert config.execution.live_trading_enabled is False
    assert broker.is_live is False
