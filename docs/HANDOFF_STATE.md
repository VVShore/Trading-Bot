# HANDOFF_STATE — persistent source of truth for new Claude chats

Last verified: 2026-09-28. Baseline = `main` @ b2b5683 plus the uncommitted Phase 1 work described in section 9. Update this file, not chat history.

## 1. Verified state (audit)
- **Tests:** `pytest -q` -> **76 passed** (32 original + 44 Phase 1). The "139 tests / Strategy V0.1" milestone from the old handoff is NOT in this repo.
- **`python main.py`:** boots config, asserts live guard off, evaluates strategy once on an empty MarketContext -> `NO_TRADE`, writes `data/decision_log.ndjson`. No market data, no orders.
- **~1,950 py LOC, 2 commits, single branch.** README status matches code (foundation only).

## 2. Implemented (real, tested)
- Config: `config/schema.py` (pydantic), `default.yaml`, `loader.py`; `AppConfig.parameter_snapshot()`.
- Domain models: Candle (validated OHLC, `is_closed`), Order/OrderResult, TradeSetup/SetupCondition, TradeRecord, Bias, Liquidity, Concept, Target, MarketStateSnapshot.
- Risk pieces: `calculate_position_size` (floor, rejects if 1 contract > max risk), `DailyRiskState.can_trade()` lockouts, `Account`.
- Execution: `ExecutionBroker` ABC + `LIVE_TRADING_ENABLED=False` hard guard; `PaperBroker` (market fill vs supplied reference price, slippage, commission); `TradovateBroker` raises on construction.
- Strategy: `Strategy` ABC + `NyAmHtfContinuationV1` **skeleton** (all conditions NOT_EVALUATED -> always NO_TRADE); `FvgInversionConfirmation` skeleton.
- `DecisionLogger` / `TradeStore` (NDJSON).
- **Phase 1 (new):** `InstrumentSpec` (+ `backend/config/instruments.py`), `TradeSetup.proposed_entry/targets`, `OrderIntent` (issuer-token protected), `RiskEngine`, `OrderStatus`, rebuilt `PaperBroker` (netting, lifecycle, `submit_intent`, `from_config`).

## 3. NOT implemented (docs/stubs only)
market-data ingestion (`data/`), timeframe aggregation, sessions, liquidity, HTF bias, all `concepts/` detectors, `management/` (stops/targets/trailing), `BacktestEngine.run` (raises NotImplementedError), forward-test engine, Tradovate, dashboard, strategy registry, analytics.

## 4. Current end-to-end path (actual)
- `main.py`: config -> strategy (empty context) -> `NO_TRADE` -> decision log. Unchanged; strategies still emit no TRADE.
- **Milestone 1 works (tested):** `TradeSetup(TRADE) -> RiskEngine.evaluate -> OrderIntent -> PaperBroker.submit_intent(reference_price) -> Position -> opposite order closes -> DailyRiskState.record_trade_result`. Exercised in `tests/integration/test_setup_to_position.py` with hand-built setups; no market data or real strategy feeds it yet.

## 5. Findings / conflicts with intended architecture
1. Handoff-described components (RiskEngine, OrderIntent, Aggregator, V0.1 logic, 128/139 tests) are absent from this checkout. Either another branch/repo holds them or they were never committed. **Unresolved: confirm with owner before rebuilding.**
2. ~~PaperBroker side-netting bug~~ FIXED in Phase 1. Still open: no stop/target exits (Phase 5); MARKET only (other order types are rejected, not faked).
3. Broker does not check `DailyRiskState` by design; gating lives in `RiskEngine`, and the broker only accepts authentic OrderIntents via `submit_intent` (`submit_order` remains a low-level, non-gated method used for exits/tests; live brokers must not expose an ungated entry path).
4. `Candle.is_closed` exists but nothing enforces closed-only consumption yet; `MarketContext` fields default to `None` (typed as list) — fragile.
5. `LIVE_TRADING_ENABLED` guard is sound (import-safe, tested); config flag `live_trading_enabled` is not wired to anything (fine for now).

