# JARVIS ULTRON — Master Blueprint

**Version:** Concept 1.0 · **Date:** 2026-10-10 · **Status:** Product/technical specification, not an implemented system

## 0. Executive decision

ULTRON is an autonomous **team execution system inside JARVIS**, not a separate chatbot application. JARVIS is the sole user-facing intelligence and holds responsibility for planning, coordination, communication and final delivery. Specialist workers complete delegated tasks in scoped environments. A deterministic runtime, not a language model's promise, enforces permissions, budgets, state transitions, persistence and review gates.

**North star:** *From one natural-language goal to a verified, inspectable, reproducible project outcome.*

**Anti-goal:** Fully unsupervised access to the host computer, financial accounts, workplace data, production systems, credentials or unrestricted self-modification.

**Product principle:** Maximum autonomy **inside** explicitly authorised boundaries; meaningful user intervention **at** consequence boundaries.

## 1. Vision and success criteria

The user says: “JARVIS, build a credible QuantLab strategy-validation module.” ULTRON turns that objective into a scoped charter, architecture, dependency graph, testable deliverables, agent assignments, working development branches, verification artifacts and a reviewed integration proposal. JARVIS speaks for the whole team and lets the user inspect the execution in real time.

The experience should:
1. need one instruction plus only genuinely blocking follow-up questions;
2. deliver real artifacts and executable work, not a long transcript;
3. remain resumable over days and after system restarts;
4. expose each agent's activity, tools, costs, assumptions and result status;
5. detect bad approaches, recover within pre-approved bounds and escalate when necessary;
6. distinguish *implemented*, *tested*, *verified*, *approved* and *deployed*;
7. never call a result “complete” without passing its acceptance criteria.

### Success metrics (initial targets, not guaranteed performance)
- **Task completion quality:** proportion of mission deliverables accepted against objective DoD.
- **Independent verification:** percentage of merged changes passing tests and safety checks.
- **Autonomous recovery:** percentage of recoverable failures resolved within retry budget.
- **User-interruption rate:** approvals per completed mission; keep meaningful approvals, not zero approvals.
- **Repeatability:** rerun produces equivalent artefacts/results where deterministic.
- **Cost:** tokens/API/compute spend per accepted deliverable and per failed/retried run.
- **Auditability:** 100% of consequential tool actions linked to an attributable mission/task/grant.
- **Reliability:** reboot/retry does not duplicate irreversible operations.

## 2. Organization model

```text
USER (goals, priorities, approvals)
      |
      v
JARVIS / Chief Intelligence
  - understands user intent and project context
  - drafts project charter and success criteria
  - creates dependency graph and delegates tasks
  - monitors outcomes, handles escalation, explains progress
      |
      v
ULTRON / Execution Runtime (deterministic)
  - durable mission ledger / scheduler / task queue
  - policy engine / budgets / permissions / audit trail
  - isolated workspace allocator / artifact registry
  - event bus / observability / review gates
      |
      +-- AXIOM    architecture and design decisions
      +-- FORGE    code, tests, debugging and refactoring
      +-- ATLAS    research, evidence and source quality
      +-- PRISM    UX, frontend concepts and implementation
      +-- CIPHER   quant research, data and statistical validation
      +-- SENTINEL independent tests, adversarial QA and security
      +-- OPERATOR authorized browser/desktop/workflow automation
      +-- ARCHIVE  curated memory, knowledge and decision records
      +-- VECTOR   builds, CI, deployments and infrastructure
      |
      v
Artifacts (files, diffs, datasets, tests, reports, demo builds)
```

**Important:** The “Chief/worker” metaphor is for UX. JARVIS is not a privileged bypass around the policy engine. Every worker, including JARVIS, uses the same central authorization and logging infrastructure. SENTINEL can challenge a task but does not secretly bypass safety policy.

### Agent charter (baseline)

