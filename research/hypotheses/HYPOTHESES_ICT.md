# HYPOTHESES_ICT — Family B (ICT / Smart Money Concepts)

Pre-registered 2026-10-07, **before any market data was seen**. Event definitions,
parameters, primary tests and expectations are frozen; later changes get new IDs and a
journal entry. Theory: `theory/THEORY_ICT.md`. Common definitions (bars, ATR, ATR_d,
decision timing, controls, session bounds) are those of `HYPOTHESES_AUCTION.md` and
`RESEARCH_PROTOCOL.md`. Machine-readable mirror: `configs/event_studies.yaml`.

## Family-specific definitions

* **Liquidity levels.** PDH/PDL = completed prior RTH high/low; ONH/ONL = overnight
  (18:00–09:30) high/low, known at 09:30; PWH/PWL = completed prior-week high/low.
* **Swings.** Fractal pivots with left = right = 3 on 5m bars, usable 3 bars after the
  pivot bar (`find_pivots`).
* **Equal highs/lows.** Two confirmed pivot highs (lows) within 0.10·ATR of each
  other, ≥ 5 bars apart, with a pullback ≥ 1.0·ATR between them (`equal_levels`). The
  level is the higher (lower) of the two; known when the second pivot is confirmed.
* **Sweep-reclaim detector.** First bar t0 with high > L + p·ATR; event = first bar
  t ∈ [t0, t0 + N] with close < L (short; long mirrors). p = 0.10, N = 3 unless stated.
* **Displacement bar.** Body ≥ 1.5 × median body of the prior 100 bars **and** close
  location ≥ 0.75 in the move direction.
* **Session window.** RTH decisions 09:45–15:00 unless stated.
* **PA twin.** The same detector applied to a C-family pivot zone of matched distance
  (does the ICT level beat an ordinary price-action level?). **Shifted-reference
  twin** as in family A.

## Ranking (made before data)

Scores 1–5 as in family A (M mechanism, T testability, F frequency, D distinctness,
C cost survival); ties by C, M, D, F, ID.

| rank | ID | hypothesis | M | T | F | D | C | total |
|---|---|---|---|---|---|---|---|---|
| — | HB00 | Premise: a liquidity footprint exists at the levels | — | — | — | — | — | mechanism test |
| 1 | HB01 | PDH/PDL sweep and reclaim → reversal | 3 | 5 | 4 | 2 | 3 | 17 |
| 2 | HB02 | Overnight high/low sweep at the NY open → reversal | 3 | 5 | 4 | 2 | 3 | 17 |
| 3 | HB04 | Liquidity run: clean break, no reclaim → continuation | 4 | 5 | 4 | 2 | 2 | 17 |
| 4 | HB07 | FVG first retrace → continuation | 2 | 5 | 5 | 2 | 2 | 16 |
| 5 | HB05 | Sweep + displacement adds information | 2 | 5 | 3 | 2 | 3 | 15 |
| 6 | HB12 | Killzone dependence of sweep outcomes | 2 | 5 | 4 | 2 | 2 | 15 |
| 7 | HB08 | FVGs get filled (retracement tendency) | 3 | 5 | 5 | 1 | 1 | 15 |
| 8 | HB06 | Sweep + market structure shift adds information | 2 | 4 | 3 | 2 | 3 | 14 |
| 9 | HB14 | Judas swing (= HA08, tested once as A009) | 3 | 4 | 3 | 2 | 2 | 14 |
| 10 | HB03 | Equal highs/lows sweeps beat single-swing sweeps | 2 | 4 | 3 | 3 | 2 | 14 |
| 11 | HB11 | Premium/discount and OTE after controlling trend | 1 | 4 | 5 | 1 | 2 | 13 |
| 12 | HB09 | Inverse FVG → continuation in the new direction | 1 | 5 | 4 | 1 | 2 | 13 |
| 13 | HB13 | SMT divergence at sweeps (needs ES + NQ) | 2 | 2 | 2 | 3 | 3 | 12 |
| 14 | HB10 | Order-block retest → reaction | 1 | 4 | 4 | 1 | 2 | 12 |

---

