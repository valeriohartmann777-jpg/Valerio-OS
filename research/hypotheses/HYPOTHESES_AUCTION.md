# HYPOTHESES_AUCTION — Family A (Auction Market Theory / Orochi-style)

Pre-registered 2026-10-07, **before any market data was seen**. Event definitions,
parameters, primary tests and expectations below are frozen; any later change is a new
hypothesis with a new ID, logged in the journal. Theory: `theory/THEORY_AUCTION.md`.
Common definitions and decision rules: `RESEARCH_PROTOCOL.md`. Machine-readable mirror:
`configs/event_studies.yaml`.

## Common definitions

* **Bars.** 5-minute signal bars built from 1-minute bars (bins aligned to :00/:05);
  30-minute bars anchored at 09:30 where stated. Execution is always simulated on 1m bars.
* **ATR** = Wilder ATR(14) of 5m bars at the event bar. **ATR_d** = ATR(14) of completed
  daily RTH bars, known after the prior RTH close.
* **Prior value** = POC/VAH/VAL of the completed prior RTH volume profile (1-tick bins,
  uniform allocation, 70%, single-level expansion). Sensitivity: typical and close
  allocation, CBOT dual method.
* **IB** = 09:30–10:30 high/low, known at 10:30.
* **Decision** at the close of the event bar; outcomes start at the next bar's open.
* **Outcome horizons** stop at the RTH close (15:55 decision limit); nothing crosses
  into the post-close session.
* **Controls.** *Time-matched*: 5 bars at the same minute of day on other trading dates
  of the same split, same direction. *Shifted-reference*: the reference levels of a
  randomly drawn other trading date, shifted so that their offset from the current
  day's RTH open is preserved (5 draws per day; offset scaled by the ratio of daily
  ATRs, RESEARCH_PROTOCOL.md 13.5); the identical detector is rerun on them. This is
  the randomized-level test: it keeps the geometry and breaks the link to the actual
  auction.
* **Primary test** per hypothesis: one metric, one horizon, one control, registered
  in the multiple-testing registry. Everything else is descriptive.

## Ranking (made before data)

Scores 1–5: **M** mechanism credibility, **T** testability with 1m OHLCV, **F** expected
event frequency, **D** distinctness from a simpler explanation, **C** potential to
survive costs. Rank by total; ties by C, then M, then D, then F, then ID.

| rank | ID | hypothesis | M | T | F | D | C | total |
|---|---|---|---|---|---|---|---|---|
| 1 | HA04 | Open location vs prior value → day type | 4 | 5 | 5 | 3 | 2 | 19 |
| 2 | HA05 | Failed IB range extension → reversal | 4 | 5 | 4 | 2 | 3 | 18 |
| 3 | HA06 | Narrow IB → range extension, continuation | 4 | 5 | 4 | 3 | 2 | 18 |
| 4 | HA01 | Rejection back inside prior value → rotation | 4 | 4 | 4 | 2 | 3 | 17 |
| 5 | HA03 | 80% rule: outside open, re-entry → opposite edge | 3 | 5 | 3 | 3 | 3 | 17 |
| 6 | HA02 | Acceptance outside prior value → continuation | 3 | 5 | 3 | 2 | 3 | 16 |
| 7 | HA07 | Opening drive → trend continuation | 3 | 4 | 3 | 2 | 3 | 15 |
| 8 | HA09 | Overnight inventory correction at the open | 3 | 4 | 3 | 3 | 2 | 15 |
| 9 | HA10 | VWAP 2-SD deviation → reversion (and reclaim) | 3 | 5 | 4 | 1 | 2 | 15 |
| 10 | HA11 | Poor high/low revisited vs excess | 2 | 4 | 4 | 3 | 2 | 15 |
| 11 | HA08 | Open-rejection-reverse → reversal | 3 | 4 | 3 | 2 | 2 | 14 |
| 12 | HA12 | POC migration / value relationship / shape → next day | 2 | 5 | 5 | 1 | 1 | 14 |
| 13 | HA13 | LVN fast travel, HVN magnet | 2 | 2 | 4 | 2 | 2 | 12 |

