"""
PipelineOrchestrator: the production loop that runs one closed 1M candle at a time.

    raw record / tick
      -> MarketDataPipeline (BarNormalizer -> closed-candle gate -> TimeframeAggregator -> MarketContext)
      -> per candle, in this fixed order:
           a. fill the intent approved on the PREVIOUS bar at THIS bar's open
              (RiskEngine.reconcile_fill -> broker.submit_intent), then settle_intent
           b. broker.on_candle(candle): evaluate stop/target brackets (this bar can hit them);
              closed trades feed DailyRiskState, safety events halt the session
           c. Strategy.evaluate(context) -> if TRADE: RiskEngine.evaluate -> pending intent
      -> every step is written to the DecisionLogger

Timing: a decision made at a bar's close fills no earlier than the next bar's open. A pending
intent whose next candle is not contiguous (missing minute, maintenance halt, weekend) is
CANCELLED, never filled against an unrelated price.

Intent settlement (owner resolution R7): `RiskEngine.settle_intent` is called after EVERY fill,
rejection and cancellation of an approved intent, inside a `finally`, so an exception anywhere in
the fill path can never leave the single-position slot blocked.

The orchestrator knows nothing about detectors, ICT rules or a specific broker: the strategy and
broker are injected, and the orchestrator only talks to their interfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from backend.config.schema import AppConfig
from backend.context.builder import MarketContextBuilder
from backend.core.models.candle import Candle
from backend.core.models.order import OrderResult
from backend.core.models.order_intent import OrderIntent
from backend.core.models.setup import TradeSetup
from backend.data.normalizer import BarNormalizer
from backend.data.pipeline import MarketDataPipeline, ProcessedBar
from backend.data.provider import MarketDataProvider
from backend.data.ticks import Tick, TickCandleBuilder
from backend.decision_log.decisions import DecisionLogger
from backend.decision_log.trades import TradeStore
from backend.detectors.base import Detector
from backend.detectors.registry import default_detectors
from backend.execution.base import ExecutionBroker
from backend.execution.events import ExitEvent
from backend.execution.paper import PaperBroker
from backend.management.bar_exit import enforce_safety_event
from backend.market.candles.aggregator import TimeframeAggregator
from backend.risk.daily_limits import DailyRiskState
from backend.risk.engine import RiskDecision, RiskEngine
from backend.strategies.base import MarketContext, Strategy


@dataclass
class StepResult:
    skipped: bool = False                       # the raw record produced no closed candle (developing / halt row)
    candle: Optional[Candle] = None
    context: Optional[MarketContext] = None
    setup: Optional[TradeSetup] = None
    risk: Optional[RiskDecision] = None         # only when the strategy said TRADE
    entry: Optional[OrderResult] = None         # entry fill/rejection processed on this bar
    cancelled_intent: Optional[str] = None      # intent_id cancelled on this bar (stale)
    exits: list[ExitEvent] = field(default_factory=list)


@dataclass(frozen=True)
class _Pending:
    intent: OrderIntent
    decision_as_of: datetime  # the decision bar's close time == the next bar's open time


class PipelineOrchestrator:
    def __init__(
        self,
        *,
        pipeline: MarketDataPipeline,
        strategy: Strategy,
        risk_engine: RiskEngine,
        broker: ExecutionBroker,
        daily_state: DailyRiskState,
        decision_logger: DecisionLogger,
        tick_builder: Optional[TickCandleBuilder] = None,
        log_no_trade: bool = True,
    ) -> None:
        self._pipeline = pipeline
        self._strategy = strategy
        self._risk = risk_engine
        self._broker = broker
        self._daily = daily_state
        self._log = decision_logger
        self._ticks = tick_builder
        self._log_no_trade = log_no_trade
        self._records: Optional[Iterator[Any]] = None
        self._pending: Optional[_Pending] = None

    # ------------------------------------------------------------------ inputs

    def step(self) -> Optional[StepResult]:
        """Consume ONE raw record from the provider and run the full cycle. None once the provider is exhausted."""
        if self._records is None:
            self._records = iter(self._pipeline.provider.stream())
        try:
            raw = next(self._records)
        except StopIteration:
            return None
        processed = self._pipeline.process(raw)
        return StepResult(skipped=True) if processed is None else self._on_bar(processed)

    def run(self) -> Iterator[StepResult]:
        """Step until the provider is exhausted, then cancel anything still pending."""
        while (result := self.step()) is not None:
            yield result
        self.finish()

    def on_tick(self, tick: Tick) -> list[StepResult]:
        """Feed a raw tick; each 1M candle it closes runs the full cycle."""
        if self._ticks is None:
            raise RuntimeError("No TickCandleBuilder configured for this orchestrator.")
        results = []
        for candle in self._ticks.ingest(tick):
            processed = self._pipeline.ingest_candle(candle)
            if processed is not None:
                results.append(self._on_bar(processed))
        return results

    def finish(self, reason: str = "end of data") -> None:
        """Cancel a still-pending intent (and settle it). Open positions are left as they are."""
        if self._pending is not None:
            pending, self._pending = self._pending, None
            self._cancel(pending, at=pending.decision_as_of, reason=reason)

    @property
    def has_pending_intent(self) -> bool:
        return self._pending is not None

    # ------------------------------------------------------------------ one closed candle

    def _on_bar(self, bar: ProcessedBar) -> StepResult:
        candle, ctx = bar.candle, bar.context
        result = StepResult(candle=candle, context=ctx)

        self._fill_pending(candle, result)                                   # a
        self._evaluate_brackets(candle, result)                              # b
        self._decide(candle, ctx, result)                                    # c
        return result

    def _fill_pending(self, candle: Candle, result: StepResult) -> None:
        if self._pending is None:
            return
        pending, self._pending = self._pending, None
        intent = pending.intent
        if candle.open_time != pending.decision_as_of:
            result.cancelled_intent = intent.intent_id
            self._cancel(pending, at=candle.open_time, reason="stale: next candle is not contiguous with the decision bar")
            return
        try:
            check = self._risk.reconcile_fill(intent, candle.open)
            self._event("fill_check", intent.setup_id, candle.open_time, check.to_dict())
            if not check.approved:
                return
            entry = self._broker.submit_intent(check.intent, reference_price=candle.open, at=candle.open_time)
            result.entry = entry
            self._event("order_result", intent.setup_id, candle.open_time, entry.model_dump(mode="json"))
            if entry.filled:
                open_trades = getattr(self._broker, "open_trades", lambda: [])()
                trade = next((t for t in open_trades if t.trade_id == intent.intent_id), None)
                if trade is not None:
                    self._event("trade_opened", intent.setup_id, candle.open_time, trade.model_dump(mode="json"))
        finally:
            self._risk.settle_intent(intent.intent_id)  # fill, rejection or exception: the slot is always released

    def _evaluate_brackets(self, candle: Candle, result: StepResult) -> None:
        for ev in self._broker.on_candle(candle):
            result.exits.append(ev)
            setup_id = ev.trade.decision_log_ref or ev.trade.trade_id
            self._daily.record_trade_result(ev.net_pnl)
            self._event("order_result", setup_id, candle.close_time, ev.result.model_dump(mode="json"))
            if ev.bar_result.safety_event is not None:
                self._event("safety_event", setup_id, candle.close_time, ev.bar_result.safety_event.to_dict())
            enforce_safety_event(ev.bar_result, self._daily)  # same-bar SL/TP halts the session
            self._event("trade_closed", setup_id, candle.close_time, ev.trade.model_dump(mode="json"))
            self._event("daily_state", setup_id, candle.close_time, self._daily_snapshot(ev.net_pnl))

    def _decide(self, candle: Candle, ctx: MarketContext, result: StepResult) -> None:
        setup = self._strategy.evaluate(ctx)
        result.setup = setup
        if setup.decision == "TRADE" or self._log_no_trade:
            self._log.record(setup)
        if setup.decision != "TRADE":
            return
        risk = self._risk.evaluate(setup, market_price=candle.close)
        result.risk = risk
        self._event("risk_decision", setup.setup_id, ctx.as_of, risk.to_dict())
        if risk.approved:
            self._pending = _Pending(intent=risk.intent, decision_as_of=setup.evaluated_at)

    # ------------------------------------------------------------------ helpers

    def _cancel(self, pending: _Pending, at: datetime, reason: str) -> None:
        try:
            self._event("intent_cancelled", pending.intent.setup_id, at,
                        {"intent_id": pending.intent.intent_id, "reason": reason})
        finally:
            self._risk.settle_intent(pending.intent.intent_id)

    def _event(self, kind: str, setup_id: str, at: datetime, payload: dict[str, Any]) -> None:
        self._log.record_event(kind, setup_id, at, payload)

    def _daily_snapshot(self, net_pnl: float) -> dict[str, Any]:
        d = self._daily
        return {
            "trades_taken": d.trades_taken, "wins": d.wins, "losses": d.losses,
            "realized_pnl_dollars": d.realized_pnl_dollars, "locked_out": d.locked_out,
            "lockout_reason": d.lockout_reason, "last_trade_net_pnl": net_pnl,
        }


def build_paper_orchestrator(
    *,
    config: AppConfig,
    provider: MarketDataProvider,
    normalizer: BarNormalizer,
    strategy: Strategy,
    trading_day: date,
    log_path: Path,
    trade_store_path: Optional[Path] = None,
    detectors: Optional[Sequence[Detector]] = None,
    use_ticks: bool = False,
    log_no_trade: bool = True,
    broker: Optional[PaperBroker] = None,
) -> tuple[PipelineOrchestrator, PaperBroker, DailyRiskState]:
    """Standard paper/replay wiring. Returns (orchestrator, broker, daily_state) for inspection.
    Pass `broker` to inject a pre-built PaperBroker (its open_position_count feeds the RiskEngine)."""
    aggregator = TimeframeAggregator(normalizer.symbol, config.session)
    builder = MarketContextBuilder(aggregator, detectors=default_detectors() if detectors is None else detectors)
    pipeline = MarketDataPipeline(provider, normalizer, aggregator, builder, config.session)
    daily = DailyRiskState(trading_day=trading_day, config=config.risk)
    if broker is None:
        broker = PaperBroker.from_config(config, trade_store=TradeStore(trade_store_path) if trade_store_path else None)
    engine = RiskEngine(config, daily, broker.open_position_count)
    orchestrator = PipelineOrchestrator(
        pipeline=pipeline, strategy=strategy, risk_engine=engine, broker=broker, daily_state=daily,
        decision_logger=DecisionLogger(log_path),
        tick_builder=TickCandleBuilder(normalizer.symbol) if use_ticks else None,
        log_no_trade=log_no_trade,
    )
    return orchestrator, broker, daily
