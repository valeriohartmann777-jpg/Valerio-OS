# Claude Code — JARVIS ULTRON implementation handoff

You are the principal engineer implementing **ULTRON: Autonomous Team Execution** inside the user's existing JARVIS project.

The authoritative product spec is `ULTRON_TEAM_MASTER_BLUEPRINT.md` in this handoff directory. Read it fully. Follow existing repository instructions and do not overwrite or destabilize JARVIS/QuantLab.

## Work mode
1. Inspect the **actual** repository, stack, running commands, tests, Git status, existing task/agent/event/permission infrastructure. Do not assume earlier architecture drafts have already been implemented.
2. Write a concise gap analysis in `docs/ultron/INTEGRATION_PLAN.md` that maps the blueprint to existing code and identifies re-use opportunities.
3. Implement a working **R0 → R1 vertical slice**, not a vast set of stubs.
4. Execute and verify relevant backend/frontend tests, plus one real integration demo; repair failures within scope.
5. Maintain `docs/ultron/IMPLEMENTATION_STATUS.md`: DONE / TESTED / MOCKED / BLOCKED / NEXT, with evidence and exact commands.
6. Give a final implementation report with changed files, verified behavior, unresolved limitations and one next task.

## Non-negotiable invariants
- Mission/task state and permissions enforced by deterministic code and durable storage, not only LLM instructions.
- Scoped tool broker; no arbitrary unrestricted shell on the host.
- Approval for external side effects, destructive operations, production deployments and credential changes.
- Financial order execution remains disabled.
- No privileged child agent or unconstrained recursive spawning.
- Every completed task links to actual tested artifacts.
- No fabricated logs, progress, test outcomes or UI capabilities.
- Protect existing QuantLab data and confidential information.
- No secrets committed to source control.

## R0 deliverable
A user opens JARVIS → ULTRON Mission Control, types a simple development mission; backend persists mission/tasks and publishes state changes; a real safe worker writes a file inside an allowed isolated workspace, executes a deterministic test and creates verification evidence; the UI shows honest states, events, artifacts and allows Pause/Stop; task is only Complete if checks pass; reload retains the state.

## R1 extension
AXIOM planning, FORGE implementation, SENTINEL independent verification; bounded task DAG, retries, Git worktree isolation and role-based permission manifests. Show which tasks are parallelizable; support review before merging to protected branches.

## Implementation selection rules
- Reuse the existing React/Electron/FastAPI services if actually present; avoid duplicate state stores.
- Start with **one** agent SDK adapter behind a stable interface (choose to match installed credentials/runtime). Do not integrate several competing orchestration frameworks in the same milestone.
- If a robust existing queue/checkpointer exists, reuse it. Otherwise implement a small transaction-safe durable local queue for R0/R1 and record a Temporal migration trigger; do not build a distributed scheduler from scratch.
- Support dummy tests only when visibly labeled test fixtures; the main demo must perform a real sandboxed execution and real verification.
- Prefer strongly typed contracts and focused tests to excessively elaborate abstractions.

## Acceptance demo
`User asks for a testable utility → JARVIS creates charter and tasks → AXIOM provides spec → FORGE creates patch in worktree → tests run → SENTINEL independently validates evidence → JARVIS presents reviewed artifact.`

Prove persistence after restart, denial of out-of-scope writes, bounded retry handling, honest failure reporting, and working Pause/Stop controls. Begin coding now after repository inspection. Do not stop at the planning document.
