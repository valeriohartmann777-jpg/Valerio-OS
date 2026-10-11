<!-- The owner's brief for D-031, kept verbatim (uploaded 2026-10-11). -->

# JARVIS QUANTLAB — LOCAL DATA FIRST
## Autonomous Claude Code Build Directive — Backtest UX + Permanent Local Futures Data Vault

**Version:** 2026-10-10 · **Status:** Implementation specification · **Language:** English for engineering precision

> **Owner's non-negotiable request:** I do no manual programming, configuration, importing, folder creation, or integration work. Claude Code must inspect my existing JARVIS/QuantLab/ULTRON repository and implement all technically possible changes autonomously. I only intervene when entering sensitive credentials into an actual secure local UI, approving real monetary costs, or making genuinely irreversible decisions. Do not ask me to manually run shell commands or copy data between directories if your authorized local tools can do so.

## A. Goal and mandatory constraints

Rebuild the QuantLab backtesting experience around a three-control **Strategy / Symbol / Timeframe** default flow and a **Local Data First** architecture.

- Default operation is fully offline **once the selected historical coverage is already present locally**. Repeat backtests must generate **zero Databento historical timeseries requests** and **zero external data fees**.
- A Databento API key is **optional**, never needed to re-run a cached backtest.
- Support one-time approved historical imports/downloads and deterministic timeframe derivation.
- Integrate cleanly with existing idea intake (text/TikTok/video), StrategySpec, ULTRON agents, institutional-grade validation and the existing JARVIS shell. Maintain backward compatibility.
- Never turn missing data into fabricated data. Do not substitute index, ETF, CFD, cash index or another futures contract without explicit disclosure and affirmative selection.
- The source of truth is original historical records + metadata + immutable source manifest, NOT AI-provided market data.
- A genuinely successful product can run sophisticated repeated backtests entirely offline, even if all AI and Databento keys have expired. AI-based strategy extraction/interpretation may degrade gracefully and must be labelled as unavailable without an available model.

## B. User interface — aggressively simple by default

QuantLab → Backtest Lab landing area:

1. **Strategy** selector: all saved and versioned existing strategies, searchable; show label, version, status, last research verdict. Also `Create with JARVIS` link to existing Idea-to-Edge intake. A strategy must be a real, explicitly versioned StrategySpec; not a freeform prompt interpreted afresh during each run.
2. **Market / Symbol** selector: searchable futures catalogue, start with CME NQ / MNQ / ES / MES, but allow future equities, FX, crypto via correctly typed adapters. Clearly differentiate NQ vs MNQ multipliers, exchanges and contract specs. Under the hood select a concrete contract or an explicit point-in-time roll schedule for a continuous research series. Never represent Nasdaq-100 cash index as NQ futures.
3. **Timeframe** buttons: 1m, 2m, 3m, 5m, 10m, 15m, 30m, 1h. Only enable derivations supported by available source resolution and coverage. Expose strategy compatibility with timeframe, and preview signal/stop behavior changes that timeframes can cause.
4. **Backtest** primary action: launch fully local deterministic run for the exact StrategySpec version + symbol definition + timeframe + data snapshot. No network calls of any kind are allowed in local-only mode.
5. **Data readiness** contextual status: `Ready locally`, `Partial coverage`, `Wrong granularity`, `Missing contract mappings`, `Invalid data` or `Not available`. On missing data, do NOT make silent purchases. Offer `Open Data Vault` with exact missing symbols/ranges/schema. Never imply that an API key alone means data are present.

Under one collapsible **Advanced settings** section: test start/end, session template (exchange timezone, RTH vs ETH), cost model (commissions, tick, spread, slippage), fill realism/ambiguity policy, long/short, capital, sizing, leverage, risk, strategy parameters, data granularity and validation suite. Store reproducible, visible profiles (e.g. `Intraday Futures Conservative`), not hard-coded unknowable assumptions.

On results: price chart with entries/stops/exits, equity and drawdown curves, trade explorer, metrics, validation verdict, full audit provenance. A data status badge must be based on actual catalog state. Changing timeframe must NOT trigger Databento.

