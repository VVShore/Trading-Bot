import pytest

from backend.core.enums import TradeSide
from backend.management.bar_exit import BarExitOutcome, SAME_BAR_SL_TP, enforce_safety_event, evaluate_bar_exit
from tests.helpers import make_daily_state, m1


def test_only_stop_touched():
    r = evaluate_bar_exit(TradeSide.LONG, stop=99.0, target=110.0, candle=m1(2026, 1, 5, 9, 30, o=100, h=101, l=98.5, c=99.5))
    assert r.outcome == BarExitOutcome.STOP and r.safety_event is None


def test_only_target_touched():
    r = evaluate_bar_exit(TradeSide.LONG, stop=95.0, target=105.0, candle=m1(2026, 1, 5, 9, 30, o=100, h=105.5, l=99, c=105))
    assert r.outcome == BarExitOutcome.TARGET


def test_neither_touched():
    r = evaluate_bar_exit(TradeSide.SHORT, stop=105.0, target=95.0, candle=m1(2026, 1, 5, 9, 30, o=100, h=101, l=99, c=100))
    assert r.outcome == BarExitOutcome.NONE


def test_short_side_directions():
    r = evaluate_bar_exit(TradeSide.SHORT, stop=101.0, target=95.0, candle=m1(2026, 1, 5, 9, 30, o=100, h=101.5, l=99, c=100))
    assert r.outcome == BarExitOutcome.STOP


def test_same_bar_touch_assumes_stop_first_and_emits_safety_event():
    candle = m1(2026, 1, 5, 9, 31, o=100, h=106, l=94, c=100)
    r = evaluate_bar_exit(TradeSide.LONG, stop=95.0, target=105.0, candle=candle)
    assert r.outcome == BarExitOutcome.AMBIGUOUS_STOP_FIRST and r.exits_at_stop
    assert r.safety_event.kind == SAME_BAR_SL_TP
    assert r.safety_event.to_dict()["kind"] == SAME_BAR_SL_TP


def test_safety_event_halts_the_session():
    state = make_daily_state()
    r = evaluate_bar_exit(TradeSide.LONG, 95.0, 105.0, m1(2026, 1, 5, 9, 31, o=100, h=106, l=94, c=100))
    enforce_safety_event(r, state)
    assert not state.can_trade() and "SAME_BAR_SL_TP" in state.lockout_reason


def test_plain_stop_does_not_halt_the_session():
    state = make_daily_state()
    r = evaluate_bar_exit(TradeSide.LONG, 99.0, 110.0, m1(2026, 1, 5, 9, 30, o=100, h=101, l=98, c=99))
    enforce_safety_event(r, state)
    assert state.can_trade()


def test_policy_only_defined_for_closed_one_minute_candles():
    with pytest.raises(ValueError):
        evaluate_bar_exit(TradeSide.LONG, 95.0, 105.0, m1(2026, 1, 5, 9, 31, closed=False))
