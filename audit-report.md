# Architecture & Specification Audit — Foundation Review (pre–Step 2)

Everything below is grounded in the actual files in `/trading-bot`, verified
by running import-graph analysis, executing the real position-sizing code,
and cloning + reading both of your GitHub repos. Nothing here is
speculative about what the code "would" do — where something isn't wired
up, I say so explicitly.

---

## 1. Architecture audit — dependency flow

Actual verified import graph (from static analysis of every `backend/*.py` file):

```
core.enums  ←  core.models  ←  config.schema  ←  {strategies, risk, execution, decision_log}  ←  backtest.engine (orchestrator)
```

| Connection | Interface/class | Who imports whom | One-directional? | Replaceable independently? |
|---|---|---|---|---|
| Market Data → Timeframe Aggregation | **Not implemented.** No files exist in `data/` or `market/candles/` beyond empty `__init__.py`. | n/a | n/a | n/a |
| Timeframe Aggregation → Market State | **Not implemented.** `market/structure/` is empty. | n/a | n/a | n/a |
| Market State → Concept Detectors | **Not implemented.** `concepts/` is empty. | n/a | n/a | n/a |
| Concept Detectors → Strategy | **Not implemented**, but the *shape* exists: `ConceptObject` (core/models/concept.py) is the generic return type every detector is meant to produce; `Strategy.evaluate()` takes a `MarketContext` with a loosely-typed `concepts: list[Any]` field. | Strategy does not import any concept module today (none exist). | Designed to be, not proven yet. | Cannot verify — nothing to replace yet. |
| Strategy → Risk | **No import exists.** `backend/strategies/*.py` does not import anything from `backend/risk/`. | Neither imports the other. | Yes — currently fully decoupled because nothing connects them at all. | Trivially — there is no coupling to remove. |
| Strategy → Execution | **No import exists.** `backend/strategies/*.py` does not import `backend/execution/`. | Neither imports the other. | Yes | Yes |
| Risk → Execution | **No import exists.** `backend/risk/*.py` does not import `backend/execution/`. | Neither. | Yes | Yes |
| Execution → Trade Management | **Not implemented.** `backend/management/` is empty. | n/a | n/a | n/a |
| Trade Management → Decision Logging | **Not implemented** on the management side; `DecisionLogger` itself only imports `core.models.setup.TradeSetup`. | `decision_log/decisions.py` imports only `core.models.setup`. | Yes | Yes |
| Decision Logging → Analytics | **Not implemented.** No analytics module exists. | n/a | n/a | n/a |
| Analytics → Dashboard | **Not implemented.** No API layer or dashboard code exists. | n/a | n/a | n/a |

**Direct answer to your specific question — is Strategy V1 decoupled from broker, risk engine, database, and dashboard?**

Yes, verifiably. `backend/strategies/ny_continuation_v1.py` imports only:
```
backend.core.enums
backend.core.models.setup
backend.strategies.base
```
Zero imports of `execution`, `risk`, `decision_log`, or anything dashboard-related. `Strategy.evaluate()` returns a `TradeSetup` — a plain data object — and nothing downstream is referenced from inside the strategy. This is real, not aspirational: I ran a full AST-based import scan of every file to confirm it, not just a visual read.

**Important caveat:** this decoupling is currently easy to verify *because most of the graph doesn't exist yet*. The real test comes when `backend/backtest/engine.py` (currently a stub) becomes the orchestrator that constructs a `Strategy`, calls `.evaluate()`, hands the resulting `TradeSetup` to a not-yet-built `RiskEngine`, and only then to `PaperBroker`. That orchestration pattern is sketched in `docs/SDS.md` §4 but not implemented. I'd call today's decoupling "not-yet-contradicted" rather than "proven under load."

---

## 2. Strategy plug-in audit

**Would adding Strategy V2 require only a new file + config entry, or core-module changes?**

Based on what exists today: **mostly a new file**, with one necessary (already-anticipated) config touch. Concretely, to add `strategies/reversal_v1.py`:

1. Create the class, subclassing `Strategy` (`backend/strategies/base.py`) — no changes needed to `base.py` itself; `MarketContext` and the `Strategy.evaluate()` signature are generic.
2. Add a new value to `StrategyName` enum (`core/enums/enums.py`) — **this is a real touch to a shared file**, because `TradeSetup.strategy` is typed as the `StrategyName` enum, not a free string. This is intentional (prevents typos/drift in stored records) but it does mean adding a strategy always requires a one-line enum addition.
3. Add a config section if V2 needs its own tunables not already in `AppConfig` (e.g. reversal-specific thresholds) — this means extending `backend/config/schema.py` with a new sub-model and wiring it into `AppConfig`. This is the same pattern already used for `entry.fvg_confirmation_enabled` toggling `FvgInversionConfirmation`.
4. Register it wherever strategies get instantiated for backtest/forward-test — **this registry does not exist yet**. Nothing in the current codebase enumerates "which strategies are active." `main.py` hard-imports `NyAmHtfContinuationV1` directly. Before Step 2, you should decide whether this becomes a `STRATEGY_REGISTRY: dict[StrategyName, type[Strategy]]` (simple, explicit) or a plugin-discovery mechanism (over-engineered for 2 strategies). I'd flag this as a real gap — see §17.

**Verdict:** no core detection/risk/execution logic needs to change. The `StrategyName` enum touch and the missing strategy registry are the two honest exceptions to "just drop in a new file."

---

## 3. Detector architecture

**Can Strategy V1 and a hypothetical V2 consume the same detector outputs?**

Structurally yes — `ConceptObject` (`core/models/concept.py`) is a single shared shape for FVG, iFVG, OB, breaker, OTE, CE, volume imbalance, and BPR. Example of what a (not-yet-implemented) FVG detector would hand back, and how a strategy would consume it:

