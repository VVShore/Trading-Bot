import ast
from pathlib import Path

import pytest

from backend.core.enums import OrderType, StrategyName, TradeSide
from backend.core.models.order_intent import OrderIntent, UnauthorizedOrderIntentError
from tests.helpers import DECISION_TIME

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def test_direct_construction_is_refused():
    with pytest.raises(UnauthorizedOrderIntentError):
        OrderIntent(
            intent_id="x", setup_id="x", strategy=StrategyName.NY_AM_HTF_CONTINUATION_V1,
            strategy_version="0", symbol="MNQ", side=TradeSide.LONG, order_type=OrderType.MARKET,
            quantity=1, entry_price=1.0, stop_price=0.5, targets=(),
            risk_per_contract_dollars=1.0, approved_risk_dollars=1.0, created_at=DECISION_TIME,
        )


def _referenced_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.name for a in node.names)
    return names


def test_only_risk_engine_references_issue_order_intent():
    offenders = [
        str(p.relative_to(BACKEND))
        for p in BACKEND.rglob("*.py")
        if "issue_order_intent" in _referenced_names(p)
        and p.name not in {"engine.py", "order_intent.py"}
    ]
    assert offenders == []
    assert "issue_order_intent" in _referenced_names(BACKEND / "risk" / "engine.py")
