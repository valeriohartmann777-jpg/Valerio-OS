# THEORY_AUCTION — Auction Market Theory / "Orochi-style" trading

Written 2026-10-07, **before any market data was seen**. Everything below about
effects is a prior, not a finding. The point of writing it down now is that the
results can later be judged against what was expected, not rationalised afterwards.

## 0. Sources and scope

* J. Peter Steidlmayer's Market Profile work at the CBOT (1980s) and Dalton, Jones &
  Dalton, *Mind Over Markets* (1990, rev. 2013); Dalton, Dalton & Jones, *Markets in
  Profile* (2007). These define value, balance and imbalance, other-timeframe (OTF)
  participants, opening types, excess, poor highs and lows, range extension and the
  "80% rule".
* **"Orochi-style"**: I have no verifiable source for a rule set published under that
  name. This document treats it as discretionary AMT / volume-profile trading on index
  futures built around prior value (VAH, VAL, POC), VWAP, overnight inventory, the
  initial balance, and acceptance or rejection at those references. If Orochi material
  is supplied (rules, checklists, annotated trades), each rule is translated with the
  same template below and added as a hypothesis. Material is a source of hypotheses,
  never a substitute for evidence.
* Empirical literature used for priors only (none of it is evidence for this dataset):
  * Intraday volume and volatility are U-shaped with a strong time-of-day periodicity
    (Admati & Pfleiderer 1988; Andersen & Bollerslev 1997). Consequence: every event
    study needs time-of-day matched controls, otherwise "the open is volatile" looks
    like an edge.
  * Gao, Han, Li & Zhou (2018, *JFE*), "Market intraday momentum": the first
    half-hour return of SPY predicts the last half-hour return, more so on volatile
    days. Relevant to opening-type and acceptance hypotheses.
  * Lou, Polk & Skouras (2019, *JFE*), "A tug of war": overnight and intraday returns
    behave differently. Relevant to overnight inventory and gap hypotheses.

## 1. The model in AMT's own terms

The market exists to facilitate trade. Price moves to find the other side; where it
finds two-sided trade, time and volume accumulate and that region becomes **value**.
A market in **balance** rotates inside value: responsive participants sell its upper
edge and buy its lower edge. A market in **imbalance** moves directionally (price
discovery) until it finds a new balance. Short-term "day timeframe" participants
(market makers, locals, intraday traders) produce rotation; longer-horizon "other
timeframe" participants (institutions re-pricing positions) produce range extension
and value migration. An auction ends with **excess** (a sharp rejection tail) and is
**unfinished** when an extreme is flat (poor high or low), which AMT says invites a
revisit. Prices beyond a reference are either **accepted** (time and volume build
there, value moves) or **rejected** (fast return).

The tradable claim of AMT is therefore conditional: *rejection beyond value predicts
rotation back through value; acceptance beyond value predicts continuation*. The
difficulty is that "accepted" and "rejected" are only known after some time has
passed, and conditioning on that time can manufacture apparent predictability.

## 2. What 1-minute OHLCV can and cannot measure

| quantity | with 1m OHLCV | note |
|---|---|---|
| TPO profile (30-min letters) | **exact** | a letter covers [low, high] of its 30-min period by construction |
| prior-day / overnight / IB / weekly highs and lows | exact | availability = scheduled end of the period |
| session VWAP and bands | close approximation | typical price x volume per 1m bar |
| volume profile, POC, VA | **approximation** | where inside a bar volume traded is unknown; `uniform`, `typical`, `close` allocation must give the same conclusions |
| time beyond a level, closes beyond, close location | exact | |
| initiative vs responsive in the **order-flow** sense (aggressor side, delta, absorption) | **not measurable** | needs trades with aggressor flag or bid/ask; will not be faked |
| initiative vs responsive in AMT's **location** sense (buying above value = initiative) | measurable | used only in this sense |
| dealer gamma, macro release times, rebalances | not in data | unobserved confounders; time-of-day buckets partly absorb them |

## 3. Concept decomposition

For every concept: what it claims, the observable event, why it could carry an edge,
who would cause it, the simpler explanation that must be ruled out, and the test.
Implementation references are to `edgelab/`.

### 3.1 Value area, POC, VAH, VAL
* **Claim.** The price band holding about 70% of a session's volume is fair value; the
  POC is the fairest price. Price outside value is "unfair": either rejected back into
  value or accepted, in which case value moves.