```python
# What backend/concepts/fvg.py::detect_fvgs() would return (shape only, not implemented):
fvg = ConceptObject(
    id="fvg_1h_0007",
    type=ConceptType.FVG,
    timeframe=Timeframe.H1,
    direction="bullish",
    upper=20050.0,
    lower=20030.0,
    creation_time=some_datetime,
    source_candles=["candle_h1_0512", "candle_h1_0513", "candle_h1_0514"],
)

# Any strategy consumes it the same way, generically:
ce = fvg.compute_ce()          # 20040.0 — works regardless of which strategy calls it
if fvg.status == ConceptStatus.ACTIVE and fvg.direction == "bullish":
    ...
```

Nothing in `ConceptObject` is strategy-specific. `LiquidityObject` (liquidity detector output) follows the identical pattern — generic `type`, `price`, `status`, `metadata` dict for anything detector-specific that doesn't deserve a first-class field.

**Honest limitation:** this is a *type-shape* guarantee, not a *behavioral* one, because zero detectors are implemented yet. I can show you the contract is reusable; I can't yet show you two strategies actually sharing a live FVG list, because there is no live FVG list. This should be the first thing validated once Step 5 starts — write the FVG detector, then immediately write a throwaway second "test strategy" that also queries it, before building out V1's full logic, to catch coupling early rather than after V1 is finished.

---

## 4. Configuration audit

