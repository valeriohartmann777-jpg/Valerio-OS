# THEORY_ICT — ICT / Smart Money Concepts

Written 2026-10-07, **before any market data was seen**. Statements about effects are
priors. ICT terminology is treated as a set of claims about market behaviour, not as a
description of what institutions actually do.

## 0. Sources and scope

* "ICT" (Inner Circle Trader) material is taught mainly through videos and mentorship
  programmes; "Smart Money Concepts" (SMC) is the retail derivative. There is no
  peer-reviewed literature on these concepts, and definitions vary between teachers.
  For every term this document picks the most common definition and makes it
  deterministic. Where the community disagrees, the variants become separate,
  pre-registered test arms rather than a free choice made after seeing results.
* Academic work on the mechanisms ICT invokes (priors only):
  * Osler (2003, *Journal of Finance*), "Currency orders and exchange rate dynamics":
    take-profit orders cluster at round numbers (→ reversals there); stop-loss orders
    cluster just beyond them (→ trend acceleration when crossed).
  * Osler (2005, *J. International Money and Finance*), "Stop-loss orders and price
    cascades": triggered stop-loss orders produce rapid, self-reinforcing moves.
  * Kavajecz & Odders-White (2004, *Review of Financial Studies*): technical support and
    resistance levels coincide with peaks of limit-order-book depth.
* **The central tension.** Osler's evidence says crossing a level where stops cluster
  tends to **accelerate** price (continuation). ICT says stops are "engineered" and
  taken before a **reversal** (sweep). Both claims are testable on the same events, and
  the answer may depend on how far price runs beyond the level and whether it reclaims.

## 1. The underlying ideas translated into market phenomena

| ICT idea | market phenomenon | observable with 1m OHLCV? |
|---|---|---|
| liquidity pools (buy-side above highs, sell-side below lows) | resting stop orders beyond obvious extremes | **not directly** (no order book); only price/volume reaction at presumed levels |
| sweep / stop hunt | stops triggered, market orders absorbed, price returns | yes: penetration depth, reclaim time, close location |
| liquidity run | stops triggered, cascade continues | yes: continuation beyond the level |
| displacement | aggressive one-sided execution | yes: range/ATR, body/ATR, close location, volume percentile |
| imbalance / FVG | one-sided execution leaves a 3-bar gap | yes: exact definition |
| mitigation, order blocks | institutions return to fill remaining orders at the origin of a move | yes as a price event; the institutional story is not observable |
| market structure shift | change of swing sequence after displacement | yes, with an explicit confirmation delay |
| killzones | time-of-day concentration of activity | yes; but intraday volatility is U-shaped regardless |
| SMT divergence | correlated markets disagree at an extreme | yes once ES and NQ are both available, timestamp-aligned |

A first, cheap test of the "liquidity" premise: if stops cluster beyond prior-day
highs/lows and equal highs/lows, the minute a level is crossed should show a volume and
range spike relative to time-matched controls and to random levels. If it does not,
the "liquidity pool" vocabulary has no measurable footprint in this data.

## 2. Concept decomposition

### 2.1 Buy-side / sell-side liquidity, external vs internal liquidity
* **Claim.** Stops of shorts rest above swing highs (buy-side liquidity, BSL), stops of
  longs below swing lows (SSL). External liquidity = beyond the dealing-range extremes;
  internal = inside it (FVGs, minor swings). Price "draws" toward liquidity.
* **Observable.** Candidate liquidity levels: completed prior-day RTH and full-session
  high/low, overnight high/low (available 09:30), prior-week high/low, confirmed swing
  pivots (`find_pivots`, known `right` bars later), equal highs/lows (2.3).
* **Why edge.** If stops cluster there, crossings create forced order flow.
* **Who.** Retail and systematic stop placement; liquidity providers who know it.
* **Simpler.** Prior extremes are simply the nearest support/resistance (family C);
  "draw on liquidity" may just restate that price eventually trades through nearby
  extremes, which it does frequently by random-walk arithmetic.
* **Test.** Footprint test above; P(level taken within the session) vs random levels
  at the same distance.

### 2.2 Prior-day, overnight and prior-week highs/lows
* Same observables as THEORY_AUCTION 3.12. **Test.** Are sweep/run outcomes at these
  levels different from the same event at random levels and at ordinary swing pivots?

### 2.3 Equal highs / equal lows
* **Claim.** Two or more highs at nearly the same price are "engineered liquidity",
  more attractive targets than a single high.
* **Observable (deterministic).** Two confirmed pivot highs *i < j* with
  |H_i − H_j| ≤ tol × ATR_j, separated by ≥ m bars, with a pullback between them of
  ≥ r × ATR (`equal_levels(tol_atr, min_sep_bars, min_reaction_atr)`). Known when the
  second pivot is confirmed. Pre-registered values: tol 0.10 ATR, m 5 bars,
  r 1.0 ATR; sensitivity 0.05 / 0.20 ATR.
