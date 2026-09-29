"""
TimeframeAggregator: builds 5M / 15M / 1H / 4H bars from CLOSED 1-minute candles.

No-lookahead design
-------------------
* Only closed 1M candles are accepted (a non-closed candle raises). Candles must arrive in
  strictly increasing open_time order.
* A higher-timeframe bar is published to `closed_bars` only once it is complete:
    - the last 1M candle of its bucket has been ingested, or
    - a candle from a LATER bucket arrives (a gap: missing minutes / halt / weekend).
  It is never published early, and never revised afterwards.
* The bar still being built is kept apart as the developing bar (`developing_bar`, always
  `is_closed=False`). Nothing that builds MarketContext reads it.
* Feeding a stream in prefixes yields prefixes of the same closed-bar sequence (tested), so a
  bar can never depend on data that arrives after it closes.

Session boundaries (America/New_York wall clock, owner decision 6)
------------------------------------------------------------------
* 5M/15M/1H buckets align to the clock hour; 4H buckets are anchored at
  `session.four_hour_anchor` (18:00 ET by default -> 18,22,02,06,10,14).
* The daily maintenance halt (17:00-18:00 ET) is a hard boundary: no bar spans it. A bucket that
  contains 17:00 ends there (e.g. the 14:00 4H bar is 3 hours long and closes when the 16:59
  candle arrives). A candle stamped inside the halt is rejected.
* Bucket arithmetic is done on New York wall-clock time, so DST changes cannot shift alignment.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Deque, Optional, Sequence

from backend.config.schema import SessionConfig
from backend.core.enums import Timeframe
from backend.core.models.candle import Candle
from backend.market.sessions.clock import MARKET_TZ, in_maintenance_halt, parse_hhmm, to_market_time

_MINUTES = {Timeframe.M5: 5, Timeframe.M15: 15, Timeframe.H1: 60, Timeframe.H4: 240}
DEFAULT_TIMEFRAMES: tuple[Timeframe, ...] = (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4)


class AggregationError(ValueError):
    pass


class NotClosedCandleError(AggregationError):
    pass


class OutOfOrderCandleError(AggregationError):
    pass


@dataclass
class _Building:
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    last_open_time: datetime

    def merge(self, c: Candle) -> None:
        self.high = max(self.high, c.high)
        self.low = min(self.low, c.low)
        self.close = c.close
        self.volume += c.volume
        self.last_open_time = c.open_time

    def to_candle(self, symbol: str, tf: Timeframe, closed: bool) -> Candle:
        return Candle(
            symbol=symbol,
            timeframe=tf,
            open_time=self.start,
            close_time=self.end,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            is_closed=closed,
        )


class TimeframeAggregator:
    def __init__(
        self,
        symbol: str,
        session: SessionConfig,
        timeframes: Sequence[Timeframe] = DEFAULT_TIMEFRAMES,
        max_bars: Optional[int] = None,
    ) -> None:
        for tf in timeframes:
            if tf not in _MINUTES:
                raise ValueError(f"Unsupported aggregation timeframe: {tf}")
        self.symbol = symbol
        self._session = session
        self._tfs = tuple(timeframes)
        self._halt_start = parse_hhmm(session.maintenance_halt_start)
        self._anchor_min = parse_hhmm(session.four_hour_anchor).hour * 60 + parse_hhmm(session.four_hour_anchor).minute
        self._building: dict[Timeframe, Optional[_Building]] = {tf: None for tf in self._tfs}
        self._closed: dict[Timeframe, Deque[Candle]] = {
            tf: deque(maxlen=max_bars) for tf in (Timeframe.M1, *self._tfs)
        }
        self._last_open: Optional[datetime] = None
        self._as_of: Optional[datetime] = None

    # ------------------------------------------------------------------ ingest

    def ingest(self, candle: Candle) -> list[Candle]:
        """Feed one CLOSED 1M candle. Returns the higher-timeframe bars that just closed."""
        self._validate(candle)
        self._closed[Timeframe.M1].append(candle)
        self._last_open = candle.open_time
        self._as_of = candle.close_time

        newly_closed: list[Candle] = []
        for tf in self._tfs:
            start, end = self._bucket(candle.open_time, tf)
            cur = self._building[tf]

            if cur is not None and cur.start != start:  # a later bucket began: previous one is done
                newly_closed.append(self._finish(tf, cur))
                cur = None

            if cur is None:
                cur = _Building(start, end, candle.open, candle.high, candle.low, candle.close, candle.volume, candle.open_time)
            else:
                cur.merge(candle)
            self._building[tf] = cur

            if candle.close_time >= cur.end:  # last minute of the bucket has arrived
                newly_closed.append(self._finish(tf, cur))
        return newly_closed

    def _finish(self, tf: Timeframe, cur: _Building) -> Candle:
        bar = cur.to_candle(self.symbol, tf, closed=True)
        self._closed[tf].append(bar)
        self._building[tf] = None
        return bar

    def _validate(self, c: Candle) -> None:
        if c.symbol != self.symbol:
            raise AggregationError(f"Candle symbol {c.symbol} != aggregator symbol {self.symbol}")
        if c.timeframe != Timeframe.M1:
            raise AggregationError(f"Aggregator ingests 1M candles only (got {c.timeframe.value})")
        if not c.is_closed:
            raise NotClosedCandleError("Only closed candles may be ingested; got a developing candle.")
        if c.close_time - c.open_time != timedelta(minutes=1):
            raise AggregationError("1M candle must span exactly one minute")
        if in_maintenance_halt(c.open_time, self._session):
            raise AggregationError(
                f"Candle {to_market_time(c.open_time).isoformat()} lies inside the daily maintenance halt."
            )
        if self._last_open is not None and c.open_time <= self._last_open:
            raise OutOfOrderCandleError(
                f"Candle open_time {c.open_time.isoformat()} is not after the previous {self._last_open.isoformat()}"
            )

    # ------------------------------------------------------------------ buckets

    def _bucket(self, open_time: datetime, tf: Timeframe) -> tuple[datetime, datetime]:
        local = to_market_time(open_time).replace(second=0, microsecond=0, tzinfo=None)  # wall clock
        minutes = _MINUTES[tf]
        if tf == Timeframe.H4:
            offset = (local.hour * 60 + local.minute - self._anchor_min) % (24 * 60)
            start = local - timedelta(minutes=offset % minutes)
        else:
            start = local - timedelta(minutes=local.minute % minutes) if tf != Timeframe.H1 else local.replace(minute=0)
        end = start + timedelta(minutes=minutes)

        # The maintenance halt is a hard boundary: a bucket containing it ends where the halt starts.
        for day_offset in (0, 1):
            halt = (start + timedelta(days=day_offset)).replace(
                hour=self._halt_start.hour, minute=self._halt_start.minute
            )
            if start < halt < end:
                end = halt
                break
        return start.replace(tzinfo=MARKET_TZ), end.replace(tzinfo=MARKET_TZ)

    # ------------------------------------------------------------------ queries

    @property
    def as_of(self) -> Optional[datetime]:
        """Close time of the latest ingested 1M candle: everything known is known by this instant."""
        return self._as_of

    def closed_bars(self, tf: Timeframe, count: Optional[int] = None) -> list[Candle]:
        bars = list(self._closed[tf])
        return bars if count is None else bars[-count:] if count > 0 else []

    def last_closed(self, tf: Timeframe) -> Optional[Candle]:
        return self._closed[tf][-1] if self._closed[tf] else None

    def developing_bar(self, tf: Timeframe) -> Optional[Candle]:
        """The bar still forming (is_closed=False). Diagnostics only: never feed it to strategy context."""
        cur = self._building.get(tf)
        return cur.to_candle(self.symbol, tf, closed=False) if cur is not None else None
