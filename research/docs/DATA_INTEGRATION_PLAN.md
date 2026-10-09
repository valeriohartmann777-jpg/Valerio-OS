# DATA_INTEGRATION_PLAN — Databento trades for ES and NQ

Written 2026-10-09, after reading the repository and **before any Databento data was
requested**. Scope: acquire, validate and normalise real CME data. The 52 event studies,
their parameters, controls, gates, FDR and fallback logic are frozen and are not touched.

## 1. What the existing pipeline expects

| step | file | contract |
|---|---|---|
| raw vendor file | `data/raw/...` (never committed) | any CSV/parquet with OHLCV; timezone and open/close label stated in the manifest |
| manifest | `configs/datasets.yaml` | `id, instrument, path, source, tz_in, label, bar_minutes, adjustment` (+ optional mapping keys) |
| canonical bars | `data/processed/<id>.parquet` + `<id>.meta.json` (`edgelab/data/manifest.py: prepare`) | index `ts` = tz-aware bar **open** time in America/New_York; float64 `open, high, low, close, volume`; optional `n_trades`, `contract`; missing bars are never filled |
| bar-level quality | `reports/DATA_QUALITY_REPORT.md`, `reports/data_quality/<id>.json` (`scripts/data_quality.py`) | the runner checks that the JSON belongs to the prepared file |
| quality gate | journal note `DATA_QUALITY <id>` | the runner refuses to start without it |
| runner | `scripts/run_event_studies.py` → `edgelab/studies/runner.py: run_all` | reads **only OHLCV** of 1-minute bars (5-minute signal bars are built inside); a `contract` column decides the roll exclusions (protocol 13.3) |

Session logic (`edgelab/sessions.py`): CME trade date starts 18:00 ET, maintenance break
17:00–18:00 ET, RTH 09:30–16:00 ET, all on wall-clock time through the tz database (no
fixed UTC offsets). Profiles (`edgelab/features/profile.py`): 1-tick bins, 70% value
area, single-level expansion; the pre-registered studies build them from 1-minute bars
with **uniform allocation** (`configs/event_studies.yaml: value_area`).

**Frozen roll rule** (`configs/instruments.yaml`, ES and NQ): quarterly contracts
(Mar/Jun/Sep/Dec), expiry on the third Friday, roll **8 calendar days before expiry**.
This rule is used exactly; see `docs/FUTURES_ROLL_METHOD.md`.

## 2. Adaptations needed

None in the studies. New data infrastructure only:

1. `configs/databento.yaml`: dataset, schema, parent symbols, raw paths, safety limit.
2. `edgelab/data/databento_client.py`: API key from `DATABENTO_API_KEY` only, cost and
   size estimates, chunked download of raw DBN with a metadata JSON per file, cost limit.
3. `edgelab/data/contracts.py`: instrument definitions, outright-future filter, the
   frozen calendar roll as a per-trading-date active contract, volume diagnostics.
4. `edgelab/data/trades.py`: trade loading, trade-level validation, 1-minute bars of the
   active contract (exact OHLCV from trades, no empty bars).
5. `edgelab/features/vap.py`: exact volume-at-price from trades, POC/VAH/VAL with the
   existing value-area algorithm, developing POC/VAH/VAL per minute, session VWAP,
   composites of completed sessions of one contract.
6. Scripts: `databento_cost.py`, `databento_download.py`, `validate_market_data.py`,
   `build_market_data.py`, `databento_debug_charts.py`, `pipeline_check.py`.
7. Manifest field `purpose` (`research` default, `pipeline_check` for the pilot).
   `run_event_studies.py` refuses pipeline-check datasets, so a 3-month pilot can never
   freeze the DEV/VAL/TEST split, consume the pre-registered first run or add tests to
   the multiple-testing registry.

## 3. Planned Databento integration

* Request: `GLBX.MDP3`, schema `trades`, `stype_in="parent"`, `ES.FUT` and `NQ.FUT`, plus
  schema `definition` for the same symbols and period (needed to map instrument IDs to
  contracts and to drop spreads). Costs are estimated with the metadata API before every
  download; above the limit (default $20) the download aborts.
* Raw: DBN (zstd) under `data/raw/databento/<ES|NQ>/<schema>/`, one file per calendar
  month, never overwritten; a JSON beside each file records request, download time,
  library version, record count, size, SHA-256 and the cost estimate (never the key).
* Contracts: only `instrument_class = F` outright futures of the product's quarterly
  cycle. One active contract per trading date by the frozen calendar rule.
* Bars: 1-minute OHLCV from the active contract's trades, time = exchange timestamp
  `ts_event`, label = minute open, UTC in the file; empty minutes stay missing.
