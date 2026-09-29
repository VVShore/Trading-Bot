"""Resolve InstrumentSpec objects from AppConfig (config -> domain boundary)."""

from __future__ import annotations

from backend.config.schema import AppConfig
from backend.core.models.instrument import InstrumentSpec

# Per docs/SDS.md: ES is analysis-only (SMT reference), never executed.
ANALYSIS_ONLY_SYMBOLS = frozenset({"ES"})


def instrument_spec_from_config(config: AppConfig, symbol: str) -> InstrumentSpec:
    inst = config.execution.instruments.get(symbol)
    if inst is None:
        raise ValueError(f"Unknown instrument '{symbol}': not present in execution.instruments.")
    return InstrumentSpec(
        symbol=symbol,
        tick_size=inst.tick_size,
        point_value=inst.point_value,
        commission_per_contract=inst.commission_per_contract,
        estimated_slippage_points=inst.estimated_slippage_points,
        analysis_only=symbol in ANALYSIS_ONLY_SYMBOLS,
    )
