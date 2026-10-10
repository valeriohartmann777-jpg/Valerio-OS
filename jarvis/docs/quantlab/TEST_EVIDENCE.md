# QuantLab test evidence (Institutional Edition)

Updated: 2026-10-10 · branch `claude/jarvis-foundation-mxnsz4` · Linux container (Python 3.13,
Node 24, Chromium via Playwright, `xvfb-run`).

**What this evidence is:** engineering checks of the software. **What it isn't:** evidence
about any market.

- Every market-data path ran against the offline fixture provider
  (`JARVIS_QUANTLAB_PROVIDER=fixture`), which writes real DBN files with **synthetic** prices.
- Every key was stored in the labelled memory keystore (`JARVIS_QUANTLAB_KEYSTORE=memory`).
- Every Strategy Architect answer came from a scripted model
  (`JARVIS_QUANTLAB_ARCHITECT_SCRIPT`).
- No real Databento key, no macOS Keychain and no real Claude model were involved.

## 1. Commands and results

| Command | Result |
|---|---|
| `./scripts/check.sh` (ruff, ruff format, mypy strict on linux / win32 / darwin, backend pytest, desktop typecheck, vitest) | all passed — see §5 for the counts |
| `cd backend && .venv/bin/pytest -q tests/test_quantlab_hub.py tests/test_quantlab_futures.py tests/test_quantlab_research.py` | 47 passed (18 + 20 + 9) |
| `cd backend && .venv/bin/pytest -q tests/test_quantlab.py` | R1 equity lab: 48 passed, unchanged |
| `xvfb-run -a npm run test:e2e` | **26 of 26 steps passed**, including the new QuantLab futures step (11.5 s) and the R1 QuantLab step |

## 2. Data Hub — `backend/tests/test_quantlab_hub.py` (18)

| Requirement | Test | What is asserted |
|---|---|---|
| Key only in the OS keystore, verified by metadata | `test_connect_checks_metadata_and_keeps_the_key_only_in_the_keystore` | connected via metadata; key in the keystore; the key's bytes appear nowhere in the database, data files, events or status (only a 4-character hint); zero downloads |
| Bad keys store nothing | `test_rejected_or_malformed_keys_store_nothing` | malformed → `INVALID_FORMAT` before any provider call; revoked → `AUTH_INVALID` with a remedy; offline → `NETWORK`; keystore empty, status not connected |
| No plaintext fallback | `test_without_a_secure_keystore_nothing_is_stored_or_sent` | `keyring.backends.fail` → `NO_SECURE_KEYSTORE`, zero provider calls |
| Test / disconnect | `test_test_and_disconnect_keep_the_cache` | re-verification by metadata; revoke deletes the key and keeps cached data |
| A valid key ≠ available data | `test_catalog_comes_from_metadata`, `test_unlicensed_dataset_is_reported_as_entitlement` | schemas, range and conditions come from metadata calls; the unlicensed dataset reports `NO_ENTITLEMENT` |
| Cost first | `test_quote_rules_and_no_download_without_approval` | empty symbols and `ALL_SYMBOLS` refused; out-of-range dates, a non-ingestible schema, a bad range and stype refused; a quote has a price, records, both schemas, the "not a guaranteed price" caveat and a valid signature; zero downloads |
| Never buy a day twice | `test_download_caches_days_and_never_buys_them_twice` | second quote over the same window = $0 and no job |
| Approval binding | `test_approval_is_bound_capped_and_single_use` | budget < cost → `BUDGET_BELOW_ESTIMATE`; `OVER_REQUEST_CAP`; replay → `NOT_OPEN`; the first approval counts toward the month → `OVER_MONTH_CAP`; cap changes audited |
| Approval integrity | `test_expired_tampered_and_repriced_quotes_are_refused` | expired → refused; changed signature → refused; price change > 1 % → `SUPERSEDED` |
| Decline is final | `test_declined_quote_cannot_be_approved` | |
| Jobs | `test_cancel_and_restart` | a cancelled job stops before its last chunk; a job RUNNING when JARVIS stops is `INTERRUPTED` on the next start |
| Dataset integrity | `test_dataset_snapshot_manifest_quality_and_tamper_check` | manifest `qh-dataset-1`; files 0444; SQLite trigger blocks updates; a changed byte refuses the load |
| Errors | `test_provider_errors_are_classified_and_redacted` | real `BentoClientError` / `BentoServerError` → codes with remedies; the key never appears in the text |
| Logs | `test_logs_never_contain_keys` | `RedactingFilter` removes `db-…` and `sk-ant-…` from messages, args and extras |
| Contracts | `test_fixture_contracts_and_roll_dates` | HMUZ cycle, roll 7 days before expiry, instrument ids per contract |
| API | `test_api_connect_quote_approve_dataset` | full flow over HTTP; a request without `confirm: true` → 422; the key never appears in a response, the status or the audit |
| Restart | `test_restart_without_the_key_is_not_connected` | if the keystore entry disappears, the status is `KEY_MISSING`, not connected |