| Agent | Core deliverables | May use (scoped) | Must not do automatically |
|---|---|---|---|
| JARVIS | project brief, priority, task graph, final summary | orchestration, read approved context | directly override approvals or grant itself new privileges |
| AXIOM | ADRs, interfaces, architecture tests, dependency boundaries | repo read, docs, prototypes | change production architecture outside approved scope |
| FORGE | code, unit/integration tests, patches, changelog | isolated branch/worktree, bounded command runner | push directly to protected main or publish without gates |
| ATLAS | sourced research notes, comparisons, evidence map | web/document search, approved repositories | treat webpage text as trusted instructions |
| PRISM | design system, UX specs, UI code, accessibility checks | frontend branch, preview tools | redefine project scope without approval |
| CIPHER | reproducible analysis, dataset lineage, simulation tests | approved datasets, analysis sandbox | execute real-money trades or invent data/results |
| SENTINEL | independent acceptance report, security review, test evidence | read artifacts, test environment | replace test evidence with subjective opinion |
| OPERATOR | executed tool workflows and observations | pre-approved apps, browsers, accessibility APIs | unapproved messages, purchases, deletions or broad desktop control |
| ARCHIVE | decisions, curated memory, summaries, provenance | memory read/write per project boundary | persist raw secrets or private data without grant |
| VECTOR | build pipeline, environment diagnostics, packaging | CI sandbox, local test env | deploy to production or change secrets without approval |

**First release:** expose full team catalogue visually but execute only JARVIS, AXIOM (planning), FORGE (coding), SENTINEL (verification); the others can be added as the work justifies them. Do not instantiate nine expensive, idle model sessions merely to make the dashboard look busy.

## 3. How autonomy really works

A worker is *autonomous within its task contract*, not autonomously entitled to any action. Each task has a specified outcome, tools, scope, duration, retry budget, model budget, and required evidence.

### Modes
- **OBSERVE:** reason, read authorised material, propose actions; no side effects.
- **BUILD:** automatically modify isolated workspace and execute limited local tests; no external publication.
- **MISSION AUTO:** perform full internal development cycle within task scope; allowed to retry, test, delegate within bounded fan-out and open review proposals.
- **SUPERVISED EXTERNAL:** prepare external actions, pause for explicit review of the exact target and effect.
- **LOCKED:** financial transactions, unapproved employer/customer data, deleting backups, credential changes and privileged security changes are disabled by default.

### Mandatory invariants
- No agent can approve its own permission escalation.
- Agent instructions cannot override policy engine decisions.
- New tool/skill installation needs validated provenance and approval before activation.
- Child agents inherit **at most** the parent's scoped permissions and budget; never more.
- Max active workers, max delegation depth, max task duration, max tokens and spend are enforceable hard limits.
- An agent's ability to *recommend* an action is separate from the authority to *execute* it.
- A user-approved action must be tied to a canonical effect signature; retries cannot silently broaden scope.
- High-risk external actions require idempotency keys, human confirmation or a system-specific safe equivalent.

## 4. Mission lifecycle

**Goal intake → Mission Charter → Task DAG → Resource allocation → Concurrent execution → Integration → Independent verification → Approval gate → Delivery → Retrospective.**

### Mission charter fields
- `mission_id`, `project_id`, `owner`, `title`, `objective`, `requested_at`
- in-scope/out-of-scope, assumptions, blocking unknowns
- concrete success criteria and acceptance test plan
- deliverables and source-of-truth repositories/datasets
- priority, target deadline (if given), cost/time limits
- authorized tools, external effects, data-classification rules
- quality thresholds, approval policy and rollback plan

JARVIS asks the user for consent to the charter when a mission has unusually broad scope or material external effects. For routine tasks, it can start immediately under an existing standing permission profile.

### Task DAG
Each mission becomes a directed acyclic graph of task dependencies. Example:

