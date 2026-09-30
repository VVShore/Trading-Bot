"""
RiskEngine: the independent gate between a strategy's TradeSetup and an order.

    TradeSetup -> RiskEngine.evaluate() -> RiskDecision(rejected | OrderIntent)
    OrderIntent -> RiskEngine.reconcile_fill(next-bar open) -> unchanged | re-sized down | rejected

The RiskEngine is the ONLY issuer of OrderIntent (see core/models/order_intent.py),
and brokers only accept authentic OrderIntents, so strategy code has no path to
a broker that skips this gate. A TradeSetup with decision == "TRADE" is a proposal,
nothing more.

Owner policy locks enforced here (docs/ASSUMPTIONS.md, "Policy locks"):
  1  sizing: N = floor(dollar_risk / (SL points x point_value)); if the fill gaps so the stop
     distance would exceed the risk ceiling, re-size DOWN at fill or reject (reconcile_fill)
  2  off-tick prices are rounded deterministically (entry toward market, stop away from
     entry, target toward entry) BEFORE sizing, so the ceiling holds on the rounded stop
  3  max concurrent positions (open + approved-but-unsettled); max trades/day via DailyRiskState
  4  daily loss limit and "remaining budget can't cover one trade" lockout via DailyRiskState
  5  only execution.active_symbol may trade (allow_nq_manual_override is ignored)
  7  pause_trading manual halt

Sizing deliberately excludes commission and slippage (policy lock 1 is a pure
points x $ formula); the paper broker still charges them on top.

Fails closed: every unmet or unverifiable condition is a rejection, and all
independent rejection reasons found are reported together.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, replace
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from backend.config.instruments import instrument_spec_from_config
from backend.config.schema import AppConfig
from backend.core.enums import OrderType, TradeSide
from backend.core.models.instrument import InstrumentSpec
from backend.core.models.order_intent import OrderIntent, issue_order_intent
from backend.core.models.setup import TradeSetup
from backend.core.models.target import Target
from backend.risk.daily_limits import DailyRiskState
from backend.risk.position_sizing import PositionSizeResult, calculate_position_size


@dataclass(frozen=True)
class RiskDecision:
    setup_id: str
    approved: bool
    intent: Optional[OrderIntent] = None
    rejection_reasons: tuple[str, ...] = ()
    sizing: Optional[PositionSizeResult] = None
    adjustments: tuple[str, ...] = ()  # e.g. tick roundings or fill-time re-sizing, for the decision log

    def to_dict(self) -> dict:
        return {
            "setup_id": self.setup_id,
            "approved": self.approved,
            "rejection_reasons": list(self.rejection_reasons),
            "adjustments": list(self.adjustments),
            "intent": self.intent.to_dict() if self.intent is not None else None,
            "sizing": dataclasses.asdict(self.sizing) if self.sizing is not None else None,
        }


@dataclass(frozen=True)
class _PreparedPrices:
    entry: float
    stop: float
    targets: tuple[Target, ...]
    adjustments: tuple[str, ...]


class RiskEngine:
    def __init__(
        self,
        config: AppConfig,
        daily_state: DailyRiskState,
        open_position_count: Callable[[], int],
    ) -> None:
        """
        open_position_count: live count of open positions (e.g. PaperBroker.open_position_count).
        Required, not defaulted: an engine that cannot see positions cannot enforce the cap.
        """
        self._config = config
        self._daily = daily_state
        self._open_positions = open_position_count
        self._tz = ZoneInfo(config.session.timezone)
        self._outstanding: set[str] = set()  # approved intents not yet settled

    # ------------------------------------------------------------------ evaluate

    def evaluate(self, setup: TradeSetup, market_price: Optional[float] = None) -> RiskDecision:
        """
        market_price: current market price, needed ONLY to round an off-tick entry toward the
        market (policy lock 2). An off-tick entry with no market price is rejected.
        """
        reasons = self._gate_reasons(setup.evaluated_at)

        if setup.decision != "TRADE":
            reasons.append(f"Setup decision is '{setup.decision}', not 'TRADE'.")

        spec = self._resolve_instrument(setup.symbol, reasons)
        prepared: Optional[_PreparedPrices] = None
        if spec is not None:
            prepared, problems = self._prepare_prices(setup, spec, market_price)
            reasons.extend(problems)
        else:
            reasons.extend(self._missing_field_reasons(setup))

        if reasons or prepared is None:
            return self._reject(setup.setup_id, reasons)

        assert spec is not None and setup.proposed_side is not None
        sizing = self._size(prepared.entry, prepared.stop, spec)
        if not sizing.accepted:
            return self._reject(setup.setup_id, [f"Position sizing rejected: {sizing.rejection_reason}"], sizing, prepared.adjustments)

        quantity = spec.round_down_quantity(sizing.contracts)
        if quantity == 0:
            return self._reject(
                setup.setup_id,
                [f"Sized quantity {sizing.contracts} is below the instrument's minimum quantity."],
                sizing,
                prepared.adjustments,
            )

        intent = issue_order_intent(
            intent_id=f"intent-{setup.setup_id}",
            setup_id=setup.setup_id,
            strategy=setup.strategy,
            strategy_version=setup.strategy_version,
            symbol=setup.symbol,
            side=setup.proposed_side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            entry_price=prepared.entry,
            stop_price=prepared.stop,
            targets=prepared.targets,
            risk_per_contract_dollars=sizing.risk_per_contract_dollars,
            approved_risk_dollars=quantity * sizing.risk_per_contract_dollars,
            created_at=setup.evaluated_at,
        )
        self._outstanding.add(intent.intent_id)
        return RiskDecision(
            setup_id=setup.setup_id, approved=True, intent=intent, sizing=sizing, adjustments=prepared.adjustments
        )

    # ------------------------------------------------------------------ fill-time re-check

    def reconcile_fill(self, intent: OrderIntent, fill_reference_price: float) -> RiskDecision:
        """
        Policy lock 1 (slippage rule). Call with the price the entry will actually fill against
        (next-bar open in replay). The stop distance is recomputed from that price:
          - distance not larger      -> intent unchanged (size is never increased)
          - distance larger          -> quantity re-sized DOWN to keep risk <= ceiling
          - ceiling can't be met / price gapped through the stop -> rejected
        The intent stays "outstanding" (counts toward max concurrent positions) until
        settle_intent() is called; a rejection settles it automatically.
        """
        if not isinstance(intent, OrderIntent) or not intent.is_authentic:
            return self._reject(getattr(intent, "setup_id", "?"), ["Not an authentic RiskEngine-issued OrderIntent."])
        if intent.intent_id not in self._outstanding:
            return self._reject(intent.setup_id, ["Intent is unknown or already settled."])

        reasons = self._gate_reasons(None, include_positions=False)
        spec = self._resolve_instrument(intent.symbol, reasons)
        if not (math.isfinite(fill_reference_price) and fill_reference_price > 0):
            reasons.append("Fill reference price must be finite and positive.")
        if reasons or spec is None:
            self._outstanding.discard(intent.intent_id)
            return self._reject(intent.setup_id, reasons)

        distance = (
            fill_reference_price - intent.stop_price
            if intent.side == TradeSide.LONG
            else intent.stop_price - fill_reference_price
        )
        if distance <= 0:
            self._outstanding.discard(intent.intent_id)
            return self._reject(intent.setup_id, ["Fill price is at or through the stop; entry rejected."])

        sizing = self._size(fill_reference_price, intent.stop_price, spec)
        quantity = spec.round_down_quantity(sizing.contracts) if sizing.accepted else 0
        if quantity == 0:
            self._outstanding.discard(intent.intent_id)
            return self._reject(
                intent.setup_id,
                [f"Stop distance at fill ({distance:.2f} pts) exceeds the risk ceiling for any valid size."],
                sizing,
            )

        if quantity >= intent.quantity:
            return RiskDecision(setup_id=intent.setup_id, approved=True, intent=intent, sizing=sizing)

        resized = replace(
            intent,
            quantity=quantity,
            entry_price=fill_reference_price,
            risk_per_contract_dollars=sizing.risk_per_contract_dollars,
            approved_risk_dollars=quantity * sizing.risk_per_contract_dollars,
        )
        note = (
            f"Re-sized at fill: stop distance {abs(intent.entry_price - intent.stop_price):.2f} -> "
            f"{distance:.2f} pts, quantity {intent.quantity} -> {quantity}."
        )
        return RiskDecision(
            setup_id=intent.setup_id, approved=True, intent=resized, sizing=sizing, adjustments=(note,)
        )

    def settle_intent(self, intent_id: str) -> None:
        """Release an approved intent once it has filled (now visible as a position) or died."""
        self._outstanding.discard(intent_id)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _reject(
        setup_id: str,
        reasons: list[str],
        sizing: Optional[PositionSizeResult] = None,
        adjustments: tuple[str, ...] = (),
    ) -> RiskDecision:
        return RiskDecision(
            setup_id=setup_id, approved=False, rejection_reasons=tuple(reasons), sizing=sizing, adjustments=adjustments
        )

    def _gate_reasons(self, evaluated_at, include_positions: bool = True) -> list[str]:
        reasons: list[str] = []
        if self._config.risk.pause_trading:
            reasons.append("Trading is manually paused (risk.pause_trading).")
        if not self._daily.can_trade():
            reasons.append(f"Daily risk lockout active: {self._daily.lockout_reason}")
        if evaluated_at is not None:
            if evaluated_at.tzinfo is None:
                reasons.append("Setup evaluated_at must be timezone-aware.")
            elif evaluated_at.astimezone(self._tz).date() != self._daily.trading_day:
                reasons.append(
                    f"Daily risk state is for {self._daily.trading_day}, but setup was evaluated "
                    f"on {evaluated_at.astimezone(self._tz).date()} ({self._config.session.timezone})."
                )
        if include_positions:
            exposure = self._open_positions() + len(self._outstanding)
            cap = self._config.risk.max_concurrent_positions
            if exposure >= cap:
                reasons.append(f"Max concurrent positions reached ({exposure}/{cap}, including unsettled intents).")
        return reasons

    def _size(self, entry: float, stop: float, spec: InstrumentSpec) -> PositionSizeResult:
        # Policy lock 1: pure points x point_value; no commission/slippage in the ceiling.
        return calculate_position_size(
            entry_price=entry,
            stop_price=stop,
            point_value=spec.point_value,
            max_risk_dollars=self._config.risk.resolved_risk_dollars,
        )

    def _resolve_instrument(self, symbol: str, reasons: list[str]) -> Optional[InstrumentSpec]:
        try:
            spec = instrument_spec_from_config(self._config, symbol)
        except ValueError as exc:
            reasons.append(str(exc))
            return None
        if spec.analysis_only:
            reasons.append(f"{symbol} is analysis-only and can never be executed.")
        if symbol != self._config.execution.active_symbol:
            reasons.append(
                f"{symbol} is not the configured active execution symbol "
                f"({self._config.execution.active_symbol})."
            )
        return spec

    @staticmethod
    def _missing_field_reasons(setup: TradeSetup) -> list[str]:
        out = []
        if setup.proposed_side is None:
            out.append("Setup has no proposed side.")
        if setup.proposed_entry is None:
            out.append("Setup has no proposed entry.")
        if setup.proposed_stop is None:
            out.append("Setup has no proposed stop.")
        return out

    def _prepare_prices(
        self, setup: TradeSetup, spec: InstrumentSpec, market_price: Optional[float]
    ) -> tuple[Optional[_PreparedPrices], list[str]]:
        problems = self._missing_field_reasons(setup)
        side, entry, stop = setup.proposed_side, setup.proposed_entry, setup.proposed_stop
        if problems or side is None or entry is None or stop is None:
            return None, problems

        if not (math.isfinite(entry) and math.isfinite(stop)) or entry <= 0 or stop <= 0:
            return None, ["Entry and stop must be finite, positive prices."]

        notes: list[str] = []

        if not spec.is_on_tick(entry):
            if market_price is None or not math.isfinite(market_price) or market_price <= 0:
                return None, [f"Entry {entry} is off-tick and no valid market price was given to round it toward."]
            rounded = spec.round_entry(entry, market_price)
            notes.append(f"entry {entry} -> {rounded} (toward market {market_price})")
            entry = rounded

        rounded_stop = spec.round_stop(stop, side)
        if rounded_stop != stop:
            notes.append(f"stop {stop} -> {rounded_stop} (away from entry)")
        stop = rounded_stop

        if side == TradeSide.LONG and not stop < entry:
            problems.append("Long setup requires stop below entry.")
        if side == TradeSide.SHORT and not stop > entry:
            problems.append("Short setup requires stop above entry.")

        targets: list[Target] = []
        for t in setup.targets:
            if not t.active:
                continue
            if not math.isfinite(t.price) or t.price <= 0:
                problems.append(f"Target {t.type} has an invalid price.")
                continue
            rounded_t = spec.round_target(t.price, side)
            if rounded_t != t.price:
                notes.append(f"target {t.type} {t.price} -> {rounded_t} (toward entry)")
            on_profit_side = rounded_t > entry if side == TradeSide.LONG else rounded_t < entry
            if not on_profit_side:
                problems.append(f"Active target {t.type}@{t.price} is not on the profit side of entry after rounding.")
                continue
            targets.append(t.model_copy(update={"price": rounded_t}))

        if problems:
            return None, problems
        return _PreparedPrices(entry=entry, stop=stop, targets=tuple(targets), adjustments=tuple(notes)), []
