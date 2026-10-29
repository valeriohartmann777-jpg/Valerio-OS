# JARVIS APEX — test evidence (recovery & validation)

> Earlier phase (P0–P5). APEX Ultimate evidence: `docs/apex/APEX_TEST_EVIDENCE.md`.

What was run, on what, with which result — 2026-10-28, branch `claude/jarvis-foundation-mxnsz4`,
in a Linux cloud container (Electron under xvfb, the real start path Electron →
`BackendSupervisor` → `python -m jarvis`).

**All market data in these tests is generated test data** (`apex.data.synthetic_bars`, labelled
SYNTHETIC) or hand-built bars. The tests prove software behaviour — fills, timing, gates,
contracts, protocol mechanics — and nothing about any market. Real-data evidence: none yet
(`APEX_IMPLEMENTATION_STATUS.md`).

## Totals

| Suite | Command | Result |
|---|---|---|
| Backend (pytest) | `scripts/check.sh` → `pytest` | **725 passed**, 0 failed (7 min 50 s) |
| Lint / format / types | ruff, ruff format, mypy (strict) | clean |
| Desktop unit (vitest) | `npm test` in `apps/desktop` | 13 files, 67 tests passed |
| Desktop types | `tsc` (renderer + electron) | clean |
| End-to-end | `xvfb-run node tests/e2e/run.mjs` | **36 / 36 passed** (§6) |
| `scripts/check.sh` overall | | `✓ all checks passed` |

## 1. P0 — backend reachability

| Test | Proves |
|---|---|
| `electron/backend.test.ts` (8) | causes are classified from real output (missing package, port in use, database, permission, crash); secrets are redacted; a backend that crashes at import stops after 5 quick crashes with `MISSING_DEPENDENCY` and writes its log file; changed `pyproject.toml` → `pip install` before the start; two `ensureRunning()` plus a `restart()` leave exactly one backend and nothing listening after shutdown (the orphan found during this work) |
| `src/lib/backend.test.ts` (2) | header text: "Backend online" only with the event stream **and** readiness; degraded / unreachable / installing / offline |
| `tests/test_health_ready.py` (2) | `/health/live`, `/health/ready` with real checks, `/system/capabilities` (`orders: false`); a failing database → 503 "degraded" naming the check, live still 200 |
| E2E "a crashed backend is started again …" | kill the backend → banner and "Backend offline" → restarted → "Backend online", banner gone |
| E2E "a backend that can't start is diagnosed …" | fake Python without `pyarrow` → banner `crashed`, cause `MISSING_DEPENDENCY` naming the package, log lines, **Repair** runs pip; a port held by another program → `PORT_IN_USE` naming its pid; Restart after freeing it → "Backend online" |
| E2E "backend supervised by Electron is stopped on quit", "an outdated backend … is replaced", "starting a newer version takes over" | no orphans, no stale backends |

Before → after: `APEX_RECOVERY_AUDIT.md` §1.

## 2. P1 — data fitness

| Test | Proves |
|---|---|
| `test_apex_fitness.py` (4) | a planted missing day, a 45-minute gap, 7 single missing minutes and a 5 % wick are each counted under their reason / minute, the reasons add up to all missing minutes, nothing is repaired; weekend and daily break are never missing; a CFD proxy for NQ is `DATA_UNAVAILABLE`; the endpoint serves the view |
| `test_apex_data_repair.py` (10), `test_apex_pipeline.py` (3) | (D-034, still green) cache healing, loud outages, update reports, gap/spike rules, download → chart pipeline |
| E2E | Data fitness tab: coverage 100.00 % of expected bars on test data, three gap reasons, quote side "generated" |

## 3. P2 — engine and signal contract

