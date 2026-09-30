"""
Default detector set: one PlaceholderDetector per concept named in the design docs.

These carry NO trading logic; they only reserve the names and the wiring. Required timeframes
are limited to what the SDS/glossary state outright (1H bias, 1M manipulation candle); the rest
declare none until their rules are specified. Replace a placeholder with a real detector class
(same `name`) only once its definition is confirmed in docs/ASSUMPTIONS.md.
"""

from __future__ import annotations

from backend.core.enums import Timeframe
from backend.detectors.base import Detector, PlaceholderDetector


def default_detectors() -> list[Detector]:
    return [
        PlaceholderDetector("htf_bias", (Timeframe.H1,)),
        PlaceholderDetector("liquidity"),
        PlaceholderDetector("fvg"),
        PlaceholderDetector("ifvg"),
        PlaceholderDetector("ce"),
        PlaceholderDetector("manipulation", (Timeframe.M1,)),
        PlaceholderDetector("mss"),
        PlaceholderDetector("cisd"),
        PlaceholderDetector("smt"),
    ]
