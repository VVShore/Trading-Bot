from backend.config.loader import load_config
from backend.config.schema import AppConfig


def test_default_config_loads_and_validates():
    config = load_config()
    assert isinstance(config, AppConfig)


def test_default_config_live_trading_disabled():
    config = load_config()
    assert config.execution.live_trading_enabled is False


def test_default_risk_values_match_spec_defaults():
    config = load_config()
    assert config.risk.risk_dollars == 300
    assert config.risk.max_daily_loss == 2000  # decision 4
    assert config.risk.max_losses == 2
    assert config.risk.max_trades_per_day == 6  # decision 3


def test_htf_lookback_within_supported_range():
    config = load_config()
    assert 1 <= config.htf.lookback_candles <= config.htf.max_lookback_candles
    assert config.htf.max_lookback_candles == 12


def test_management_be_plus_one_disallowed_by_default():
    config = load_config()
    assert config.management.be_plus_one is False


def test_config_parameter_snapshot_is_serializable():
    config = load_config()
    snapshot = config.parameter_snapshot()
    assert isinstance(snapshot, dict)
    assert snapshot["execution"]["live_trading_enabled"] is False
