# APEX — validation protocol 1.2.0

Pre-registered in `config/apex_validation.yaml` on 2026-10-28, **before any run on real data**
(none was possible: no market data had been imported). 1.2.0 adds section `probability`; every
1.1.0 number is unchanged — the full 1.1.0 text (split, embargo, development criteria, holdout,
verdict) is `docs/APEX_VALIDATION_PROTOCOL.md`. Code: `validation.py`, `probability.py`.

## Strategy validation (unchanged from 1.1.0)

Chronological split after 21 warm-up days: development folds (4) and a final holdout (last
20 %), **frozen** at the first run per market, source and data kind; embargo = the strategy's
whole label horizon (entry validity + maximum hold, at least 480 min). Development criteria
(all must hold before the holdout opens): ≥ 60 filled trades on ≥ 20 days; day-block bootstrap
95 % interval of mean R above 0; random baseline p < 0.05 / trials; mean R > 0 at 1.5 × costs;
≥ 66 % of one-step parameter neighbours and ≥ 75 % of folds with mean R > 0. Holdout opened
**once** per strategy version (`apex_holdout_opens`). Every measured variant is a registered
trial (`apex_trials`), failures included. Generated data never yields more than RESEARCH_ONLY.
The zone setups are validated at every 5-minute close (exact zone-map cache) with their own
horizon (15 min validity + 480 min hold).

## Probability head (new in 1.2.0) — P(TP before SL after the fill)

Fitted only inside the development window of a setup validation:

| Window | Share of the development time | Use |
|---|---|---|
| fit | first 60 % | L2 logistic regression (C = 1), standardised features |
| calibration | next 20 % | Platt scaling only |
| test | last 20 % | graded once |

Trades whose label horizon crosses a window boundary are dropped (embargo); the holdout is
never read. Label: target 1 first = 1; stop first (a minute through both counts as the stop)
or neither within the hold = 0; unfilled plans are outside the event (reported as fill rate).
Features (all known at the decision): break-even probability after 1.5 × costs, zone quality /
100, with the 60-minute trend, long / short, stop in ATR.

Minimums: fit 60, calibration 30, test 30 filled trades — else `INSUFFICIENT_DATA`, no model.

VALIDATED only if all hold on the test window: effective sample (day-clustered design effect)
≥ 30; Brier skill > 0 against the base rate; skill above the 95th percentile of 20 label
shuffles (permutation negative control); calibration slope within 1 ± 0.30;
|mean predicted − observed| ≤ 0.05. Reported: Brier, log loss, skill, slope, intercept,
reliability tenths with Wilson intervals.

A plan's probability = the calibrated output; its interval and effective sample = the test
window's reliability tenth it falls in. Before it can reach the gate the head must be **promoted**:
SENTINEL review (validated, real data, current protocol, setup version OOS_SUPPORTED, no failed
re-check) and the owner's approval (`governance.py`, `APEX_MODEL_CARD.md`).

## Negative controls (tested)

A random walk (outcomes independent of every feature) is never validated (three seeds); three
"80 %" trades give no model at all; a head with real dependence is validated with slope ≈ 1
— on generated data only.