## 3. Futures engine — `backend/tests/test_quantlab_futures.py` (20)

Nineteen tests use hand-built bars with hand-calculated expected fills and P&L. Examples:

- **`test_long_breakout_hits_its_target`** — exact entry, stop and target ticks, fees, net
  USD.
- **`test_micro_contract_scales_by_its_own_tick_value`** — the same trade on MNQ is exactly
  1/10 of NQ.
- **`test_stop_and_target_in_one_bar_conservative_and_optimistic`** — conservative mode takes
  the stop, optimistic mode takes the target, and both are reported.
- **`test_both_breakouts_in_one_bar_are_excluded`** — the day is excluded and counted as
  ambiguous.
- **`test_gap_through_the_stop_fills_at_the_open`** — the fill is at the gap open, not at the
  stop price.
- **`test_future_bars_cannot_change_past_trades`** — truncating the data after a trade
  leaves that trade identical.
- **`test_roll_inside_a_window_is_skipped_not_traded`** — sessions spanning two contracts are
  `SKIPPED_ROLL`.
- **`test_windows_follow_dst_holidays_and_early_closes`** — exchange calendar: DST switch,
  Thanksgiving, the early close on the day after.
- **`test_contract_master_from_definitions_and_mismatch`** — specs come from definitions;
  MNQ data under an NQ spec is INVALID.
- **`test_audit_catches_a_tampered_ledger`** — the independent audit flags a changed fill.

**Bias regression:** `test_no_edge_on_a_driftless_random_walk`.

- Setup: 800 sessions of a continuous driftless walk with 20 sub-steps per minute, run
  through the ORB.
- Asserts: mean R < 0.05 and t < 2.5.
- Why it exists: the first fixture let breakout strategies win on random data, because its
  bars had no intrabar path and so no failed breakouts.
  - The engine itself was shown unbiased (mean R ≈ 0).
  - The fixture was rewritten (12 sub-minute steps per bar, session-continuous paths).
  - This test now guards the engine.

## 4. Research and validation — `backend/tests/test_quantlab_research.py` (9)

| Test | What is asserted |
|---|---|
| `test_backtest_validation_holdout_and_reproduction` | a backtest gives no verdict; the same request returns the same run; audit all PASS; contracts from definitions; leakage and independent reference PASSED; holdout NOT_RUN until confirmed (`HOLDOUT_NOT_CONFIRMED` otherwise); a second version's look at the same holdout is marked not independent; ≥ 7 variants in the trial registry; report says SYNTHETIC; reproduce identical |
| `test_runs_refuse_unknowns_and_wrong_instruments` | an `unknown` assumption blocks the run (`REQUIRES_CLARIFICATION`); an MNQ spec on an NQ dataset runs but its fitness is INVALID (`INSTRUMENT_MISMATCH`) |
| `test_cancel_and_restart` | cancel → CANCELED; the same manifest starts again under the same id and completes (this test found the race where the stopped worker overwrote the re-queued run; fixed with compare-and-set) |
| `test_api_research_flow` | the HTTP flow end to end |
| `test_split_is_chronological_with_embargo` | IS < OOS < holdout, embargo sessions unused |
| `test_bootstrap_is_seeded_and_dsr_penalises_many_trials` | the same seed gives the same resamples; the same Sharpe deflates more after 50 trials than after 1; too short a series → not applicable |
| `test_verdict_policy` | each verdict from fixed test outcomes; fixture capped at INSUFFICIENT_EVIDENCE with `would_be` |
| `test_architect_drafts_validates_and_asks` | a reply without the tool is nudged; an invalid spec goes back with the parser's errors; the third reply is valid; unknowns become questions, unsupported parts are listed, nothing is saved; the idea is wrapped in `<idea>`; without a model → `NO_MODEL` with the template as remedy |
| `test_api_interpret_with_scripted_architect` | `/quantlab/research/interpret` with the scripted model |

