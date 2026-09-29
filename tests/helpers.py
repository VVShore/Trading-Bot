"""Shared test builders (plain functions, no fixtures magic)."""
from datetime import date, datetime, timezone

from backend.config.loader import load_config
from backend.core.enums import StrategyName, TradeSide
from backend.core.models.setup import TradeSetup
from backend.core.models.target import Target
from backend.risk.daily_limits import DailyRiskState

TRADING_DAY = date(2026, 1, 5)  # a Monday
# 09:30 America/New_York on 2026-01-05 == 14:30 UTC
DECISION_TIME = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


def make_setup(**overrides) -> TradeSetup:
    fields = dict(
        setup_id="setup-1",
        strategy=StrategyName.NY_AM_HTF_CONTINUATION_V1,
        strategy_version="0.1.0",
        symbol="MNQ",
        evaluated_at=DECISION_TIME,
        proposed_side=TradeSide.LONG,
        proposed_entry=20000.00,
        proposed_stop=19990.00,  # 10 pts -> $20 + slippage + commission per contract
        targets=[Target(type="test", price=20030.00, source="test")],
        decision="TRADE",
    )
    fields.update(overrides)
    return TradeSetup(**fields)


def make_daily_state(config=None, **risk_overrides) -> DailyRiskState:
    config = config or load_config()
    risk = config.risk.model_copy(update=risk_overrides)
    return DailyRiskState(trading_day=TRADING_DAY, config=risk)