| Test | Proves |
|---|---|
| `test_apex_signal.py` (17) | the contract refuses broken long/short geometry, TP2 before TP1, R:R ≤ 0, an order plan on FAIL data, a stop entry without trigger, any plan on NO_TRADE / WAITING / BLOCKED, BLOCKED without reasons, PAPER_VALIDATED without OOS/forward support, a probability without interval or with < 30 calibration cases; records are immutable |
| `test_apex_signals.py` (17) | a bullish 5-minute rejection of S1 → buy stop one tick above the bar, stop and targets exactly as specified; no confirmed trigger → WAITING_FOR_TRIGGER with the missing condition named; stale bar → waiting; short mirrors; too little reward → rejected with reason; stop entries fill only when the move continues, gaps fill worse, cancellation when price closes beyond the stop first; on bid data a short's stop triggers on the ask and a buy limit needs the ask; execution model (measured / estimated spread, never below the spread, trade prices, stress, round trip); the CFD proxy gets no plan for NQ; both strategies give identical decisions with and without later bars (no look-ahead, XAUUSD and NQ); every signal of 120 analyses satisfies the contract and survives a JSON round trip; signals are stored once and refuse UPDATE/DELETE; migration 21 rolls back (data kept, backup written) and applies again; the API serves signals and refuses an unknown strategy; SENTINEL passes an analysis whose plans carry a track record |
| `test_apex.py` (30) | (APEX-1..3, still green) no look-ahead, conservative resolution incl. the random-walk check, rates, track record, SENTINEL, journal immutability |
| E2E | Analyst panel sections for both strategies; every stored signal has a plan iff it is a CANDIDATE; no PAPER_VALIDATED, no probability; SENTINEL 3/3 |

## 4. P3 — validation protocol

| Test | Proves |
|---|---|
| `test_apex_validation.py` (12) | the YAML file equals what the code judges with (1.1.0); the split is chronological and contiguous, the warm-up stays out, a locked holdout is reused with more data; no decision's outcome window reaches into the next fold (embargo ≥ label horizon); the bootstrap resamples whole days; the random baseline gives p < 0.05 only to a strategy that beats chance and p = 1 to one that doesn't; development measures all six criteria, reconciles to its trade ledger, cost stress never helps, every trade lies in the development window, the run is reproducible; the verdict table never overstates (test data → at most RESEARCH_ONLY); a run registers 7 trials, freezes the holdout (UPDATE refused), keeps it closed when development failed, and a second run counts 14 trials with the same holdout; new signals carry the state and the validation id; a running validation can be cancelled; a holdout opens once per version (the first result stands); the API starts, lists and refuses unknown strategies |
| E2E | Validation tab on test data: RESEARCH_ONLY, 6 criteria, ≥ 7 trials, holdout panel; a 5-minute validation started and cancelled |

Sample (test data, 110 days, `zone_reaction`): 98 filled trades on 33 days, mean −0.074 R
[−0.373, +0.237]; baseline p = 0.381; cost stress −0.140 R; 1/6 neighbours and 1/4 folds
positive → every criterion except EVIDENCE fails — the protocol finds no edge in a random walk,
as it must.

## 5. P4 / P5 — UI, forward log, ULTRON

| Test | Proves |
|---|---|
| `analyst.test.ts` (3) | App online ≠ Data ready ≠ Signal validated: an unvalidated candidate is never green; blocked / stale / proxy data are not ready, with the reason; test data never plain ready; bid-only execution text |
| `test_apex_forward.py` (5) | the forward log is opt-in and persisted; a round analyses every market in session and records live (non-replay) signals; outside the session nothing runs; forward evidence counts each live plan once (re-made plans, replays and older versions excluded) and test data never counts for real; only an OOS_SUPPORTED version on real data is lifted to FORWARD_SUPPORTED, a REJECTED one never; validation runs show as CIPHER work in the HQ |
| E2E | status strip tones; Start analysis as a job — Cancel → "Analysis cancelled", Retry → success; Signal journal rows; Backtest per strategy with calibration; forward log: run one round (ok or outside session), switch on and off; no `/apex` route mentions order, trade, broker or execute (≥ 14 routes) |

## 6. End-to-end

`xvfb-run -a node tests/e2e/run.mjs <out>` — the whole app (Electron, supervisor, backend,
fixture data providers), 36 steps: **all 36 passed** on the final run (APEX step 38.8 s,
including a full validation run and the forward log). Screenshots of the APEX steps: `14a…14f`
in the run's output folder (analyst `14c2`, data fitness `14d`, validation `14f`).

Defects the test runs found during this work, all fixed before the final run: the orphan
backend after a restart click during a scheduled restart (P0); the screen jumping back to
"Chart & analysis" when an analysis finished (P4); the new event type missing from the
TypeScript protocol list (`test_protocol_sync.py`).

## 7. What is not evidence

- No real market data was analysed (container network policy). No real-data track record,
  validation or forward result exists.
- The Mac itself was not tested from here; the start-path diagnosis is proven on Linux with the
  same code and must be confirmed on the owner's machine.
- The XAUUSD spread estimate (0.30) is an assumption until ask candles are measured.
