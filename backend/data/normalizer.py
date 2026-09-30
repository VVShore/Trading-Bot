"""
BarNormalizer: raw vendor bar payload -> canonical Candle.

* Timestamps become tz-aware America/New_York (owner decision 6). ISO strings, datetimes and
  epoch seconds/milliseconds are accepted. Naive timestamps are interpreted in `source_tz`
  (default UTC) -- never guessed silently from the machine's local zone.
* `timestamp_marks` says whether the vendor stamps a bar by its OPEN or its CLOSE time; getting
  this wrong shifts every bar by one period and silently leaks the future, so it is explicit.
* Closed state: `is_closed` is read from the payload when `field_map.is_closed` is set;
  otherwise `assume_closed` decides (True for historical files, and a live provider MUST supply
  the flag).
* Fails loudly (NormalizationError) on anything malformed rather than repairing it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping, Optional
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from backend.core.enums import Timeframe
from backend.core.models.candle import Candle
from backend.market.sessions.clock import MARKET_TZ

_TF_DELTA = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
}
_TRUE = {"true", "1", "yes", "y", "t"}
_FALSE = {"false", "0", "no", "n", "f"}


class NormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class BarFieldMap:
    timestamp: str = "timestamp"
    open: str = "open"
    high: str = "high"
    low: str = "low"
    close: str = "close"
    volume: Optional[str] = "volume"  # None -> volume is not provided by the vendor (0.0)
    is_closed: Optional[str] = None


class BarNormalizer:
    def __init__(
        self,
        symbol: str,
        timeframe: Timeframe = Timeframe.M1,
        field_map: BarFieldMap = BarFieldMap(),
        source_tz: str = "UTC",
        timestamp_marks: Literal["open", "close"] = "open",
        assume_closed: bool = True,
    ) -> None:
        if timeframe not in _TF_DELTA:
            raise ValueError(f"Unsupported timeframe for normalization: {timeframe}")
        self.symbol = symbol
        self.timeframe = timeframe
        self._fm = field_map
        self._source_tz = ZoneInfo(source_tz)
        self._marks = timestamp_marks
        self._assume_closed = assume_closed

    def normalize(self, raw: Mapping[str, Any]) -> Candle:
        try:
            ts = self._parse_time(self._get(raw, self._fm.timestamp))
            delta = _TF_DELTA[self.timeframe]
            open_time, close_time = (ts, ts + delta) if self._marks == "open" else (ts - delta, ts)
            volume = 0.0 if self._fm.volume is None else self._number(self._get(raw, self._fm.volume), "volume")
            return Candle(
                symbol=self.symbol,
                timeframe=self.timeframe,
                open_time=open_time,
                close_time=close_time,
                open=self._number(self._get(raw, self._fm.open), "open"),
                high=self._number(self._get(raw, self._fm.high), "high"),
                low=self._number(self._get(raw, self._fm.low), "low"),
                close=self._number(self._get(raw, self._fm.close), "close"),
                volume=volume,
                is_closed=self._closed_flag(raw),
            )
        except NormalizationError:
            raise
        except (ValidationError, ValueError, TypeError) as exc:
            raise NormalizationError(f"Invalid bar payload {dict(raw)!r}: {exc}") from exc

    # ------------------------------------------------------------------ parsing

    @staticmethod
    def _get(raw: Mapping[str, Any], key: str) -> Any:
        if key not in raw or raw[key] is None or (isinstance(raw[key], str) and raw[key].strip() == ""):
            raise NormalizationError(f"Missing field '{key}' in payload {dict(raw)!r}")
        return raw[key]

    @staticmethod
    def _number(value: Any, name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise NormalizationError(f"Field '{name}' is not numeric: {value!r}") from exc
        if not math.isfinite(number):
            raise NormalizationError(f"Field '{name}' is not finite: {value!r}")
        return number

    def _parse_time(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            seconds = value / 1000.0 if abs(value) > 1e11 else float(value)  # ms vs s
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        elif isinstance(value, str):
            text = value.strip()
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                try:
                    return self._parse_time(float(text))
                except ValueError as exc:
                    raise NormalizationError(f"Unparseable timestamp: {value!r}") from exc
        else:
            raise NormalizationError(f"Unsupported timestamp type: {type(value).__name__}")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self._source_tz)
        return dt.astimezone(MARKET_TZ)

    def _closed_flag(self, raw: Mapping[str, Any]) -> bool:
        key = self._fm.is_closed
        if key is None:
            return self._assume_closed
        value = self._get(raw, key)
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise NormalizationError(f"Field '{key}' is not a boolean: {value!r}")
