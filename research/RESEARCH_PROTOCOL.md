# RESEARCH_PROTOCOL — pre-registered methods and decision rules

Frozen 2026-10-07, **before any market data was seen**. A rule here may only change
through a dated journal entry that states the reason, and never after the data period
it affects has been looked at. The final test window can never change.

## 1. Phase order

theory → mechanism → hypothesis → **event study (DEV)** → minimum viable strategy (DEV)
→ trade-distribution analysis (DEV) → development V0..V3 (DEV) → version and candidate
comparison (VAL) → ablation (DEV, VAL) → walk-forward (DEV+VAL) → **rule freeze** →
**final test (TEST, once)** → robustness and cross-market → practical system.

## 2. Data

1. `reports/DATA_QUALITY_REPORT.md` exists and is read before any research step; its
   findings are handled in the journal before the first event study.
2. **Event days** need full RTH coverage (≥ 95% of RTH bars) on the event date and on
   the date that supplies its prior-session levels. Early-close dates are not event days.
3. Unadjusted continuous series: the first session after each roll is not an event day
   and does not supply prior-session levels. Difference/ratio-adjusted series: levels are
   used as given, and the adjustment method is stated in every report.
4. Missing bars are never filled. Partial resampled bars are kept and flagged
   (`n_bars`).
5. **Splits.** Chronological by trading date: DEV 50%, VAL 25%, TEST 25%, with a
   5-trading-day embargo between them. Computed on the development market (ES) when its
   data is first registered and frozen in `results/split_ledger.json`. Every other
   market uses the same calendar boundaries. NQ is not used at all before rule freeze.
   BTCUSDT is PROXY evidence only and never enters a CME conclusion.

## 3. Event studies

1. Detectors live in `edgelab/studies/`, parameters in `configs/event_studies.yaml`;
   both are committed before any data is loaded. A detector change after data has been
   seen creates a new study ID.
2. Event studies run on **DEV only**.
3. Decision at the event bar's close; outcomes from the next bar's open; horizons in
   bars of the event timeframe, bounded by the RTH session (no outcome crosses 16:00).
   Returns are signed by the expected direction and normalised by the event bar's ATR
   (session-level hypotheses: ATR_d).
4. Controls: as named per hypothesis (time-matched, shifted-reference, twin). Five
   control draws per event.
5. **Primary test.** One metric, one horizon, one control per study ID. Effect = mean
   (event − control), 95% CI and p-value from a **cluster bootstrap over trading dates,
   10,000 resamples**. Cohen's d and the full distribution (mean, median, quantiles,
   P(>0), MFE, MAE) are reported for every horizon.
6. Every primary test, and every additional test computed later and used for a decision,
   is appended to `results/test_registry.csv`. **Benjamini–Hochberg at q = 0.10 across
   all registered tests of all families** decides significance; per-family BH is shown
   for information.
7. **Cost hurdle.** For each event: round-trip BASE cost in points = market slippage
   (entry) + stop slippage (exit) in ticks × tick size + commission / point value,
   divided by the event ATR. The hurdle is the median over events.

## 4. Gates from event study to candidate

A hypothesis becomes a candidate when its primary test meets **all** of:

* **G1** effect > 0 and the 95% cluster-bootstrap CI excludes 0;
* **G2** BH-adjusted p ≤ 0.10 (global registry);
* **G3** effect ≥ the cost hurdle;
* **G4** ≥ 100 events in DEV;
* **G5** same sign in the first and second chronological halves of DEV;
* **G6** the effect is not explained by its simpler twin (event − twin CI excludes 0);
  where the twin explains it, the **simpler version** becomes the candidate instead.

If no hypothesis of a family passes, the family's highest-ranked hypothesis by the
lower CI bound of its primary effect is still developed as the family's *best available*
candidate, labelled **"promoted without statistical support"**. Its final status can
then not be better than INCONCLUSIVE unless VAL and TEST independently support it.
At most 5 candidates per family.

## 5. Strategy development (DEV, then VAL)

1. Every strategy states: context, location, event, trigger, entry (type and timing),
   invalidation (market-based), exit (mechanism-based), no-trade conditions, each with
   a rationale.
2. **V0** is the minimal mechanism; at most **3** refinement generations (V1 entry,
   V2 context, V3 final). Every version is a registered experiment with a pre-registered
   expectation in the journal.
3. Parameter grids are coarse (≤ 4 values per parameter, ≤ 3 parameters varied in one
   grid). The chosen point must sit on a plateau: ≥ 50% of its grid neighbours with
   positive DEV expectancy and no isolated optimum (`plateau_table`).
