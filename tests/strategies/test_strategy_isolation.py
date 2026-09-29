"""Strategies must not be able to reach brokers, the RiskEngine internals, or OrderIntent."""
import ast
from pathlib import Path

FORBIDDEN_PREFIXES = ("backend.execution", "backend.risk", "backend.core.models.order_intent", "backend.core.models.order")
STRATEGIES = Path(__file__).resolve().parents[2] / "backend" / "strategies"


def _imports(path: Path) -> list[str]:
    mods = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def test_strategy_modules_import_no_execution_or_risk_code():
    bad = {
        p.name: [m for m in _imports(p) if m.startswith(FORBIDDEN_PREFIXES)]
        for p in STRATEGIES.glob("*.py")
    }
    assert {k: v for k, v in bad.items() if v} == {}
