"""
Tick -> 1M candle builder.

A minute's candle is emitted (closed) only when a tick from a LATER minute arrives. That is
deliberately conservative: without a trusted clock we cannot know the minute has really ended,
so we never close a bar on a guess. (A live wrapper may add clock-driven closing later.)
Minutes with no ticks produce no candle -- the aggregator handles such gaps.
Out-of-order ticks and invalid prices raise instead of being repaired.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from backend.core.enums import Timeframe
from backend.core.models.candle import Candle
from backend.market.sessions.clock import MARKET_TZ


@dataclass(frozen=True)
class Tick:
    timestamp: datetime  # tz-aware
    price: float
    size: float = 0.0


class TickError(ValueError):
    pass


class TickCandleBuilder:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._minute: Optional[datetime] = None
        self._o = self._h = self._l = self._c = 0.0
        self._v = 0.0
        self._last_ts: Optional[datetime] = None

    def ingest(self, tick: Tick) -> list[Candle]:
        if tick.timestamp.tzinfo is None:
            raise TickError("Tick timestamp must be timezone-aware.")
        if not (math.isfinite(tick.price) and tick.price > 0) or not math.isfinite(tick.size) or tick.size < 0:
            raise TickError(f"Invalid tick values: {tick!r}")
        if self._last_ts is not None and tick.timestamp < self._last_ts:
            raise TickError(f"Out-of-order tick {tick.timestamp.isoformat()} < {self._last_ts.isoformat()}")
        self._last_ts = tick.timestamp

        minute = tick.timestamp.astimezone(MARKET_TZ).replace(second=0, microsecond=0)
        closed: list[Candle] = []
        if self._minute is not None and minute > self._minute:
            closed.append(self._emit(closed=True))
            self._minute = None
        if self._minute is None:
            self._minute = minute
            self._o = self._h = self._l = self._c = tick.price
            self._v = tick.size
        else:
            self._h, self._l, self._c = max(self._h, tick.price), min(self._l, tick.price), tick.price
            self._v += tick.size
        return closed

    def developing_candle(self) -> Optional[Candle]:
        """The forming minute (is_closed=False). Never feed this to an aggregator."""
        return self._emit(closed=False) if self._minute is not None else None

    def _emit(self, closed: bool) -> Candle:
        assert self._minute is not None
        return Candle(
            symbol=self.symbol,
            timeframe=Timeframe.M1,
            open_time=self._minute,
            close_time=self._minute + timedelta(minutes=1),
            open=self._o,
            high=self._h,
            low=self._l,
            close=self._c,
            volume=self._v,
            is_closed=closed,
        )
