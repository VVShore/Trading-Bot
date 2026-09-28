"""
Candle domain model.

A Candle is the atomic unit of price data. Higher timeframe candles are
built by aggregating lower timeframe candles (see backend/market/candles).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from backend.core.enums import Timeframe


class Candle(BaseModel):
    symbol: str
    timeframe: Timeframe
    open_time: datetime = Field(..., description="Candle open timestamp, tz-aware (UTC internally).")
    close_time: datetime = Field(..., description="Candle close timestamp, tz-aware (UTC internally).")
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    # Whether this candle has fully closed. Forming candles may be used for
    # developing context but must never contribute to "confirmed" signals.
    is_closed: bool = True

    @model_validator(mode="after")
    def _validate_ohlc(self) -> "Candle":
        if self.high < max(self.open, self.close):
            raise ValueError("Candle high must be >= max(open, close)")
        if self.low > min(self.open, self.close):
            raise ValueError("Candle low must be <= min(open, close)")
        if self.close_time <= self.open_time:
            raise ValueError("close_time must be after open_time")
        return self

    # --- Derived properties -------------------------------------------------

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def body_percent(self) -> float:
        """Body size as a percentage of total range. Returns 0 for a zero-range candle."""
        if self.range == 0:
            return 0.0
        return (self.body / self.range) * 100.0

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def midpoint(self) -> float:
        """Used as CE (consequent encroachment) reference for candle-based CE."""
        return (self.high + self.low) / 2.0