* **Simpler.** A double top is an ordinary resistance with two touches (family C).
* **Test.** Equal-high sweeps vs single-pivot sweeps of matched age and distance.

### 2.4 Liquidity sweep (stop hunt) — formulations
* **Claim.** Price trades beyond a liquidity level, takes stops, and reverses.
* **Observable, four pre-registered variants** (penetration measured in ATR):
  * *wick sweep*: high > L + p·ATR and the same bar closes back below L;
  * *close sweep*: a close beyond L, then a close back inside within N bars;
  * *reclaim sweep*: wick or close beyond, then a close back inside **and** beyond the
    opposite side of the sweep bar's body within N bars;
  * *multi-bar failed breakout*: ≥ 2 closes beyond, then a close back inside within
    N bars.
  Variables recorded per event: penetration depth, bars beyond, maximum continuation
  before the reclaim, close location of the reclaim bar, time of day, distance to the
  next opposite liquidity level.
* **Why edge.** Forced stop orders are absorbed by larger limit orders; once absorbed,
  the imbalance is spent.
* **Simpler.** This is a failed breakout. Short-horizon mean reversion after a new
  extreme is a well-known property of range-bound intraday markets.
* **Test.** Each variant vs its PA twin (failed breakout of a C-family S/R zone) and vs
  random levels; continuation (run) outcomes are measured on the same events.

### 2.5 Liquidity run
* **Claim.** When price takes liquidity with displacement and holds beyond, it runs to
  the next liquidity pool.
* **Observable.** Crossing of L with a close beyond and no reclaim within N bars.
* **Simpler.** Breakout continuation / momentum (family C).
* **Test.** Outcomes conditional on "no reclaim within N" vs time-matched controls.
  Conditioning on no reclaim is a survivorship filter: the entry time is the bar where
  "no reclaim" became known, never earlier.

### 2.6 Displacement
* **Claim.** A strong, large-bodied move away from a level signals institutional
  participation.
* **Observable.** Candidate metrics (`candle_features`): range/ATR, body/ATR, body /
  rolling median body, close location value, volume percentile. Pre-registered
  definition: body ≥ 1.5 × rolling median body **and** close location ≥ 0.75 in the
  move direction; variants by range/ATR ≥ 1.5 and volume percentile ≥ 0.8.
* **Simpler.** A large bar is high short-term volatility; follow-through after large
  bars is weak or negative at short horizons in liquid futures.
* **Test.** Forward returns after displacement bars vs after equally large bars
  without the preceding sweep (does the sweep context matter?).

### 2.7 Market structure: BOS, CHoCH, MSS
* **Claim.** Break of structure (BOS) continues a trend; change of character (CHoCH) /
  market structure shift (MSS) is the first break against it and signals reversal.
* **Observable.** Swings from `find_pivots(left, right)`; a break = close (or wick,
  variant) beyond the last confirmed swing (`structure_breaks(mode)`). The swing is
  usable only after its `right` confirmation bars; MSS = break against the prevailing
  `structure_trend`.
* **Simpler.** A close beyond a recent swing is a short-term momentum signal; its
  information content may be the same as an N-bar breakout.
* **Test.** MSS alone vs sweep + MSS vs sweep + displacement + MSS (ablation tree, 3.).

### 2.8 Fair Value Gap and inverse FVG
* **Claim.** A bullish FVG (high of candle 1 < low of candle 3) is an inefficiency that
  price returns to "rebalance", then continues in the FVG direction. An inverse FVG is
  an FVG that price closed through; it now acts in the opposite direction.
