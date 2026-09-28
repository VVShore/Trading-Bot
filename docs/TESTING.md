# Testing

## Philosophy

Per spec rule: "Do not consider a module complete merely because it runs."
Every concept detector, risk rule, and execution guard needs unit tests
covering edge cases, not just a happy path.

## Running tests

```bash
pip install -r requirements.txt
pytest -q               # run everything
pytest tests/risk -q    # run one package
pytest -k position_size # run by keyword
```

Configuration lives in `pytest.ini` (`pythonpath = .`, `testpaths = tests`).

## Current coverage (32 tests)

| Area | File | What's covered |
|---|---|---|
| Config | `tests/config/test_config_loader.py` | Schema validates, live-trading guard defaults false, risk/HTF defaults match spec, config is JSON-serializable for snapshots. |
| Core models | `tests/core/test_candle.py` | OHLC invariants (high/low bounds, close after open), body/range/wick math, zero-range edge case. |
| Risk — position sizing | `tests/risk/test_position_sizing.py` | Correct contract count (floor, never rounds up past max risk), single-contract-exceeds-max rejection, equal entry/stop rejection, dollar vs. percent risk resolution. |
| Risk — daily limits | `tests/risk/test_daily_limits.py` | Lockout on max losses / max trades / max daily loss $ / BE allowance; consecutive-loss counter resets on a win. |
| Strategy skeleton | `tests/strategies/test_ny_continuation_v1.py` | V1 returns a well-formed NO_TRADE setup pre-detectors, parameter snapshot is attached, secondary strategy is disabled by default, no shared mutable state between instances. |
| Execution guards | `tests/backtest/test_execution_guards.py` | `LIVE_TRADING_ENABLED` is False, `PaperBroker.is_live` is False, `TradovateBroker()` raises, `PaperBroker` fill math (slippage/commission) is correct, rejects orders with no reference price. |

## Test layout convention

`tests/` mirrors `backend/` package-for-package (`tests/risk` ↔
`backend/risk`, `tests/strategies` ↔ `backend/strategies`, etc.). New
modules should add a matching test file in the same relative location.

## What must be tested before a module is "done"

Per the spec's testing requirements section, at minimum:

- FVG, iFVG, CE, OTE, equal highs/lows, PDH/PDL, PWH/PWL, session highs/lows
- Candle strength, rejection, engulfing
- Liquidity sweep, manipulation candle
- Position sizing ✅, daily loss limit ✅, consecutive-loss lockout ✅
- Target calculation, BE movement, trailing
- Session restriction (entry window, session close)

Detector modules (FVG, OB, manipulation, etc.) don't exist yet — as each
is implemented (Steps 5-6), it must land with tests covering: a clear
positive case, a clear negative case, a boundary/tolerance edge case (e.g.
exactly at the configured tolerance), and — where lookahead bias is
possible — a test proving the detector only uses closed candles.

## Backtest correctness testing (once implemented)

In addition to unit tests, `docs/BACKTESTING.md` describes a no-lookahead
regression test that must pass before the backtest engine is considered
trustworthy: replaying the same historical window forward in two different
"chunk sizes" must produce identical signals.
