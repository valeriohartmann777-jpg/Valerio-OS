# JARVIS 2.0 — status

As of 2026-10-11, branch `claude/jarvis-foundation-mxnsz4`. Not "production ready": it is a
working, tested build for the owner's own use, verified on Linux (Electron under xvfb) with
scripted AI models and synthetic or fixture market data. See §4 for what was **not** verified.

## 1. Milestones

| Milestone | State | Commit |
|---|---|---|
| J2-0 Audit, baseline, rollback point (`192bd1a`) | done | `b46ab5d` |
| J2-1 Daily-use blockers and false states | done | `d88f9b1` |
| J2-2 New shell, five tabs, routes, Home, Settings, Interface panel | done | `0d7431d` |
| J2-3 Training Hub (feedback, evaluations) + QuantLab Solo polish | done | `7869f23` |
| J2-4 ULTRON HQ: rooms, nine robots, state projection, Inspector | done | `700665b` |
| J2-5 ULTRON QuantLab: team workspaces, grants, agent tasks | done | `7869f23` |
| J2-6 Navigation/layout checks, rollback rehearsal, docs | done | this commit |

Last full run: `./scripts/check.sh` green (ruff, format, mypy ×3, **pytest 625**, typecheck,
**vitest 47**); **E2E 34/34** (`tests/e2e/run.mjs`, real Electron app + backend).

## 2. Acceptance matrix (brief §14)

✅ executed and passing · ◐ partly covered · ✗ not verified here

| # | Criterion | | Evidence |
|---|---|---|---|
| 1 | Exactly five tabs, keyboard accessible | ✅ | `routes.test.ts`; E2E *navigation* (order, ←/→/End/Enter) |
| 2 | Home by default; area colour/icon follows section | ✅ | `routes.test.ts` (empty address → Home); `--area` per page |
| 3 | Deep link, reload, history | ✅ | E2E *navigation*: `#/training/evaluations` → reload → back/forward; `#/learning`, `#/bots` |
| 4 | Reachable at 1366×768, 1920×1080, narrow, 125/150 % DPI | ◐ | E2E at 1366×768 and the window minimum 1120×720 (tabs, composer, Run backtest, HQ, no sideways scroll); 1480×920 throughout. **1920×1080 and DPI scaling not run.** |
| 5 | Contrast, reduced motion, screen-reader labels | ◐ | contrast computed from tokens (`JARVIS_2_DESIGN_SYSTEM.md`, all text ≥ 5.1 : 1); reduced-motion / high-contrast switches exist; labels on tabs, robots, icons. **No automated axe/screen-reader audit.** |
| 6 | Chat sends with AI configured, honest no-service state otherwise | ✅ | E2E *open-ended request without a key*, *AI & Billing* (scripted plan stand-in) |
| 7 | API error / restart without fake success | ✅ | E2E *failures are explained*, *a crashed backend is started again* |
| 8 | Settings persist across restart, migrated intact | ✅ | `test_feedback.py::test_settings_and_training_hub_data_survive_a_restart`; `test_memory`, `test_conversation`; rollback rehearsal |
| 9 | Learning data in Training Hub, no weight-training claim | ✅ | E2E *learning*, *memory*, *feedback*; Overview truth panel |
| 10 | Nine identities, roles, original robots | ✅ | `test_team_hq.py`; E2E HQ step (9 robots) |
| 11 | Idle → Homebase, active → task room, failed/offline shown | ✅ | `test_team_hq.py` (10 tests); E2E FORGE in Workshop and back |
| 12 | Inspector shows the backend's task | ✅ | E2E HQ step ("Mission 001", last output) |
| 13 | Room click changes content | ✅ | E2E (`hq-room-content-<room>`) |
| 14 | Pause/resume/cancel follow backend; restart recovery | ✅ | `test_ultron.py` (pause/resume, stop, emergency stop, survives restart, interrupted task re-runs), `test_missions.py` |
| 15 | No robot "working" without evidence | ✅ | `test_team_hq.py` (projection from persisted tasks only) |
| 16 | Solo: strategy + symbol + timeframe → real offline backtest | ✅ | `test_quantlab_vault.py` 01, 20; E2E *local data first* |
| 17 | Same dataset, engine, ledger in Solo and Team | ✅ | `test_team_quantlab.py::test_solo_and_team_share_one_engine_one_dataset_one_ledger` |
| 18 | Team can't touch Solo without a grant | ✅ | `test_team_quantlab.py` (grants, `TEAM_WORKSPACE`, `NOT_GRANTED`) |
| 19 | Shared research creates new versions, Solo not overwritten | ✅ | `test_team_quantlab.py` (CIPHER versions, return as new Solo version); E2E team step |
| 20 | Databento cost approval still required | ✅ | `test_quantlab_vault.py` 08/09, `test_quantlab_hub.py` (approval bound, capped, single use) |
| 21 | Data gap / ambiguous fill → warning, not profit | ✅ | `test_quantlab_vault.py` 06, 07, 12, 14 |
| 22 | Agent variants in history, holdout untouched | ✅ | `test_team_quantlab.py` (`lock_candidate`, sealed holdout); evolution tests; E2E evolution |
| 23 | Existing services keep passing | ✅ | pytest 596 → 625, vitest 40 → 47, E2E 31 → 34, nothing removed |
| 24 | No credentials in UI, logs, browser storage | ✅ | `test_quantlab_vault.py` 19, `test_api.py` secret-free settings, sandbox scrubbing; E2E storage scan |
| 25 | Data survives migration and rollback | ✅ | `test_database.py` (backup before migration, rollback rehearsal v16 → v19 → v16 → v19) |
| 26 | No live orders, messages or deletions without approval | ✅ | QuantLab is research-only (no order path exists); ULTRON policy tests (locked categories, approvals, sandbox) |

