# RESEARCH_JOURNAL

Append-only. Every experiment is pre-registered here (hypothesis, rules, parameters,
dataset, **expected outcome before running**) by `edgelab.journal.preregister`; results
are inserted under their entry by `record_result`, which refuses experiments that were
never pre-registered. Dated notes (`### NOTE`) record protocol clarifications, data
findings and re-run reasons. Git history of this file is the proof of order.

Event-study IDs A001–A015, B001–B018, C001–C018 and C009vC001 are taken; strategy
experiments continue with the next free number of their family.

### NOTE 2026-10-07T15:12:18+00:00 — DATA BLOCKER (reports/DATA_ACQUISITION_LOG.md)

No real market data could be acquired from inside the environment on 2026-10-07: every
market-data host (Binance, Bybit, Kraken, Coinbase, Yahoo, Stooq, Dukascopy, Databento,
FirstRate, CME, Zenodo) is refused at the environment's proxy (403), the repository and
the container hold no market data, the user's device could not be reached. Per the
mission rules no synthetic data is used for research and no result exists. The framework,
the theory, the hypotheses and all event-study detectors are written and pre-registered
before any data, so the first real run cannot be tuned to what it shows.


### NOTE 2026-10-07T15:12:36+00:00 — PRE-DATA CLARIFICATIONS (RESEARCH_PROTOCOL.md section 13)

**13. Pre-data clarifications (2026-10-07)**

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

## A001 — Rejection back inside prior value -> rotation

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA01 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 4 in family auction.
- **Rules:** HA01: probe beyond prior VAL/VAH by 0.10 ATR after a close inside value, close back inside within 3 bars; one event per side per session; vs shifted-reference value. Primary test: r12 against shifted_reference, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"pen_atr": 0.1, "reclaim_bars": 3, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.03 to +0.08 ATR vs shifted reference; P(passes gates) ~ 0.25

<!-- results:A001 -->

## A002 — Acceptance outside prior value -> continuation

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA02 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 6 in family auction.
- **Rules:** HA02: RTH open <= VAH, first two consecutive 30m closes > VAH (second closes by 14:30); long (mirror short below VAL); vs momentum twin (sign of the prior 60-minute return). Primary test: r12 against momentum_twin_60min, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"last_pair_close": "14:30", "momentum_lookback_bars": 12}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.06 ATR vs momentum twin; P ~ 0.20

<!-- results:A002 -->

## A003 — 80% rule - outside open, re-entry -> opposite value edge

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA03 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 5 in family auction.
- **Rules:** HA03 80% rule: open outside value, two consecutive 30m closes inside value (second by 14:30); target = opposite edge, stop = entry-side edge +/- 0.25 ATR_d, by 15:55. Primary: P(target first) event - geometry twin (time-matched, same distances in ATR_d). Primary test: P(target before stop by 15:55) against geometry_twin, unit R; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"last_pair_close": "14:30", "stop_atr_d": 0.25}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** target-first rate 0.40-0.55; excess over geometry twin +0.00 to +0.08; P ~ 0.20

<!-- results:A003 -->

## A004 — Open location vs prior value and range -> day range (magnitude)

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA04 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type M (mechanism / magnitude / filter; not a signed trade return); pre-data rank 1 in family auction.
- **Rules:** HA04 magnitude: RTH range / ATR_d of class-3 days minus class-1 days (one row per day). Primary test: RTH range / ATR_d against class 1 days, unit atr_d; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** class 3 - class 1 clearly positive (+0.2 ATR_d or more)

<!-- results:A004 -->

## A005 — Class-3 open, gap-direction continuation 09:35 -> 15:55

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA04 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 1 in family auction.
- **Rules:** HA04 tradable variant: class-3 open, decision 09:35, direction = gap sign, outcome to 15:55 in ATR_d vs time-matched controls. Primary test: reod (ATR_d) against time_matched, unit atr_d; G6 twin: none pre-registered; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +-0.05 ATR_d; P(passes) ~ 0.15

<!-- results:A005 -->

