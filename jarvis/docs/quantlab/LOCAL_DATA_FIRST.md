# QuantLab — Local Data First (D-031)

Brief: `LOCAL_DATA_FIRST_DIRECTIVE.md` (verbatim). Evidence: `LOCAL_DATA_FIRST_TEST_EVIDENCE.md`.

A normal backtest takes three choices — **strategy, market, timeframe** — and runs on data
already on the Mac. Buying data is a separate, deliberate act in the **Data Vault**: a free
quote, your explicit approval with a maximum, one Databento batch job, verified files kept
forever. After that, any number of backtests on any timeframe cost nothing and need no
network.

## 1. What the owner sees

**QuantLab → Backtest Lab** (the default section)

| Control | Behaviour |
|---|---|
| Strategy | Searchable list of saved strategy versions (Studio, Idea-to-Edge, templates) with version, status and last verdict. Drafts with open questions are listed but can't run. |
| Market | NQ · MNQ · ES · MES. NQ and MNQ are different contracts with different point values; one is never used for the other. |
| Timeframe | 1m · 2m · 3m · 5m · 10m · 15m · 30m · 1h. A button is enabled only if the strategy can run there and the local data allows it (`data-ready`). |
| Readiness | Ready locally · Partial coverage · Wrong granularity · Missing contract mappings · Invalid data · Not available · Not compatible with this strategy — with what is missing and **Open Data Vault**. |
| Advanced (collapsed) | Period, cost profile (`intraday_conservative` / `intraday_stress`, shown as assumptions), run kind (backtest / full validation), contracts, capital, direction, slippage, commission, exchange fees, limit fills. |
| Backtest | Builds or reuses the local snapshot, starts the run, shows the existing results: chart, equity/drawdown, trade explorer, validation, verdict, and a "How this run was computed" card (product · timeframe, source series, provider, derivation, hashes). |

Changing the timeframe or market only re-reads the local catalog. It never contacts Databento.

**QuantLab → Data Vault**

- Series table: symbol, source (Databento download, Databento file, your file, fixture),
  coverage with the number of gaps, sessions with bars / expected sessions, source bar size →
  derived timeframes, quality grade (A/B/C/F, plus "unmapped" / "damaged").
- Storage panel: raw originals, day files, snapshots, derived bars, free disk, and a warning
  while no verified backup exists.
- **Import files:** DBN / DBN.ZST (Databento), Parquet, CSV. Preview, column mapping,
  timezone and contract declaration, strict checks, commit. Your CSV/Parquet is labelled
  *user-supplied* and its runs carry the fitness warning `UNVERIFIED_SOURCE`; it is never
  presented as exchange data.
- **Acquire once with Databento:** symbol, period → **Get free quote** (only the missing
  days are priced) → your maximum + tick box → **Approve** → **Start download**. Progress,
  resume, and the result appear in the list.
- **Offline mode:** one switch that blocks every Databento request (quotes, downloads,
  verification). Backtests keep working.
- **Storage & safety:** audit (re-hash everything), repair, rebuild derived timeframes,
  backup to a folder, restore, warning when no backup exists.
- **Providers:** the comparison table (Databento integrated, your files integrated, other
  vendors not evaluated — no adapter, no claims).

## 2. Data model

```
<data>/quantlab/hub/
  raw/<provider>/…                 immutable originals (DBN.ZST from Databento, your imports)
  canonical/<series>/<day>.parquet one UTC day of 1-minute bars per file (existing qh_cache)
  datasets/<id>/…                  frozen snapshots with manifest + checksum (existing qh_datasets)
  derived/<dataset>/<tf>/<profile16>/ql-resample-1/bars.parquet
  staging-imports/<id>/            uploads before commit (cleared at start)
```

Tables (migration 16): `qv_imports`, `qv_derived` (unique dataset · minutes · profile ·
aggregator), `qv_settings` (offline switch), `qv_backups`, `qv_issues`, `qv_acquisitions`,
`qv_approvals` (append-only, enforced by triggers). Existing `qh_*` tables are reused.

- **1-minute bars are the source of truth.** Everything else is derived and can be thrown
  away and rebuilt bit-identically from the originals.
- **Provenance per series:** provider `databento` (batch download), `databento-file` (DBN you
  imported), `import` (CSV/Parquet). Files without a contract column get a declared contract
  and a synthetic instrument id (`2**40 + crc32`) that can't collide with exchange ids.

