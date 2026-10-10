# AI router test results

_Evidence for D-030 (Smart AI Routing & Billing), run on 2026-10-10 in the Linux build
container. Architecture: [`AI_ROUTING_ARCHITECTURE.md`](AI_ROUTING_ARCHITECTURE.md)._

## How it was tested

The tests use **offline stand-ins**, each labelled as a test stand-in in the code and in the UI:

| Stand-in | Replaces |
|---|---|
| `FakeCli` | Claude Code, run in-process. |
| `tests/fixtures/ai/fake_claude.py` | Claude Code as a real executable. It logs its arguments, stdin and the environment's variable names, so the tests can prove what Claude Code could see. |
| `FakeApi` | The Anthropic SDK. |
| `httpx` mock transport | Ollama. |
| Memory keystore | The OS keystore. |

No real Claude plan, API key, Keychain or local model was used in these runs (see
"Not verified" below).

| Suite | Command | Result |
|---|---|---|
| Router scenarios + classification | `pytest tests/test_ai_router.py` | **34 passed** |
| Key connection | `pytest tests/test_connector.py` | **17 passed** |
| Whole backend (`scripts/check.sh`, incl. ruff, format, mypy for linux/win32/darwin) | `./scripts/check.sh` | **568 passed**, all checks green |
| Desktop typecheck + vitest | (part of `check.sh`) | **40 passed** |
| End-to-end, real Electron app + backend | `xvfb-run -a node tests/e2e/run.mjs` | **30/30 steps passed** |

## Definition of Done — the 15 scenarios of the brief

