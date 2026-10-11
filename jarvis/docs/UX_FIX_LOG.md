# UX fix log — JARVIS 2.0

Each entry: audit id · change · evidence. Audit: `docs/UX_BUG_AUDIT.md`.

## J2-1 — daily-use blockers and false states (2026-10-11)

| Audit | Change | Evidence |
|---|---|---|
| A-01 | Bots: an idle lab whose setup checks fail says **"Setup needed"** (warning), not "Ready"; "Set up test terminal" explains that MetaTrader 5 must be installed first. | crawl `audit1` |
| A-03 | `Database._migrate` makes a consistent copy (`VACUUM INTO`, WAL included) to `data/backups/jarvis-v<old>-<time>.db` before any pending migration; the newest 5 are kept; a failed copy is logged and the additive migration continues. | `tests/test_database.py` (3 tests) |
| A-04 | ULTRON banner points to Settings → AI & Billing (Claude plan or approved API) instead of "add your Anthropic API key". | — |
| A-05 | `Button` takes a `reason` shown as tooltip and accessible description while disabled; disabled buttons keep pointer events so the tooltip can appear (they used `pointer-events: none`). Reasons added to all 19 buttons the crawler found (17 + "Save strategy", "Import & check"). | crawl `audit1`: 0 disabled buttons without a reason |
| A-09 | Top-bar chip says **"AI · not set up"** when neither the plan route nor paid fallback was ever turned on; a real pause still says "Paused". | `ai.test.ts` |
| A-10 | Learning budget text: counts paid API calls only (verified in `learning/service.py`: plan calls book $0). | code read |
| A-11 | "What time is it?", "Wie spät ist es?", "what's the date" are answered locally from the computer clock. | `test_router.py`; crawl: "It's 12:39 on Sunday, 11 October 2026." |

## J2-2 — new shell, Home, Settings (2026-10-11)

| Audit | Change | Evidence |
|---|---|---|
| A-02 | Home no longer shows the Phase-1 agent roster (deleted `AgentsPanel`); robot states come from one backend projection in Team ULTRON (J2-4). | E2E step 4 now checks the live mission step instead |
| A-06 | Home is chat first: the conversation fills the main panel with JARVIS's real state above it; focus (approval / active mission with its controls), area tiles with real status, tasks, activity and system context on the right; composer with Send button. | screenshot `audit2/chat.png` |
| A-07 | Hash routes for every page and section (`#/home`, `#/training/<s>`, `#/quantlab/<s>`, `#/ultron/<room>`, `#/settings/<group>`), history push/pop, reload keeps the page, last sub-section remembered per tab, Home on a fresh launch. | `routes.test.ts` (6) |
| A-08 | Exactly five top-level tabs: Home · Training Hub (Learning) · QuantLab (incl. MT5 Bots) · Team ULTRON · Settings. Old links `#/learning`, `#/bots`, `#/mission/<id>` redirect. Arrow keys move between tabs. | `routes.test.ts`; E2E 31/31 |
| A-12 | Body 14–16 px, labels ≥ 12 px, 2 px focus ring in the area colour, controls 36–48 px; Settings → Interface: reduced motion, high contrast, simple HQ list. | crawl `audit2`: 0 errors |
| A-13 | JARVIS 2.0 tokens (navy surfaces, area accents, yellow CTA), central icon map (`ui/icons.tsx`), shell primitives (`ui/shell.tsx`: PageHeader, Panel, StatusPill, WorkspaceModeBadge, Empty/Error/Loading states, Tile, SubNav, SafeErrorBoundary). | all text tokens ≥ 5:1 contrast (computed) |

## J2-3 — Training Hub and QuantLab Solo (2026-10-11)

- **Training Hub** (former Learning tab, `#/training/<section>`): Overview (what "learning"
  means here — research loop, the one small local model JARVIS trains, knowledge, feedback;
  explicitly *not* retraining Claude), Research loop, Trained model, Knowledge (memory),
  **Feedback**, **Evaluations**.
- **Feedback intake** (`backend/jarvis/feedback/service.py`, migration 18 `fb_feedback`,
  `GET/POST /feedback`, event `feedback.recorded`): 👍 / 👎 under every reply on Home, 👎 with an
  optional "what was wrong". Stored with the question, the intent and the route that answered
  (instant / reasoning / think); one rating per reply. Training Hub → Feedback lists them with
  totals per route. Nothing changes automatically: *Keep as correction* turns a 👎 note into a
  normal memory (visible and removable under Knowledge).
