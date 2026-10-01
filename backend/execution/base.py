"""
ExecutionBroker abstraction.

All order placement in V1 flows through this interface so that backtest,
forward-test (paper), and (eventually) live execution share identical
strategy/risk/trade-management code -- only the broker implementation
changes.

LIVE TRADING GUARD
-------------------
V1 must never send live orders. This is enforced at multiple layers:

  1. AppConfig.execution.live_trading_enabled defaults to False and must
     be explicitly set to True in config to change (it is not currently
     wired to do anything -- there is no live broker implementation yet).
  2. The module-level LIVE_TRADING_ENABLED constant below is a hard-coded
     kill switch independent of config. It is False and must stay False
     for V1.
  3. TradovateBroker (backend/execution/tradovate.py) raises on
     construction unless explicitly acknowledged as a non-executing
     "structure only" adapter -- see that file.

Do not remove or bypass these guards to "test something quickly."
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from backend.core.models.candle import Candle
from backend.core.models.order import Order, OrderResult

if TYPE_CHECKING:
    from backend.core.models.order_intent import OrderIntent
    from backend.execution.events import ExitEvent

# HARD GUARD. Do not set True. There is no live execution path in V1.
LIVE_TRADING_ENABLED: bool = False


class LiveTradingDisabledError(RuntimeError):
    pass


class ExecutionBroker(ABC):
    """Base interface every broker (paper or real) must implement."""

    name: str = "base"

    def __init__(self) -> None:
        if not LIVE_TRADING_ENABLED and self.is_live:
            raise LiveTradingDisabledError(
                f"{self.__class__.__name__} is marked as a live broker, but "
                "LIVE_TRADING_ENABLED is False. V1 does not support live execution."
            )

    @property
    def is_live(self) -> bool:
        """Subclasses that ever touch real money must override this to True."""
        return False

    @abstractmethod
    def submit_order(self, order: Order) -> OrderResult:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[dict]:
        ...

    # ---- gated entry + bracket handling ------------------------------------------------
    # Non-abstract on purpose: a broker that cannot do these fails loudly / does nothing.

    def submit_intent(
        self, intent: "OrderIntent", reference_price: Optional[float] = None, at: Optional[datetime] = None
    ) -> OrderResult:
        """Place an entry from a RiskEngine-approved intent (and attach its stop/target bracket)."""
        raise NotImplementedError(f"{self.__class__.__name__} does not implement submit_intent.")

    def on_candle(self, candle: Candle) -> list["ExitEvent"]:
        """Evaluate resting bracket orders against a CLOSED candle. Real brokers do this server-side (OCO)."""
        return []

    def flatten_position(
        self, symbol: str, reference_price: float, at: datetime, reason: str = "session_flatten"
    ) -> Optional["ExitEvent"]:
        """Close any open position in `symbol` with a MARKET order. Returns the closed-trade event, if managed."""
        raise NotImplementedError(f"{self.__class__.__name__} does not implement flatten_position.")

    def open_trades(self) -> list:
        """Currently open TradeRecords (empty for brokers that do not keep them)."""
        return []