## A006 — Failed IB range extension -> reversal

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA05 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 2 in family auction.
- **Rules:** HA05: after 10:30, high > IB high + 0.10 ATR then a close < IB high within 3 bars (short; mirror at IB low); vs shifted-reference IB (offset from the 10:30 price kept). Primary test: r12 against shifted_reference (anchor 10:30 price), unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"ib_tercile_min": 60, "ib_tercile_window": 252, "pen_atr": 0.1, "reclaim_bars": 3, "scan_from": "10:30", "window": ["10:35", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.03 to +0.10 ATR; P ~ 0.25

<!-- results:A006 -->

## A007 — Narrow IB -> first extension continues

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA06 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 3 in family auction.
- **Rules:** HA06: narrow IB (bottom tercile of IB/ATR_d over the trailing 252 sessions, min 60); first 5m close beyond the IB after 10:30 (decisions to 15:00), with the close; vs time-matched. G6 twin: the same first extension on wide-IB (top tercile) days. Primary test: r12 against time_matched, unit atr; G6 twin: first IB extension on wide-IB (top tercile) days; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"ib_tercile_min": 60, "ib_tercile_window": 252, "scan_from": "10:30", "window": ["10:35", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** direction +0.00 to +0.06 ATR (magnitude effect strong, descriptive); P ~ 0.15

<!-- results:A007 -->

## A008 — Opening drive -> trend continuation

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA07 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 7 in family auction.
- **Rules:** HA07 open drive (decision 10:00, outcome to 15:55 in ATR_d) vs momentum twin: days with |first-30-min return| >= 0.30 ATR_d that are not open drives, direction = return sign. Primary test: reod from 10:00 (ATR_d) against momentum_twin (large first-30-min move, unit atr_d; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"clv": 0.7, "decision": "10:00", "drive_atr_d": 0.3}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.08 ATR_d vs twin; P ~ 0.15

<!-- results:A008 -->

## A009 — Open-rejection-reverse / Judas swing (also HB14)

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA08 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 11 in family auction.
- **Rules:** HA08/HB14 open-rejection-reverse: before 10:30 a first excursion >= 0.20 ATR_d from the RTH open (1m order), then a 5m close beyond the open on the other side by >= 0.05 ATR_d (close by 10:30); direction = the new side; 24-bar outcome vs time-matched. G6 twin: the first 5m close crossing the open by >= 0.05 ATR_d without a prior 0.20 ATR_d excursion. Primary test: r24 against time_matched, unit atr; G6 twin: open-crossing twin (crossing the open without a prior 0.20 ATR_d excursion); gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"cross_atr_d": 0.05, "end": "10:30", "excursion_atr_d": 0.2, "window": ["09:35", "10:30"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.05 ATR; P ~ 0.15

<!-- results:A009 -->

## A010 — Overnight inventory correction at the open

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA09 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 8 in family auction.
- **Rules:** HA09: open in the top 15% of the overnight range and ON return >= +0.30 ATR_d -> short at 09:35 (mirror long); 6-bar outcome vs time-matched. Primary test: r6 against time_matched, unit atr; G6 twin: none pre-registered; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"on_ret_atr_d": 0.3, "top_frac": 0.85}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** -0.03 to +0.03 ATR (no effect); P ~ 0.10

<!-- results:A010 -->

## A011 — VWAP 2-SD deviation -> reversion

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA10 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 9 in family auction.
- **Rules:** HA10a: first 5m close beyond RTH VWAP +/- 2 SD per side (decisions 10:00-15:00), toward VWAP; vs the same detector on EMA(20) +/- 2 rolling SD(20) of the residual. Primary test: r12 against ema_twin, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"window": ["10:00", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.02 ATR overall, +0.06 in low-ER days, negative in high-ER days; P ~ 0.15

<!-- results:A011 -->

## A012 — VWAP reclaim after a 2-SD deviation -> continuation

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA10 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 9 in family auction.
- **Rules:** HA10b: after the first -2 SD (+2 SD) deviation, the first later close back across VWAP; with the reclaim; vs the EMA twin. Primary test: r12 against ema_twin, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"window": ["10:00", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +-0.03 ATR; P ~ 0.15

<!-- results:A012 -->

## A013 — Poor highs/lows revisited more than excess extremes

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA11 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type M (mechanism / magnitude / filter; not a signed trade return); pre-data rank 10 in family auction.
- **Rules:** HA11: poor (top price >= 2 TPO letters) vs excess (>= 2 single-print ticks) extremes of day d; outcome = d+1 RTH trades through the extreme. Only extremes the d+1 open sits inside of (distance > 0). Primary: P(revisit) poor - excess, stratified by distance tercile (CMH weights), day-cluster bootstrap. Primary test: P(revisit next RTH) against excess extremes, unit none; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** difference ~ 0; P ~ 0.10

<!-- results:A013 -->

## A014 — POC migration -> next session direction

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA12 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 12 in family auction.
- **Rules:** HA12: next-day open->15:55 return in ATR_d on POC-up (POC_d > POC_d-1) vs POC-down days, controlling for the sign of day d's close-to-close return (OLS coefficient). Primary test: OLS coefficient of POC-up on next-day open->15:55 return against sign of day-d return, unit atr_d; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** no incremental effect; P ~ 0.05

<!-- results:A014 -->

## A015 — LVN fast travel (stability gate first)

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HA13 (hypotheses/HYPOTHESES_AUCTION.md)
- **Reason for test:** Event study of type M (mechanism / magnitude / filter; not a signed trade return); pre-data rank 13 in family auction.
- **Rules:** HA13: node stability gate first; only if >= 50% of reference nodes are stable, LVN traversal speed (median 5m bars to exit the far side of a 0.10 ATR_d zone) vs random zones of equal width inside the composite range. Positive effect = LVNs are faster. Primary test: median bars to traverse a 0.10 ATR_d zone against random zones of equal width, unit none; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"bin_ticks": [1, 2, 4], "composite_sessions": 5, "gate_frac": 0.5, "match_bins": 2, "min_settings": 7, "prominence": 0.1, "random_per_node": 5, "ref_bin_ticks": 1, "ref_sigma": 2, "sigmas": [1, 2, 4], "zone_atr_d": 0.1}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** stability gate likely fails at 1m resolution; P ~ 0.05

<!-- results:A015 -->

## B001 — Liquidity footprint at PDH/PDL/ONH/ONL/equal levels

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB00 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type M (mechanism / magnitude / filter; not a signed trade return); pre-data rank 0 in family ict.
- **Rules:** HB00 footprint: first RTH 1m bar trading >= 1 tick through PDH/PDL/ONH/ONL or an equal high/low active at the open; log(volume / same-minute median of 20 prior sessions), real levels minus shifted-reference levels (each level type on its near side of the open). Primary test: log volume ratio of the first 1m crossing against shifted_reference, unit none; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"equal_max_age_bars": 300}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** positive at PDH/PDL (+0.05 to +0.20 log points); equal highs uncertain

<!-- results:B001 -->

## B002 — PDH/PDL sweep and reclaim -> reversal

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB01 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 1 in family ict.
- **Rules:** HB01 reclaim sweep: high > PDH + 0.10 ATR, close < PDH within 3 bars (mirror PDL). Primary test: r12 against shifted_reference, unit atr; G6 twin: PA twin (identical detector on the nearest 6/6 pivot zones); gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"mode": "pen", "pen_atr": 0.1, "reclaim_bars": 3, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.02 to +0.08 ATR; reclaim variant >= wick variant; P ~ 0.25

<!-- results:B002 -->

## B003 — PDH/PDL wick sweep

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB01 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 1 in family ict.
- **Rules:** HB01 wick variant: the penetrating bar itself closes back inside. Primary test: r12 against shifted_reference, unit atr; G6 twin: PA twin (identical detector on the nearest 6/6 pivot zones); gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"mode": "wick", "pen_atr": 0.1, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** <= B002; P ~ 0.20

<!-- results:B003 -->

## B004 — PDH/PDL close sweep

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB01 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 1 in family ict.
- **Rules:** HB01 close variant: a close beyond PDH, then a close back below within 3 bars. Primary test: r12 against shifted_reference, unit atr; G6 twin: PA twin (identical detector on the nearest 6/6 pivot zones); gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"close_thr_atr": 0.0, "mode": "close", "reclaim_bars": 3, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** similar to B002; P ~ 0.20

<!-- results:B004 -->

## B005 — PDH/PDL multi-bar failed breakout

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB01 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 1 in family ict.
- **Rules:** HB01 multi-bar variant: >= 2 closes beyond, back inside within 6 bars. Primary test: r12 against shifted_reference, unit atr; G6 twin: PA twin (identical detector on the nearest 6/6 pivot zones); gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"max_bars_multi": 6, "min_closes": 2, "mode": "multi", "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** similar to B002; P ~ 0.20

<!-- results:B005 -->

## B006 — Overnight high/low sweep at the NY open -> reversal

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB02 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 2 in family ict.
- **Rules:** HB02: sweep-reclaim of the overnight high/low, decisions 09:35-11:00. Primary test: r12 against shifted_reference, unit atr; G6 twin: PA twin (identical detector on the nearest 6/6 pivot zones); gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"mode": "pen", "pen_atr": 0.1, "reclaim_bars": 3, "window": ["09:35", "11:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.06 ATR; P ~ 0.20

<!-- results:B006 -->

## B007 — Equal highs/lows vs single-swing sweeps

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB03 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type I (incremental comparison; refines a candidate, never a candidate by itself); pre-data rank 10 in family ict.
- **Rules:** HB03: sweeps of equal highs/lows vs sweeps of single pivots (3/3 pivots not part of an equal pair), stratified by age tercile x distance-from-open tercile (CMH weights). Primary test: r12 against single-pivot sweeps stratified by age x distance tercile, unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"max_age_bars": 300, "pen_atr": 0.1, "reclaim_bars": 3, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** difference ~ 0; P ~ 0.10

<!-- results:B007 -->

## B008 — Liquidity run - clean break that holds -> continuation

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB04 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 3 in family ict.
- **Rules:** HB04 run: first close > PDH + 0.10 ATR, the next 3 closes > PDH; event at the third (mirror PDL); vs time-matched. G6: the same detector on shifted-reference levels. Primary test: r12 against time_matched, unit atr; G6 twin: same detector on shifted-reference levels; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"hold_bars": 3, "thr_atr": 0.1, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.06 ATR; P ~ 0.20

<!-- results:B008 -->

## B009 — Sweep + displacement adds information

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB05 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type I (incremental comparison; refines a candidate, never a candidate by itself); pre-data rank 5 in family ict.
- **Rules:** HB05: B002 events whose reclaim bar is a displacement bar in the reversal direction vs B002 events whose reclaim bar is not. Primary test: r12 against B002 events without displacement, unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** ~ 0; P ~ 0.15

<!-- results:B009 -->

## B010 — Sweep + market structure shift

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB06 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 8 in family ict.
- **Rules:** HB06: after a B002 event, within 12 bars, the first close beyond the most recent swing (3/3, confirmed by the previous bar) in the reversal direction; vs time-matched. G6: the parent B002 entries of the same sessions. Primary test: r12 against time_matched, unit atr; G6 twin: the parent B002 entries of the same sessions; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"window": ["09:45", "15:00"], "within_bars": 12}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.04 ATR; MSS does not beat the reclaim entry; P ~ 0.15

<!-- results:B010 -->

## B011 — FVG first retrace -> continuation

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB07 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 4 in family ict.
- **Rules:** HB07: first retrace into the FVG within 24 bars that does not close through it; with the FVG; vs matched-displacement twins entered at the same relative depth. Primary test: r12 against matched_displacement_twin, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"formed": ["09:45", "14:30"], "max_bars": 24, "min_width_atr": 0.25, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** ~ 0 vs twin; P ~ 0.10

<!-- results:B011 -->

## B012 — FVGs get filled (dedicated FVG study)

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB08 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type M (mechanism / magnitude / filter; not a signed trade return); pre-data rank 7 in family ict.
- **Rules:** HB08: P(partial fill within 12 bars) of FVGs vs matched-displacement twins (same depth). Also the dedicated FVG table: full fill, time to fill, by width tercile and trend. Primary test: P(partial fill within 12 bars) against matched_displacement_twin, unit none; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"fill_bars": 12, "formed": ["09:45", "14:30"], "min_width_atr": 0.25, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** fill rate > 0.6 for both; difference ~ 0; P ~ 0.10

<!-- results:B012 -->

## B013 — Inverse FVG -> continuation in the new direction

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB09 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 12 in family ict.
- **Rules:** HB09 inverse FVG: a close through the far edge within 60 bars of formation (same session), direction against the original gap; vs the same close through the pre-move extreme of gap-free moves of the same size tercile that closed beyond it. Primary test: r12 against gap-free move closing back through its pre-move extreme, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"formed": ["09:45", "14:30"], "max_bars": 60, "min_width_atr": 0.25, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** ~ 0; P ~ 0.10

<!-- results:B013 -->

## B014 — Order-block retest -> reaction

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB10 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 14 in family ict.
- **Rules:** HB10 order block: a structure break (close beyond the last 3/3 swing) by a displacement bar; OB = last opposite-colour bar among the 5 before it; event = first bar within 48 that trades into the OB without closing through it (a close through voids it); vs zones of the same width at a uniform random depth of the impulse leg (5 draws). Primary test: r12 against random_depth_twin, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"max_bars": 48, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** ~ 0; P ~ 0.05

<!-- results:B014 -->

## B015 — Premium/discount location after trend control

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB11 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type M (mechanism / magnitude / filter; not a signed trade return); pre-data rank 11 in family ict.
- **Rules:** HB11a: every 6th RTH bar (from 09:30), location in the last confirmed swing range (3/3), deciles 1-10 inside [0, 1]; 12-bar long return; pooled within-stratum slope over structure trend x daily-ER tercile. Effect = -slope (positive = discount is better). Primary test: -slope of r12 on location decile against structure trend x daily ER strata, unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"every_bars": 6, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** slope ~ 0 after trend control; P ~ 0.05

<!-- results:B015 -->

## B016 — OTE band vs 0.50-0.62 band

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB11 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type I (incremental comparison; refines a candidate, never a candidate by itself); pre-data rank 11 in family ict.
- **Rules:** HB11b OTE: after a structure break, leg = last confirmed opposite swing -> running extreme (through the previous bar); first bar entering each retracement band within 24 bars (leg void when price passes the swing); OTE [0.62, 0.79] minus [0.50, 0.62) bands. Primary test: r12 against [0.50, 0.62) band entries, unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"bands": {"b38": [0.38, 0.5], "b50": [0.5, 0.62], "ote": [0.62, 0.79]}, "max_bars": 24, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** no Fibonacci peak; P ~ 0.05

<!-- results:B016 -->

## B017 — Killzone dependence of sweep outcomes

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB12 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type I (incremental comparison; refines a candidate, never a candidate by itself); pre-data rank 6 in family ict.
- **Rules:** HB12: (event - time-matched) of B002 + B006 sweeps in RTH_OPEN + MORNING minus the same difference in MIDDAY. Primary test: r12 against time-matched per bucket, unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"comparison_buckets": ["MIDDAY"], "killzone_buckets": ["RTH_OPEN", "MORNING"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** ~ 0 after normalisation; P ~ 0.10

<!-- results:B017 -->

## B018 — SMT divergence (needs ES + NQ) - BLOCKED

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HB13 (hypotheses/HYPOTHESES_ICT.md)
- **Reason for test:** Event study of type I (incremental comparison; refines a candidate, never a candidate by itself); pre-data rank 13 in family ict.
- **Rules:** HB13 SMT divergence: needs ES and NQ on one grid, and NQ may not be used before rule freeze (protocol 2.5). Registered as BLOCKED so the test count stays honest. Primary test: r12 against B002 events without divergence, unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** ~ 0 to slightly positive; P ~ 0.10 (blocked: NQ embargoed until rule freeze)

<!-- results:B018 -->

## C001 — First touch of prior-day extremes -> reaction

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC01 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 1 in family price_action.
- **Rules:** HC01: first zone entry of PDL (long) / PDH (short) in the session; vs shifted reference. Primary test: r12 against shifted_reference, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"break_atr": 0.25, "rearm_atr": 0.5, "window": ["09:45", "15:00"], "zone_atr": 0.1}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.02 to +0.08 ATR; P ~ 0.25

<!-- results:C001 -->

## C002 — Touch number - first touch strongest

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC02 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type I (incremental comparison; refines a candidate, never a candidate by itself); pre-data rank 5 in family price_action.
- **Rules:** HC02: all zone entries of PD and PZ levels numbered within the session; (event - time-matched) for touch 1 minus the same for touches 3+. Primary test: r12 against time-matched per touch number, unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"break_atr": 0.25, "rearm_atr": 0.5, "window": ["09:45", "15:00"], "zone_atr": 0.1}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** positive difference (+0.02 to +0.06); P ~ 0.20

<!-- results:C002 -->

## C003 — Recent levels beat old levels

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC03 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type I (incremental comparison; refines a candidate, never a candidate by itself); pre-data rank 8 in family price_action.
- **Rules:** HC03: first zone entries of pivot zones by zone age (sessions since the zone's first pivot): current session vs 1 vs 2-5 sessions; (ev - ct) current minus (ev - ct) 2-5. Primary test: r12 against time-matched per age group, unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"break_atr": 0.25, "rearm_atr": 0.5, "window": ["09:45", "15:00"], "zone_atr": 0.1}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** small positive; P ~ 0.15

<!-- results:C003 -->

## C004 — ATR close breakout (close > L + 0.25 ATR) -> continuation

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC04 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 4 in family price_action.
- **Rules:** HC04 ATR breakout: close beyond the level by > 0.25 ATR. Primary test: r12 against time_matched, unit atr; G6 twin: strength twin (same-minute bars with a body of the same sign within +-20%); gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.05 ATR; false-break rate 0.35-0.55; P ~ 0.20

<!-- results:C004 -->

## C005 — Wick breakout (high > L + 1 tick)

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC04 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 4 in family price_action.
- **Rules:** HC04 wick breakout: extreme beyond the level by more than 1 tick. Primary test: r12 against time_matched, unit atr; G6 twin: strength twin (same-minute bars with a body of the same sign within +-20%); gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** <= C004; P ~ 0.10

<!-- results:C005 -->

## C006 — Close breakout (close > L + 0.10 ATR)

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC04 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 4 in family price_action.
- **Rules:** HC04 close breakout: close beyond the level by > 0.10 ATR. Primary test: r12 against time_matched, unit atr; G6 twin: strength twin (same-minute bars with a body of the same sign within +-20%); gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.04 ATR; P ~ 0.15

<!-- results:C006 -->

## C007 — Volume breakout (close breakout, volume >= 80th pct of the slot)

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC04 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 4 in family price_action.
- **Rules:** HC04 volume breakout: C006 breakouts with bar volume >= 80th pct of the slot (20 sessions). Primary test: r12 against time_matched, unit atr; G6 twin: strength twin (same-minute bars with a body of the same sign within +-20%); gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.05 ATR; P ~ 0.15

<!-- results:C007 -->

## C008 — Strong-body breakout (displacement bar)

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC04 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 4 in family price_action.
- **Rules:** HC04 strong-body breakout: C006 breakouts on a displacement bar in the break direction. Primary test: r12 against time_matched, unit atr; G6 twin: strength twin (same-minute bars with a body of the same sign within +-20%); gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.05 ATR; P ~ 0.15

<!-- results:C008 -->

## C009 — Failed breakout -> reversal

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC05 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 2 in family price_action.
- **Rules:** HC05: close beyond a PD or PZ level by > 0.10 ATR, then a close back inside within 3 bars; reversal; vs shifted reference (PD and PZ levels shifted together). Primary test: r12 against shifted_reference (PD: other day; PZ: displaced zone set), unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"close_thr_atr": 0.1, "reclaim_bars": 3, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.03 to +0.10 ATR; larger than C001; P ~ 0.25

<!-- results:C009 -->

## C009vC001 — Failed breakout vs ordinary first touch (PD levels)

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC05 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type I (incremental comparison; refines a candidate, never a candidate by itself); pre-data rank 2 in family price_action.
- **Rules:** Registered HC05 comparison on PD levels: (C009 - shifted) minus (C001 - shifted). Primary test: r12 against (C009 - shifted) - (C001 - shifted) on PD levels, unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** positive; P ~ 0.20

<!-- results:C009vC001 -->

## C010 — Polarity - first retest of a broken level holds

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC06 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 10 in family price_action.
- **Rules:** HC06 polarity: after a C004 breakout, the first retest within 24 bars that holds (low <= L + 0.10 ATR, close >= L - 0.25 ATR); vs the same pipeline on shifted levels. Primary test: r12 against shifted_reference (PD: other day; PZ: displaced zone set), unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"break_atr": 0.25, "max_bars": 24, "window": ["09:45", "15:00"], "zone_atr": 0.1}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.05 ATR; P ~ 0.15

<!-- results:C010 -->

## C011 — Compression -> larger move (magnitude)

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC07 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type M (mechanism / magnitude / filter; not a signed trade return); pre-data rank 9 in family price_action.
- **Rules:** HC07 magnitude: mean |12-bar return| in ATR after a range break from compression minus after a range break without compression (also reported in ATR_d units). Primary test: |r12| against range breaks without compression, unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"atr_pct_max": 0.2, "pct_window": 500, "range_atr_max": 2.0, "range_bars": 12, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** positive in ATR units, ~ 0 in ATR_d units

<!-- results:C011 -->

## C012 — Compression breakout direction

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC07 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 9 in family price_action.
- **Rules:** HC07 direction: signed 12-bar return after compression breaks minus non-compressed breaks. Primary test: r12 against range breaks without compression, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"atr_pct_max": 0.2, "pct_window": 500, "range_atr_max": 2.0, "range_bars": 12, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** ~ 0; P ~ 0.10

<!-- results:C012 -->

## C013 — Higher-low confirmation in an uptrend -> continuation

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC08 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 12 in family price_action.
- **Rules:** HC08: structure trend +1 (3/3 pivots, close breaks) and a newly confirmed higher low -> long at the confirmation bar (mirror: trend -1, lower high); vs momentum twin (20 bars). Primary test: r12 against momentum_twin_20_bars, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"momentum_lookback_bars": 20, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** ~ 0; P ~ 0.10

<!-- results:C013 -->

## C014 — Round-number bounce

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC09 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 6 in family price_action.
- **Rules:** HC09a: zone entries at the nearest round number below (support) / above (resistance); vs the identical detector on the grid shifted by half a step. Primary test: r12 against half_step_grid, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"break_atr": 0.25, "rearm_atr": 0.5, "rn_step": {"ES": 25, "MES": 25, "MNQ": 100, "NQ": 100}, "window": ["09:45", "15:00"], "zone_atr": 0.1}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.03 ATR; P ~ 0.10