4. Free parameters are counted. Between versions, the simpler one wins unless the more
   complex one improves VAL expectancy by more than one standard error.
5. Each version is evaluated on VAL **once**, after its DEV development is finished.
   VAL compares versions and candidates, rejects fragile ones and picks broad parameter
   ranges. It is not tuned on.
6. Entry methods (immediate, first pullback, 50% retracement, retest limit) are compared
   on the same predictive events, with missed fills counted as missed trades.
7. Exits come from small logical families (structural, fixed R, trailing structural,
   VWAP/POC); no exit grid beyond 4 values per family.
8. Trade management stays simple (no partials, scale-ins or multi-step stops) unless
   evidence justifies them.

## 6. Ablation and walk-forward

* The final candidate is decomposed: without context, without filter, without trigger
  enhancement, without session condition. A rule that adds DEV return but reduces VAL
  robustness is removed.
* Walk-forward on DEV+VAL: 12 months train, 3 months test, step 3 months (shorter if the
  data is shorter, decided before running). Parameters re-chosen on each train window by
  the plateau rule. Reported: IS/OOS expectancy and PF per window, parameter choices,
  OOS/IS ratios.

## 7. Freeze and final test

1. Freeze = rule document + parameters + code commit hash, registry status FROZEN.
2. `TestSetGuard` releases the TEST window once per frozen candidate. After the run the
   window is consumed for that candidate. A modified strategy is a new candidate and
   **cannot** be evaluated on the same TEST window as out-of-sample.
3. Cross-market: the frozen rules unchanged on NQ (all periods, NQ-TEST dates reported
   separately as the strongest evidence). Cross-timeframe: 3m and 15m variants.

## 8. Robustness battery (after freeze)

Costs LOW/BASE/STRESS; slippage × 0.5, 1, 1.5, 2; entry delay 0, +1, +2 bars; stop and
target −10%, +10%, +20%; ±10% / ±20% parameter perturbations; missing trades 5/10/20%
(Monte Carlo); trade-order reshuffle and bootstrap Monte Carlo (ending PnL, max
drawdown, longest losing streak, time under water: median, 75th, 90th, 95th adverse);
risk of ruin at 0.25% and 0.50% fixed-fractional risk; random-entry, random-direction
and random-level tests; results by year, quarter, month, weekday, session bucket,
long/short and regime; 20 random winners, 20 random losers and 20 random trades charted
for audit.

## 9. Final status rules

Evaluated in this order; the first that applies is the status.

| status | rule |
|---|---|
| **PASS** | TEST net expectancy (BASE) > 0; pooled VAL+TEST expectancy CI lower bound > 0; ≥ 100 TEST trades; STRESS expectancy on TEST ≥ 0; walk-forward OOS positive in ≥ 60% of windows and pooled; ≥ 80% of ±20% perturbations positive on DEV+VAL; +1-bar delay expectancy > 0 on VAL+TEST; event-study gates G1–G4 met; trade audit clean; NQ expectancy > 0 when NQ exists |
| **PROMISING** | TEST net expectancy > 0, and either the CI includes 0 or TEST has < 100 trades, with at least 4 of: STRESS ≥ 0, walk-forward rule, perturbation rule, delay rule, NQ > 0, 20%-missing-trades median > 0 |
| **LIKELY OVERFIT** | DEV expectancy CI lower bound > 0 but TEST expectancy ≤ 0 or OOS/IS expectancy ratio < 0.3; or the plateau rule failed; or ≥ 8 free parameters with performance concentrated in one year |
| **FAIL** | TEST expectancy ≤ 0 without strong DEV evidence, or no event-study effect and no version positive on VAL |
| **INCONCLUSIVE** | anything else (mixed signs, too few trades) |

Wording rule: whenever a 95% CI of expectancy includes 0, the report states *"Evidence
of positive expectancy remains statistically uncertain."*

## 10. Sample-size language

< 50 trades very weak; 50–100 weak; 100–200 interesting; 200–500 meaningful; 500+
stronger. Trades on the same day are not independent: CIs use date clusters.

## 11. Final scoring (0–10 each, never collapsed into one number)

Economic logic, event-study evidence, raw edge, OOS evidence, walk-forward evidence,
parameter robustness, cost robustness, execution robustness, regime robustness,
cross-market robustness, statistical confidence, sample size, simplicity,
interpretability, practical execution, drawdown quality, psychological feasibility,
overfit resistance. Anchors: 0 = evidence against, 5 = no information, 10 = strong
evidence for. Every score gets a one-line justification.

