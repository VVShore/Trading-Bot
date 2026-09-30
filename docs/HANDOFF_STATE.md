# HANDOFF_STATE — persistent source of truth for new Claude chats

Last verified: 2026-09-29, after Phases 4-5. Git: `b2b5683` (original) -> `feat: complete phases 1-3 baseline with 182 tests` -> `feat: pipeline orchestrator and bracket exits`. Commits exist only in the sandbox clone (delivered as a git bundle); nothing has been pushed to GitHub. Update this file, not chat history.
Owner rules live in `docs/ASSUMPTIONS.md` ("Policy locks" L1-L9, "Owner resolutions" R1-R8, provisional P1-P8); this file only summarizes them.

## 1. Verified state
- **Tests:** `pytest -q` -> **217 passed** (32 original -> 76 -> 159 -> 182 after Phase 3 -> 217: +20 broker bracket tests, +15 orchestrator tests). A fresh `git clone` of the final commit also passes.
- **`python main.py`** still boots and ends at `NO_TRADE` (main.py does not use the orchestrator yet).
- Mutation-checked in Phases 4-5: never settling intents, not settling when the broker raises, adding slippage to a limit target, removing the stale-intent check, not enforcing the same-bar halt, and removing stop slippage each make tests fail.
- Repo hygiene fix: `.gitignore` had `data/`, which silently hid `backend/data/` and `tests/data/` from git; it is now `/data/`.

## 2. Implemented (real, tested)
- **Config/domain:** `AppConfig` with policy-lock validators (risk/trade $200-400, daily loss $2,000-2,500, timezone locked to America/New_York, backtest trade-cap override only in paper); `InstrumentSpec` with exact-decimal `round_entry/round_stop/round_target`; `Candle` (finite, volume >= 0, tz-aware); `TradeSetup` (+ `proposed_entry`, `targets`); `OrderIntent` (issuer-token protected); `OrderStatus`.
- **Market data:** `MarketDataProvider` (`CsvBarProvider`, `InMemoryProvider`) -> `BarNormalizer` (NY time, explicit open/close stamping, closed flag) -> `MarketDataPipeline` (drops developing + halt rows) -> `TimeframeAggregator` (`market/candles/`; 1M -> 5M/15M/1H/4H, developing bar isolated, 17:00-18:00 ET halt is a hard boundary, 4H anchored 18:00 ET, DST-safe) -> `MarketContextBuilder` (`context/`; closed bars only + `detections`). `TickCandleBuilder` (ticks -> closed 1M).
- **Detectors (Phase 3, scaffolding only):** `backend/detectors/` — abstract `Detector`; `DetectorInput` refuses unclosed or future candles (central no-lookahead guard); missing data or a crash -> `NOT_AVAILABLE`; `PlaceholderDetector` -> `UNKNOWN`; `default_detectors()` reserves names (htf_bias, liquidity, fvg, ifvg, ce, manipulation, mss, cisd, smt) with NO ICT logic. `MarketContext.detections` is populated by the builder. Note: `backend/concepts/` (older placeholder package) is unused; detectors live in `backend/detectors/` per the Phase 3 directive.
- **Risk:** `RiskEngine` (sole `OrderIntent` issuer; sizing `floor(risk/(pts x $2))`, no costs in the ceiling; tick rounding before sizing; 1 concurrent position incl. unsettled intents; `pause_trading`; MNQ only; `reconcile_fill` re-sizes down / rejects on gaps; `RiskDecision.to_dict()`). `DailyRiskState` locks ONLY on: $2,000 daily loss, remaining budget < one trade allocation, 6 trades/day (lifted only by the paper-only backtest flag). Wins/losses/breakevens are statistics, not locks.
- **Execution:** `ExecutionBroker` + `LIVE_TRADING_ENABLED=False` hard guard; `PaperBroker` (MARKET only, netting incl. flips, realized P&L, order lifecycle, duplicate-ID rejection, `submit_intent` accepts authentic intents only, `open_position_count`); `TradovateBroker` still raises.
- **Brackets + trade lifecycle (Phases 4-5):** on every intent fill `PaperBroker` registers an OCO pair (STOP-MARKET at the stop, LIMIT at the target). `PaperBroker.on_candle(candle)` evaluates them via `management/bar_exit.py`: limit targets fill at the exact price with zero slippage; stops carry the configured slippage (and fill from the open on a gap); a same-bar touch exits at the stop with a `SafetyEvent`. `TradeRecord` goes OPEN -> (PARTIALLY_CLOSED) -> CLOSED with NET `pnl_dollars` and R multiple; closed trades go to `TradeStore`. Entries are refused while a position is open.
- **Orchestrator (Phases 4-5):** `backend/pipeline/orchestrator.py` `PipelineOrchestrator` (`step()`, `run()`, `on_tick()`, `finish()`) + `build_paper_orchestrator()`. Per candle: (a) fill the previous bar's intent at this bar's open (`reconcile_fill` -> `submit_intent`), `settle_intent` in a `finally`; (b) `broker.on_candle` -> closed trades feed `DailyRiskState`, safety events halt the session; (c) strategy -> `RiskEngine.evaluate` -> pending intent. A pending intent whose next candle is not contiguous is cancelled. Every step is logged (`setup`, `risk_decision`, `fill_check`, `order_result`, `trade_opened`, `trade_closed`, `safety_event`, `daily_state`, `intent_cancelled`).
- **Logging:** `DecisionLogger.record(setup)` unchanged; new `record_event(kind, setup_id, at, payload)` + `read_trail()` write the full decision trail into the same NDJSON (`read_all()` still returns only setups). Event time is simulated, never the wall clock.
- **Strategy:** `NyAmHtfContinuationV1` skeleton (entry-window is a real condition; everything else NOT_EVALUATED -> always NO_TRADE). Test double `tests/stubs/trigger_strategy.py::TestTriggerStrategy` (tests only) emits a TRADE with preset prices.

