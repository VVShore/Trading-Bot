"""
InstrumentSpec: the contract characteristics risk and execution need.

Deliberately small. Strategies never receive or need this; it is resolved from
AppConfig (see backend/config/instruments.py) by the RiskEngine and PaperBroker.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field


class InstrumentSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    tick_size: float = Field(gt=0)
    point_value: float = Field(gt=0)  # dollars per 1.0 index point, per contract
    min_quantity: int = Field(default=1, ge=1)
    quantity_increment: int = Field(default=1, ge=1)
    commission_per_contract: float = Field(default=0.0, ge=0)
    estimated_slippage_points: float = Field(default=0.0, ge=0)
    analysis_only: bool = False  # e.g. ES: used for SMT reference, never executed

    @property
    def tick_value(self) -> float:
        return self.tick_size * self.point_value

    def is_on_tick(self, price: float) -> bool:
        if not math.isfinite(price):
            return False
        ticks = price / self.tick_size
        return abs(ticks - round(ticks)) < 1e-6

    def round_down_quantity(self, quantity: int) -> int:
        """Largest valid quantity <= `quantity` (0 if below min_quantity)."""
        q = (quantity // self.quantity_increment) * self.quantity_increment
        return q if q >= self.min_quantity else 0
