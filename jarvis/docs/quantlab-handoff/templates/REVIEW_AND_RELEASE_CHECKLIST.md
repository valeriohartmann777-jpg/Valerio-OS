# R0+R1 Review & Release Checklist

## Scientific correctness
- [ ] Causal timestamp and available-at tests pass.
- [ ] Cash/position accounting audited independently.
- [ ] Fees and modeled slippage applied to **both** sides.
- [ ] Data gaps, incomplete instruments and unsupported fill types displayed.
- [ ] OOS labels are chronological and do not leak into optimization.
- [ ] Experiment manifest is complete and repeatable.
- [ ] No fabricated metrics or hardcoded bullish sample chart is presented as true.

## Product usability
- [ ] 1 user can start at QuantLab -> choose strategy -> import data -> run -> inspect report.
- [ ] Missing critical settings block intelligently, with reasons.
- [ ] User can open trades and know signal time vs fill time.
- [ ] Validation status is transparent and conservative.
- [ ] Loading, empty, error, incomplete data, no trades handled.

## Architecture
- [ ] Existing JARVIS remains operational.
- [ ] No unbounded network/filesystem access or broker execution.
- [ ] Typecheck/build/backend tests pass.
- [ ] Duplicates or canceled jobs don't corrupt completed reports.
- [ ] Side effects and artifacts audited, no secrets in logs.

## Delivery
- [ ] README exact startup instructions tested.
- [ ] Implementation status honest about unsupported features.
- [ ] UI visually compared to deck / correct product design.
- [ ] Known limitations and next milestones explicitly documented.
