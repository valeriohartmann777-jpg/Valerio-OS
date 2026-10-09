# QuantLab Implementation Status

Updated: 2026-10-09
Repo location: `Valerio-OS/jarvis` (the repo root holds an unrelated Next.js project, untouched)
Branch: `claude/jarvis-foundation-mxnsz4`
Stack: Electron + React 19 + Tailwind 4 + Zustand (`apps/desktop`), shared TS types
(`packages/protocol`), FastAPI + Pydantic + aiosqlite backend (`backend/jarvis`), EventBus →
WebSocket, versioned SQLite migrations, pyarrow for Parquet. Integration: Plan A from
`docs/quantlab-handoff/spec/11_REPO_INTEGRATION_GUIDE.md` (module inside JARVIS, no second app).
Decision record: D-026 in `DECISIONS.md`. Architecture: §13f in `ARCHITECTURE.md`.

Release scope: **R0 + R1**. No real market dataset was available in this environment, so
**no market evidence has been produced** — every result so far comes from the handoff's
SYNTHETIC / TEST ONLY fixtures or from fabricated test data inside the test suite.

## COMPLETE (implemented & exercised)

- [x] **T0 Repo reconnaissance** — handoff copied to `docs/quantlab-handoff/` (nothing
      overwritten); integration points recorded below; baseline: 354 backend tests passing.
- [x] **T1 Route & UI shell** — "QuantLab" in the top bar; sections Overview · Strategies ·
      Experiments · Datasets · Reports; teal accent tokens scoped as `ql-*`; empty, loading,
      running, failed, cancelled and rejected states; no curve or number without a real run.
- [x] **T2 Strategy registry** — StrategySpec v0.1 as strict Pydantic models (unknown fields
      forbidden, strings for integers refused, IANA timezone checked); canonical JSON +
      SHA-256; strategies with insert-only versions (SQLite trigger refuses UPDATE; the same
      spec twice is the same version); Architect form with Unknown / Assumed / Confirmed per
      field, live validation, warnings, plain-language pre-run summary, explicit review box.
      Futures / FX / metal specs → `UNSUPPORTED_INSTRUMENT` (NQ, NQZ6, MNQ, CME…, XAUUSD).
- [x] **T3 Data Passport importer** — CSV (delimiter sniffing, decimal commas,
      MetaTrader `<DATE>`/`<TIME>` exports) and Parquet; column auto-mapping with overrides;
      timestamps with offsets, epoch, dates, or naive + declared IANA zone; DST-ambiguous local
      times refused. BLOCK: missing columns, unparseable/naive-without-zone times, non-numeric,
      empty, NaN/Inf or ≤ 0 prices, OHLC violations, negative volume, duplicate timestamps,
      overlapping bars, frequency mismatch, < 2 bars, unsupported instruments. WARN (visible,
      nothing changed): sort fixes, gaps (no exchange calendar), irregular spacing, outlier
      moves > 20 %, zero/missing volume, undeclared corporate actions, unverified licence.
      Passport with every handoff field; original copy + Parquet snapshot frozen per dataset
      (staged write, rename, checksum verified before every run). Fixtures recognised by
      SHA-256 and labelled SYNTHETIC / TEST ONLY wherever they appear.
- [x] **T4 Reference simulator** — exact (Decimal) SMA crossings on closed bars (ties stay
      ties); signal at bar close, available at `bar_start + interval`, market fill at the next
      bar's open with directional slippage; fixed + bps fees; no fill without cash (REJECTED,
      never financed); no shorts, no pyramiding; last-bar signal NOT_EXECUTABLE; open position
      marked at the last close. Ledgers: signals (with disposition), orders, round trips;
      per-bar cash/position/equity. Independent audit re-derives causality, next-open prices,
      cash flows, equity identity, P&L identity, matched round trips, no phantom fills.