---

## HA01 — Rejection back inside prior value → rotation
* **Theory.** Responsive activity at the edges of accepted value (THEORY 3.1, 3.3, 3.6).
* **Market mechanism.** In the absence of OTF re-pricing, a probe below VAL finds
  responsive buyers and liquidity providers who fade it; price rotates back into value.
* **Event (long; short mirrors at VAH).** RTH, decisions 09:45–15:00. The session has
  at least one 5m close ≥ VAL before the probe (approach from inside value). First bar
  t0 with low < VAL − 0.10·ATR; event = first bar t ∈ [t0, t0+3] with close > VAL.
  One event per side per session.
* **Expected direction.** Long at VAL, short at VAH.
* **Expected timeframe.** 5m signal; effect within 30–120 minutes.
* **Expected session.** RTH, strongest after the opening hour.
* **Expected regime.** Balance (overlapping value areas), low/mid volatility.
* **Potential entry.** Next-bar market; alternative limit at VAL on a retest.
* **Potential invalidation.** Below the probe low − 0.10·ATR (the rejected extreme).
* **Potential target.** Prior POC; then VAH.
* **Required data.** 1m OHLCV (profile, 5m bars).
* **Main risk of false discovery.** VA edges are just quantiles of yesterday's prices;
  the rotation may be generic intraday mean reversion that the shifted-reference twin
  shows equally.
* **Controls.** Shifted-reference (primary), time-matched.
* **Primary test (A001).** 12-bar signed return in ATR: event minus shifted-reference
  twin; cluster bootstrap by date.
* **Pre-registered conditions.** Open inside prior value (yes/no); VA-overlap balance
  (yes/no); touch number of VAL in the session (1 vs 2+); daily volatility tercile.
* **Prior.** +0.03 to +0.08 ATR vs twin; P(passes protocol gates) ≈ 0.25.

## HA02 — Acceptance outside prior value → continuation
* **Theory.** Acceptance = OTF re-pricing; value migrates (THEORY 3.3, 3.6).
* **Market mechanism.** Two 30-minute periods holding beyond value show that new
  participants transact there; inventory has to be built at the new prices.
* **Event (long; short mirrors below VAL).** RTH open ≤ VAH. First pair of consecutive
  30m bars (anchored 09:30) both closing > VAH, the second ending no later than 14:30;
  event at the second close.
* **Expected direction.** With the acceptance.
* **Expected timeframe.** 30m event, 5m outcomes, 1–4 hours.
* **Expected session.** RTH, late morning to afternoon.
* **Expected regime.** Trend / high efficiency ratio.
* **Potential entry.** Next 5m open after the event.
* **Potential invalidation.** A 30m close back inside value (below VAH − 0.10·ATR).
* **Potential target.** Prior RTH high, then prior-week high; or session close.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Selecting days that have already trended for an
  hour; equals plain 60-minute momentum.
* **Controls.** Momentum twin (primary): time-matched bars on other dates with direction
  = sign of the prior 60-minute return; shifted-reference.
* **Primary test (A002).** 12-bar signed return in ATR: event minus momentum twin.
* **Conditions.** Developing-POC slope agrees (yes/no); daily trend tercile.
* **Prior.** +0.00 to +0.06 ATR vs twin; P ≈ 0.20.

## HA03 — The 80% rule
* **Theory.** Dalton's 80% rule (THEORY 3.10).
* **Market mechanism.** An outside open that is rejected back into value for an hour
  signals failed re-pricing; the market seeks the other side of the old balance.
* **Event (short after an open above VAH; long mirrors below VAL).** RTH open > VAH.
  First pair of consecutive 30m bars both closing inside [VAL, VAH], second ending
  ≤ 14:30; event at the second close.
* **Expected direction.** Toward the opposite value edge.
* **Expected timeframe.** Rest of the session.
* **Expected session.** RTH.
* **Expected regime.** Balance.
* **Potential entry.** Next 5m open.
* **Potential invalidation.** VAH + 0.25·ATR_d (back outside on the opening side).
* **Potential target.** VAL.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Barrier geometry alone fixes the hit rate of a
  random walk; a high hit rate with a near target and far stop is not an edge.
