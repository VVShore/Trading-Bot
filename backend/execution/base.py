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
from typing import Optional

from backend.core.models.order import Order, OrderResult

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