* **Observable.** From the completed RTH profile of session *d* (`session_profiles`):
  POC = highest-volume level (deterministic tie-break), VA = expansion from the POC to
  70% of volume (`value_area`, single or CBOT dual method). Known after the scheduled
  RTH end of *d*, used on *d+1* (`session_levels(..., table=profiles)`).
* **Why edge could exist.** Liquidity providers who anchor on yesterday's prices fade
  moves away from them; institutions that re-price produce persistent moves. Two
  regimes, one reference.
* **Who.** Market makers and short-term liquidity (reversion), OTF institutions
  (re-pricing), and the many discretionary traders who watch the same lines
  (possibly self-fulfilling).
* **Simpler explanation.** The VA is a volume-weighted inter-quantile range of
  yesterday's prices. A "VA effect" may be nothing more than distance from yesterday's
  mid or close in ATR units plus ordinary intraday mean reversion.
* **Test.** Reactions at VAH/VAL against (a) random levels with the same distance
  distribution from price (`random_level_offsets`), (b) prior-day range quantiles
  (15th/85th percentile of traded prices), (c) prior close ± k ATR. Repeat with all
  three volume allocations; a conclusion that flips with the allocation is discarded.

### 3.2 Balance, imbalance, price discovery
* **Claim.** Markets alternate between balance (two-sided rotation) and imbalance
  (directional discovery). Breakouts from balance with acceptance travel to the next
  reference; failed breakouts return to the far side of the balance area.
* **Observable.** Balance = overlapping value: at least 2 of the last 3 RTH value
  areas overlap the previous one by ≥ 50% of the narrower width. Imbalance = value
  migration (non-overlapping consecutive VAs) or a trend day (close in the outer 10%
  of the range with one-sided range extension).
* **Why edge.** Regimes persist (volatility clustering, trend-day clustering).
* **Who.** OTF participants entering or leaving.
* **Simpler.** Low vs high efficiency ratio and volatility terciles (`regime.py`)
  may classify the same days.
* **Test.** Does VA-overlap balance predict next-session range and direction better
  than the ER / ATR regime classifiers? If not, keep the simpler classifier.

### 3.3 Acceptance and rejection
* **Claim.** Price beyond a reference with time (AMT: two 30-minute periods) and
  volume is accepted and continues; a quick return is rejection and rotates.
* **Observable.** Acceptance: two consecutive 30-min closes beyond the reference.
  Rejection: trade beyond by ≥ δ ATR, then a close back inside within N bars.
  Both are timestamped at the bar that completes the condition.
* **Why edge.** The reaction itself is information about whether new participants
  arrived.
* **Who.** OTF (acceptance), responsive DT participants (rejection).
* **Simpler.** Conditioning on "stayed beyond for an hour" selects trending days;
  the effect could be plain 60-minute momentum. "Rejection" could be plain mean
  reversion after an extended move.
* **Test.** Compare with (a) the same conditions at random levels, (b) a pure 60-min
  return-sign rule at the same timestamps.

### 3.4 Excess, poor highs and lows, single prints
* **Claim.** Excess (≥ 2 single-TPO prints at an extreme) ends an auction; the extreme
  holds. A poor high or low (≥ 2 TPOs at the extreme, no tail) is unfinished and is
  revisited.
* **Observable.** From the TPO profile (`tpo_profile`): tail length in ticks at each
  extreme; poor = the extreme price carries ≥ 2 letters.
* **Why edge.** A flat extreme means no responsive seller (buyer) appeared; the
  move was not rejected, only paused.
* **Who.** Short-term traders who stopped pushing, not a real opposite side.
* **Simpler.** The next session trades through the prior high or low most of the
  time anyway; distance from the next open matters far more than shape.
* **Test.** P(next session takes the prior extreme | poor) vs (| excess), matched on
  the distance between the next RTH open and the extreme in ATR units. Report against
  the unconditional base rate.

### 3.5 Failed auction
* **Claim.** A break of a reference (IB extreme, prior high/low, VA edge) that
  attracts no follow-through and returns inside travels fast to the opposite side.
* **Observable.** Trade beyond the reference by ≥ δ ATR, close back inside within N
  bars, then measure first passage to the opposite reference vs a stop beyond the
  failed extreme.
* **Simpler.** It is the same event as a failed breakout (family C) and a liquidity
  sweep (family B). Three vocabularies, one event: the families are compared on the
  identical event library.
* **Test.** Shared event definition; the difference between families is only the
  location (AMT references vs liquidity levels vs S/R zones).

### 3.6 Responsive and initiative activity (location sense only)
* **Claim.** Buying below value or selling above value is responsive (expect
  reversion); buying above value or selling below value is initiative (expect trend).