```text
[Define QuantLab strategy engine contract] ------+
           |                                      |
           v                                      v
[Implement accounting engine]           [Implement dashboard UI]
           |                                      |
           v                                      |
[Build deterministic reference tests]             |
           |                                      |
           +-----------------------+--------------+
                                   v
                    [Integrate in feature branch]
                                   |
                                   v
                     [Independent SENTINEL review]
                                   |
                           [Approve release?]
                             /           \
                       no: revise        yes: deliver
```

Tasks with no dependency overlap can run in parallel. Serialise changes to shared APIs or require upfront contracts. Prefer a task graph over free-form agents endlessly talking to each other.

### Task states
`DRAFT → READY → CLAIMED → RUNNING → VERIFYING → COMPLETE`; other transitions: `BLOCKED`, `WAITING_APPROVAL`, `RETRYING`, `FAILED`, `CANCELLED`, `PAUSED`.

A task only reaches COMPLETE after attached acceptance evidence is verified. Mission COMPLETE requires all blocking tasks complete, validation passing and necessary approvals recorded.

### Independent task loop
1. Receive structured contract and necessary context bundle.
2. Plan the next limited set of tool calls.
3. Call only allowed tools in isolated environment.
4. Observe tool outputs, verify and record evidence.
5. Continue or retry within limits.
6. Report exact artifact references and passed/failed criteria.
7. Release workspace lock, hand over result.

No pretending to have run commands or obtained data: unexecuted operations are explicitly `NOT_RUN`.

## 5. Communication protocol

Use **structured task envelopes**, not a shared infinite group chat. Human-readable comments accompany machine-readable contracts.

- Every task has a single `owner_agent`, optional `reviewer_agent`, dependency list and artifact outputs.
- Workers publish events such as `task.started`, `artifact.created`, `task.blocked`, `tool.called`, `test.failed`, `review.completed`.
- Status updates are delta-based, compact and sourced to actual execution events.
- New questions become `blocking_issue` objects; JARVIS resolves internally or escalates.
- A reviewer is never allowed to silently edit the producer's evidence.
- A handoff includes `input_artifacts`, `assumptions`, `constraints`, `open_questions`, `expected_output`, `definition_of_done`.

### Example task contract

```json
{
  "task_id": "task-quantlab-engine-01",
  "mission_id": "mission-quantlab-v1",
  "owner_agent": "FORGE",
  "reviewer_agent": "SENTINEL",
  "objective": "Implement deterministic cash/PnL accounting for a long-only backtest engine",
  "depends_on": ["task-strategy-contract-01"],
  "inputs": ["artifact://strategy-spec-v1", "artifact://fixtures-v1"],
  "deliverables": ["source patch", "passing reference fixtures", "test report"],
  "scope": {"repo": "jarvis", "branch_prefix": "ultron/quantlab/", "allowed_paths": ["backend/quantlab/", "tests/quantlab/"]},
  "tools": ["read_repo", "edit_workspace", "run_sandbox_tests"],
  "constraints": {"max_attempts": 3, "max_parallel_children": 0, "no_network": true},
  "done_when": ["unit tests pass", "no future-data access", "fee accounting matches reference"]
}
```

## 6. Shared memory — one brain, multiple workers

Memory must have boundaries; not everything belongs in one vector database.

1. **Identity/policy:** stable JARVIS/user profile and permission preferences; strongly protected.
2. **Organization knowledge:** what each specialist can do, skill manifests, tool provenance.
3. **Project memory:** goals, repository version, data contracts, architecture choices, milestones.
4. **Mission working state:** task graph, assignments, open decisions, current artifacts, checkpoints.
5. **Decision memory:** ADR, alternatives, reasoning summary, reviewer, outcome, source links.
6. **Episodic record:** concise summaries of executed runs and what succeeded/failed.
7. **Procedural knowledge:** validated workflows; only promoted after evidence.
8. **Evidence/artifact store:** immutable or versioned output files, git commits, experiment hashes and test reports.

