# Glossary

Trader-supplied definitions are authoritative for V1 (spec rule #6).
External ICT/TTrades terminology is used only to aid understanding, not to
silently override the trader's definitions. Items marked **PROVISIONAL**
have an implementation-specific rule documented in `docs/ASSUMPTIONS.md`.

## Structure & liquidity

- **PDH / PDL** — Previous Day High / Low: highest/lowest wick of the previous *completed* daily candle.
- **PWH / PWL** — Previous Week High / Low: same, for the previous completed weekly candle.
- **Equal High / Equal Low** — Multiple highs/lows refusing to be violated, within a tolerance. **PROVISIONAL** tolerance.
- **Internal liquidity** — Lower-timeframe/internal candle structure.
- **External liquidity** — Higher-timeframe/external candle structure.
- **Swing high / swing low** — Local structural pivot points.
- **Liquidity sweep** — Price trades through a liquidity level (wick or close) and then reacts.

## Market state (per timeframe — a single global state is never assumed)

- **Trending up/down** — Market heading toward an HTF target despite lower-timeframe chop.
- **Accumulation** — Repeated internal-liquidity sweeps preparing for distribution.
- **Distribution** — Market delivers toward a target/imbalance, usually following accumulation.
- **Expansion** — High-volatility environment; liquidity may be swept both directions.
- **Retracement** — Market rebalances toward premium/discount after a move toward a target.
- **Manipulation** — Sudden move that traps participants and creates liquidity for a rapid move toward a target.
- **Consolidation** — Choppy behavior after distribution, before reversal/continuation.
- **Reversal** — Market rejects an area, invalidates prior-trend confluences, and continues opposite.

## Confluence concepts

- **FVG (Fair Value Gap)** — Price imbalance between candles; CE = midpoint of the gap unless otherwise configured.
- **iFVG (Inverted FVG)** — An FVG that has failed and flipped role; treated as a strong reversal/confluence signal, particularly after sweeps or HTF OB reactions. **PROVISIONAL** inversion rule.
- **OB (Order Block)** — Candle(s) preceding a displacement, used as a reaction zone. **PROVISIONAL** definition.
- **Breaker** — A failed OB that flips role. Confluence only in V1 — never triggers a trade independently.
- **OTE (Optimal Trade Entry)** — Retracement zone of a leg, using configurable Fibonacci levels (default 0.62 / 0.705 / 0.79).
- **CE (Consequent Encroachment)** — Midpoint of a range (FVG, or the 1M manipulation candle in V1's primary entry model).
- **BPR (Balanced Price Range)** — Overlap of opposing FVGs.
- **Volume imbalance** — A gap-like imbalance identified by volume/body overlap rather than wick gap.
- **SMT (Smart Money Technique) divergence** — Divergence between correlated instruments (NQ/MNQ vs. ES in V1) at a shared liquidity point.
- **Displacement** — A strong, high-momentum move, often following a manipulation or liquidity sweep.
- **Rejection** — Price approaches a level and closes away from it, evidencing supply/demand imbalance.
- **Engulfing** — A candle whose body (or full range) subsumes the prior candle's body (or range). **PROVISIONAL** thresholds.

## Strategy-specific

- **Manipulation candle** — The core V1 concept: a 1M candle representing counter-trend movement (often sweeping liquidity) after which price is expected to reverse toward the HTF-bias direction. **Fully PROVISIONAL** — see `docs/ASSUMPTIONS.md`.
- **HTF bias** — Directional expectation (bullish/bearish/neutral/transitional) derived from completed 1H candles.
- **Manipulation CE** — Midpoint of the manipulation candle's range; the primary V1 entry reference.
- **NY AM session** — New York morning session, `09:00`–`11:00` (entry window, configurable) America/New_York.
