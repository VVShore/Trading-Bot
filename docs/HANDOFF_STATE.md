# HANDOFF_STATE — persistent source of truth for new Claude chats

Last verified: 2026-09-28, after Phase 2. Baseline = `main` @ b2b5683 plus UNCOMMITTED Phase 1 + Phase 2 work (sections 2, 9). Update this file, not chat history.
Owner policy locks (decisions 1-9) live in `docs/ASSUMPTIONS.md` ("Policy locks"); this file only summarizes them.

## 1. Verified state
- **Tests:** `pytest -q` -> **159 passed** (32 original -> 76 after Phase 1 -> 159 after Phase 2). The old "139 tests / Strategy V0.1" milestone is NOT in this repo.
- **`python main.py`** still boots: config -> live guard off -> strategy on an empty context -> `NO_TRADE` -> decision log. No market data or orders.
- Mutation-checked: publishing a bar early, removing halt truncation, leaking the developing bar into context, and flipping stop rounding each make the suite fail.

## 2. Implemented (real, tested)
- **Config/domain:** pydantic `AppConfig` with policy-lock validators (risk/trade $200-400, daily loss $2,000-2,500, timezone locked to America/New_York, backtest trade-cap override only in paper); `InstrumentSpec` (+ exact-decimal `round_entry/round_stop/round_target`); `Candle` (rejects negative volume, non-finite, naive times); `TradeSetup` (+ `proposed_entry`, `targets`); `OrderIntent`; `OrderStatus`.
- **Risk:** `RiskEngine` (sole `OrderIntent` issuer; sizing `floor(risk/(pts x $2))`; tick rounding before sizing; 1 concurrent position incl. unsettled intents; `pause_trading`; MNQ-only; `reconcile_fill` re-sizes down/rejects on gaps), `DailyRiskState` (loss limit, "remaining budget can't cover a trade" lockout, `halt_session`, backtest trade-cap override), `position_sizing`.
- **Execution:** `ExecutionBroker` + `LIVE_TRADING_ENABLED=False` hard guard; `PaperBroker` (MARKET only, netting incl. flips, realized P&L, order lifecycle, duplicate-ID rejection, `submit_intent` accepts authentic intents only, `open_position_count`); `TradovateBroker` still raises.
- **Market data (Phase 2):** `MarketDataProvider` (+ `CsvBarProvider`, `InMemoryProvider`) -> `BarNormalizer` (NY time, explicit open/close stamping, closed flag) -> closed-candle gate -> `TimeframeAggregator` (`market/candles/aggregator.py`; 1M -> 5M/15M/1H/4H, developing bar isolated, 17:00-18:00 ET halt is a hard boundary, DST-safe) -> `MarketContextBuilder` (`context/builder.py`; closed bars only, `as_of` = latest known close). `TickCandleBuilder`, `MarketDataPipeline` (drops developing and halt rows, counts them).
- **Management (partial):** `management/bar_exit.py` same-bar SL/TP policy (stop first, `SafetyEvent`, session halt).
- **Strategy:** `NyAmHtfContinuationV1` skeleton; `within_execution_window` is now a real PASS/FAIL condition (outside window -> NO_TRADE); all detector conditions still NOT_EVALUATED, so it always returns NO_TRADE.
- `DecisionLogger` / `TradeStore` (NDJSON).

## 3. NOT implemented
Detectors (HTF bias, liquidity, FVG/IFVG, CE, manipulation, MSS/CISD, SMT), Strategy V0.1 logic, stop/target bracket exits in PaperBroker, trade lifecycle -> `TradeRecord`, replay/backtest engine (`BacktestEngine.run` raises), run manifests, live/WebSocket/REST providers, Tradovate, dashboard, analytics, strategy registry.

## 4. Verified pipelines
- **Milestone 1 (Phase 1):** `TradeSetup -> RiskEngine -> OrderIntent -> reconcile_fill -> PaperBroker.submit_intent -> Position -> opposite order closes -> DailyRiskState`. `tests/integration/test_setup_to_position.py`.
- **Milestone 2a (Phase 2):** `CSV/ticks -> Normalizer -> closed 1M -> Aggregator -> MarketContext -> Strategy.evaluate`. `tests/integration/test_market_data_to_context.py`. At 09:35 the context holds the closed 08:00-09:00 hour and never the developing 09:00-10:00 bar.
- **Not yet joined:** the strategy cannot emit a TRADE, so market data does not yet reach RiskEngine/PaperBroker end to end (needs Phase 3-4 detectors, or a stub strategy in a test).

