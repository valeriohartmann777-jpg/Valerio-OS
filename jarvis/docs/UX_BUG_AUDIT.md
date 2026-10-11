# UX & function audit — JARVIS 2.0 (P0)

Started 2026-10-11 on branch `claude/jarvis-foundation-mxnsz4`.
**Rollback point:** commit `192bd1a` (last commit before the JARVIS 2.0 work). Every later
milestone is a separate commit on top; `git revert` of those commits restores the old UI.

## How this audit was made

- **Code inventory:** every page, section, API router and agent definition was read (not
  inferred from earlier prompts).
- **Crawler:** `tests/e2e/audit.mjs` starts the real Electron app as a *fresh Mac* sees it —
  no Claude Code, no API key, no Databento key — opens all 6 top-level pages and 22
  sub-sections, records renderer exceptions, console errors, backend requests answered
  with 4xx/5xx, visible error texts, disabled buttons without a reason and text under 11 px,
  sends three chat messages, and calls every GET route without path parameters.
- **Baseline at `192bd1a`:** `./scripts/check.sh` green (backend 596 passed, vitest 40,
  ruff, mypy ×3, typecheck); E2E 31/31.
- **Not reproducible here:** anything that needs the owner's Mac — the real Claude plan
  route, microphone and voice, MetaTrader 5, the Keychain, real Databento data, the menu bar.
  These are marked *unknown (Mac)*, not "working".

Crawler result in short: **no crashes, no console errors, no failed API calls** on any page;
57 GET routes answer 200, 4 answer 422 because they need query parameters (correct).
The defects are in status truthfulness, guidance, navigation and layout.

## Feature inventory (before)

| Area | Location | Intended behaviour | Status | Evidence |
|---|---|---|---|---|
| App start, backend supervisor, restart after crash | `electron/backend.ts` | Start/replace backend, show ONLINE | working | E2E steps 1–2, 30 |
| Chat / command bar | `CommandBar`, `Conversation` | Send text, rule actions, Claude reasoning | working; reasoning needs an AI route | E2E; crawl: "What time is it?" → "No AI route yet" |
| Desktop missions (Operator → Sentinel) | `missions/`, Home | Open apps with approval levels | working (simulated here) | E2E steps 4–8 |
| Approvals (level 2 / level 4) | `ApprovalCard` | Approve / two-step / reject | working | E2E |
| Memory, morning briefing | Home panel, Settings | Remember / forget; briefing time | working | E2E |
| Voice | Settings → Voice | ElevenLabs key, wake word, push-to-talk | working with bad-key refusal; real mic: unknown (Mac) | E2E |
| Learning loop | Learning | Self-directed research rounds | working (needs AI route) | E2E |
| Trained S/R model | Learning | Train, walk-forward verdict | working | E2E |
| Bot Lab (MT5 EAs) | Bots | Setup, backtest, improve, copy | working with fake MT5; real MT5: unknown (Mac) | E2E |
| QuantLab Backtest Lab / Data Vault | QuantLab | Strategy → market → timeframe, local data | working | 28 tests, E2E |
| QuantLab Idea-to-Edge, Research Room, Evolution | QuantLab | Source → blueprint → mission → verdict | working (scripted model) | E2E |
| QuantLab Studio, Data Hub, Validation, Explorer, Reports, R1 lab | QuantLab | Research terminal | working | E2E |
| ULTRON missions (JARVIS, AXIOM, FORGE, SENTINEL) | ULTRON | Plan, code in worktree, verify, export | working (scripted model) | E2E |
| ULTRON agents ATLAS, CIPHER, ARCHIVE, VECTOR | ULTRON / QuantLab missions | Work inside QuantLab research missions | working there only | tests |
| ULTRON agents PRISM, OPERATOR | ULTRON roster | Planned (R2/R3) | not active in ULTRON; OPERATOR exists as JARVIS's desktop agent | `ultron/agents.py` |
| AI & Billing | Settings | Plan → API → local / pause | working | E2E, 34 router tests |
| In-app updates | TopBar, Settings | Fast-forward + staged build | working | E2E |
| Navigation | `TopBar` | 6 top-level pages + transient "Mission" tab | working, but no URL, no deep links | crawl |

## Defects

Severity: **S1** blocks daily use or shows a false state · **S2** misleads or makes a task
hard · **S3** polish.

