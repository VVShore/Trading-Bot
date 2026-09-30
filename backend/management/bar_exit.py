"""
Same-bar stop/target ambiguity policy (owner decision 8).

Paper/backtest only. If ONE 1-minute candle reaches both the stop and the target we cannot
know the order, so we assume the STOP was hit first, emit a SafetyEvent, and the caller
halts the session (`enforce_safety_event`). Live execution will use broker-side OCO brackets
instead, so this policy never applies there.

Pure decision logic only. Wiring it into PaperBroker bracket exits is Phase 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from backend.core.enums import Timeframe, TradeSide
from backend.core.models.candle import Candle
from backend.risk.daily_limits import DailyRiskState

SAME_BAR_SL_TP = "SAME_BAR_SL_TP"


class BarExitOutcome(str, Enum):
    NONE = "none"
    STOP = "stop"
    TARGET = "target"
    AMBIGUOUS_STOP_FIRST = "ambiguous_stop_first"  # both touched: stop assumed first


@dataclass(frozen=True)
class SafetyEvent:
    kind: str
    time: datetime
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "time": self.time.isoformat(), "detail": self.detail}


@dataclass(frozen=True)
class BarExitResult:
    outcome: BarExitOutcome
    safety_event: Optional[SafetyEvent] = None

    @property
    def exits_at_stop(self) -> bool:
        return self.outcome in (BarExitOutcome.STOP, BarExitOutcome.AMBIGUOUS_STOP_FIRST)


def evaluate_bar_exit(side: TradeSide, stop: float, target: Optional[float], candle: Candle) -> BarExitResult:
    """`target=None` means a stop-only bracket (the target can never be hit)."""
    if candle.timeframe != Timeframe.M1:
        raise ValueError("Same-bar policy is defined for 1-minute candles only.")
    if not candle.is_closed:
        raise ValueError("Exit evaluation requires a closed candle.")

    if side == TradeSide.LONG:
        stop_hit = candle.low <= stop
        target_hit = target is not None and candle.high >= target
    else:
        stop_hit = candle.high >= stop
        target_hit = target is not None and candle.low <= target

    if stop_hit and target_hit:
        event = SafetyEvent(
            kind=SAME_BAR_SL_TP,
            time=candle.close_time,
            detail=(
                f"1m candle {candle.open_time.isoformat()} reached both stop {stop} and target {target} "
                f"(H={candle.high}, L={candle.low}); stop assumed first."
            ),
        )
        return BarExitResult(BarExitOutcome.AMBIGUOUS_STOP_FIRST, event)
    if stop_hit:
        return BarExitResult(BarExitOutcome.STOP)
    if target_hit:
        return BarExitResult(BarExitOutcome.TARGET)
    return BarExitResult(BarExitOutcome.NONE)


def enforce_safety_event(result: BarExitResult, daily_state: DailyRiskState) -> None:
    """Halt trading for the rest of the session when the same-bar safety event fired."""
    if result.safety_event is not None:
        daily_state.halt_session(f"Safety event {result.safety_event.kind}: trading halted for the session.")