- [x] **T5 Experiment runner + storage** — manifest (spec/version/dataset hashes, engine name
      + version, git revision, simulation config, split, verdict policy, Python/pyarrow
      versions, `live_trading: false`); experiment id = SHA-256 of the manifest → asking again
      returns the same experiment; queued → running(stage) → completed / failed / cancelled
      with `quantlab.*` events; one run at a time in the backend, cancellable; artifacts
      (`manifest.json`, `data_qa.json`, `metrics.json`, `trades/orders/signals/equity.parquet`,
      `audit.jsonl`) staged and renamed only on success, SHA-256 per file; runs interrupted by
      a restart are marked failed (`INTERRUPTED`); **Reproduce** re-runs from the snapshot and
      compares `results_sha256` (identical in tests and in the E2E run).
- [x] **T6 Chronological OOS + metrics** — last ⌈n × fraction⌉ bars are OOS (fixed in the
      spec before the run; no parameter search exists); one continuous simulation, OOS judged
      from the equity at the last train close; net/gross, fees, slippage, return with stated
      denominator, max drawdown (% and money), trades, win rate / profit factor `n/a` when
      undefined, exposure, buy & hold price return (labelled: no costs, not exposure-matched);
      Sharpe NOT_RUN with the reason; "OOS not meaningful" below 30 OOS trades.
- [x] **T7 Dashboard & acceptance** — cockpit (latest experiment, JARVIS assessment, equity
      net of costs with TRAIN / OUT-OF-SAMPLE divider and drawdown panel, validation matrix,
      top warnings, recent runs, "Command JARVIS" box); experiment detail tabs Overview /
      Trades (explorer + trade detail with signal bar, known-at time, fill bar, prices, fees,
      cash) / Assumptions / Validation (gates A–E, each check `{id, method_version, result,
      assumptions, metric, observations, evidence_artifact}`) / Audit (hashes, invariants,
      artifacts, manifest, reproduce). Verdict policy: INVALID if Gate A fails; FAILED if
      ≥ 30 OOS trades lose after costs; otherwise INCONCLUSIVE. R1 never returns
      PROMISING_RESEARCH_CANDIDATE and never says "validated".
- [x] **JARVIS integration** — `quantlab_report` brain tool (read-only), persona line, events in
      the activity feed; the existing command center keeps working (full regression green).

## Test matrix (handoff `spec/09_TEST_MATRIX.md`) — `backend/tests/test_quantlab.py`

| ID | Covered by | Result |
|---|---|---|
| Q-001/002/003 | `test_golden_execution_fixture_reconciles_exactly` (values read from `golden_expected.json`) | PASS |
| Q-004 | `test_signal_on_last_bar_is_not_executed` | PASS |
| Q-005 | `test_buy_without_enough_cash_is_rejected_not_financed` | PASS |
| Q-006 | `test_ma_fixture_matches_the_independent_reference` (own Fraction-based SMA) | PASS |
| Q-007 | `test_future_prices_cannot_change_past_decisions` | PASS |
| Q-008/009 | `test_invalid_fixture_is_rejected_with_both_reasons`, `test_bad_rows_block_the_dataset` | PASS |
| Q-010 | `test_timezones_unknown_converted_and_dst_ambiguity` | PASS |
| Q-011/012 | `test_same_inputs_same_experiment_and_reproducible` | PASS |
| Q-013 | `test_versions_and_snapshots_are_immutable` | PASS |
| Q-014 | `test_fixture_run_end_to_end` (split asserts) | PASS |
| Q-015 | spec contract (`allow_parameter_search_on_oos: true` refused); no search code exists | PASS |
| Q-016 | fixture → SYNTHETIC label in passport, dataset view, summary, UI (E2E) | PASS |
| Q-017 | `test_futures_and_fx_specs_are_unsupported`, `test_futures_data_is_unsupported…` | PASS |
| Q-018 | stops/targets refused by the spec contract (no intrabar ambiguity possible in R1) | PASS (unsupported, by design) |
| Q-019 | `test_invalid_specs_are_refused_with_details`, `test_api_flow_and_errors` | PASS |
| Q-020 | event sequence test; UI refetches on `quantlab.*` events and polls running runs; failed runs show no foreign results | PASS (backend), E2E for UI |
| Q-021 | full backend suite + E2E regression | PASS |
| Q-022 | `test_no_trading_or_code_execution_surface` | PASS |