## HB00 — Premise: liquidity leaves a footprint at the levels
* **Theory.** Stops cluster beyond obvious extremes (THEORY_ICT 1, 2.1; Osler 2003).
* **Market mechanism.** Triggered stops become market orders: volume and range spike
  at the first crossing.
* **Event.** First 1m bar of the RTH that trades through PDH, PDL, ONH, ONL or an
  equal-high/low level by ≥ 1 tick.
* **Measure.** Volume relative to the median volume of the same minute over the prior
  20 sessions; range / ATR(14, 1m).
* **Expected direction.** Not directional (mechanism check).
* **Expected timeframe / session / regime.** 1m, RTH, any.
* **Potential entry / invalidation / target.** None; mechanism test only.
* **Required data.** 1m OHLCV (trade data would show it far better).
* **Main risk of false discovery.** Crossings happen at volatile times; time-matched
  and shifted-reference controls absorb that.
* **Controls.** Shifted-reference crossings (primary), time-matched bars.
* **Primary test (B001).** Mean log volume ratio: real-level crossings minus
  shifted-reference crossings.
* **Prior.** Positive at PDH/PDL (+0.05 to +0.20 log points); at equal highs
  uncertain. If ≤ 0 at every level, the "liquidity pool" premise has no footprint in
  this data and every B hypothesis must stand on price behaviour alone.

## HB01 — Prior-day high/low sweep and reclaim → reversal
* **Theory.** Liquidity sweep / stop hunt at external liquidity (THEORY_ICT 2.2, 2.4).
* **Market mechanism.** Stops above PDH are triggered and absorbed by larger sellers;
  once absorbed, buying pressure is spent and price reverses.
* **Event (short at PDH; long mirrors at PDL).** Sweep-reclaim detector with L = PDH,
  p = 0.10, N = 3; one event per level per session.
* **Pre-registered variants** (each its own registered test):
  wick sweep (t = t0: the penetrating bar itself closes back below L) — **B003**;
  close sweep (a close > L, then a close < L within 3 bars) — **B004**;
  multi-bar failed breakout (≥ 2 closes > L, then a close < L within 6 bars) — **B005**.
* **Expected direction.** Reversal (back through the level).
* **Expected timeframe.** 30–120 minutes.
* **Expected session.** RTH, strongest 09:45–11:00 (see HB12).
* **Expected regime.** Balance; lower volatility.
* **Potential entry.** Next-bar market; alternative limit at L on a retest.
* **Potential invalidation.** Sweep extreme + 0.10·ATR.
* **Potential target.** Prior RTH mid; opposite liquidity (PDL); session VWAP.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** It is a failed breakout; ordinary intraday mean
  reversion at new session extremes may explain all of it.
* **Controls.** Shifted-reference (primary), PA twin, time-matched.
* **Primary test (B002).** 12-bar signed return in ATR: event minus shifted-reference.
* **Recorded per event.** Penetration depth, bars beyond, maximum continuation before
  the reclaim, reclaim-bar close location, time bucket, distance to the opposite level.
* **Conditions.** Depth tercile; bars beyond (1 vs 2–3); daily volatility tercile.
* **Prior.** +0.02 to +0.08 ATR vs twin; reclaim variant ≥ wick variant; P ≈ 0.25.

## HB02 — Overnight high/low sweep at the NY open → reversal
* **Theory.** Overnight range as liquidity; NY AM killzone (THEORY_ICT 2.2, 2.11).
* **Market mechanism.** Overnight extremes collect stops of overnight traders; the cash
  open supplies the liquidity to sweep and reverse them.
* **Event.** Sweep-reclaim with L = ONH (short) / ONL (long), decisions 09:35–11:00.
* **Expected direction / timeframe / session / regime.** Reversal; 30–120 minutes;
  RTH open; balance.
* **Potential entry / invalidation / target.** Next-bar market; sweep extreme +
  0.10·ATR; overnight mid, then the opposite overnight extreme.
* **Required data.** 1m OHLCV including the overnight session.
* **Main risk of false discovery.** The first RTH hour is the most volatile; overnight
  extremes are often near the open, so "sweeps" are frequent and noisy.
