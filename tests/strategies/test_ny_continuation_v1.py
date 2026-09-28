from datetime import datetime, timezone

from backend.config.loader import load_config
from backend.core.enums import StrategyName
from backend.strategies.base import MarketContext
from backend.strategies.fvg_inversion_confirmation import FvgInversionConfirmation
from backend.strategies.ny_continuation_v1 import NyAmHtfContinuationV1


def _context(symbol="MNQ"):
    return MarketContext(as_of=datetime.now(timezone.utc), symbol=symbol)


def test_v1_strategy_returns_no_trade_setup_without_detectors():
    config = load_config()
    strategy = NyAmHtfContinuationV1(config=config)
    setup = strategy.evaluate(_context())

    assert setup.strategy == StrategyName.NY_AM_HTF_CONTINUATION_V1
    assert setup.decision == "NO_TRADE"
    assert setup.all_required_passed is False  # nothing evaluated yet


def test_v1_setup_carries_parameter_snapshot():
    config = load_config()
    strategy = NyAmHtfContinuationV1(config=config)
    setup = strategy.evaluate(_context())
    assert setup.parameter_snapshot["risk"]["risk_dollars"] == config.risk.risk_dollars


def test_secondary_strategy_disabled_by_default():
    config = load_config()
    strategy = FvgInversionConfirmation(config=config)
    setup = strategy.evaluate(_context())
    assert setup.decision == "NO_TRADE"
    assert "Disabled" in setup.reason


def test_strategies_do_not_share_mutable_state():
    config = load_config()
    s1 = NyAmHtfContinuationV1(config=config)
    s2 = NyAmHtfContinuationV1(config=config)
    setup1 = s1.evaluate(_context())
    setup2 = s2.evaluate(_context())
    assert setup1.setup_id != setup2.setup_id
