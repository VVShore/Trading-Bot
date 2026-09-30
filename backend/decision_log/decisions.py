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

import dataclasses
import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from backend.core.models.setup import TradeSetup

EVENT_KEY = "event_kind"


def _encode(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"Cannot serialise {type(obj).__name__} into the decision log")


class DecisionLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, setup: TradeSetup) -> None:
        with open(self.log_path, "a") as f:
            f.write(setup.model_dump_json() + "\n")

    def record_event(self, kind: str, setup_id: str, at: datetime, payload: dict[str, Any]) -> None:
        """
        Append a downstream step of a decision (risk verdict, fill check, order result, ...).
        `at` is the simulated/decision time supplied by the caller, never the wall clock, so
        replays produce identical logs. Setups written by `record()` keep their original shape.
        """
        line = {EVENT_KEY: kind, "setup_id": setup_id, "at": at.isoformat(), "payload": payload}
        with open(self.log_path, "a") as f:
            f.write(json.dumps(line, default=_encode) + "\n")

    def _lines(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        with open(self.log_path, "r") as f:
            return [json.loads(line) for line in f if line.strip()]

    def read_all(self) -> list[TradeSetup]:
        """Every recorded TradeSetup (event lines are skipped)."""
        return [TradeSetup.model_validate(d) for d in self._lines() if EVENT_KEY not in d]

    def read_trail(self, setup_id: Optional[str] = None) -> list[dict[str, Any]]:
        """All entries in write order as {event_kind, setup_id, at, payload}; a setup line is kind 'setup'."""
        trail = []
        for d in self._lines():
            if EVENT_KEY in d:
                entry = d
            else:
                entry = {EVENT_KEY: "setup", "setup_id": d["setup_id"], "at": d["evaluated_at"], "payload": d}
            if setup_id is None or entry["setup_id"] == setup_id:
                trail.append(entry)
        return trail