## C. The permanent Local Data Vault (first-class product)

Introduce QuantLab → **Data Vault** with:

- Dashboard: symbols, date coverage, raw granularity, derived timeframes, storage usage, QA status, last import, provenance, data license restrictions, backup state, and historical update needs.
- `Import files`: DBN/DBN.ZST, Parquet and CSV; guided schema mapping, timestamps, timezone, volume, OHLC, trade/quote formats, source provenance. Robust import validation. Never auto-label user-supplied CSV as exchange-authoritative.
- `Acquire once with Databento`: existing secure API connection, dataset/schema access discovery, date/symbol selection, **free cost quote** before purchase, total estimated size, duplicate-coverage deduction, optional compression estimate and **explicit user approval** before submit/download.
- `Update only missing periods`: coverage diff (set subtraction), with exact ranges and symbols to procure; never re-fetch intervals that are already complete. No automatic scheduled paid updates unless independently opted into with caps and approval policies.
- `Audit / Repair / Rebuild derived bars`: local-only audit and regenerating caches from raw immutable data.
- `Backup / Restore`: non-destructive backup manifest and optional user-approved external disk location. Warn if sole local disk risks permanent loss. Respect provider license restrictions.
- `Offline Mode`: hard API/network kill switch for market data acquisition, covering CLI/server code as well as UI. QA code must prove no API access during a complete cached test.
- `Storage optimizer`: keep compressed original source data; Parquet partitions for fast read; derived timeframes should be lazily materialized/cached and can be invalidated/rebuilt when source data or resampling version changes.

**Important:** Do not attempt to purchase/download the ENTIRE Databento catalogue. Acquisition scope must be restricted to selected symbols, periods and schemas. The owner prefers a reasonable one-time purchase rather than repeated queries or recurring subscriptions. Always show current actual quotes, not invented prices.

## D. Databento: documented, real API behavior

Use official Databento Python SDK and verify the current documentation/signatures before implementation.

- Billing: historical timeseries are metered based on **uncompressed binary data bytes transferred**, not a simplistic flat fee per API call. Repeat *streaming* requests are rebilled. Historical metadata, symbology and `metadata.get_cost` are free. Relevant docs: https://databento.com/docs/api-reference-historical and https://databento.com/docs/faqs/usage-pricing-and-data-credits .
- **Batch preferred for large archival purchases**: `batch.submit_job`, `batch.get_job_details`, `batch.list_files`, `batch.download`; a batch job incurs one data charge and permits repeat job downloads for 30 days, whereas **locally preserved files remain available to the application subject to market data license terms**. Relevant: https://databento.com/docs/examples/basics-historical/programmatic-batch-download .
- **Historical CME futures**: dataset `GLBX.MDP3` where accessible; schema `ohlcv-1m` as economical foundation for bar-based intraday research. Optional `ohlcv-1s`, `trades`, `tbbo` / quotes or more detailed schemas only for strategy classes that require them; verify coverage and permission per schema. Relevant: https://databento.com/docs/knowledge-base/datasets .
- **Exact symbol mapping**: Databento `stype_in=continuous` supports notation such as `NQ.v.0`/`NQ.c.0` when valid, but the continuity symbol is a *selector of changing underlying contracts*; prices are **not automatically back-adjusted**. Persist actual instrument IDs, instrument definitions and effective-date mappings. Relevant: https://databento.com/docs/standards-and-conventions/symbology .
- **OHLCV semantics**: `ts_event` is the START of the bar, a 1m bar is fully known only at `ts_event + 1m` (subject to actual stream availability). Bars with no trades may be absent. Relevant: https://databento.com/docs/schemas-and-data-formats/ohlcv .
- **Cost quote**: invoke `metadata.get_cost` with actual selected dataset/schema/symbols/dates/stype, confirm returned currency and that quote may differ from actual billing. Never submit paid request merely to test credentials.
- **Batch file integrity**: preserve original compressed DBN and metadata, SHA-256 hashes, account/source IDs where allowed, provider warnings, QA version, exact transfer date, and job IDs; verify each file checksum. No data re-download merely to rebuild Parquet.
- **Licensing**: verify current Databento + exchange conditions for the actual personal/internal/commercial, storage, non-display usage, and onward distribution of historical data. Do not promise unconditional, perpetual redistribution rights. Treat user archive as private and local by default. Fail closed if permission is unclear.

