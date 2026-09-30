"""
Detector contract.

A detector answers ONE question about the market from CLOSED candles only
(e.g. "is there a valid 1H bias?"). Strategies ask detectors; they never contain
detection maths themselves (see strategies/base.py).

Contract
--------
* Input is a `DetectorInput`, which refuses to exist if any candle is unclosed or closes after
  `as_of`. No-lookahead is therefore enforced once, centrally, not re-implemented per detector.
* `Detector.detect()` is the only public entry point and is not meant to be overridden. It
  returns NOT_AVAILABLE when required data is missing or the detector fails, and otherwise
  delegates to `_detect()`.
* The default outcome is never a positive signal: `PlaceholderDetector` returns UNKNOWN.
  DETECTED / NOT_DETECTED exist for real detectors added later; nothing here produces them.
* A strategy must treat anything other than DETECTED as "condition not met" (fail closed).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from backend.core.enums import Timeframe
from backend.core.models.candle import Candle


class DetectorStatus(str, Enum):
    UNKNOWN = "unknown"              # data present, but the detection rule is not implemented/specified
    NOT_AVAILABLE = "not_available"  # required data missing, or the detector failed
    DETECTED = "detected"            # reserved for real detectors
    NOT_DETECTED = "not_detected"    # reserved for real detectors


class LookaheadError(ValueError):
    pass


@dataclass(frozen=True)
class DetectorInput:
    symbol: str
    as_of: datetime
    candles: Mapping[str, Sequence[Candle]]  # timeframe value ("1m", "1h", ...) -> CLOSED candles, oldest first

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("DetectorInput.as_of must be timezone-aware.")
        for tf, bars in self.candles.items():
            for bar in bars:
                if not bar.is_closed:
                    raise LookaheadError(f"{tf}: unclosed candle {bar.open_time.isoformat()} passed to a detector.")
                if bar.close_time > self.as_of:
                    raise LookaheadError(
                        f"{tf}: candle closing {bar.close_time.isoformat()} is after as_of {self.as_of.isoformat()}."
                    )


@dataclass(frozen=True)
class DetectorResult:
    detector: str
    status: DetectorStatus
    as_of: datetime
    reason: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    @property
    def detected(self) -> bool:
        return self.status == DetectorStatus.DETECTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "status": self.status.value,
            "as_of": self.as_of.isoformat(),
            "reason": self.reason,
            "data": dict(self.data),
        }


class Detector(ABC):
    name: str = ""
    required_timeframes: tuple[Timeframe, ...] = ()

    def detect(self, data: DetectorInput) -> DetectorResult:
        missing = [tf.value for tf in self.required_timeframes if not data.candles.get(tf.value)]
        if missing:
            return self._result(data, DetectorStatus.NOT_AVAILABLE, f"No closed bars yet for: {', '.join(missing)}.")
        try:
            result = self._detect(data)
        except Exception as exc:  # a broken detector must never crash the pipeline or look like a signal
            return self._result(data, DetectorStatus.NOT_AVAILABLE, f"Detector error: {type(exc).__name__}: {exc}")
        if not isinstance(result, DetectorResult):
            return self._result(data, DetectorStatus.NOT_AVAILABLE, "Detector returned an invalid result.")
        return result

    @abstractmethod
    def _detect(self, data: DetectorInput) -> DetectorResult: ...

    def _result(self, data: DetectorInput, status: DetectorStatus, reason: str = "", **extra: Any) -> DetectorResult:
        return DetectorResult(detector=self.name, status=status, as_of=data.as_of, reason=reason, data=extra)


class PlaceholderDetector(Detector):
    """Interface placeholder: the concept's rule is not specified/implemented, so the answer is UNKNOWN."""

    def __init__(self, name: str, required_timeframes: tuple[Timeframe, ...] = ()) -> None:
        self.name = name
        self.required_timeframes = required_timeframes

    def _detect(self, data: DetectorInput) -> DetectorResult:
        return self._result(data, DetectorStatus.UNKNOWN, "Detection rule not specified/implemented.")
