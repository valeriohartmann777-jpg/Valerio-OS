# QuantLab data contracts

## Databento usage (SDK `databento==0.87.0`, signatures checked in the installed package)

| Purpose | Call | Billable |
|---|---|---|
| Verify a key | `metadata.list_datasets()` | no |
| Catalog | `metadata.list_schemas(dataset)`, `metadata.get_dataset_range(dataset)`, `metadata.get_dataset_condition(dataset, start_date, end_date)` | no |
| Contracts | `symbology.resolve(dataset, symbols, stype_in, "instrument_id", start_date, end_date)` | no |
| Quote | `metadata.get_cost / get_billable_size / get_record_count(dataset, start, end, symbols, schema, stype_in)` | no |
| Download | `timeseries.get_range(..., stype_out="instrument_id", path=…)` | **yes** — only from an approved quote |

- `symbols` must always be an explicit list. The SDK treats an empty list or `ALL_SYMBOLS`
  as every symbol, and the adapter refuses both.
- Requests: one symbol × at most 31 UTC days each. Only whole UTC days before the dataset's
  end are bought.
- Errors are mapped to `AUTH_INVALID`, `NO_ENTITLEMENT`, `UNSUPPORTED_SCHEMA`,
  `RANGE_UNAVAILABLE`, `SYMBOL_NOT_FOUND`, `INVALID_REQUEST`, `RATE_LIMITED`,
  `QUOTA_EXCEEDED`, `PROVIDER_ERROR` and `NETWORK`, each with a remedy.
- Retries: 2 for rate limit, server and network errors (2 s and 8 s backoff), each
  recorded in the audit.

## Cache

- `cache_key = provider|dataset|schema|stype_in|symbol`; one row per UTC day in `qh_cache`.
- **Raw:** `raw/<provider>/<dataset>/<schema>/<stype>/<symbol>/<start>_<end>__<job>-<n>.dbn`
  - exactly as delivered, read-only (0444), SHA-256 recorded.
- **Canonical:** `canonical/…/<YYYY-MM-DD>.parquet` (zstd), read-only, SHA-256 recorded.
  - Bars (by `ts_event` day): `ts_event`, `instrument_id`, `open`, `high`, `low`, `close`,
    `volume` — all int64, prices in fixed precision 1e-9, unadjusted.
  - Definitions (by `ts_recv` day): `ts_recv`, `ts_event`, `instrument_id`, `raw_symbol`,
    `security_type`, `instrument_class`, `asset`, `exchange`, `currency`,
    `min_price_increment`, `display_factor`, `unit_of_measure`, `unit_of_measure_qty`,
    `min_price_increment_amount`, `expiration`, `activation`.
- Days with zero records are cached too, so coverage is known.
- Provider condition and the mapped instrument ids are stored per day.
- Transform version: `canon-1`.

## Dataset manifest (`qh-dataset-1`)

Fields: `id` (`dh-` + SHA-256 of the manifest core), `provider`, `fixture`, `dataset`,
`schema`, `stype_in`, `symbol`, `window` (UTC, end exclusive), `time_convention`, `prices`,
`records`, `instruments` (id, raw symbol, bars, first/last ts, definition values), `mapping`
(instrument ids by date range), `conditions` (non-available days), `files.raw` and
`files.canonical` (paths and SHA-256), `definitions`, `transform_version`, `calendar`
(library, version), `sdk_version`, `acquisition` (job, quote, estimate, approval time),
`license`, `snapshot` (path, SHA-256), `definitions_snapshot`, `quality_status`, `created_at`.

- Snapshots are immutable: a SQLite trigger blocks updates.
- The checksum is verified before every run; a mismatch refuses the run.

## FuturesSpec 1.0

Strict JSON (unknown fields refused), canonical JSON, SHA-256 identity, insert-only versions
(`qr_versions`, trigger), origin `user` | `ai`, `parent_id`.

| Block | Fields |
|---|---|
| `instrument` | `product` NQ/MNQ/ES/MES, `dataset` GLBX.MDP3, `symbol` (`NQ.v.0` …), `stype_in` |
| `session` | `calendar` XNYS, `timezone` America/New_York, `start`, `end`, `flatten_at`, `entry_cutoff`, `weekdays` |
| `rule` | `opening_range_breakout` {range_minutes, entry: stop_through_range \| close_beyond_range, direction, buffer_ticks} or `ma_crossover` {fast, slow, direction} |
| `exits` | `stop` range_opposite \| ticks \| none, `target` r_multiple \| ticks \| none |
| `execution` | `market_fill` next_bar_open, `slippage_ticks`, `limit_fill` trade_through \| touch, `same_bar_ambiguity` conservative, `latency_bars` |
| `costs` | commission and exchange fees per contract per side, USD |
| `sizing` | contracts, account capital, optional initial margin per contract |
| `validation` | split fractions, embargo, min OOS trades, parameter grid (≤ 60 combinations), walk-forward, cost stress, bootstrap |
| `assumptions` | per field: confirmed \| assumed \| unknown (an unknown blocks runs) |

## Run manifest (`qr-run-1`)

Fields: `kind`, `include_holdout`, strategy/version ids and number, `spec_sha256`, full
`spec`, `dataset` (id, snapshot SHA-256, provider, symbol, window, fixture), `engine` (name,
version, code revision), `calendar`, `environment` (python, numpy, pyarrow, databento),
`seed`, `live_trading: false`.

- Run id = `run-` + SHA-256 of the manifest. The same request returns the same run.
- Artifacts: `trades/fills/orders/sessions/equity.parquet`, `summary.json`, `manifest.json`,
  `report.md`.
- `results_sha256` covers trades, metrics, segments, test results and verdict. Reproduce
  recomputes the trades and compares them.

## Licensing

Databento data is licensed to the user's account.

- Reports carry derived statistics, rules and provenance only, never raw bars.
- The dataset manifest and the reports carry the note "local research use only — raw and
  derived data must not be redistributed".
