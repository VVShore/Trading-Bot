"""
Generic confluence/concept object.

FVG, iFVG, Order Blocks, Breakers, OTE, CE, Volume Imbalance, BPR, and SMT
detectors all return objects shaped like this (or a thin subclass of it).
Keeping a shared shape lets the strategy and dashboard treat "confluences"
generically instead of hand-coding a branch per concept type.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.core.enums import ConceptStatus, ConceptType, Timeframe


class ConceptObject(BaseModel):
    id: str
    type: ConceptType
    timeframe: Timeframe
    direction: Optional[str] = Field(default=None, description="'bullish' | 'bearish' | None for direction-agnostic concepts.")

    upper: Optional[float] = None
    lower: Optional[float] = None
    ce: Optional[float] = Field(default=None, description="Consequent encroachment (midpoint) where applicable.")

    creation_time: datetime
    age_candles: int = 0

    fill_percentage: float = Field(default=0.0, ge=0.0, le=100.0)
    status: ConceptStatus = ConceptStatus.ACTIVE
    inverted: bool = False

    source_candles: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def compute_ce(self) -> Optional[float]:
        """Default CE = midpoint of upper/lower, unless explicitly overridden."""
        if self.ce is not None:
            return self.ce
        if self.upper is not None and self.lower is not None:
            return (self.upper + self.lower) / 2.0
        return None
