from datetime import datetime, timedelta, timezone

import pytest

from backend.core.enums import Timeframe
from backend.data.normalizer import BarFieldMap, BarNormalizer, NormalizationError
from tests.helpers import NY

RAW = {"timestamp": "2026-01-05T14:30:00Z", "open": "100", "high": "102", "low": "99", "close": "101", "volume": "12"}


def test_iso_utc_timestamp_becomes_new_york_time():
    c = BarNormalizer("MNQ").normalize(RAW)
    assert c.open_time == datetime(2026, 1, 5, 9, 30, tzinfo=NY)
    assert c.open_time.utcoffset() == timedelta(hours=-5)
    assert c.close_time - c.open_time == timedelta(minutes=1)
    assert (c.open, c.high, c.low, c.close, c.volume) == (100.0, 102.0, 99.0, 101.0, 12.0)
    assert c.is_closed and c.timeframe == Timeframe.M1 and c.symbol == "MNQ"


def test_naive_timestamps_use_the_declared_source_timezone():
    raw = dict(RAW, timestamp="2026-01-05 08:30:00")  # 08:30 Chicago == 09:30 New York
    c = BarNormalizer("MNQ", source_tz="America/Chicago").normalize(raw)
    assert c.open_time == datetime(2026, 1, 5, 9, 30, tzinfo=NY)


def test_epoch_seconds_and_milliseconds():
    sec = 1767623400  # 2026-01-05 14:30:00 UTC
    for value in (sec, sec * 1000, str(sec)):
        c = BarNormalizer("MNQ").normalize(dict(RAW, timestamp=value))
        assert c.open_time == datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


def test_close_stamped_vendor_bars_are_shifted_back_one_period():
    c = BarNormalizer("MNQ", timestamp_marks="close").normalize(dict(RAW, timestamp="2026-01-05T14:31:00Z"))
    assert c.open_time == datetime(2026, 1, 5, 9, 30, tzinfo=NY)
    assert c.close_time == datetime(2026, 1, 5, 9, 31, tzinfo=NY)


def test_closed_flag_from_payload_and_default():
    fm = BarFieldMap(is_closed="closed")
    n = BarNormalizer("MNQ", field_map=fm)
    assert n.normalize(dict(RAW, closed="false")).is_closed is False
    assert n.normalize(dict(RAW, closed=True)).is_closed is True
    with pytest.raises(NormalizationError):
        n.normalize(dict(RAW, closed="maybe"))
    assert BarNormalizer("MNQ", assume_closed=False).normalize(RAW).is_closed is False


def test_custom_field_names_and_no_volume():
    fm = BarFieldMap(timestamp="t", open="o", high="h", low="l", close="c", volume=None)
    c = BarNormalizer("MNQ", field_map=fm).normalize({"t": "2026-01-05T14:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2})
    assert c.volume == 0.0


@pytest.mark.parametrize(
    "patch",
    [
        {"high": "98"},            # high below low/open/close
        {"low": "105"},            # low above open/close
        {"volume": "-1"},          # negative volume
        {"open": "nan"},           # non-finite
        {"close": "abc"},          # not numeric
        {"timestamp": "yesterday"},  # unparseable
        {"volume": ""},            # blank required field
    ],
)
def test_malformed_payloads_fail_loudly(patch):
    with pytest.raises(NormalizationError):
        BarNormalizer("MNQ").normalize(dict(RAW, **patch))


def test_missing_field_fails_loudly():
    raw = {k: v for k, v in RAW.items() if k != "close"}
    with pytest.raises(NormalizationError, match="close"):
        BarNormalizer("MNQ").normalize(raw)
