"""
Tradovate broker adapter -- STRUCTURE ONLY, disabled in V1.

This module defines the shape of a future TradovateBroker (REST + WebSocket,
per Tradovate's documented API) so the architecture can later add:

    PaperBroker            (enabled, V1)
    TradovateDemoBroker    (future)
    TradovateLiveBroker    (future)

...without changing the Strategy, RiskEngine, or TradeManager. No network
calls are implemented here. Construction always raises NotImplementedError.

Credentials, when this is implemented, must come from environment variables
(e.g. TRADOVATE_CLIENT_ID, TRADOVATE_CLIENT_SECRET, TRADOVATE_ACCOUNT_ID)
and must never be committed to the repo.
"""

from __future__ import annotations

from typing import Optional

from backend.core.models.order import Order, OrderResult
from backend.execution.base import ExecutionBroker


class TradovateBroker(ExecutionBroker):
    name = "tradovate"

    def __init__(self, environment: str = "demo") -> None:
        # Intentionally does not call super().__init__() with a working
        # implementation -- this adapter is a structural placeholder only.
        raise NotImplementedError(
            "TradovateBroker is a structural placeholder for a future demo/live "
            "integration. It is intentionally disabled in V1. Use PaperBroker."
        )

    @property
    def is_live(self) -> bool:
        # Will be True only for a future TradovateLiveBroker subclass.
        return False

    def submit_order(self, order: Order) -> OrderResult:  # pragma: no cover - not implemented
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:  # pragma: no cover - not implemented
        raise NotImplementedError

    def get_position(self, symbol: str) -> Optional[dict]:  # pragma: no cover - not implemented
        raise NotImplementedError
