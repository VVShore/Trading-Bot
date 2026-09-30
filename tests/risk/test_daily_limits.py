from datetime import date

from backend.config.schema import RiskConfig
from backend.risk.daily_limits import DailyRiskState


def _state(**overrides):
    config = RiskConfig(**overrides)
    return DailyRiskState(trading_day=date(2026, 1, 5), config=config)


def test_losses_alone_never_lock_out():
    state = _state()
    for _ in range(4):
        state.record_trade_result(-100)  # -400 total: well inside the $2,000 budget
    assert state.can_trade() is True
    assert state.losses == 4 and state.consecutive_losses == 4  # tracked as statistics only


def test_wins_alone_never_lock_out():
    state = _state(max_trades_per_day=99)
    for _ in range(5):
        state.record_trade_result(100)
    assert state.can_trade() is True


def test_breakevens_alone_never_lock_out():
    state = _state(max_trades_per_day=99)
    for _ in range(4):
        state.record_trade_result(0, is_breakeven=True)
    assert state.can_trade() is True and state.breakevens == 4


def test_locks_out_after_max_trades_per_day():
    state = _state(max_trades_per_day=2)
    state.record_trade_result(50)
    assert state.can_trade() is True
    state.record_trade_result(50)
    assert state.can_trade() is False
    assert "Max trades/day" in state.lockout_reason


def test_trade_cap_can_be_lifted_only_by_the_explicit_backtest_flag():
    state = _state(max_trades_per_day=2, backtest_override_trade_frequency=True)
    for _ in range(5):
        state.record_trade_result(50)
    assert state.can_trade() is True


def test_locks_out_after_max_daily_loss_dollars():
    state = _state(max_trades_per_day=99, max_daily_loss=500)
    state.record_trade_result(-600)
    assert state.can_trade() is False
    assert "Max daily loss" in state.lockout_reason


def test_consecutive_losses_reset_on_win():
    state = _state()
    state.record_trade_result(-50)
    assert state.consecutive_losses == 1
    state.record_trade_result(50)
    assert state.consecutive_losses == 0


def test_halt_session_latches():
    state = _state()
    state.halt_session("manual")
    assert state.can_trade() is False and state.lockout_reason == "manual"
