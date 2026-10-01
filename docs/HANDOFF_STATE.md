# HANDOFF_STATE — persistent source of truth for new Claude chats

Last verified: 2026-09-30, after Phase 6. Git: `b2b5683` (original) -> `feat: complete phases 1-3 baseline with 182 tests` -> `feat: pipeline orchestrator and bracket exits` -> `feat: phase 6 backtest engine, session flatten, day rollover, manifests`. Commits exist only in the sandbox clone (delivered as a git bundle); nothing has been pushed to GitHub. Update this file, not chat history.
Owner rules live in `docs/ASSUMPTIONS.md` ("Policy locks" L1-L9, "Owner resolutions" R1-R12, provisional P1-P6); this file only summarizes them.

## 1. Verified state
- **Tests:** `pytest -q` -> **246 passed** (32 original -> 76 -> 159 -> 182 -> 217 -> 246: +29 in Phase 6). A fresh `git clone` of the final commit also passes.
- **`python main.py`** still boots and ends at `NO_TRADE` (main.py does not use the orchestrator yet).
- Mutation-checked in Phase 6: flatten one bar late, no day rollover, removing the stale/after-flatten checks, target picked by priority only, constant config hash, and a rollover that keeps the lockout each make tests fail.

## 2. Implemented (real, tested)
- **Config/domain:** `AppConfig` with policy-lock validators (risk/trade $200-400, daily loss $2,000-2,500, timezone locked to America/New_York, backtest trade-cap override only in paper); `InstrumentSpec` with exact-decimal `round_entry/round_stop/round_target`; `Candle` (finite, volume >= 0, tz-aware); `TradeSetup` (+ `proposed_entry`, `targets`); `OrderIntent` (issuer-token protected); `OrderStatus`.
- **Market data:** `MarketDataProvider` (`CsvBarProvider`, `InMemoryProvider`) -> `BarNormalizer` (NY time, explicit open/close stamping, closed flag) -> `MarketDataPipeline` (drops developing + halt rows) -> `TimeframeAggregator` (`market/candles/`; 1M -> 5M/15M/1H/4H, developing bar isolated, 17:00-18:00 ET halt is a hard boundary, 4H anchored 18:00 ET, DST-safe) -> `MarketContextBuilder` (`context/`; closed bars only + `detections`). `TickCandleBuilder` (ticks -> closed 1M).
- **Detectors (Phase 3, scaffolding only):** `backend/detectors/` — abstract `Detector`; `DetectorInput` refuses unclosed or future candles (central no-lookahead guard); missing data or a crash -> `NOT_AVAILABLE`; `PlaceholderDetector` -> `UNKNOWN`; `default_detectors()` reserves names (htf_bias, liquidity, fvg, ifvg, ce, manipulation, mss, cisd, smt) with NO ICT logic. `MarketContext.detections` is populated by the builder. Note: `backend/concepts/` (older placeholder package) is unused; detectors live in `backend/detectors/` per the Phase 3 directive.
- **Risk:** `RiskEngine` (sole `OrderIntent` issuer; sizing `floor(risk/(pts x $2))`, no costs in the ceiling; tick rounding before sizing; 1 concurrent position incl. unsettled intents; `pause_trading`; MNQ only; `reconcile_fill` re-sizes down / rejects on gaps; `RiskDecision.to_dict()`). `DailyRiskState` locks ONLY on: $2,000 daily loss, remaining budget < one trade allocation, 6 trades/day (lifted only by the paper-only backtest flag). Wins/losses/breakevens are statistics, not locks.
- **Execution:** `ExecutionBroker` + `LIVE_TRADING_ENABLED=False` hard guard; `PaperBroker` (MARKET only, netting incl. flips, realized P&L, order lifecycle, duplicate-ID rejection, `submit_intent` accepts authentic intents only, `open_position_count`); `TradovateBroker` still raises.
- **Brackets + trade lifecycle (Phases 4-5):** on every intent fill `PaperBroker` registers an OCO pair (STOP-MARKET at the stop, LIMIT at the target). `PaperBroker.on_candle(candle)` evaluates them via `management/bar_exit.py`: limit targets fill at the exact price with zero slippage; stops carry the configured slippage (and fill from the open on a gap); a same-bar touch exits at the stop with a `SafetyEvent`. `TradeRecord` goes OPEN -> (PARTIALLY_CLOSED) -> CLOSED with NET `pnl_dollars` and R multiple; closed trades go to `TradeStore`. Entries are refused while a position is open.
- **Orchestrator (Phases 4-5):** `backend/pipeline/orchestrator.py` `PipelineOrchestrator` (`step()`, `run()`, `on_tick()`, `finish()`) + `build_paper_orchestrator()`. Per candle: (a) fill the previous bar's intent at this bar's open (`reconcile_fill` -> `submit_intent`), `settle_intent` in a `finally`; (b) `broker.on_candle` -> closed trades feed `DailyRiskState`, safety events halt the session; (c) strategy -> `RiskEngine.evaluate` -> pending intent. A pending intent whose next candle is not contiguous is cancelled. Every step is logged (`setup`, `risk_decision`, `fill_check`, `order_result`, `trade_opened`, `trade_closed`, `safety_event`, `daily_state`, `intent_cancelled`).
- **Phase 6:** `backend/engine/backtest.py` `BacktestEngine.run(BacktestConfig)` wraps the production `PipelineOrchestrator` (no separate backtest logic; a test proves engine output == hand-wired orchestrator output). Artifacts per run: `decision_log.ndjson`, `trades.ndjson`, `manifest.json` (git SHA + dirty flag, config SHA-256, per-file and combined data SHA-256, data format, first/last candle and trading day, row/candle counters, engine version, strategy name/version, result summary incl. `open_positions_at_end`, artifact hashes). Deterministic: no wall-clock time or absolute paths. Refuses to overwrite an earlier run. `backend/backtest/engine.py` re-exports it.
- **R12 flatten:** orchestrator step a2 closes any open position with a MARKET order (`PaperBroker.flatten_position`, reason `session_flatten`) at the open of the first candle at/after `session.flatten_time` (14:57 ET), or at the first candle of a new day if a position survived overnight; intents cannot fill at/after that time.
- **Multi-day rollover:** the first candle of a new New York date calls `DailyRiskState.start_new_day()` (resets counters, loss lockout and safety halt in place), logs `day_rollover`, and appends to `orchestrator.day_summaries()`.
- **Targets (R9):** `management/targets.py` picks the bracket target by highest `Target.confidence`, then nearest entry, then lowest priority; full quantity, no partial exits.
- **Logging:** `DecisionLogger.record(setup)` unchanged; new `record_event(kind, setup_id, at, payload)` + `read_trail()` write the full decision trail into the same NDJSON (`read_all()` still returns only setups). Event time is simulated, never the wall clock.
- **Strategy:** `NyAmHtfContinuationV1` skeleton (entry-window is a real condition; everything else NOT_EVALUATED -> always NO_TRADE). Test double `tests/stubs/trigger_strategy.py::TestTriggerStrategy` (tests only) emits a TRADE with preset prices.

