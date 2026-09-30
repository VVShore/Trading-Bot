"""Events a broker reports back to the orchestrator after evaluating a candle."""

from __future__ import annotations

from dataclasses import dataclass

from backend.core.models.order import OrderResult
from backend.core.models.trade import TradeRecord
from backend.management.bar_exit import BarExitResult

EXIT_TARGET = "target"
EXIT_STOP = "stop"
EXIT_STOP_SAME_BAR = "stop_same_bar_ambiguity"
EXIT_MANUAL = "manual"


@dataclass(frozen=True)
class ExitEvent:
    """A bracket leg filled and the trade closed."""
    trade: TradeRecord        # status CLOSED, with realized P&L
    result: OrderResult       # the exit fill (price, slippage, commission, gross realized_pnl)
    reason: str               # EXIT_TARGET / EXIT_STOP / EXIT_STOP_SAME_BAR
    net_pnl: float            # gross realized P&L minus entry + exit commission
    bar_result: BarExitResult  # carries the SafetyEvent when both levels were touched in one bar
