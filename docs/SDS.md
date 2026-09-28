# Software Design Specification

## 1. Purpose

Build a modular research and forward-testing platform for a discretionary
concept ("NY AM HTF Continuation") translated into an algorithmic strategy,
with:

- reusable market-structure/concept detectors, decoupled from strategy logic
- a configuration system so parameters can change without code changes
- historical backtesting and live-data forward-testing sharing one strategy
  implementation
- paper execution only (no live orders in V1)
- a dashboard for monitoring and analytics

## 2. Scope (V1)

In scope:
- Strategy `NY_AM_HTF_CONTINUATION_V1` (primary) and
  `FVG_INVERSION_CONFIRMATION` (secondary, disabled by default)
- MNQ execution, NQ manual override, ES analysis-only
- Timeframes: 1M (execution), 5M, 15M, 1H (bias), plus ES for SMT
- Backtesting against historical 1M OHLCV, aggregated upward
- Forward-testing (analysis-only and paper modes) against real-time data
- Dashboard: live/forward-test status page, analytics

Out of scope for V1:
- Live order execution (Tradovate demo/live)
- Any instrument besides MNQ/NQ/ES
- Weekly/monthly risk limits
- Automated news-event ingestion (manual/imported events only)

## 3. Actors

- **Trader (user)** — configures parameters, reviews decision logs and
  analytics, manually enables NQ or advances broker environment in future
  versions.
- **Strategy engine** — evaluates market context and produces `TradeSetup`s.
- **Risk engine** — approves/rejects position sizing and daily trading
  eligibility.
- **Execution broker** — simulates (or, in the future, places) orders.

## 4. High-level data flow

```
Market Data (TradingView for validation / Tradovate for data)
        │
        ▼
Market Data Adapter (backend/data)
        │
        ▼
Market/Concept Engines (sessions, liquidity, HTF bias, FVG, OB, SMT, manipulation)
        │
        ▼
Strategy (backend/strategies) ── TradeSetup ──► DecisionLogger
        │ (if decision == TRADE)
        ▼
Risk Engine (backend/risk) ── approves size / rejects
        │
        ▼
ExecutionBroker (PaperBroker in V1) ── OrderResult
        │
        ▼
TradeManager (backend/management) ── TradeRecord updates ──► TradeStore
        │
        ▼
Analytics ──► Dashboard
```

## 5. Key design decisions

| Decision | Rationale |
|---|---|
| Strategy receives `MarketContext`, returns `TradeSetup` | Keeps strategies broker-agnostic and testable in isolation. |
| Detectors are separate modules the strategy *queries* | Prevents duplicated/diverging detection logic (spec rule #4). |
| All tunables live in `AppConfig` (pydantic) loaded from YAML | Changing a parameter never requires a code change or redeploy of logic. |
| `TradeSetup`/`TradeRecord` store a full `parameter_snapshot` | Historical results stay attributable to the config version that produced them, even after later parameter changes. |
| `ExecutionBroker` interface with `PaperBroker`/`TradovateBroker` | Backtest, forward-test, and (future) live share identical strategy code; only the broker differs. |
| `LIVE_TRADING_ENABLED` hard constant + config flag + `TradovateBroker` raising on construction | Defense in depth against accidentally enabling live orders. |
| Decision log package named `decision_log`, not `logging` | Avoids shadowing Python's standard library `logging` module. |

## 6. Interfaces (V1 skeleton)

- `Strategy.evaluate(context: MarketContext) -> TradeSetup`
- `ExecutionBroker.submit_order(order: Order) -> OrderResult`
- `DecisionLogger.record(setup: TradeSetup) -> None`
- `TradeStore.save(trade: TradeRecord) -> None`
- `load_config(path=None, override_path=None) -> AppConfig`

## 7. Non-functional requirements

- No lookahead bias in backtesting (HTF candles only "close" at their real close time).
- Every parameter documented as PROVISIONAL must appear in `docs/ASSUMPTIONS.md`.
- Every executed trade retains the exact config snapshot that generated it.
- Unit tests required for every concept detector, not just "does it run."

See `docs/ARCHITECTURE.md` for module-by-module detail and
`docs/ASSUMPTIONS.md` for the provisional trading definitions.
