"""
DecisionLogger

Every strategy evaluation (TradeSetup) must be recorded, whether or not it
resulted in a trade. This module is intentionally storage-agnostic in V1:
it writes newline-delimited JSON to disk, which is sufficient for
backtesting/forward-test debugging and is trivially migrated to a real
database later without changing calling code (see backend/decision_log
vs. a future `logging` package name collision -- this package is named
`decision_log`, not `logging`, to avoid shadowing Python's stdlib module).
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.core.models.setup import TradeSetup


class DecisionLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, setup: TradeSetup) -> None:
        with open(self.log_path, "a") as f:
            f.write(setup.model_dump_json() + "\n")

    def read_all(self) -> list[TradeSetup]:
        if not self.log_path.exists():
            return []
        results = []
        with open(self.log_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                results.append(TradeSetup.model_validate(json.loads(line)))
        return results
