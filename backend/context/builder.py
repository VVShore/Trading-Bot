"""
MarketContextBuilder: aggregator state -> MarketContext for the strategy.

Only CLOSED candles go into the context (developing bars are never exposed), and
`as_of` is the close time of the latest ingested 1M candle, i.e. the moment everything in
the context was already known. Detector results (`detections`) are computed from those same
closed candles; today every default detector answers UNKNOWN / NOT_AVAILABLE.
"""

from __future__ import annotations

from typing import Optional, Sequence

from backend.core.enums import Timeframe
from backend.detectors.base import Detector, DetectorInput
from backend.market.candles.aggregator import DEFAULT_TIMEFRAMES, TimeframeAggregator
from backend.strategies.base import MarketContext

DEFAULT_LOOKBACK = 50


class MarketContextBuilder:
    def __init__(
        self,
        aggregator: TimeframeAggregator,
        lookback: Optional[dict[Timeframe, int]] = None,
        detectors: Sequence[Detector] = (),
    ) -> None:
        names = [d.name for d in detectors]
        if len(set(names)) != len(names) or "" in names:
            raise ValueError(f"Detector names must be unique and non-empty: {names}")
        self._agg = aggregator
        self._lookback = lookback or {}
        self._detectors = tuple(detectors)

    def build(self) -> MarketContext:
        if self._agg.as_of is None:
            raise ValueError("No candles ingested yet; cannot build a context.")
        candles = {}
        for tf in (Timeframe.M1, *DEFAULT_TIMEFRAMES):
            bars = self._agg.closed_bars(tf, self._lookback.get(tf, DEFAULT_LOOKBACK))
            assert all(b.is_closed and b.close_time <= self._agg.as_of for b in bars), "lookahead guard"
            candles[tf.value] = bars
        # DetectorInput re-checks that every candle is closed and known by as_of (no lookahead).
        data = DetectorInput(symbol=self._agg.symbol, as_of=self._agg.as_of, candles=candles)
        detections = {d.name: d.detect(data) for d in self._detectors}
        return MarketContext(as_of=self._agg.as_of, symbol=self._agg.symbol, candles=candles, detections=detections)
