# Changelog

All notable changes to this project are recorded here. Provisional-rule
replacements (see `docs/ASSUMPTIONS.md`) must always get an entry.

## [Unreleased] - Phase 1: execution foundation

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