* **Controls.** Barrier-geometry twin (primary): time-matched controls with identical
  target and stop distances in ATR_d.
* **Primary test (A003).** P(target before stop by 15:55): event minus geometry twin;
  also reports the expectancy in R implied by the actual entry.
* **Conditions.** Gap size tercile; daily volatility tercile.
* **Prior.** Target-first rate 0.40–0.55 (far below 0.80); excess over twin
  +0.00 to +0.08; P ≈ 0.20.

## HA04 — Open location relative to prior value and range → day type
* **Theory.** Open location as the first read of balance vs imbalance (THEORY 3.10).
* **Market mechanism.** An open inside value means no re-pricing overnight (rotation);
  an open outside the prior range means re-pricing (range expansion, trend or gap fill).
* **Event.** Classification at 09:30 from the RTH open: (1) inside prior value,
  (2) outside value, inside prior range, (3) outside prior range. Gap =
  (open − prior RTH close) / ATR_d. Tradable variant: class 3, decision at 09:35, direction
  = gap direction (continuation).
* **Expected direction.** Magnitude claim; continuation for class 3 is uncertain.
* **Expected timeframe.** Whole session.
* **Expected session.** RTH.
* **Expected regime.** Any (this is a regime classifier).
* **Potential entry.** Class-3 continuation at 09:35 next 5m open; or as context for
  HA01/HA05 (class 1) and HA02/HA07 (class 3).
* **Potential invalidation.** Return to the prior close (gap filled).
* **Potential target.** Session close; prior-week extreme.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Overnight volatility already tells the same story;
  gap size, not value location, may carry the information.
* **Controls.** Unconditional distribution of the session statistics; for the tradable
  variant, time-matched controls at 09:35 with direction = gap sign.
* **Primary test (A004).** RTH range / ATR_d: class 3 minus class 1 (day bootstrap).
* **Secondary registered test (A005).** Class-3 continuation 09:35→15:55 in ATR_d vs
  time-matched controls.
* **Conditions.** Gap fill rate by class (descriptive).
* **Prior.** Range difference clearly positive (+0.2 ATR_d or more); continuation
  effect ±0.05 ATR_d; P(directional variant passes) ≈ 0.15.

## HA05 — Failed range extension beyond the IB → reversal
* **Theory.** Failed auction at the IB extreme (THEORY 3.5, 3.8).
* **Market mechanism.** An extension that attracts no OTF follow-through traps breakout
  traders; their exits push price back through the IB.
* **Event (short at IB high; long mirrors at IB low).** After 10:30, decisions
  10:35–15:00. First bar t0 with high > IB_high + 0.10·ATR; event = first bar
  t ∈ [t0, t0+3] with close < IB_high. One per side per session.
* **Expected direction.** Back into and through the IB.
* **Expected timeframe.** 30–120 minutes.
* **Expected session.** RTH after 10:30.
* **Expected regime.** Balance, wide IB.
* **Potential entry.** Next-bar market.
* **Potential invalidation.** Highest high since t0 + 0.10·ATR.
* **Potential target.** IB mid, then IB low.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Same event as a failed breakout of any session
  extreme; the IB label may add nothing.
* **Controls.** Shifted-reference (IB of another date, offset from the 10:30 price
  preserved), time-matched.
* **Primary test (A006).** 12-bar signed return in ATR: event minus shifted-reference
  twin.
* **Conditions.** IB width tercile; open inside value (yes/no); time bucket.
* **Prior.** +0.03 to +0.10 ATR; P ≈ 0.25.

## HA06 — Narrow IB → range extension and continuation
* **Theory.** IB width as the day timeframe's balance; narrow IB → trend day
  (THEORY 3.8).
* **Market mechanism.** Volatility mean reversion leaves a large expected remaining
  range after a quiet first hour; the first extension may mark OTF direction.
