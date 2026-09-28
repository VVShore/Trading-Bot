# Architecture

## Module responsibilities

### `backend/core`
Domain models (`models/`) and enums (`enums/`) only. No business logic, no
I/O. Every other package depends on `core`; `core` depends on nothing else
in `backend`.

- `models/candle.py` — `Candle` (OHLCV + derived properties: body, range, wicks, midpoint).
- `models/liquidity.py` — `LiquidityObject` (PDH/PDL, equal highs/lows, session levels, etc.).
- `models/bias.py` — `HTFBiasResult`.
- `models/market_state.py` — `MarketStateSnapshot` (state **per timeframe**, never a single global state).
- `models/concept.py` — `ConceptObject`, the shared shape for FVG/iFVG/OB/breaker/OTE/CE/BPR/volume-imbalance outputs.
- `models/setup.py` — `TradeSetup`, `SetupCondition` (decision-log record).
- `models/target.py` — `Target` (TP1..TPn as data, not hard-coded logic).
- `models/trade.py` — `TradeRecord`, `ExitFill`, `TrailingEvent`.
- `models/order.py` — `Order`, `OrderResult` (execution-layer types).
- `enums/enums.py` — all shared enums (timeframes, bias, market state, liquidity types, etc.).

### `backend/config`
- `schema.py` — `AppConfig` (pydantic), the single source of truth for every tunable parameter.
- `default.yaml` — default values, annotated with which are PROVISIONAL (cross-referenced in `docs/ASSUMPTIONS.md`).
- `loader.py` — `load_config()`, with support for deep-merging a per-strategy-version override file from `config/strategies/`.

### `backend/data` (Step 2, not yet implemented)
- `historical/` — historical OHLCV ingestion for backtesting.
- `realtime/` — live/streaming data feed for forward-testing.
- `adapters/` — broker/vendor-specific adapters (Tradovate market data, etc.), isolated from the rest of the engine behind a common feed interface.

### `backend/market` (Steps 2-3, not yet implemented)
- `candles/` — timeframe aggregation (1M → 5M → 15M → 1H → 4H → Daily), lookahead-bias-safe.
- `sessions/` — session window tracking (Asia/London/Pre-NY/NY AM/Lunch-PM) and session high/low tracking.
- `liquidity/` — PDH/PDL/PWH/PWL, equal highs/lows, internal/external liquidity detectors, producing `LiquidityObject`s.
- `structure/` — swing highs/lows, market-state detection (per timeframe).
- `volatility/` — ATR/range-based volatility context (supports expansion/consolidation classification).

### `backend/concepts` (Steps 5-6, not yet implemented)
One detector module per concept (`fvg.py`, `ifvg.py`, `order_block.py`,
`breaker.py`, `ote.py`, `ce.py`, `rejection.py`, `displacement.py`,
`manipulation.py`, and an SMT detector). Each returns `ConceptObject`s (or
the manipulation-candle equivalent). **Strategies query these; they never
reimplement detection.**

### `backend/strategies`
- `base.py` — `Strategy` ABC, `MarketContext` dataclass.
- `ny_continuation_v1.py` — `NyAmHtfContinuationV1` (primary V1 strategy). Currently a skeleton: defines the full required/optional condition list and returns a well-formed `TradeSetup`, but every condition is `NOT_EVALUATED` until the Step 3-6 detectors exist.
- `fvg_inversion_confirmation.py` — `FvgInversionConfirmation` (secondary entry model), implemented as an independent module, disabled by default via `config.entry.fvg_confirmation_enabled`.

### `backend/risk`
- `position_sizing.py` — `calculate_position_size()` (implemented, tested): `contracts = floor(max_risk / risk_per_contract)`, never exceeds configured risk, rejects if one contract already exceeds max risk.
- `daily_limits.py` — `DailyRiskState` (implemented, tested): tracks losses/wins/BE/unprofitable/$ P&L against `RiskConfig` and exposes `can_trade()`.
- `account.py` — `Account` (implemented): balance/percent-based helpers for future percent-risk and drawdown checks.

### `backend/execution`
- `base.py` — `ExecutionBroker` ABC + the `LIVE_TRADING_ENABLED` hard guard.
- `paper.py` — `PaperBroker` (implemented, tested): simulates market-order fills against a supplied reference price, with configurable slippage/commission. The only enabled broker in V1.
- `tradovate.py` — `TradovateBroker` structural placeholder; raises `NotImplementedError` on construction. Establishes the shape for a future `TradovateDemoBroker` / `TradovateLiveBroker` split without requiring strategy changes.

### `backend/management` (Step 7+, not yet implemented)
Stop/target/trailing management logic (`stops.py`, `targets.py`, `trailing.py`), operating on `TradeRecord` + `Target` objects.

### `backend/backtest` (Step 10, not yet implemented)
- `engine.py` — `BacktestEngine` skeleton; `run()` raises `NotImplementedError` with a clear explanation of its dependencies, rather than silently producing fake results.
- `simulator.py`, `metrics.py` — not yet created; will hold the sequential replay loop and post-hoc analytics respectively.

### `backend/decision_log`
(Named `decision_log`, not `logging`, to avoid shadowing Python's stdlib module.)
- `decisions.py` — `DecisionLogger`: append-only newline-delimited-JSON log of every `TradeSetup`, traded or not.
- `trades.py` — `TradeStore`: same pattern for `TradeRecord`s.

Both are intentionally file-backed for V1; swapping to a real database
later only requires changing these two modules, since everything else
depends on their public methods, not their storage format.

### `dashboard/` (Step 12, not yet implemented)
React/TypeScript/Vite/shadcn/Tailwind, using `trade-vision-replay-lab` as a
**UI/design reference only** — no business logic is to be copied from it.

## Configuration versioning

Every `TradeSetup` and `TradeRecord` carries a `parameter_snapshot`
(`AppConfig.parameter_snapshot()` → full JSON-serializable dict) at the
moment it was generated. This means:

- Changing `default.yaml` (or a strategy override file) never invalidates
  the meaning of historical trades/backtests — each record is
  self-describing.
- `config.strategy.version` and `config.config_version` should be bumped
  whenever a meaningful parameter set changes, so results can be grouped
  by "which experiment produced this."

## Why paper/backtest/forward-test share one code path

`Strategy`, `RiskEngine` (risk/*), `TradeManager` (management/*),
`DecisionLogger`, and analytics are constructed once and reused across:

- `BacktestEngine` (historical data feed)
- `ForwardTestEngine` (realtime data feed, not yet implemented)

Only the **data feed** and the **broker** (`PaperBroker` today; potentially
`TradovateDemoBroker` later) differ between backtest and forward-test. This
is what makes results comparable between the two modes.

## Development sequence

See the project spec's "DEVELOPMENT PROCESS" section for the authoritative
12-step sequence. This repository currently completes Step 1 (repository
structure, configuration, domain models, docs, tests, minimal app) plus
partial scaffolding for Steps 7 (strategy skeleton) and 9/10/11 (execution
guard rails and backtest-engine shape) so later steps have a clear
integration point.
