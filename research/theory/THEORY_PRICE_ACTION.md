# THEORY_PRICE_ACTION — Classical price action, support and resistance

Written 2026-10-07, **before any market data was seen**. Statements about effects are
priors. This family is both a strategy philosophy of its own and the **complexity
benchmark** for families A and B: if a plain price-action rule does as well as an
AMT or ICT rule on the same events, the extra vocabulary adds nothing.

## 0. Sources and scope

* Classical technical analysis (Edwards & Magee, *Technical Analysis of Stock Trends*,
  1948) and intraday bar-by-bar price action as taught by discretionary futures traders.
* Academic evidence (priors only):
  * Brock, Lakonishok & LeBaron (1992, *Journal of Finance*): moving-average and
    trading-range-break rules appeared profitable on the DJIA 1897–1986.
  * Sullivan, Timmermann & White (1999, *Journal of Finance*): after correcting for data
    snooping across the whole rule universe, the best rules do not hold up out of sample
    after 1986. **The lesson for this lab: count every rule tried.**
  * Osler (2000, *FRBNY Economic Policy Review*): support and resistance levels published
    by banks predicted intraday trend interruptions in FX better than random levels.
  * Osler (2003, *Journal of Finance*): take-profit orders cluster at round numbers
    (reversal), stop-loss orders just beyond them (acceleration).
  * Kavajecz & Odders-White (2004, *Review of Financial Studies*): S/R levels coincide
    with peaks in limit-order-book depth.
  * Lo, Mamaysky & Wang (2000, *Journal of Finance*): some chart patterns carry modest
    incremental information.
  * Park & Irwin (2007, *Journal of Economic Surveys*): many positive results, much of
    it vulnerable to data snooping and cost assumptions.
* Prior for the whole family: **small edges at best on liquid index futures, easily
  consumed by costs; the most credible mechanism is order clustering at obvious levels.**

## 1. Mechanisms that could make price action work

1. **Order clustering at obvious levels.** Limit orders rest at prior extremes and round
   numbers (reaction, S/R); stop orders rest just beyond them (acceleration, breakouts).
2. **Anchoring and break-even behaviour.** Traders anchor on prior highs and lows; those
   who bought a failed high sell at break-even when price returns there (resistance).
   **Polarity:** after a breakout, shorts who sold the old resistance cover and late
   buyers buy the retest, so the old resistance becomes support.
3. **Volatility clustering.** Compression is followed by expansion. This predicts the
   **size** of the next move, not its direction.
4. **Momentum and trend persistence.** Trend days reinforce themselves through stops and
   momentum algorithms; ordinary intraday time-series momentum is weak.
5. **Inventory mean reversion.** Liquidity providers lean against short-term moves,
   producing small reversals at short horizons in balanced conditions.

## 2. Concept decomposition

### 2.1 Support, resistance, horizontal levels, polarity
* **Claim.** Prices where the market previously reversed attract reactions again; once
  broken, support and resistance swap roles.
* **Observable.** Levels from confirmed pivots (`find_pivots`, known `right` bars after
  the pivot), clustered into zones of width k·ATR (`pivot_zones`), plus prior-day and
  weekly extremes and round numbers (`round_number_levels`). A touch is defined by the
  state machine in `level_touches`: a level is armed only when price is on its correct
  side, a touch starts when price enters the zone, is confirmed when price moves away
  by a minimum distance, and one visit counts once; a close beyond by `break_atr`
  breaks it.
* **Why edge.** Order clustering (mechanism 1), anchoring (2).
* **Simpler.** Price oscillates; any line gets touched and "reacts". Only a difference
  from random levels counts as evidence.
* **Test.** Touch outcomes vs random levels at matched distances; polarity: retests of
  broken levels from the other side vs random levels.

### 2.2 Swing highs and lows, higher highs / lower lows, trend
* **Claim.** A sequence of higher highs and higher lows is an uptrend; trade with it.
* **Observable.** `find_pivots(left, right)` or the causal ATR zig-zag
  (`zigzag_pivots`), `structure_breaks`, `structure_trend`.
* **Simpler.** A trailing return or moving-average slope.
* **Test.** Does swing-based trend state predict forward returns beyond a 20-bar
  return sign? Used mainly as a context variable.

### 2.3 Previous-day levels, session highs and lows, range boundaries
* As THEORY_AUCTION 3.12 / THEORY_ICT 2.2. PA asks the same question with plain
  words: do these levels outperform local swing levels and random levels?

### 2.4 Breakout
* **Claim.** A close beyond resistance with strength continues.
* **Observable, pre-registered variants.** Wick breakout (high > L), close breakout
  (close > L), ATR breakout (close > L + 0.25 ATR), volume breakout (close breakout with
  bar volume ≥ 80th percentile of its time-of-day), strong-body breakout (body ≥ 1.5 ×
  rolling median body, close location ≥ 0.75).
* **Simpler.** Momentum: any large up bar.
* **Test.** False-break rate (close back inside within N bars) and forward returns for
  each variant vs time-matched controls and vs the same bar-strength filter without a
  level.

### 2.5 Failed breakout
* **Claim.** A breakout that fails reverses strongly because trapped traders exit.
* **Observable.** Close breakout, then a close back inside within N bars.
* **Simpler.** Identical event to the ICT sweep and the AMT failed auction.
* **Test.** Shared event library; compare reversal expectancy with an ordinary support
  touch (the mission asks this explicitly).