* **Event (both sides).** IB width / ATR_d in the bottom tercile of its trailing
  252-session distribution (causal, min 60). Event = first 5m close beyond the IB after
  10:30 (decisions to 15:00); direction with the close.
* **Expected direction.** With the extension.
* **Expected timeframe.** 1 hour to session close.
* **Expected session.** RTH after 10:30.
* **Expected regime.** Low volatility days.
* **Potential entry.** Next-bar market or stop entry one tick beyond the IB.
* **Potential invalidation.** Back inside the IB by 0.25·ATR or IB mid.
* **Potential target.** IB width projected (1× and 2×) from the extreme; session close.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** The magnitude effect is real but direction-free; a
  directional edge could be a drift artefact of a bull market sample.
* **Controls.** Time-matched (primary); wide-IB events as comparison group.
* **Primary test (A007).** 12-bar signed return in ATR after the first extension on
  narrow-IB days minus time-matched controls.
* **Descriptive.** P(extension by 15:55) and extension size by IB tercile.
* **Prior.** Magnitude: narrow IB → higher P(extension) (strong); direction
  +0.00 to +0.06 ATR; P ≈ 0.15.

## HA07 — Opening drive → trend continuation
* **Theory.** Open drive = strongest open type (THEORY 3.9); intraday momentum.
* **Market mechanism.** Aggressive OTF participation from the bell, no responsive
  opposition, continues as inventory keeps being built.
* **Event (up; down mirrors).** From 1m bars 09:30–09:59: close(09:59) − open(09:30)
  ≥ 0.30·ATR_d; the low of 09:35–09:59 stays above open(09:30); close location of the
  09:30–09:59 range ≥ 0.70. Decision at 10:00.
* **Expected direction.** With the drive.
* **Expected timeframe.** 10:00 to session close.
* **Expected session.** RTH.
* **Expected regime.** High volatility, trend.
* **Potential entry.** 10:00 next-bar market; or first pullback to the 30-min VWAP.
* **Potential invalidation.** Back below the RTH open.
* **Potential target.** Session close; 1× ATR_d from the open.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** The label = a large first-30-minute return; any
  effect may come from the return size alone.
* **Controls.** Momentum twin (primary): days with |first-30-min return| ≥ 0.30·ATR_d
  that are **not** open drives (they retraced to the open), direction = return sign;
  time-matched.
* **Primary test (A008).** 10:00→15:55 signed return in ATR_d: open-drive minus twin.
* **Conditions.** Open location class (HA04); gap direction agrees.
* **Prior.** +0.00 to +0.08 ATR_d vs twin; P ≈ 0.15.

## HA08 — Open-rejection-reverse (also ICT "Judas swing")
* **Theory.** Open-rejection-reverse (THEORY 3.9); ICT Judas swing / Power of Three
  (THEORY_ICT 2.12). One event, two vocabularies; tested once.
* **Market mechanism.** An early move fails to attract follow-through; trapped
  early participants exit and price travels the other way.
* **Event.** Between 09:30 and 10:30 price first moves ≥ 0.20·ATR_d away from the RTH
  open on one side; then a 5m close beyond the open on the other side by ≥ 0.05·ATR_d,
  before 10:30. First occurrence; direction = the new side.
* **Expected direction.** Reversal of the first excursion.
* **Expected timeframe.** 1–3 hours.
* **Expected session.** RTH opening hour.
* **Expected regime.** Any; better in balance.
* **Potential entry.** Next-bar market.
* **Potential invalidation.** Beyond the first excursion's extreme.
* **Potential target.** Session close; 1× the first excursion beyond the open.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Opening noise; the open is a magnet in balance
  and a crossing of it carries little information.
* **Controls.** Time-matched; open-crossing twin (any 5m close crossing the open by
  ≥ 0.05·ATR_d without a prior 0.20·ATR_d excursion).
* **Primary test (A009).** 24-bar signed return in ATR: event minus time-matched.
* **Prior.** +0.00 to +0.05 ATR; P ≈ 0.15.

## HA09 — Overnight inventory correction at the open
* **Theory.** Overnight inventory (THEORY 3.11).
* **Market mechanism.** Liquidity providers carrying one-sided overnight inventory
  unwind it into the RTH open.
