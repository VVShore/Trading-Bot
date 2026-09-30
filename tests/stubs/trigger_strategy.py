"""
TestTriggerStrategy: integration-test double. NEVER import this from backend/.

Given a populated MarketContext it skips every discretionary condition and emits a TRADE
setup with predefined entry / stop / target prices, so the downstream pipeline
(Risk -> Intent -> Broker -> Log) can be exercised end to end before any real detector exists.

TradeSetup.strategy is a fixed enum with no test member, so the setup borrows the V1 name and is
told apart by strategy_version == "TEST-TRIGGER-0.0.0" (and reason text).
"""

from __future__ import annotations

from typing import Optional, Sequence

from backend.config.schema import AppConfig
from backend.core.enums import StrategyName, TradeSide
from backend.core.models.setup import TradeSetup
from backend.core.models.target import Target
from backend.strategies.base import MarketContext, Strategy


class TestTriggerStrategy(Strategy):
    __test__ = False  # not a pytest test class, despite the name

    name = "TEST_TRIGGER"
    version = "TEST-TRIGGER-0.0.0"

    def __init__(
        self,
        config: AppConfig,
        side: TradeSide,
        entry: float,
        stop: float,
        targets: Sequence[float],
    ) -> None:
        super().__init__(config)
        self._side, self._entry, self._stop = side, entry, stop
        self._targets = tuple(targets)

    def evaluate(self, context: MarketContext) -> TradeSetup:
        populated = bool(context.candles.get("1m"))
        common = dict(
            setup_id=f"test-trigger-{context.as_of.isoformat()}",
            strategy=StrategyName.NY_AM_HTF_CONTINUATION_V1,
            strategy_version=self.version,
            symbol=context.symbol,
            evaluated_at=context.as_of,
            parameter_snapshot=self.config.parameter_snapshot(),
        )
        if not populated:
            return TradeSetup(**common, decision="NO_TRADE", reason="Test trigger: context has no closed candles.")
        return TradeSetup(
            **common,
            proposed_side=self._side,
            proposed_entry=self._entry,
            proposed_stop=self._stop,
            targets=[Target(type="test_target", price=p, source="test", priority=i + 1) for i, p in enumerate(self._targets)],
            decision="TRADE",
            reason="Test trigger: discretionary conditions bypassed.",
        )
