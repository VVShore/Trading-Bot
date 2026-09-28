"""
Position sizing.

Implements the formula from the spec:

    risk_per_contract = abs(entry_price - stop_price) * point_value
    contracts = floor(max_risk / risk_per_contract)

Must never exceed configured risk due to rounding (we floor, never round-up),
and must reject the trade outright if a single contract already exceeds the
configured max risk.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class PositionSizeResult:
    contracts: int
    risk_per_contract_dollars: float
    total_risk_dollars: float
    accepted: bool
    rejection_reason: Optional[str] = None


def calculate_position_size(
    entry_price: float,
    stop_price: float,
    point_value: float,
    max_risk_dollars: float,
    commission_per_contract: float = 0.0,
    estimated_slippage_points: float = 0.0,
) -> PositionSizeResult:
    if entry_price == stop_price:
        return PositionSizeResult(
            contracts=0,
            risk_per_contract_dollars=0.0,
            total_risk_dollars=0.0,
            accepted=False,
            rejection_reason="Entry price equals stop price; undefined risk per contract.",
        )

    stop_distance_points = abs(entry_price - stop_price) + estimated_slippage_points
    risk_per_contract = stop_distance_points * point_value + commission_per_contract

    if risk_per_contract <= 0:
        return PositionSizeResult(
            contracts=0,
            risk_per_contract_dollars=risk_per_contract,
            total_risk_dollars=0.0,
            accepted=False,
            rejection_reason="Non-positive risk per contract.",
        )

    if risk_per_contract > max_risk_dollars:
        return PositionSizeResult(
            contracts=0,
            risk_per_contract_dollars=risk_per_contract,
            total_risk_dollars=0.0,
            accepted=False,
            rejection_reason=(
                f"Single contract risk (${risk_per_contract:.2f}) exceeds max permitted "
                f"risk (${max_risk_dollars:.2f})."
            ),
        )

    contracts = math.floor(max_risk_dollars / risk_per_contract)
    total_risk = contracts * risk_per_contract

    if contracts <= 0:
        return PositionSizeResult(
            contracts=0,
            risk_per_contract_dollars=risk_per_contract,
            total_risk_dollars=0.0,
            accepted=False,
            rejection_reason="Computed contract size is zero.",
        )

    return PositionSizeResult(
        contracts=contracts,
        risk_per_contract_dollars=risk_per_contract,
        total_risk_dollars=total_risk,
        accepted=True,
    )


def resolve_risk_dollars(risk_mode: str, risk_dollars: float, risk_percent: float, account_size: float) -> float:
    """UI allows editing either dollar risk or percent risk; the other is derived."""
    if risk_mode == "percent":
        return account_size * (risk_percent / 100.0)
    return risk_dollars
