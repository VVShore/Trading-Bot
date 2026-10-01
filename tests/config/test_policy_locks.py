import pytest
from pydantic import ValidationError

from backend.config.loader import load_config
from backend.config.schema import AppConfig, ExecutionConfig, RiskConfig, SessionConfig


def _cfg(**sections):
    base = load_config().model_dump()
    for name, values in sections.items():
        base[name].update(values)
    return AppConfig.model_validate(base)


def test_locked_defaults():
    c = load_config()
    assert c.risk.account_size == 50000
    assert c.risk.resolved_risk_dollars == 300
    assert c.risk.max_daily_loss == 2000
    assert c.risk.max_trades_per_day == 6 and c.risk.max_concurrent_positions == 1
    assert c.risk.pause_trading is False and c.risk.backtest_override_trade_frequency is False
    assert c.execution.active_symbol == "MNQ"
    assert c.execution.active_contract is None  # rollover is manual config, never automatic
    assert c.session.timezone == "America/New_York"


@pytest.mark.parametrize("dollars", [199.0, 401.0])
def test_risk_per_trade_must_stay_in_200_400(dollars):
    with pytest.raises(ValidationError):
        _cfg(risk={"risk_dollars": dollars})


def test_percent_mode_is_range_checked_after_resolution():
    _cfg(risk={"risk_mode": "percent", "risk_percent": 0.5})  # $250 fine
    with pytest.raises(ValidationError):
        _cfg(risk={"risk_mode": "percent", "risk_percent": 1.0})  # $500 out of range


@pytest.mark.parametrize("limit", [1999.0, 2501.0])
def test_daily_loss_limit_must_stay_in_2000_2500(limit):
    with pytest.raises(ValidationError):
        _cfg(risk={"max_daily_loss": limit})


def test_daily_loss_limit_edges_allowed():
    assert _cfg(risk={"max_daily_loss": 2500.0}).risk.max_daily_loss == 2500


def test_timezone_is_locked_to_new_york():
    with pytest.raises(ValidationError):
        _cfg(session={"timezone": "UTC"})


def test_trade_frequency_override_only_in_paper():
    assert _cfg(risk={"backtest_override_trade_frequency": True}).risk.backtest_override_trade_frequency
    with pytest.raises(ValidationError):
        _cfg(risk={"backtest_override_trade_frequency": True}, execution={"broker_environment": "tradovate_demo"})


def test_building_blocks_stay_constructible_for_unit_tests():
    assert RiskConfig(max_daily_loss=500).max_daily_loss == 500  # policy is enforced on AppConfig
    assert SessionConfig().timezone == "America/New_York"
    assert ExecutionConfig().active_symbol == "MNQ"


def test_flatten_time_is_1457_and_validated():
    assert load_config().session.flatten_time == "14:57"
    with pytest.raises(ValidationError):
        _cfg(session={"flatten_time": "10:30"})   # before entry_end (11:00)
    with pytest.raises(ValidationError):
        _cfg(session={"flatten_time": "17:30"})   # inside/after the maintenance halt