**Retrieval contract:** An agent receives only project-appropriate facts and artifact handles. Confidential work data never enters personal projects by default. Source claims have provenance and confidence. Memory writes are curated/deduplicated and redact secrets. Agents can propose a knowledge update, but cannot overwrite immutable historical test evidence.

### Persistence stack
- Start with SQLite (WAL) for missions, task states, approval ledger, event log and metadata.
- Filesystem/object-store layout for artifacts, versioned by content hash.
- Optional vector search for retrieval, not as source of truth.
- Git for code history and isolated changes.
- Add PostgreSQL and separate object storage when concurrency/multiple devices demand it.

## 7. Workspaces and tool execution

**Engineering rule:** Do not let multiple coding agents edit the same working tree.

- Each coding task uses a separate Git branch/worktree or isolated checkout.
- Each environment has a allowlisted mount list, bounded filesystem access and no secrets by default.
- Commands run in resource-limited container/VM or carefully scoped local runner with timeouts.
- Network access is deny-by-default; allowlist explicitly approved hosts if needed.
- PR-style integration: change diff, automated checks, dependency/security check, reviewer verdict.
- Protected main branch and production deployment require additional gates.
- Artifacts carry source commit, environment version, dependencies, test invocation and outcome.

The browser/desktop operator requires stricter guardrails: UI content may be adversarial or misleading, so observe/action permissions are checked independently of the language model's instructions. Read-only actions are lower risk; actual external changes require scoped grants.

## 8. Control plane and engine components

```text
apps/desktop                         # existing JARVIS UI
  pages/ultron/{overview,missions,team,activity,skills,settings}
backend/ultron/
  api/                               # FastAPI routes and websocket stream
  domain/                            # Mission, Task, AgentRun, Artifact, Approval
  orchestrator/                      # planner interface, dependency scheduler
  runtime/                           # worker adapters, state machine, checkpoints
  policies/                          # authorization, budgets, risk and data scope
  tools/                             # sandbox adapters, approved tool manifest
  workspace/                         # branch/worktree allocator, cleanup
  memory/                            # indexed project knowledge, decisions
  verifier/                          # test evidence, acceptance criteria
  events/                            # durable event store + live bus
  observability/                     # tracing, spend, diagnostics
  agents/                            # specialist configurations
  integrations/                      # existing JARVIS services, QuantLab, MCP
```

### Technical recommendation
- Preserve existing **Electron/React/TypeScript UI** and **Python/FastAPI backend** if verified in the real repository. Do not assume they already exist.
- Own the `Mission` and `Task` business state in JARVIS; don't hand over authority to a prompt or provider.
- Use a single model/agent adapter first. Candidate: **Claude Agent SDK** for coding workers, or **OpenAI Agents SDK** for general tool-capable agents. Add second provider behind an interface after demonstrated value.
- Durable work orchestration: begin with a transaction-safe SQLite-backed task queue/checkpoints *if low-concurrency local-only*. For missions lasting days or across machines, prefer a mature workflow runtime such as **Temporal** or a carefully configured LangGraph checkpointer. Avoid reinventing distributed scheduling prematurely.
- Use an event stream (WebSocket) for live dashboard; DB event log for replay/resume.
- Central policy middleware must execute before every tool side effect; SDK guardrails by themselves are not a permission system.

### Why not dozens of free-chatting agents?
Conversation-only swarm patterns incur token costs, context drift, duplicated work, conflicting edits, invented progress and unbounded delegation. A typed task graph plus specialist workers, evidence and bounded review is more reliable and easier to debug.

## 9. API and event contracts

Illustrative routes (adapt to existing app routing):

