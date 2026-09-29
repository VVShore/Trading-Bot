"""
NY_AM_HTF_CONTINUATION_V1

This is a SKELETON for Step 7 of the development process. Detector modules
(HTF bias, liquidity, manipulation candle, FVG/OB/CE/OTE, SMT) are Steps
3-6 and are not implemented yet, so this class defines the evaluation
sequence and the structured TradeSetup output shape, with each condition
marked NOT_EVALUATED until its backing detector exists.

Do not fill in detector logic here -- add it to backend/concepts/* and
backend/market/* and have this class call those modules. See
CRITICAL_DEVELOPMENT_RULES #4 in the project spec: detection must stay
separate from strategy logic.
"""

from __future__ import annotations

from uuid import uuid4

from backend.core.enums import SetupConditionStatus, StrategyName, TradeSide
from backend.core.models.setup import SetupCondition, TradeSetup
from backend.market.sessions.clock import is_within_entry_window
from backend.strategies.base import MarketContext, Strategy

REQUIRED_CONDITION_NAMES = [
    "valid_htf_bias",
    "valid_liquidity_context",
    "valid_manipulation_candle",
    "valid_retracement_to_ce",
    "risk_engine_approval",
    "no_execution_lockout",
    "within_execution_window",
]

OPTIONAL_CONFLUENCE_NAMES = [
    "htf_fvg",
    "htf_ob",
    "ifvg",
    "ote",
    "smt",
    "breaker",
    "bpr",
    "volume_imbalance",
    "displacement",
    "structure_confirmation",
]


class NyAmHtfContinuationV1(Strategy):
    name = StrategyName.NY_AM_HTF_CONTINUATION_V1.value
    version = "0.1.0"

    def evaluate(self, context: MarketContext) -> TradeSetup:
        required = [SetupCondition(name=n, status=SetupConditionStatus.NOT_EVALUATED) for n in REQUIRED_CONDITION_NAMES]
        optional = [
            SetupCondition(name=n, status=SetupConditionStatus.NOT_EVALUATED, required=False)
            for n in OPTIONAL_CONFLUENCE_NAMES
        ]

        in_window = is_within_entry_window(context.as_of, self.config.session)
        for cond in required:
            if cond.name == "within_execution_window":
                cond.status = SetupConditionStatus.PASS if in_window else SetupConditionStatus.FAIL
                cond.detail = (
                    f"{self.config.session.entry_start}-{self.config.session.entry_end} "
                    f"{self.config.session.timezone}"
                )

        reason = "Detector modules not yet implemented (Steps 3-6 of development process)."
        if not in_window:
            reason = "Outside the configured entry window."

        setup = TradeSetup(
            setup_id=str(uuid4()),
            strategy=StrategyName.NY_AM_HTF_CONTINUATION_V1,
            strategy_version=self.version,
            symbol=context.symbol,
            evaluated_at=context.as_of,
            proposed_side=None,
            required_conditions=required,
            optional_confluences=optional,
            decision="NO_TRADE",
            reason=reason,
            parameter_snapshot=self.config.parameter_snapshot(),
        )
        return setup
