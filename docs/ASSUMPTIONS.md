# Assumptions (PROVISIONAL definitions)

The trading specification is intentionally incomplete in places. Per spec
rules #5 and #6: where a definition is ambiguous, this system implements a
reasonable provisional rule, makes it configurable, and documents it here.
The trader's definitions remain authoritative; anything below is a
placeholder pending refinement, not a claim of correctness.

**Every row below must be updated (with a CHANGELOG entry) the moment a
provisional rule is replaced by a confirmed definition.**

| # | Concept | Provisional rule (V1 default) | Config path | Status |
|---|---|---|---|---|
| 1 | Liquidity staleness distance | A liquidity level is considered no longer immediately relevant once price has distributed 150 NQ points beyond it (still tracked, but excluded from active targeting). | `liquidity.staleness.stale_distance_points` | PROVISIONAL |
| 2 | Equal high/low tolerance | Two or more highs (or lows) within 5 NQ points of each other are treated as "equal" liquidity. | `liquidity.tolerance.equal_level_tolerance_points` | PROVISIONAL |
| 3 | Market-state detection (accumulation/distribution/expansion/retracement/manipulation/consolidation/reversal) | Not yet implemented (Step 3+). Definitions in `docs/GLOSSARY.md` are the trader's; the *detection algorithm* deriving each state from candle sequences is still to be built and will be PROVISIONAL by nature — several of these states are inherently subjective. | n/a yet — `backend/market/structure` | PROVISIONAL, unimplemented |
| 4 | Strong HTF candle | Body ≥ 60% of total candle range. | `htf.strong_body_percent` | PROVISIONAL |
| 5 | Weak/rejection wick | Dominant wick ≥ 70% of candle range, OR wick exceeds body by 10–15% (implemented as a single configurable midpoint, 12.5%). | `htf.dominant_wick_rejection_percent`, `htf.wick_exceeds_body_percent` | PROVISIONAL |
| 6 | Body-engulfing threshold | ≥ 85% body overlap counts as a body engulf (stronger signal than full-candle engulf). | `htf.body_engulf_threshold_percent` | PROVISIONAL |
| 7 | FVG minimum size / max age | Not specified in the trading spec. Defaulted to `min_size_points=1.0`, `max_age_candles=200` as a starting point — these are placeholders, not derived from trader input, and should be tuned once FVG detection (Step 5) is validated against real charts. | `fvg.min_size_points`, `fvg.max_age_candles` | PROVISIONAL, needs trader review |
| 8 | iFVG inversion rule | Not yet implemented. Will require a defined "fails and closes back through" rule tied to the FVG detector once built (Step 5). Marked in spec as needing "further SDS refinement." | n/a yet — `backend/concepts/ifvg.py` | PROVISIONAL, unimplemented |
| 9 | Order Block definition/quality | Not yet implemented. Spec explicitly states the OB definition is provisional; V1 must expose displacement-following-OB, retest count, and age as quality metadata rather than treating all OBs as equally valid. | n/a yet — `backend/concepts/order_block.py` | PROVISIONAL, unimplemented |
| 10 | Manipulation candle detector | Not yet implemented. This is the single most important — and most subjective — V1 concept. Parameters are scaffolded in config with starting defaults (`min_wick_size_points=8`, `max_body_percent=40`, `require_liquidity_sweep=true`, `require_htf_confluence=false`, `require_rejection=true`, `lookback_candles=30`, `max_age_candles=20`, `require_confirmation=false`) but the detection algorithm itself does not exist yet. **Do not treat these numbers as validated — they are placeholders to unblock configuration wiring.** | `manipulation.*` | PROVISIONAL, unimplemented |
| 11 | OTE levels | 0.62 / 0.705 / 0.79 used as starting Fibonacci levels; spec explicitly says these are not permanently authoritative. | `ote.levels` | PROVISIONAL |
| 12 | CE calculation | FVG CE = midpoint of FVG range unless overridden; manipulation-candle CE = midpoint of candle range. This is the trader's stated rule, not an assumption, but is recorded here because other CE variants (e.g. weighted CE) may be added later. | `backend/core/models/concept.py::ConceptObject.compute_ce`, `backend/core/models/candle.py::Candle.midpoint` | Trader-specified default |
| 13 | Fixed trailing increment | 20 NQ points, only used when `management.use_fixed_trailing=true` (opt-in, off by default). | `management.fixed_trailing_increment_points` | PROVISIONAL |
| 14 | BE breathing room | If price is at/above BE and a strong 1M rejection occurs, the trade is allowed to "breathe" if at least 20 points of room exists before exiting. | `management.be_breathing_room_points` | PROVISIONAL |
| 15 | Risk per trade | Spec states "$200–$400 depending on setup quality/conviction." V1 defaults to a flat `$300` (midpoint) since conviction-based sizing is not yet implemented; the risk engine and UI already support switching to percent-of-account risk. | `risk.risk_dollars`, `risk.risk_mode` | PROVISIONAL default, conviction-based scaling not yet implemented |
| 16 | Daily 20% account-loss threshold | Spec explicitly says this is a configurable *safety* limit, not to be assumed as a broker/prop-firm rule. Implemented purely as an informational config field for now; not yet wired into `DailyRiskState` enforcement. | `risk.daily_account_loss_threshold_percent` | PROVISIONAL, not yet enforced |
| 17 | Instrument point values / tick sizes | MNQ ($2/pt, 0.25 tick), NQ ($20/pt, 0.25 tick), ES ($50/pt, 0.25 tick) are standard CME contract specs, not provisional — included here for completeness since they feed directly into position sizing. | `execution.instruments` | Factual, not provisional |
| 18 | Commission / slippage estimates | `commission_per_contract` and `estimated_slippage_points` per instrument are placeholder starting points (not sourced from a specific broker fee schedule) and should be replaced with actual Tradovate/broker figures before backtest results are trusted quantitatively. | `execution.instruments.*.commission_per_contract`, `execution.instruments.*.estimated_slippage_points` | PROVISIONAL |
| 19 | SMT lookback window | 20 candles used as the comparison window for NQ/MNQ vs. ES divergence. Detector itself not yet implemented (Step 5+). | `smt.lookback_candles` | PROVISIONAL, unimplemented |
| 20 | HTF bias lookback | Default 5 completed 1H candles, engine supports 1–12 (spec-specified range, not an assumption). | `htf.lookback_candles`, `htf.max_lookback_candles` | Trader-specified default |

## How to replace a provisional rule

1. Update the value/algorithm in the relevant `backend/` module and `backend/config/schema.py` + `default.yaml` if the shape changes.
2. Update the row above (or remove it if fully resolved) and set Status to "Confirmed — see CHANGELOG vX.X".
3. Add a `docs/CHANGELOG.md` entry describing what changed and why.
4. Add/update unit tests covering the new rule, including edge cases.
5. Because every `TradeSetup`/`TradeRecord` stores a `parameter_snapshot`, historical backtests remain attributable to the old rule automatically — no migration of historical data is required.
