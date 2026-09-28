"""
Core enumerations shared across the trading engine.

Keeping these centralized avoids "magic strings" scattered across
detectors, the strategy, risk engine, and dashboard.
"""

from enum import Enum


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


class BiasDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    TRANSITIONAL = "transitional"


class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MarketState(str, Enum):
    """
    A timeframe can be in exactly one of these states at a time, but
    different timeframes may report different states simultaneously.
    See MarketStateEngine / MarketStateSnapshot.
    """
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    ACCUMULATION = "accumulation"
    DISTRIBUTION = "distribution"
    EXPANSION = "expansion"
    RETRACEMENT = "retracement"
    MANIPULATION = "manipulation"
    CONSOLIDATION = "consolidation"
    REVERSAL = "reversal"
    UNKNOWN = "unknown"


class LiquidityType(str, Enum):
    PDH = "pdh"
    PDL = "pdl"
    PWH = "pwh"
    PWL = "pwl"
    SESSION_HIGH = "session_high"
    SESSION_LOW = "session_low"
    EQUAL_HIGH = "equal_high"
    EQUAL_LOW = "equal_low"
    INTERNAL_LIQUIDITY = "internal_liquidity"
    EXTERNAL_LIQUIDITY = "external_liquidity"
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"


class LiquidityRole(str, Enum):
    """A liquidity object's function is contextual, not fixed at creation."""
    TARGET = "target"
    SUPPORT = "support"
    RESISTANCE = "resistance"
    REJECTION_AREA = "rejection_area"


class LiquidityStatus(str, Enum):
    ACTIVE = "active"
    SWEPT = "swept"
    INVALIDATED = "invalidated"
    STALE = "stale"  # distributed sufficiently far away (provisional distance rule)


class SessionName(str, Enum):
    ASIA = "asia"
    LONDON = "london"
    PRE_NY = "pre_ny"
    NY_AM = "ny_am"
    LUNCH_PM = "lunch_pm"


class ConceptType(str, Enum):
    FVG = "fvg"
    IFVG = "ifvg"
    ORDER_BLOCK = "order_block"
    BREAKER = "breaker"
    OTE = "ote"
    CE = "ce"
    VOLUME_IMBALANCE = "volume_imbalance"
    BPR = "bpr"
    SMT = "smt"
    MANIPULATION_CANDLE = "manipulation_candle"
    DISPLACEMENT = "displacement"
    REJECTION = "rejection"
    ENGULFING = "engulfing"


class ConceptStatus(str, Enum):
    ACTIVE = "active"
    FILLED = "filled"
    INVALIDATED = "invalidated"
    INVERTED = "inverted"
    EXPIRED = "expired"


class StrategyName(str, Enum):
    NY_AM_HTF_CONTINUATION_V1 = "NY_AM_HTF_CONTINUATION_V1"
    FVG_INVERSION_CONFIRMATION = "FVG_INVERSION_CONFIRMATION"


class TradeSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class TradeStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_CLOSED = "partially_closed"
    CLOSED = "closed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class SetupConditionStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"


class InvalidationReason(str, Enum):
    HTF_BIAS_VIOLATED = "htf_bias_violated"
    TARGET_ALREADY_REACHED = "target_already_reached"
    OPPOSING_STRUCTURE = "opposing_structure"
    SMT_REVERSAL_WARNING = "smt_reversal_warning"
    EXCESSIVE_CONSOLIDATION = "excessive_consolidation"
    RISK_REJECTED = "risk_rejected"
    ENTRY_WINDOW_EXPIRED = "entry_window_expired"
    NEWS_LOCKOUT = "news_lockout"
    MANIPULATION_SETUP_EXPIRED = "manipulation_setup_expired"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class BrokerEnvironment(str, Enum):
    PAPER = "paper"
    TRADOVATE_DEMO = "tradovate_demo"
    TRADOVATE_LIVE = "tradovate_live"


class ExecutionSymbol(str, Enum):
    MNQ = "MNQ"
    NQ = "NQ"
    ES = "ES"  # analysis-only, never executed in V1
