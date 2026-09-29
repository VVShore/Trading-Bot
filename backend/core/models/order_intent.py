"""
OrderIntent: the broker-independent representation of an order the RiskEngine
has APPROVED.

Bypass protection
-----------------
An OrderIntent can only be built through `issue_order_intent()`, which is meant
to be called by exactly one place: `backend/risk/engine.py`. Direct construction
raises `UnauthorizedOrderIntentError`, and brokers re-check authenticity before
acting. Python cannot make this impossible for someone determined to defeat it,
so `tests/core/test_order_intent.py` also statically enforces that nothing in
`backend/` other than the RiskEngine references `issue_order_intent`, and that
strategy modules never import it (or any broker/execution module).

Not coupled to any broker: only fields a paper/real broker adapter would need.
Stop/target *management* is a later phase; stop and targets are carried through
unchanged so that phase can use them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.core.enums import OrderType, StrategyName, TradeSide
from backend.core.models.target import Target

_ISSUER_TOKEN = object()


class UnauthorizedOrderIntentError(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str  # deterministic: derived from setup_id
    setup_id: str
    strategy: StrategyName
    strategy_version: str
    symbol: str
    side: TradeSide
    order_type: OrderType
    quantity: int
    entry_price: float  # planned entry the size was computed from; actual fill comes from the broker
    stop_price: float
    targets: tuple[Target, ...]
    risk_per_contract_dollars: float
    approved_risk_dollars: float
    created_at: datetime  # the setup's evaluated_at (decision time), never wall-clock
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _ISSUER_TOKEN:
            raise UnauthorizedOrderIntentError(
                "OrderIntent can only be issued by the RiskEngine."
            )

    @property
    def is_authentic(self) -> bool:
        return self._token is _ISSUER_TOKEN

    @property
    def entry_order_id(self) -> str:
        return f"{self.intent_id}:entry"

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "setup_id": self.setup_id,
            "strategy": self.strategy.value,
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "targets": [t.model_dump(mode="json") for t in self.targets],
            "risk_per_contract_dollars": self.risk_per_contract_dollars,
            "approved_risk_dollars": self.approved_risk_dollars,
            "created_at": self.created_at.isoformat(),
        }


def issue_order_intent(**fields: Any) -> OrderIntent:
    """ONLY the RiskEngine may call this."""
    return OrderIntent(_token=_ISSUER_TOKEN, **fields)
