"""Target objects (TP1..TPn), represented as data rather than hard-coded logic."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Target(BaseModel):
    type: str = Field(..., description="e.g. '50pct_ote_15m', '1h_liquidity', 'session_high_low', '4h_fvg_fill'")
    price: float
    source: str = Field(..., description="What produced this target, e.g. a liquidity object id or concept id.")
    priority: int = Field(default=1, description="Lower number = higher priority (TP1=1, TP2=2, ...).")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    active: bool = True
    reached: bool = False
