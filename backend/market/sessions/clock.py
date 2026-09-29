"""
Market clock helpers. Everything is New York wall-clock time (owner decision 6).

Pure functions over tz-aware datetimes and SessionConfig strings; no wall-clock reads.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from backend.config.schema import REQUIRED_TIMEZONE, SessionConfig

MARKET_TZ = ZoneInfo(REQUIRED_TIMEZONE)


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def to_market_time(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("Expected a timezone-aware datetime.")
    return dt.astimezone(MARKET_TZ)


def in_maintenance_halt(dt: datetime, session: SessionConfig) -> bool:
    """True inside the daily CME maintenance halt [halt_start, halt_end) ET (17:00-18:00 by default)."""
    t = to_market_time(dt).time()
    return parse_hhmm(session.maintenance_halt_start) <= t < parse_hhmm(session.maintenance_halt_end)


def is_within_entry_window(as_of: datetime, session: SessionConfig) -> bool:
    """entry_start <= local time < entry_end. A strategy CONDITION (owner decision 7), not a risk rule."""
    t = to_market_time(as_of).time()
    return parse_hhmm(session.entry_start) <= t < parse_hhmm(session.entry_end)
