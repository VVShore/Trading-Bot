"""
TradeStore

Persists TradeRecord objects (executed paper/live trades), separate from
the DecisionLogger (which records every evaluation, traded or not). Same
newline-delimited-JSON approach for V1; swap for a real DB later.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.core.models.trade import TradeRecord


class TradeStore:
    def __init__(self, store_path: Path) -> None:
        self.store_path = store_path
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, trade: TradeRecord) -> None:
        with open(self.store_path, "a") as f:
            f.write(trade.model_dump_json() + "\n")

    def read_all(self) -> list[TradeRecord]:
        if not self.store_path.exists():
            return []
        results = []
        with open(self.store_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                results.append(TradeRecord.model_validate(json.loads(line)))
        return results