* **Event (short after long inventory; long mirrors).** RTH open in the top 15% of the
  overnight range ((open − ON low)/(ON high − ON low) ≥ 0.85) and ON return
  (open − prior RTH close) ≥ +0.30·ATR_d. Decision at 09:35.
* **Expected direction.** Against the overnight inventory.
* **Expected timeframe.** First 30–60 RTH minutes.
* **Expected session.** RTH open.
* **Expected regime.** Balance; no major overnight news (not observable).
* **Potential entry.** 09:35 next-bar market.
* **Potential invalidation.** Above the overnight high + 0.10·ATR_d.
* **Potential target.** Overnight VWAP / overnight mid.
* **Required data.** 1m OHLCV including the overnight session.
* **Main risk of false discovery.** Overnight news makes the open move persistent
  (momentum); the effect may flip sign across regimes.
* **Controls.** Time-matched at 09:35, direction = −sign(ON return).
* **Primary test (A010).** 6-bar signed return in ATR minus time-matched.
* **Prior.** −0.03 to +0.03 ATR (no effect); P ≈ 0.10.

## HA10 — VWAP 2-SD deviation → reversion; VWAP reclaim → continuation
* **Theory.** VWAP as fair-value benchmark (THEORY 3.15).
* **Market mechanism.** VWAP execution supplies liquidity near VWAP; a 2-SD deviation
  means one-sided flow that tends to exhaust in balance and persist in trend.
* **Event a (fade).** RTH VWAP anchored at 09:30 with volume-weighted SD bands. First
  5m close above VWAP + 2 SD (or below − 2 SD) in the session, decisions 10:00–15:00;
  direction toward VWAP.
* **Event b (reclaim).** After an event-a deviation below −2 SD, the first later 5m
  close above VWAP in the same session → long (mirror short).
* **Expected direction.** a: toward VWAP; b: with the reclaim.
* **Expected timeframe.** 30–90 minutes.
* **Expected session.** RTH after 10:00.
* **Expected regime.** a: low efficiency ratio; b: any.
* **Potential entry.** Next-bar market.
* **Potential invalidation.** a: 1·ATR beyond the deviation extreme; b: back below the
  −1 SD band.
* **Potential target.** a: VWAP; b: +2 SD band.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** VWAP is a smoothed price; the same effect may hold
  for any moving-average distance.
* **Controls.** EMA twin (primary): the same event with a 20-bar EMA ± 2 rolling SD of
  the close-minus-EMA residual; time-matched.
* **Primary tests (A011 fade, A012 reclaim).** 12-bar signed return in ATR: event minus
  EMA twin.
* **Conditions.** Daily trend tercile (pre-registered expectation: fade works only in
  the low tercile).
* **Prior.** a: +0.02 ATR overall, +0.06 in low-ER days, negative in high-ER days;
  b: ±0.03. P ≈ 0.15 each.

## HA11 — Poor highs/lows get revisited; excess holds
* **Theory.** Unfinished auctions (THEORY 3.4).
* **Market mechanism.** A flat extreme shows no responsive opposite side; the move
  paused rather than ended.
* **Event.** At the RTH close of day *d*, from the RTH TPO profile (30-minute letters,
  1-tick bins): the high is *poor* if its price carries ≥ 2 letters, *excess* if the
  top ≥ 2 ticks carry exactly 1 letter. Mirror for lows. Outcome on *d+1*: does the RTH
  trade through the extreme, and when.
* **Expected direction.** Toward the poor extreme.
* **Expected timeframe.** Next session.
* **Expected session.** RTH of *d+1*.
* **Expected regime.** Any.
* **Potential entry.** If *d+1* opens below a poor high within 0.5·ATR_d: long at 09:35.
* **Potential invalidation.** 0.5·ATR_d below the entry.
* **Potential target.** The poor high.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** The base rate of taking a prior extreme is high
  and driven by distance from the open.
* **Controls.** Excess extremes at the same distance from the *d+1* open (distance
  terciles in ATR_d).
