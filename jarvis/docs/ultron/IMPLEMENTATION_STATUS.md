# ULTRON — implementation status

Updated: 2026-10-10 · Release scope: **R0 + R1** · Decision: D-027 · Architecture: §13g
Spec: `docs/ultron-handoff/ULTRON_TEAM_MASTER_BLUEPRINT.md` · Gap analysis: `INTEGRATION_PLAN.md`

## DONE and TESTED (in this environment)

| Blueprint requirement | Where | Evidence |
|---|---|---|
| Goal → charter + task DAG by JARVIS, validated in code | `ultron/plan.py`, `service._plan` | `test_plan_validation_rules`, `test_invalid_plan_is_sent_back_and_blocked_after_rounds` |
| Every FORGE task independently reviewed (added if the plan forgot) | `plan.validate` | `test_plan_validation_rules` |
| Blocking questions go to the user; answer → re-plan | `service.answer` | `test_blocking_questions_wait_for_the_user` |
| Durable mission ledger (missions, tasks, runs, artifacts, approvals, events) | migration 10, `ultron/store.py` | `test_mission_survives_a_restart`, E2E restart step |
| Dependency scheduling, independent tasks in parallel (bounded) | `service._tick` | the scheduler starts every READY task up to `max_parallel_workers` |
| Isolated workspaces: git repo per mission, worktree + branch per task | `ultron/workspace.py` | all mission tests; `test_jarvis_project_works_on_a_branch_never_the_checkout` |
| Scoped tool broker, per-agent tool manifests, audited calls | `ultron/tools.py` | out-of-scope writes refused and logged (acceptance test + E2E) |
| No shell; argv allowlist; network/install tools locked | `ultron/policy.py` | `test_path_and_command_policy` |
| Sandboxed commands: scrubbed env, timeout kills the process group, no network | `ultron/sandbox.py` | `test_sandbox_scrubs_secrets_and_blocks_network` (Linux netns) |
| Checks run by the runtime, not trusted from the agent; failure → retry with logs | `service._produce` | acceptance test: attempt 1 fails the runtime's pytest, attempt 2 passes |
| SENTINEL reviews a throwaway checkout, with the runtime's own check results; rejection reopens FORGE | `service._review` | `test_sentinel_rejection_sends_work_back_to_forge` |
| Bounded retries; exhausted → BLOCKED, never COMPLETE; user can grant another round | `service._failed_attempt`, `resume` | `test_exhausted_retries_block_and_resume_grants_more` |
| Spend cap checked before every model call; raise budget → continues | `runner.Budget`, `set_budget` | `test_budget_is_checked_before_every_call` |
| Pause holds agents before their next step; resume continues | gate per mission | `test_pause_holds_agents_and_resume_continues` |
| Stop / emergency stop interrupts running agents and names in-flight work | `cancel`, `stop_all` | `test_stop_cancels_running_work`, `test_emergency_stop_all` |
| Crash/restart recovery: interrupted runs recorded, tasks rerun from a clean tree | `service.start` | `test_interrupted_task_runs_again_after_restart` |
| Approval gate bound to an effect signature; atomic; tamper-evident; locked categories can't be requested | `request_approval`, `decide`, `_execute` | `test_approvals_are_atomic_bound_and_locked_categories_refused`, acceptance test |
| JARVIS-repo missions on a branch only; merging into the checkout locked | `workspace.create` (jarvis) | `test_jarvis_project_works_on_a_branch_never_the_checkout` |
| Final acceptance report: each criterion verified by a check or confirmed by SENTINEL | `service._finish` | acceptance test |
| Brain integration: `ultron_start_mission`, `ultron_status` | `tools/ultron.py` | `test_brain_tool_starts_a_mission` |
| API `/ultron/*` with honest errors (e.g. NO_MODEL) | `api/routes.py` | `test_api_without_a_model_says_what_is_missing` |
| Mission Control UI: Overview, Mission Control (DAG, evidence, controls), Agent Matrix, Projects, Knowledge, Activity, Permissions & Settings | `pages/Ultron.tsx`, `components/ultron/` | E2E ULTRON step + screenshots |

## MOCKED (labelled)
- **Model replies in tests and the E2E.** No Anthropic key exists in this development
  container, so agent decisions come from a script (`tests/e2e/ultron_script.json`,
  `JARVIS_ULTRON_SCRIPT`). The UI shows "SCRIPTED TEST MODEL" whenever it is active.
  Everything around the replies is real: files, git worktrees and merges, sandboxed pytest
  runs, policy refusals, approvals, the export, persistence and recovery.

## NOT VERIFIED YET
- A mission with the real Claude models (needs the user's key; costs money, capped per
  mission). Prompts and tool schemas follow the existing JARVIS tool-use pattern but have
  not run against the live API here.
- macOS Seatbelt profile (`sandbox-exec`): written and probed at startup, falls back
  honestly if the probe fails; not executable in this Linux container.

## BLOCKED / LOCKED BY DESIGN (R1)
- Network for agents, package installs, merging into the running JARVIS checkout,
  deployments, outbound messages, credentials, money.
- ATLAS, PRISM, CIPHER, ARCHIVE, VECTOR, OPERATOR as ULTRON workers (listed, not active).
- On Linux the OS doesn't confine file writes (the broker still does); on macOS Seatbelt does.

## NEXT
1. First real mission on the MacBook (e.g. "CSV → JSON CLI with tests", budget $3) and a
   look at the real transcripts/costs; tune prompts from that evidence.
2. Merge conflicts between parallel FORGE tasks: route to an integration task instead of
   blocking.
3. R2 workers: CIPHER on QuantLab analyses (data provenance, no look-ahead), PRISM with a
   Node sandbox for UI tasks.

## Commands actually executed (2026-10-10)
- `./scripts/check.sh` → all checks passed: ruff, format, mypy strict (linux, win32,
  darwin), backend pytest **420 passed** (402 before + 18 ULTRON), desktop typecheck,
  vitest **31 passed**
- `cd apps/desktop && npm run build` → ok
- `xvfb-run -a npm run test:e2e` → **25/25 steps passed**, including "ULTRON: a goal becomes
  a plan; AXIOM specs, FORGE codes and retries, SENTINEL verifies, export approved" and the
  mission ledger surviving an app restart
- Screenshots: `docs/screenshots/ultron-overview.png`, `ultron-mission.png`,
  `ultron-patch.png`, `ultron-agents.png` (scripted test model, labelled in the UI)