Additional: zero trades → `n/a` metrics and no NaN; slippage direction and bps fees; tampered
ledger caught by the audit; exact ties; tampered snapshot refused; cancel before start;
interrupted runs; pre-registered FAILED and positive-OOS-still-INCONCLUSIVE cases on
fabricated test data; path traversal on ids; downsampling keeps peaks and troughs.

## IMPLEMENTED BUT UNVERIFIED
- [ ] Import of a real, licensed market file (only fixtures and fabricated test data so far).
- [ ] The Mac app on the user's MacBook (Python 3.13): pyarrow is a new dependency; the
      in-app update's preflight installs it — not yet observed on that machine.
- [ ] Very large files: measured 200,000 one-minute bars (12 MB CSV) → import 4.2 s, snapshot
      0.8 s, simulation 0.8 s, audit + metrics + series 2.0 s (Linux container). The 2,000,000
      row limit itself was not exercised.

## IN PROGRESS
- —

## BLOCKED
- Market evidence: no real historical dataset is bundled or was provided. Until the user
  imports licensed bars, every QuantLab result is an engineering check.
- The user's own markets (NQ futures, XAUUSD) are out of R1 scope by design and refused as
  `UNSUPPORTED_INSTRUMENT`; they need their own accounting engines (R4).

## NEXT
1. Import real daily bars for one liquid ETF (e.g. SPY or a broad index ETF, adjusted, with
   provider and licence noted) and run one frozen 20/50 spec once — the first real verdict.
2. R2: walk-forward with a parameter range fixed in advance, plus a pre-registered cost
   stress (slippage ×2 / ×3), recorded as experiments in the same registry.
3. Design the futures accounting (multiplier, tick value, roll, margin) as a separate engine
   with its own golden fixtures before NQ is accepted.

## Commands actually executed (this environment, 2026-10-09)
- Backend tests: `cd backend && .venv/bin/python -m pytest -q` → 402 passed (354 before + 48 QuantLab)
- Lint/types: `ruff check`, `ruff format --check`, `mypy jarvis` (strict) → clean
- UI typecheck + build: `cd apps/desktop && npm run build` → ok
- UI unit tests: `npx vitest run` → 29 passed
- E2E: `xvfb-run -a npm run test:e2e` → all 24 steps passed, including the QuantLab step
- Fixture arithmetic: `python -I docs/quantlab-handoff/fixtures/verify_fixture_math.py` → PASS

## Known limitations
- Instruments/timeframes: one cash equity, long only, 1m / 5m / 1h / 1d bars, USD-style
  single currency (no FX conversion); no futures, options, CFDs, FX or crypto.
- Datasets: no exchange calendar (sessions, holidays, early closes, DST of the venue);
  dividends and splits not handled — declare adjusted data; no automatic downloads.
- Fill assumptions: next-bar open with zero latency and modelled slippage (optimistic);
  no partial fills, no volume constraint, no spread model, no stops/targets/limits.
- Missing evidence gates: Sharpe/risk-adjusted metrics, walk-forward, parameter stability,
  cost stress, regimes, dependence-preserving bootstrap, multiple-testing adjustment,
  forward paper test (all shown as NOT_RUN).
- AI integration: JARVIS reads results through `quantlab_report` and answers critically; it
  does not write specs (natural language → spec is R3, and would stay DRAFT until confirmed).

## Screenshot and artifact links
- Committed: `docs/screenshots/quantlab-cockpit.png`, `quantlab-experiment.png`,
  `quantlab-passport.png` — all from the SYNTHETIC fixture, not market results.
- Every E2E run also writes `tests/e2e/output/11c…11g-quantlab-*.png` (git-ignored).
- Run artifacts at runtime: `data/quantlab/experiments/<exp_id>/` (git-ignored).
