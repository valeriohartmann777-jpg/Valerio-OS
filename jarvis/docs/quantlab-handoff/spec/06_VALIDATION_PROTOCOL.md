# 06 — Validation Protocol / Evidence Gate

## Leitgedanke
Der Simulator errechnet hypothetische historische Ausführung unter dokumentierten Annahmen. Nur ein Research-Protokoll kann falsche Sicherheit reduzieren. **Mehrfaches Durchprobieren verbraucht statistische Unabhängigkeit.** Jede getestete Variante muss im Experiment-Register erscheinen.

## Testpyramide

### Gate A — Valid execution & data
- Herkunft/Lizenz, Data Passport und QA OK.
- Handelsregeln sind eindeutig und bestätigt.
- Zeitkausalität und Kostenmodell bestanden; keine ungeregelten Stop/Limit-Ambiguitäten.
- Sind diese Bedingungen verletzt: `INVALID` / `UNSUPPORTED` statt P&L-Bewertung.

### Gate B — Baseline plausibility
- Complete trade ledger, net equity, fees, exposure and profit/loss, consistent with accounting.
- Benchmark matching exposure/timeframe where justified.
- Trade count, independence/correlation, period coverage, concentration and distribution given.
- Equity curve alone is **not** a verdict.

### Gate C — Generalization
- Chronological development vs untouched OOS. Specify cutoff before experiment; no random train/test shuffle for time series.
- If parameters are chosen from validation performance, that validation set becomes part of development. Use an independent **new final holdout** after such tuning.
- Walk-forward: parameter selection only within each train window, forward OOS chronological segments; overlapping examples may require purge/embargo.
- Parameter stability: scan only declared plausible ranges and keep full attempted variant log; robust plateaus better than sharp optima.

### Gate D — Costs, regimes & uncertainty
- Transaction fee, spread/slippage stress, realistic execution constraints.
- Market regimes, time periods, day-of-week/session diagnostics when sample supports them.
- Trade-order Monte Carlo is not always a valid statistical model. Use dependence-preserving bootstrap where justified and disclose assumptions; do not treat shuffled trades as robust OOS validation.
- Multiple comparison adjustment / PBO-style diagnostics only when enough appropriate independent configurations and data for meaningful calculation. Don't display invented significance scores.

### Gate E — Forward validation
- After robust historical evidence, paper trading with frozen parameters and timestamped outcome. Separate future test data from research decisions. No live money without separate risk/security approval outside MVP.

## Verdict enum
`INVALID`: data/causal/accounting failure. `FAILED`: predefined critical methodology/robustness gate fails. `INCONCLUSIVE`: incomplete/low-power evidence, conflicting tests, or only baseline. `PROMISING_RESEARCH_CANDIDATE`: positive, reproducible, reasonably generalizable evidence with caveats, suitable **for further forward validation, not live capital**.

## MVP verdict policy
R0/R1 only implements Data + Baseline + simple OOS. Even a positive run must default to `INCONCLUSIVE` with a list of missing gates, unless a validation failure makes it `INVALID` or `FAILED` based on explicit criteria. Never use „VALIDATED EDGE“ badge in R1.

## Report shape
Each check: `{ id, method_version, result: PASS|WARN|FAIL|NOT_RUN|N_A, assumptions, metric, observations, evidence_artifact }`.
JARVIS must output:
1. Evidence observed (separate train/OOS and net/gross).
2. Limitations and reasons for risk.
3. What cannot yet be concluded.
4. Most informative next *pre-registered* test.

## Statistical pitfalls
- Sharpe annualization from periodic samples requires a defensible frequency/serial-dependence assumption; warn if not.
- Bootstrap blocks should preserve time dependence when plausible.
- Multi-regime slices with tiny sample are descriptive not conclusive.
- PBO/DSR/FDR tools demand controlled design; cannot reduce research rigor to one „confidence score“.
- OOS sample sizes require effective observations, not just hundreds of highly correlated trades.

## Relevant independent sources
- Bailey et al., *The Probability of Backtest Overfitting*: https://escholarship.org/uc/item/4w1110bb
- VectorBT docs warn to shift signals when trading close-derived signals: https://vectorbt.dev/api/portfolio/base/