## 3. NOT implemented
Real detector rules (HTF bias, liquidity, FVG/IFVG, CE, manipulation, MSS/CISD, SMT), Strategy V0.1 logic, replay/backtest engine (`BacktestEngine.run` raises; it should wrap the orchestrator), multi-day daily-state rollover, run manifests, live providers, Tradovate (server-side OCO brackets), session-close flattening, dashboard, analytics.

## 4. Verified pipelines
- **Milestone 1 (Phase 1):** `TradeSetup -> RiskEngine -> OrderIntent -> PaperBroker -> Position`.
- **Milestone 2 (Phase 3, `tests/integration/test_e2e_pipeline.py`, 8 tests):** `bars/ticks -> Provider -> Normalizer -> closed 1M -> Aggregator -> MarketContext(+detections) -> TestTriggerStrategy -> RiskEngine (rounding, limits, sizing) -> OrderIntent -> next-bar-open fill check -> PaperBroker.submit_intent -> Position -> bar-by-bar exit -> DailyRiskState -> DecisionLogger trail`. Also verified: lockout stops a signal before any order; off-tick entry with no market price is rejected; gap open re-sizes 15 -> 10 contracts; same-bar SL/TP exits at stop, logs a safety event and halts the session (net loss -$337.20 exceeds the $300 ceiling through costs, accepted per R2); second signal blocked while a position is open; NO_TRADE never becomes an order.
- The e2e test's `Harness` (tests/integration/test_e2e_pipeline.py) is now redundant with the orchestrator; it was kept so the original 182 tests stay intact and can be deleted later.
- **Milestone 3 (Phases 4-5, `tests/pipeline/test_orchestrator.py`):** raw records/ticks -> `PipelineOrchestrator.step()` -> bracket exits -> `TradeStore`, with no test-harness glue. Also verified: target exits are exact-price limit fills with $0.00 slippage while stops include slippage; intent slots are released after a fill+exit, a fill-check rejection, a broker rejection, a broker exception and a stale cancellation, and a later signal then enters; while a position is open a second signal is blocked; a same-bar touch halts the session.

