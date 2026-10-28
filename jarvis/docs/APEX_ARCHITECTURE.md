# JARVIS APEX — architecture

Research and paper only: no module, route or button places, sends or simulates a broker order.
`GET /system/capabilities` reports `apex.orders: false`; the E2E run checks that no `/apex`
route mentions order, trade, broker or execute.

## 1. Processes

```
JARVIS.app / npm start
  └─ Electron main (apps/desktop/electron)
       ├─ BackendSupervisor (backend.ts) ── spawns  backend/.venv/bin/python -m jarvis
       │     · installs changed Python packages first (dependency stamp)
       │     · keeps the last 200 lines (keys redacted) + logs/backend-console.log
       │     · classifies why a start failed; stops after 5 quick crashes
       │     · IPC jarvis:backend:status | restart | repair
       └─ Renderer (React)
             · header "Backend online" = event stream up AND GET /health/ready passed
             · BackendBanner: cause, remedy, Retry / Restart / Repair / diagnostics
             · QuantLab → APEX
FastAPI backend (backend/jarvis)
  · /health/live · /health/ready · /system/capabilities   (api/health_routes.py)
  · /apex/*                                                (api/apex_routes.py)
  · ApexService (apex/service.py) + SQLite (storage/database.py, migrations 20-21)
```

## 2. APEX pipeline

```
sources (config/apex.yaml, in order)        Series (1-minute bars + provenance, content hash)
  vault:NQ/MNQ  → Data Vault snapshots ─┐
  dukascopy:*   → learning cache       ─┼─▶ series.until(as_of)   ← nothing after the moment
  fixture       → generated, SYNTHETIC ─┘
        │
        ▼
quality gate (data.assess) ──BLOCK──▶ every strategy BLOCKED (with the gate's reasons)
        │                    stale ──▶ every strategy BLOCKED ("update first")
        ▼
execution model (execution.py): bid+spread | futures flat costs | DATA_UNAVAILABLE (proxy)
        │
        ▼
engine.plans_at(as_of)  ── structure (confirmed swings, BOS/CHoCH, 15m/60m)
                         ── zones (briefing key levels → zones, ATR width)
                         ├─ zone_reaction 1.1.0      → limit plans or rejections
                         └─ zone_rejection_5m 1.0.0  → SETUP → TRIGGER (5m) → stop plan
        │
        ▼
analysis (stored, apex_analyses) ─▶ candidates (apex_candidates, immutable)
                                 ─▶ signals    (apex_signals,    immutable; signal.py contract)
        │
        ▼
resolve.py on later bars of the same source ─▶ apex_outcomes (final = never changed)
```

The same `plans_at` runs live, in the track record (`evaluate.py`), in the validation
(`validation.py`) and in SENTINEL's re-check — there is one implementation of each rule.

## 3. Research layers

| Layer | Module | Output |
|---|---|---|
| Backtest (track record) | `evaluate.py` | walk-forward as if live; IS / OOS split; calibration of the zone rates |
| Validation | `validation.py` | pre-registered protocol 1.1.0: folds + embargo, bootstrap, baseline, cost stress, neighbours, trials, frozen holdout → `validation_state` |
| Data fitness | `fitness.py` | coverage, gaps by reason, spikes, quote side, contract |
| Forward paper log | `ApexService` forward loop | live analyses on a schedule (opt-in); signals and outcomes as forward evidence |
| SENTINEL re-check | `audit.py` | recompute a stored analysis with later data present: same data, no look-ahead, same outcomes |

## 4. Storage (SQLite, `data/jarvis.db`)

| Table | Migration | Rule |
|---|---|---|
| `apex_analyses` | 20 | one row per analysis (content id) |
| `apex_candidates` | 20 | immutable (triggers) |
| `apex_outcomes` | 20 | moves until final, then frozen |
| `apex_evaluations`, `apex_audits` | 20 | track records, SENTINEL checks |
| `apex_signals` | 21 | immutable |
| `apex_validations` | 21 | validation runs (RUNNING / COMPLETED / FAILED / CANCELLED) |
| `apex_trials` | 21 | append-only trial register |
| `apex_holdouts`, `apex_holdout_opens` | 21 | frozen holdout periods; one opening per strategy version |

Every migration is preceded by a `VACUUM INTO` copy in `data/backups/`. Migration 21 has a
tested downgrade (`Database.rollback_to(20)` drops only the new tables; existing data stays).

## 5. ULTRON

Deterministic agents work without any AI connection and are shown in the ULTRON HQ:
**SENTINEL** (re-check of an analysis) and **CIPHER** (track records and validation runs).
The language-model agents (AXIOM, FORGE, …) need a connected Claude plan or API key; APEX
numbers never come from a model — JARVIS's written reading quotes only the analysis' own
numbers.

## 6. UI (QuantLab → APEX)

Toolbar (symbol, timeframe 1m-4h, **Start analysis** with elapsed time, Cancel and Retry) ·
status strip **App / Data / Signal** (three separate states) · tabs: Chart & analysis (chart,
zones, structure, scenarios, **Analyst** per strategy, candidates, JARVIS), Signal journal,
Backtest, Validation (protocol run, criteria, folds, robustness, baseline, holdout, trial
register, forward log), Data fitness, SENTINEL check.