## 3. NOT implemented
Real detector rules (HTF bias, liquidity, FVG/IFVG, CE, manipulation, MSS/CISD, SMT), Strategy V0.1 logic, live providers (Tradovate WebSocket/REST), Tradovate adapter (server-side OCO brackets, reconciliation), data-range filtering in the engine, analytics/reporting beyond the manifest summary, dashboard.

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
1. Real detectors, one at a time, ONLY after the owner specifies each definition (Phase 7); then a real strategy and a replay on real 1M data.
2. Point `main.py` at the orchestrator / BacktestEngine for a paper run once a real strategy exists.
3. Tradovate adapter: server-side OCO brackets (`on_candle` becomes a no-op), reconciliation and disconnect recovery, live market-data provider.
4. Push the commits (the bundle must be pulled on the owner's machine).

## 8. Unresolved decisions (do not invent)
1. **R9 interpretation:** "highest likelihood of being reached" is implemented as the highest `Target.confidence` (default 0.5), ties -> nearest to entry, then lowest priority. Confirm `confidence` is the intended likelihood field (if targets all keep the default 0.5 the nearest target is chosen).
2. **Trading-day boundary (P4):** calendar New York date, not the 18:00 ET CME roll. Evening candles (18:00-23:59) belong to that calendar date.
3. **Flatten details (P3):** exits fill at the bar open with normal slippage; entries cannot fill at/after 14:57; overnight survivors flatten at the next day's first candle.
4. **End of data (P5):** open positions are reported, not closed.
5. Same-bar halt latches until the next day's rollover; brackets/same-bar rule are 1M-only.
6. Sizing at fill never increases size on a favorable gap.
7. The stub/scripted strategies borrow the `NY_AM_HTF_CONTINUATION_V1` enum name; `TradeSetup.strategy` has no test member.
8. `session.session_close` and `close_before_session_close_minutes` are legacy/unused; `flatten_time` is the enforced rule.

## 9. Recent changes
- 2026-09-28: audit, reference-repo cross-reference, this file created.
- 2026-09-28 Phase 1 (uncommitted): `InstrumentSpec`, `OrderIntent`, `RiskEngine`, PaperBroker rebuild. 76 tests.
- 2026-09-28 Phase 2 (uncommitted): policy locks L1-L9, market-data pipeline, aggregator, context builder, tick builder, bar-exit policy, `reconcile_fill`. 159 tests.
- 2026-09-29 Phase 3 (committed as the baseline): resolutions R1-R4, legacy counters removed, detector interface + wiring, decision-trail logging, `TestTriggerStrategy`, end-to-end test. 182 tests. Tests that asserted the removed counters were rewritten to assert they no longer lock; no other existing test was weakened.

- 2026-09-29 Phases 4-5 (committed): `PipelineOrchestrator`, PaperBroker brackets + TradeRecord lifecycle, `.gitignore` fix, resolutions R5-R8. 217 tests. No existing test was changed.

- 2026-09-30 Phase 6 (committed): `BacktestEngine`, session flatten, day rollover, manifests, target selection per R9, resolutions R9-R12. 246 tests; the only existing tests touched were the one that encoded the old priority-based target rule.

## 10. Next recommended slice
Phase 7 needs the owner's trading definitions (one detector at a time: HTF bias first). Engineering work that does not depend on them: a CLI/entry point that runs `BacktestEngine` on a CSV path, and a Tradovate provider/adapter skeleton behind the existing interfaces.