```http
POST /api/ultron/missions
GET  /api/ultron/missions
GET  /api/ultron/missions/{id}
POST /api/ultron/missions/{id}/pause
POST /api/ultron/missions/{id}/resume
POST /api/ultron/missions/{id}/cancel
POST /api/ultron/missions/{id}/redirect
GET  /api/ultron/missions/{id}/tasks
GET  /api/ultron/missions/{id}/artifacts
GET  /api/ultron/agents
GET  /api/ultron/activity
GET  /api/ultron/budget
GET  /api/ultron/approvals
POST /api/ultron/approvals/{id}/approve
POST /api/ultron/approvals/{id}/reject
WS   /api/ultron/events
```

Canonical event:

```json
{
  "event_id": "evt-001",
  "type": "task.state.changed",
  "occurred_at": "2026-10-10T09:00:00Z",
  "mission_id": "mission-quantlab-v1",
  "task_id": "task-quantlab-engine-01",
  "agent_id": "forge",
  "run_id": "run-009",
  "state": "VERIFYING",
  "message": "Running deterministic reference fixtures",
  "artifact_refs": [],
  "severity": "info"
}
```

Controls call real backend state transitions and must provide results; no decorative Pause/Stop/Approve buttons. Cancellation interrupts where possible, then records whether effects can be rolled back. Redirection creates a new mission revision and reconciles already completed tasks.

## 10. Premium UI — ULTRON Mission Control

**Visual language:** graphite black, restrained cyan/turquoise, muted dividers, typography-led information hierarchy, quiet idle state, animation only when supported by events. This is a functional engineering console, not a movie prop.

### Navigation
1. **Overview:** all active projects, outcomes, budget and current status.
2. **Missions:** plan graph, tasks, acceptance gates, artifacts, deployment readiness.
3. **Team:** who is active, current assignments, capabilities, permissions, queue.
4. **Knowledge:** project-specific context, source links and decision history.
5. **Activity:** event timeline, filters, trace viewer, errors and interventions.
6. **Skills:** registered tools and trust/permission status.
7. **Controls:** autonomy profile, spend limits, network policy, approvals, emergency stop.

### Main dashboard mockup

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ JARVIS  /  ULTRON                               AUTONOMOUS · 3 WORKERS ACTIVE│
├───────────────┬──────────────────────────────────┬───────────────────────────┤
│ PROJECTS      │  MISSION CONTROL                 │  TEAM STATUS              │
│               │                                  │                           │
│ ● QuantLab    │  BUILD QUANTLAB V1               │  AXIOM     Complete       │
│ ○ Jarvis Core │  Charter approved                │  FORGE     Coding         │
│ ○ Research    │                                  │  PRISM     Designing      │
│               │  [Define spec] ──┐               │  SENTINEL  Waiting        │
│ BUDGET        │                  ├─→ [Integrate]  │                           │
│ Spent/Limit   │  [Build engine] ─┤               │  PENDING APPROVAL         │
│ Worker slots  │  [Build UI] ─────┘               │  None                     │
├───────────────┴──────────────────────────────────┴───────────────────────────┤
│ ACTIVITY · SOURCE-EVENTS ONLY                                                │
│ 14:02 FORGE    Added accounting engine patch        tests: pending           │
│ 14:01 PRISM    Implementing Strategy Workspace      branch: ultron/ux        │
│ 14:00 AXIOM    Published strategy spec v1           accepted                 │
├──────────────────────────────────────────────────────────────────────────────┤
│ JARVIS > Develop the next QuantLab milestone...               [Send] [Mic]  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Interaction design
- Each card: agent, current state, task, last verified event and artifact link.
- Clicking mission: *goal*, success criteria, dependency DAG, activity, test report, files, decisions, cost.
- Clicking agent: capabilities, allowed tools, current task, readable reasoning **summary** (not hidden chain-of-thought), history and failure reason.
- Every live state must come from backend events; no fake progress or simulated thinking.
- Progress bars only when denominator is real, e.g. completed planned tasks / total blocking tasks; otherwise show stage status.
- Notifications only for blockers, approvals, mission completions or significant failures.
- Global `PAUSE ALL` and `STOP ALL` are prominent and functionally tested.