* The bar file is registered in `configs/datasets.yaml` (`tz_in: UTC`, `label: open`,
  `adjustment: unadjusted`, `contract` column) and then goes through the unchanged
  `prepare_data.py`, `data_quality.py`, journal gate and runner.
* Volume-at-price is computed exactly from trades in parallel. The frozen studies keep
  their pre-registered 1-minute uniform-allocation profile; the difference between the
  two is measured and reported (switching the studies to trade profiles would change a
  pre-registered definition and needs a separate, dated decision).
* Pilot (2024-01-01 → 2024-04-01) first. The 52 studies then run once over the ES pilot
  as a **blind pipeline check** in a sandbox outside the repository: structural checks
  only (studies run, outputs complete, NaNs, broken sessions, lookahead on real data);
  effect sizes, p-values and decisions are not printed, not committed and deleted,
  because Q1 2024 will most likely fall into the DEV period of the real run.
* Full history only after the user picks a period from the cost table.

## 4. Risks and how they are handled

| risk | handling |
|---|---|
| Profiles mixing two contracts | one contract per trading date; composites only over sessions of one contract (NaN across a roll) |
| Roll gap in an unadjusted series | the trading date of each contract change is excluded by the runner (protocol 13.3). Multi-session features (ATR_d, prior week, pivot zones, 20-session volume) still straddle the roll; the gap is measured in ATR_d units and reported, nothing is changed without a decision |
| Lookahead in contract choice | calendar rule known in advance (expiry from the definition, listing date checked); volume is used only for diagnostics; tests perturb the future |
| Spreads, options, odd instruments in the parent | filtered by definitions; counts by instrument class reported; trades without a definition counted |
| Exchange vs. capture time | bars use `ts_event`; ordering of `ts_recv` and `ts_event` checked and reported |
| DST and session boundaries | tz-aware conversion only; session opens checked at 18:00 ET across DST; tests for winter, summer and both transitions |
| Holidays, early closes, expiry days | reported per trading date; the runner's event-day rule (full RTH) already excludes them |
| Expired or not-yet-listed contracts | active contract must be listed and at least 8 days from expiry; trades after expiry flagged |
| Accidental large purchase | estimate before every download, $20 limit, explicit `--confirm-cost`, existing files never downloaded twice |
| API key leakage (public repository) | key only from the environment, never printed, logged or written; `.env` and key files ignored by git |
| Data licence (public repository) | raw data, processed data, per-session level tables and charts stay out of git; charts go to the project folder |
| NQ embargo (protocol 2.5) | NQ is downloaded and validated as data only; no study runs on NQ before rule freeze (the runner and the pipeline check refuse it) |

## 5. As built (2026-10-09, still before any Databento data)

The plan above was implemented with these details and changes:

* **Order: validate, then build.** `validate_market_data.py` reads every raw trade once,
  writes per-file aggregates and `reports/data_quality/trades_<product>_<start>_<end>.json`
  plus `reports/TRADE_DATA_QUALITY_REPORT.md`. `build_market_data.py` refuses to run
  unless that validation passed for the same raw files (SHA-256), then writes the bars,
  the contract schedule and the volume-at-price products and registers the dataset.
* **Record handling.** Fatal (pipeline stops): no trades, no outright, prices <= 0,
  sizes <= 0, prices off the tick grid, a trading date without an eligible active
  contract, a date on which the active contract did not trade while other outrights did,
  an outright whose expiration changed between definitions, complete sessions whose
  usual first bar is not at 18:00 New York time in a DST regime. To review (kept,
  counted, explained in the `DATA_QUALITY <id>` note): exact duplicates (identical in every field; the only
  records removed, first copy kept), trades without a definition, capture-time or
  exchange-time order breaks, negative capture latency, records outside the request
  window, trades after expiry, trades in the 17:00-18:00 break or on weekend dates.
  Spreads and other non-outright instruments are counted by class and never used.
* **Pilot dataset ids** `ES_DB_PILOT` and `NQ_DB_PILOT` (`configs/databento.yaml:
  pilot`), manifest purpose `pipeline_check`.
* **Developing levels.** The developing profile at minute t spans only the prices
  traded up to t. A first version sized the price grid by the whole session's range,
  which could change value-area tie-breaks of earlier minutes when later prices
  extended the range: a within-session lookahead. The brute-force and
  future-perturbation tests found it before any data existed; it is fixed and tested.
* **Session time.** Bars and profiles use `ts_event`; each build checks, per DST regime,
  that the first bar of complete sessions is at 18:00 New York time and reports the UTC
  equivalent (23:00 in EST, 22:00 in EDT).