## 5. Full suite counts (this environment, 2026-10-10)

| Suite | Result |
|---|---|
| Backend pytest (whole JARVIS backend) | 467 passed (107 s) |
| Desktop vitest | 34 passed (6 files, incl. 3 QuantLab formatting tests) |
| E2E | 26 / 26 steps |

## 6. E2E QuantLab futures step (`tests/e2e/run.mjs`)

What the step does in the real Electron app with the backend started by Electron:

1. **Fixture banner.** The Data Hub shows the fixture banner.
2. **Bad key.** A revoked key is rejected ("rejected") and nothing is stored.
3. **Connect.** The fixture key connects, and "Test connection" appears.
4. **Contracts.** NQ, 2025-11-03 → 2026-03-03, "Show contracts" lists two instrument ids
   (NQZ5, NQH6).
5. **Quote, no approval yet.**
   - Quote: **$3.68** (fixture price), 109,198 records, 6.2 MB, degraded day 2026-02-17
     flagged.
   - The approve button is **disabled** until the box is ticked (asserted).
6. **Approval and download.** The job reaches `COMPLETED`.
7. **Dataset.**
   - Built from the cache: 109,095 bars, roll NQZ5 → NQH6 on 2025-12-12 shown, quality
     `USABLE · WARNINGS`.
   - The capability matrix shows stops/limits as LIMITED and spread/queue as NOT SUPPORTED.
8. **JARVIS draft.**
   - The scripted architect drafts the ORB.
   - `rule.direction` is unknown, so the state is DRAFT. After confirming it, the state is
     READY.
   - Save stays disabled until "reviewed" is ticked (asserted).
9. **Validation run.**
   - 66 trades, net −$4,769.16, out-of-sample +$1,363.50 on 25 trades.
   - Verdict **INSUFFICIENT EVIDENCE** (synthetic cap). The gates alone would say PROMISING
     RESEARCH CANDIDATE, with six robustness tests unmet.
   - Leakage PASSED; holdout NOT_RUN (sealed).
10. **Trade Explorer.** A trade's story and chart render.
11. **Reports and reproduce.**
    - The report carries "SYNTHETIC FIXTURE DATA".
    - Reproduce returns **Identical**.
12. **Institutional mode.** Risk & Execution shows the audit with PASS, and the Overview lists
    the run.
13. **Restart step (later in the E2E).** The QuantLab run is still listed after the app
    restarts.

Screenshots, all from the synthetic fixture: `docs/screenshots/quantlab-hub-quote.png`,
`quantlab-dataset.png`, `quantlab-studio.png`, `quantlab-backtest.png`,
`quantlab-validation.png`, `quantlab-trade.png`, `quantlab-overview.png`.

Note on the run above: 25 out-of-sample trades on 4 months of random-walk data produced a
positive OOS number while the whole run lost money. That is the kind of result the
robustness gates exist for. Here they leave it at "promising, not robust", and the synthetic
cap reduces it further. In a separate 9-month smoke run on the same fixture the
out-of-sample result was negative (−$5.2k on 57 trades, `would_be` REJECTED_HYPOTHESIS).

## 7. What none of this proves

- That the real Databento API responds exactly like the fixture. The fixture mirrors SDK
  0.87.0 call signatures and exception types; response shapes still need the real-key check
  in RELEASE_PLAN.md.
- That the macOS Keychain accepts and returns the key without problems on the user's Mac.
- That a real Claude model produces valid FuturesSpecs within 3 rounds as often as the
  scripted one.
- Anything about NQ, ES, MNQ or MES as markets.
