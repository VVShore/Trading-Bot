# Modular Algorithmic Trading Research & Forward-Testing Platform

A modular research/forward-testing platform for futures (MNQ primary, NQ
optional, ES analysis-only), built around a pluggable strategy
architecture so new strategies can be added without rewriting the engine.

**This is not a live trading system.** V1 has no live execution path.
`LIVE_TRADING_ENABLED = False` is enforced at multiple layers — see
`backend/execution/base.py`.

## Status

This repository currently contains the **foundation** described in the
project spec's "FIRST TASK": directory structure, configuration system,
domain models, documentation, a minimal working application, and a test
framework. It does **not** yet contain working detectors (FVG, OB,
manipulation candle, HTF bias, etc.), backtesting, or forward-testing —
those are Steps 2–12 of the development process (see `docs/ARCHITECTURE.md`).

## Quick start

```bash
pip install -r requirements.txt
python main.py          # boots config, strategy, decision logger end-to-end
pytest -q               # run the test suite
```

## Repository layout

```
trading-bot/
├── backend/
│   ├── core/            # domain models, enums — no business logic
│   ├── data/            # market-data ingestion (historical / realtime / adapters) — Step 2
│   ├── market/          # candles, sessions, liquidity, structure, volatility — Steps 2-3
│   ├── concepts/        # FVG, iFVG, OB, breaker, OTE, CE, manipulation, SMT detectors — Steps 5-6
│   ├── strategies/       # Strategy interface + NY_AM_HTF_CONTINUATION_V1, FVG_INVERSION_CONFIRMATION
│   ├── risk/             # position sizing, daily limits, account state
│   ├── execution/        # ExecutionBroker interface, PaperBroker (enabled), TradovateBroker (disabled)
│   ├── management/       # stop/target/trailing management — Step 7+
│   ├── backtest/         # historical replay engine — Step 10
│   ├── decision_log/     # DecisionLogger, TradeStore (named to avoid shadowing stdlib `logging`)
│   └── config/           # schema.py (pydantic), default.yaml, loader.py, strategies/ overrides
│
├── dashboard/            # React/TS/Vite/shadcn dashboard (UI reference: trade-vision-replay-lab) — Step 12
├── tests/                # mirrors backend/ package structure
├── docs/                 # SDS, GLOSSARY, ASSUMPTIONS, ARCHITECTURE, CHANGELOG, TESTING, BACKTESTING
└── main.py               # minimal bootstrap entrypoint
```

See `docs/ARCHITECTURE.md` for module responsibilities and data flow, and
`docs/ASSUMPTIONS.md` for every provisional trading definition and where
to change it.

## Instruments

- **MNQ** — primary execution instrument (V1 default).
- **NQ** — may be enabled manually (`execution.allow_nq_manual_override`) when account buffer allows.
- **ES** — analysis-only (SMT divergence reference). Never executed.

## Safety

- `backend/execution/base.py::LIVE_TRADING_ENABLED = False` — hard-coded kill switch, independent of config.
- `config.execution.live_trading_enabled` — config-level guard, also defaults to `False`.
- `TradovateBroker` raises `NotImplementedError` on construction; it is a structural placeholder only.
- `PaperBroker` is the only enabled execution path in V1.
