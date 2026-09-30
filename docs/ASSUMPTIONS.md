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
| 15 | Risk per trade | Spec states "$200–$400 depending on setup quality/conviction." V1 defaults to a flat `$300` (midpoint) since conviction-based sizing is not yet implemented; the risk engine and UI already support switching to percent-of-account risk. | `risk.risk_dollars`, `risk.risk_mode` | SUPERSEDED by policy lock 1 (below); conviction-based scaling still not implemented |
| 16 | Daily 20% account-loss threshold | Spec explicitly says this is a configurable *safety* limit, not to be assumed as a broker/prop-firm rule. Implemented purely as an informational config field for now; not yet wired into `DailyRiskState` enforcement. | `risk.daily_account_loss_threshold_percent` | PROVISIONAL, not yet enforced |
| 17 | Instrument point values / tick sizes | MNQ ($2/pt, 0.25 tick), NQ ($20/pt, 0.25 tick), ES ($50/pt, 0.25 tick) are standard CME contract specs, not provisional — included here for completeness since they feed directly into position sizing. | `execution.instruments` | Factual, not provisional |
| 18 | Commission / slippage estimates | `commission_per_contract` and `estimated_slippage_points` per instrument are placeholder starting points (not sourced from a specific broker fee schedule) and should be replaced with actual Tradovate/broker figures before backtest results are trusted quantitatively. | `execution.instruments.*.commission_per_contract`, `execution.instruments.*.estimated_slippage_points` | PROVISIONAL |
| 19 | SMT lookback window | 20 candles used as the comparison window for NQ/MNQ vs. ES divergence. Detector itself not yet implemented (Step 5+). | `smt.lookback_candles` | PROVISIONAL, unimplemented |
| 20 | HTF bias lookback | Default 5 completed 1H candles, engine supports 1–12 (spec-specified range, not an assumption). | `htf.lookback_candles`, `htf.max_lookback_candles` | Trader-specified default |

## Policy locks (owner decisions 1-9, recorded 2026-09-28)

These are the owner's confirmed rules for the MVP, not provisional definitions. "Enforced" means
code + tests exist; "Partial" says exactly what is missing.

| # | Rule | Where enforced | Status |
|---|---|---|---|
| L1 | Base sizing on the $50,000 account with $200-$400 max risk/trade. `N = floor(dollar_risk / (SL pts x $2))` for MNQ. If the next-bar open gaps so the stop distance would break the ceiling, re-size DOWN at fill, or reject. | `RiskConfig`/`AppConfig` range check (risk must resolve to $200-$400); `RiskEngine.evaluate` (formula); `RiskEngine.reconcile_fill` (gap rule: never sizes up, rejects if no valid size or fill is through the stop) | Enforced. CONFIRMED by owner resolution R2: sizing stays pure (no commission/slippage in the formula); a stop-out that loses more than the ceiling because of costs is an accepted operating cost, not a RiskEngine violation. |
| L2 | Off-tick prices round deterministically to the 0.25 tick: entries TOWARD market price, stops AWAY from entry, targets TOWARD entry. | `InstrumentSpec.round_entry/round_stop/round_target` (exact decimal math); applied by `RiskEngine` BEFORE sizing, so the ceiling holds on the rounded stop | Enforced. CONFIRMED by owner resolution R4: "toward market" = the neighbouring tick on the market's side; an off-tick entry with no `market_price` is rejected (fail closed). |
| L3 | Max 1 concurrent position; max 6 trades/day; backtests may lift the trade cap via an explicit flag. | `risk.max_concurrent_positions` (open positions + approved-but-unsettled intents); `risk.max_trades_per_day` (= "max_daily_trades") via `DailyRiskState`; `risk.backtest_override_trade_frequency` (paper environment only) | Enforced. The override lifts only the trades/day cap. Per resolution R1 the legacy counters (max losses, max wins, max unprofitable trades, breakeven allowance) are REMOVED: the only lockouts are the daily loss limit, the remaining-budget rule and the 6-trade cap. |
| L4 | Fixed daily loss limit of $2,000-$2,500 (V1 default $2,000). If the remaining budget cannot cover one full trade allocation, lock out for the rest of the session. No partial-size scaling. | `risk.max_daily_loss` (default $2,000, range-checked); `DailyRiskState` (limit reached, or remaining budget < resolved risk per trade) | Enforced. |
| L5 | `active_symbol` is MNQ only; ignore `allow_nq_manual_override`. | `RiskEngine` (only `execution.active_symbol`; ES never) | Enforced; the override field is read by nothing. |
| L6 | All aggregators/session logic in America/New_York; contract rollover is manual config, never automated. | `session.timezone` locked by validator; `market/sessions/clock.py`; aggregator and normalizer; `execution.active_contract` (manual field) | Enforced. `active_contract` is a plain setting, not yet consumed by any component. |
| L7 | Session windows are strategy CONDITIONS (outside window -> NO_TRADE); no news APIs; a `pause_trading` flag for manual halts. | `NyAmHtfContinuationV1` (`within_execution_window` condition); `risk.pause_trading` enforced by `RiskEngine` | Enforced. |
| L8 | Paper/backtest: if one 1M candle touches both SL and TP, assume stop first, log a safety event, halt the session. Live relies on OCO brackets. | `management/bar_exit.py` (`evaluate_bar_exit`, `SafetyEvent`, `enforce_safety_event` -> `DailyRiskState.halt_session`) | PARTIAL: decision logic, event and session halt are implemented and tested; they are not wired into PaperBroker bracket exits yet (Phase 5), and the event is not yet written to the decision log. |
| L9 | RiskEngine and OrderIntent were built fresh in Phase 1; continue on this baseline. | n/a | Confirmed. |