* **Primary test (A013).** P(revisit during *d+1* RTH): poor minus excess, stratified
  by distance tercile (Cochran–Mantel–Haenszel-style pooled difference, day bootstrap).
* **Prior.** Difference ≈ 0; P ≈ 0.10.

## HA12 — Value migration, POC migration, profile shape → next session
* **Theory.** Value migration, two-day relationships, profile shapes (THEORY 3.7,
  3.17).
* **Market mechanism.** Value moving higher shows buyers re-pricing; shape shows who
  was active (short covering, long liquidation).
* **Event.** At the RTH close of *d*: sign(POC_d − POC_{d−1}); two-day value
  relationship class; profile shape class. Outcome: *d+1* RTH open→15:55 return in ATR_d.
* **Expected direction.** With value migration.
* **Expected timeframe.** Next session.
* **Expected session.** RTH.
* **Expected regime.** Any.
* **Potential entry.** *d+1* 09:35.
* **Potential invalidation.** Prior POC.
* **Potential target.** Session close.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** POC change is a noisy proxy of the daily return;
  daily index returns have little autocorrelation.
* **Controls.** Regression control for the sign of the *d* close-to-close return.
* **Primary test (A014).** Mean *d+1* return in ATR_d: POC-up minus POC-down, after
  partialling out the *d* return sign (day bootstrap).
* **Prior.** No incremental effect; P ≈ 0.05.

## HA13 — LVNs are fast-travel zones, HVNs are magnets
* **Theory.** Volume nodes (THEORY 3.14).
* **Market mechanism.** Past liquidity concentration persists; thin zones are crossed
  quickly.
* **Stability gate (first).** Composite profile of the prior 5 RTH sessions; nodes by
  smoothing σ ∈ {1, 2, 4} bins × bin size ∈ {1, 2, 4} ticks, prominence ≥ 10% of the
  maximum. A node is stable if found within 2 bins in ≥ 7 of the 9 settings. **If fewer
  than half of the nodes are stable, HA13 is rejected without testing direction.**
* **Event.** Price enters a stable LVN zone: bars needed to exit on the far side vs
  HVN zones vs random zones of equal width. Magnet: from 1·ATR_d away, P(reach the HVN
  before moving another 1·ATR_d away) vs random levels.
* **Expected direction.** Speed claim, not direction.
* **Expected timeframe.** Intraday.
* **Expected session.** RTH.
* **Expected regime.** Balance.
* **Potential entry.** Breakout into an LVN toward the next HVN.
* **Potential invalidation.** Back out of the LVN on the entry side.
* **Potential target.** Next HVN.
* **Required data.** 1m OHLCV; trade data would be better.
* **Main risk of false discovery.** Node instability; tautology (nodes are where price
  spent time).
* **Controls.** Random zones of equal width.
* **Primary test (A015).** Median bars-per-ATR traversal speed: LVN minus random zones.
* **Prior.** Stability gate likely fails at 1m resolution; P ≈ 0.05.

---

## Coverage of the mission's auction research questions

| question | where |
|---|---|
| Rejection outside prior value → rotation? | HA01 |
| Acceptance outside prior value → continuation? | HA02 |
| Opening inside previous value → rotation? / outside value → trend? | HA04 |
| Return inside prior value after an outside open → opposite edge? | HA03 |
| POC migration directional? Prior-day value migration → next session? | HA12 |
| LVN rejection or fast travel? HVN magnets? | HA13 |
| First vs third touch of VAL? | HA01 condition (touch number) |
| Overnight inventory → open reversal? | HA09 |
| Gap outside value vs inside value? | HA04 (class × gap fill) |
| Failed range extension → reversal? | HA05 |
| Developing POC shift improves direction? | HA02 condition (dPOC slope) |
| Distance from VWAP → mean reversion? VWAP regain → continuation? | HA10 a / b |
| Opening drive continuation? Failed opening drive reversal? | HA07 / HA08 |
| Balance width? | HA01, HA05 conditions (VA width, IB width) |
| Prior-day shape (D, P, b, double distribution)? | HA12 |
