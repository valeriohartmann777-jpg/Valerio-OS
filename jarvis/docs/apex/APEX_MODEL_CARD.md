# APEX — model card: trade-success probability head `ptp-1.0.0`

| | |
|---|---|
| Predicts | P(target 1 before the stop, after the fill, within the maximum hold) for one plan of one zone setup (`sweep_reclaim`, `rejection_rotation`, `breakout_retest` 1.0.0) |
| Scope | one market × data source × setup version × kind of data (real / generated); never transferred to another |
| Model | L2-regularised logistic regression (C = 1) on 5 standardised features + Platt scaling |
| Features | break-even probability after 1.5 × costs · zone quality / 100 · with the 60-minute trend (1 / 0 / 0.5 unknown) · long (1 / 0) · stop in ATR(15m) |
| Training data | filled plans of the setup's development window (fit 60 % / calibration 20 % / test 20 % of the time, embargoed) |
| Excluded | the frozen holdout; unfilled plans (fill rate reported); later bars |
| Grading | Brier, log loss, Brier skill vs base rate, calibration slope / intercept, reliability tenths (Wilson), effective sample, permutation control (20 shuffles) |
| Acceptance | protocol 1.2.0 (`APEX_VALIDATION_PROTOCOL.md`) |
| Uncertainty shown | the test-window reliability tenth's Wilson interval and effective sample |
| Storage | `apex_probability_models` (immutable), registered as a trial |
| Governance | propose (anyone) → SENTINEL review / veto → owner approval → may revoke; events in `apex_promotions` |
| Use | only an APPROVED head's output reaches the eligibility gate; an unpromoted head shows "VALIDATED — NOT PROMOTED" |

## Current instances

**None on real data.** No real market data was reachable in the build session, so no head was
fitted on real data, none is validated, none is promoted, and APEX shows
`PROBABILITY NOT VALIDATED` everywhere. Heads fitted on generated data are labelled test data and
are vetoed by SENTINEL (`REAL_DATA`).

## Known limits

- Bar data cannot resolve the order of high and low inside a minute; a minute through stop and
  target counts as a loss (conservative), which biases the estimate down, never up.
- Five simple features: the head can only be as good as the setup's structure; no order-flow
  features exist without trade data.
- Trades of one day are correlated; the effective sample (ICC design effect) accounts for it,
  overlapping plans of the same zone are reduced by the plan de-duplication of the track record.
- Calibration can drift with the market regime; the paper forward log (APEX → Validation) is the
  independent check (`PAPER_FORWARD` checkpoint).
- An LLM never produces or overrides this number.
