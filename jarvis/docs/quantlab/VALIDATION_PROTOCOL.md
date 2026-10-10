# QuantLab validation protocol (futures research, v1)

Fixed in code (`backend/jarvis/quantlab/futures/validation.py`) and in every spec's
`validation` block. The spec is frozen by its hash before the run. Nothing here is tuned
after seeing results.

## Split

Strategy sessions (NYSE regular hours, New York wall clock, holidays removed, early closes
shortened) are taken in chronological order:

```
| in-sample | embargo | out-of-sample | embargo | holdout (sealed) |
```

- **Holdout:** the last `holdout_fraction` of sessions (default 15 %).
  - Never read by any test unless the user explicitly evaluates it.
  - Every evaluation is recorded per strategy family in `qr_holdouts`.
  - A second look on overlapping dates is flagged "not independent". The HOLDOUT test can
    then at best be a WARNING.
- **Out-of-sample:** the `oos_fraction` before the holdout (default 30 %).
- **Embargo:** `embargo_sessions` (default 1) between segments, used by nothing.
- Parameters are chosen only on in-sample data, or inside each walk-forward training window.

## Fill model (what "conservative" means)

1. **Market orders** (signals on a completed bar, time exits) fill at the next eligible bar's
   open, plus `slippage_ticks` against you. `latency_bars` delays eligibility.
2. **Stop orders** fill at the stop price, or at the bar's open if price gapped through it,
   plus slippage.
3. **Targets** (limit orders) fill at their price when price trades through it (`touch` is
   optional and labelled optimistic). A target that opened beyond the limit fills at the
   open.
4. **Same-bar stop and target:**
   - conservative mode takes the stop;
   - the optimistic mode takes the target and is reported as a bound;
   - the trade is flagged ambiguous.
5. **Stop entry and protective stop inside the entry bar:** conservative mode assumes the
   stop.
6. **Both breakout stops in one bar** (open inside the range): the day is excluded and
   counted (AMBIGUOUS_ENTRY), never guessed.
7. **Positions are flat at each session's end:**
   - exit at the open of the first bar at or after `flatten_at`;
   - if there is no such bar, at the last bar's close, flagged `LAST_BAR_CLOSE`.
8. **Roll in a session:** a session whose bars come from two contracts is skipped
   (SKIPPED_ROLL), so no P&L ever spans a roll.

## Tests

Each test returns PASSED, WARNING, FAILED, INCONCLUSIVE, NOT_APPLICABLE or NOT_RUN, with
assumptions, requirements, metric and interpretation.

