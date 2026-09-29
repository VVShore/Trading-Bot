"""
PaperBroker: simulated, deterministic execution for backtest and paper mode.

This is the ONLY execution path enabled in V1. It is driven entirely by inputs
(orders + a caller-supplied reference price); it reads no clock and no RNG, so
identical inputs always produce identical fills.

Phase 1 scope
-------------
  - MARKET orders only. LIMIT/STOP/STOP_LIMIT are REJECTED (not silently filled)
    until bar-driven resting-order simulation exists (stop/target management,
    Phase 5). Failing closed beats fake fills.
  - Netting: a same-side order adds to the position (volume-weighted entry);
    an opposite-side order reduces it, closes it, or -- if larger than the
    position -- closes it and opens the remainder on the other side.
  - Order lifecycle is recorded per order (SUBMITTED -> FILLED | REJECTED,
    CANCELLED for future resting orders) in an OrderRecord.
  - Reusing an order_id is rejected and leaves the original record untouched.
  - `submit_intent()` accepts only authentic OrderIntents issued by the RiskEngine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from backend.config.instruments import instrument_spec_from_config
from backend.config.schema import AppConfig
from backend.core.enums import OrderStatus, OrderType, TradeSide
from backend.core.models.instrument import InstrumentSpec
from backend.core.models.order import Order, OrderResult
from backend.core.models.order_intent import OrderIntent
from backend.execution.base import ExecutionBroker


@dataclass
class SimulatedPosition:
    symbol: str
    side: TradeSide
    quantity: int
    entry_price: float


@dataclass
class PaperBrokerConfig:
    """Fallback cost model for symbols with no InstrumentSpec registered."""
    slippage_points: float = 0.25
    commission_per_contract: float = 0.74
    point_value: float = 2.0  # default MNQ


@dataclass
class OrderRecord:
    order: Order
    status: OrderStatus = OrderStatus.SUBMITTED
    result: Optional[OrderResult] = None
    history: list[tuple[OrderStatus, Optional[datetime]]] = field(default_factory=list)

    def _move(self, status: OrderStatus, at: Optional[datetime]) -> None:
        self.status = status
        self.history.append((status, at))


class PaperBroker(ExecutionBroker):
    name = "paper"

    def __init__(
        self,
        config: Optional[PaperBrokerConfig] = None,
        instruments: Optional[dict[str, InstrumentSpec]] = None,
    ) -> None:
        super().__init__()
        self.config = config or PaperBrokerConfig()
        self._instruments: dict[str, InstrumentSpec] = dict(instruments or {})
        self._positions: dict[str, SimulatedPosition] = {}
        self._orders: dict[str, OrderRecord] = {}
        self._realized_pnl_gross = 0.0
        self._commission_paid = 0.0

    @classmethod
    def from_config(cls, app_config: AppConfig) -> "PaperBroker":
        """Shared factory so backtest and paper mode use identical cost assumptions."""
        instruments = {
            sym: spec
            for sym in app_config.execution.instruments
            if not (spec := instrument_spec_from_config(app_config, sym)).analysis_only
        }
        return cls(instruments=instruments)

    @property
    def is_live(self) -> bool:
        return False

    # ------------------------------------------------------------------ costs

    def _costs_for(self, symbol: str) -> tuple[float, float, float]:
        """(point_value, slippage_points, commission_per_contract)."""
        spec = self._instruments.get(symbol)
        if spec is not None:
            return spec.point_value, spec.estimated_slippage_points, spec.commission_per_contract
        c = self.config
        return c.point_value, c.slippage_points, c.commission_per_contract

    # ------------------------------------------------------------------ orders

    def submit_intent(self, intent: OrderIntent, reference_price: Optional[float] = None) -> OrderResult:
        """Entry point for approved orders. Rejects anything not issued by the RiskEngine."""
        if not isinstance(intent, OrderIntent) or not intent.is_authentic:
            raise PermissionError("PaperBroker.submit_intent requires an authentic RiskEngine-issued OrderIntent.")
        order = Order(
            order_id=intent.entry_order_id,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            created_at=intent.created_at,
            trade_id=intent.intent_id,
        )
        if self._instruments and intent.symbol not in self._instruments:
            return self._reject_new(order, f"No executable instrument registered for {intent.symbol}.")
        return self.submit_order(order, reference_price=reference_price)

    def submit_order(self, order: Order, reference_price: Optional[float] = None) -> OrderResult:
        """
        reference_price is the current market price the order fills against. In replay it
        is supplied by the engine (e.g. next bar open); the broker never invents one.
        """
        if order.order_id in self._orders:
            return OrderResult(
                order_id=order.order_id,
                accepted=False,
                rejection_reason="Duplicate order_id; original order left unchanged.",
                timestamp=order.created_at,
            )
        if order.quantity <= 0:
            return self._reject_new(order, "Order quantity must be positive.")
        if order.order_type != OrderType.MARKET:
            return self._reject_new(order, f"PaperBroker supports MARKET orders only (got {order.order_type.value}).")
        if reference_price is None:
            return self._reject_new(order, "No reference price available for simulated fill.")
        if reference_price <= 0:
            return self._reject_new(order, "Reference price must be positive.")

        record = OrderRecord(order=order)
        record._move(OrderStatus.SUBMITTED, order.created_at)
        self._orders[order.order_id] = record

        point_value, slippage_points, commission_per_contract = self._costs_for(order.symbol)
        direction = 1 if order.side == TradeSide.LONG else -1
        fill_price = reference_price + direction * slippage_points
        commission = commission_per_contract * order.quantity
        slippage_amount = abs(fill_price - reference_price) * order.quantity * point_value

        realized = self._apply_fill(order.symbol, order.side, order.quantity, fill_price, point_value)
        self._realized_pnl_gross += realized
        self._commission_paid += commission

        result = OrderResult(
            order_id=order.order_id,
            accepted=True,
            filled=True,
            fill_price=fill_price,
            fill_quantity=order.quantity,
            commission=commission,
            slippage=slippage_amount,
            realized_pnl=realized,
            timestamp=order.created_at,
        )
        record.result = result
        record._move(OrderStatus.FILLED, order.created_at)
        return result

    def _reject_new(self, order: Order, reason: str) -> OrderResult:
        result = OrderResult(
            order_id=order.order_id, accepted=False, rejection_reason=reason, timestamp=order.created_at
        )
        if order.order_id not in self._orders:
            record = OrderRecord(order=order, result=result)
            record._move(OrderStatus.REJECTED, order.created_at)
            self._orders[order.order_id] = record
        return result

    def _apply_fill(self, symbol: str, side: TradeSide, qty: int, fill_price: float, point_value: float) -> float:
        """Update position state for a fill; returns gross realized P&L from any reduction."""
        pos = self._positions.get(symbol)
        if pos is None:
            self._positions[symbol] = SimulatedPosition(symbol, side, qty, fill_price)
            return 0.0

        if pos.side == side:
            total = pos.quantity + qty
            pos.entry_price = (pos.entry_price * pos.quantity + fill_price * qty) / total
            pos.quantity = total
            return 0.0

        closing = min(pos.quantity, qty)
        pos_dir = 1 if pos.side == TradeSide.LONG else -1
        realized = (fill_price - pos.entry_price) * pos_dir * closing * point_value
        pos.quantity -= closing
        remainder = qty - closing
        if pos.quantity == 0:
            del self._positions[symbol]
        if remainder > 0:
            self._positions[symbol] = SimulatedPosition(symbol, side, remainder, fill_price)
        return realized

    def cancel_order(self, order_id: str) -> bool:
        """Only a still-working order can be cancelled; market orders fill immediately, so
        today this is False for every order. Kept correct for future resting orders."""
        record = self._orders.get(order_id)
        if record is None or record.status != OrderStatus.SUBMITTED:
            return False
        record._move(OrderStatus.CANCELLED, record.order.created_at)
        return True

    # ------------------------------------------------------------------ state

    def get_position(self, symbol: str) -> Optional[dict]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None
        return {"symbol": pos.symbol, "side": pos.side, "quantity": pos.quantity, "entry_price": pos.entry_price}

    def get_order_record(self, order_id: str) -> Optional[OrderRecord]:
        return self._orders.get(order_id)

    @property
    def realized_pnl_gross(self) -> float:
        return self._realized_pnl_gross

    @property
    def commission_paid(self) -> float:
        return self._commission_paid
