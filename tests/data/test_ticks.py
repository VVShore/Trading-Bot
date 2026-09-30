from datetime import datetime, timedelta

import pytest

from backend.core.enums import Timeframe
from backend.data.ticks import Tick, TickCandleBuilder, TickError
from tests.helpers import NY


def tk(minute, second, price, size=1.0, hour=9):
    return Tick(datetime(2026, 1, 5, hour, minute, second, tzinfo=NY), price, size)


def test_minute_candle_closes_only_when_a_later_minute_tick_arrives():
    b = TickCandleBuilder("MNQ")
    assert b.ingest(tk(30, 1, 100.0, 2)) == []
    assert b.ingest(tk(30, 20, 103.0, 1)) == []
    assert b.ingest(tk(30, 59, 99.0, 3)) == []      # still inside 09:30: nothing closed yet
    dev = b.developing_candle()
    assert dev.is_closed is False and dev.close == 99.0
    out = b.ingest(tk(31, 0, 101.0))
    assert len(out) == 1
    c = out[0]
    assert c.is_closed and c.timeframe == Timeframe.M1
    assert (c.open, c.high, c.low, c.close, c.volume) == (100.0, 103.0, 99.0, 99.0, 6.0)
    assert c.open_time == datetime(2026, 1, 5, 9, 30, tzinfo=NY)
    assert c.close_time == c.open_time + timedelta(minutes=1)


def test_minutes_without_ticks_produce_no_candle():
    b = TickCandleBuilder("MNQ")
    b.ingest(tk(30, 5, 100.0))
    out = b.ingest(tk(34, 5, 101.0))
    assert [c.open_time.minute for c in out] == [30]  # 31-33 never existed


def test_out_of_order_and_invalid_ticks_raise():
    b = TickCandleBuilder("MNQ")
    b.ingest(tk(30, 30, 100.0))
    with pytest.raises(TickError):
        b.ingest(tk(30, 10, 100.0))
    for bad in (Tick(datetime(2026, 1, 5, 9, 31), 100.0), tk(31, 0, -1.0), tk(31, 0, float("nan")), tk(31, 0, 100.0, -1)):
        with pytest.raises(TickError):
            b.ingest(bad)
