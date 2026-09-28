"""
TradeRecord: the durable, storable record of an executed (paper or live)
trade. The parameter_snapshot field is mandatory -- historical trades must
retain the exact configuration that generated them even if parameters
change later. See docs/ARCHITECTURE.md#configuration-versioning.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.core.enums import BiasDirection, SessionName, StrategyName, TradeSide, TradeStatus
from backend.core.models.target import Target


class TrailingEvent(BaseModel):
    time: datetime
    new_stop: float
    reason: str


class ExitFill(BaseModel):
    time: datetime
    price: float
    quantity: int
    reason: str


class TradeRecord(BaseModel):
    trade_id: str
    strategy: StrategyName
    strategy_version: str

    symbol: str
    side: TradeSide
    status: TradeStatus = TradeStatus.PENDING

    entry_time: Optional[datetime] = None
    entry_price: Optional[float] = None
    initial_stop: Optional[float] = None
    initial_risk_dollars: Optional[float] = None
    quantity: int = 0

    targets: list[Target] = Field(default_factory=list)
    exits: list[ExitFill] = Field(default_factory=list)

    exit_time: Optional[datetime] = None
    pnl_dollars: Optional[float] = None
    r_multiple: Optional[float] = None

    mae: Optional[float] = Field(default=None, description="Maximum adverse excursion.")
    mfe: Optional[float] = Field(default=None, description="Maximum favorable excursion.")

    be_time: Optional[datetime] = None
    be_price: Optional[float] = None
    trailing_events: list[TrailingEvent] = Field(default_factory=list)

    exit_reason: Optional[str] = None

    htf_bias: Optional[BiasDirection] = None
    session: Optional[SessionName] = None
    liquidity_target_id: Optional[str] = None

    active_confluences: list[str] = Field(default_factory=list)
    failed_confluences: list[str] = Field(default_factory=list)

    parameter_snapshot: dict[str, Any] = Field(default_factory=dict)
    decision_log_ref: Optional[str] = None
