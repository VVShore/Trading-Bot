# Changelog

All notable changes to this project are recorded here. Provisional-rule
replacements (see `docs/ASSUMPTIONS.md`) must always get an entry.

## [Unreleased] - Phase 3: detector scaffolding + first end-to-end run

### Added
- `backend/detectors/`: abstract `Detector` (`DetectorInput` refuses unclosed/future candles; missing data or errors -> NOT_AVAILABLE), `DetectorStatus`, `DetectorResult`, `PlaceholderDetector` (UNKNOWN), `default_detectors()` (names only, no ICT logic). Wired into `MarketContextBuilder(detectors=...)` -> `MarketContext.detections`.
- `DecisionLogger.record_event / read_trail`: full decision trail in the same NDJSON file (setup lines unchanged; `read_all` still returns only setups). `RiskDecision.to_dict()`.
- `tests/stubs/trigger_strategy.py` (`TestTriggerStrategy`) and `tests/integration/test_e2e_pipeline.py` (data -> context -> strategy -> risk -> intent -> broker -> position -> exit -> daily state -> log).

### Changed
- Owner resolutions R1-R4 (docs/ASSUMPTIONS.md): legacy daily counters removed (`max_losses`, `max_wins_per_day`, `max_unprofitable_trades_per_day`, and `allow_be_trades`); sizing purity, 18:00 ET 4H anchor and toward-market rounding confirmed.
- `DailyRiskState` locks only on: max daily loss, remaining budget < one trade, max trades/day.
- Tests asserting the removed counters were rewritten to assert they no longer lock trading.

## [Phase 2] - Market-data foundation + policy locks

### Added
- Data pipeline: `MarketDataProvider` (+ `CsvBarProvider`, `InMemoryProvider`), `BarNormalizer` (timestamps -> America/New_York, explicit open/close stamping, closed flag), `TickCandleBuilder`, `MarketDataPipeline`.
- `TimeframeAggregator` (`backend/market/candles/aggregator.py`): 1M -> 5M/15M/1H/4H from closed candles only, developing bar kept separate, 17:00-18:00 ET halt is a hard boundary, DST-safe.
- `MarketContextBuilder` (`backend/context/builder.py`): closed bars only, `as_of` = latest known close.
- `backend/market/sessions/clock.py`, `backend/management/bar_exit.py` (same-bar SL/TP policy).
- `RiskEngine.reconcile_fill` (re-size down or reject on gap), `settle_intent`; `DailyRiskState.halt_session` / `remaining_loss_budget`.
- `InstrumentSpec.round_entry/round_stop/round_target`.
- Config: `max_concurrent_positions`, `pause_trading`, `backtest_override_trade_frequency`, `execution.active_contract`, session halt/4H-anchor settings.
- `docs/ASSUMPTIONS.md`: "Policy locks" (owner decisions 1-9) and provisional rules P1-P3.

### Changed
- Policy locks: sizing is now `floor(risk / (pts x $2))` (commission/slippage no longer in the ceiling); `max_daily_loss` 1200 -> 2000; `max_trades_per_day` 5 -> 6; off-tick prices are rounded, not rejected; timezone locked to America/New_York.
- `RiskEngine(config, daily_state, open_position_count)` (new required argument); `evaluate(setup, market_price=None)`.
- `Candle` now rejects negative volume, non-finite values and naive timestamps.
- `MarketContext` list/dict fields default to empty instead of `None`.
- `NyAmHtfContinuationV1`: `within_execution_window` is a real PASS/FAIL condition.
- Two tests asserting the old defaults ($1,200 / 5 trades) were updated to the locked values; one Phase 1 test threshold changed accordingly.

## [Phase 1] - Execution foundation

### Added
- `InstrumentSpec` + `backend/config/instruments.py` resolver (MNQ $2/pt, 0.25 tick; ES analysis-only).
- `TradeSetup.proposed_entry` and `TradeSetup.targets`.
- `RiskEngine` (`backend/risk/engine.py`): TradeSetup -> rejection | `OrderIntent`; reuses `DailyRiskState`, `resolve_risk_dollars`, `calculate_position_size`.
- `OrderIntent` (issuer-token protected) and `OrderStatus` enum.
- `PaperBroker.submit_intent`, `PaperBroker.from_config`, order lifecycle records.
- 44 tests, including static bypass guards and the TradeSetup -> Position vertical slice.

### Changed
- `PaperBroker`: opposite-side orders now reduce/close/flip positions with realized P&L; same-side adds use volume-weighted entry; non-MARKET orders and duplicate order IDs are rejected.
- `OrderResult.realized_pnl` added (default 0.0).
- `requirements.txt`: added `tzdata` (needed by `zoneinfo` on Windows).

## [0.1.0] - Initial foundation (Step 1)

### Added
- Repository structure per `docs/ARCHITECTURE.md` (backend, dashboard, tests, docs).
- Configuration system: `backend/config/schema.py` (pydantic `AppConfig`), `default.yaml`, `loader.py` with override-file support.
- Domain models: `Candle`, `LiquidityObject`, `HTFBiasResult`, `MarketStateSnapshot`, `ConceptObject`, `TradeSetup`/`SetupCondition`, `Target`, `TradeRecord`, `Order`/`OrderResult`.
- Shared enums covering timeframes, bias, market state, liquidity, concepts, strategy names, trade/order status.
- `Strategy` interface (`backend/strategies/base.py`) and skeletons for `NyAmHtfContinuationV1` and `FvgInversionConfirmation` (detectors not yet implemented).
- `ExecutionBroker` interface with hard `LIVE_TRADING_ENABLED` guard, working `PaperBroker`, and a disabled `TradovateBroker` structural placeholder.
- Fully implemented, tested `position_sizing.py` and `daily_limits.py` risk modules.
- `DecisionLogger` and `TradeStore` (file-backed, newline-delimited JSON).
- `BacktestEngine` skeleton that fails loudly (`NotImplementedError`) rather than faking results.
- Minimal bootstrap application (`main.py`) proving config → strategy → decision-log wiring works end to end.
- Test suite (32 tests) covering config validation, candle model invariants, position sizing, daily risk limits, strategy skeleton behavior, and execution guards.
- Documentation set: `README.md`, `docs/SDS.md`, `docs/GLOSSARY.md`, `docs/ASSUMPTIONS.md`, `docs/ARCHITECTURE.md`, `docs/CHANGELOG.md`, `docs/TESTING.md`, `docs/BACKTESTING.md`.

### Not yet implemented
See `docs/ARCHITECTURE.md` "Development sequence" — Steps 2-6, 8, 10-12.