## 12. Reporting and transparency

* Every experiment has a journal entry with the expectation written before the run.
* Every rejected hypothesis and candidate goes into `REJECTED_IDEAS.md` with its result
  and the reason.
* The final report states the total number of hypotheses and tests run, including all
  failures, so the multiple-testing burden is visible.
* Synthetic data never appears in a result. Proxy (BTCUSDT) results are labelled PROXY
  everywhere.

## 13. Pre-data clarifications (2026-10-07)

Written while the detectors were implemented, **before any market data existed in this
project**. They make the rules above operational; none was chosen after seeing a result.
The same text is the journal note "PRE-DATA CLARIFICATIONS".

1. **Timeframe and horizons.** Event studies use 5-minute bars built from 1-minute data
   (anchored 18:00 ET). Horizons are 1, 3, 6, 12, 24, 48 bars (5 min to 4 h). `r{h}` is the
   signed return from the next bar's open to the close of bar t+h in units of the event
   bar's 5-minute ATR(14); `reod` runs to the close of the bar ending 15:55.
2. **Event days (2.2).** Day k qualifies when it lies in the research period, k and k−1
   both have full RTH (first bar 09:30, last bar within one bar of 16:00) and no early
   close, neither is roll-excluded, k−1 is at most 5 calendar days before k, and the RTH
   open and daily ATR are known.
3. **Rolls (2.3).** With a contract column: the trading date of every contract change.
   Without one (unadjusted or unknown adjustment): the conventional roll date (third
   Friday minus 8 days) and the session after it. Difference or ratio adjusted: none.
4. **DEV only (3.2).** The event-study runner does not load any bar after the last DEV
   trading date.
5. **Shifted reference.** The partner day is a random other event day (5 draws per day).
   Its levels are re-anchored to today's RTH open (A006: the 10:30 price) with the offset
   scaled by ATR_d(today) / ATR_d(partner), so the distance distribution matches in
   volatility units. For the nearest pivot zone, an adaptive level that follows today's
   price, the control is the displaced zone set: per draw and date the whole zone set is
   moved by ±U(0.5, 1.5) × the 5-minute ATR at the open, and the nearest displaced zone
   above/below today's price is used. Another day's nearest zones would sit far from
   today's path and be reached only after large moves; a software check on synthetic
   data (not evidence) showed 2–7× fewer control events per draw with that design and
   equal counts with the displaced set.
6. **Time-matched controls.** The same 5-minute bar (exact minute) on 5 randomly drawn
   other event days of the period, same direction.
7. **p-values and CIs.** Two-sided percentile bootstrap, p = 2·min(P*(T ≤ 0), P*(T ≥ 0)),
   10,000 resamples of whole trading dates (multinomial date weights); 95% percentile CI.
8. **Gates.** G5: the oriented effect is positive in both halves, split at the median
   trading date of the study's observations. G6: when the pre-registered twin is the
   primary control, G6 equals G1; when no twin was pre-registered, G6 is not applicable
   and does not block. Study types: E (event effect, G1–G6), I (incremental comparison,
   never a candidate, G1 G2 G4 G5) and M (mechanism / magnitude / filter, never a
   candidate, G1 G2 G4 G5); for I and M, G4 counts the smallest group. G3 for A003 uses
   the R-multiple test against the cost in R (cost / stop distance), for A014 half the
   coefficient (the per-trade gain of a long-after-up / short-after-down rule).
9. **Registry.** The G6 twin test and the G3 economic test are registered with the
   primary test and count in Benjamini–Hochberg. Descriptive tables (other horizons,
   conditions, CIs at 2,000 resamples) never decide anything; a number used later for a
   decision is registered first.
10. **Family fallback and cap.** Effects of different studies have different units (ATR,
    ATR_d, R, probability), so "highest lower CI bound" is applied unit-free: the lower
    95% bound of the net-of-cost effect in standard errors, (effect − hurdle) / se − 1.96,
    ties by pre-data rank. A hypothesis yields at most one candidate (its best variant
    by that score); at most five candidates per family.
11. **Costs.** The cost hurdle of the crypto proxy also includes the taker fee on both
    sides (basis points × price).
12. **Re-runs.** A study that already ran on a dataset runs again only with a written
    reason (journal note); its tests are registered again and count.
13. **Data-quality gate (2.1).** The runner refuses to start until the journal holds a
    note titled "DATA_QUALITY <dataset id>" with the findings of the quality report.