## 3. Timeframes (`futures/resample.py`, `futures/timeframes.py`)

- Aggregator `ql-resample-1`: buckets are anchored at the **session window start in exchange
  local time** (e.g. 09:30 ET → 09:30, 09:35, …); open = first, high = max, low = min,
  close = last, volume = sum.
- Bar timestamp = **bar start** (Databento `ts_event` convention). A bar is known at
  `start + duration`; the engine fills close-based signals only after that.
- Missing minutes are **never filled**. A bucket with missing minutes keeps the minutes it
  has; a partial last bucket of a session is flagged.
- A bucket never mixes instrument ids. At a roll the bucket ends early.
- Source coarser than requested → `WRONG_GRANULARITY` (a 5m source never yields 1m).
  Source not aligned to the grid → `MISALIGNED_SOURCE`.
- **Strategy compatibility** (blocking): the opening range must be a whole number of bars;
  MA bar sizes must be compatible; the session window must hold at least one bar.
  Notes (non-blocking): time exits happen at the bar that contains the flatten time,
  entry cut-offs are evaluated at bar close, counts are in bars, grid values that don't fit
  are dropped, and intrabar stop/target ambiguity grows with the bar size.
- The engine (`ENGINE_VERSION 1.1.0`) takes `bar_minutes`; 1-minute results are identical
  to 1.0.x. Stop and target touched in the same bar → stop first (conservative), with the
  optimistic case reported alongside.
- SENTINEL's independent second engine is 1-minute only; for coarser runs that audit check
  reports *not run* (WARN) instead of a false pass.

Run identity: the manifest of a run on a timeframe > 1 or with a market binding adds
`timeframe`, `binding`, `effective_spec` and `effective_spec_sha256`. Same snapshot + same
effective spec + same engine version → the same run (reused, not recomputed).

## 4. The backtest pipeline (`vault/lab.py`)

```
readiness(strategy, market, timeframe, period)        local catalog only
  → hub.build_dataset(provider=…, extra=provenance)     local canonical days → snapshot
  → research.start_run(timeframe, binding)              compatibility + effective spec
      → research.derive = vault.derived_bars            cache hit / verify / rebuild locally
      → engine · audit · validation · verdict           unchanged
```

`run_backtest` (`BacktestLab.launch`) has no code path to the provider. The tests patch the
socket layer and `databento.Historical` to raise, and count `adapter.calls`; both stay at
zero across 100 reruns.

## 5. One-time acquisition (`vault/acquire.py`)

1. **prepare** — diff the requested period against local coverage (sessions on the XNYS
   calendar), price only the missing days with `metadata.get_cost` (free), store the quote
   with a request hash and a 15-minute TTL. Same request → same open quote (no duplicates).
   Nothing missing → `NOTHING_TO_BUY`.
2. **approve** — requires `confirm: true`, the exact request hash, a maximum ≥ the quote,
   and the Data Hub caps (per request, per month). Approvals are append-only and expire
   after 30 minutes.
3. **execute** — re-prices (price above your maximum → `PRICE_CHANGED`, nothing submitted),
   then a compare-and-set `APPROVED → SUBMITTING` so two clicks can't submit twice. The
   SUBMITTING state is written **before** `batch.submit_job`.
   - If the submit answer is lost, JARVIS looks the job up with `batch.list_jobs`; if it
     still can't tell, the acquisition goes to **ATTENTION** and asks you. It never
     resubmits blindly.
4. **download** — `batch.list_files` → `batch.download` with resume, SHA-256 check of every
   file, then the file moves into the immutable raw archive and is ingested into the
   canonical 1-minute cache. After a restart, work in SUBMITTING / SUBMITTED / DOWNLOADING /
   INGESTING continues from its saved state (SUBMITTING is looked up, never resubmitted);
   an approval that was never executed stays waiting for your **Start download**.
5. **COMPLETED** — a new quote for the same period reports `NOTHING_TO_BUY`.

Request shape: `GLBX.MDP3`, `ohlcv-1m`, `stype_in` continuous / parent / raw_symbol /
instrument_id (e.g. `NQ.v.0` —
volume-ranked continuous, **not back-adjusted**), `split_duration="month"`,
`compression="zstd"`, `delivery="download"`.

