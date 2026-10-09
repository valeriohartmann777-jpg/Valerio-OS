# 08 — Releases, Tickets, Definition of Done

## Execution strategy
Work sequentially in small inspect->build->test->verify->document increments. The actual repository decides file paths. All tests and functionality listed as planned until run in the real environment.

### T0 — Repo reconnaissance (no destructive changes)
- Detect real JARVIS app stack, routes, styling, backend and build commands; preserve root CLAUDE.md and unstaged modifications.
- Record constraints, module location, exact commands, integration decisions. Deliver one-page plan; proceed unless technically blocked.
**Done:** `docs/quantlab/IMPLEMENTATION_STATUS.md` with observed repo, chosen integration, initial test baseline.

### T1 — Route & UI shell (R0)
- Add QuantLab navigation without breaking core Jarvis. Create Overview, Strategies, Datasets, ExperimentDetail routes and accessible empty/error/loading states.
- Reuse existing UI libraries. Build coherent shell responsive, no fake performance data.
**Done:** UI loads, navigation works, existing app tests pass, at least one screenshot visual smoke review.

### T2 — Strategy registry (R0)
- Build StrategySpec JSON Schema/Pydantic, canonical SHA/versioning, basic MA-Crossover guided form; validation errors shown.
**Done:** create/read/version StrategySpec; old versions unmodified; roundtrip+hash tests.

### T3 — Data Passport importer (R0)
- Upload CSV/Parquet; enforce schema/time/QA, immutable snapshot, checksum, provenance and accepted/warn/rejected UX.
**Done:** reject broken fixture; import deterministic valid fixture as test-only; real import possible with user-supplied historical data.

### T4 — Reference simulator (R1)
- Pure deterministic MA signals; signal t close -> fill t+1 open; fees, slippage, capital, ledger, equity; cash long-only market orders. Explicit unsupported scope.
**Done:** exact golden execution test, independent MA fixture, invalid-asset checks and reproducibility.

### T5 — Experiment runner + storage (R1)
- Run manifest, queued->running->complete/failed status, status events, cancel when feasible, artifacts checksums and audit.
**Done:** repeat -> byte-equivalent numerical output with matching inputs (ignoring explicitly documented non-semantic timestamps).

### T6 — Chronological OOS + Metrics (R1)
- Predefined split, train/OOS labels, baseline performance, correct net/gross and drawdown calculations, insufficiency states.
**Done:** split strictly ordered, no strategy parameters tuned on OOS, proper OOS caveat.

### T7 — Dashboard & acceptance (R1)
- Equity/drawdown/trades/results details/report; show warnings, synthetic labels, status `INCONCLUSIVE` or `INVALID`/`FAILED` as appropriate.
- Smoke-test end-to-end with actual uploaded data; if no true market file available, document real-data block clearly and still pass synthetic engineering fixtures.
**Done:** backend pytest, UI typecheck/build, E2E or manual acceptance, docs and diffs reviewed.

## After R1 only
**R2**: walk-forward, time-safe parameter grids, realistic cost stress, trade dependence bootstrap, market regimes, static reports. **R3**: NL->Spec with LLM, critical copilot, comparisons, supervised planning. **R4**: paper-forward testing, model drift, live operational observability. Each separate acceptance gate.

## Release acceptance gate (MVP)
A1 QuantLab works inside real JARVIS, preserves existing features.
A2 Strategies immutable versioned; validation errors surfaced.
A3 CSV/Parquet import tracks provenance and QA; corrupt input rejected.
A4 Execution runs on real provided data; synthetic runs clearly labeled.
A5 Signal close -> next open execution; no future info.
A6 Orders/trades/equity/cash/fees reconcile exactly to fixtures.
A7 Split chronological; holdout not used for training/selection.
A8 Run manifest and artifacts persisted with checksums; rerun reproducible.
A9 UI accessible, polished, no fabricated metrics, clear unsupported states.
A10 Tests/build execute successfully on actual developer machine; verified results documented.
A11 No brokerage orders, no arbitrary LLM code or secret leakage.

## Explicit stop conditions
- Important math invariants fail -> don't declare R1 complete.
- Real historical dataset absent -> test with SYNTHETIC fixture and report that no market validation was performed.
- Existing Jarvis architecture cannot be identified -> build in isolated module and explain actual integration blocker, no false claim.