* **Controls.** Shifted-reference (primary), time-matched.
* **Primary test (B006).** 12-bar signed return in ATR: event minus shifted-reference.
* **Prior.** +0.00 to +0.06 ATR; P ≈ 0.20.

## HB03 — Equal highs/lows are better sweep targets than single swings
* **Theory.** "Engineered" liquidity (THEORY_ICT 2.3).
* **Market mechanism.** Double extremes attract more stops.
* **Event.** Sweep-reclaim at an equal-high/low level (definition above), RTH.
* **Expected direction / timeframe / session / regime.** Reversal; 30–90 minutes; RTH;
  any.
* **Potential entry / invalidation / target.** Next-bar market; sweep extreme +
  0.10·ATR; the last opposite swing.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** A double top is an ordinary twice-touched
  resistance; small sample.
* **Controls.** Single-pivot sweeps of matched age (bars since pivot, tercile) and
  distance (primary).
* **Primary test (B007).** 12-bar signed return in ATR: equal-level sweeps minus
  matched single-pivot sweeps.
* **Prior.** Difference ≈ 0; P ≈ 0.10.

## HB04 — Liquidity run: a clean break that holds → continuation
* **Theory.** Liquidity run / draw on liquidity (THEORY_ICT 2.5); stop cascades
  (Osler 2005).
* **Market mechanism.** Triggered stops cascade; with no absorption, price runs to the
  next pool.
* **Event (long at PDH; short mirrors at PDL).** First 5m close > PDH + 0.10·ATR
  (decisions 09:45–15:00); the next 3 bars all close > PDH; event at the third of them
  (the moment "no reclaim" is known).
* **Expected direction.** With the break.
* **Expected timeframe.** 1–3 hours.
* **Expected session.** RTH.
* **Expected regime.** Trend, high volatility.
* **Potential entry.** Next-bar market.
* **Potential invalidation.** A close back below PDH − 0.25·ATR.
* **Potential target.** Prior-week high; 1·ATR_d projection.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Waiting three bars consumes most of the move;
  bull-market drift.
* **Controls.** Time-matched (primary), shifted-reference.
* **Primary test (B008).** 12-bar signed return in ATR: event minus time-matched.
* **Prior.** +0.00 to +0.06 ATR; P ≈ 0.20. Note: HB01 and HB04 predict opposite
  outcomes after a crossing; at most one family of outcomes can dominate.

## HB05 — Sweep + displacement adds information
* **Theory.** Displacement as evidence of institutional participation (THEORY_ICT 2.6).
* **Event.** HB01 (B002) events whose reclaim bar is a displacement bar in the reversal
  direction, vs B002 events whose reclaim bar is not.
* **Expected direction / timeframe / session / regime.** As HB01.
* **Potential entry / invalidation / target.** As HB01; stop alternatively beyond the
  displacement bar.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Large reclaim bars give a worse entry price; the
  apparent improvement may vanish in R terms.
* **Controls.** Within-event comparison (B002 events without displacement).
* **Primary test (B009).** 12-bar signed return in ATR: displacement minus
  non-displacement B002 events.
* **Prior.** ≈ 0; P ≈ 0.15.

## HB06 — Sweep + market structure shift adds information
* **Theory.** MSS / CHoCH after a sweep (THEORY_ICT 2.7).
* **Event (short; long mirrors).** After a B002 short event, within 12 bars, the first
  5m close below the most recent confirmed swing low (confirmed before that close);
  event at that close.
* **Expected direction / timeframe / session / regime.** Reversal continuation; 1–2
  hours; RTH; any.
* **Potential entry / invalidation / target.** Next-bar market; sweep extreme +
  0.10·ATR or the last lower high; opposite liquidity.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** The MSS entry is later and worse; any gain in
  hit rate may be paid for in R.
* **Controls.** Time-matched (primary); B002 entries on the same sessions.
* **Primary test (B010).** 12-bar signed return in ATR: event minus time-matched.
* **Prior.** +0.00 to +0.04 ATR; MSS does not beat the reclaim entry in R; P ≈ 0.15.

