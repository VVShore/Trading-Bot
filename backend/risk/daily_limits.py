"""
Daily risk-limit tracking.

Tracks daily results and exposes a single `can_trade()` check the risk engine
calls before allowing entry. Lockouts derive strictly from the policy locks
(max daily loss, remaining-budget, max trades/day); see `_evaluate_lockout`.

This is intentionally a plain in-memory tracker for V1. Trading days roll over via
`start_new_day()` (the orchestrator calls it on the first candle of a new New York date).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from backend.config.schema import RiskConfig


@dataclass
class DailyRiskState:
    trading_day: date
    config: RiskConfig

    trades_taken: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    breakevens: int = 0
    unprofitable_trades: int = 0
    realized_pnl_dollars: float = 0.0
    locked_out: bool = False
    lockout_reason: Optional[str] = None

    def record_trade_result(self, pnl_dollars: float, is_breakeven: bool = False) -> None:
        self.trades_taken += 1
        self.realized_pnl_dollars += pnl_dollars

        if is_breakeven:
            self.breakevens += 1
            self.consecutive_losses = 0
        elif pnl_dollars > 0:
            self.wins += 1
            self.consecutive_losses = 0
        else:
            self.losses += 1
            self.consecutive_losses += 1
            self.unprofitable_trades += 1

        self._evaluate_lockout()

    def _evaluate_lockout(self) -> None:
        """
        Lockouts come ONLY from the owner's policy locks (docs/ASSUMPTIONS.md):
          - max daily loss reached                       ($2,000)
          - remaining loss budget < one full trade risk  (no partial sizing)
          - max trades per day                           (6; lifted only by the backtest flag)
        Wins/losses/breakevens are tracked as statistics but never lock trading by themselves.
        """
        c = self.config
        if self.realized_pnl_dollars <= -abs(c.max_daily_loss):
            self._lock(f"Max daily loss reached (${self.realized_pnl_dollars:.2f}).")
        elif self.remaining_loss_budget() < c.resolved_risk_dollars:
            # Decision 4: if what is left of the daily loss budget cannot cover one full
            # trade allocation, the session is over. No partial-size scaling in the MVP.
            self._lock(
                f"Remaining daily loss budget (${self.remaining_loss_budget():.2f}) cannot cover a full "
                f"trade allocation (${c.resolved_risk_dollars:.2f})."
            )
        elif self.trades_taken >= c.max_trades_per_day and not c.backtest_override_trade_frequency:
            self._lock(f"Max trades/day reached ({self.trades_taken}/{c.max_trades_per_day}).")

    def snapshot(self) -> dict[str, Any]:
        """Plain-dict summary of the day so far (for logs / run manifests)."""
        return {
            "trading_day": self.trading_day.isoformat(),
            "trades_taken": self.trades_taken,
            "wins": self.wins,
            "losses": self.losses,
            "breakevens": self.breakevens,
            "realized_pnl_dollars": round(self.realized_pnl_dollars, 6),
            "locked_out": self.locked_out,
            "lockout_reason": self.lockout_reason,
        }

    def start_new_day(self, day: date) -> None:
        """
        Multi-day rollover: reset every counter and lockout IN PLACE (so the RiskEngine and the
        orchestrator, which hold this object, see the new day). Days only move forward.
        A halted or loss-locked session ends with its day; it never carries over.
        """
        if day <= self.trading_day:
            raise ValueError(f"start_new_day({day}) must be after the current trading day {self.trading_day}.")
        self.trading_day = day
        self.trades_taken = self.wins = self.losses = self.breakevens = 0
        self.consecutive_losses = self.unprofitable_trades = 0
        self.realized_pnl_dollars = 0.0
        self.locked_out = False
        self.lockout_reason = None

    def remaining_loss_budget(self) -> float:
        """Dollars that can still be lost today before max_daily_loss is reached."""
        return abs(self.config.max_daily_loss) + min(self.realized_pnl_dollars, 0.0)

    def halt_session(self, reason: str) -> None:
        """Latch a lockout for the rest of the session (e.g. after a safety event)."""
        self._lock(reason)

    def _lock(self, reason: str) -> None:
        self.locked_out = True
        self.lockout_reason = reason

    def can_trade(self) -> bool:
        return not self.locked_out
