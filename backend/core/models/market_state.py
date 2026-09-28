"""
Market state snapshot.

The state engine returns state PER TIMEFRAME -- there is no single global
market state. See docs/ASSUMPTIONS.md for the provisional detection rules
behind each MarketState value.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.core.enums import MarketState, Timeframe


class MarketStateSnapshot(BaseModel):
    as_of: datetime
    states_by_timeframe: dict[Timeframe, MarketState] = Field(default_factory=dict)

    def get(self, timeframe: Timeframe) -> MarketState:
        return self.states_by_timeframe.get(timeframe, MarketState.UNKNOWN)