States: `QUOTED → APPROVED → SUBMITTING → SUBMITTED → DOWNLOADING → INGESTING → COMPLETED`,
plus `EXPIRED`, `CANCELED`, `FAILED`, `ATTENTION`.

## 6. Offline isolation — what it is and isn't

- Enforced by **code structure** (the backtest path never holds the adapter), a **guard** in
  the single network gateway `DatabentoAdapter._call` (offline switch → `OFFLINE_MODE`), a
  call counter exposed as `provider_requests` in the vault overview, and tests that block the
  socket layer.
- It is **not** an OS-level sandbox: the process could open sockets elsewhere (e.g. the AI
  router). The claim is narrower and tested: no market-data request and no Databento fee
  from backtests, ever, and none at all while offline mode is on.

## 7. Integrity, backup, restore

- **Audit** re-hashes raw files, canonical days, snapshots, derived bars and imports; finds
  are listed as issues and the affected series is marked not runnable. Nothing is reported
  as "ready" that fails its checksum.
- **Repair** re-derives a damaged canonical day from its raw DBN original; derived bars are
  rebuilt from the snapshot. User CSV without a stored original can only be re-imported.
- **Backup** copies raw, canonical, datasets and the catalog with a checksum manifest
  (refuses a target inside the vault). **Restore** needs a confirmation, checks every backup
  file against the manifest (damaged copies are skipped and listed), replaces only local
  files that are missing or fail their checksum, and adds catalog rows without touching
  existing ones (`INSERT OR IGNORE`).

## 8. ULTRON roles (who does what here)

The brief names seven roles. For the Data Vault they are **code paths, not new chat agents**
— no model call is involved in buying, storing, resampling or running.

| Role | Where it happens |
|---|---|
| JARVIS | Backtest Lab readiness text, provenance card, verdict wording (existing) |
| CIPHER | Validation suite and evolution proposals (existing Idea-to-Edge missions) |
| SENTINEL | Run audit: look-ahead, `LAST_BAR_CLOSE` timing per bar size, second engine (1m), intrabar ambiguity |
| ATLAS | Provider comparison table; no price data comes from ATLAS |
| FORGE | Strategy logic stays in the DSL + engine; no arbitrary code runs |
| ARCHIVE | Research registry keyed by manifest hash; append-only trial ledger and approvals |
| VECTOR | `VaultService`: catalog, coverage, audit, backup, derived cache, storage usage |

## 9. API

```
GET  /quantlab/data/vault                  overview (series, usage, offline, provider_requests)
GET  /quantlab/data/coverage               coverage per series (sessions, holes)
POST /quantlab/data/imports                raw body + x-filename → staged import + preview
POST /quantlab/data/imports/path           stage a local file by path
POST /quantlab/data/imports/{id}/commit    mapping + declared contract → series
GET  /quantlab/data/imports
POST /quantlab/data/rebuild-derived        rebuild derived bars (offline)
POST /quantlab/data/vault/audit            {repair: bool}
POST /quantlab/data/vault/offline          {offline: bool}
POST /quantlab/data/vault/backups          {folder}
POST /quantlab/data/vault/restore          {folder}
POST /quantlab/data/quotes                 = acquisitions/prepare
POST /quantlab/data/acquisitions/prepare|approve|execute
POST /quantlab/data/acquisitions/{id}/resume|abandon
GET  /quantlab/data/acquisitions[/{id}]
GET  /quantlab/instruments · /quantlab/strategies · /quantlab/backtest/options · /quantlab/backtest/readiness
POST /quantlab/backtests
GET  /quantlab/backtests/{id} · /trades · /validation
```

Events: `quantlab.vault`, `quantlab.backtest`.

## 10. Limits (honest)

- Verified here only against the **labelled fixture** provider (synthetic prices, real zstd
  DBN files built by the official `databento` encoder). No real batch job, no real DBN from
  Databento, no real Keychain, not on macOS.
- Coarser timeframes are approximations of what the strategy would see on a chart. Fills
  inside a bar remain ambiguous; QuantLab resolves them against you.
- `NQ.v.0` is not back-adjusted; price levels jump at rolls. Runs never span a roll inside a
  bar, and contract changes are shown.
- Other providers have no adapter; the table says so instead of guessing their prices.
