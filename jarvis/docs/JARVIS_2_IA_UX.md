# JARVIS 2.0 — information architecture and user journeys

Status: implemented on branch `claude/jarvis-foundation-mxnsz4` (J2-0 … J2-6, 2026-10-11).
What works, what is mocked and what is open: [`JARVIS_2_STATUS.md`](JARVIS_2_STATUS.md).

## 1. Five tabs, one address each

| Tab | Colour | Purpose | Default route |
|---|---|---|---|
| **Home** | cyan `#54CFFF` | Talk to JARVIS; what needs you now; tasks and activity | `#/home` |
| **Training Hub** | purple `#B29AFF` | What JARVIS learns, how it is checked, what it keeps | `#/training/overview` |
| **QuantLab** | mint `#42D7B6` | The owner's private (Solo) trading research | `#/quantlab/backtest-lab` |
| **Team ULTRON** | amber `#FFB45D` | The robot team: HQ with five rooms, missions, ULTRON QuantLab | `#/ultron/homebase` |
| **Settings** | `#BAC9DD` | AI & Billing, voice, permissions, briefing, interface … | `#/settings` |

Rules (`apps/desktop/src/lib/routes.ts`, `lib/history.ts`):

- Every page and sub-section has a hash route; reload keeps it; back/forward walk it.
- Clicking a tab opens the sub-section used last in that tab (remembered per tab in
  `localStorage` `jarvis.route.<tab>`); a fresh launch opens Home.
- JARVIS 1 links keep working: `#/learning` → Training Hub, `#/bots` → QuantLab › MT5 Bots,
  `#/mission/<id>` → Home › mission.
- Keyboard: ←/→ move along the tabs, Home/End jump, Enter opens. 2 px focus ring in the
  area colour.

### Route map

```
#/home                         #/home/mission/<id>
#/training/{overview|research|model|knowledge|feedback|evaluations}
#/quantlab/{backtest-lab|data-vault|idea-inbox|research-room|overview|strategy-studio|
            data-hub|validation|trade-explorer|experiments|risk-execution|reports|
            equity-lab-r1|mt5-bots}
#/ultron/{homebase|workshop|quantlab|research|academy}[/mission/<id>]
#/settings/<group>             (scrolls to the group: ai, voice, interface, morning-briefing …)
```

## 2. Pages

### Home — chat first
- **Core strip**: JARVIS's real state (idle, listening, thinking, acting, error) and headline.
- **Conversation** fills the main column; composer with Send and voice. Every reply has
  👍 / 👎 (👎 asks "what was wrong?", optional). Mission replies link to the mission.
- **Needs you now**: pending approvals and the active mission with pause/resume/stop.
- **Area tiles**: one per tab with its real status (Backtest Lab readiness, ULTRON counts …).
- Tasks, activity stream, system context.

### Training Hub — honest learning
| Section | What it shows | Source |
|---|---|---|
| Overview | What "learning" means here and what it doesn't (no retraining of Claude) | static truth + live states |
| Research loop | Claude forms hypotheses, JARVIS backtests them; only out-of-sample counts | `learning/` |
| Trained model | The one small local model JARVIS trains (S/R odds), judged walk-forward | `training/` |
| Knowledge | Memory: preferences, facts, corrections — add / remove | `memory/` |
| Feedback | Rated replies with question, route, note; totals per route; *Keep as correction* | `fb_feedback` |
| Evaluations | Behaviour check (curated + pinned sentences vs. local router, stored runs) and stored evidence: walk-forward runs, research counts, QuantLab verdicts, SENTINEL reviews | `ev_runs`, existing stores |

### QuantLab — Solo
- Solo banner + workspace badge: this is the owner's private space; agents have no access
  unless something is explicitly shared with Team ULTRON.
- **Backtest Lab** (default): saved strategy × symbol × timeframe → one large *Run backtest*;
  readiness from the local Data Vault; a one-line summary of the material assumptions next
  to Run; advanced settings collapsed.
- Data Vault, Idea Inbox → Research Room (Idea-to-Edge), Strategy Studio, Data Hub,
  validation, trade explorer, experiments, reports, Equity lab, MT5 Bots — unchanged
  functionality, modes *Simple / Research / Institutional* control how many sections show.

