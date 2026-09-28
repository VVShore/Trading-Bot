"""
Strategy interface.

A Strategy receives market state (candles, liquidity, bias, concepts, market
state snapshot) and returns a TradeSetup describing what it saw and whether
it wants to trade. Strategies must NOT talk to a broker directly -- that
happens downstream once a TradeSetup indicates a trade decision.

Detection logic (FVG, OB, manipulation, etc.) does not belong inside a
Strategy subclass; strategies ASK detectors for answers. See
backend/concepts/ for detector modules once implemented (Step 5+).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from backend.config.schema import AppConfig
from backend.core.models.bias import HTFBiasResult
from backend.core.models.market_state import MarketStateSnapshot
from backend.core.models.setup import TradeSetup


@dataclass
class MarketContext:
    """
    Everything a strategy needs to evaluate a single point in time.
    Populated by the engine (backtest replay or forward-test realtime feed)
    from the market-data / liquidity / concept / bias modules.
    """
    as_of: datetime
    symbol: str
    htf_bias: Optional[HTFBiasResult] = None
    market_state: Optional[MarketStateSnapshot] = None
    # Detector outputs are intentionally loosely typed here (dict of concept
    # collections) so the base interface doesn't need to change every time a
    # new detector is added. Concrete strategies narrow this as needed.
    liquidity: list[Any] = None  # list[LiquidityObject]
    concepts: list[Any] = None  # list[ConceptObject]
    candles: dict[str, list[Any]] = None  # keyed by timeframe -> list[Candle]


class Strategy(ABC):
    name: str
    version: str

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @abstractmethod
    def evaluate(self, context: MarketContext) -> TradeSetup:
        """Evaluate current market context and return a TradeSetup (trade or no-trade)."""
        ...