## 5. Findings
1. RiskEngine/OrderIntent were built fresh in Phase 1 (owner-confirmed, lock L9).
2. Existing daily counters `max_losses=2`, `max_wins_per_day=3`, `max_unprofitable_trades_per_day=3`, `allow_be_trades=1` are still active. They lock the day long before the locked limits ($2,000 loss, 6 trades) can be reached. See section 8, item 1.
3. `PaperBroker.submit_order` is a low-level ungated method (used for exits/tests); `submit_intent` is the gated entry. A live broker must not expose an ungated entry path.
4. `execution.active_contract` (manual rollover) is stored but consumed by nothing yet.

## 6. Accepted architectural decisions
- Data layer: `MarketDataProvider -> Normalizer -> closed Candle -> Aggregator`; strategies never see a provider; no TradingView scraping in production.
- Decision timing: decide on bar close; entries fill at next bar open (`reconcile_fill` re-checks risk at that price). Same-bar SL/TP: stop first + safety event + session halt (paper/backtest only).
- Reproducible runs (P1, later): run manifests (git SHA, config hash, data hash + range, strategy/engine version).
- Strategy registry: explicit dict, no plugin discovery. TradingView (optional, later): external-signal adapter that must pass through RiskEngine. Broker interface: keep `ExecutionBroker`; add lifecycle/reconciliation with Tradovate.
- Rejected: Alpaca/Tradier as broker target, scraping, ML/optimization, notebooks.

## 7. Active TODOs (ordered)
1. Answer the open decisions in section 8 (1, 2, 4 change trading behavior).
2. Phase 3: context detectors, each returning UNKNOWN/NOT_AVAILABLE unless its rule is specified.
3. Phase 5: PaperBroker bracket exits (use `bar_exit`), `TradeRecord` lifecycle, write `SafetyEvent`s to the decision log, call `settle_intent` after fills.
4. Phase 6: replay engine on the same pipeline + manifests.
5. Commit the uncommitted Phase 1-2 work (nothing has been committed or pushed from this sandbox).

## 8. Unresolved decisions (do not invent)
1. **Daily counters vs the new limits:** with `max_losses=2` the day ends at about -$600, so the $2,000 loss budget and the 6-trade cap are unreachable; `max_wins_per_day=3` also caps trades. Keep, raise, or remove these?
2. **4H alignment:** SDS has no 4H timeframe; 18:00 ET anchor is provisional (`session.four_hour_anchor`). Affects any future 4H bias.
3. **Entry rounding reading:** "toward market" implemented as the neighbouring tick on the market's side (not nearest); off-tick entries need `market_price` passed to `evaluate()`.
4. **Costs vs ceiling:** lock L1's formula ignores commission/slippage, so loss at the stop can exceed the $ ceiling by roughly the per-contract costs (about $0.74 + 0.25 pt x $2 per contract). Confirm or add costs.
5. Same-bar halt latches for the rest of the day (there is no day-rollover reset yet) and applies to 1M candles only.
6. Sizing at fill never increases size on a favorable gap.
7. `settle_intent` must be called by the orchestrator after a fill/rejection or the slot stays blocked (fails closed).
8. Tick candles close only on a later-minute tick; HTF bars close on the next bucket when minutes are missing.
9. Aggregator lives in `market/candles/` (documented architecture location), not `data/`.

## 9. Recent changes
- 2026-09-28: audit, reference-repo cross-reference, this file created.
- 2026-09-28 Phase 1 (uncommitted): `InstrumentSpec`, `OrderIntent`, `RiskEngine`, PaperBroker rebuild. 76 tests.
- 2026-09-28 Phase 2 (uncommitted): policy locks 1-9 in config/code/docs, market-data pipeline, aggregator, context builder, tick builder, bar-exit policy, `reconcile_fill`, candle hardening. 159 tests. Two stale-default tests and one Phase 1 test threshold were updated to the locked values; no other existing test was edited.

## 10. Next recommended slice
Phase 3 skeleton: a `Detector` interface returning `UNKNOWN/NOT_AVAILABLE`, wired into `MarketContext`, plus a throwaway test strategy that emits a TRADE from the real context so market data -> RiskEngine -> PaperBroker runs end to end. Do not implement any trading definitions until the owner specifies them.