* **Observable.** `detect_fvgs` (known at candle 3's close), width in ticks and ATR;
  `fvg_outcomes`: partial fill, full fill, time to fill, return after fill;
  `fvg_state` with invalidation by close-through and a maximum age.
* **Why edge.** One-sided execution may leave unfilled interest that is completed on
  a revisit.
* **Simpler.** An FVG is a fast three-bar move. "Fill" is a retracement, which follows
  most fast moves anyway. Continuation after the fill may equal the base rate of
  continuation after any strong bar.
* **Test.** Dedicated FVG study: fill rates and times vs matched displacement bars
  without a gap; forward returns after first touch of the gap vs after a pullback of the
  same depth into a non-FVG bar; by width / ATR tercile, regime, location in range, and
  after sweeps.

### 2.9 Order block, breaker block, mitigation block
* **Claim.** Order block (OB) = last opposite-colour candle before a displacement;
  price returns to it and reacts. Breaker = a failed OB, broken through, reused in the
  opposite direction. Mitigation block = similar, without a sweep.
* **Observable.** OB: the last down-close bar before a bullish displacement bar whose
  move breaks a swing high; zone = that bar's [low, high] (variant: [low, open]).
  Known only when the displacement completes.
* **Simpler.** The origin of an impulse is a pullback support (family C), and a
  retracement of a given depth may matter more than the specific candle.
* **Test.** First return to an OB zone vs a pullback to a zone of the same depth and
  width chosen at random inside the impulse. **Prior: no difference.**

### 2.10 Premium, discount, equilibrium, dealing range, OTE
* **Claim.** Inside a dealing range, buy in discount (below 50%) and sell in premium;
  OTE (optimal trade entry) is the 62–79% retracement of the last leg.
* **Observable.** Dealing range = the last two confirmed opposite swings
  (`swing_state`), never hindsight endpoints. Location = (price − low) / (high − low).
* **Simpler.** Position in range is a mean-reversion oscillator (like %K); its effect
  should vanish once trend is controlled for. Fibonacci ratios have no mechanism
  beyond self-fulfilment.
* **Test.** Forward return by location decile within the dealing range, within trend
  terciles. OTE: compare 0.62–0.79 retracement entries with 0.38–0.50 and 0.50–0.62
  retracement entries. **Prior: monotone or flat, no peak at Fibonacci levels.**

### 2.11 Killzones and session timing
* **Claim.** Setups work in killzones (London 02:00–05:00 ET, NY AM 08:30–11:00 ET,
  NY PM 13:30–16:00 ET) and fail outside them.
* **Observable.** Time-of-day buckets from `sessions.yaml`.
* **Simpler.** Volume and volatility are U-shaped through the day; any event looks
  "stronger" when volatility is higher. Normalising by ATR at the event and using
  time-matched controls removes this.
* **Test.** Interaction of event × time bucket on ATR-normalised outcomes, broad buckets
  only.

### 2.12 Judas swing, Power of Three (accumulation, manipulation, distribution)
* **Claim.** The session opens, makes a false move against the day's true direction
  (manipulation, "Judas swing"), then trends (distribution).
* **Observable (causal version).** Within the first 30–60 RTH minutes price moves away
  from the RTH open by ≥ k ATR, then closes back beyond the open; enter in the
  reclaim direction. The "true direction" is never used as an input.
* **Simpler.** An opening-range failure / open-rejection-reverse (THEORY_AUCTION 3.9).
* **Test.** Same event as the AMT open-rejection-reverse; compare the definitions.

### 2.13 Opening range
* As THEORY_AUCTION 3.8 / 3.9; ICT uses it as a liquidity reference (sweep of the
  opening range high/low). Test as 2.4 with the opening range as L.

### 2.14 SMT divergence
* **Claim.** When ES makes a lower low but NQ does not (or vice versa), the move is
  weak and reverses.
* **Observable.** Same-minute bars of ES and NQ; at a new ES swing low below the last
  confirmed ES low, NQ's low over the same window stays above its own last confirmed
  low. Requires both datasets on an identical timestamp grid.
* **Simpler.** Relative strength; divergence events are rare and the two series are
  highly correlated, so the sample may be small.
* **Test.** Sweep outcomes with vs without SMT divergence. Deferred until NQ data
  exists; noted in `REJECTED_IDEAS.md` as blocked, not rejected.

### 2.15 Daily bias and draw on liquidity
* **Claim.** A daily bias (from higher-timeframe structure) tells which liquidity will
  be taken; trade only toward it.
* **Observable.** Bias = sign of the daily `structure_trend` or of the prior-day close
  vs prior value; draw = nearest untaken liquidity level in the bias direction.
* **Simpler.** A trend filter; the draw on liquidity is a target, not a predictor.
* **Test.** Used as a context filter in ablations only if it adds expectancy over the
  unfiltered event, and as a target family in exits.

## 3. Mandatory ablation tree (from the mission)

B0 liquidity event only → B1 + reclaim → B2 + displacement → B3 + MSS → B4 + FVG entry →
B5 + session condition. Each step must add expectancy **and** robustness on DEV and
VAL; a step that only adds DEV return is removed. Every step is also run with the PA
twin event (failed breakout of a C-family zone) to see whether the ICT label adds
anything beyond plain price action.

## 4. Priors before data

1. **Footprint**: a measurable volume/range spike at the first crossing of prior-day
   extremes is likely (many participants reference them). At equal highs: uncertain.
2. **Sweep vs run**: genuinely uncertain. Microstructure evidence favours continuation
   for clean crossings; reversal is plausible only after a deep reclaim. Expect
   reclaim-type definitions to separate the two better than wick sweeps.
3. **Displacement and MSS** add little once the sweep and reclaim are known (both are
   largely re-descriptions of the same bars).
4. **FVGs** fill often (most fast moves retrace); continuation after a fill is unlikely
   to beat time-matched controls by more than costs.
5. **Order blocks, OTE, premium/discount**: no incremental information expected beyond
   trend and retracement depth.
6. **Killzones**: apparent effects should mostly disappear after ATR normalisation.

## 5. Consequences for the hypothesis list

* Every B hypothesis has a PA twin control and a random-level control.
* Variants (wick / close / reclaim / multi-bar) are pre-registered arms; the number
  of arms is counted in the multiple-testing registry.
* The institutional narrative is never used as evidence; only distribution shifts are.

The ranked hypotheses are in `hypotheses/HYPOTHESES_ICT.md`.
