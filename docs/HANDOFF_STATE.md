# HANDOFF_STATE — persistent source of truth for new Claude chats

Last verified: 2026-09-29, after Phase 3. Baseline = `main` @ b2b5683 plus UNCOMMITTED Phase 1-3 work (nothing has been committed or pushed from the sandbox). Update this file, not chat history.
Owner rules live in `docs/ASSUMPTIONS.md` ("Policy locks" L1-L9, "Owner resolutions" R1-R4); this file only summarizes them.

## 1. Verified state
- **Tests:** `pytest -q` -> **182 passed** (32 original -> 76 Phase 1 -> 159 Phase 2 -> 182 Phase 3: +4 net from the counter cleanup, +11 detectors, +8 end-to-end).
- **`python main.py`** still boots and ends at `NO_TRADE` (the real strategy has no detectors yet).
- Mutation-checked in Phase 3: letting a detector see a developing/future candle, skipping the concurrency cap, skipping stop rounding, and skipping the fill-time risk check each make tests fail.

## 2. Implemented (real, tested)
- **Config/domain:** `AppConfig` with policy-lock validators (risk/trade $200-400, daily loss $2,000-2,500, timezone locked to America/New_York, backtest trade-cap override only in paper); `InstrumentSpec` with exact-decimal `round_entry/round_stop/round_target`; `Candle` (finite, volume >= 0, tz-aware); `TradeSetup` (+ `proposed_entry`, `targets`); `OrderIntent` (issuer-token protected); `OrderStatus`.
- **Market data:** `MarketDataProvider` (`CsvBarProvider`, `InMemoryProvider`) -> `BarNormalizer` (NY time, explicit open/close stamping, closed flag) -> `MarketDataPipeline` (drops developing + halt rows) -> `TimeframeAggregator` (`market/candles/`; 1M -> 5M/15M/1H/4H, developing bar isolated, 17:00-18:00 ET halt is a hard boundary, 4H anchored 18:00 ET, DST-safe) -> `MarketContextBuilder` (`context/`; closed bars only + `detections`). `TickCandleBuilder` (ticks -> closed 1M).
- **Detectors (Phase 3, scaffolding only):** `backend/detectors/` — abstract `Detector`; `DetectorInput` refuses unclosed or future candles (central no-lookahead guard); missing data or a crash -> `NOT_AVAILABLE`; `PlaceholderDetector` -> `UNKNOWN`; `default_detectors()` reserves names (htf_bias, liquidity, fvg, ifvg, ce, manipulation, mss, cisd, smt) with NO ICT logic. `MarketContext.detections` is populated by the builder. Note: `backend/concepts/` (older placeholder package) is unused; detectors live in `backend/detectors/` per the Phase 3 directive.
- **Risk:** `RiskEngine` (sole `OrderIntent` issuer; sizing `floor(risk/(pts x $2))`, no costs in the ceiling; tick rounding before sizing; 1 concurrent position incl. unsettled intents; `pause_trading`; MNQ only; `reconcile_fill` re-sizes down / rejects on gaps; `RiskDecision.to_dict()`). `DailyRiskState` locks ONLY on: $2,000 daily loss, remaining budget < one trade allocation, 6 trades/day (lifted only by the paper-only backtest flag). Wins/losses/breakevens are statistics, not locks.
- **Execution:** `ExecutionBroker` + `LIVE_TRADING_ENABLED=False` hard guard; `PaperBroker` (MARKET only, netting incl. flips, realized P&L, order lifecycle, duplicate-ID rejection, `submit_intent` accepts authentic intents only, `open_position_count`); `TradovateBroker` still raises.
- **Management (partial):** `management/bar_exit.py` same-bar SL/TP policy (stop first, `SafetyEvent`, session halt).
- **Logging:** `DecisionLogger.record(setup)` unchanged; new `record_event(kind, setup_id, at, payload)` + `read_trail()` write the full decision trail into the same NDJSON (`read_all()` still returns only setups). Event time is simulated, never the wall clock.
- **Strategy:** `NyAmHtfContinuationV1` skeleton (entry-window is a real condition; everything else NOT_EVALUATED -> always NO_TRADE). Test double `tests/stubs/trigger_strategy.py::TestTriggerStrategy` (tests only) emits a TRADE with preset prices.

## 3. NOT implemented
Real detector rules (HTF bias, liquidity, FVG/IFVG, CE, manipulation, MSS/CISD, SMT), Strategy V0.1 logic, PaperBroker bracket exits (stop/target orders), `TradeRecord` lifecycle, a production orchestrator, replay/backtest engine (`BacktestEngine.run` raises), run manifests, live providers, Tradovate, dashboard, analytics.

