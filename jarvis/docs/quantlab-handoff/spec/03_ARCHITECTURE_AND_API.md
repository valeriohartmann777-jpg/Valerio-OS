# 03 — Systemarchitektur, Verträge, Integrationsplan

## Regel 0
**Zuerst das existierende JARVIS-Repository inspizieren.** Der bisher vorgeschlagene Electron/React/TS + FastAPI Stack ist nicht verifiziert. Wiederverwende real vorhandene UI-/Backend-Muster, Auth, routing, events, theming. Falls Jarvis ausschließlich eine Web-App ist: QuantLab als Modul darin, keine unnötige zweite Desktop-App.

## Trennbare Domänen
```
JARVIS navigation / chat / event bus
       │
 QuantLab UI (Strategy | Data | Run | Evidence)
       │ typed API
 QuantLab application services
       ├─ StrategyRegistry   immutable version + hash
       ├─ DataIntelligence   import + quality + snapshot
       ├─ ExperimentService  run orchestrator + artifacts
       ├─ ReferenceEngine    deterministic causal simulation
       ├─ ValidationService chronological OOS + warnings
       ├─ QuantResearchAI    optional NL spec adapter
       └─ ReportService      metrics + evidence decisions
       │
 SQLite metadata + Parquet snapshots + checksum artifact store
```

## Technologie-Entscheid
- Backend: Python 3.12+, FastAPI, Pydantic; SQLite migrations via existing project approach or Alembic when justified.
- Data: pandas/polars as appropriate, pyarrow for Parquet; DuckDB for ad-hoc querying, not as default truth database.
- Engine V1: kleiner **deterministischer Referenzsimulator** mit explizitem trade ledger/accounting + golden tests. VectorBT als späterer schneller Adapter für Parameter-Sweeps, niemals ungeprüft als einziges Wahrheitssystem.
- V1 keine beliebige KI-Python-Strategie ausführen. Rule-template/DSL mit JSON validation.
- UI: bestehender JARVIS React-/TypeScript-Stack bevorzugt, wenn vorhanden.
- Alle großen Läufe als Background-Job **im lokalen Backend** mit Status-Events, Cancellation und Atomic Artifact Commit; Browser-Request darf nicht blockieren.

## Minimal-Datenmodell
```
Strategy(id,name,hypothesis,created_at)
StrategyVersion(id,strategy_id,spec_json,spec_sha256,created_at)
DatasetSnapshot(id,manifest_json,sha256,immutable_path,created_at)
Experiment(id,strategy_version_id,dataset_snapshot_id,run_manifest_sha256,status,created_at)
RunArtifact(id,experiment_id,artifact_type,relative_path,sha256)
ValidationResult(id,experiment_id,test_name,outcome,method,notes,metrics_json)
```
Alle ID-Referenzen referentielle Integrität, sensible Dateipfade serverseitig gebunden (keine `../` Traversal). Alte Experimente unveränderlich; neue Parameter -> neue StrategyVersion/Experiment.

## API Vorschlag (bestehende Konventionen übernehmen)
```
GET  /api/quantlab/health
GET  /api/quantlab/strategies
POST /api/quantlab/strategies
POST /api/quantlab/strategies/{id}/versions
POST /api/quantlab/datasets/import
GET  /api/quantlab/datasets/{id}
POST /api/quantlab/experiments
GET  /api/quantlab/experiments/{id}
GET  /api/quantlab/experiments/{id}/trades
GET  /api/quantlab/experiments/{id}/artifacts
POST /api/quantlab/experiments/{id}/cancel
```
Possible response envelopes:
```
{ "id": "...", "status": "running", "created_at": "...", "links": { ... } }
{ "error": { "code": "DATA_INVALID", "message": "...", "details": {...} } }
```

## Events
`quantlab.strategy.created`, `quantlab.dataset.imported`, `quantlab.dataset.rejected`, `quantlab.experiment.created`, `quantlab.experiment.running`, `quantlab.experiment.completed`, `quantlab.experiment.failed`, `quantlab.experiment.cancelled`, `quantlab.validation.completed`. Event fields: `event_id`, `trace_id`, `timestamp` UTC, `entity_id`, `type`, `safe_payload`. Frontend can resync through GET after reconnect.

## Reproducibility manifest
- Experiment ID, strategy/version SHA, immutable dataset SHA, code commit and Engine-Version, config incl. timezone, fill/fee/slippage, split cutoffs, seed (if relevant), Python/runtime versions.
- Both source files and normalized snapshot hashes where applicable; stable JSON canonicalization (sorted keys, normalized UTC ISO8601).
- Artifacts: `trades.parquet`, `equity.parquet`, `metrics.json`, `data_qa.json`, `manifest.json`, `audit.jsonl`.
- All writes are transactional/staged; failed runs do not appear complete.

## Delivery constraints
No microservices / Redis / Docker / Kubernetes requirement in R1 if existing repo doesn't need them. One API and one local worker are enough. No speculative multi-agent scaffolding before the baseline runs work.