## 6. Accepted architectural decisions (from external-repo cross-reference)
See table in chat summary; decisions only:
- **Data layer (P0):** `MarketDataProvider -> Normalizer -> Canonical Candle(closed only) -> Aggregator`. Providers are swappable; strategy never sees a provider. Reference pattern: ilcardella `Broker`/interface split. **No TradingView scraping in production.**
- **Backtest fill semantics (P0):** decisions on bar close; entries fill at NEXT bar open; SL/TP checked against bar path with **stop-first on ambiguity** (more conservative than HyperView's open-nearest-extreme heuristic). Same semantics for replay and forward paper.
- **Reproducible runs (P1):** each run writes a manifest (git sha, config snapshot hash, data file hash + range, seed, engine version) and standard artifacts (trades, decisions, metrics). Adapt HyperView's artifact/preset discipline; do NOT adopt Optuna/optimization now.
- **Strategy registry (P1):** explicit `dict[StrategyName, type[Strategy]]` (ilcardella factory idea), no plugin discovery.
- **TradingView (P2, optional):** external signal *adapter* -> normalized `ExternalSignal` -> goes through the same Strategy-gate/RiskEngine as anything else. Auth via secret in header (constant-time compare) + payload schema validation; never route to broker. Do not copy fabston's IP whitelist/body-key approach as-is.
- **Broker interface (P2):** keep `ExecutionBroker`; add order-state lifecycle + reconciliation hooks when Tradovate work starts. Alpaca/Tradier repos = API-client structure reference only.
- **Ops (P2):** Dockerfile + Makefile/CI test target (ilcardella).
- **Rejected/out of scope:** Alpaca/Tradier as broker target, TV-Alpaca-Bot's stock/options logic, scraping, ML/optimization, legacy notebooks.

## 7. Active TODOs (ordered)
1. Resolve finding 5.1 (was RiskEngine/OrderIntent code ever on another branch? Phase 1 was built fresh here).
2. Phase 2: `MarketDataProvider -> Normalizer -> closed Candle -> TimeframeAggregator` with closed-candle/no-partial-HTF tests. Verify which timeframes the strategy needs first (1M/5M/15M/1H/4H likely).
3. Phase 3: MarketContext builder; detectors return UNKNOWN/NOT_AVAILABLE unless the rule is specified.
4. Phase 5/6: bracket exits (stop/target) in PaperBroker, TradeRecord lifecycle, replay engine with next-bar-open fills and run manifests.

## 8. Unresolved decisions (do not invent)
- Exact "DOL + PA = 2/2" definition, manipulation/CE rules, HTF bias rules: see `docs/SDS.md` / `docs/ASSUMPTIONS.md`; unspecified -> strategy must fail closed (NO_TRADE).
- Data vendor/format for 1M MNQ history; session timezone/DST handling; commission/slippage model source.
- Same-bar SL/TP ambiguity policy (proposed: stop-first) — needs owner sign-off before it is treated as final.
- **Flagged by Phase 1 (each changes risk/entry behavior, so NOT decided by engineering):**
  a. Sizing uses the strategy's *proposed entry*; replay will fill at next bar open, so realized risk can differ. Re-size at fill, cap, or accept?
  b. Off-tick entry/stop/target prices are REJECTED (fail closed). Tick-rounding policy (which direction, who rounds) is unspecified; e.g. manipulation CE midpoints can be off-tick.
  c. No cap on concurrent/stacked positions, and entries are not blocked when a position is already open.
  d. Size is not reduced to the remaining daily-loss budget (a full-risk trade can be approved when only a small budget remains before lockout).
  e. Only `execution.active_symbol` may trade; `allow_nq_manual_override` is not wired (NQ rejected unless it is the active symbol).
  f. Daily-state day check uses the calendar date in `session.timezone`; futures trading-day rollover (18:00 ET) is not modelled.
  g. Entry-window (`entry_start/entry_end`) and news lockout are NOT enforced by RiskEngine (treated as strategy conditions).

## 9. Recent changes
- 2026-09-28: audit + cross-reference; this file created.
- 2026-09-28 (Phase 1, uncommitted): added `InstrumentSpec`, `OrderIntent`, `RiskEngine`, `OrderStatus`; extended `TradeSetup` (`proposed_entry`, `targets`) and `OrderResult` (`realized_pnl`); rebuilt `PaperBroker`; added `tzdata` to requirements. 44 new tests (76 total).
  - RiskEngine is the only issuer of `OrderIntent` (token + AST tests: only `risk/engine.py` may reference `issue_order_intent`; strategy modules may not import execution/risk).
  - Existing code paths preserved; all 32 original tests pass unmodified.

## 10. Next recommended slice
Phase 2: market-data foundation (provider -> normalizer -> closed candles -> aggregator). Confirm needed timeframes from the SDS first.
