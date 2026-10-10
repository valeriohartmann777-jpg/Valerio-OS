# AI routing architecture

_Smart AI Routing & Billing for JARVIS, ULTRON and QuantLab (D-030), as of 2026-10-10._
The owner's brief is in [`docs/AI_ROUTING_DIRECTIVE.md`](AI_ROUTING_DIRECTIVE.md). The money
rules are in [`AI_BILLING_POLICY.md`](AI_BILLING_POLICY.md), the owner's how-to is in
[`AI_FALLBACK_RUNBOOK.md`](AI_FALLBACK_RUNBOOK.md), and the evidence is in
[`AI_ROUTER_TEST_RESULTS.md`](AI_ROUTER_TEST_RESULTS.md).

## 1. Behaviour in one paragraph

Every model request in JARVIS goes through one `SmartRouter`. This covers the brain, Learning,
Bot Lab, the QuantLab Strategy Architect and research missions, and all ULTRON agents.

The router tries the routes in the owner's order:

1. **The Claude plan.** This runs the official Claude Code CLI under the owner's own claude.ai
   sign-in.
2. **The Claude API.** This uses the owner's key, but only after the owner has approved paid
   fallback with caps.
3. **Local AI.** This is Ollama, only if set up, and only for conversation.

If no allowed route is left, the work **pauses**. Missions are checkpointed and resume by
themselves once a route is back. Paused work is never silently degraded.

Temporary errors (overload, rate limit, network) are retried on the same route with backoff. They
are never a reason to start paying. Deterministic work (backtests, data checks, plots, ledgers)
never calls a model.

## 2. Components

The brief's contracts (§2) map onto the code as follows:

| Brief | Code | Notes |
|---|---|---|
| `AIProvider` interface | `jarvis.llm.base.ChatModel` (`complete(system, messages, tools, …) → ModelReply`), routed by `RoutedChatModel` | `usable()` is capabilities, `check()` plus the probe loop is health, `complete()` is invoke, the `ai_usage` ledger is usage, and `ai/classify.py` is error_mapping. **No streaming** through the router: consumers get complete replies (as before). |
| `ClaudeSubscriptionAdapter` | `jarvis.ai.plan.PlanChatModel` + `ClaudeCli` | Uses the official `claude -p` only, in an isolated environment. See §4. |
| `ClaudeApiAdapter` | `jarvis.llm.anthropic_provider.AnthropicChatModel` (official `anthropic` SDK) | The key lives in the OS keystore via `jarvis.ai.keys.ApiKeyStore`. See §5. |
| `LocalModelAdapter` | `jarvis.ai.local.LocalChatModel` (Ollama `/api/chat`, loopback only) | Off by default. It is labelled Ready or Unavailable only after a real check. |
| `SmartRouter` | `jarvis.ai.router.SmartRouter` | Handles capability matching, the order, health and limit state, policy, failover, sticky runs and the probe loop. |
| `UsageAccounting` | `ai_usage` (append-only) via `jarvis.ai.store.AiStore` | One row per call: provider, model, consumer, agent, mission, task, tokens, billed cost, list value, outcome, failure and latency. |
| `ApprovalAndBudgetPolicy` | `jarvis.ai.policy` + `SmartRouter._check_paid` | Applies hard caps, takes a separate one-time approval, and logs to `ai_approvals` (append-only). |
| `JobCheckpointStore` | `ai_checkpoints` + `ai_tool_ledger` via `jarvis.ai.checkpoint.RunCheckpoint` | Checkpoints conversations; a tool ledger keyed by tool-call id is the idempotency key. |
| `RouterEventBus` | `ai.route` events on the existing JARVIS event bus, then the WebSocket | These feed the top-bar route chip, Settings → AI & Billing and the ULTRON / QuantLab blockers. |

```
 consumer (brain · learning · bots · QuantLab architect/missions · ULTRON agents)
     │  RoutedChatModel(RoleSpec: consumer, role, capability, model, effort, max_tokens)
     ▼
 SmartRouter.complete ──► order (strategy + sticky run) ──► usable(provider, capability)?
     │                                                         │
     │  plan ──► PlanChatModel ──► `claude -p …` (isolated env, own claude.ai sign-in)
     │  api  ──► _check_paid (budget, cap, price) ──► AnthropicChatModel (key from keystore)
     │  local──► LocalChatModel (Ollama, chat only)
     ▼
 classify(error) ──► TEMPORARY: retry/backoff, then pause  ·  FAILOVER: mark route, next one
                     CAPABILITY / BAD_REQUEST / UNKNOWN: back to the caller (no switch)
     ▼
 book usage (ai_usage) ──► ai.route events ──► dashboard · ULTRON · QuantLab
```

## 3. Routing rules

- **Strategy** (Settings): `subscription_first` (plan → API → local), `plan_only` (plan →
  local, never paid) or `api_only` (API → local).