### Owner resolutions (2026-09-29)

| # | Resolution | Effect |
|---|---|---|
| R1 | Daily counter cleanup | Removed `max_losses`, `max_wins_per_day`, `max_unprofitable_trades_per_day` from config and code. Also removed `allow_be_trades` and the breakeven-allowance lockout (same family of legacy counters, so that limits rely strictly on the policy locks; flagged for the owner to confirm). Wins/losses/breakevens remain as statistics only. Trade limits are now: 6 trades/day, $2,000 daily loss, $200-$400 risk per trade (flat $300 default). |
| R2 | Costs vs risk ceiling | Position sizing is pure `floor(risk / (SL pts x $2))`; commission ($0.74/contract) and slippage are NOT deducted. Losses above the ceiling caused only by costs are acceptable. Covered by tests. |
| R3 | 4H alignment | 18:00 ET anchor CONFIRMED (bars at 18, 22, 02, 06, 10, 14; the 14:00 bar is cut at the 17:00 halt). Former provisional rule P1. |
| R4 | "Toward market" rounding | CONFIRMED as the neighbouring tick on the market's side; missing `market_price` -> reject. |

### Provisional rules introduced by Phase 2

| # | Concept | Provisional rule | Config path | Status |
|---|---|---|---|---|
| P1 | Bar close on gaps | A higher-timeframe bar closes when its last minute arrives, or when a later bucket's candle arrives (missing minutes are never invented). | n/a | PROVISIONAL |
| P2 | Tick-built candles | A 1M candle from ticks closes only when a tick from a later minute arrives (no clock-driven close). | n/a | PROVISIONAL |

## How to replace a provisional rule

1. Update the value/algorithm in the relevant `backend/` module and `backend/config/schema.py` + `default.yaml` if the shape changes.
2. Update the row above (or remove it if fully resolved) and set Status to "Confirmed — see CHANGELOG vX.X".
3. Add a `docs/CHANGELOG.md` entry describing what changed and why.
4. Add/update unit tests covering the new rule, including edge cases.
5. Because every `TradeSetup`/`TradeRecord` stores a `parameter_snapshot`, historical backtests remain attributable to the old rule automatically — no migration of historical data is required.
