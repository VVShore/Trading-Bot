"""
Configuration schema.

This is the authoritative, validated shape of backend/config/default.yaml
(and any per-strategy override file in backend/config/strategies/).

Every tunable mentioned in docs/ASSUMPTIONS.md as PROVISIONAL has a field
here with the documented default. Nothing in strategy/detector code should
hard-code these values -- they should read them from a loaded AppConfig.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# --- Owner policy locks (docs/ASSUMPTIONS.md, "Policy locks", decisions 1-9) ---------
RISK_PER_TRADE_RANGE = (200.0, 400.0)   # decision 1
DAILY_LOSS_LIMIT_RANGE = (2000.0, 2500.0)  # decision 4
REQUIRED_TIMEZONE = "America/New_York"  # decision 6


class StrategyToggle(BaseModel):
    enabled: bool = True
    name: str = "NY_AM_HTF_CONTINUATION_V1"
    version: str = "0.1.0"


class SessionConfig(BaseModel):
    # Decision 6: every aggregator/session component is forced to New York time.
    timezone: str = REQUIRED_TIMEZONE
    analysis_start: str = "09:00"
    entry_start: str = "09:02"
    entry_end: str = "11:00"  # configurable; may later extend to 11:30
    session_close: str = "16:00"
    close_before_session_close_minutes: int = 5
    # CME equity-futures daily maintenance halt (ET). No candles exist inside it and no
    # aggregated bar may span it.
    maintenance_halt_start: str = "17:00"
    maintenance_halt_end: str = "18:00"
    # 4H bars are anchored here (ET wall clock): 18:00, 22:00, 02:00, 06:00, 10:00, 14:00.
    # Confirmed by the owner (docs/ASSUMPTIONS.md, resolution 3); the 14:00 bar is cut at the 17:00 halt.
    four_hour_anchor: str = "18:00"  # CONFIRMED (owner resolution 3)

    @field_validator("timezone")
    @classmethod
    def _timezone_is_new_york(cls, v: str) -> str:
        if v != REQUIRED_TIMEZONE:
            raise ValueError(f"session.timezone is locked to {REQUIRED_TIMEZONE} for the MVP (got {v!r}).")
        return v


class SessionImportanceConfig(BaseModel):
    asia: float = 7.0
    london: float = 8.0
    pre_ny: float = 7.0
    ny_open: float = 10.0
    lunch_pm: float = 5.0


class LiquidityStalenessConfig(BaseModel):
    # PROVISIONAL: distance (in NQ points) beyond which a liquidity level is
    # considered no longer immediately relevant. See docs/ASSUMPTIONS.md.
    stale_distance_points: float = 150.0


class LiquidityToleranceConfig(BaseModel):
    equal_level_tolerance_points: float = 5.0  # PROVISIONAL


class LiquidityImportanceConfig(BaseModel):
    pdh_importance: float = 6.0
    pdl_importance: float = 6.0
    pdh_pdl_weight: float = 7.0
    equal_highs_importance: float = 8.0
    equal_lows_importance: float = 8.0
    internal_liquidity_importance: float = 6.0
    internal_liquidity_weight: float = 8.0
    external_liquidity_importance: float = 7.0
    external_liquidity_weight: float = 8.0


class LiquidityConfig(BaseModel):
    tolerance: LiquidityToleranceConfig = Field(default_factory=LiquidityToleranceConfig)
    staleness: LiquidityStalenessConfig = Field(default_factory=LiquidityStalenessConfig)
    importance: LiquidityImportanceConfig = Field(default_factory=LiquidityImportanceConfig)


class HTFConfig(BaseModel):
    timeframe: str = "1h"
    lookback_candles: int = 5
    max_lookback_candles: int = 12  # engine must support 1-12
    strong_body_percent: float = 60.0  # PROVISIONAL
    dominant_wick_rejection_percent: float = 70.0  # PROVISIONAL
    wick_exceeds_body_percent: float = 12.5  # PROVISIONAL, "10-15%" -> midpoint default
    body_engulf_threshold_percent: float = 85.0  # PROVISIONAL


class FVGConfig(BaseModel):
    min_size_points: float = 1.0  # PROVISIONAL, must be > 0 to count as a gap
    max_age_candles: int = 200  # PROVISIONAL


class OTEConfig(BaseModel):
    levels: list[float] = Field(default_factory=lambda: [0.62, 0.705, 0.79])


class ManipulationCandleConfig(BaseModel):
    """
    PROVISIONAL detector parameters. See docs/ASSUMPTIONS.md for the full
    definition and reasoning -- this is the single source of truth for
    tuning the manipulation-candle detector; do not duplicate elsewhere.
    """
    min_wick_size_points: float = 8.0
    max_body_percent: float = 40.0
    require_liquidity_sweep: bool = True
    require_htf_confluence: bool = False
    require_rejection: bool = True
    lookback_candles: int = 30
    max_age_candles: int = 20
    require_confirmation: bool = False


class SMTConfig(BaseModel):
    enabled: bool = True
    reference_symbol: str = "ES"
    lookback_candles: int = 20


class EntryConfig(BaseModel):
    manipulation_ce_enabled: bool = True
    fvg_confirmation_enabled: bool = False  # secondary model, disabled by default


class ManagementConfig(BaseModel):
    move_to_be: bool = True
    be_plus_one: bool = False  # explicitly disallowed by spec
    be_breathing_room_points: float = 20.0  # PROVISIONAL
    fixed_trailing_increment_points: float = 20.0  # PROVISIONAL, optional mode
    use_fixed_trailing: bool = False


class NewsConfig(BaseModel):
    enabled: bool = True
    lockout_minutes_after_release: int = 15
    blackout_event_types: list[str] = Field(
        default_factory=lambda: ["CPI", "NFP", "FOMC", "FED_CHAIR_SPEECH", "RED_FOLDER"]
    )


class RiskConfig(BaseModel):
    risk_mode: str = "dollar"  # "dollar" | "percent"
    risk_dollars: float = 300.0
    risk_percent: float = 0.6
    account_size: float = 50000.0

    max_daily_loss: float = 2000.0  # decision 4: fixed, within DAILY_LOSS_LIMIT_RANGE
    max_trades_per_day: int = 6  # decision 3 ("max_daily_trades"); one setting, not two
    max_concurrent_positions: int = Field(default=1, ge=1)  # decision 3

    daily_account_loss_threshold_percent: float = 20.0  # informational safety cap, not a broker rule

    reduce_risk_after_losing_day: bool = True
    increase_risk_after_win: bool = False

    # Decision 7: manual halt (e.g. around high-impact news). Enforced by the RiskEngine.
    pause_trading: bool = False
    # Decision 3: replay/backtest may explicitly lift the max-trades-per-day cap. Only
    # honoured when execution.broker_environment == "paper" (validated on AppConfig).
    backtest_override_trade_frequency: bool = False

    @property
    def resolved_risk_dollars(self) -> float:
        """Max dollar risk per trade (dollar mode, or percent of account_size)."""
        if self.risk_mode == "percent":
            return self.account_size * (self.risk_percent / 100.0)
        return self.risk_dollars


class InstrumentConfig(BaseModel):
    symbol: str = "MNQ"
    point_value: float = 2.0  # MNQ = $2/point
    tick_size: float = 0.25
    commission_per_contract: float = 0.74
    estimated_slippage_points: float = 0.25


class ExecutionConfig(BaseModel):
    active_symbol: str = "MNQ"
    allow_nq_manual_override: bool = True  # IGNORED in the MVP (decision 5): only active_symbol trades
    # Decision 6: contract rollover is manual. Set the specific contract here (e.g. "MNQZ6");
    # there is no automated roll logic. None = unspecified.
    active_contract: Optional[str] = None
    live_trading_enabled: bool = False  # HARD GUARD -- see backend/execution/base.py
    broker_environment: str = "paper"  # "paper" | "tradovate_demo" | "tradovate_live"
    instruments: dict[str, InstrumentConfig] = Field(
        default_factory=lambda: {
            "MNQ": InstrumentConfig(symbol="MNQ", point_value=2.0, tick_size=0.25),
            "NQ": InstrumentConfig(symbol="NQ", point_value=20.0, tick_size=0.25),
            "ES": InstrumentConfig(symbol="ES", point_value=50.0, tick_size=0.25),
        }
    )


class AppConfig(BaseModel):
    strategy: StrategyToggle = Field(default_factory=StrategyToggle)
    session: SessionConfig = Field(default_factory=SessionConfig)
    session_importance: SessionImportanceConfig = Field(default_factory=SessionImportanceConfig)
    htf: HTFConfig = Field(default_factory=HTFConfig)
    liquidity: LiquidityConfig = Field(default_factory=LiquidityConfig)
    fvg: FVGConfig = Field(default_factory=FVGConfig)
    ote: OTEConfig = Field(default_factory=OTEConfig)
    manipulation: ManipulationCandleConfig = Field(default_factory=ManipulationCandleConfig)
    smt: SMTConfig = Field(default_factory=SMTConfig)
    entry: EntryConfig = Field(default_factory=EntryConfig)
    management: ManagementConfig = Field(default_factory=ManagementConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)

    config_version: str = "0.1.0"

    @model_validator(mode="after")
    def _enforce_policy_locks(self) -> "AppConfig":
        # Policy locks are enforced on the assembled config (not on the RiskConfig building
        # block) so unit tests can still construct small RiskConfig objects directly.
        lo, hi = RISK_PER_TRADE_RANGE
        resolved = self.risk.resolved_risk_dollars
        if not lo <= resolved <= hi:
            raise ValueError(f"Risk per trade must be within ${lo:.0f}-${hi:.0f} (resolved ${resolved:.2f}).")
        dlo, dhi = DAILY_LOSS_LIMIT_RANGE
        if not dlo <= self.risk.max_daily_loss <= dhi:
            raise ValueError(
                f"risk.max_daily_loss must be within ${dlo:.0f}-${dhi:.0f} (got ${self.risk.max_daily_loss:.2f})."
            )
        if self.risk.backtest_override_trade_frequency and self.execution.broker_environment != "paper":
            raise ValueError("risk.backtest_override_trade_frequency is only allowed when broker_environment == 'paper'.")
        return self

    def parameter_snapshot(self) -> dict:
        """Full snapshot for attaching to trades/backtests. See docs/ARCHITECTURE.md."""
        return self.model_dump(mode="json")