- **Behaviour evaluation** (`backend/jarvis/feedback/evals.py`, `config/evals.yaml`,
  migration 19 `ev_cases`/`ev_runs`, `/training/evals…`, event `evaluation.completed`): curated
  sentences with what JARVIS must understand (kind, tool, topic, apps) run against the local
  command router — offline, free, nothing is opened. *Pin as test case* on a 👍 reply keeps how
  it was understood as a regression case. Every run is stored; failures name the differing field.
- **Evaluations** also lists stored evidence from where it happened: trained-model walk-forward
  runs, research-loop counts (rejected / validated out-of-sample / confirmed), QuantLab run
  verdicts (synthetic data labelled), ULTRON SENTINEL reviews — each with a link to its source.
  No self-assigned scores.
- **QuantLab Solo**: Solo banner + workspace badge, MT5 Bots as a QuantLab section (old
  `#/bots` redirects), Backtest Lab with the three choices; the Run CTA is now the large
  "Run backtest" with a one-line summary of the material assumptions next to it (period,
  session, backtest/validation, costs, size, direction) — brief §6.1.
- Not built (documented in `JARVIS_2_STATUS.md`): versioned prompt/skill candidates with an
  approval gate before activation.
- Evidence: `tests/test_feedback.py` (5); E2E step "feedback" (👍 pinned and checked, 👎 note
  kept as correction and removed again).

## J2-4 — ULTRON HQ (2026-10-11)

- `GET /team/hq` (`backend/jarvis/team/hq.py`): nine robots and five rooms projected from
  persisted work — `ul_tasks` (software missions), `qm_tasks` (QuantLab research missions) and
  JARVIS's desktop agent registry (OPERATOR). States: working · waiting_approval · blocked ·
  failed · reserved · complete · idle · offline; finished work shows for 15 minutes, then the
  robot is docked. Offline only when every ability needs Claude and there is no AI route.
- Frontend: original SVG robots with per-robot antenna and emblem; eyes closed when docked,
  open while working (the only animation), badges for approval/blocked/failed/done; drawn
  base with five rooms (Homebase spans two rows), crew bar, room card, agent inspector
  (task, mission, room, last verified output, permissions, spend, how to assign — disabled
  with the reason when not possible). Settings → Interface "Simple list" replaces the drawing.
- Rooms change content: Workshop = the complete former Mission Control (overview, missions,
  projects, activity, permissions); Academy = agent matrix + evaluations; Research =
  knowledge + research missions; Homebase = approvals and recent missions.
- Evidence: `tests/test_team_hq.py` (10, incl. DB integration); E2E: robots docked before a
  mission, FORGE `working` in the Workshop during it, back at the dock with its verified
  output afterwards (31/31).

## J2-5 — ULTRON QuantLab: team workspaces on the Solo engine (2026-10-11)

- **One engine, one Data Vault, one registry, two workspaces.** `backend/jarvis/team/`
  (`store.py`, `workspace.py`), migration 17 (`tm_projects`, append-only `tm_audit` with
  no-update/no-delete triggers, `tm_tasks`). A team project holds a *copy* of one Solo version
  as its own strategy family; Solo never lists team families and refuses to run them
  (`TEAM_WORKSPACE`, 409); the team lists only its copy.
- **Share with ULTRON** is a deliberate owner action: pick a version, tick grants
  (`read_spec` · `run_backtests` · `propose_versions`), confirm. Grants are checked server-side
  per agent and task (`NEEDS`); changing them needs a confirmation and is audited.
- **Agent tasks:** SENTINEL *challenge* = the independent run audit (determinism, look-ahead,
  costs, second engine — reported as *not run* on bars > 1m, never as a pass). CIPHER *improve*
  = one bounded evolution round from the team's own completed validation run (≤ 3 variants,
  1 iteration), each variant a new team version with a trial; the holdout stays sealed.
  CIPHER needs an AI route and says so. Tasks show in the HQ (the robot works in the
  ULTRON QuantLab room, from `tm_tasks`).
- **Back to Solo** only by the owner, as a *new* Solo version (origin `team`); nothing in
  Solo is overwritten.
- **Bug fixed on the way:** SENTINEL's audit crashed (`UnboundLocalError`) on any run with
  bars > 1 minute (LD work) — the second-engine comparison is now reported as skipped.
- UI: Team ULTRON → ULTRON QuantLab room: members, share form, project view (access,
  the same Backtest Lab form scoped to `team:<id>`, ask SENTINEL/CIPHER with reasons when
  disabled, tasks, trials, versions with "Copy to Solo", results, audit trail).
- Evidence: `tests/test_team_quantlab.py` (5 — confirmation/copy, grants, identical ledger
  Solo vs team on the same dataset, CIPHER versions with Solo untouched, no AI route);
  E2E step "ULTRON QuantLab" (share → team backtest → SENTINEL → CIPHER → Solo unchanged,
  audit `share, backtest, ask, ask`): **32/32**.
