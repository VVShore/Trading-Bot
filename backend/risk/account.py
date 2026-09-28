"""Account state used by the risk engine for percent-based risk and drawdown checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Account:
    account_size: float
    starting_balance: float
    current_balance: float

    def daily_pnl_percent(self) -> float:
        if self.starting_balance == 0:
            return 0.0
        return ((self.current_balance - self.starting_balance) / self.starting_balance) * 100.0