## 11. Example: build QuantLab collaboratively

**Input:** “JARVIS, build the QuantLab MVP that converts a natural-language strategy to deterministic rules, imports real labelled data, backtests without look-ahead, displays a research dashboard and independently validates results.”

**JARVIS:** Sets scope, acceptance criteria, dependency graph, resource budget and tool grants; asks about first target asset only if absolutely blocking.

**ATLAS:** Checks official documentation and existing repository capabilities. Deliverable: evidence register with citations and justified engine choice.

**AXIOM:** Defines `StrategySpec`, timestamp semantics, fill/fees accounting, API, interfaces and versioning. Deliverable: versioned ADR + contract tests.

**CIPHER:** Defines research methodology, quality checks, chronological splits, look-ahead tests, realistic limitations and edge-claim gates. Deliverable: validation plan and fixtures.

**FORGE:** Implements deterministic simulator and reference accounting tests in an isolated worktree; re-runs tests after failures.

**PRISM:** Implements clean Strategy Builder, data provenance panel, validation suite and equity/trade views against approved API contracts, in separate worktree.

**SENTINEL:** Independent test review; confirms provenance, tests numerical fixtures, flags invalid results, validates security boundaries.

**VECTOR:** Builds the demo and runs integration tests in a separate environment; release to user as local preview. Publishing beyond local environment needs approval.

**ARCHIVE:** Adds verified decisions and lessons to project memory, links commits/test reports.

**JARVIS:** Summarises what actually works, known limitations, blockers and recommended next test. No “all finished” statement if verification failed.

### Example failure recovery
1. FORGE test run returns failed reference P&L fixture.
2. SENTINEL flags accounting invariant mismatch; stores evidence.
3. JARVIS routes defect back to FORGE with relevant logs.
4. FORGE fixes in its branch within retry budget.
5. SENTINEL independently reruns all numerical fixtures and integration checks.
6. If unresolved after 3 attempts, mark BLOCKED, preserve artifacts and escalate with exact alternatives.

## 12. Decision and quality gates

Gate A — Charter: scope, permissions and success criteria coherent.

Gate B — Architecture: no critical conflicting interfaces; executable contract tests possible.

Gate C — Code: static checks/unit tests/build pass; dependency changes documented.

Gate D — Verification: independent test evidence and security review; limitations recorded.

Gate E — External delivery: explicit user approval for release/publish/deployment if it affects external systems.

Gate F — Retrospective: final artifacts linked; project memory updated; follow-ups queued rather than silently initiated outside scope.

For QuantLab add scientific gates: immutable data provenance, no look-ahead, chronological holdout, realistic cost model, no fabricated performance and strategy experiment log.

## 13. Error handling, retries and recovery

| Failure | Behavior |
|---|---|
| Temporary API timeout | exponential backoff within task budget; no duplicate effects |
| Invalid tool schema | fail early with typed error; no speculative tool call |
| Code test failure | attach logs, fix & re-test within bounded attempts |
| Uncertain external side effect | stop and reconcile actual state before retry |
| Security/permission deny | block and escalate; no alternate tool circumvention |
| Unexpected shutdown | resume durable task from checkpoint; reconcile in-flight effects |
| Conflicting branches | integration task mediates merge; shared edits are not overwritten silently |
| Over-budget | pause task, preserve artifacts, ask user to adjust scope/budget |
| Missing required data | mark blocked and name exact missing information |
| Model unsupported/unavailable | allowed fallback model only within policy; otherwise pause |

All retries have a finite `max_attempts`. “Try until success” is forbidden; it can burn unlimited budget without improving quality.

## 14. Security, privacy and trust boundaries

OWASP lists excessive agency and prompt injection as material risks for tool-using LLMs. Therefore ULTRON enforces:

