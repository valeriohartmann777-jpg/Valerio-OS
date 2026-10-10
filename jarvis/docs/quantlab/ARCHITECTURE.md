# QuantLab architecture (Institutional Edition)

QuantLab is a part of JARVIS: the same shell, event bus, SQLite, permissions model and
brain. It is not a second app. The R1 cash-equity lab (`backend/jarvis/quantlab/*.py`)
is unchanged. The new parts are two packages.

```
apps/desktop  pages/QuantLab.tsx (shell, modes, nav, Stop all)
              components/quantlab/hub/DataHub.tsx
              components/quantlab/research/{StrategyStudio,Results,Explorer,Overview,charts,common}.tsx
              components/quantlab/EquityLab.tsx (R1, unchanged behaviour)
                  │ HTTP /quantlab/*  ·  WS events quantlab.hub.* / quantlab.research.run
backend/jarvis/api/quantlab_hub_routes.py · quantlab_research_routes.py
backend/jarvis/quantlab/hub/        Data Hub
   vault.py      OS keystore via keyring; no plaintext fallback; memory backend for tests only
   provider.py   Databento adapter (SDK 0.87.0, verified signatures), error classes, redaction
   fixture.py    offline stand-in: same call surface, real DBN files, synthetic prices (labelled)
   cache.py      raw DBN (as delivered) + canonical per-UTC-day Parquet (integer prices)
   quality.py    bar QA against CMES/XNYS calendars, rolls, conditions, capability matrix
   store.py      qh_* tables (migration 11)
   service.py    connect/test/disconnect, catalog, resolve, quote, approve, jobs, datasets
backend/jarvis/quantlab/futures/    research
   sessions.py   exchange_calendars sessions (holidays, early closes, DST) → strategy windows
   contracts.py  contract master from definition records; reference table only as ASSUMED
   spec.py       FuturesSpec 1.0 (strict Pydantic), plain-language description, templates
   engine.py     event-driven bar engine: integer ticks, Decimal money, orders/fills/trades
   audit.py      independent ledger checks re-derived from bars and ticks
   metrics.py    metrics from the ledger; undefined → None + reason
   validation.py split, tests, statistics (bootstrap, deflated Sharpe), fitness, verdict
   architect.py  NL → draft spec via one tool call, validated, never saved automatically
   store.py      qr_* tables (migration 12): strategies, versions, runs, trials, holdouts, notes
   service.py    runs as persistent jobs, artifacts, reports, trade detail, comparison
```

## Flow

```
key ─▶ vault check ─▶ metadata.list_datasets (verify) ─▶ keystore ─▶ qh_connections (hint only)
request ─▶ cache subtracts held days ─▶ get_cost/billable_size/record_count per missing range
        ─▶ quote (signature, 15 min TTL) ─▶ user approves max budget (UI, confirm:true)
        ─▶ caps + re-price + compare-and-set ─▶ job: chunks (≤31 days) ─▶ timeseries.get_range(path)
        ─▶ decode (DBNStore) ─▶ raw file frozen ─▶ per-day canonical Parquet ─▶ qh_cache
cache ─▶ dataset build: checksums ─▶ snapshot ─▶ manifest ─▶ quality report (immutable)
spec (user / template / JARVIS draft) ─▶ version (hash, insert-only)
run(version, dataset) ─▶ manifest hash = run id ─▶ queue ─▶ worker thread:
   load + verify snapshot ─▶ contracts ─▶ windows ─▶ split ─▶ simulate conservative + optimistic
   ─▶ audit ─▶ fitness ─▶ [validation suite + trial registry + holdout once] ─▶ verdict
   ─▶ artifacts (Parquet ledgers, summary, report.md) ─▶ results hash ─▶ COMPLETED
```

## Time semantics

- `ts_event` of an OHLCV bar is the bar's **start**. A bar's values are known at
  `ts_event + 1 min`.
- Signals on completed bars fill at the next eligible bar's open (plus latency bars).
- Opening-range levels are known at range end. Stop entries can trigger from the first bar
  starting at or after that time.
- Every fill records `known_at` (when its deciding information existed) and the bar it
  filled in. The audit and the look-ahead perturbation test both check this.
- Everything is UTC nanoseconds internally. Session rules are New York wall-clock times,
  converted per date (DST-correct).

## Concurrency and recovery

- One download worker and one research worker (asyncio tasks).
- CPU work runs in threads; cancellation is a `threading.Event` the engine checks.
- Status transitions are compare-and-set (QUEUED→RUNNING, QUEUED→CANCELED,
  RUNNING→terminal). A cancel can't race a worker start, and a stale worker can't overwrite
  a re-queued run.
- On restart, QUEUED/RUNNING jobs and runs become INTERRUPTED. Downloads restart through a
  new quote (cached days are free). Runs restart with the same manifest and give identical
  results.

## Why not …

- **DuckDB:** Parquet plus pyarrow covers per-dataset analysis today. DuckDB arrives with
  cross-dataset queries (D-028).
- **VectorBT:** the event engine is the ground truth for fills. A vectorized helper may come
  later for sweeps but never overrides fills.
- **A second app:** the shell, events, permissions and brain are already there.
