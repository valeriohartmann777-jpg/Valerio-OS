# JARVIS 2.0 — release plan

Each milestone leaves the app runnable and is one commit (or a small series) on
`claude/jarvis-foundation-mxnsz4`; `192bd1a` is the rollback point before 2.0. Current state
of every item: [`JARVIS_2_STATUS.md`](JARVIS_2_STATUS.md).

## Shipped (2.0)

| Milestone | Scope | Exit check (met) |
|---|---|---|
| **J2-0 Discover & protect** | crawler over all pages/buttons/GET routes, inventory, defects A-01…A-13, data inventory, rollback point | audit doc; baseline check.sh + E2E 31/31 |
| **J2-1 Daily-use blockers** | truthful states (Bots, AI chip, ULTRON banner), reasons on every disabled button, backup before migrations, local time/date | E2E 31/31; crawl: 0 disabled buttons without reason |
| **J2-2 Shell, Home, Settings** | tokens, icons, five tabs, hash routes + legacy aliases, chat-first Home, Settings cards + Interface panel | E2E 31/31; `routes.test.ts` |
| **J2-3 Training Hub + Solo** | Learning → Training Hub (honest), feedback 👍/👎, behaviour evaluation, evidence list; Solo banner, MT5 Bots inside QuantLab, Run backtest + assumptions | `test_feedback.py`; E2E feedback step |
| **J2-4 ULTRON HQ** | five rooms, nine original robots, state projection from persisted tasks, Inspector, Mission Control inside the Workshop | `test_team_hq.py`; E2E HQ step |
| **J2-5 ULTRON QuantLab** | team workspaces on the Solo engine/vault/registry, grants, audit, SENTINEL challenge, CIPHER improve, copy back to Solo | `test_team_quantlab.py`; E2E team step |
| **J2-6 Readiness** | navigation/layout E2E (keyboard, deep link, reload, history, 1366×768, 1120×720, storage scan), settings-after-restart test, rollback rehearsal, top-bar fit at minimum width, docs | check.sh green; E2E 34/34 |

## Owner acceptance on the Mac (next, needs the owner)

1. Pull the branch, start the Mac app (it backs up `jarvis.db` before migrations 17–19).
2. Settings → AI & Billing: connect the Claude plan; Home: ask something open-ended.
3. QuantLab → Backtest Lab: a saved strategy, NQ, 5m on the real Data Vault → Run backtest.
4. Team ULTRON: start a small Workshop mission; watch FORGE leave and return to the dock.
5. ULTRON QuantLab: share one strategy with *read_spec* only → SENTINEL challenge.
6. Rate a few replies; Training Hub → Evaluations → Run check.

Anything that fails here goes into `UX_BUG_AUDIT.md` as a new defect before more features.

## Next milestones (proposed, not started)

| Milestone | Scope | Exit check |
|---|---|---|
| **2.1 Improvement candidates** | versioned prompt/skill candidates from feedback, run against the behaviour cases, activated only after owner approval; rollback per candidate | candidate can't activate without approval; eval run stored per candidate |
| **2.2 Accessibility pass** | automated axe audit in the E2E run, screen-reader walk-through on macOS (VoiceOver), DPI 125/150 % and 1920×1080 screenshots | 0 serious axe findings; manual VoiceOver notes |
| **2.3 Notifications & onboarding** | one notification centre for actionable items (approvals, failed runs, budget), dismissible first-run hints per tab | no duplicate toasts; hints dismissed persist |
| **2.4 Team ULTRON depth** | more team task kinds (ATLAS literature notes, ARCHIVE run summaries), PRISM only once a real capability exists | each new robot task has a backend test and an HQ state |

Out of scope until decided by the owner: live trading, i18n (German UI), new paid providers.