### 2.6 Retest, pullback, first pullback
* **Claim.** After a breakout, the first return to the broken level holds (polarity);
  the first pullback in a new trend is the best entry.
* **Observable.** Retest = within M bars of a breakout, low ≤ L + tolerance·ATR with no
  close below L − break·ATR; first vs second retest vs no retest recorded.
  First pullback = first counter-trend swing after a structure break.
* **Simpler.** Waiting for a pullback changes the entry price, not the predictive event.
  Missed trades (no retest) must be counted, otherwise the retest entry looks better
  than it is.
* **Test.** Entry-method comparison on the same breakout events: immediate entry vs
  retest limit vs 50% retracement, with missed-trade accounting.

### 2.7 Compression, expansion, volatility contraction
* **Claim.** Compression (declining ATR, narrowing ranges, inside bars, higher lows into
  resistance) precedes successful breakouts.
* **Observable.** ATR percentile within its trailing window (`trailing_percentile`),
  N-bar range / ATR, count of inside bars, slope of swing lows into a flat high.
* **Simpler.** Volatility mean reversion predicts a larger move, direction 50/50.
* **Test.** Compression → |forward return| (magnitude) and → directional success of the
  breakout vs non-compressed breakouts. Only a directional difference supports a
  strategy.

### 2.8 Momentum
* **Claim.** Strong recent moves continue.
* **Observable.** N-bar returns in ATR units, efficiency ratio.
* **Simpler.** It is the simple alternative for most other concepts.
* **Test.** Baseline benchmark (section 4).

### 2.9 Candle features: close location, wicks, inside/outside bars, engulfing
* **Claim.** Long lower wicks and high close location at support show rejection;
  engulfing bars reverse; inside bars compress.
* **Observable.** `candle_features`: close location value, upper/lower wick share,
  body/ATR, inside/outside flags, engulfing (body of bar t covers body of bar t−1 in the
  opposite direction).
* **Simpler.** At 5-minute resolution in ES, single-bar patterns are mostly noise;
  close location may carry short-horizon continuation or reversal information.
* **Test.** Only as triggers at a pre-defined location (support touch), compared with
  the location alone. No pattern is tested in isolation over all bars.

### 2.10 Multiple touches, level strength, level weakening
* **Claim.** Either repeated touches strengthen a level (more proof) or weaken it (orders
  consumed). Recent levels beat old ones; large reactions mean strong levels.
* **Observable.** `touch_number`, `reaction_atr`, level age in sessions, time between
  touches.
* **Test.** One variable at a time, as the mission requires: first vs second vs third+
  touch; age terciles; reaction-size terciles. No combined "strength score" until each
  component shows information on its own.

### 2.11 Channels and trendlines
* **Claim.** Prices respect sloped lines drawn through swing points.
* **Observable.** A trendline is objectively definable only as the line through the
  last two confirmed swing lows (highs) with a minimum separation, extended forward;
  a touch is a bar low within tolerance·ATR of the extended line.
* **Simpler.** Equivalent to a trend filter plus pullback depth.
* **Test.** Low priority; included only if the horizontal-level results justify it.

### 2.12 ATR and volume
* ATR is the normaliser for all distances and the base of volatility regimes, not a
  signal. Volume is used relative to its own time-of-day distribution (raw volume is
  dominated by the U-shape) and as an explicit filter arm, never silently.

## 3. Level construction alternatives (compared, not assumed)

| method | definition | known at |
|---|---|---|
| pivot levels | confirmed fractal highs/lows (`left`, `right` bars) | pivot bar + `right` bars |
| swing clustering | pivots within k·ATR merged into zones (`pivot_zones`) | when the last pivot of the zone is confirmed |
| ATR zig-zag | causal reversal of ≥ k·ATR (`zigzag_pivots`) | at the reversal bar |
| prior-day extremes | completed RTH / full-session high and low | scheduled session end |
| weekly extremes | completed prior week high and low | scheduled week end |
| round numbers | multiples of 25 / 50 / 100 index points | always |
| random levels | price ± distance drawn from the real levels' distance distribution | control |

## 4. Simple baselines every strategy in this lab must beat

1. Intraday buy-and-hold: long at the RTH open, flat at 15:55.
2. Random entry and random direction with the candidate's exact exit rules.
3. Previous-day breakout: stop entry one tick beyond the prior RTH high/low.
4. VWAP mean reversion: fade 2 SD band closes, target VWAP.
5. Simple EMA trend system on 5-minute bars (fast/slow crossover, session-flat).

If an A or B candidate barely beats these, the report says so.

## 5. Priors before data

1. Prior-day and weekly extremes show more reaction than random levels (moderate
   prior), most of it on the first touch.
2. Breakouts: false-break rates are high; close breakouts with strength continue slightly
   more than controls; after costs, uncertain.
3. Failed breakouts reverse more than an ordinary touch (moderate prior), consistent
   with trapped-trader and stop-cascade mechanics.
4. Polarity: weak positive prior on first retests only.
5. Compression predicts magnitude, not direction (strong prior).
6. Candle patterns add little beyond location (strong prior).

## 6. Consequences for the hypothesis list

* Every C hypothesis has a random-level control and, where it matters, a magnitude vs
  direction split.
* Entry methods are compared on identical predictive events, with missed trades
  counted.
* The baselines in section 4 are computed on the same periods and costs for every
  candidate in every family.

The ranked hypotheses are in `hypotheses/HYPOTHESES_PRICE_ACTION.md`.
