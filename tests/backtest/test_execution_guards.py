import pytest

from backend.core.enums import OrderType, TradeSide
from backend.core.models.order import Order
from backend.execution.base import LIVE_TRADING_ENABLED
from backend.execution.paper import PaperBroker, PaperBrokerConfig
from backend.execution.tradovate import TradovateBroker


def test_live_trading_hard_guard_is_false():
    assert LIVE_TRADING_ENABLED is False


def test_paper_broker_is_not_live():
    broker = PaperBroker()
    assert broker.is_live is False


def test_tradovate_broker_construction_raises():
    with pytest.raises(NotImplementedError):
        TradovateBroker()


def test_paper_broker_fills_market_order():
    broker = PaperBroker(config=PaperBrokerConfig(slippage_points=0.25, commission_per_contract=0.74, point_value=2.0))
    order = Order(symbol="MNQ", side=TradeSide.LONG, order_type=OrderType.MARKET, quantity=2)
    result = broker.submit_order(order, reference_price=20000.0)
    assert result.accepted is True
    assert result.filled is True
    assert result.fill_price == 20000.25
    assert result.commission == pytest.approx(1.48)


def test_paper_broker_rejects_without_reference_price():
    broker = PaperBroker()
    order = Order(symbol="MNQ", side=TradeSide.LONG, order_type=OrderType.MARKET, quantity=1)
    result = broker.submit_order(order)
    assert result.accepted is False