## HB07 — FVG first retrace → continuation
* **Theory.** Imbalance is rebalanced, then price continues (THEORY_ICT 2.8).
* **Event (bullish; bearish mirrors).** FVG on 5m: low[t] > high[t−2], width ≥ 0.25·ATR,
  formed 09:45–14:30. Event = first bar k ∈ (t, t+24] with low[k] ≤ low[t] (enters the
  gap) and close[k] ≥ high[t−2] (not closed through); direction long.
* **Expected direction / timeframe / session / regime.** FVG direction; 30–90 minutes;
  RTH; trend.
* **Potential entry.** Next-bar market; alternative limit at the gap midpoint.
* **Potential invalidation.** Close below the gap bottom − 0.10·ATR.
* **Potential target.** The high of the displacement leg; 2R.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** FVG = a fast move; the retrace-and-continue
  pattern may equal any pullback after any strong bar.
* **Controls.** Matched-displacement twin (primary): 3-bar moves of the same size
  tercile without a gap, entered at a pullback of the same depth relative to the move.
* **Primary test (B011).** 12-bar signed return in ATR: FVG events minus twin.
* **Conditions.** Width tercile; location in the dealing range; daily trend tercile;
  FVG formed within 12 bars after a sweep (yes/no).
* **Prior.** ≈ 0 vs twin; P ≈ 0.10.

## HB08 — FVGs get filled (retracement tendency)
* **Theory.** "Price returns to rebalance inefficiencies" (THEORY_ICT 2.8).
* **Event.** At FVG formation (candle 3 close, definition as HB07); direction = fade
  (back toward the gap).
* **Expected direction / timeframe / session / regime.** Against the FVG; 5–60 minutes;
  RTH; balance.
* **Potential entry / invalidation / target.** Next-bar market fade; beyond candle 3's
  extreme; gap midpoint.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Short-horizon reversal after any large bar.
* **Controls.** Matched-displacement twin (primary).
* **Primary test (B012).** P(partial fill within 12 bars): FVG minus twin. Reported
  alongside (descriptive): full-fill rate, time to fill, by width tercile, regime and
  after sweeps — the mission's dedicated FVG study.
* **Prior.** Fill rate high (> 0.6) for both; difference ≈ 0; P ≈ 0.10. HB07 and HB08
  cannot both hold at the same horizon.

## HB09 — Inverse FVG → continuation in the new direction
* **Theory.** IFVG (THEORY_ICT 2.8).
* **Event.** A bullish FVG invalidated by a close below its bottom within 60 bars of
  formation (`fvg_state`, close-through); event at that close; direction short.
* **Expected direction / timeframe / session / regime.** Short; 30–90 minutes; RTH; any.
* **Potential entry / invalidation / target.** Next-bar market; close back above the
  gap top; the leg low.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** It is a breakdown below a recent bar's high.
* **Controls.** Twin: close below the pre-move high of a matched displacement bar
  without a gap (primary).
* **Primary test (B013).** 12-bar signed return in ATR: event minus twin.
* **Prior.** ≈ 0; P ≈ 0.10.

## HB10 — Order-block retest → reaction
* **Theory.** Order blocks / mitigation (THEORY_ICT 2.9).
* **Event (bullish).** A displacement bar d whose close is above the most recent
  confirmed swing high (BOS); OB = the last bar with close < open among the 5 bars
  before d; zone = [OB low, OB high]. Event = first later bar (≤ 48 bars) with low ≤ OB
  high and close ≥ OB low; direction long.
* **Expected direction / timeframe / session / regime.** Impulse direction; 30–120
  minutes; RTH; trend.
* **Potential entry / invalidation / target.** Next-bar market or limit at the OB high;
  below the OB low − 0.10·ATR; the impulse high.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** The OB is a generic pullback area at the start of
  an impulse.
* **Controls.** Random-depth twin (primary): a zone of the same width at a uniformly
  random depth inside the impulse leg, same detector.
* **Primary test (B014).** 12-bar signed return in ATR: OB events minus twin.
* **Prior.** ≈ 0; P ≈ 0.05.

## HB11 — Premium/discount and OTE, after controlling for trend
* **Theory.** Dealing-range location, OTE 0.62–0.79 (THEORY_ICT 2.10).
* **Event a (location).** Every 6th RTH 5m bar: location = (close − last confirmed swing
  low) / (last confirmed swing high − last confirmed swing low), deciles; outcome 12-bar
  return; within `structure_trend` and daily ER terciles.