## 5. Findings
1. RiskEngine/OrderIntent were built fresh in Phase 1 (owner-confirmed, L9).
2. `PaperBroker.submit_order` is a low-level ungated method (used for exits/tests); `submit_intent` is the gated entry. A live broker must not expose an ungated entry path.
3. Resolved in Phases 4-5: target exits are limit fills (R6). The old e2e Harness still exits with a MARKET order, which is fine for what it tests.
4. `execution.active_contract` (manual rollover) is stored but consumed by nothing yet.

## 6. Accepted architectural decisions
- Data layer: `MarketDataProvider -> Normalizer -> closed Candle -> Aggregator`; strategies never see a provider; no TradingView scraping in production.
- Decision timing: decide on bar close; entries fill at the NEXT bar's open (`reconcile_fill` re-checks risk at that price). Same-bar SL/TP: stop first + safety event + session halt (paper/backtest only).
- Detectors answer from closed candles only; anything other than DETECTED means "not established" and strategies fail closed.
- Later: run manifests, explicit strategy registry, TradingView as an optional adapter that must pass through RiskEngine, keep `ExecutionBroker` for Tradovate. Rejected: Alpaca/Tradier as broker target, scraping, ML/optimization.

## 7. Active TODOs (ordered)
1. Phase 6: replay engine that wraps `PipelineOrchestrator` (same strategy/risk/execution), multi-day `DailyRiskState` rollover, session-close flatten policy (needs owner rule), run manifests (git SHA, config hash, data hash + range).
2. Point `main.py` at the orchestrator for a paper run once a real strategy exists.
3. Real detectors, one at a time, ONLY after the owner specifies each definition (Phase 7).
4. Tradovate adapter: server-side OCO brackets, `on_candle` becomes a no-op, reconciliation/recovery.
5. Push the commits (the bundle must be pulled on the owner's machine).

## 8. Unresolved decisions (do not invent)
1. **Target selection (P3):** with several targets the FULL quantity is bracketed on the lowest-`priority` target; with none it is stop-only. Partial exits/scaling are a trading rule: confirm or specify.
2. **Stop gaps (P4):** a bar that opens beyond the stop fills from that open plus slippage (conservative). Confirm.
3. **Target gaps (P5):** no price improvement on limit fills; exit fills are stamped at the candle close. Confirm.
4. **Stale intents (P6):** cancelled when the next candle is not contiguous. Confirm.
5. **End of session:** open positions are left open when data ends; there is no flatten-before-close rule yet (SDS has `close_before_session_close_minutes`, unused). Owner rule needed.
6. Same-bar halt latches for the rest of the day; the daily state is single-day (multi-day replay needs rollover).
7. Sizing at fill never increases size on a favorable gap.
8. `evaluate_bar_exit` and brackets are 1M-only.
9. The stub/scripted strategies borrow the `NY_AM_HTF_CONTINUATION_V1` enum name; `TradeSetup.strategy` has no test member.

## 9. Recent changes
- 2026-09-28: audit, reference-repo cross-reference, this file created.
- 2026-09-28 Phase 1 (uncommitted): `InstrumentSpec`, `OrderIntent`, `RiskEngine`, PaperBroker rebuild. 76 tests.
- 2026-09-28 Phase 2 (uncommitted): policy locks L1-L9, market-data pipeline, aggregator, context builder, tick builder, bar-exit policy, `reconcile_fill`. 159 tests.
- 2026-09-29 Phase 3 (committed as the baseline): resolutions R1-R4, legacy counters removed, detector interface + wiring, decision-trail logging, `TestTriggerStrategy`, end-to-end test. 182 tests. Tests that asserted the removed counters were rewritten to assert they no longer lock; no other existing test was weakened.

- 2026-09-29 Phases 4-5 (committed): `PipelineOrchestrator`, PaperBroker brackets + TradeRecord lifecycle, `.gitignore` fix, resolutions R5-R8. 217 tests. No existing test was changed.

## 10. Next recommended slice
Phase 6 replay engine: wrap `PipelineOrchestrator`, add multi-day rollover and run manifests, and get the owner's ruling on end-of-session flattening. Trading definitions (real detectors) still wait for the owner.
