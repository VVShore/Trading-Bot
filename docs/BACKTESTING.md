# Backtesting

## Status

Not implemented yet. `backend/backtest/engine.py::BacktestEngine.run()`
raises `NotImplementedError` with its unmet dependencies listed, rather
than returning fabricated results. This document describes the intended
design (Step 10 of the development process) so later implementation has a
clear target and reviewers know what "done" looks like.

## Input

- 1-minute OHLCV minimum, per symbol (MNQ primary; ES alongside it for SMT).
- Source: historical data ingestion (`backend/data/historical/`, Step 2 — not yet implemented).

## Timeframe aggregation

1M candles aggregate upward: 1M → 5M → 15M → 1H → 4H → Daily. This lives in
`backend/market/candles/` (Step 2/3 — not yet implemented) and is shared by
both backtest and forward-test so aggregation logic never diverges.

**Hard rule:** a higher-timeframe candle is not considered "closed" until
its actual close time. The backtester processes 1M bars sequentially, as
if the engine were watching them arrive in real time — it must never look
at a higher-timeframe candle that, at the current simulated timestamp,
hasn't finished forming yet.

## Simulation responsibilities

The backtest engine (via the same `Strategy`, `RiskEngine`, `PaperBroker`,
`TradeManager`, `DecisionLogger` used by forward-testing) must simulate:

- entry, stop, and target fills
- slippage and commission (via `PaperBroker`'s configured values per instrument)
- partial exits
- breakeven moves and trailing
- session restrictions (analysis/entry window, session-close forced exit)
- news restrictions (manual/imported event blackout windows)
- daily risk lockouts (`DailyRiskState`, already implemented and tested)

## Determinism & no-lookahead verification

Before trusting backtest output, run this regression check (to be added to
`tests/backtest/` once the engine exists): replay the same historical
window (a) bar-by-bar and (b) in two different chunk sizes, and confirm
identical decision logs. A mismatch indicates the engine is (incorrectly)
using data beyond the current simulated timestamp.

## Configuration & attribution

Every backtest run must record:

- strategy name + version
- full `AppConfig.parameter_snapshot()`
- the data window (start/end) and symbol

This reuses the same `parameter_snapshot` mechanism already implemented on
`TradeSetup`/`TradeRecord` (see `docs/ARCHITECTURE.md#configuration-versioning`),
so a backtest result is always traceable to exactly the configuration that
produced it — including which PROVISIONAL rules (`docs/ASSUMPTIONS.md`)
were in effect.

## Relationship to forward-testing

`ForwardTestEngine` (Step 11, not yet implemented) is intended to be a
near-mirror of `BacktestEngine`: same `Strategy`/`RiskEngine`/`TradeManager`/
`DecisionLogger`, swapping `HistoricalDataFeed` for `RealtimeDataFeed`. Any
backtest-only logic that can't cleanly generalize to real-time data (e.g.
random-access lookahead conveniences) is a sign the backtest engine is
diverging from what forward-testing will need — that's a design smell to
fix during implementation, not after.

## Not yet decided

- Whether partial fills should be modeled probabilistically or as
  configurable fixed behavior (e.g. "always fill target 1 in full at the
  target price"). Needs a PROVISIONAL rule and an `docs/ASSUMPTIONS.md`
  entry once implementation starts.
- Multi-symbol replay synchronization detail for SMT (ES + MNQ ticking
  independently) — needs its own design note before Step 6 (SMT detector)
  lands.