* **Event b (OTE).** After a bullish BOS (close above the last swing high), with leg low
  L = the last swing low before the BOS and leg high H = max high since L: first bar
  entering each retracement band (H − low)/(H − L) ∈ [0.38, 0.50), [0.50, 0.62),
  [0.62, 0.79] within 24 bars; direction long; outcomes per band.
* **Expected direction.** Long in discount/OTE (ICT claim).
* **Expected timeframe / session / regime.** 30–90 minutes; RTH; trend.
* **Potential entry / invalidation / target.** Next-bar market; below L; H.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Location is a mean-reversion oscillator; Fibonacci
  bands have no mechanism.
* **Controls.** Regression on trend controls (a); band-vs-band comparison (b).
* **Primary tests.** B015: slope of 12-bar return on location decile within trend
  terciles (pooled), expected sign negative (discount better). B016: 12-bar return of
  the OTE band minus the [0.50, 0.62) band.
* **Prior.** a: slope ≈ 0 after trend control; b: no Fibonacci peak. P ≈ 0.05 each.

## HB12 — Killzone dependence of sweep outcomes
* **Theory.** Session timing (THEORY_ICT 2.11).
* **Event.** B002 and B006 events split by time bucket: RTH_OPEN (09:30–10:30) and
  MORNING (10:30–12:00) vs MIDDAY (12:00–14:00).
* **Expected direction / timeframe / session / regime.** As HB01; killzone stronger.
* **Potential entry / invalidation / target.** As HB01.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Volatility seasonality; ATR normalisation and
  time-matched controls should remove it.
* **Controls.** Each bucket against its own time-matched controls.
* **Primary test (B017).** (event − control) in RTH_OPEN+MORNING minus (event −
  control) in MIDDAY, 12-bar ATR returns.
* **Prior.** ≈ 0 after normalisation; P ≈ 0.10.

## HB13 — SMT divergence at sweeps improves outcomes
* **Theory.** SMT divergence (THEORY_ICT 2.14).
* **Event.** B002 long setups on ES (PDL sweep) where NQ, over the same bars [t0, t],
  did not trade below its own PDL (and mirror for shorts).
* **Expected direction / timeframe / session / regime.** Reversal; 30–120 minutes; RTH;
  any.
* **Potential entry / invalidation / target.** As HB01.
* **Required data.** ES and NQ 1m OHLCV on an identical timestamp grid.
* **Main risk of false discovery.** Rare events, highly correlated markets, small n.
* **Controls.** B002 events without divergence.
* **Primary test (B018).** 12-bar signed return in ATR: SMT minus non-SMT B002 events.
* **Status.** Blocked until NQ data exists; registered now so the test count is honest.
* **Prior.** ≈ 0 to slightly positive; P ≈ 0.10.

## HB14 — Judas swing / Power of Three
* Same event as HA08 (early move away from the RTH open, then a close back through
  it). Tested once, as **A009**; the ICT reading is reported in the B family results.

---

## Coverage of the mission's ICT questions

| question | where |
|---|---|
| Prior-session extremes: more reversals than random levels? | HB01, HB02 vs shifted-reference |
| Sweep → reversal or continuation? | HB01 vs HB04 |
| Does reclaim matter? Sweep depth? Time beyond the level? | HB01 variants and conditions |
| Does displacement add information? Does MSS add after displacement? | HB05, HB06 (and the B0–B5 ablation tree) |
| FVG retracement behaviour, direction, width, location, trend, after sweeps? | HB07, HB08 (dedicated FVG study), HB09 |
| Premium/discount after controlling trend? | HB11 |
| Equal highs vs ordinary swing highs? | HB03 |
| Previous-day levels vs arbitrary swings? | HB01 PA twin |
| Overnight levels? NY-open vs midday sweeps? | HB02, HB12 |
| SMT divergence? | HB13 (blocked until NQ) |
| Is a sweep just a classic failed breakout? | HB01 PA twin and C-family HC05 on identical execution |
