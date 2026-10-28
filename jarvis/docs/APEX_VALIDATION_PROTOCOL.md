# JARVIS APEX — validation protocol

Protocol **1.1.0**, pre-registered in `config/apex_validation.yaml` on 2026-10-28, before any
holdout of any APEX strategy was evaluated and before any real market data was analysed with
it. Code: `backend/jarvis/apex/validation.py`; job: `ApexService.validate`; API:
`POST /apex/validations`, `GET /apex/validations[/{id}]`, `POST /apex/validations/{id}/cancel`,
`GET /apex/trials`; UI: APEX → Validation.

**Nothing in this protocol chooses parameters.** The strategies run with their registered
values (`config/apex.yaml`); neighbours are measured, never adopted. A changed rule or number
is a new strategy version, a new set of trials and its own holdout opening.

Protocol history: 1.0.0 set the embargo to the maximum hold only (480 min). A label of
`zone_reaction` lasts up to validity + hold = 720 min, so 1.0.0 would have let development
outcomes overlap the next fold and the holdout. Corrected to 1.1.0 **before the first run**;
no verdict was ever made with 1.0.0.

## 1. Split

1. Trading days = New York weekdays with bars; the last day with bars is left out (it may
   still be trading).
2. The first `warmup_days` (21) only feed zones, structure and base rates.
3. **Holdout:** the last `holdout_share` (20 %) of the remaining days at the **first**
   validation run for this market, data source and data kind (real / test). The period is then
   **frozen** (`apex_holdouts`, `UPDATE` refused by a trigger). Later runs reuse it exactly.
4. **Development window:** the days before the holdout, divided into `folds` (4) contiguous
   chronological folds (fold *i* ends where fold *i+1* begins; the last ends where the holdout
   begins).

## 2. Embargo (purging)

The outcome window of a decision is entry validity + maximum hold (zone_reaction 240 + 480 =
720 min; zone_rejection_5m 15 + 480 = 495 min). The embargo is
max(`embargo_minutes` = 480, that window). Decisions later than *fold end − embargo* are
dropped, at every fold end and before the holdout. No development outcome uses a bar of the
next fold or of the holdout.

## 3. Development measurements

Decisions are made exactly as live (`evaluate.collect` → `engine.plans_at`): every hour of the
session window for `zone_reaction`, every 5-minute close for `zone_rejection_5m`; a repeat of
the same day, direction and entry counts once. Outcomes: `resolve.py` with the execution model
(bid/ask side, spread, costs; conservative same-bar rule).

| Measure | Definition |
|---|---|
| mean R | over filled trades (win, loss, timeout) |
| 95 % interval | day-block bootstrap (`bootstrap_resamples` 2000): whole days are resampled, because trades of one day are dependent; seed = data hash (reproducible) |
| folds | mean R and interval per fold |
| cost stress | the same trades with spread and costs × `cost_stress_factor` (1.5) |
| neighbours | each parameter in `neighbours` moved one step (min_rr 1.25/1.75, reach_atr 2/4, stop_buffer_atr 0.10/0.20), the others registered |
| random baseline | `baseline_runs` (200) runs; each real filled trade is replaced by a market entry at a random decision time of the development window, random side, the same risk and target distance, the same execution model. p = (1 + runs with mean R ≥ the strategy's) / (1 + runs) |
| trials | every parameter set measured is appended to `apex_trials` (append-only); family = market + strategy, real and test data counted apart |

## 4. Pre-registered criteria (all must pass)

| Code | Required |
|---|---|
| `EVIDENCE` | ≥ `min_dev_trades` (60) filled trades on ≥ `min_trade_days` (20) days |
| `DEV_CI` | 95 % interval of mean R above 0 |
| `BASELINE` | baseline p < `alpha` (0.05) / number of trials registered in the family (Bonferroni) |
| `COST_STRESS` | mean R > 0 with 1.5 × costs |
| `NEIGHBOURS` | ≥ 66 % of the neighbours with mean R > 0 |
| `FOLDS` | ≥ 75 % of the folds with mean R > 0 |

## 5. Holdout

Opened only when every development criterion passed — and **once** per market, data source,
data kind, strategy and version (`apex_holdout_opens`, primary key; `UPDATE` refused). The
first result stands; a later run of the same version reports it ("opened by …") and never
re-evaluates. Pass: ≥ `min_holdout_trades` (30) filled, mean R > 0 and interval above 0.

## 6. Verdict → `validation_state`

| Case | State |
|---|---|
| fewer trades than `EVIDENCE` requires | `RESEARCH_ONLY` (no conclusion) |
| any development criterion failed | `REJECTED`, holdout stays closed |
| development passed, holdout has too few trades | `RESEARCH_ONLY` |
| development passed, holdout failed | `REJECTED` |
| development and holdout passed, **real data** | `OOS_SUPPORTED` |
| `OOS_SUPPORTED` and the paper forward journal passed (§7) | `FORWARD_SUPPORTED` |
| any result on generated test data | at most `RESEARCH_ONLY` |

New signals of the strategy version carry the latest state and the validation id as evidence.
A `CANDIDATE` becomes `PAPER_VALIDATED` only under `OOS_SUPPORTED` / `FORWARD_SUPPORTED`.

## 7. Forward paper evidence

Forward = real-data signals recorded live by the forward log (APEX → Validation → Paper
forward log; not replays, not test data) of the same strategy version, resolved on later bars.
Pass: ≥ `forward.min_trades` (30) filled and the day-bootstrap interval of mean R above 0.
Until then the forward section says how many trades are missing.

## 8. Probabilities and calibration

APEX shows **no per-signal probability**. The scenario rates are counted shares of earlier
touches (Wilson interval, n, days); their calibration against later touches (reliability bins,
Brier score vs. a side-only reference) is reported with every track record
(`docs/apex/APEX_METHOD.md` §10b). A probability field would need ≥ 30 calibration
cases and its interval — the signal contract refuses anything less.

## 9. What this protocol cannot do

- It cannot make up for data: XAUUSD results are `execution_assumption_limited` (bid candles,
  modelled ask). NQ/MNQ need futures bars in the Data Vault; the CFD proxy is refused.
- Two strategies × several neighbours are many tests; the Bonferroni correction is strict on
  purpose. A `RESEARCH_ONLY` or `REJECTED` result is a finding, not a failure of the software.