### Team ULTRON — the HQ
- **World**: five rooms drawn as a miniature base — 01 Homebase (docking, rest), 02 Workshop
  (code, systems), 03 ULTRON QuantLab (shared trading lab), 04 Research, 05 Academy (tests,
  reviews). Nine original robots stand where the backend says they are.
- **Crew bar** (accessible list of all nine with role and state) and **Inspector** (task,
  mission, room, last verified output, permissions, spend, how to assign — disabled with
  the reason when not possible). Settings → Interface *Simple list* replaces the drawing.
- **Rooms change content**: Homebase = approvals and recent missions; Workshop = the former
  Mission Control (overview, missions, projects, activity, permissions); ULTRON QuantLab =
  team workspaces; Research = knowledge and research missions; Academy = agent matrix and
  evaluations.

### Settings
Information architecture preserved (System, Permissions, Personality, Files, Known
applications, Brain, AI & Billing, Voice, Morning briefing, Always on) plus **Interface**
(reduced motion, high contrast, simple HQ list). Groups are cards with anchors.

## 3. Robot state machine

States: `offline · idle · reserved · working · waiting_approval · blocked · failed · complete`.
They are a **projection** of persisted work (`backend/jarvis/team/hq.py`, `GET /team/hq`):

| Source | What it contributes |
|---|---|
| `ul_tasks` | tasks of ULTRON software missions, by the agent that holds them (SENTINEL reviews → Academy, everything else → Workshop) |
| `qm_tasks` | tasks of QuantLab research missions (Idea-to-Edge, evolution — incl. CIPHER's team rounds) → ULTRON QuantLab room |
| `tm_tasks` | in-process team tasks (SENTINEL's challenge of a team run) → ULTRON QuantLab room |
| desktop agent registry | OPERATOR (computer automation for JARVIS's own commands) |

The nine robots: AXIOM (architecture), FORGE (software), ATLAS (research), PRISM (product &
UI — *planned*), CIPHER (quant, data & statistics), SENTINEL (verification, risk & security),
OPERATOR (computer & browser automation), ARCHIVE (memory & knowledge), VECTOR
(infrastructure & data).

Priority when a robot has several: working > waiting_approval > blocked > failed > reserved >
complete > idle > offline. Working / waiting / blocked / failed stand in the task's room;
everything else is docked at Homebase. Finished work shows as *complete* for 15 minutes.
*Offline* only when every ability of the robot needs Claude and no AI route is available.
PRISM is shown as *planned* (no backend capability yet) — never as working. Standing in a
room grants nothing: permissions come from the mission policy and team grants.

## 4. Solo ↔ Team (ULTRON QuantLab)

```
Solo strategy v3 ──(owner: Share with ULTRON, grants, confirm)──▶ team copy (new family)
        ▲                                                      │  same Data Vault, engine, registry
        │                                                      ├─ backtest (run_backtests)
        └──(owner: Copy to Solo, confirm → new Solo version)◀──┼─ SENTINEL challenge (read_spec)
                                                               └─ CIPHER improve → new team versions
                                                                   + trials (propose_versions)
```

- Grants: `read_spec`, `run_backtests`, `propose_versions`; checked server-side per agent and
  task; every change confirmed and written to the append-only audit (`tm_audit`).
- Solo never lists team families and refuses to run them (`TEAM_WORKSPACE`); the team sees
  only its copy. Same dataset + same spec ⇒ identical ledger in both (tested).
- Nothing in Solo is overwritten: results come back only as a new Solo version by the owner.

## 5. Journeys (demo scenarios A–E, all covered by the E2E run)

- **A — Personal Home**: launch → five tabs → Home → "What time is it?" → local answer with
  the computer clock → activity recorded → 👍.
- **B — Solo backtest**: QuantLab → Backtest Lab → saved strategy, NQ, 5m → readiness *Ready*
  from local data → Run backtest → trades, warnings, verdict; nothing downloaded.
- **C — ULTRON world**: Team ULTRON → robots docked → Workshop → start the test mission →
  FORGE *working* in the Workshop (SENTINEL in the Academy when reviewing) → back to the
  dock with its verified output.
- **D — Team QuantLab**: ULTRON QuantLab → share a Solo version with grants → team backtest →
  SENTINEL challenge → CIPHER proposes variants as new team versions → Solo unchanged.
- **E — Broken service**: no AI route → chip *AI · not set up*, Home stays usable, local
  commands and QuantLab work, CIPHER/AXIOM say why they can't start; backend crash → restarted
  by Electron, no fake "verified".
