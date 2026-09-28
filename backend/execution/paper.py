"""
PaperBroker: simulated execution for backtesting and forward-test paper mode.

This is the ONLY execution path enabled in V1. Backtest and forward-test
should both construct this broker through the same factory so simulation
assumptions (slippage, commission) stay consistent between the two modes.

NOTE: this initial version implements market and stop/target fills against
a single "current price" tick. Partial fills, limit-order queueing behavior,
and richer rejection scenarios are left as TODO and covered by
docs/ASSUMPTIONS.md until the backtest engine (Step 10) needs them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import uuid4

from backend.core.enums import OrderType, TradeSide
from backend.core.models.order import Order, OrderResult
from backend.execution.base import ExecutionBroker


@dataclass
class SimulatedPosition:
    symbol: str
    side: TradeSide
    quantity: int
    entry_price: float


@dataclass
class PaperBrokerConfig:
    slippage_points: float = 0.25
    commission_per_contract: float = 0.74
    point_value: float = 2.0  # default MNQ; caller should set per-instrument


class PaperBroker(ExecutionBroker):
    name = "paper"

    def __init__(self, config: Optional[PaperBrokerConfig] = None) -> None:
        super().__init__()
        self.config = config or PaperBrokerConfig()
        self._positions: dict[str, SimulatedPosition] = {}
        self._orders: dict[str, Order] = {}

    @property
    def is_live(self) -> bool:
        return False

    def _apply_slippage(self, price: float, side: TradeSide, order_type: OrderType) -> float:
        if order_type != OrderType.MARKET:
            return price
        direction = 1 if side == TradeSide.LONG else -1
        return price + direction * self.config.slippage_points

    def submit_order(self, order: Order, reference_price: Optional[float] = None) -> OrderResult:
        """
        reference_price simulates "current market price" the paper broker fills against.
        In backtest mode this will be supplied by the replay engine tick-by-tick.
        """
        self._orders[order.order_id] = order

        if reference_price is None:
            return OrderResult(
                order_id=order.order_id,
                accepted=False,
                rejection_reason="No reference price available for simulated fill.",
                timestamp=order.created_at,
            )

        fill_price = self._apply_slippage(reference_price, order.side, order.order_type)
        commission = self.config.commission_per_contract * order.quantity
        slippage_amount = abs(fill_price - reference_price) * order.quantity * self.config.point_value

        position = self._positions.get(order.symbol)
        if position is None:
            self._positions[order.symbol] = SimulatedPosition(
                symbol=order.symbol, side=order.side, quantity=order.quantity, entry_price=fill_price
            )
        else:
            # Naive netting; richer partial-close handling belongs to TradeManager, not the broker.
            position.quantity += order.quantity

        return OrderResult(
            order_id=order.order_id,
            accepted=True,
            filled=True,
            fill_price=fill_price,
            fill_quantity=order.quantity,
            commission=commission,
            slippage=slippage_amount,
            timestamp=order.created_at,
        )

    def cancel_order(self, order_id: str) -> bool:
        return self._orders.pop(order_id, None) is not None

    def get_position(self, symbol: str) -> Optional[dict]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None
        return {"symbol": pos.symbol, "side": pos.side, "quantity": pos.quantity, "entry_price": pos.entry_price}