<!-- results:C014 -->

## C015 — Round-number cascade

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC09 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 6 in family price_action.
- **Rules:** HC09b: a bar whose previous close was below a round number closes >= 0.25 ATR above it (mirror down); with the cross; vs the half-step grid. Primary test: r12 against half_step_grid, unit atr; G6 twin: primary; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"rn_step": {"ES": 25, "MES": 25, "MNQ": 100, "NQ": 100}, "thr_atr": 0.25, "window": ["09:45", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.04 ATR; P ~ 0.10

<!-- results:C015 -->

## C016 — Rejection candle at the level adds information

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC10 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type I (incremental comparison; refines a candidate, never a candidate by itself); pre-data rank 11 in family price_action.
- **Rules:** HC10: C001 events whose touch bar is a rejection candle (wick >= 0.50 range toward the level, close location >= 0.65 away from it) vs the other C001 events. Primary test: r12 against other C001 events, unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"clv": 0.65, "wick_frac": 0.5}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** ~ 0; P ~ 0.10

<!-- results:C016 -->

## C017 — Level source - PD vs pivot zones (PW, RN descriptive)

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC11 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type I (incremental comparison; refines a candidate, never a candidate by itself); pre-data rank 7 in family price_action.
- **Rules:** HC11: (first touch - shifted) for PD levels minus the same for pivot zones; prior-week and round-number first touches against their own shifted reference are descriptive. Primary test: r12 against (PD - shifted) - (PZ - displaced zone set), unit atr; G6 twin: none pre-registered; gates G1, G2, G4, G5 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"break_atr": 0.25, "rearm_atr": 0.5, "window": ["09:45", "15:00"], "zone_atr": 0.1}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** PD > PZ by +0.01 to +0.05; P ~ 0.15

<!-- results:C017 -->

## C018 — 30-minute opening-range breakout -> continuation

- **Pre-registered (UTC):** 2026-10-07T15:12:36+00:00
- **Hypothesis:** HC12 (hypotheses/HYPOTHESES_PRICE_ACTION.md)
- **Reason for test:** Event study of type E (event effect; may become a strategy candidate); pre-data rank 3 in family price_action.
- **Rules:** HC12: 30-minute opening range (known at 10:00); first 5m close beyond either side, decisions 10:00-15:00; with the close; outcome to 15:55 in ATR_d vs time-matched. Primary test: reod (ATR_d) against time_matched, unit atr_d; G6 twin: none pre-registered; gates G1, G2, G3, G4, G5, G6 (RESEARCH_PROTOCOL.md 4).
- **Parameters:** {"common": {"bar_minutes": 5, "control_draws": 5, "cost_scenario": "BASE", "fdr_q": 0.1, "min_events": 100, "n_boot": 10000, "seed": 20261007}, "params": {"window": ["10:00", "15:00"]}}
- **Dataset:** ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json when the data is registered). No market data existed at pre-registration.
- **Expected outcome BEFORE running:** +0.00 to +0.05 ATR_d; P ~ 0.20

<!-- results:C018 -->