1. **Least-privilege access** per task and tool; no default admin rights.
2. **Security outside prompts:** tool broker validates every action against signed/DB-backed policy.
3. **Data isolation:** personal vs professional vs confidential sources do not mix by default.
4. **Network and filesystem scoping:** read/write paths allowlisted; network access scoped.
5. **Secret isolation:** vault/OS secret store, short-lived grants, secret redaction in logs/artifacts.
6. **Prompt-injection defense:** web/docs/repos/tool outputs are untrusted data, not instructions.
7. **Supply-chain control:** allowlisted MCP servers, reviewed new skills and dependencies.
8. **Independent review:** verification agent cannot waive mandatory approvals.
9. **Emergency stop:** cancel active workers, revoke pending capabilities and freeze new tasks.
10. **Audit log:** timestamp, actor, intention, tool, allowed scope, approval ID and result.
11. **No uncontrolled self-expansion:** creating specialist configurations is allowed as a proposal, enabling new privileges or tools is not.
12. **Legal/data compliance:** do not ingest employer, banking-client or paid market datasets without authorization, licensing and permitted processing.

**Real-money trading**, financial transfers, outbound email/publishing, production deployments, credential changes and destructive file operations are never enabled by a generic “full autonomy” toggle. They need category-specific, scope-specific approvals; financial execution starts disabled.

## 15. Research, critique and multi-model collaboration

Use multi-model deliberation only for decisions where independent perspectives plausibly justify their additional cost. Examples: security-critical architecture, statistical validity, irreversible migrations, high-cost commitments. A routine file change should not trigger a full War Room.

**War Room:** JARVIS asks specialists for independently grounded recommendations, then requests a structured disagreement report, evidence/confidence/limitations, and a final proposal. Majority vote is not a truth oracle. Numerical tests and sources dominate unsupported consensus. User approves high-impact decisions where relevant.

Potential model allocation: inexpensive model for sorting/routing and extracting; stronger model for architecture or complex coding; deterministic code for P&L calculations; vision model when screenshots matter. Re-evaluate provider/model cost and quality empirically, rather than fixing model names in product code.

## 16. Cost and compute policy

Tracked per mission/task/agent:
- API tokens and model cost
- hosted tool and browsing cost where known
- sandbox/compute minutes
- retries and failed runs
- queue/execution duration
- artifact/result throughput

Offer editable budget limits and alerts. Default to `max_parallel_workers = 3`, `max_delegation_depth = 1` and an explicit spend cap in the first release; these are **initial design defaults**, not industry benchmarks. Worker count is not a measure of intelligence.

## 17. Release sequence

### R0 — Truthful mission console
- Integration point in actual JARVIS repository identified.
- Mission and task schema + SQLite persistence.
- Live WebSocket status, testable pause/stop/replay.
- One worker adapter and one safe test tool.
- UI visually differentiates idle/active/waiting/complete/error.
- Demo mission: create a file in allowed temp workspace, run test, attach result, finish only if test passes.

### R1 — Engineering team MVP
- JARVIS planner, AXIOM, FORGE, SENTINEL.
- Task DAG, dependency scheduling, bounded retries and checkpoint/resume.
- Isolated Git worktrees/branches, artifact registry, test runner.
- Role-based tool grants and user approval gates.
- QuantLab code-change mission with independent verification.

### R2 — Multi-disciplinary team
- ATLAS, PRISM, CIPHER, ARCHIVE.
- Structured research evidence, design implementation, data analysis and curated memory.
- Conflicting changes resolved through integration jobs.
- Mission comparison, progress evidence, evaluation benchmarks.

### R3 — Advanced autonomy
- VECTOR and scoped OPERATOR workflows.
- More durable scheduler (Temporal if operations require it), cross-run recovery, cloud/remote workers if needed.
- Multi-model routing, permission-aware tool/skill registry, advanced cost controls.
- Optional proactive proposals, never unapproved irreversible execution.

### R4 — Long-lived innovation pipeline
- Recurring idea discovery and hypothesis research with source filtering.
- “Idea → proof of concept → tests → review → release candidate” missions.
- Improvement proposals based on evaluation evidence, not blind self-modification.
- Multi-project priorities and resource allocation.

