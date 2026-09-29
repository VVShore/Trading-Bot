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


# --- owner decision 7: entry window is a strategy CONDITION -----------------------------
from backend.core.enums import SetupConditionStatus  # noqa: E402


def _condition(setup, name):
    return next(c for c in setup.required_conditions if c.name == name)


def test_outside_entry_window_yields_no_trade_with_failed_condition():
    config = load_config()
    ctx = MarketContext(as_of=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc), symbol="MNQ")  # 07:00 ET
    setup = NyAmHtfContinuationV1(config=config).evaluate(ctx)
    assert setup.decision == "NO_TRADE"
    assert _condition(setup, "within_execution_window").status == SetupConditionStatus.FAIL
    assert "entry window" in setup.reason


def test_inside_entry_window_passes_the_window_condition_only():
    config = load_config()
    ctx = MarketContext(as_of=datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc), symbol="MNQ")  # 09:30 ET
    setup = NyAmHtfContinuationV1(config=config).evaluate(ctx)
    assert _condition(setup, "within_execution_window").status == SetupConditionStatus.PASS
    assert setup.decision == "NO_TRADE"  # other conditions still unevaluated (no detectors yet)
    assert _condition(setup, "valid_htf_bias").status == SetupConditionStatus.NOT_EVALUATED


def test_window_edges_start_inclusive_end_exclusive():
    from backend.market.sessions.clock import is_within_entry_window
    s = load_config().session
    assert is_within_entry_window(datetime(2026, 1, 5, 9, 2, tzinfo=timezone(__import__("datetime").timedelta(hours=-5))), s)
    assert not is_within_entry_window(datetime(2026, 1, 5, 9, 1, tzinfo=timezone(__import__("datetime").timedelta(hours=-5))), s)
    assert not is_within_entry_window(datetime(2026, 1, 5, 11, 0, tzinfo=timezone(__import__("datetime").timedelta(hours=-5))), s)
