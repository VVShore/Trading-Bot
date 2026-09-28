from datetime import date

from backend.config.schema import RiskConfig
from backend.risk.daily_limits import DailyRiskState


def _state(**overrides):
    config = RiskConfig(**overrides)
    return DailyRiskState(trading_day=date(2026, 1, 5), config=config)


def test_locks_out_after_max_losses():
    state = _state(max_losses=2)
    state.record_trade_result(-100)
    assert state.can_trade() is True
    state.record_trade_result(-100)
    assert state.can_trade() is False
    assert "Max losses" in state.lockout_reason


def test_locks_out_after_max_trades_per_day():
    state = _state(max_losses=99, max_trades_per_day=2)
    state.record_trade_result(50)
    assert state.can_trade() is True
    state.record_trade_result(50)
    assert state.can_trade() is False


def test_locks_out_after_max_daily_loss_dollars():
    state = _state(max_losses=99, max_trades_per_day=99, max_daily_loss=500)
    state.record_trade_result(-600)
    assert state.can_trade() is False


def test_breakeven_trade_allowance():
    state = _state(max_losses=99, allow_be_trades=1)
    state.record_trade_result(0, is_breakeven=True)
    assert state.can_trade() is True
    state.record_trade_result(0, is_breakeven=True)
    assert state.can_trade() is False


def test_consecutive_losses_reset_on_win():
    state = _state(max_losses=99)
    state.record_trade_result(-50)
    assert state.consecutive_losses == 1
    state.record_trade_result(50)
    assert state.consecutive_losses == 0
