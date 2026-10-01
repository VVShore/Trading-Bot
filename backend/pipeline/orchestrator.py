"""
PipelineOrchestrator: the production loop that runs one closed 1M candle at a time.

    raw record / tick
      -> MarketDataPipeline (BarNormalizer -> closed-candle gate -> TimeframeAggregator -> MarketContext)
      -> per candle, in this fixed order:
           0. day rollover: the first candle of a new New York date resets DailyRiskState
           a. fill the intent approved on the PREVIOUS bar at THIS bar's open
              (RiskEngine.reconcile_fill -> broker.submit_intent), then settle_intent
           a2. session flatten (R12): at/after session.flatten_time (14:57 ET), or when a position
              survived into a new day, close it with a MARKET order at this bar's open
           b. broker.on_candle(candle): evaluate stop/target brackets (this bar can hit them);
              closed trades feed DailyRiskState, safety events halt the session
           c. Strategy.evaluate(context) -> if TRADE: RiskEngine.evaluate -> pending intent
      -> every step is written to the DecisionLogger

Timing: a decision made at a bar's close fills no earlier than the next bar's open. A pending
intent whose next candle is not contiguous (missing minute, maintenance halt, weekend) is
CANCELLED, never filled against an unrelated price (R11). An intent may not fill at or after the
flatten time either: it would be closed at once, so it is cancelled instead.

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

from backend.config.schema import AppConfig, SessionConfig
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
from backend.market.sessions.clock import parse_hhmm, to_market_time
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
    cancelled_intent: Optional[str] = None      # intent_id cancelled on this bar (stale / after flatten time)
    new_day: Optional[date] = None              # set when this candle started a new trading day
    exits: list[ExitEvent] = field(default_factory=list)  # bracket exits AND session flattens (see ExitEvent.reason)


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
        session: SessionConfig,
        tick_builder: Optional[TickCandleBuilder] = None,
        log_no_trade: bool = True,
    ) -> None:
        self._pipeline = pipeline
        self._strategy = strategy
        self._risk = risk_engine
        self._broker = broker
        self._daily = daily_state
        self._log = decision_logger
        self._flatten_at = parse_hhmm(session.flatten_time)
        self._day_history: list[dict[str, Any]] = []
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

    @property
    def pipeline_stats(self):
        """Row/candle counters of the underlying MarketDataPipeline."""
        return self._pipeline.stats

    def day_summaries(self) -> list[dict[str, Any]]:
        """Completed days plus the day in progress (empty until the first candle)."""
        current = [self._daily.snapshot()] if self._daily.trading_day != date.min else []
        return [*self._day_history, *current]

    # ------------------------------------------------------------------ one closed candle

    def _on_bar(self, bar: ProcessedBar) -> StepResult:
        candle, ctx = bar.candle, bar.context
        result = StepResult(candle=candle, context=ctx)

        self._roll_day(candle, result)                                       # 0
        self._fill_pending(candle, result)                                   # a
        self._flatten_if_due(candle, result)                                 # a2
        self._evaluate_brackets(candle, result)                              # b
        self._decide(candle, ctx, result)                                    # c
        return result

    def _roll_day(self, candle: Candle, result: StepResult) -> None:
        day = to_market_time(candle.open_time).date()
        current = self._daily.trading_day
        if day == current:
            return
        if day < current:
            raise ValueError(f"Candle {candle.open_time.isoformat()} belongs to {day}, before the current day {current}.")
        if current != date.min:  # date.min is the "no candle seen yet" placeholder
            finished = self._daily.snapshot()
            self._day_history.append(finished)
            self._event("day_rollover", f"day-{current.isoformat()}", candle.open_time, {"finished": finished, "next_day": day.isoformat()})
        self._daily.start_new_day(day)
        result.new_day = day

    def _fill_pending(self, candle: Candle, result: StepResult) -> None:
        if self._pending is None:
            return
        pending, self._pending = self._pending, None
        intent = pending.intent
        if candle.open_time != pending.decision_as_of:
            result.cancelled_intent = intent.intent_id
            self._cancel(pending, at=candle.open_time, reason="stale: next candle is not contiguous with the decision bar")
            return
        if to_market_time(candle.open_time).time() >= self._flatten_at:
            result.cancelled_intent = intent.intent_id
            self._cancel(pending, at=candle.open_time, reason="the fill bar is at/after the session flatten time")
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
                trade = next((t for t in self._broker.open_trades() if t.trade_id == intent.intent_id), None)
                if trade is not None:
                    self._event("trade_opened", intent.setup_id, candle.open_time, trade.model_dump(mode="json"))
        finally:
            self._risk.settle_intent(intent.intent_id)  # fill, rejection or exception: the slot is always released

    def _flatten_if_due(self, candle: Candle, result: StepResult) -> None:
        """R12: close any open position with a market order at this bar's open once 14:57 ET is reached.
        A position that somehow survived into a new day is closed at the first candle of that day."""
        if self._broker.get_position(candle.symbol) is None:
            return
        local = to_market_time(candle.open_time)
        due = local.time() >= self._flatten_at
        if not due:
            trade = next((t for t in self._broker.open_trades() if t.symbol == candle.symbol), None)
            due = trade is not None and trade.entry_time is not None and to_market_time(trade.entry_time).date() < local.date()
        if not due:
            return
        ev = self._broker.flatten_position(candle.symbol, reference_price=candle.open, at=candle.open_time)
        if ev is None:
            return
        setup_id = ev.trade.decision_log_ref or ev.trade.trade_id
        self._event("session_flatten", setup_id, candle.open_time,
                    {"symbol": candle.symbol, "reference_price": candle.open, "flatten_time": self._flatten_at.isoformat()})
        self._handle_exit(ev, candle.open_time, result)

    def _evaluate_brackets(self, candle: Candle, result: StepResult) -> None:
        for ev in self._broker.on_candle(candle):
            self._handle_exit(ev, candle.close_time, result)

    def _handle_exit(self, ev: ExitEvent, at: datetime, result: StepResult) -> None:
        """A trade closed (bracket or flatten): book it, enforce safety events, log it."""
        result.exits.append(ev)
        setup_id = ev.trade.decision_log_ref or ev.trade.trade_id
        self._daily.record_trade_result(ev.net_pnl)
        self._event("order_result", setup_id, at, ev.result.model_dump(mode="json"))
        if ev.bar_result.safety_event is not None:
            self._event("safety_event", setup_id, at, ev.bar_result.safety_event.to_dict())
        enforce_safety_event(ev.bar_result, self._daily)  # same-bar SL/TP halts the session
        self._event("trade_closed", setup_id, at, ev.trade.model_dump(mode="json"))
        self._event("daily_state", setup_id, at, self._daily_snapshot(ev.net_pnl))

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
    log_path: Path,
    trading_day: Optional[date] = None,
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
    # trading_day=None: the state starts at a placeholder and the first candle sets the real day.
    daily = DailyRiskState(trading_day=trading_day or date.min, config=config.risk)
    if broker is None:
        broker = PaperBroker.from_config(config, trade_store=TradeStore(trade_store_path) if trade_store_path else None)
    engine = RiskEngine(config, daily, broker.open_position_count)
    orchestrator = PipelineOrchestrator(
        pipeline=pipeline, strategy=strategy, risk_engine=engine, broker=broker, daily_state=daily,
        decision_logger=DecisionLogger(log_path), session=config.session,
        tick_builder=TickCandleBuilder(normalizer.symbol) if use_ticks else None,
        log_no_trade=log_no_trade,
    )
    return orchestrator, broker, daily