| ID | Method | PASSED when |
|---|---|---|
| BASELINE | Out-of-sample net vs. a naive session-drift benchmark (long 1 lot from the window's first open to the flatten open, same costs), computed independently of the engine | strategy > benchmark (else WARNING, informational) |
| DATA_INTEGRITY | Strategy-specific Data Fitness (below) | FIT (LIMITED → WARNING, INVALID → FAILED) |
| INDEPENDENT_REFERENCE | Ledger audit: prices re-looked-up in bars, money from ticks × tick value, no fill before `known_at`, flat per session, one contract per trade, fees once per side, ledger = equity | all checks pass (conservative and optimistic runs) |
| LEAKAGE | All prices from the middle session on are reversed and jittered (tick grid kept); trades that closed before the cut must be identical | identical |
| OUT_OF_SAMPLE | Net after costs on OOS sessions, conservative fills | ≥ `min_trades_oos` trades and net > 0 (fewer trades → INCONCLUSIVE; net ≤ 0 → FAILED) |
| COST_STRESS | Commission + fees × k, slippage ticks × k (rounded up) for each `cost_stress` k | OOS net > 0 for every k (first k ≤ 0 → FAILED) |
| PARAMETER_SENSITIVITY | Pre-registered grid, in-sample only; share of positive grid points; is the chosen point an isolated peak (neighbours < 50 % of its net)? | ≥ 60 % positive and not isolated (< 30 % → FAILED) |
| WALK_FORWARD | Rolling or anchored windows over in-sample + OOS: best grid point on each training window (net, conservative), evaluated on the next test window; test windows stitched | stitched net > 0 and ≥ 50 % of windows positive (net ≤ 0 → FAILED) |
| BOOTSTRAP | Politis–Romano stationary bootstrap of OOS daily P&L (mean block `block_sessions`, `samples`, `seed`); 90 % interval of the mean, P(total ≤ 0), 95th percentile drawdown | lower bound > 0 (upper < 0 → FAILED, else INCONCLUSIVE) |
| SELECTION_BIAS | Deflated Sharpe ratio (Bailey & López de Prado 2014) on OOS daily returns, with N = distinct variants (versions × parameter sets) ever evaluated for the strategy family and the variance of their in-sample daily Sharpes | DSR ≥ 0.95 (0.5–0.95 INCONCLUSIVE, < 0.5 FAILED, < 30 sessions NOT_APPLICABLE) |
| REGIMES | OOS P&L by ex-ante volatility tercile (previous session's range; cut-offs from in-sample sessions only); weekday table for context | every regime with ≥ 10 trades ≥ 0 (no regime with 10 trades → INCONCLUSIVE) |
| SUBPERIODS | Net per calendar month (IS + OOS) | ≥ 50 % of months positive |
| TAIL_DEPENDENCE | OOS net without the 5 best trades; worst trade, streak, top-5 share | > 0 |
| AMBIGUITY | OOS conservative vs. optimistic net; share of ambiguous trades; excluded days | sign doesn't depend on intrabar order and share ≤ 10 % |
| HOLDOUT | The sealed sessions with the frozen spec, once | trades ≥ max(5, min_trades_oos / 3) and net > 0, and independent |
| CROSS_INSTRUMENT | — | NOT_RUN (R6) |
| FORWARD_PAPER | — | NOT_RUN (R7) |

## Verdict (applied in this order)

1. **INVALID_DATA_OR_METHOD:** DATA_INTEGRITY, LEAKAGE or INDEPENDENT_REFERENCE FAILED.
2. **INSUFFICIENT_EVIDENCE:** OUT_OF_SAMPLE INCONCLUSIVE.
3. **REJECTED_HYPOTHESIS:** OUT_OF_SAMPLE, HOLDOUT or BOOTSTRAP FAILED.
4. **PROMISING_RESEARCH_CANDIDATE:** OOS passed, but any of WALK_FORWARD, COST_STRESS,
   PARAMETER_SENSITIVITY, BOOTSTRAP, SELECTION_BIAS, AMBIGUITY, TAIL_DEPENDENCE or HOLDOUT is
   not PASSED. The unmet gates become the next steps.
5. **FORWARD_VALIDATION_REQUIRED:** every one of those PASSED, including an independent
   holdout.
6. **ROBUST_UNDER_TESTED_ASSUMPTIONS:** reserved. It needs forward paper evidence, which isn't
   built.

On synthetic (fixture) data the verdict is always INSUFFICIENT_EVIDENCE. `would_be` shows what
the gates alone would say.

## Data Fitness (per strategy × dataset)

| Status | Rules |
|---|---|
| INVALID | Any of the following: quality BLOCK (duplicates, OHLC violations, misaligned or non-positive prices, off-tick prices); a contract-spec BLOCK (inconsistent definition, missing tick size); instrument mismatch (e.g. an MNQ spec on NQ data). |
| INSUFFICIENT | Fewer than 40 observed sessions. |
| FIT_WITH_LIMITATIONS | Any of the following: assumed contract specs, provider-degraded days, missing sessions, RTH gaps, jumps, sessions skipped for rolls, intrabar order dependence (stops, targets, stop entries on 1-minute bars), no bid/ask (spread inside slippage). |
| FIT | None of the above. |

## Metrics conventions

- **Daily series:** net per strategy session. Sessions without a trade count as 0. Skipped or
  no-data sessions are excluded and counted separately.
- **Returns:** net divided by the stated account capital, no compounding.
- **Sharpe / Sortino:** mean divided by (downside) deviation of daily returns, × √252,
  risk-free rate 0. Only with ≥ 20 sessions and non-zero deviation.
- **Annualized return:** only with ≥ 60 sessions. **Calmar:** only with ≥ 120 sessions.
- **Profit factor:** undefined without losing trades (`None` with the reason, never ∞).
- **MAE/MFE:** whole-minute extremes of the entry and exit bars (upper bounds).
