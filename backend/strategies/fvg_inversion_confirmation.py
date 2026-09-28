"""
FVG_INVERSION_CONFIRMATION (secondary entry model)

Disabled by default (config.entry.fvg_confirmation_enabled = false).
Implemented as an independent module rather than mixed into
NyAmHtfContinuationV1, per the spec. Skeleton only -- see
backend/strategies/ny_continuation_v1.py for the same caveat about
detector modules not being implemented yet.
"""

from __future__ import annotations

from uuid import uuid4

from backend.core.enums import SetupConditionStatus, StrategyName
from backend.core.models.setup import SetupCondition, TradeSetup
from backend.strategies.base import MarketContext, Strategy

REQUIRED_CONDITION_NAMES = [
    "valid_fvg_inversion_close",
    "risk_engine_approval",
    "no_execution_lockout",
    "within_execution_window",
]


class FvgInversionConfirmation(Strategy):
    name = StrategyName.FVG_INVERSION_CONFIRMATION.value
    version = "0.1.0"

    def evaluate(self, context: MarketContext) -> TradeSetup:
        if not self.config.entry.fvg_confirmation_enabled:
            return TradeSetup(
                setup_id=str(uuid4()),
                strategy=StrategyName.FVG_INVERSION_CONFIRMATION,
                strategy_version=self.version,
                symbol=context.symbol,
                evaluated_at=context.as_of,
                decision="NO_TRADE",
                reason="Disabled via config.entry.fvg_confirmation_enabled.",
                parameter_snapshot=self.config.parameter_snapshot(),
            )

        required = [
            SetupCondition(name=n, status=SetupConditionStatus.NOT_EVALUATED) for n in REQUIRED_CONDITION_NAMES
        ]
        return TradeSetup(
            setup_id=str(uuid4()),
            strategy=StrategyName.FVG_INVERSION_CONFIRMATION,
            strategy_version=self.version,
            symbol=context.symbol,
            evaluated_at=context.as_of,
            required_conditions=required,
            decision="NO_TRADE",
            reason="Detector modules not yet implemented.",
            parameter_snapshot=self.config.parameter_snapshot(),
        )
