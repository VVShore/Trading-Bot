from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.core.enums import Timeframe
from backend.core.models.candle import Candle


def _candle(**overrides):
    base = dict(
        symbol="MNQ",
        timeframe=Timeframe.M1,
        open_time=datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 1, 5, 9, 1, tzinfo=timezone.utc),
        open=100.0,
        high=105.0,
        low=98.0,
        close=103.0,
        volume=10,
    )
    base.update(overrides)
    return Candle(**base)


def test_valid_candle_constructs():
    c = _candle()
    assert c.is_bullish is True
    assert c.is_bearish is False


def test_invalid_high_raises():
    with pytest.raises(ValidationError):
        _candle(high=99.0)  # high < close


def test_invalid_low_raises():
    with pytest.raises(ValidationError):
        _candle(low=101.0)  # low > open


def test_close_time_before_open_time_raises():
    with pytest.raises(ValidationError):
        _candle(close_time=datetime(2026, 1, 5, 8, 59, tzinfo=timezone.utc))


def test_body_and_range_and_wicks():
    c = _candle(open=100.0, high=110.0, low=95.0, close=105.0)
    assert c.range == 15.0
    assert c.body == 5.0
    assert c.upper_wick == 5.0
    assert c.lower_wick == 5.0
    assert c.midpoint == pytest.approx(102.5)


def test_body_percent_zero_range_candle():
    c = _candle(open=100.0, high=100.0, low=100.0, close=100.0)
    assert c.body_percent == 0.0