- **Capabilities.** Every consumer declares one of `chat`, `planning`, `coding`, `research` or
  `review`:

  | Consumer | Capability |
  |---|---|
  | Brain | chat |
  | Learning | research |
  | Bot Lab | coding |
  | QuantLab Strategy Architect and missions | research |
  | ULTRON FORGE | coding |
  | ULTRON SENTINEL | review |
  | Other ULTRON agents | planning |

  Local AI is allowed for `chat` only. A coding or review task never runs on a local model;
  it pauses instead.
- **Capability mismatch on the plan.** The plan route can't take images or Anthropic server
  tools (web search), so it reports `capability`, and the router passes that back to the
  caller rather than switching silently. Learning is the one consumer that then asks again
  without web search, because web search is optional there. That call runs on the model's
  own knowledge. Any other consumer surfaces the error.
- **Sticky runs.** A run is identified by its first message. Once a run fails over (for
  example plan → API), it stays on that route until it ends, so one transaction never mixes
  authentication. New runs start from the top of the order again, so they return to the plan
  as soon as it is available.
- **Failure taxonomy** (`ai/types.py`, `ai/classify.py`):

  | Failure | Examples | Action |
  |---|---|---|
  | `overloaded`, `rate_limit`, `network` | 529 / 5xx, 429 + Retry-After, timeouts | Retry on the same route: at most `max_attempts`, backoff `2·2ⁿ` s capped at `max_backoff_seconds`, Retry-After honoured. Then a short cooldown and **pause (temporary)**. Never a paid switch. |
  | `plan_limit` | "You've hit your session limit · resets 3:45pm" | Mark the plan `LIMIT_REACHED` until the reported reset (or `plan_retry_minutes`), then fail over. |
  | `auth` | "Not logged in", expired sign-in | Mark the plan `NOT_LOGGED_IN`, then fail over. No retry loop. |
  | `key_invalid` | 401 from the API | Mark the API `INVALID_KEY`, then fail over. |
  | `credit` | "Your credit balance is too low…", org spend limit | Mark the API `INSUFFICIENT_CREDIT` (cooldown), then fail over. Nothing is ever bought. |
  | `policy` | Organization disabled, not permitted | Mark the route `UNAVAILABLE`, then fail over. |
  | `capability`, `bad_request`, `unknown` | Images on the plan, malformed request | Back to the caller. No switch. |

- **Pause and resume.** When no route is left, the router raises `AIPaused` (code
  `ai_paused`) with the reasons.
  - **Brain:** answers "My AI is paused right now." with the reasons.
  - **ULTRON:** the running task returns to READY and keeps its checkpoint. The attempt isn't
    counted. The mission is BLOCKED with a blocker of kind `ai_route`.
  - **QuantLab missions:** the task is BLOCKED with `AI_PAUSED`.
  - **Learning and Bot Lab:** they wait and retry on their own schedule.

  The probe loop (default every 300 s, all checks free) clears expired limits and cooldowns
  and re-checks the plan sign-in and the local server. When the route turns available, the
  `on_available` listeners resume blocked ULTRON and QuantLab missions automatically.

## 4. The Claude plan route (compliance)

Anthropic's documentation (checked 2026-10-10, links in the billing policy) says the Agent
SDK and `claude -p` may use a subscriber's plan limits. The same documentation says plans are
for the subscriber, and it prohibits tools that misrepresent their identity or route
third-party traffic. JARVIS therefore:

- runs **only the official `claude` binary** (`claude -p`, `claude auth status`,
  `claude --version`). It never reads, copies or forwards OAuth tokens, and it never imitates
  a client or alters headers.
- accepts the plan only when `claude auth status` reports `authMethod: "claude.ai"`. That is
  the owner's own subscription sign-in. Any other method (API key, token, cloud provider) is
  shown as **Not supported for JARVIS**.