## 4. Verified pipelines
- **Milestone 1 (Phase 1):** `TradeSetup -> RiskEngine -> OrderIntent -> PaperBroker -> Position`.
- **Milestone 2 (Phase 3, `tests/integration/test_e2e_pipeline.py`, 8 tests):** `bars/ticks -> Provider -> Normalizer -> closed 1M -> Aggregator -> MarketContext(+detections) -> TestTriggerStrategy -> RiskEngine (rounding, limits, sizing) -> OrderIntent -> next-bar-open fill check -> PaperBroker.submit_intent -> Position -> bar-by-bar exit -> DailyRiskState -> DecisionLogger trail`. Also verified: lockout stops a signal before any order; off-tick entry with no market price is rejected; gap open re-sizes 15 -> 10 contracts; same-bar SL/TP exits at stop, logs a safety event and halts the session (net loss -$337.20 exceeds the $300 ceiling through costs, accepted per R2); second signal blocked while a position is open; NO_TRADE never becomes an order.
- **Caveat:** the loop that stitches these steps lives inside the test (`Harness`). It is not yet a production module.

## 5. Findings
1. RiskEngine/OrderIntent were built fresh in Phase 1 (owner-confirmed, L9).
2. `PaperBroker.submit_order` is a low-level ungated method (used for exits/tests); `submit_intent` is the gated entry. A live broker must not expose an ungated entry path.
3. The harness exits at the target price using a MARKET order, so PaperBroker adds slippage to the exit; real target exits would be limit fills. Decide when bracket orders are built (Phase 5).
4. `execution.active_contract` (manual rollover) is stored but consumed by nothing yet.

## 6. Accepted architectural decisions
- Data layer: `MarketDataProvider -> Normalizer -> closed Candle -> Aggregator`; strategies never see a provider; no TradingView scraping in production.
- Decision timing: decide on bar close; entries fill at the NEXT bar's open (`reconcile_fill` re-checks risk at that price). Same-bar SL/TP: stop first + safety event + session halt (paper/backtest only).
- Detectors answer from closed candles only; anything other than DETECTED means "not established" and strategies fail closed.
- Later: run manifests, explicit strategy registry, TradingView as an optional adapter that must pass through RiskEngine, keep `ExecutionBroker` for Tradovate. Rejected: Alpaca/Tradier as broker target, scraping, ML/optimization.

## 7. Active TODOs (ordered)
1. Promote the harness loop into a production orchestrator (`backend/pipeline/`), then reuse it for replay.
2. Phase 5: PaperBroker bracket exits using `bar_exit`, `TradeRecord` lifecycle, safety events to the log by default, `settle_intent` after every fill/reject.
3. Phase 6: replay engine on the same pipeline + run manifests.
4. Real detectors, one at a time, ONLY after the owner specifies each definition.
5. Commit and push the Phase 1-3 work.

## 8. Unresolved decisions (do not invent)
1. **Confirm R1 scope:** besides the three listed counters I also removed `allow_be_trades` / the breakeven-allowance lockout so limits rely strictly on the policy locks. Say so if you want it kept.
2. Same-bar halt latches for the rest of the day (no day-rollover reset yet) and applies to 1M candles only.
3. Sizing at fill never increases size on a favorable gap.
4. `settle_intent` must be called by the orchestrator after a fill/rejection or the slot stays blocked (fails closed).
5. Tick candles close only on a later-minute tick; HTF bars close on the next bucket when minutes are missing.
6. Aggregator lives in `market/candles/`; detectors in `detectors/` (the older `concepts/` package is unused).
7. The stub strategy borrows the `NY_AM_HTF_CONTINUATION_V1` enum name because `TradeSetup.strategy` has no test member; it is told apart by `strategy_version="TEST-TRIGGER-0.0.0"`.

## 9. Recent changes
- 2026-09-28: audit, reference-repo cross-reference, this file created.
- 2026-09-28 Phase 1 (uncommitted): `InstrumentSpec`, `OrderIntent`, `RiskEngine`, PaperBroker rebuild. 76 tests.
- 2026-09-28 Phase 2 (uncommitted): policy locks L1-L9, market-data pipeline, aggregator, context builder, tick builder, bar-exit policy, `reconcile_fill`. 159 tests.
- 2026-09-29 Phase 3 (uncommitted): resolutions R1-R4, legacy counters removed, detector interface + wiring, decision-trail logging, `TestTriggerStrategy`, end-to-end test. 182 tests. Tests that asserted the removed counters were rewritten to assert they no longer lock; no other existing test was weakened.

## 10. Next recommended slice
Production orchestrator + PaperBroker bracket exits (TODO 1-2) so a full trade runs without test-only glue, then the replay engine. Trading definitions (real detectors) wait for the owner.