## E. Data architecture

Example internal layout (adapt to actual repository layout):

```
quantlab_data/
  raw/databento/GLBX.MDP3/ohlcv-1m/<job-id>/... .dbn.zst
  raw/imported/<provider-or-user>/<dataset-id>/...
  normalized/futures/<venue>/<instrument>/<schema>/year=YYYY/month=MM/*.parquet
  derived/<instrument-or-series>/<timeframe>/<source-version>/... .parquet
  reference/instrument_definitions/...
  manifests/<dataset-id>.json
  backups/...
```

A relational catalogue using SQLite initially (no hardcoded local path assumptions):

- `data_sources(id, provider, license_scope, account_owner, ...)`
- `dataset_snapshots(id, provider, dataset_code, schema, acquired_at, raw_hash, transform_version, status, ...)`
- `instruments(id, venue, raw_symbol, root, type, tick_size, tick_value, multiplier, currency, expiry, effective_from, effective_to, ...)`
- `instrument_mappings(id, stype, continuous_symbol, actual_instrument_id, valid_from, valid_to, rule, ...)`
- `coverage_segments(snapshot_id, actual_instrument_id, schema, start_utc, end_utc_exclusive, quality_grade, ...)`
- `data_files(snapshot_id, role, path, checksum, bytes, immutable, ...)`
- `derived_datasets(source_snapshot_id, timeframe, session_profile, aggregator_version, output_hash, ...)`
- `acquisition_quotes(id, exact_request_hash, currency, amount, quoted_at, expiry, status, ...)`
- `acquisition_approvals(id, exact_request_hash, user_approval_at, max_approved_amount, ...)`
- `imports(...)`, `quality_issues(...)`, `backtest_runs(...)` linked to snapshots.

Use UTC internally; exchange-local session calendars and daylight saving applied explicitly. Explicit coverage semantics: `[start_utc, end_utc_exclusive)` with separate contract roll gaps and data-quality flags. A single availability range is not proof of complete bars; validate holes against sessions, instrument lifecycle and schema's no-trade semantics.

## F. Timeframe and futures correctness

- The source-of-truth candle is **1 minute** for bar-based strategies. Derive 2/3/5/10/15/30/60-minute bars using deterministic resampling with session-aligned anchors. Never accidentally offset New York RTH bars to UTC midnight or straddle maintenance breaks or different actual contracts.
- Aggregation: `open = first actual open`, `high = max high`, `low = min low`, `close = last actual close`, `volume = sum volume`. Empty/incomplete groups must be flagged, not silently filled with synthetic traded volume/prices.
- Do not mix expired-contract bars across roll boundaries inside one bar; use actual instrument ID and effective-date mapping, and explicit roll policies. A stitched research series and executable tradable ledger are different products.
- Strategy results on 1m vs 5m can differ legitimately: candle completion, thresholds, fills and sampling change. Keep each test's timeframe part of StrategySpec run identity.
- Completed-bar information is available no earlier than the bar close; a close-based signal cannot fill at the same bar's prior opening. Define timestamp as bar start and `available_at` accordingly.
- 1m OHLCV does **not** prove whether intrabar stop or target came first; preserve uncertainty, use configurable conservative bounds, or use higher-resolution bought/imported data where appropriate.
- Spread and slippage cannot be measured from OHLCV alone; use explicit estimation assumptions, flag execution realism, and if relevant request quotes/ticks after approval.
- Do not use back-adjusted continuous prices to compute actual account P&L without correct mapping to tradable contract fills and explicit roll trades/costs.
- Point-in-time instrument specs must govern tick size, tick value, contract multiplier, currency, expirations, session and holidays. NQ and MNQ cannot be conflated.

