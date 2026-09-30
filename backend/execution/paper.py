"""
PaperBroker: simulated, deterministic execution for backtest and paper mode.

This is the ONLY execution path enabled in V1. It is driven entirely by inputs
(orders, a caller-supplied reference price, closed candles); it reads no clock and no RNG,
so identical inputs always produce identical fills.

Entries
-------
  - `submit_intent()` accepts only authentic RiskEngine-issued OrderIntents, fills them as a
    MARKET order against the supplied reference price (+ configured slippage), and -- because
    entries require a flat position -- opens exactly one managed trade per fill.
  - Low-level `submit_order()` is MARKET-only (LIMIT/STOP/STOP_LIMIT are rejected, not faked).
    Netting: an opposite-side order reduces / closes / flips the position; a same-side order adds
    (volume-weighted entry) unless a managed trade is open, in which case it is rejected.
  - Reusing an order_id is rejected and leaves the original record untouched.

Brackets (owner resolution R6)
------------------------------
  On an intent fill the broker registers an OCO pair for the position:
    * STOP-MARKET  at intent.stop_price   -> fills with configured adverse slippage; if the bar
      opens THROUGH the stop it fills from the open (worse than the stop), plus slippage.
    * LIMIT        at the target          -> fills at EXACTLY the target price, zero slippage
      (no price improvement is granted either).
  `on_candle(candle)` evaluates them against each CLOSED 1M candle via management/bar_exit.py.
  If one candle reaches both, the stop is assumed hit first (owner decision 8) and a SafetyEvent
  is returned for the caller to act on. Exit fills are stamped with the candle's close time
  (intrabar time is unknowable at bar resolution).
  Target choice (provisional, docs/ASSUMPTIONS.md P3): an intent with several targets brackets
  the FULL quantity on the highest-priority target (lowest `priority` number); none -> stop only.

Trade lifecycle
---------------
  A TradeRecord is OPEN from the entry fill and CLOSED when the position is flat, with
  `pnl_dollars` NET of entry + exit commission (gross P&L minus costs) and an R multiple.
  Closed records are written to the optional TradeStore; open ones never are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from backend.config.instruments import instrument_spec_from_config
from backend.config.schema import AppConfig
from backend.core.enums import OrderStatus, OrderType, TradeSide, TradeStatus
from backend.core.models.candle import Candle
from backend.core.models.instrument import InstrumentSpec
from backend.core.models.order import Order, OrderResult
from backend.core.models.order_intent import OrderIntent
from backend.core.models.trade import ExitFill, TradeRecord
from backend.decision_log.trades import TradeStore
from backend.execution.base import ExecutionBroker
from backend.execution.events import EXIT_MANUAL, EXIT_STOP, EXIT_STOP_SAME_BAR, EXIT_TARGET, ExitEvent
from backend.management.bar_exit import BarExitOutcome, evaluate_bar_exit


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


@dataclass
class _Bracket:
    trade_id: str
    symbol: str
    position_side: TradeSide
    quantity: int
    stop_price: float
    target_price: Optional[float]
    stop_order_id: str
    target_order_id: Optional[str]


@dataclass
class _TradeState:
    record: TradeRecord
    remaining: int
    entry_commission: float
    gross: float = 0.0
    exit_commission: float = 0.0


def _opposite(side: TradeSide) -> TradeSide:
    return TradeSide.SHORT if side == TradeSide.LONG else TradeSide.LONG


class PaperBroker(ExecutionBroker):
    name = "paper"

    def __init__(
        self,
        config: Optional[PaperBrokerConfig] = None,
        instruments: Optional[dict[str, InstrumentSpec]] = None,
        trade_store: Optional[TradeStore] = None,
        parameter_snapshot: Optional[dict] = None,
    ) -> None:
        super().__init__()
        self.config = config or PaperBrokerConfig()
        self._instruments: dict[str, InstrumentSpec] = dict(instruments or {})
        self._trade_store = trade_store
        self._parameter_snapshot = dict(parameter_snapshot or {})
        self._positions: dict[str, SimulatedPosition] = {}
        self._orders: dict[str, OrderRecord] = {}
        self._brackets: dict[str, _Bracket] = {}
        self._trades: dict[str, _TradeState] = {}
        self._closed_trades: list[TradeRecord] = []
        self._realized_pnl_gross = 0.0
        self._commission_paid = 0.0

    @classmethod
    def from_config(cls, app_config: AppConfig, trade_store: Optional[TradeStore] = None) -> "PaperBroker":
        """Shared factory so backtest and paper mode use identical cost assumptions."""
        instruments = {
            sym: spec
            for sym in app_config.execution.instruments
            if not (spec := instrument_spec_from_config(app_config, sym)).analysis_only
        }
        return cls(instruments=instruments, trade_store=trade_store, parameter_snapshot=app_config.parameter_snapshot())

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

    # ------------------------------------------------------------------ entries

    def submit_intent(
        self, intent: OrderIntent, reference_price: Optional[float] = None, at: Optional[datetime] = None
    ) -> OrderResult:
        """Entry point for approved orders. `at` is the simulated fill time (defaults to the decision time)."""
        if not isinstance(intent, OrderIntent) or not intent.is_authentic:
            raise PermissionError("PaperBroker.submit_intent requires an authentic RiskEngine-issued OrderIntent.")
        when = at or intent.created_at
        order = Order(
            order_id=intent.entry_order_id,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            created_at=when,
            trade_id=intent.intent_id,
        )
        if self._instruments and intent.symbol not in self._instruments:
            return self._reject_new(order, f"No executable instrument registered for {intent.symbol}.")
        if intent.symbol in self._positions:
            return self._reject_new(order, f"Position already open in {intent.symbol}; entries require a flat position.")
        result = self.submit_order(order, reference_price=reference_price)
        if result.filled:
            self._open_trade(intent, order, result)
        return result

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
        managed = self._managed_trade_for(order.symbol)
        before = self._positions.get(order.symbol)
        if managed is not None and before is not None and before.side == order.side:
            return self._reject_new(order, "Order would add to a managed trade; not supported.")

        record = OrderRecord(order=order)
        record._move(OrderStatus.SUBMITTED, order.created_at)
        self._orders[order.order_id] = record

        point_value, slippage_points, commission_per_contract = self._costs_for(order.symbol)
        direction = 1 if order.side == TradeSide.LONG else -1
        fill_price = reference_price + direction * slippage_points
        commission = commission_per_contract * order.quantity
        slippage_amount = abs(fill_price - reference_price) * order.quantity * point_value

        before_side = before.side if before is not None else None
        before_qty = before.quantity if before is not None else 0
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

        if managed is not None and before_side is not None and before_side != order.side:
            closed_qty = min(before_qty, order.quantity)
            closed = self._register_exit(
                managed.record.trade_id, order.created_at, fill_price, closed_qty, EXIT_MANUAL,
                realized, commission_per_contract * closed_qty,
            )
            if closed is not None:
                self._cancel_bracket(managed.record.trade_id, order.created_at)
            else:
                pos = self._positions.get(order.symbol)
                bracket = self._brackets.get(managed.record.trade_id)
                if bracket is not None and pos is not None:
                    bracket.quantity = min(bracket.quantity, pos.quantity)
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

    # ------------------------------------------------------------------ trades + brackets

    def _managed_trade_for(self, symbol: str) -> Optional[_TradeState]:
        for state in self._trades.values():
            if state.record.symbol == symbol:
                return state
        return None

    def _open_trade(self, intent: OrderIntent, order: Order, result: OrderResult) -> None:
        when = order.created_at
        record = TradeRecord(
            trade_id=intent.intent_id,
            strategy=intent.strategy,
            strategy_version=intent.strategy_version,
            symbol=intent.symbol,
            side=intent.side,
            status=TradeStatus.OPEN,
            entry_time=when,
            entry_price=result.fill_price,
            initial_stop=intent.stop_price,
            initial_risk_dollars=intent.approved_risk_dollars,
            quantity=result.fill_quantity,
            targets=list(intent.targets),
            parameter_snapshot=dict(self._parameter_snapshot),
            decision_log_ref=intent.setup_id,
        )
        self._trades[intent.intent_id] = _TradeState(record, result.fill_quantity, result.commission)

        target = min(intent.targets, key=lambda t: t.priority) if intent.targets else None
        exit_side = _opposite(intent.side)
        stop_id = f"{intent.intent_id}:stop"
        stop_order = Order(order_id=stop_id, symbol=intent.symbol, side=exit_side, order_type=OrderType.STOP,
                           quantity=result.fill_quantity, stop_price=intent.stop_price, created_at=when,
                           trade_id=intent.intent_id)
        self._register_working(stop_order)
        target_id = None
        if target is not None:
            target_id = f"{intent.intent_id}:target"
            self._register_working(Order(order_id=target_id, symbol=intent.symbol, side=exit_side,
                                         order_type=OrderType.LIMIT, quantity=result.fill_quantity,
                                         limit_price=target.price, created_at=when, trade_id=intent.intent_id))
        self._brackets[intent.intent_id] = _Bracket(
            trade_id=intent.intent_id, symbol=intent.symbol, position_side=intent.side,
            quantity=result.fill_quantity, stop_price=intent.stop_price,
            target_price=target.price if target is not None else None,
            stop_order_id=stop_id, target_order_id=target_id,
        )

    def _register_working(self, order: Order) -> None:
        record = OrderRecord(order=order)
        record._move(OrderStatus.SUBMITTED, order.created_at)
        self._orders[order.order_id] = record

    def _cancel_bracket(self, trade_id: str, at: Optional[datetime]) -> None:
        bracket = self._brackets.pop(trade_id, None)
        if bracket is None:
            return
        for leg_id in (bracket.stop_order_id, bracket.target_order_id):
            rec = self._orders.get(leg_id) if leg_id else None
            if rec is not None and rec.status == OrderStatus.SUBMITTED:
                rec._move(OrderStatus.CANCELLED, at)

    def _register_exit(
        self, trade_id: str, at: datetime, price: float, qty: int, reason: str, gross: float, commission: float
    ) -> Optional[TradeRecord]:
        """Record an exit fill on the trade; returns the CLOSED record once the trade is flat, else None."""
        state = self._trades[trade_id]
        rec = state.record
        rec.exits.append(ExitFill(time=at, price=price, quantity=qty, reason=reason))
        state.remaining -= qty
        state.gross += gross
        state.exit_commission += commission
        if state.remaining > 0:
            rec.status = TradeStatus.PARTIALLY_CLOSED
            return None
        net = state.gross - state.entry_commission - state.exit_commission
        rec.status = TradeStatus.CLOSED
        rec.exit_time = at
        rec.exit_reason = reason
        rec.pnl_dollars = net
        rec.r_multiple = net / rec.initial_risk_dollars if rec.initial_risk_dollars else None
        del self._trades[trade_id]
        self._closed_trades.append(rec)
        if self._trade_store is not None:
            self._trade_store.save(rec)
        return rec

    def on_candle(self, candle: Candle) -> list[ExitEvent]:
        """
        Evaluate every active bracket on this symbol against a CLOSED 1M candle. Call once per
        candle, AFTER any entry that filled at this candle's open (the entry bar can hit a level).
        """
        events: list[ExitEvent] = []
        for bracket in list(self._brackets.values()):
            if bracket.symbol != candle.symbol:
                continue
            pos = self._positions.get(bracket.symbol)
            if pos is None or pos.side != bracket.position_side:
                self._cancel_bracket(bracket.trade_id, candle.close_time)  # defensive: nothing left to protect
                continue
            res = evaluate_bar_exit(bracket.position_side, bracket.stop_price, bracket.target_price, candle)
            if res.outcome == BarExitOutcome.NONE:
                continue
            events.append(self._fill_bracket_exit(bracket, pos, res, candle))
        return events

    def _fill_bracket_exit(self, bracket: _Bracket, pos: SimulatedPosition, res, candle: Candle) -> ExitEvent:
        point_value, slippage_points, commission_per_contract = self._costs_for(bracket.symbol)
        is_long = bracket.position_side == TradeSide.LONG
        if res.outcome == BarExitOutcome.TARGET:
            filled_leg, other_leg = bracket.target_order_id, bracket.stop_order_id
            fill_price, slip_points, reason = bracket.target_price, 0.0, EXIT_TARGET  # limit: exact, no slippage
        else:
            filled_leg, other_leg = bracket.stop_order_id, bracket.target_order_id
            triggered_at = min(bracket.stop_price, candle.open) if is_long else max(bracket.stop_price, candle.open)
            fill_price = triggered_at + (-slippage_points if is_long else slippage_points)  # adverse
            slip_points = slippage_points
            reason = EXIT_STOP_SAME_BAR if res.outcome == BarExitOutcome.AMBIGUOUS_STOP_FIRST else EXIT_STOP

        qty = min(bracket.quantity, pos.quantity)
        commission = commission_per_contract * qty
        slippage_amount = slip_points * qty * point_value
        realized = self._apply_fill(bracket.symbol, _opposite(bracket.position_side), qty, fill_price, point_value)
        self._realized_pnl_gross += realized
        self._commission_paid += commission

        result = OrderResult(
            order_id=filled_leg, accepted=True, filled=True, fill_price=fill_price, fill_quantity=qty,
            commission=commission, slippage=slippage_amount, realized_pnl=realized, timestamp=candle.close_time,
        )
        leg = self._orders[filled_leg]
        leg.result = result
        leg._move(OrderStatus.FILLED, candle.close_time)
        other = self._orders.get(other_leg) if other_leg else None
        if other is not None and other.status == OrderStatus.SUBMITTED:  # OCO
            other._move(OrderStatus.CANCELLED, candle.close_time)
        del self._brackets[bracket.trade_id]

        closed = self._register_exit(bracket.trade_id, candle.close_time, fill_price, qty, reason, realized, commission)
        assert closed is not None, "a bracket exit always flattens the managed position"
        return ExitEvent(trade=closed, result=result, reason=reason, net_pnl=closed.pnl_dollars or 0.0, bar_result=res)

    def cancel_order(self, order_id: str) -> bool:
        """Only a still-working, non-bracket order can be cancelled. Bracket legs belong to the broker
        (removing a stop would strip protection); market orders fill immediately. Kept correct for
        future resting orders."""
        record = self._orders.get(order_id)
        if record is None or record.status != OrderStatus.SUBMITTED:
            return False
        if any(order_id in (b.stop_order_id, b.target_order_id) for b in self._brackets.values()):
            return False
        record._move(OrderStatus.CANCELLED, record.order.created_at)
        return True

    # ------------------------------------------------------------------ state

    def get_position(self, symbol: str) -> Optional[dict]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None
        return {"symbol": pos.symbol, "side": pos.side, "quantity": pos.quantity, "entry_price": pos.entry_price}

    def open_position_count(self) -> int:
        return len(self._positions)

    def open_trades(self) -> list[TradeRecord]:
        return [s.record for s in self._trades.values()]

    def closed_trades(self) -> list[TradeRecord]:
        return list(self._closed_trades)

    def active_bracket_order_ids(self, trade_id: str) -> list[str]:
        b = self._brackets.get(trade_id)
        return [] if b is None else [i for i in (b.stop_order_id, b.target_order_id) if i]

    def get_order_record(self, order_id: str) -> Optional[OrderRecord]:
        return self._orders.get(order_id)

    @property
    def realized_pnl_gross(self) -> float:
        return self._realized_pnl_gross

    @property
    def commission_paid(self) -> float:
        return self._commission_paid
