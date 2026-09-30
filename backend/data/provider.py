"""
MarketDataProvider: the ONLY thing that knows where raw market data comes from.

A provider yields raw vendor payloads (plain mappings) in arrival order. It does no
interpretation: turning payloads into canonical Candles is the Normalizer's job, and
strategies never see a provider (they see MarketContext).

Implementations: CsvBarProvider / InMemoryProvider (replay, tests) here; a Tradovate
WebSocket/REST provider is a later, separate module.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


class MarketDataProvider(ABC):
    name: str = "provider"

    @abstractmethod
    def stream(self) -> Iterator[Mapping[str, Any]]:
        """Yield raw payloads in the order they arrived."""
        ...


class InMemoryProvider(MarketDataProvider):
    name = "in_memory"

    def __init__(self, records: Iterable[Mapping[str, Any]]) -> None:
        self._records = list(records)

    def stream(self) -> Iterator[Mapping[str, Any]]:
        yield from self._records


class CsvBarProvider(MarketDataProvider):
    """Replay historical bars from a CSV with a header row. Values are yielded as raw strings."""

    name = "csv"

    def __init__(self, path: Path, delimiter: str = ",") -> None:
        self._path = Path(path)
        self._delimiter = delimiter

    def stream(self) -> Iterator[Mapping[str, Any]]:
        with open(self._path, newline="") as f:
            yield from csv.DictReader(f, delimiter=self._delimiter)