| # | Sev | Where | Defect | Root cause | Plan |
|---|---|---|---|---|---|
| A-01 | S1 | Bots | Header says **"Ready"** while MetaTrader, Wine, MQL5 folder and test terminal are all missing. | `STATE.idle` is labelled "Ready" regardless of `lab.ready`. | Label "Setup needed" (warning) when `!lab.ready`. |
| A-02 | S1 | Home → Agents panel | Shows an old phase roster ("2/6 online", **FORGE · Phase 9**, **Vision · Phase 5**) that contradicts ULTRON, where FORGE is active and Vision doesn't exist. | Panel reads the Phase-1 desktop agent catalog (`agents/catalog.py`), not ULTRON. | New Home without that panel; one agent projection for the robots (ULTRON roster + JARVIS's desktop OPERATOR). |
| A-03 | S1 | Data safety | Database migrations run at start **without any backup**. | `Database._migrate` executes pending migrations directly. | Consistent snapshot (`sqlite3` backup API) of `jarvis.db` before any pending migration; keep the last 5. |
| A-04 | S2 | ULTRON banner | "Add your Anthropic API key to start missions" — outdated since D-030 (Claude plan first). | Text predates the AI router. | Point to Settings → AI & Billing (plan or approved API). |
| A-05 | S2 | 17 buttons | Disabled **without a reason**: Start mission, Backtest, Get free quote, Connect & Verify, Draft rules with JARVIS, Ask (×2), Add text, Set up test terminal, Sign in with Claude…, Use my Claude plan, Approve paid fallback, Save focus, Back up, Read, Connect (×2). | `disabled` without `title`/hint. | Every disabled primary action gets a visible or tooltip reason. |
| A-06 | S2 | Home | The chat is squeezed under the core and the mission card; at 1440×900 the newest reply is cut off. 3 columns of telemetry compete with it. | Layout from the Phase-1 "command center". | Home = chat first (spec §4); telemetry moves to a compact status strip / Settings. |
| A-07 | S2 | Navigation | No URL: reload always lands on Home, no back/forward, no deep link into QuantLab or ULTRON sections. Section choice is lost when switching tabs. | View is plain component state. | Hash routes (`#/quantlab/backtest`, `#/ultron/quantlab`, …) with history, legacy aliases (`learning`, `bots`), remembered sub-tab. |
| A-08 | S2 | Navigation | 6 top-level tabs plus a transient "Mission" tab; brief requires exactly 5. | Pages added over time. | Home · Training Hub · QuantLab (Bots inside) · Team ULTRON · Settings; Mission detail opens inside Home. |
| A-09 | S2 | Top bar | Fresh install shows **"AI · PAUSED"** in red, which reads like a failure although nothing was set up yet. | Router state `paused` covers both "nothing configured" and "limit reached". | Show "AI · not set up" when no route was ever configured; keep "Paused" for real pauses. |
| A-10 | S3 | Learning | "Hard limit per day, from the API's own usage" — unclear after D-030 (plan calls aren't billed). | Text predates the router. | "Counts paid API calls; Claude-plan calls don't use it." |
| A-11 | S3 | Chat without AI | "What time is it?" → "I can't reason about that yet." Simple local questions need Claude. | No local rule for time/date. | Local answer for time and date. |
| A-12 | S3 | Accessibility | Research Room uses text below 11 px (10 elements); many labels are 11 px uppercase; focus ring is a 1 px faint outline. | Dense terminal styling. | Tokens: body 14–16 px, labels ≥ 12 px, 2 px focus ring. |
| A-13 | S3 | Visual system | One near-black palette, no icons, accent colours not tied to areas. | Phase-1 design. | JARVIS 2.0 tokens (navy, area accents), central icon map. |

Crawler notes: "Strategies"/"Datasets" crawl timeouts were labels from a nested nav inside a
QuantLab section, not a product defect.

## Data and settings inventory (what must survive)

- `data/jarvis.db` (SQLite, schema v16): conversation, missions, approvals, memory, learning,
  training, bots, QuantLab (`qh_*`, `qr_*`, `qs_*`, `qm_*`, `qv_*`), ULTRON (`ul_*`), AI router.
- `data/quantlab/` (raw DBN, canonical Parquet, snapshots, derived bars, runs, intake),
  `data/ultron/` (worktrees, artifacts), `data/logs/`.
- `config/*.yaml` (settings, permissions, models), OS keystore entries (Claude API key,
  Databento, ElevenLabs) — never read or moved by the redesign.
- Browser storage: only `quantlab.mode` (per-viewer convenience).

Migration rule for JARVIS 2.0: additive schema changes only, automatic backup before they
run (A-03), no reset or drop of any table.