## G. Backtest execution: 100% local after data provision

Pipeline:

```
User selects StrategySpec/version + Symbol + Timeframe
  -> DatasetResolver queries local CoverageIndex FIRST
  -> Validate source granularity, instrument mappings, QC, license and coverage
  -> Generate/retrieve derived bars locally if needed
  -> Deterministic simulation (orders/fills/ledger/costs)
  -> Local validation suite (OOS, walk-forward, robustness, regime, etc.)
  -> Persist complete experiment (hashes, assumptions, metrics, provenance)
  -> Render results + high-level JARVIS commentary if AI available
```

NEVER call Databento from the `run_backtest` service. Acquisition is a separate explicit operation with a different authorization gate. Architecturally enforce this via network-isolated simulation worker and separate market-data downloader process.

The backtest must work when the network is physically unavailable. This includes reusing cached strategy specs and local dataset snapshots. The AI assistant can be optional but cannot be a runtime dependency for arithmetic or validation.

## H. Existing JARVIS / ULTRON / Idea-to-Edge integration

Inspect existing agents and modules; reuse them rather than duplicating. Preserve saved strategies from text/TikTok/video analysis. Formalized StrategySpec goes directly into Strategy selector.

ULTRON collaboration (real jobs not cosmetic avatars):

- **JARVIS**: orchestrates, explains data coverage and outcomes; never changes rules during final evaluation.
- **CIPHER**: statistical research, strategy assessment, robust experiment design.
- **SENTINEL**: independent QA, look-ahead/data leakage/intrabar ambiguity/selection bias review.
- **ATLAS**: third-party source and instrument context, licensing research, NOT authoritative historical price generation.
- **FORGE**: controlled implementation of strategy logic and reproducible tests in sandbox, not arbitrary host code execution.
- **ARCHIVE**: experiment registry, failed hypotheses and attempted variants preserved to prevent cherry-picking.
- **VECTOR**: local data indexing/storage/backups, storage-health checks.

The team may propose improvements and compare them on research/development data. Preserve a truly untouched holdout set and track all trials. Every variant is a separate version; never claim that optimizing on history confirms future profitability.

## I. Acquisition policy and alternative providers

Default procurement priority:
1. Reuse local snapshots (zero recurring provider calls, zero new provider costs).
2. Import legally obtained user files (DBN, CSV, Parquet), with full provenance and QA.
3. Purchase an appropriately scoped one-time Databento **batch** archive only after actual cost quote and user approval.
4. Connect other legitimate providers later through adapters if they demonstrably satisfy the required coverage/quality/license at lower *total* cost.

Support an `Alternative Provider Evaluation` in the Data Vault comparing explicit coverage, venue, contract identity, granularity, history, cost structure, license, source reliability and APIs. Do not use CFD/spot/index/ETF series in place of exact CME futures or free inaccurate data for institutional-grade claims. Do not falsely promise that complete historical CME tick datasets are free.

The most economically sensible initial archive is *user-selected* instruments (e.g. NQ and optional MNQ/ES/MES) with several years of `ohlcv-1m`, if covered/affordable, plus required definitions and mappings. Make the exact range and quote configurable rather than inventing a price, coverage or account entitlement. Later quote a narrow tick/quote supplement if a strategy depends on intrabar details.

## J. High-level API/service contracts

Adapt to existing service conventions, e.g.:

- `GET /quantlab/strategies`, `GET /quantlab/instruments`
- `GET /quantlab/data/coverage?symbol=NQ&schema=ohlcv-1m&start=...&end=...`
- `GET /quantlab/data/vault`
- `POST /quantlab/data/imports` (local files)
- `POST /quantlab/data/quotes` (FREE quote; needs valid key but no paid data)
- `POST /quantlab/data/acquisitions/prepare` (exact request hash and estimated amount)
- `POST /quantlab/data/acquisitions/approve` (user UI affirmative confirmation of exact quote/scope)
- `POST /quantlab/data/acquisitions/execute` (only approved immutable request)
- `GET /quantlab/data/acquisitions/:id` (real progress)
- `POST /quantlab/data/rebuild-derived` (local)
- `POST /quantlab/backtests` (MUST NOT perform paid downloads or provider calls)
- `GET /quantlab/backtests/:id`
- `GET /quantlab/backtests/:id/trades`
- `GET /quantlab/backtests/:id/validation`

