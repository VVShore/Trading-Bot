import pytest

from backend.config.loader import load_config
from backend.core.enums import OrderStatus, OrderType, TradeSide
from backend.core.models.order import Order
from backend.execution.base import ExecutionBroker, LiveTradingDisabledError
from backend.execution.paper import PaperBroker
from tests.helpers import DECISION_TIME


def _broker():
    return PaperBroker.from_config(load_config())


def _order(side, qty, oid=None, otype=OrderType.MARKET):
    kwargs = dict(symbol="MNQ", side=side, order_type=otype, quantity=qty, created_at=DECISION_TIME)
    if oid:
        kwargs["order_id"] = oid
    return Order(**kwargs)


def test_open_long_position_with_slippage_and_costs():
    b = _broker()
    r = b.submit_order(_order(TradeSide.LONG, 2, "o1"), reference_price=20000.0)
    assert r.filled and r.fill_price == 20000.25
    assert r.commission == pytest.approx(1.48)
    assert b.get_position("MNQ") == {"symbol": "MNQ", "side": TradeSide.LONG, "quantity": 2, "entry_price": 20000.25}


def test_opposite_order_reduces_position_and_realizes_pnl():
    b = _broker()
    b.submit_order(_order(TradeSide.LONG, 3, "o1"), reference_price=20000.0)   # entry 20000.25
    r = b.submit_order(_order(TradeSide.SHORT, 1, "o2"), reference_price=20010.0)  # fill 20009.75
    pos = b.get_position("MNQ")
    assert pos["side"] == TradeSide.LONG and pos["quantity"] == 2
    assert pos["entry_price"] == 20000.25  # reduction keeps entry
    assert r.realized_pnl == pytest.approx((20009.75 - 20000.25) * 1 * 2.0)


def test_opposite_order_closes_position_flat():
    b = _broker()
    b.submit_order(_order(TradeSide.LONG, 2, "o1"), reference_price=20000.0)
    b.submit_order(_order(TradeSide.SHORT, 2, "o2"), reference_price=19990.0)  # fill 19989.75
    assert b.get_position("MNQ") is None
    assert b.realized_pnl_gross == pytest.approx((19989.75 - 20000.25) * 2 * 2.0)


def test_oversized_opposite_order_flips_position():
    b = _broker()
    b.submit_order(_order(TradeSide.LONG, 1, "o1"), reference_price=20000.0)
    b.submit_order(_order(TradeSide.SHORT, 3, "o2"), reference_price=20000.0)  # fill 19999.75
    pos = b.get_position("MNQ")
    assert pos["side"] == TradeSide.SHORT and pos["quantity"] == 2 and pos["entry_price"] == 19999.75


def test_same_side_adds_with_weighted_average_entry():
    b = _broker()
    b.submit_order(_order(TradeSide.LONG, 1, "o1"), reference_price=20000.0)  # 20000.25
    b.submit_order(_order(TradeSide.LONG, 1, "o2"), reference_price=20010.0)  # 20010.25
    pos = b.get_position("MNQ")
    assert pos["quantity"] == 2 and pos["entry_price"] == pytest.approx(20005.25)


def test_short_trade_pnl_sign():
    b = _broker()
    b.submit_order(_order(TradeSide.SHORT, 1, "o1"), reference_price=20000.0)  # 19999.75
    r = b.submit_order(_order(TradeSide.LONG, 1, "o2"), reference_price=19990.0)  # 19990.25
    assert r.realized_pnl == pytest.approx((19990.25 - 19999.75) * -1 * 2.0)  # profit


def test_order_lifecycle_recorded():
    b = _broker()
    b.submit_order(_order(TradeSide.LONG, 1, "o1"), reference_price=20000.0)
    rec = b.get_order_record("o1")
    assert rec.status == OrderStatus.FILLED
    assert [s for s, _ in rec.history] == [OrderStatus.SUBMITTED, OrderStatus.FILLED]
    assert b.cancel_order("o1") is False  # already filled
    assert b.cancel_order("nope") is False


def test_non_market_orders_are_rejected_not_faked():
    b = _broker()
    r = b.submit_order(_order(TradeSide.LONG, 1, "o1", OrderType.LIMIT), reference_price=20000.0)
    assert not r.accepted
    assert b.get_position("MNQ") is None
    assert b.get_order_record("o1").status == OrderStatus.REJECTED


def test_duplicate_order_id_rejected_and_state_unchanged():
    b = _broker()
    b.submit_order(_order(TradeSide.LONG, 1, "dup"), reference_price=20000.0)
    r = b.submit_order(_order(TradeSide.LONG, 5, "dup"), reference_price=20000.0)
    assert not r.accepted
    assert b.get_position("MNQ")["quantity"] == 1


def test_invalid_inputs_rejected():
    b = _broker()
    assert not b.submit_order(_order(TradeSide.LONG, 0, "a"), reference_price=20000.0).accepted
    assert not b.submit_order(_order(TradeSide.LONG, 1, "b"), reference_price=None).accepted
    assert not b.submit_order(_order(TradeSide.LONG, 1, "c"), reference_price=-1.0).accepted


def test_fills_are_deterministic():
    def run():
        b = _broker()
        return [
            b.submit_order(_order(TradeSide.LONG, 2, "o1"), reference_price=20000.0),
            b.submit_order(_order(TradeSide.SHORT, 1, "o2"), reference_price=20005.0),
        ]
    assert run() == run()


def test_live_broker_subclass_cannot_be_constructed():
    class FakeLive(ExecutionBroker):
        name = "fake_live"
        is_live = property(lambda self: True)
        def submit_order(self, order): ...
        def cancel_order(self, order_id): ...
        def get_position(self, symbol): ...

    with pytest.raises(LiveTradingDisabledError):
        FakeLive()