- needs the owner to turn the plan route on once and confirm personal use ("Only I use this
  JARVIS, with my own Claude account"). The confirmation is stored with its timestamp in the
  approval log.
- starts the CLI with an **allow-listed environment** (`ai/plan.py: isolated_env`).
  `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, cloud-provider
  switches and every other variable are never passed on, so an API key in the shell can't
  silently turn plan use into API billing. `DISABLE_AUTOUPDATER=1` is set.
- locks Claude Code down to a pure model call:
  - `--tools ""` — no Claude Code tools;
  - `--strict-mcp-config --disallowedTools mcp__*` — no MCP;
  - `--permission-mode dontAsk`;
  - `--safe-mode`;
  - `--no-session-persistence`;
  - `--max-turns` (small);
  - a system-prompt file (mode 0600, deleted afterwards).

  JARVIS's own tools are described in that prompt. The model answers through `--json-schema`
  as `{text, tool_calls}`, and **JARVIS executes the tools itself through the same permission
  gates as on the API**. Nothing runs inside Claude Code.
- does not use `--bare` (it skips the claude.ai sign-in).

Plan usage and remaining limits have no official interface. The UI says *"Not retrievable —
Anthropic reports a limit when it is reached"*, and JARVIS only knows a limit (and its reset
time) when Claude Code reports one.

## 5. The Claude API route and the key

- Official `anthropic` SDK, unchanged (`AnthropicChatModel`).
- **Key storage.** The key goes into the OS keystore (macOS Keychain, Windows Credential
  Manager or Secret Service) through `CredentialVault(service="JARVIS AI")`.
  - If no protected keystore is reachable, connecting is disabled; nothing is stored in plain
    text.
  - A key left in `jarvis/.env` by earlier versions is moved into the keystore once at
    startup, and its line in `.env` is replaced by a comment.
  - The key is verified with the free models endpoint before it is stored.
  - Only its last four characters are ever shown. It is never logged, never emitted in an
    event, never returned by the API and never put into a prompt.
- Paid calls are allowed only while paid fallback is approved and the budget and caps hold
  (see the billing policy). Before each paid call the router reserves a slot (`max_concurrent`)
  and checks the worst-case cost of that call. Afterwards it books the actual cost from the
  reported usage.

## 6. Checkpoints and idempotency

- `run_agent` (ULTRON) saves the conversation after every model reply and after every round
  of tool results (`ai_checkpoints`, key `ultron:{task_id}`).
- Every executed tool call is recorded in `ai_tool_ledger` under its call id with its result.
  On resume, recorded calls return their recorded result and are **not executed again**. That
  covers file writes, exports and anything else with an effect. Unanswered calls of the last
  reply are handled first.
- The checkpoint is kept on pause or cancel and deleted when the task finishes or fails for
  good. A resumed ULTRON task keeps its workspace (no reset) and its attempt count.
- QuantLab missions keep their own persisted state (stages, typed tasks, Databento approvals).
  A paused stage is re-run from its start once AI is back. Data purchases sit behind their own
  quote and approval, and failover never triggers a purchase.

## 7. Events and UI

- `ai.route` events (`EventType.AI_ROUTE`) carry route changes, failovers, limits, pauses,
  budget warnings and approvals. Payloads hold no secrets.
- **Top bar.** A chip shows *AI · PLAN / API / LOCAL / PAUSED* on every page, ULTRON and
  QuantLab included. Its tooltip gives the reason, and clicking it opens Settings.
- **Settings → AI & Billing** shows:
  - the current route and the reason;
  - strategy and work profile;
  - the fallback order;
  - the plan: status, check, test call, sign-in, install, personal-use confirmation;
  - the API: status, key (hint only), paid-fallback form and month meter;
  - local AI;
  - this month's usage per route and per area;
  - the display currency with a rate assumption;
  - recent routing events;
  - a label when the test keystore is in use.

## 8. Storage (SQLite migration 15)

| Table | Content |
|---|---|
| `ai_settings` | The owner's choices (`AiConfig` as JSON): strategy, profile, plan on/off with confirmation time, paid fallback terms, local model, currency. |
| `ai_approvals` | **Append-only** (triggers block UPDATE and DELETE). Every approval, change and automatic switch-off. |
| `ai_usage` | **Append-only.** One row per model call (billed or not). |
| `ai_events` | Route changes, limits, warnings (one per threshold per month). |
| `ai_checkpoints`, `ai_tool_ledger` | Resumable runs and executed tool calls. |

## 9. API (`backend/jarvis/api/ai_routes.py`)

| Endpoint | Purpose |
|---|---|
| `GET /ai/status` | Full status snapshot (no secrets). |
| `GET /ai/usage` | Last calls, events, approvals. |
| `POST /ai/strategy` | Strategy and profile. |
| `POST /ai/plan` `{enabled, personal_use}` | Turn the plan route on (needs `personal_use: true`) or off. |
| `POST /ai/plan/check` | Free check: `claude --version`, `claude auth status`. |
| `POST /ai/plan/test` | One tiny real call on the plan (owner-initiated). |
| `POST /ai/plan/terminal` `{action}` | macOS: opens Terminal with `claude auth login` or the official installer. |
| `POST /ai/paid` `{monthly_budget_usd, per_mission_cap_usd, warn_at, max_concurrent, stop_at_budget, confirm: true}` | Approve paid fallback. |
| `POST /ai/paid/disable` | Turn paid fallback off. |
| `POST /ai/api-key`, `DELETE /ai/api-key`, `POST /ai/api/check` | Key: verify and store, remove, check. |
| `POST /ai/local`, `POST /ai/local/check` | Local model. |
| `POST /ai/currency` | USD / CHF and the rate assumption. |

No agent tool can call these endpoints; they are owner actions from the UI.

## 10. Configuration

`config/ai.yaml` holds static settings:

- keystore;
- the `claude` path;
- timeouts, attempts, backoff, cooldown;
- the probe interval;
- list prices per model (USD per MTok);
- work profiles.

Environment variables:

- `JARVIS_AI_KEYSTORE=memory` — tests and E2E only, labelled in the UI;
- `JARVIS_AI_CLI=/path/to/claude` — a different binary, shown as *Custom* in the UI.

The owner's choices live in the database and are changed only in Settings.