## 3. Demo scenarios (brief §15)

All five run inside the E2E suite (screenshots `docs/screenshots/jarvis2-*.png`):
A Home chat · B Solo backtest NQ 5m · C HQ with FORGE working and docking · D Team QuantLab
share → SENTINEL → CIPHER → Solo unchanged · E no AI route / backend crash.

## 4. Mocked, synthetic, not verified

- **Scripted AI**: Claude plan, ULTRON agents and CIPHER/Architect run against test scripts
  (labelled in the UI as *Scripted test model*). Real Claude behaviour is **not verified**.
- **Market data**: fixture/synthetic Databento-format data (labelled *Fixture data ·
  synthetic*). Real Databento downloads and costs are **not verified**; no validated edge
  is claimed anywhere.
- **Desktop control**: simulated backend on Linux; macOS app launching, Keychain, microphone,
  menu bar and MetaTrader 5 are **not verified here**.
- Display scaling (125/150 %), 1920×1080 and screen-reader output: **not run**.

## 5. Not built (open)

- Versioned prompt/skill **candidates with an approval gate** before activation (Training Hub
  brief §5): feedback and behaviour checks exist; nothing is changed automatically, but there
  is no candidate workflow yet.
- A shared **notification centre** (Home's "Needs you now" covers approvals and the active
  mission).
- First-run **onboarding** hints; strategy/mission **search** beyond the existing lists.
- UI language is **English** (no i18n in this scope).
- PRISM (product & UI design robot) is shown as *planned* — no backend capability.

## 6. Data safety and rollback

- Migrations 17–19 are additive (new tables only). Before any pending migration the database
  is copied with `VACUUM INTO` to `data/backups/jarvis-v<old>-<time>.db` (newest 5 kept).
- **Rollback**: quit JARVIS → replace `data/jarvis.db` with the newest
  `data/backups/jarvis-v16-*.db` (delete `jarvis.db-wal`/`-shm`) → check out `192bd1a` (or
  `git revert` the J2 commits) → start. Rehearsed in `test_database.py`.
- Browser storage holds only per-device conveniences (`jarvis.route.*`, `jarvis.interface`,
  `quantlab.mode`) — no keys, checked by the E2E run.
