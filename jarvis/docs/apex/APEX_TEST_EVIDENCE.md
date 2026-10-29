# APEX — test evidence (APEX Ultimate, build kit 4.0)

What was run, on what, with which result. Linux cloud container (Electron under xvfb, the real
start path Electron → `BackendSupervisor` → `python -m jarvis`), branch
`claude/jarvis-foundation-mxnsz4`. **All market data in these runs was generated test data
(`fixture`, labelled SYNTHETIC)** — no real-data claim follows from any number here.

## Suites (final run)

| Suite | Command | Result |
|---|---|---|
| Backend lint / format / types | `scripts/check.sh` (ruff, ruff format, mypy linux + win32 + darwin) | all checks passed (301 files) |
| Backend tests | `scripts/check.sh` → pytest | **827 passed**, 0 failed |
| Desktop typecheck + unit | `scripts/check.sh` → tsc, vitest | typecheck clean; **75 passed** (14 files) |
| End-to-end (Electron, xvfb) | `npm run build && xvfb-run -a npm run test:e2e` | **36 / 36 passed** — log `docs/apex/evidence/e2e_run.log` |
| Acceptance matrix | `cd backend && .venv/bin/python ../scripts/apex_acceptance.py --e2e-log ../docs/apex/evidence/e2e_run.log` | 51 rows: 46 TESTED_SYNTHETIC, 5 BLOCKED (Mac access: tests 1–3, A, O), 0 FAILED, 0 TESTED_REAL — `docs/apex/evidence/acceptance_matrix.json` |

## New test files of this build

| File | Covers |
|---|---|
| `test_apex_zonemap.py` | capability honesty, causal pivots, one episode per visit, flips, top four / fewer, no look-ahead, immutable snapshots, migrations |
| `test_apex_context.py` | path partition + ambiguity, interactions per episode, bias, macro point in time with DST, unavailable related markets |
| `test_apex_setups.py` | acceptance 22–28, scenarios E, H, I, K, L; zone-map cache exactness |
| `test_apex_probability.py` | acceptance 29–30, scenario I; random-walk negative control, windows / embargo / holdout untouched, determinism |
| `test_apex_orderflow.py` | acceptance 17–19, scenario F |
| `test_apex_governance.py` | acceptance 35, scenario M; CIPHER proposes, SENTINEL vetoes, owner approves |
| `test_apex_protocol_sync.py` | acceptance 32: Pydantic ↔ TypeScript contract |
| `apexView.test.ts` | the eight checkpoints, decision verdicts, chart bands and path lines |

## E2E APEX step (what the window proved)

QuantLab has five first-level areas; XAUUSD analysis on generated data shows eight checkpoints
with App passed, Data **not** fit (test data), Probability / Forward / Signal not passed; mode
SYNTHETIC; verdict WAIT or NO TRADE (never LONG / SHORT); three setups; any plan marked not
eligible with `PROBABILITY NOT VALIDATED` and failed gate checks; chart labels only S1–S4 /
R1–R4; a zone label opens its evidence with order flow UNAVAILABLE; nine timeframes; all five
strategies journaled, none eligible, none executable; SENTINEL re-check passes; Market Context
shows the macro check UNAVAILABLE until a calendar is imported (a bad row refused with its
line); Trade Replay lists the stored analyses. Screenshots: `docs/screenshots/apex-workstation.png`,
`apex-zone-evidence.png`, `apex-market-context.png`.

## Not tested — and why

- **Mac runtime** — BLOCKED: MAC ACCESS (`APEX_MAC_RUNTIME_AUDIT.md`).
- **Any real-data claim** (coverage, spreads, validation, calibration, forward) — BLOCKED: no
  real market data reachable (market-data hosts refused; nothing imported).
- **Delta on real trades, absorption, options** — no such source connected.
