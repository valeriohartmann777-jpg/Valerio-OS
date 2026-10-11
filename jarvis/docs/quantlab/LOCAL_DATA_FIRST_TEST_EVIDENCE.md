# Local Data First — test evidence (D-031)

Date: 2026-10-11 · Branch `claude/jarvis-foundation-mxnsz4` · Linux container (no macOS, no
Databento account). All market data below is **synthetic** (labelled fixture provider or
formula toy bars). The zstd DBN files are real DBN encoded with the official `databento`
package, but their prices are synthetic.

Suite: `backend/tests/test_quantlab_vault.py` — 28 tests (the 20 required by §K, the
variants 9b and 9c, 4 parametrized cases of test 3 counted once, plus CSV checks,
backup/restore and offline mode). Run: `cd backend && .venv/bin/pytest tests/test_quantlab_vault.py`.

"Network blocked" means: `socket.connect`, `socket.create_connection`, `getaddrinfo` and
`databento.Historical` are patched to raise and record the attempt; the test asserts the
record is empty and `hub.adapter.calls == 0`.

| § K | Requirement | Test | What it proves |
|---|---|---|---|
| 1 | Pre-imported NQ 1m run, network blocked, no Databento call | `test_01_cached_backtest_runs_with_the_network_blocked` | DBN files (bars + definitions) imported with no key connected; Strategy → NQ → 1m completes with trades, all conservative audit checks PASS, contracts come from the definition records; zero socket attempts, zero adapter calls. |
| 2 | 1m → 5m → 15m on one snapshot, correct bars, provenance | `test_02_timeframes_share_one_snapshot_and_derive_correct_bars` | One dataset id for all three runs; no acquisition, no job; 5m bars equal an independent plain-Python grouping in New York time over 4 months (incl. early closes); summary carries aggregator `ql-resample-1`, source 1m and the derived hash. |
| 3 | 2m, 3m, 10m, 30m vs independent fixtures | `test_03_derived_bars_match_independent_fixtures[2,3,10,30]` | A linear price path whose OHLCV per bucket has a closed form; every bar matches, timestamps are bucket starts from 09:30 ET. |
| 4 | Sessions, DST, early close, missing bars, no-trade gaps | `test_04_sessions_dst_early_close_and_missing_bars` | 09:30 EST = 14:30 UTC and 09:30 EDT = 13:30 UTC around the March switch; buckets anchored in New York; Black Friday 13:00 close → 1h bars 60/60/60/30 with the last flagged partial; missing minutes are not filled, an empty bucket produces no bar. |
| 5 | No bar mixes instrument ids at a roll | `test_05_no_bar_mixes_instrument_ids` | A 5m bucket crossing an id change becomes two bars, one per contract, with exact OHLC; the engine skips that session (`SKIPPED_ROLL`) instead of trading across it. |
| 6 | NQ missing, ES present → block, no substitution | `test_06_missing_nq_is_never_replaced_by_es` | Readiness for NQ is NOT_AVAILABLE although ES data exists; launch refuses. |
| 7 | Only 5m source → 1m blocked | `test_07_five_minute_source_blocks_one_minute` | A 5m CSV imports as a 5m series; NQ 1m → WRONG_GRANULARITY; 5m and 15m are allowed. |
| 8 | Free quote via `get_cost`; purchase needs exact, unexpired approval with a maximum and authentication | `test_08_quote_is_free_and_purchase_needs_exact_approval` | No key → no quote. Quote from `metadata.get_cost`, no batch job created (`submit_batch` patched to fail). Execute without approval → NOT_APPROVED; approve without confirm / wrong hash / below quote / above cap → refused; after 31 minutes → APPROVAL_EXPIRED; offline mode → OFFLINE_MODE. Still zero jobs. |
| 9 | Duplicates never charge twice; resumable | `test_09_duplicates_never_charge_twice`, `test_09b_an_uncertain_submission_is_looked_up_never_repeated`, `test_09c_network_loss_during_submit_stops_and_asks` | Same request → same acquisition; two parallel executes → each batch job (1m bars, definitions) submitted once; afterwards the same request is NOTHING_TO_BUY at $0; a re-import of the same DBN is recognised by hash. After a restart in SUBMITTING, a job that exists at the provider is adopted via `list_jobs`, not resubmitted. A network error during submit → ATTENTION (`SUBMIT_UNCERTAIN`) after exactly one attempt; "check again" only looks up. |
| 10 | Interrupted download resumes, archive intact, hashes verified | `test_10_interrupted_download_resumes_and_verifies` | The fixture cuts one file mid-transfer once; the retry resumes, every file's SHA-256 matches the provider hash, originals are read-only (0444), no partial file is left in staging, and the vault audit is clean. |
| 11 | Rebuild derived bars offline after the cache is deleted | `test_11_rebuild_derived_offline_after_deletion` | `derived/` removed, network blocked → **Rebuild derived bars** recreates every entry bit-identically; `reproduce` of the earlier 5m run is identical and its trades unchanged. |
| 12 | Bar start timestamps; close-based signal can't fill early | `test_12_close_signal_fills_only_after_the_bar_completed` | On 5m toy bars a breakout on the 09:40 bar is known at 09:45 and fills at the 09:45 bar's open, not earlier; the run audit (incl. `LAST_BAR_CLOSE`) passes. |
| 13 | Ledger NQ vs MNQ | `test_13_ledger_nq_vs_mnq` | Same 11-tick trade: NQ $55 gross − $6 fees = $49; MNQ $5.50 − $1.74 = $3.76 (MNQ fee profile); gross ratio exactly 10; the note names the MNQ contract master as the source. |
| 14 | Stop and target in one bar → conservative | `test_14_stop_and_target_in_one_bar_is_resolved_conservatively` | Default mode exits at the stop (−$61), flagged ambiguous; only the separate optimistic mode takes the target (+$49), also flagged. |
| 15 | Reproducible runs from hashes | `test_15_runs_are_reproducible_and_identified_by_their_inputs` | Same inputs → same run id; manifest has timeframe, engine 1.1.0, effective-spec hash and snapshot hash; `reproduce` → identical; 15m → a different run. |
| 16 | UI shows true coverage, filters by symbol/timeframe | `test_16_options_and_readiness_show_true_coverage` | Empty vault → no local data shown; after import NQ.v.0 with its real start; NQ $5/tick vs MNQ $0.50; 5m/15m READY, 10m INCOMPATIBLE (15-min opening range), source labelled "supplied by you"; MNQ NOT_AVAILABLE. |
| 17 | 100 reruns → zero provider charges | `test_17_a_hundred_reruns_cost_nothing` | 100 launches across 1m/5m/15m with the network blocked: zero attempts, zero adapter calls, 3 distinct runs, no acquisitions, no jobs. |
| 18 | Corruption detected, never "ready" | `test_18_corruption_is_detected_and_repaired_from_the_original` | One canonical day altered on disk → audit reports it, readiness INVALID, launch refused; `audit(repair=True)` rebuilds it from the raw DBN → READY. |
| 19 | Secrets not exposed | `test_19_secrets_are_not_exposed` | After a full purchase the key (or its body) appears in none of: vault overview, acquisitions, hub status, debug logs, `qv_acquisitions`, `qv_approvals`, `qh_cache`, `qh_connections`, `qh_audit`, `events`. |
| 20 | Real UI path: strategy → NQ → 5m → Backtest → chart + ledger + validation, one cached dataset, no network | `test_20_ui_flow_over_the_api_with_the_network_blocked` (HTTP API) **and** E2E step "QuantLab local data first" (Electron UI) | API: DBN upload + commit → strategy from `/quantlab/backtest/options` → readiness 5m READY → `POST /quantlab/backtests` (full validation) → chart candles/equity/markers, trade ledger, validation with a verdict from the fixed policy and timeframe 5m; ES → 409. Network blocked throughout. UI: see below. |