* **Observable.** Location relative to prior value plus the outcome; aggressor data is
  absent, so no bar is labelled "aggressive buying". Close location in the bar is a
  weak proxy and is labelled as such wherever used.
* **Simpler.** Mean reversion vs momentum conditional on location.
* **Test.** Not a standalone hypothesis; it is the framing for the rotation (HA01) vs
  acceptance (HA02) hypotheses.

### 3.7 Value migration and POC migration
* **Claim.** Higher value day over day means buyers are in control: next session
  biased up. Dalton's two-day relationships (higher, overlapping-higher, inside,
  outside, overlapping-lower, lower) classify this.
* **Observable.** sign(POC_d − POC_{d−1}); VA relationship class.
* **Simpler.** Daily return momentum: POC change is strongly correlated with the
  close-to-close change, and daily index-futures returns have little
  autocorrelation.
* **Test.** Does POC migration predict the next RTH return after partialling out the
  prior close-to-close return? **Prior: no incremental effect.**

### 3.8 Initial balance and range extension
* **Claim.** The IB (first 60 RTH minutes) is the day timeframe's balance. Range
  extension (RE) beyond it shows OTF activity; a narrow IB makes a trend day likelier;
  a failed RE traps traders and reverses.
* **Observable.** IB high/low from 09:30–10:30 (`opening_range`, available 10:30).
  RE = first trade beyond the IB after 10:30. Failed RE = close back inside the IB
  within N bars of the extension.
* **Why edge.** Volatility mean reversion: a small first hour relative to daily ATR
  leaves a larger expected remaining range.
* **Simpler.** That is a statement about magnitude, not direction. Direction after an
  RE may be no better than a coin flip after costs.
* **Test.** (1) IB width tercile → P(RE) and extension size; (2) direction after the
  first RE (continuation vs reversal) against time-matched controls; (3) failed RE →
  travel to the opposite IB extreme.

### 3.9 Opening types
* **Claim (Dalton).** Open drive (aggressive move from the open, no return) = highest
  conviction, trend; open-test-drive = test a reference, then drive the other way;
  open-rejection-reverse = move one way, get rejected, reverse; open auction =
  rotation around the open, balance.
* **Observable.** Classified from the first 30 RTH minutes with thresholds fixed in
  the hypothesis file; the label is available at 10:00, outcomes measured from 10:00.
* **Why edge.** Intraday momentum literature: the first half hour carries information
  about the rest of the day.
* **Simpler.** The first-30-minute return alone (sign and size) may carry the same
  information as the open-type label.
* **Test.** Label vs plain first-30-minute return as predictors of the 10:00–15:55
  return, with time-matched controls. The label must add information to be kept.

### 3.10 Open location relative to prior value and range; gaps; the 80% rule
* **Claim.** Open inside prior value: balance and rotation expected. Open outside value
  but inside the prior range: moderate conviction. Open outside the prior range:
  imbalance, either a trend day or a gap fill. **80% rule:** if the market opens
  outside value and then trades inside it for two consecutive 30-min periods, it
  traverses value to the opposite edge with "80%" probability.
