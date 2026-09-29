"""
Daily risk-limit tracking.

Tracks the counters described in the spec (max losses, max trades, max
wins, max unprofitable trades, max daily loss dollars) and exposes a single
`can_trade()` check the strategy/risk engine calls before allowing entry.

This is intentionally a plain in-memory tracker for V1; persistence lives
in whatever calls this (e.g. ForwardTestEngine keeps one instance per day).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

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
        c = self.config
        if self.losses >= c.max_losses:
            self._lock(f"Max losses reached ({self.losses}/{c.max_losses}).")
        elif self.trades_taken >= c.max_trades_per_day and not c.backtest_override_trade_frequency:
            self._lock(f"Max trades/day reached ({self.trades_taken}/{c.max_trades_per_day}).")
        elif self.wins >= c.max_wins_per_day:
            self._lock(f"Max wins/day reached ({self.wins}/{c.max_wins_per_day}).")
        elif self.unprofitable_trades >= c.max_unprofitable_trades_per_day:
            self._lock(
                f"Max unprofitable trades/day reached "
                f"({self.unprofitable_trades}/{c.max_unprofitable_trades_per_day})."
            )
        elif self.breakevens > c.allow_be_trades:
            self._lock(f"Breakeven trade allowance exceeded ({self.breakevens}/{c.allow_be_trades}).")
        elif self.realized_pnl_dollars <= -abs(c.max_daily_loss):
            self._lock(f"Max daily loss reached (${self.realized_pnl_dollars:.2f}).")
        elif self.remaining_loss_budget() < c.resolved_risk_dollars:
            # Decision 4: if what is left of the daily loss budget cannot cover one full
            # trade allocation, the session is over. No partial-size scaling in the MVP.
            self._lock(
                f"Remaining daily loss budget (${self.remaining_loss_budget():.2f}) cannot cover a full "
                f"trade allocation (${c.resolved_risk_dollars:.2f})."
            )

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
