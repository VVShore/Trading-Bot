"""
InstrumentSpec: the contract characteristics risk and execution need.

Deliberately small. Strategies never receive or need this; it is resolved from
AppConfig (see backend/config/instruments.py) by the RiskEngine and PaperBroker.
"""

from __future__ import annotations

import math
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.core.enums import TradeSide


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

    # ---- Deterministic tick rounding (owner decision 2) ------------------------------
    # Exact decimal arithmetic so midpoints such as 18250.125 never depend on float noise.
    # An already-on-tick price is returned unchanged by every method.

    def _snap(self, price: float, rounding: str) -> float:
        tick = Decimal(str(self.tick_size))
        ticks = (Decimal(str(price)) / tick).to_integral_value(rounding=rounding)
        return float(ticks * tick)

    def round_up(self, price: float) -> float:
        return self._snap(price, ROUND_CEILING)

    def round_down(self, price: float) -> float:
        return self._snap(price, ROUND_FLOOR)

    def round_entry(self, price: float, market_price: float) -> float:
        """Entries round TOWARD the current market price (to a neighbouring tick)."""
        return self.round_up(price) if market_price > price else self.round_down(price)

    def round_stop(self, price: float, side: TradeSide) -> float:
        """Stops round AWAY from entry: long stops sit below entry (down), short stops above (up)."""
        return self.round_down(price) if side == TradeSide.LONG else self.round_up(price)

    def round_target(self, price: float, side: TradeSide) -> float:
        """Targets round TOWARD entry (guarantees limit fills): long targets sit above entry (down)."""
        return self.round_down(price) if side == TradeSide.LONG else self.round_up(price)
