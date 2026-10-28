# JARVIS APEX — implementation status (recovery & validation)

Brief: `JARVIS_APEX_Claude_Code_Recovery_Validation_Masterprompt.md` (2026-10-28). Branch
`claude/jarvis-foundation-mxnsz4`. Rollback point before the recovery: tag
`apex-recovery-start` (= `40d2f71`). Decision: `DECISIONS.md` D-035.

Four things are kept apart everywhere, as the brief requires:

| | State on 2026-10-28 |
|---|---|
| **Software correctness** | tested — unit, integration and end-to-end (`APEX_TEST_EVIDENCE.md`) |
| **Data availability** | **not shown on real data.** This work ran in a cloud container whose network policy refuses Dukascopy and Databento; the owner's Mac was not reachable (no Remote Control folder shared). Every run here used generated test data, labelled SYNTHETIC |
| **Historical robustness** | **no evidence yet.** No validation has run on real data; on test data the protocol correctly finds nothing (RESEARCH_ONLY) |
| **Forward predictive quality** | **no evidence yet.** The paper forward log exists and is off until the owner switches it on |

No APEX signal is validated. Nothing in this repository says otherwise.

## Milestones

| | Content | State |
|---|---|---|
| **P0** Backend reachable | `/health/live`, `/health/ready` (database, schema, data folder, APEX), `/system/capabilities` (`orders: false`); supervisor: captured + redacted output, rotating log file, cause classification (missing package, port in use + owner, database, permission, crash, no Python, old backend, slow start, install failed), dependency sync before start, stop after 5 quick crashes, serialised starts (no orphan backend), IPC status/restart/repair; header "Backend online" only with readiness; BackendBanner with Retry / Restart / Repair / diagnostics; APEX reloads after reconnect | **done** — `APEX_RECOVERY_AUDIT.md` |
| **P1** Data fitness | fitness view (source, contract, quote side, last closed bar, expected vs present bars, gaps by reason, spikes with minutes, integrity, execution, hints); quality gate unchanged in strictness; XAUUSD bid-only → `execution_assumption_limited`; NQ/MNQ CFD proxy → `DATA_UNAVAILABLE` for plans, backtests, validations | **done** — `APEX_DATA_FITNESS.md` |
| **P2** Point-in-time engine | engine `apex-1.2.0`; `zone_rejection_5m` 1.0.0 (SETUP → 5-minute TRIGGER → stop ORDER_PLAN, `WAITING_FOR_TRIGGER`); `zone_reaction` 1.1.0; execution model (bid/ask side); resolver with stop/market entries, gaps, cancellation; typed immutable `Signal` (pydantic + TypeScript), migration 21 (reversible, tested), `GET /apex/signals`; SENTINEL compares per-strategy decisions | **done** — `APEX_SIGNAL_SPEC.md` |
| **P3** Validation | protocol 1.1.0 pre-registered; frozen holdout; embargoed folds; day-block bootstrap; random baseline; Bonferroni over an append-only trial register; cost stress; neighbours; one holdout opening per version; background job with progress/cancel; verdict → `validation_state` on new signals | **done (mechanics)** — `APEX_VALIDATION_PROTOCOL.md`; real-data verdicts **pending** |
| **P4** UX | status strip App / Data / Signal; Analyst panel per strategy (data ready/blocked, structure, setup, trigger, entry/SL/TP, R:R after costs, evidence, why this setup, why not trade); Start analysis as a job (elapsed, Cancel, Retry); tabs Signal journal, Backtest (per strategy), Validation, Data fitness, SENTINEL | **done** |
| **P5** Forward & ULTRON | opt-in paper forward log (every 5 min in session, persisted switch, run-now); forward evidence (live, deduplicated, current version) lifts OOS_SUPPORTED → FORWARD_SUPPORTED only on real data; CIPHER (track records, validations) and SENTINEL (re-checks) in the ULTRON HQ | **done (mechanics)**; forward evidence **pending** |
| Docs | `APEX_RECOVERY_AUDIT.md`, `APEX_ARCHITECTURE.md`, `APEX_DATA_FITNESS.md`, `APEX_SIGNAL_SPEC.md`, `APEX_VALIDATION_PROTOCOL.md`, `APEX_TEST_EVIDENCE.md`, this file | done |

## ULTRON

Used where it already works without an AI connection: SENTINEL (deterministic re-check of a
stored analysis) and CIPHER (track records, validation runs) — both real work shown in the HQ.
The language-model agents need a connected Claude plan or API key; none was available here, so
none was simulated. APEX numbers never come from a model.

## Blocked / missing evidence

| What | Why | Unblocks it |
|---|---|---|
| Cause of the owner's "backend not reachable" on the Mac | the Mac was not reachable from this session | the updated app shows the cause in the window and in `~/Library/Application Support/@jarvis/desktop/logs/backend-console.log` |
| Real XAUUSD results | Dukascopy refused by this container's network policy | Mac: APEX → Data fitness → Repair, then Start analysis |
| Real NQ/MNQ results | no futures bars available here; buying data needs the owner's explicit approval | Data Vault import (CSV/Parquet) or a Databento quote → approval |
| Validation verdicts | need the real data above | APEX → Validation → Run validation (each strategy) |
| Forward evidence | needs weeks of live sessions | switch the paper forward log on |

## Next action (owner, on the Mac — about 10 minutes, no cost)

1. Update and open JARVIS. If the backend can't start, the window names the cause; press
   **Repair** (missing package) or **Restart**.
2. QuantLab → APEX → XAUUSD → **Data fitness** → Repair data; wait until the status is not BLOCK.
3. **Start analysis** (with "Update data first").
4. **Validation** → Zone reaction → Run validation; then 5-min rejection → Run validation
   (the 5-minute one takes several minutes).
5. **Paper forward log** → Switch on.
6. For NQ/MNQ: Data Vault → import futures bars (or request a Databento quote — it asks before
   any purchase).
