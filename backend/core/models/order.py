"""Order domain model used by the ExecutionBroker abstraction."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.core.enums import OrderType, TradeSide


class Order(BaseModel):
    order_id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    side: TradeSide
    order_type: OrderType
    quantity: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    created_at: Optional[datetime] = None

    # Parent trade linkage, so fills can be attributed back to a TradeRecord.
    trade_id: Optional[str] = None


class OrderResult(BaseModel):
    order_id: str
    accepted: bool
    filled: bool = False
    fill_price: Optional[float] = None
    fill_quantity: int = 0
    commission: float = 0.0
    slippage: float = 0.0
    rejection_reason: Optional[str] = None
    timestamp: Optional[datetime] = None
