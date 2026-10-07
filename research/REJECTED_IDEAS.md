# REJECTED_IDEAS

Every idea that was rejected, deferred or blocked, with the reason, so it is not tried
again by accident and so the multiple-testing burden stays visible. Entries are only
appended. Results-based rejections are added as each experiment concludes.

## A priori decisions (2026-10-07, before any data)

| # | idea | family | status | reason | would be reopened by |
|---|---|---|---|---|---|
| R01 | Initiative vs responsive activity in the **order-flow** sense (aggressor side, delta, absorption, footprint) | A, B | REJECTED — untestable | 1m OHLCV has no aggressor flag or bid/ask; approximating it from bar shape would be fake order flow | tick data with aggressor side or bid/ask volume |
| R02 | Orochi-specific rules | A | BLOCKED | no verifiable source for a rule set under that name; generic AMT used instead | the user supplying Orochi material |
| R03 | SMT divergence (HB13) | B | BLOCKED — registered | needs ES and NQ on one timestamp grid | NQ data |
| R04 | "Smart money intent", institutional order-flow narratives | B | REJECTED — not falsifiable | intent is unobservable; only the claimed price behaviour is tested | — |
| R05 | Accumulation phase of Power of Three | B | REJECTED — not causal | accumulation is only identifiable in hindsight; the manipulation → distribution part is tested as A009 | a causal definition |
| R06 | Daily bias / draw on liquidity as a standalone predictor | B | DEFERRED | the educator definition is discretionary; a causal version (structure trend) is used only as a filter in ablations and as a target family | — |
| R07 | Breaker and mitigation blocks | B | DEFERRED | variants of order blocks; tested only if HB10 (B014) shows an effect, to avoid spending tests | B014 passing its gates |
| R08 | Trendlines and channels | C | DEFERRED | definable only loosely; low priority behind horizontal levels | horizontal-level hypotheses showing an effect |
| R09 | Exact killzone minutes (e.g. 09:50–10:10 "macro" windows) | B | REJECTED | minute-level windows invite overfitting; broad buckets only (HB12) | — |
| R10 | Dealer gamma / options positioning | A | REJECTED — no data | not observable in the available data; noted as an unobserved confounder | options open-interest data |
| R11 | Machine-learning filters | all | EXCLUDED | mission rule: no ML during discovery | a robust base edge, phase 2 |
| R12 | Profile-shape labels as standalone signals beyond HA12 | A | DEFERRED | shapes summarise the day's path; tested once inside HA12 | HA12 showing incremental information |

## Results-based rejections

_None yet: no real data has been available._