* **Observable.** RTH open (first RTH bar's open) relative to prior VAH/VAL/high/low;
  gap = open − prior RTH close in ATR. 80% rule: two consecutive 30-min TPO periods
  with closes inside prior value after an outside open.
* **Simpler.** Gap mean reversion; the prior close as a magnet.
* **Test.** Conditional event study by open location; 80% rule as a first-passage
  test (opposite VA edge before a stop beyond the entry-side edge). The famous number
  is a claim to be measured, not assumed. **Prior: true rate well below 80%.**

### 3.11 Overnight inventory
* **Claim.** If overnight trading leaves the market net long (most ON trade above the
  prior close, RTH open near the ON high), the inventory is corrected at the open: an
  early move against the overnight direction.
* **Observable.** ON inventory = share of ON volume traded above the prior RTH close
  and (RTH open − ON VWAP) / ATR; known at 09:30.
* **Why edge.** Liquidity providers unwind overnight inventory into the RTH open.
* **Simpler.** Overnight-to-intraday return reversal, which may itself be small.
* **Test.** Correlation of ON inventory with the 09:30–10:30 return; event study for
  extreme-inventory days with time-matched controls.

### 3.12 Previous-day, overnight and prior-week levels
* **Claim.** Widely watched references; the first touch reacts.
* **Observable.** Completed prior RTH OHLC, overnight high/low (available 09:30),
  prior week high/low.
* **Simpler.** Price oscillates; any line gets "reactions". Only the difference
  from random levels matters.
* **Test.** Touch study with random-level controls; this test is shared with B and C.

### 3.13 Composite value
* **Claim.** Multi-day composite value (5 or 20 sessions) defines the longer
  balance; inside it, price rotates around the composite POC.
* **Observable.** `composite_profiles(n_sessions=5 | 20)`, available after the last
  included session.
* **Simpler.** A longer moving average.
* **Test.** As 3.1 with composite levels; compare with a 5/20-day VWAP.

### 3.14 HVN and LVN
* **Claim.** High-volume nodes are acceptance (magnets, slow price); low-volume nodes
  are rejection (fast travel).
* **Observable.** Smoothed composite profile, peaks and troughs by prominence
  (`volume_nodes(smooth_sigma_bins, prominence_frac)`).
* **Why edge.** Concentrated past liquidity may mean concentrated current liquidity.
* **Simpler.** Partly tautological: HVNs are where price spent time in the past.
  Prediction requires a difference in future price speed.
* **Test.** First, stability: nodes must survive bin size (1/2/4 ticks) and smoothing
  (σ = 1/2/4 bins) changes; **if unstable, the concept is dropped**. Then speed of
  travel (bars per ATR) through LVN vs HVN vs random zones.

### 3.15 VWAP, anchored VWAP, VWAP deviation
* **Claim.** VWAP is the day's average price paid and an institutional benchmark;
  ≥ 2 SD deviations revert; a VWAP reclaim after a deviation continues; VWAP slope is
  trend.
* **Observable.** Session VWAP and volume-weighted SD bands (`session_vwap`); anchored
  VWAP only from deterministic anchors (RTH open, prior-session extreme bars, the
  first range-extension bar), never hand-picked.
* **Why edge.** VWAP execution algorithms supply liquidity near VWAP; a large deviation
  means one-sided flow that may exhaust.
* **Simpler.** VWAP is a smoothed price. Distance from VWAP ≈ distance from a moving
  average; reversion in range regimes, momentum in trend regimes.
* **Test.** Deviation buckets (in SD and ATR) → forward return, split by regime;
  compare with distance from an EMA of matched lag.

### 3.16 Developing POC and developing value
* **Claim.** A rising developing POC during the session means value migrating up.
* **Observable.** `developing_value` (computed bar by bar, causal).
* **Simpler.** Running VWAP or median-price drift.
* **Test.** dPOC slope vs VWAP slope as predictors of the next 30–60 minutes.
  **Prior: no difference.**

### 3.17 Profile shapes (D, P, b, double distribution, trend)
* **Claim.** P = short covering, b = long liquidation, D = balance, double
  distribution = trend day with two value areas; each implies next-day behaviour.
* **Observable.** `profile_shape` with thresholds fixed a priori (POC position in the
  range, VA width / range).
* **Simpler.** Shape is a summary of the day's return path; its information for the
  next day is likely the prior-day return and range.
* **Test.** Shape → next-day open location and direction after partialling out prior
  return and range. **Prior: no incremental information.**

## 4. Priors before data (ranked by plausibility)

1. Regularities about **magnitude**: narrow IB → more range extension; balance → smaller
   next-day range. Plausible (volatility mean reversion), but they do not give a
   direction and need a separate directional trigger to be tradable.
2. **Opening momentum / acceptance** after a strong first half hour: small positive
   effect expected from the intraday-momentum literature; uncertain after costs.
3. **Return to value after an outside open** (80% rule): base rate likely above the
   control, true rate far below 80%.
4. **Rejection at VAH/VAL in balanced regimes** (rotation to POC or the opposite edge):
   plausible but the most crowded idea; must beat random levels.
5. **Failed range extension → reversal**: plausible; identical to the failed-breakout
   event in families B and C.
6. Low prior: POC migration, profile shapes, HVN magnets, dPOC slope, overnight
   inventory as a standalone predictor.

## 5. Consequences for the hypothesis list

* Every AMT hypothesis gets a random-level or simpler-feature twin as its control.
* Volume-profile hypotheses run under all three allocation methods.
* Opening hypotheses are timestamped at the moment the label is known (10:00 or
  10:30), never at 09:30 with hindsight about the first hour.
* Concepts that cannot be measured honestly with OHLCV (order-flow initiative,
  absorption, delta) are recorded in `REJECTED_IDEAS.md` as untestable, not
  approximated.

The ranked hypotheses are in `hypotheses/HYPOTHESES_AUCTION.md`.
