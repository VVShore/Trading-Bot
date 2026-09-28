"""HTF bias result domain model."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.core.enums import BiasDirection, ConfidenceLevel


class HTFBiasResult(BaseModel):
    bias: BiasDirection
    confidence: ConfidenceLevel
    supporting_factors: list[str] = Field(default_factory=list)
    contradicting_factors: list[str] = Field(default_factory=list)
    reversal_risk: list[str] = Field(default_factory=list)
    source_candles: list[str] = Field(default_factory=list, description="IDs/refs of completed 1H candles used.")
    evaluated_at: Optional[datetime] = None