Legend: **Exists** = field is in `AppConfig`/`default.yaml`. **Validated** = pydantic enforces type/shape on load (true for every field, since it's all pydantic). **Consumed** = actual non-test code reads `config.<field>` (verified via grep across `backend/` and `main.py`, not assumed).

| Parameter | Config path | Default | Provisional? | Consumed by real code? |
|---|---|---|---|---|
| Strategy enabled/name/version | `strategy.*` | true / NY_AM_HTF_CONTINUATION_V1 / 0.1.0 | No | ❌ Not read anywhere (name/version are hard-coded as class attributes on `NyAmHtfContinuationV1` instead — see §17 duplication risk) |
| Session times | `session.*` | 09:00/09:02/11:00/16:00 | entry_end explicitly configurable per spec | ❌ Not read — no session engine exists yet |
| Session importance | `session_importance.*` | 7/8/7/10/5 | No | ❌ Not read |
| HTF lookback | `htf.lookback_candles`, `max_lookback_candles` | 5, 12 | No (spec-given range) | ❌ Not read — no HTF bias engine yet |
| HTF strength thresholds | `htf.strong_body_percent`, `dominant_wick_rejection_percent`, `wick_exceeds_body_percent`, `body_engulf_threshold_percent` | 60/70/12.5/85 | **Yes** | ❌ Not read |
| Liquidity tolerance | `liquidity.tolerance.equal_level_tolerance_points` | 5 | **Yes** | ❌ Not read |
| Liquidity staleness | `liquidity.staleness.stale_distance_points` | 150 | **Yes** | ❌ Not read |
| Liquidity importance weights | `liquidity.importance.*` | per spec | No | ❌ Not read |
| FVG min size/age | `fvg.min_size_points`, `max_age_candles` | 1.0, 200 | **Yes (my invented default)** | ❌ Not read |
| OTE levels | `ote.levels` | [0.62, 0.705, 0.79] | Spec says "not permanently authoritative" | ❌ Not read |
| Manipulation candle params | `manipulation.*` (8 fields) | see ASSUMPTIONS.md | **Yes, fully** | ❌ Not read |
| SMT config | `smt.*` | enabled=true, ES, 20 candles | **Yes (lookback)** | ❌ Not read |
| Entry toggles | `entry.manipulation_ce_enabled`, `entry.fvg_confirmation_enabled` | true, false | No | ✅ **`entry.fvg_confirmation_enabled` is read** — `FvgInversionConfirmation.evaluate()` checks it and short-circuits to NO_TRADE when false. This is the one non-trivial config read in the whole strategy layer today. |
| Management rules | `management.*` (5 fields) | see default.yaml | **Yes (2 of 5)** | ❌ Not read |
| News config | `news.*` | enabled, 15min, event list | No | ❌ Not read |
| Risk dollar/percent | `risk.risk_mode`, `risk_dollars`, `risk_percent`, `account_size` | dollar/300/0.6/50000 | **Yes (risk_dollars, midpoint of $200–400)** | ❌ **Not read from AppConfig anywhere.** `calculate_position_size()` takes `max_risk_dollars` as a plain function argument — nothing currently pulls that argument from `config.risk.risk_dollars`. The two are disconnected today. |
| Daily risk limits | `risk.max_daily_loss`, `max_losses`, `max_trades_per_day`, `max_wins_per_day`, `max_unprofitable_trades_per_day`, `allow_be_trades` | 1200/2/5/3/3/1 | No | ✅ **All six are read** — `DailyRiskState._evaluate_lockout()` reads every one of them directly off the injected `RiskConfig`. This is real, tested (`tests/risk/test_daily_limits.py`), and matches the config values 1:1. |
| Risk scaling behavior | `risk.reduce_risk_after_losing_day`, `increase_risk_after_win` | true, false | No | ❌ Not read — fields exist, no logic consumes them yet |
| Daily account-loss % safety cap | `risk.daily_account_loss_threshold_percent` | 20 | **Yes, explicitly flagged as not-yet-enforced** | ❌ Not read (documented as such in ASSUMPTIONS.md) |
| Execution symbol/override | `execution.active_symbol`, `allow_nq_manual_override` | MNQ, true | No | ✅ `active_symbol` is read once, in `main.py`, only to print/pass into a `MarketContext`. Not used for any actual routing logic yet. |
| Live trading guard | `execution.live_trading_enabled` | false | No — factual safety gate | ✅ Read and asserted in `main.py`. **Not** currently cross-checked inside `PaperBroker` or `TradovateBroker` themselves (they rely on the separate module-level `LIVE_TRADING_ENABLED` constant, not this config field) — see §17. |
| Broker environment | `execution.broker_environment` | "paper" | No | ✅ Read (printed) in `main.py`. Not used to actually select a broker class anywhere — `main.py` doesn't construct a broker at all yet. |
| Per-instrument specs | `execution.instruments.{MNQ,NQ,ES}.*` (point_value, tick_size, commission, slippage) | real CME contract specs | No (factual), commission/slippage estimates are provisional | ❌ **Not read.** `PaperBroker` uses its own separate `PaperBrokerConfig` dataclass with independent defaults (`point_value=2.0` hard-coded), not `AppConfig.execution.instruments`. **This is a real disconnect** — see §17 CRITICAL. |

**Summary:** of ~45 leaf parameters in `AppConfig`, **8 are actually read by implemented logic** (the 6 daily-risk fields, `entry.fvg_confirmation_enabled`, and the informational `execution.*` fields printed in `main.py`). Everything else exists, validates, and gets included in `parameter_snapshot()` (so it's not *lost* — it'll be attributed correctly to whichever backtest run used it), but nothing acts on it yet. That's expected at this stage (no detectors exist to consume detector-tuning parameters) but I want to be precise rather than implying more is wired up than actually is.

---

## 5. Provisional assumptions audit

All 20 rows from `docs/ASSUMPTIONS.md`, restated with the isolation/difficulty assessment you asked for:

| # | Concept | Spec said | What I assumed | Where in code | Isolated to one place? | Difficulty to replace |
|---|---|---|---|---|---|---|
| 1 | Liquidity staleness | "sufficiently far... currently PROVISIONAL, must be configurable" | 150 NQ points (spec's own suggested default) | `config.liquidity.staleness.stale_distance_points` only — no consuming code yet | ✅ Yes | Trivial — change one config value, no code exists to touch |
| 2 | Equal high/low tolerance | "PROVISIONAL... make configurable" | 5 NQ points (spec's own suggested default) | `config.liquidity.tolerance.equal_level_tolerance_points` only | ✅ Yes | Trivial |
| 3 | Market-state detection algorithm | Definitions given, detection method not specified; "implement...as PROVISIONAL" | Not implemented at all yet | n/a | N/A — nothing to isolate yet | N/A |
| 4 | Strong HTF candle | "Default provisional rule: Body >= 60%" | Used spec's own number verbatim | `config.htf.strong_body_percent` only, no consumer yet | ✅ Yes | Trivial |
| 5 | Rejection wick | "70% OR wick exceeds body by 10-15%" | Took the spec's range and picked its midpoint (12.5%) for the second threshold since it needed one number, not a range | `config.htf.dominant_wick_rejection_percent`, `wick_exceeds_body_percent` | ✅ Yes | Trivial — but flag: **this is the one place I made a real numeric judgment call** (midpoint of your stated range) rather than using a spec-given single value. Worth your explicit sign-off. |
| 6 | Body engulf threshold | "Default provisional body-engulf threshold: 85%" | Used spec's number verbatim | `config.htf.body_engulf_threshold_percent` | ✅ Yes | Trivial |
| 7 | FVG min size/age | **Not specified in your spec at all** | I invented `min_size_points=1.0`, `max_age_candles=200` to unblock the config schema | `config.fvg.*`, no consumer yet | ✅ Yes | Trivial to change, but **flagging again: these numbers came from me, not you** — needs your review before FVG detection is built on top of them |
| 8 | iFVG inversion rule | "must be configurable and documented as PROVISIONAL until further SDS refinement" | Not implemented — no rule invented at all | n/a | N/A | N/A |
| 9 | OB definition | "PROVISIONAL and must be documented" | Not implemented — no rule invented | n/a | N/A | N/A |
| 10 | Manipulation candle detector | Spec lists 8 required configurable parameters, no values given | I invented starting values for all 8 (`min_wick_size_points=8`, `max_body_percent=40`, etc.) to unblock config wiring | `config.manipulation.*`, no consumer yet — **detector itself does not exist** | ✅ Yes (isolated to one config block) | Trivial to *change the numbers*; the actual detector algorithm is unwritten, so "replacing" this assumption really means *writing* Step 6 for the first time, not editing existing logic |
| 11 | OTE levels | "0.62/0.705/0.79... not permanently authoritative" | Used spec's numbers verbatim | `config.ote.levels` | ✅ Yes | Trivial |
| 12 | CE calculation | Trader-specified (not an assumption) | Implemented exactly as specified: FVG midpoint, manipulation-candle midpoint | `ConceptObject.compute_ce()`, `Candle.midpoint` | ✅ Yes | N/A — not provisional |
| 13 | Fixed trailing increment | "Default provisional value: 20 NQ points" | Used spec's number verbatim | `config.management.fixed_trailing_increment_points` | ✅ Yes | Trivial |
| 14 | BE breathing room | "Default provisional BE breathing room: 20 points" | Used spec's number verbatim | `config.management.be_breathing_room_points` | ✅ Yes | Trivial |
| 15 | Risk per trade | "$200-$400 depending on setup quality/conviction" | I picked the midpoint, $300, as a flat default since conviction-based scaling isn't built | `config.risk.risk_dollars` — **not yet consumed by position sizing code**, see §4 | ✅ Yes | Trivial to change the number; **replacing the assumption properly means building conviction-based sizing**, which is a real feature, not a config edit |
| 16 | 20% daily account-loss threshold | "exists as a configurable safety limit, do not assume broker/prop-firm rule" | Left as an unenforced informational field, exactly as instructed | `config.risk.daily_account_loss_threshold_percent`, explicitly marked "not yet enforced" | ✅ Yes | Requires writing enforcement logic (not just a config edit) when you're ready |
| 17 | Instrument point values/tick sizes | Not explicitly given, but these are standard CME specs | Used real MNQ/NQ/ES contract specs (not provisional — factual) | `config.execution.instruments.*` — **not consumed by PaperBroker, see §17 below** | ✅ Yes | N/A (factual) but currently disconnected from execution |
| 18 | Commission/slippage estimates | Not specified | I invented placeholder per-instrument values (not sourced from Tradovate's actual fee schedule) | `config.execution.instruments.*.commission_per_contract/estimated_slippage_points` | ✅ Yes | Trivial to update once you have real Tradovate figures |
| 19 | SMT lookback window | Not specified | I invented 20 candles | `config.smt.lookback_candles`, no consumer — SMT detector unwritten | ✅ Yes | Trivial to change; detector itself needs writing regardless |
| 20 | HTF bias lookback | "Default...5 candles...must support 1-12" | Used spec's numbers verbatim | `config.htf.lookback_candles`, `max_lookback_candles` | ✅ Yes | Trivial |

**Bottom line on your specific worry ("none secretly embedded throughout the codebase"):** confirmed clean. Every provisional number lives in exactly one place — `backend/config/schema.py` + `default.yaml` — and nothing else in the codebase currently reads most of them (because the consuming detectors don't exist yet). The two numbers that most deserve your explicit review before Steps 5–6 are **#5** (the 12.5% midpoint I picked) and **#7/#10** (FVG and manipulation-candle numeric defaults that I invented outright, not derived from your spec).

---

## 6. Domain model audit

Relationships as actually defined in `core/models/`:

```
Candle ──────────────┐
                      ├──used to derive──► HTFBiasResult
LiquidityObject ──────┤                          │
ConceptObject ────────┤                          │
MarketStateSnapshot ──┘                          ▼
                                             TradeSetup ◄── SetupCondition (list)
                                                  │              (required + optional confluences)
                                                  │ if decision == TRADE
                                                  ▼
                                              Target (list) ──► referenced by──┐
                                                                                ▼
                                                                          TradeRecord ──► ExitFill, TrailingEvent
```

- `TradeSetup` holds: `htf_bias` (BiasDirection enum, not a full `HTFBiasResult` object — see gap below), a list of `SetupCondition` for both required and optional confluences (each with pass/fail/not-evaluated + a free-text `detail`), `manipulation_ce`, `proposed_stop`, `invalidation_reasons`, `decision`, `reason`, and a full `parameter_snapshot`.
- `TradeRecord` holds: `htf_bias`, `active_confluences`/`failed_confluences` (currently `list[str]`, not `list[ConceptObject]` — a gap, see below), `targets: list[Target]`, `decision_log_ref`, and its own `parameter_snapshot`.

**Can a `TradeSetup` explain WHY a trade was or wasn't taken?**
Mostly yes, with one real gap: `SetupCondition.detail` is free text (`Optional[str]`), so a detector *can* attach human-readable reasoning today, but nothing enforces that detectors populate it richly rather than just "pass"/"fail". `invalidation_reasons` uses the `InvalidationReason` enum (9 defined values), which gives you structured filtering ("show me all NO_TRADEs caused by `NEWS_LOCKOUT`") — that part is solid. The `htf_bias` field being just the enum (`BULLISH`/`BEARISH`/...) rather than the full `HTFBiasResult` means the *supporting/contradicting factors list* from the bias engine isn't captured on the `TradeSetup` itself — you'd need to separately log the `HTFBiasResult` and cross-reference by timestamp. I'd fix this before Step 4 (HTF bias engine) rather than after — see §17.

**Can a `TradeRecord` preserve the exact strategy/configuration state that generated it?**
Yes — `parameter_snapshot: dict[str, Any]` is a full `AppConfig.model_dump()`, and `strategy` + `strategy_version` are separately stored fields. This is implemented and tested (`test_config_parameter_snapshot_is_serializable`, `test_v1_setup_carries_parameter_snapshot`).

**Can historical trades remain reproducible after configuration changes?**
Yes, mechanically — nothing about changing `default.yaml` tomorrow alters a `TradeRecord` saved today, because the full snapshot is embedded at creation time, not referenced by pointer/version-number lookup into a mutable config store. The trade record is self-contained. (Caveat: reproducibility also depends on the *strategy code itself* not changing behavior between versions without a version bump — that's a discipline requirement on you/me going forward, not something the schema can enforce automatically.)

---

## 7. Backtesting readiness

**Can the current architecture support the described pipeline?** Structurally, yes — the types exist to carry data through every stage (`Candle` → aggregation → `MarketStateSnapshot`/`HTFBiasResult`/`ConceptObject`/`LiquidityObject` → `Strategy.evaluate()` → `TradeSetup` → (unbuilt `RiskEngine`) → `PaperBroker.submit_order()` → `TradeRecord`). **None of the aggregation, sequential-replay, or lookahead-prevention logic exists yet** — `BacktestEngine.run()` is a stub that raises `NotImplementedError` on purpose, listing exactly what's missing (I did not fake a "working" backtester).

**How lookahead bias will be prevented (design, not yet built):**

The rule (stated in `docs/BACKTESTING.md` and `Candle.is_closed`) is: a higher-timeframe candle is not "closed" — and therefore not usable for bias/confluence — until its own `close_time` has actually passed in the simulated clock. Concretely, once the aggregation module exists:

> **Example:** replaying 1M bars sequentially, at simulated time `09:47`, the in-progress 1H candle (opened `09:00`, would close `10:00`) exists only as a **partially-formed** object — `Candle(is_closed=False)` — built from the 1M bars seen so far (`09:00`–`09:47`). The HTF bias engine is only allowed to use it as "developing context" (per your spec's own wording), never as a completed candle contributing to `HTFBiasResult.source_candles`. Only once the simulated clock reaches `10:00` does that candle flip to `is_closed=True` and become eligible to feed `HTFBiasResult`. The `Candle` model already has the `is_closed` field for exactly this purpose — it's unused today because no aggregation engine calls it yet, but the field exists precisely so this rule has somewhere to live once Step 2 starts.

I'd treat "does the aggregator ever hand out a `Candle(is_closed=True)` before its real close time" as the very first test written for Step 2, before anything else.

---

## 8. Real-time vs. historical parity

**Can the exact same detectors/strategy/risk/trade-manager be used for both?** Architecturally, yes, and I made a specific design choice to protect this: `Strategy.evaluate(context: MarketContext)` takes a plain dataclass with candles/liquidity/concepts/bias already resolved — the strategy has no idea whether that data came from a historical replay or a live feed. `BacktestEngine.__init__` already takes a `Strategy` instance as a constructor argument (not a subclass or backtest-specific variant), which is the same pattern a not-yet-written `ForwardTestEngine` would use.

**What currently prevents this from being proven, honestly:** nothing exists yet that would let it diverge (no backtest-specific strategy variant, no live-only strategy variant), but also nothing exists yet that has been *tested* running both paths. The risk of divergence in practice usually shows up in the **data feed → MarketContext** translation layer, which is 100% unbuilt. My recommendation: when you build `HistoricalDataFeed` and `RealtimeDataFeed` (Step 2 / Step 11), give them the exact same output type (`MarketContext` or whatever produces it) and write one shared test that feeds identical synthetic data through both and asserts identical `TradeSetup` output — don't wait until Step 11 to find out they've quietly diverged.

---

## 9. PaperBroker audit

| Capability | Status |
|---|---|
| Market orders | **IMPLEMENTED** — `submit_order()` fills immediately against a supplied `reference_price`, with slippage applied only for `OrderType.MARKET`. |
| Limit orders | **NOT IMPLEMENTED** — `OrderType.LIMIT` exists in the enum and is accepted by `submit_order()`'s type signature, but the fill logic doesn't branch on it; a limit order would currently fill like a market order (slippage skipped, since `_apply_slippage` checks `order_type != MARKET`, but there's no queueing/limit-price-respecting logic at all). |
| Stop orders (entry) | **NOT IMPLEMENTED** — same gap as limit orders. |
| Stop loss | **NOT IMPLEMENTED** — `PaperBroker` has no concept of an attached stop; it only fills the single order it's given. |
| Take profit | **NOT IMPLEMENTED** — same. |
| Partial fills | **NOT IMPLEMENTED** — `submit_order()` always fills the full requested `quantity` or rejects entirely (no partial-quantity path). |
| Slippage | **PARTIAL** — implemented for market orders only, as a fixed configurable point offset (`PaperBrokerConfig.slippage_points`), applied deterministically (always adverse to the trader's side). No randomness/volatility-scaling. |
| Commissions | **IMPLEMENTED** — `commission_per_contract * quantity`, deterministic. |
| Rejected orders | **PARTIAL** — only one rejection path exists today (`reference_price is None`). There's no rejection for e.g. exceeding position limits, invalid quantity, or market-closed. |
| Position state | **PARTIAL** — `get_position()` returns a naive net position per symbol; `SimulatedPosition` tracking is a simple dict, and adding to an existing position just increments `quantity` without recalculating a blended `entry_price` (a real gap — averaging in at a new fill price should update `entry_price`, and it currently doesn't). |
| Multiple positions | **PARTIAL** — multiple *symbols* work fine (dict keyed by symbol), but multiple *concurrent positions in the same symbol* (e.g. scaling in) are not correctly modeled, per the entry-price gap above. |
| Order cancellation | **IMPLEMENTED** (trivially) — `cancel_order()` removes the order from an in-memory dict. Since orders fill synchronously and immediately in `submit_order()`, there's currently nothing meaningful to cancel — this method exists for interface completeness, not because there's a pending-order queue yet. |
| Order modification | **NOT IMPLEMENTED** — no modify method exists on `ExecutionBroker` or `PaperBroker` at all. |

**Honest summary:** `PaperBroker` today is a same-tick market-fill simulator with commission math. It does not yet model a resting order book, stop-loss/take-profit attachment, or partial exits — all of which your spec explicitly requires ("PaperBroker must simulate... stop loss, take profit, partial fills..."). This is expected at "Step 9 skeleton, not yet fleshed out" stage, but I want to be direct: if Step 2 proceeds without revisiting this, `PaperBroker` is not yet capable of backing a real backtest of your strategy, which inherently needs stop-loss and target exits.

---

## 10. Risk audit — formulas and real test output

**Formulas actually implemented** (`backend/risk/position_sizing.py`):

```
stop_distance_points = |entry_price - stop_price| + estimated_slippage_points
risk_per_contract     = stop_distance_points * point_value + commission_per_contract
contracts              = floor(max_risk_dollars / risk_per_contract)     # never rounds up
total_risk             = contracts * risk_per_contract
reject if risk_per_contract > max_risk_dollars  (single contract already too big)
reject if entry_price == stop_price              (undefined risk)
```

This matches your spec's formula, extended to also account for slippage and commission (your spec's example formula didn't include those, but the "position sizing must account for... commission... estimated slippage" requirement elsewhere in the spec does — I folded them in).

**Requested test cases, actually executed against the real code** (MNQ, point_value=$2, commission=$0.74/contract, slippage=0.25pt, max risk $300):

| Stop distance | Accepted | Contracts | Risk/contract | Total risk |
|---|---|---|---|---|
| 10 pts | ✅ | **14** | $21.24 | $297.36 |
| 25 pts | ✅ | **5** | $51.24 | $256.20 |
| 50 pts | ✅ | **2** | $101.24 | $202.48 |
| 300 pts (single contract exceeds max) | ❌ | 0 | $1,200.00 (>$300 max) | rejected — `"Single contract risk ($1200.00) exceeds max permitted risk ($300.00)."` |

Note the 10pt case: without slippage/commission it'd be exactly `floor(300/20) = 15` contracts at $300.00 total risk. With slippage+commission folded in, risk/contract rises to $21.24 and it floors to 14 contracts / $297.36 — **strictly under** your $300 cap, confirming the "never exceed configured risk due to rounding" requirement holds even after adding real trading costs.

---

## 11. Daily-loss safety audit

All six limits live in exactly one place — `DailyRiskState` (`backend/risk/daily_limits.py`) — and are **account/session-level, not symbol-level or strategy-level**, because `DailyRiskState` is constructed with a single `RiskConfig` and tracks aggregate counters (`trades_taken`, `wins`, `losses`, `unprofitable_trades`, `realized_pnl_dollars`) with no symbol or strategy dimension at all.

| Limit | Level | Interacts with others how |
|---|---|---|
| Max daily loss ($1,200) | Account-level (one `DailyRiskState` = one account, one day) | Checked every `record_trade_result()` call; locks out regardless of *why* the loss occurred |
| Max consecutive losses | Tracked (`consecutive_losses`), but **not currently used as a lockout trigger** — only `max_losses` (total losses, not consecutive) triggers lockout today. This is a gap versus your spec, which said "After 2 consecutive/max-loss trades: stop trading." | n/a yet |
| Max trades/day (5) | Account-level | Independent trigger — locks out even if all 5 were winners |
| Max unprofitable trades (3) | Account-level | Independent — distinct counter from `losses`; a trade with `pnl <= 0` and not flagged breakeven increments both `losses` and `unprofitable_trades` today, meaning they're currently redundant counters that will always move together unless breakeven trades are involved. Worth deciding if that's intended. |
| Account-level vs strategy-level | **Everything is account-level.** There is no per-strategy or per-symbol `DailyRiskState`. If you run `NyAmHtfContinuationV1` and `FvgInversionConfirmation` simultaneously against the same account, they'd need to share one `DailyRiskState` (not built) or you'd need to decide whether daily limits should be per-strategy — **this is an open design question, not yet answered anywhere in the code.** | |

**Gap to flag explicitly:** your spec says "After 2 consecutive/max-loss trades: stop trading for the day" — the code correctly implements the *max losses* half but not a true *consecutive*-losses lockout trigger (it tracks the counter but never checks it against a threshold). Given `max_losses=2` in the default config, in practice these often coincide, but they are not the same rule, and a scenario like win/loss/win/loss/loss (2 total losses, only 1 consecutive) would lock out under the current "total losses" rule even though it isn't "2 consecutive."

---

## 12. Decision logging audit

**Rejected-trade example** (hypothetical, using the actual `TradeSetup` schema — this is what a real logged record looks like):

```json
{
  "setup_id": "8f2e...",
  "strategy": "NY_AM_HTF_CONTINUATION_V1",
  "strategy_version": "1.0.0",
  "symbol": "MNQ",
  "session": "ny_am",
  "evaluated_at": "2026-08-10T09:42:15-04:00",
  "htf_bias": "bullish",
  "proposed_side": "long",
  "required_conditions": [
    {"name": "valid_htf_bias", "status": "pass", "detail": "1H bias bullish, confidence high, 5/5 candles agree"},
    {"name": "valid_liquidity_context", "status": "pass", "detail": "London High 24,315.50 identified as HTF target"},
    {"name": "valid_manipulation_candle", "status": "pass", "detail": "1M candle 09:41 swept pre-NY low, CE=24,288.25"},
    {"name": "valid_retracement_to_ce", "status": "fail", "detail": "Price has not retraced to manipulation CE (24,288.25); current price 24,295.00"},
    {"name": "risk_engine_approval", "status": "not_evaluated"},
    {"name": "no_execution_lockout", "status": "pass"},
    {"name": "within_execution_window", "status": "pass", "detail": "09:42 within 09:02-11:00"}
  ],
  "optional_confluences": [
    {"name": "htf_fvg", "status": "pass", "required": false, "detail": "1H FVG at 24,280-24,300 overlaps CE"},
    {"name": "smt", "status": "fail", "required": false, "detail": "ES made a new low alongside NQ; no divergence"}
  ],
  "manipulation_ce": 24288.25,
  "proposed_stop": 24270.0,
  "invalidation_reasons": [],
  "decision": "NO_TRADE",
  "reason": "Price has not retraced to manipulation CE.",
  "parameter_snapshot": { "...": "full AppConfig dump" }
}
```

This directly answers "why didn't the bot take this trade" — every required condition's pass/fail state, the specific numeric levels involved, and which optional confluences were present or absent, all in one record, not just a bare `NO_TRADE` string. **This is the target shape** — I want to be clear that today's actual code produces a much sparser version of this (every condition is `NOT_EVALUATED` because no detectors exist), but the schema is already rich enough to hold this once Steps 3–6 populate it.

**Completed-trade example** (same idea, `decision: "TRADE"`, all required conditions `pass`, plus the corresponding `TradeRecord` created afterward holding `entry_price`, `initial_stop`, `quantity`, `targets`, and eventually `exits`/`pnl_dollars`/`exit_reason` as the trade plays out — `TradeRecord.decision_log_ref` links back to this `TradeSetup.setup_id` so the two records are traceable to each other).

---

## 13. Parameter/version reproducibility

`Strategy.version` is a hard-coded class attribute today (`"0.1.0"` on `NyAmHtfContinuationV1`), **not** read from `config.strategy.version` — this is a real inconsistency (see §17). Config-side versioning (`AppConfig.config_version`) and strategy-code-side versioning (`Strategy.version` class attribute) are two separate, currently-unsynchronized numbers.

**Can you distinguish "V1.0 + config A" from "V1.1 + config B" after the fact?** Yes, per individual trade/setup record — each carries `strategy_version` (from the class attribute) and a full `parameter_snapshot` (from `AppConfig`). So even with today's inconsistency, nothing is *lost*: a stored `TradeSetup` unambiguously shows exactly which config values and which code-version string produced it. What's missing is a *single source of truth* tying "code version 0.1.0" to "this exact git commit / this exact detector logic," since `Strategy.version` is just a string I chose to bump manually — there's no automated enforcement that the string actually changes when the underlying detection logic changes.

**Recommendation before Step 2 (not yet fixed, per your instruction to only report):** make `Strategy.version` come from config (`config.strategy.version`) rather than being a separate hard-coded class attribute, so there's exactly one version knob, not two that can silently drift apart.

---

## 14. Dashboard architecture (proposed, not built)

Proposed layering, consistent with your explicit requirement that React never import Python directly:

```
Trading Engine (backend/)
   │
   ▼
Decision/Trade Stores (decision_log/*.py — currently flat-file JSON)
   │
   ▼
API layer (NOT YET BUILT — e.g. FastAPI, mirroring the pattern your
  trading-journal repo already uses) exposing REST endpoints like:
    GET /api/trades?date=...&strategy=...
    GET /api/decisions?date=...
    GET /api/analytics/summary?days=30
    GET /api/status  (live/forward-test bot status, today's P&L, lockout state)
   │
   ▼
Dashboard (React/TS/Vite/shadcn, per trade-vision-replay-lab's stack)
```

The file-backed `DecisionLogger`/`TradeStore` (§Step 1 of this project) are intentionally storage-agnostic in *shape* (they read/write pydantic models), so swapping them for real Postgres-backed stores later (matching your `trading-journal` repo's `psycopg2` pattern) shouldn't require changing the API layer's contract — only the two store classes' internals.

This whole layer — API included — is unbuilt (Step 12). Flagging it now because your instinct to insist on this separation is correct and matches what I'd have recommended anyway.

---

## 15. Existing GitHub projects — actually inspected

I cloned and read both repos (not just the READMEs).

### `trading-journal`
FastAPI + Postgres (Neon) backend with a single static `index.html` frontend, deployed on Railway, with Whop-based auth and Groq-based AI journaling ("mirror" reflections on trading sessions).

**Reference only (do not reuse code, useful as a pattern):**
- Its `db()` context manager pattern (connection-pool-with-health-check-and-auto-recovery) is a reasonable pattern to look at once your dashboard needs a real database, but it's Postgres/psycopg2-specific and tightly coupled to Whop auth — not something to copy wholesale.
- Its `trades` table schema (session_num, setup_type, quality, entry_behavior, mgmt_behavior, execution_score, r_multiple, htf_aligned, liq_flow, chart_levels, dol_levels) is a genuinely useful **reference for what a *discretionary* trade-journal schema looks like** — but it's a manual/subjective journal (a human fills these fields in after the fact), not an automated `TradeRecord`. Some field names (`r_multiple`, `htf_aligned`) validate naming choices already made in your spec.
- Its `analytics()` and `_streak()` functions are a reasonable pattern reference for how you might compute streaks/win-rate later, but they're written against this specific manual schema.

**Should NOT be reused:** the auth/embedding/CORS middleware stack (Whop-iframe-specific, irrelevant to a trading bot dashboard), the AI-journaling/Groq integration, and the schema itself should not be imported directly — your `TradeRecord` is a much richer, machine-generated model and conflating the two would be a regression.

### `trade-vision-replay-lab`
**Important correction to my Step-1 assumption:** despite the name, this is **not** a chart-replay or candle-visualization tool. It's a Supabase-backed **student coaching/quiz platform** — daily reflections, quizzes, admin messaging, user management, feedback forms, role-based access (admin/moderator/user via Postgres RLS). I want to flag this clearly since I described it in Step 1 as a "UI/design reference" without having actually opened it yet; now that I have, here's what's genuinely there:

**Reference only (do not reuse code):**
- The **`ChartViewer/` component pair** (`ChartViewerCard.tsx` + `ChartAnnotator.tsx`) is the one piece close to your use case — but it's a **static-image annotator** (zoom/pan/drag zone-and-point annotations on an uploaded screenshot), not a live OHLCV candle chart or replay engine. Useful as a *pattern* for "how to build a zoomable/pannable annotated image viewer in this stack" if your dashboard ever needs to show annotated chart screenshots (e.g. attached to a `TradeRecord`), but it does nothing resembling candle-by-candle replay.
- `src/components/ui/*` — this is the full shadcn/ui primitive set (accordion, card, dialog, table, tabs, chart.tsx wrapper, etc.), already wired to Tailwind. This is genuinely reusable **as the component library**, exactly as your spec anticipated ("These technologies may be reused for the dashboard").
- `MonthlyOverview.tsx` / `StudentProgress.tsx` are dashboard-page patterns (cards + charts summarizing a user's activity over time) — reasonable *layout* reference for an analytics page, not reusable logic (they query quiz/reflection tables specific to this app).

**Should NOT be reused:** anything Supabase-auth/RLS-specific (`profiles`, `user_roles`, `has_role()`), the quiz/questionnaire/messaging domain logic, and — importantly — **there is no candle-replay or trading-chart code in this repo to reuse at all.** If "deterministic market replay you can watch make decisions candle-by-candle" (your stated goal below) is the target experience, that visualization component doesn't exist yet in either repo and will need to be built from scratch, likely using a real charting library (lightweight-charts, recharts, or similar) rather than adapted from `ChartAnnotator`.

---

## 16. Most important question

**"Reversal strategy using: liquidity sweep → SMT → HTF OB → iFVG → 1M displacement → retracement → entry" — can this run without rewriting market-data, risk, execution, dashboard, or database?**

**YES**, *architecturally*, with one clarification. Every concept it needs (liquidity sweep, SMT, HTF OB, iFVG, displacement) is already modeled as a first-class `ConceptType`/`LiquidityType` in the shared enums, and the `Strategy` interface doesn't care what sequence or combination of detector outputs a strategy consumes — `NyAmHtfContinuationV1` and this hypothetical reversal strategy would both just be classes implementing `evaluate(context: MarketContext) -> TradeSetup`, reading from the same `concepts`/`liquidity` lists on `MarketContext`.

**The clarification:** "without rewriting" is true for market-data, risk, execution, dashboard, and the database/store layer — but it is **not yet true for the detectors themselves**, because none of them exist yet. Today, "yes" is true in the sense that *when* the detectors are built (Steps 5–6), they'll be built once and reused by both strategies — not that a reversal strategy could run *today* without those detectors existing, same as V1 can't run fully today either. I don't want to overclaim readiness that hasn't been exercised yet.

---

## 17. Technical debt / red flags

**CRITICAL**
1. `PaperBroker` does not consume `AppConfig.execution.instruments` — it has its own independent `PaperBrokerConfig` with different defaults. Right now, if you switched `active_symbol` to `NQ` in config, `PaperBroker` would keep simulating with MNQ's `$2` point value unless someone remembers to manually construct a different `PaperBrokerConfig`. This will silently produce wrong P&L math the moment NQ is enabled.
2. `PaperBroker` doesn't model stop-loss/take-profit/partial fills at all (§9) — this blocks any real backtest of a strategy that (per your spec) exits at Multiple targets and manages stops. Needs design attention before Step 9 is "real," not just before Step 10.
3. No `RiskEngine` class exists to connect `TradeSetup` → `calculate_position_size()`/`DailyRiskState` → an actual go/no-go decision. Right now these are three unconnected pieces (strategy skeleton, sizing function, daily-limit tracker) with nothing gluing them together into the single "risk engine" your spec describes.

**HIGH**
4. No strategy registry — `main.py` hard-imports `NyAmHtfContinuationV1` directly; nothing enumerates "which strategies exist / are active," which will matter the moment a second strategy is real (§2, §16).
5. `Strategy.version` (class attribute) and `config.strategy.version` (config field) are two independent, unsynchronized version numbers (§13).
6. `TradeSetup.htf_bias` stores only the `BiasDirection` enum, not the full `HTFBiasResult` (with its supporting/contradicting factors) — weakens the "explain why" goal from §6/§12 unless you separately correlate by timestamp.
7. Consecutive-loss lockout is tracked but not actually enforced as its own trigger — only aggregate `max_losses` triggers lockout today (§11), which doesn't match your spec's literal "2 consecutive" wording.
8. `TradeRecord.active_confluences`/`failed_confluences` are `list[str]`, not structured objects — fine for a human-readable log line, weaker for a dashboard that wants to filter/aggregate by confluence type.

**MEDIUM**
9. `risk.risk_dollars`/`risk_percent`/`account_size` aren't yet wired from `AppConfig` into `calculate_position_size()` — currently that function only works if something explicitly passes the numbers in (§4, §10 — the math itself is correct and tested, just not yet connected to config).
10. `unprofitable_trades` and `losses` counters in `DailyRiskState` currently always move together (every non-breakeven loss increments both) — worth deciding if that's intentional redundancy or if `unprofitable_trades` was meant to mean something distinct (e.g. including scratch/near-breakeven losers that don't count as full "losses").
11. `PaperBroker`'s position-averaging on repeated fills to the same symbol doesn't recompute a blended `entry_price` (§9) — fine while nothing scales in, will produce wrong average cost the moment it does.

**LOW**
12. `docs/ASSUMPTIONS.md` row 5 (wick-rejection midpoint) and rows 7/10 (FVG/manipulation defaults) are numbers I chose, not numbers you gave — flagged again here for visibility, low urgency since they're isolated and trivially editable (§5).
13. No `.env.example` or documented environment-variable list yet for future Tradovate credentials, even though `docs/ARCHITECTURE.md`/spec both call for env-var-only credential handling — not urgent since `TradovateBroker` is unimplemented anyway.

---

## 18. Final recommendation

**A. Genuinely ready for Step 2 (market-data ingestion) as-is:**
- `Candle` model (including the `is_closed` field needed for lookahead-bias prevention)
- Config system (schema + loader + versioning-via-snapshot pattern)
- `DecisionLogger`/`TradeStore` file-backed persistence
- Test framework/conventions

**B. Only scaffolded (shape exists, no working logic):**
- `Strategy` interface + both strategy skeletons
- `ExecutionBroker` interface + `PaperBroker` (partial — market orders + commission only)
- `BacktestEngine` (intentionally raises `NotImplementedError`)
- Every field in `config.htf/liquidity/fvg/ote/manipulation/smt/management/news` (validated, unconsumed)

**C. Should be changed before Step 2 (architecture-level, not just "nice to have"):**
- Fix `PaperBroker` ↔ `AppConfig.execution.instruments` disconnect (Red flag #1) — otherwise every future step builds on a broker that silently ignores config.
- Introduce a minimal `RiskEngine` class that actually connects `TradeSetup` → position sizing → `DailyRiskState` (Red flag #3) — right now these three pieces don't know about each other, and Step 2 shouldn't add a fourth disconnected piece (market data) to a chain that doesn't have a working middle yet.
- Decide and fix `Strategy.version` vs `config.strategy.version` duplication (Red flag #5) before more strategy versions exist to make it worse.
- Add a strategy registry (Red flag #4) — small now, annoying to retrofit once V2 exists.

**D. Can safely wait:**
- `TradeSetup.htf_bias` richness (#6), confluence structuring (#8), consecutive-loss enforcement exactness (#7), position-averaging correctness (#11) — all real, but none block Step 2's actual work (market data ingestion doesn't touch any of them).
- Dashboard/API layer entirely (Step 12 is explicitly last).
- Tradovate env-var scaffolding (#13) — no live broker exists to need it yet.

**E. Assumptions you should personally review before Steps 5–6:**
- ASSUMPTIONS.md row 5 (12.5% wick-exceeds-body midpoint)
- ASSUMPTIONS.md row 7 (FVG min size 1.0pt / max age 200 candles — invented, not derived from your spec)
- ASSUMPTIONS.md row 10 (all 8 manipulation-candle parameter defaults — invented, not derived from your spec; this is your most important strategy concept, most worth a real second look)
- ASSUMPTIONS.md row 15 ($300 flat risk as the midpoint of your $200–$400 range, pending real conviction-based sizing)

**F. Recommended order, revised:**

Given your closing note — you want a **deterministic replay engine you can watch make decisions candle-by-candle** before a polished dashboard — I'd sequence it as:

1. **Fix the three CRITICAL/HIGH items in (C) above** — small, contained changes to existing scaffolding, not new features.
2. **Market data ingestion + timeframe aggregation** (your original Step 2), with the no-lookahead test (§7) written first, before any detector logic.
3. **Sessions + liquidity** (Step 3) — gets you PDH/PDL/session highs-lows, which are the simplest detectors and a good pipeline smoke-test.
4. **A minimal, ugly, no-styling replay CLI** — step through 1M bars, print `MarketContext` state + whatever `TradeSetup` the (still mostly-skeleton) strategy produces, one candle at a time. This is cheap to build once (2)+(3) exist, and gives you exactly the "watch it think" feedback loop you described — well before HTF bias, manipulation detection, or the dashboard are ready. I'd insert this before Step 4, not after Step 12.
5. Continue HTF bias → concept detectors → manipulation detector → Strategy V1 real logic (Steps 4–7), validating each one visually against the replay CLI as it lands.
6. Risk engine wiring (fixing C above *is* most of this), then real `PaperBroker` stop/target/partial-fill support (Step 9 properly).
7. Backtest engine (Step 10) — by this point it's mostly assembling pieces already validated individually via the replay CLI.
8. Forward-testing (Step 11), dashboard (Step 12) — unchanged from your original ordering.

I have not made any of the changes in (C) — awaiting your review.
