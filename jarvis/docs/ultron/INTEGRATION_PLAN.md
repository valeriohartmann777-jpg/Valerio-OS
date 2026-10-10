# ULTRON — integration plan (gap analysis)

Spec: `docs/ultron-handoff/ULTRON_TEAM_MASTER_BLUEPRINT.md`. Scope of this build: **R0 + R1**
(truthful mission console; JARVIS planner, AXIOM, FORGE, SENTINEL; task DAG; worktrees;
approvals). The other six specialists are shown in the catalogue as *not active yet*.

## What JARVIS already has (reused)

| Blueprint need | Existing JARVIS piece | Use |
|---|---|---|
| UI shell, live stream | Electron + React + Zustand, WebSocket `/events`, `EventBus` → `EventStore` | ULTRON page; `ultron.*` events on the same bus |
| Durable state | aiosqlite + versioned `MIGRATIONS` (WAL) | migration 10: `ul_*` tables |
| Model adapter | `ChatModel` protocol, `AnthropicChatModel` (tool use, usage) | one adapter for every ULTRON agent |
| Prices / spend | `ModelPrice` (USD per million tokens) | per-run, per-task, per-mission cost |
| Brain tools | `ToolRegistry` + `ToolExecutor` + permission levels | `ultron_start_mission`, `ultron_status` |
| Approvals UI pattern | `PermissionService` (desktop actions) | ULTRON keeps its own durable approval ledger (missions outlive a chat turn) |
| Test fakes | `tests/fakes.py` `ScriptedChatModel` | scripted agent replies in tests |
| Desktop "Operator/Sentinel" | per-command agents in `agents/` | unchanged; ULTRON's roster is separate |

## Gaps (built here)

| Blueprint | Built as |
|---|---|
| Mission / Task / AgentRun / Artifact / Approval domain + state machine | `ultron/models.py`, `ultron/store.py` |
| JARVIS planner → charter + task DAG | `ultron/plan.py` (deterministic validation: DAG acyclic, owners active, every FORGE task reviewed by SENTINEL, scopes and checks valid) |
| Scheduler with dependencies, parallelism, bounded retries, checkpoint/resume | `ultron/service.py` (asyncio, SQLite as the queue; a Temporal migration is the documented trigger once missions run across machines) |
| Isolated workspaces | `ultron/workspace.py`: a git repo per mission (new sandbox project, or a worktree of the JARVIS repo on branch `ultron/<mission>`), a worktree + branch per task, runtime-owned commits and merges, a throwaway review checkout for SENTINEL |
| Isolated execution | `ultron/sandbox.py`: argv allowlist (no shell), scrubbed env, timeout, output cap; macOS Seatbelt profile (no network, writes only in the workspace); Linux network namespace (`unshare -rn`); reported honestly when unavailable |
| Tool broker + policy | `ultron/tools.py`, `ultron/policy.py`: per-agent tool manifests, path globs per task, audit of every call (allowed and denied) |
| Evidence | checks run by the runtime (not the agent's claim), artifacts with SHA-256 (spec, patch, test logs, review) |
| Approval gates | durable approvals with an effect signature; R1 effect: exporting a finished project out of the workspace. Locked categories listed and refused |
| Budgets / limits | mission spend cap (checked before every model call), max parallel workers, max attempts, max rounds, delegation depth 1 (agents can't spawn agents) |
| Controls | pause / resume / cancel per mission, PAUSE ALL / STOP ALL |
| Dashboard | ULTRON page: Overview, Mission Control (DAG), Agent Matrix, Projects, Knowledge, Activity, Permissions & Settings |

## Not in this build (honest)

- ATLAS, PRISM, CIPHER, ARCHIVE, VECTOR, OPERATOR as ULTRON workers (R2/R3).
- Network access for agents, dependency installation, merging into the running JARVIS
  checkout, deployments, external messages, money: locked.
- A real-model run in this development container: no Anthropic key here. Tests and the E2E
  use a visibly labelled scripted model; file writes, git, test runs, isolation, approvals and
  persistence are real.