| # | Scenario (brief §6) | Test | What is asserted | Result |
|---|---|---|---|---|
| 1 | Plan available and permitted → plan, no API charge | `test_01_plan_first_and_no_api_charge` | Even with paid fallback approved, the reply comes from the plan; 0 API calls; billed $0; ledger row `plan`, `billed=0`, list value informational; route `plan` | ✅ |
| 2 | Plan unavailable / unsupported → API only with the cost opt-in | `test_02_unsupported_plan_needs_the_paid_opt_in`, `test_02b_paid_fallback_needs_confirm_and_caps` | Claude Code signed in by API key gives `NOT_SUPPORTED`, so no `claude -p` and no API call; the call pauses with "paid fallback not approved". After approval it runs on the API and books $0.007. Approval without `confirm` or with cap > budget is refused, and only the valid one is logged. | ✅ |
| 3 | Plan quota exhausted → checkpoint, authorised API fallback, correct status | `test_03_plan_limit_fails_over_to_the_approved_api_mid_run` | A FORGE run writes a note on the plan, then hits "session limit · resets 3:45pm" and finishes on the API. The note is written once. Plan shows `LIMIT_REACHED` with `until`; failover and "Claude plan → Claude API" events; checkpoint saved. New work stays on the API until the reset, then returns to the plan. | ✅ |
| 4 | No API opt-in → never a paid call | `test_04_without_opt_in_the_api_is_never_called` | On the plan limit: `AIPaused` with "Claude API: paid fallback not approved"; 0 API calls; no key read; $0; ledger has only the failed plan attempt | ✅ |
| 5 | `ANTHROPIC_API_KEY` in the environment would override the plan → process isolation | `test_05_process_isolation_keeps_api_keys_away_from_claude_code` | With the **real executable** stand-in, and `ANTHROPIC_API_KEY` plus `CLAUDE_CODE_OAUTH_TOKEN` set in JARVIS's environment, Claude Code's environment holds neither; the key appears nowhere in the call log; `--safe-mode` and `--tools` are passed. A Claude Code signed in by API key is refused before any `-p` call. | ✅ |
| 6 | Max/Team API credits vs plan limits, no invented balances | `test_06_credits_and_plan_limits_are_not_invented` | Credits note present; plan usage "Not retrievable"; no balance or remaining-token fields; budget left is the owner's own budget, and `null` until one is approved | ✅ |
| 7 | Paid budget exceeded → new paid jobs blocked; running cost booked honestly | `test_07_budget_reached_blocks_new_paid_calls`, `test_07b_a_call_that_could_exceed_the_cap_never_starts` | A call that ran is booked even above the budget ($0.032 of $0.02); warnings at 50 / 80 / 100 % once each; stop-at-budget switches paid off and logs it; the next call pauses; a call whose worst case would break the per-mission cap never starts | ✅ |
| 8 | API credit exhausted → pause or local, no endless loop | `test_08_credit_exhausted_pauses_without_a_retry_loop` | "No credit" gives `INSUFFICIENT_CREDIT` and a pause; the second request makes **no** API call. With Ollama on, chat goes local while coding pauses ("not suitable"). A non-loopback local URL is refused. | ✅ |
| 9 | Network failure → bounded retries, no double charge | `test_09_temporary_errors_retry_boundedly_and_never_switch_to_paid`, `test_09b_api_overload_is_retried_and_charged_once` | Two network errors, then success: backoff 2 s and 4 s, no API call. Three errors: temporary pause, plan `UNAVAILABLE`, still no paid call, $0 billed. API overload plus 429 with Retry-After 7 s: sleeps [2, 7], charged once ($0.007). | ✅ |
| 10 | Restart during a mission → resume from checkpoint, tool idempotency | `test_10_a_paused_run_resumes_without_repeating_a_tool`, `test_10b_ultron_mission_pauses_for_ai_and_resumes_by_itself` | A paused run resumes after the executed tool, without running it again. After a crash between "model asked" and "results saved", the ledger answers instead of re-running. A full ULTRON mission blocks on `ai_route`; FORGE goes back to READY with 0 attempts; `on_ai_available()` resumes it to `WAITING_APPROVAL`. | ✅ |
| 11 | Databento approval stays separate and mandatory | `test_11_ai_failover_never_buys_market_data` | A QuantLab mission waits on a Databento quote. Approving, then disabling, paid AI and re-checking the plan leaves it `WAITING_APPROVAL`: no job, quote not approved, no data-buying consumer in the AI ledger. | ✅ |
| 12 | All agents use the same router; budgets and permission gates hold | `test_12_every_consumer_goes_through_the_one_router`, `test_12b_permission_gates_hold_on_the_plan_route` | ULTRON, QuantLab, architect, learning, bots and brain models are all `RoutedChatModel` on the one router; every consumer is booked; one key, one factory. A tool call arriving **via the Claude plan** (`open_application powershell`) waits for the owner's approval; after approval the result goes back to the plan, $0 billed. | ✅ |
| 13 | AI offline → data, backtests and reports still run | `test_13_without_any_ai_route_backtests_and_reports_run` | Route `paused`, brain offline; dataset, strategy, backtest `COMPLETED` with trades; report labelled SYNTHETIC; the AI ledger is empty | ✅ |
| 14 | UI billing status matches real events; no mock shown as a real connection | `test_14_status_endpoints_reflect_real_checks_and_events` + E2E step | `/ai/status`: no binary gives `NOT_INSTALLED`, `NO_KEY`, local `DISABLED`, route `paused`, test keystore flagged. A bad key gives 422. Saving a key gives `CONNECTED`, but the route stays `paused` (a key isn't permission to spend). A foreign Origin gives 403; no confirm gives 422; approval gives route `api`. Approvals and events are logged. | ✅ |
| 15 | No key, token or secret header in logs, traces, WebSocket or crash reports | `test_15_secrets_never_reach_logs_events_ledgers_or_prompts` | Neither the full key nor its prefix appears in: status, log capture (DEBUG), bus events, usage, events, approvals, Claude Code calls or the prompts sent. Only the last four characters are shown, and the key never lands in the process environment. | ✅ |

Classification tests:

- `test_claude_code_messages_are_classified` (13 cases) covers:
  - plan limits in session, weekly, spend and epoch format;
  - sign-in;
  - credit;
  - policy;
  - 429 capacity versus usage;
  - 529;
  - DNS;
  - unknown.
- `test_reset_times_are_read_not_guessed` checks that reset times are read from the message
  (today, tomorrow, weekday, epoch) or left unknown.

## End-to-end (real Electron app, simulated desktop)

Step: **"AI & Billing: plan first (test stand-in), its limit pauses the brain, paid fallback
only with explicit caps"**. The environment is a memory keystore plus Claude Code replaced by
`fake_claude.py` through `JARVIS_AI_CLI`. Both are labelled in the UI as *Test keystore* and
*Custom binary*. The step checks the following:

1. Defaults: chip `AI · PAUSED`, paid fallback *Off*, "Use my Claude plan" disabled until
   personal use is confirmed, "Approve paid fallback" disabled without the confirmation tick.
2. Confirm personal use: plan *Connected*, chip `AI · PLAN`, brain online "via Claude plan".
3. Chat "plan my evening" is answered by the plan stand-in.
4. The stand-in switches to "You've hit your session limit · resets 3:45pm". The next chat gets
   "My AI is paused right now.", chip `AI · PAUSED`, plan *Usage limit reached*, a limit event
   in *Recent*.
5. The call log proves no `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or
   `CLAUDE_CODE_OAUTH_TOKEN` reached Claude Code, and every `-p` call ran with `--safe-mode`
   and `--no-session-persistence`.
6. Approve paid fallback ($20 / $5) shows *On*, meter "$0.00 of $20.00", route reason "no API
   key". `/ai/usage` has a `paid_approved` approval and **no billed call**.
7. Paid fallback and the plan are turned off again for the rest of the run.

Screenshots: `docs/screenshots/ai-billing-plan.png`, `ai-billing-limit.png`,
`ai-billing-paid.png`.

The 29 earlier E2E steps (desktop, missions, approvals, Learning, Bot Lab, QuantLab, ULTRON,
restart, update) still pass with the router in place. These include the brain's "connect"
form, which now stores keys via AI & Billing.

## Mocked here, and not verified

- **Mocked (labelled):** Claude Code (in-process and executable stand-ins), the Anthropic API
  (fake model), Ollama (mock transport), the keystore (memory).
- **Not verified in this environment:**
  - a real claude.ai sign-in and a real `claude -p` call on the owner's plan;
  - a real API call with the owner's key;
  - the macOS Keychain;
  - a real Ollama model;
  - Claude Code's exact limit wording on the owner's machine. The classifier covers the
    documented messages; an undocumented one is classified `unknown` and is **not** treated
    as a reason to pay.

  The first real check belongs on the MacBook (runbook §1a, button **Test**).
- **One development incident, for the record.** While investigating the CLI's output format,
  one exploratory run of the `claude` binary inside this build container made one real, tiny
  model call. It used the container's own Claude Code credentials (method `oauth_token`), not
  the owner's account; the list value was about $0.004. No further real calls were made. JARVIS
  itself would refuse that sign-in method for the plan route (it isn't `claude.ai`).