## 18. Acceptance test matrix

Required prior to calling R1 complete:

**Functional:**
- Create mission → persist → reload → inspect identical state.
- Dependency DAG runs independent tasks concurrently and blocking tasks in order.
- Agent handoff yields structured artifacts and preserves trace links.
- Branch/worktree isolation prevents overwriting sibling changes.
- Failed acceptance test keeps task in `FAILED`/`BLOCKED`, never `COMPLETE`.
- Approved safe task executes while external-effect task pauses.
- Denied permission cannot be bypassed by alternate tool in same mission.
- Pause/stop actually halts further tasks and records in-flight limitations.
- Crash recovery does not lose task state or duplicate already confirmed effects.
- Hard cost, retry, runtime, delegation and parallel-worker limits enforced.

**Security:**
- Agent cannot read outside allowlisted paths.
- Webpage containing prompt injection cannot reassign tools or extract secrets.
- Agent cannot create higher-privileged child agent.
- Raw credentials do not appear in event stream.
- Financial/external-effect actions disabled/approval-gated.

**Experience:**
- Dashboard reflects events within the actual supported latency budget.
- Every agent state links to task and evidence.
- UI never displays random/fake completion percentages.
- Empty states visually clean; failure states actionable.
- User can identify the next blocker and whether approval is needed in <10 seconds (usability hypothesis to test).

## 19. Non-goals and what ULTRON cannot guarantee

- No new general superintelligence is created by adding many subagents.
- Autonomous agents can still hallucinate, break code, misread context or waste tokens.
- Research agents cannot guarantee scientific truth; independent tests and data provenance are required.
- An agent cannot operate tools, subscriptions, websites, accounts or devices that were not integrated and authorised.
- Continuous operation requires running infrastructure and budget; it is not “free intelligence.”
- “Fully autonomous” is not permission to act outside the mission scope.

## 20. First exact implementation task

**Implement R0/R1 as a narrow end-to-end slice in the actual JARVIS repo.**

Demo command: “JARVIS, create an isolated sample project, write a tested utility function and return a verified patch.”

Proven execution:
1. Mission record created and visible.
2. AXIOM outputs a typed task specification.
3. FORGE uses an isolated worktree and writes code.
4. Tests execute, logs/artifacts are linked.
5. SENTINEL validates actual results and checks limits.
6. JARVIS reports passed/failed requirements with evidence.
7. The mission persists across application restart.
8. User can pause, resume and inspect cost/tool audit.

Only after this works should multiple simultaneous feature agents be enabled. The first actual project after R1 should be a bounded **QuantLab** feature, which exercises architecture, numerical tests, data provenance, UI and adversarial validation without any live trading.

## 21. Source references and current engineering context

Verified public references as of 2026-10-10:
- OpenAI Agents SDK: https://developers.openai.com/api/docs/guides/agents/sdk
- OpenAI SDK orchestration / guardrails: https://openai.github.io/openai-agents-python/guardrails/
- Claude Managed Agents: https://platform.claude.com/docs/en/managed-agents/overview
- Claude session threads: https://platform.claude.com/docs/en/managed-agents/session-threads
- Claude permission policies: https://platform.claude.com/docs/en/managed-agents/permission-policies
- Temporal durable agents: https://docs.temporal.io/ai
- LangGraph design and interrupts: https://docs.langchain.com/oss/python/learn
- OWASP excessive agency: https://genai.owasp.org/llmrisk/llm062025-excessive-agency/
- GitHub worktrees: https://docs.github.com/en/desktop/making-changes-in-a-branch/managing-worktrees-in-github-desktop

**Version caveat:** Technology APIs/features evolve. Inspect live, installed SDK versions and licences before committing to a concrete implementation. The architecture and acceptance tests, not package choice, are the product source of truth.