Deduplicate acquisition requests by normalized hash; secure API key in OS credential vault, never in frontend localStorage, repo, LLM prompts or logs. Use safe in-progress state and retry resume without accidentally submitting a second paid batch. Default no auto-buy.

## K. Required automated acceptance tests (NO exceptions)

1. Run a pre-imported NQ one-minute strategy with outbound network blocked; no Databento methods invoked.
2. Run 1m then 5m then 15m on same snapshot; zero additional acquisition, correct derived OHLCV bars and provenance.
3. Derive 2m, 3m, 10m and 30m and verify timestamps/sessions by independent fixtures.
4. Correct special handling of session transitions, DST, early closes, missing bars, no-trade intervals.
5. No bar may mix real instrument IDs spanning continuous-series rollover.
6. If NQ is missing and ES exists, block instead of substituting ES.
7. If only 5m source exists, block a 1m test (do not invent subdivisions).
8. Databento quote uses real metadata/free cost endpoint; no cost side effect. Paid acquisition requires exact matching unexpired user approval, max approved amount and API authentication.
9. Duplicate import / duplicate batch work cannot incur repeated charges by silent resubmission. Check resumable jobs.
10. Interrupt batch download mid-file: resume without corrupting validated raw archive; verify hashes and partial files.
11. Rebuild derived bars from immutable originals offline even after deletion of derived cache.
12. Bar timestamps represent start; close-based signal cannot fill before information becomes available.
13. Check broker-grade ledger: commissions, tick size, point value and multiplier for NQ vs MNQ.
14. Stop and target touched in one OHLCV bar: result conservative/ambiguous, never magically optimal.
15. Snapshot, strategy, execution assumptions and engine version hashes yield reproducible runs.
16. UI shows true actual coverage and filters strategies by compatible symbol/timeframe.
17. Re-run the same historical data 100 times: zero new provider data charges.
18. Local data loss/quality corruption is detected and reported; do not claim offline-ready.
19. Secrets not exposed in browser state, DB exports, logs or request tracing.
20. End-to-end real UI: select saved strategy → NQ → 5m → Backtest → chart + ledger + validation with one cached dataset, no network.

Create extra tests wherever necessary. Verify against independently calculated toy fixtures; do not silently manufacture market data and present them as real.

## L. Implementation order and definition of done

### Phase L0 — Repository inspection
Read project instructions and inspect JARVIS, QuantLab, Databento, UI, backend, agents, tests and storage. Create migrations and compatibility plan; never delete working functionality.

### Phase L1 — Working local-only vertical slice
Build 3-field selector, local file import (CSV/DBN/Parquet), catalogue+coverage+hashing, 1m→5m/15m aggregation, deterministic local backtest, real report. **This works without a Databento key.**

### Phase L2 — Databento batch acquisition, once
Secure credentials, metadata catalogue, get_cost quote, explicit approval UI, batch submit/download/verify/store, missing-range update, no-duplicate purchasing.

### Phase L3 — Professional quality
Correct futures contracts/rolls/calendars, gaps, cost/ledger, timestamp semantics, more timeframes, storage+backup+rebuild, performance.

### Phase L4 — QuantLab + ULTRON intelligence
Full validation suite, strategy comparison, experiment memory, AI critique, bounded autonomous research and future optional tick/quote ingestion.

Every phase must run, be exercised end-to-end, pass tests, and leave the repository runnable. The owner will not do manual implementation. No pretend demos, no fake data presented as market truth, no fake API connectivity, no fabricated costs. Track status and remaining limitations.

**Start immediately with repository inspection, then implement L1 as a working vertical slice before any UI polish unrelated to functionality. Continue autonomously through phases subject to spending/credential approvals.**
