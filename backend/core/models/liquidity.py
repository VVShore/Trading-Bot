"""
Liquidity object domain model.

Per docs/ASSUMPTIONS.md, a liquidity object is never permanently bullish
or bearish -- its role (target / support / resistance / rejection) is
determined contextually by whoever queries it, not baked in at creation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.core.enums import LiquidityRole, LiquidityStatus, LiquidityType, Timeframe


class LiquidityObject(BaseModel):
    id: str
    type: LiquidityType
    timeframe: Timeframe
    price: float

    upper_bound: float
    lower_bound: float

    creation_time: datetime
    detection_time: datetime

    source_candle: Optional[str] = Field(
        default=None, description="Reference id of the candle that produced this level."
    )

    strength: float = Field(default=1.0, ge=0.0, description="Importance/strength score, see config.liquidity_importance.")

    status: LiquidityStatus = LiquidityStatus.ACTIVE
    swept: bool = False
    invalidated: bool = False

    # A liquidity object may be eligible for more than one role simultaneously;
    # actual role selection happens in strategy/target logic, not here.
    target_eligible: bool = True
    rejection_eligible: bool = True

    metadata: dict[str, Any] = Field(default_factory=dict)

    def possible_roles(self) -> list[LiquidityRole]:
        roles = []
        if self.target_eligible:
            roles.append(LiquidityRole.TARGET)
        if self.rejection_eligible:
            roles.append(LiquidityRole.REJECTION_AREA)
        roles.append(LiquidityRole.SUPPORT)
        roles.append(LiquidityRole.RESISTANCE)
        return roles