Additional:

- `test_csv_import_checks_refuse_what_they_cannot_place` — timestamps without a timezone →
  TIMEZONE_REQUIRED; back-adjusted prices off the 0.25 tick grid → OFF_TICK_GRID; no contract
  mapping → imported with NO_CONTRACT_MAPPING and readiness MISSING_MAPPINGS (not runnable).
  The importer has further checks (irregular spacing, duplicates, DST-ambiguous local times)
  that this test doesn't exercise.
- `test_backup_and_restore_round_trip` — backup needs confirmation, is VERIFIED and clears the
  "no backup" warning; a deleted canonical day fails the audit; restore puts back exactly
  that file; audit clean.
- `test_offline_mode_blocks_every_market_data_request` — Data Hub quote and vault quote both
  raise OFFLINE_MODE; switching back allows a quote again. (That backtests still run offline
  is covered by tests 1, 2, 11, 17 and the E2E step.)

## E2E (Electron, real UI, xvfb)

Step **"QuantLab local data first: Data Vault buys MNQ once, then Strategy → NQ → 5m →
Backtest runs offline"** in `tests/e2e/run.mjs`:

1. Data Vault → Acquire once: MNQ.v.0, free quote (fixture provider, labelled), maximum,
   tick box, Approve, Start download → COMPLETED.
2. Offline mode on.
3. Backtest Lab: strategy "NQ 15-minute opening range breakout" → NQ → 5m: badge
   "Ready locally", 10m marked not ready; Backtest → results with the provenance card
   "NQ · 5m".
4. MNQ → 15m → Backtest → completes on the purchased MNQ data.
5. ES → "Not available" with **Open Data Vault**, Backtest disabled.
6. `provider_requests` in the vault overview unchanged across steps 2–5.
7. Offline mode off.

Screenshots: `docs/screenshots/quantlab-backtest-lab.png`,
`quantlab-data-vault.png`, `quantlab-vault-quote.png`.

## Regression

- Engine 1.0 → 1.1 (bar duration): all existing futures, research, SENTINEL and mission tests
  pass unchanged; 1-minute results are identical.
- Full backend suite, vitest, typecheck and the full E2E run: see the commit's check output
  (`./scripts/check.sh`, E2E 31/31).

## Not verified here

- A real Databento batch job (`submit_job`, `list_files`, `download`) and its real cost.
- Real DBN files from Databento (only DBN encoded locally with the official package).
- The macOS Keychain and the Mac app.
- Large histories (years of 1-minute bars): import and resample speed on the Mac.
