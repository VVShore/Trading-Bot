"""
RiskEngine: the independent gate between a strategy's TradeSetup and an order.

    TradeSetup -> RiskEngine.evaluate() -> RiskDecision(rejected | OrderIntent)

The RiskEngine is the ONLY issuer of OrderIntent (see core/models/order_intent.py),
and brokers only accept authentic OrderIntents, so strategy code has no path to
a broker that skips this gate. It does not ask whether the strategy "likes" the
setup: a TradeSetup with decision == "TRADE" is a proposal, nothing more.

It reuses existing risk mechanisms rather than duplicating them:
  - DailyRiskState.can_trade()            (daily lockouts)
  - resolve_risk_dollars()                (dollar vs percent risk)
  - calculate_position_size()             (sizing formula)

Fails closed: every unmet or unverifiable condition is a rejection, and all
independent rejection reasons found are reported together.

Deliberately NOT done here (unspecified trading rules -- see docs/HANDOFF_STATE.md):
  - no entry-window / news-lockout enforcement (strategy conditions)
  - no cap on concurrent positions / stacking
  - no shrinking of size to the remaining daily-loss budget
  - no rounding of off-tick prices (rejected instead)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo

from backend.config.instruments import instrument_spec_from_config
from backend.config.schema import AppConfig
from backend.core.enums import OrderType, TradeSide
from backend.core.models.instrument import InstrumentSpec
from backend.core.models.order_intent import OrderIntent, issue_order_intent
from backend.core.models.setup import TradeSetup
from backend.risk.daily_limits import DailyRiskState
from backend.risk.position_sizing import PositionSizeResult, calculate_position_size, resolve_risk_dollars


@dataclass(frozen=True)
class RiskDecision:
    setup_id: str
    approved: bool
    intent: Optional[OrderIntent] = None
    rejection_reasons: tuple[str, ...] = ()
    sizing: Optional[PositionSizeResult] = None


class RiskEngine:
    def __init__(self, config: AppConfig, daily_state: DailyRiskState) -> None:
        self._config = config
        self._daily = daily_state
        self._tz = ZoneInfo(config.session.timezone)

    def evaluate(self, setup: TradeSetup) -> RiskDecision:
        reasons: list[str] = []

        if setup.decision != "TRADE":
            reasons.append(f"Setup decision is '{setup.decision}', not 'TRADE'.")

        if not self._daily.can_trade():
            reasons.append(f"Daily risk lockout active: {self._daily.lockout_reason}")

        if setup.evaluated_at.tzinfo is None:
            reasons.append("Setup evaluated_at must be timezone-aware.")
        elif setup.evaluated_at.astimezone(self._tz).date() != self._daily.trading_day:
            reasons.append(
                f"Daily risk state is for {self._daily.trading_day}, but setup was evaluated "
                f"on {setup.evaluated_at.astimezone(self._tz).date()} ({self._config.session.timezone})."
            )

        spec = self._resolve_instrument(setup.symbol, reasons)
        reasons.extend(self._validate_prices(setup, spec))

        if reasons:
            return self._reject(setup, reasons)

        assert spec is not None and setup.proposed_side is not None
        assert setup.proposed_entry is not None and setup.proposed_stop is not None

        max_risk = resolve_risk_dollars(
            self._config.risk.risk_mode,
            self._config.risk.risk_dollars,
            self._config.risk.risk_percent,
            self._config.risk.account_size,
        )
        sizing = calculate_position_size(
            entry_price=setup.proposed_entry,
            stop_price=setup.proposed_stop,
            point_value=spec.point_value,
            max_risk_dollars=max_risk,
            commission_per_contract=spec.commission_per_contract,
            estimated_slippage_points=spec.estimated_slippage_points,
        )
        if not sizing.accepted:
            return self._reject(setup, [f"Position sizing rejected: {sizing.rejection_reason}"], sizing)

        quantity = spec.round_down_quantity(sizing.contracts)
        if quantity == 0:
            return self._reject(
                setup, [f"Sized quantity {sizing.contracts} is below the instrument's minimum quantity."], sizing
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
            entry_price=setup.proposed_entry,
            stop_price=setup.proposed_stop,
            targets=tuple(t for t in setup.targets if t.active),
            risk_per_contract_dollars=sizing.risk_per_contract_dollars,
            approved_risk_dollars=quantity * sizing.risk_per_contract_dollars,
            created_at=setup.evaluated_at,
        )
        return RiskDecision(setup_id=setup.setup_id, approved=True, intent=intent, sizing=sizing)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _reject(setup: TradeSetup, reasons: list[str], sizing: Optional[PositionSizeResult] = None) -> RiskDecision:
        return RiskDecision(setup_id=setup.setup_id, approved=False, rejection_reasons=tuple(reasons), sizing=sizing)

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
    def _validate_prices(setup: TradeSetup, spec: Optional[InstrumentSpec]) -> list[str]:
        problems: list[str] = []
        side, entry, stop = setup.proposed_side, setup.proposed_entry, setup.proposed_stop

        if side is None:
            problems.append("Setup has no proposed side.")
        if entry is None:
            problems.append("Setup has no proposed entry.")
        if stop is None:
            problems.append("Setup has no proposed stop.")
        if side is None or entry is None or stop is None:
            return problems

        if not (math.isfinite(entry) and math.isfinite(stop)) or entry <= 0 or stop <= 0:
            problems.append("Entry and stop must be finite, positive prices.")
            return problems

        if side == TradeSide.LONG and not stop < entry:
            problems.append("Long setup requires stop below entry.")
        if side == TradeSide.SHORT and not stop > entry:
            problems.append("Short setup requires stop above entry.")

        if spec is not None:
            for label, price in (("entry", entry), ("stop", stop)):
                if not spec.is_on_tick(price):
                    problems.append(f"Proposed {label} {price} is not on the {spec.tick_size} tick grid.")
            for t in setup.targets:
                if not t.active:
                    continue
                on_profit_side = t.price > entry if side == TradeSide.LONG else t.price < entry
                if not on_profit_side:
                    problems.append(f"Active target {t.type}@{t.price} is not on the profit side of entry.")
                elif not spec.is_on_tick(t.price):
                    problems.append(f"Target {t.type}@{t.price} is not on the {spec.tick_size} tick grid.")
        return problems
