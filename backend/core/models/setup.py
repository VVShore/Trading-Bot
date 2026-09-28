"""
TradeSetup: the structured record of which entry conditions passed/failed
for a single strategy evaluation. This is what gets written to the
decision log regardless of whether a trade is actually taken.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.core.enums import (
    BiasDirection,
    InvalidationReason,
    SessionName,
    SetupConditionStatus,
    StrategyName,
    TradeSide,
)


class SetupCondition(BaseModel):
    name: str
    status: SetupConditionStatus = SetupConditionStatus.NOT_EVALUATED
    detail: Optional[str] = None
    required: bool = True


class TradeSetup(BaseModel):
    """A single point-in-time evaluation of the strategy against market state."""

    setup_id: str
    strategy: StrategyName
    strategy_version: str
    symbol: str
    session: Optional[SessionName] = None
    evaluated_at: datetime

    htf_bias: Optional[BiasDirection] = None
    proposed_side: Optional[TradeSide] = None

    required_conditions: list[SetupCondition] = Field(default_factory=list)
    optional_confluences: list[SetupCondition] = Field(default_factory=list)

    manipulation_ce: Optional[float] = None
    proposed_stop: Optional[float] = None

    invalidation_reasons: list[InvalidationReason] = Field(default_factory=list)

    decision: str = Field(default="NO_TRADE", description="'TRADE' or 'NO_TRADE'")
    reason: Optional[str] = None

    parameter_snapshot: dict[str, Any] = Field(default_factory=dict)

    @property
    def all_required_passed(self) -> bool:
        return all(c.status == SetupConditionStatus.PASS for c in self.required_conditions if c.required)
