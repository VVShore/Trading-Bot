"""
MarketDataPipeline: Provider -> Normalizer -> closed-candle gate -> Aggregator -> MarketContext.

Yields one MarketContext per accepted closed 1M candle. Developing (unclosed) updates are
counted and dropped; candles inside the daily maintenance halt are dropped (vendors sometimes
emit flat filler bars there) and counted. Everything else that is invalid raises.
The same pipeline serves replay and (later) live, so decision timing is identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional

from backend.config.schema import SessionConfig
from backend.context.builder import MarketContextBuilder
from backend.core.models.candle import Candle
from backend.data.normalizer import BarNormalizer
from backend.data.provider import MarketDataProvider
from backend.market.candles.aggregator import TimeframeAggregator
from backend.market.sessions.clock import in_maintenance_halt
from backend.strategies.base import MarketContext


@dataclass
class PipelineStats:
    records_in: int = 0
    developing_skipped: int = 0
    halt_skipped: int = 0
    candles_ingested: int = 0


@dataclass(frozen=True)
class ProcessedBar:
    """One accepted closed 1M candle and the context that exists once it has closed."""
    candle: Candle
    context: MarketContext


class MarketDataPipeline:
    def __init__(
        self,
        provider: MarketDataProvider,
        normalizer: BarNormalizer,
        aggregator: TimeframeAggregator,
        context_builder: MarketContextBuilder,
        session: SessionConfig,
    ) -> None:
        self._provider = provider
        self._normalizer = normalizer
        self._aggregator = aggregator
        self._builder = context_builder
        self._session = session
        self.stats = PipelineStats()

    @property
    def provider(self) -> MarketDataProvider:
        return self._provider

    def process(self, raw: Mapping[str, Any]) -> Optional[ProcessedBar]:
        """Normalize one raw payload and ingest it. None if it was dropped (developing or halt bar)."""
        self.stats.records_in += 1
        return self.ingest_candle(self._normalizer.normalize(raw))

    def ingest_candle(self, candle: Candle) -> Optional[ProcessedBar]:
        """Gate an already-normalized candle (e.g. built from ticks), aggregate it, build the context."""
        if not candle.is_closed:
            self.stats.developing_skipped += 1
            return None
        if in_maintenance_halt(candle.open_time, self._session):
            self.stats.halt_skipped += 1
            return None
        self._aggregator.ingest(candle)
        self.stats.candles_ingested += 1
        return ProcessedBar(candle=candle, context=self._builder.build())

    def run(self) -> Iterator[MarketContext]:
        for raw in self._provider.stream():
            processed = self.process(raw)
            if processed is not None:
                yield processed.context
