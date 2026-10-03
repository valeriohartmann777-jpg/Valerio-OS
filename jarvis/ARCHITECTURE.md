# JARVIS — Architecture

JARVIS is a persistent personal intelligence layer between the user and their
digital environment. Internally it is many parts (router, agents, tools,
permissions, storage, sensors); externally it is one voice: **JARVIS**.

This document describes the system as it exists today and the boundaries that
later phases plug into. Anything not yet built is marked **(planned)**.

---

## 1. Lifecycle of every consequential action

```
USER ─► UNDERSTAND ─► PLAN ─► DELEGATE ─► ACT ─► OBSERVE ─► VERIFY ─► RESPOND
                                            ▲                   │
                                            └── permission gate ┘ (Sentinel / policy)
```

Concretely, for `"open notepad"`:

| Step        | Component                         | Event(s)                                   | JARVIS state          |
|-------------|-----------------------------------|--------------------------------------------|-----------------------|
| receive     | `core.JarvisCore`                 | `command.received`                         | `UNDERSTANDING`       |
| understand  | `core.router.RuleBasedRouter`     | `intent.classified`                        | `UNDERSTANDING`       |
| plan        | `missions.planner`                | `mission.created`                          | `PLANNING`            |
| delegate    | `missions.MissionEngine`          | `agent.started` (Operator)                 | `DELEGATING`          |
| authorize   | `permissions.PermissionService`   | `permission.requested` (only if required)  | `WAITING_FOR_APPROVAL`|
| act         | `tools.ToolExecutor` → `open_application` | `tool.started`                     | `EXECUTING`           |
| observe     | tool snapshots before/after       | `tool.completed` (with observations)       | `EXECUTING`           |
| verify      | `agents.SentinelAgent` → `tool.verify()` (independent re-observation) | `verification.completed` | `VERIFYING` |
| respond     | `core.responses.ResponseComposer` | `mission.updated`, `jarvis.message`        | `COMPLETE` → `DORMANT`|

A tool call is never considered successful just because it was issued: tools
observe the environment before and after acting, and Sentinel re-observes
independently.

---

## 2. Layers and boundaries

```
┌──────────────────────────── apps/desktop (Electron) ─────────────────────────────┐
│ main process: window, app:// protocol, backend supervisor                         │
│ renderer: React dashboard — consumes WS /events, calls REST                       │
└───────────────────────────────▲──────────────────────────────────────────────────┘
                                │  packages/protocol (typed event + API contract)
┌───────────────────────────────┴──────────── backend (FastAPI) ───────────────────┐
│ api/          REST + WebSocket. Thin. No business logic.                          │
│ core/         JarvisCore (orchestrator), StateService, Router, ResponseComposer,  │
│               EnvironmentContextService, TraceContext                             │
│ missions/     Mission model, deterministic planner, MissionEngine, repository     │
│ agents/       Agent specs + registry; Operator (acts), Sentinel (verifies)        │
│ tools/        Tool interface, registry, executor (permission gate, audit, events) │
│   system/     SystemBackend protocol: WindowsSystemBackend | SimulatedSystemBackend│
│ permissions/  Levels 0–4, policy (config/permissions.yaml), approval requests     │
│ events/       Event model, EventBus (pub/sub + history), SQLite event store       │
│ storage/      SQLite database + migrations, audit log                             │
│ llm/          ChatModel protocol, Anthropic provider, model registry              │
│ observability/ Structured JSON logging with trace_id / mission_id                 │
└───────────────────────────────────────────────────────────────────────────────────┘
```

Rules that keep the boundaries honest:

- **The UI only knows the protocol.** It renders events and snapshots. It never
  derives business state that the backend does not send.
- **The API layer has no logic.** Routes translate HTTP ↔ service calls.
- **Tools never talk to the UI.** They return structured `ToolResult`s; the
  executor emits events.
- **Agents never call the OS directly.** Operator goes through `ToolExecutor`,
  which enforces permissions and writes the audit log.
- **Platform code lives behind `SystemBackend`.** Nothing outside
  `tools/system/` imports `ctypes`, `winreg` or OS specifics.
- **One composition root.** `jarvis/runtime.py` wires every service. There is no
  hidden global state; services receive dependencies via their constructors.

---

## 3. Event system

All state changes are published on the in-process `EventBus`
(`backend/jarvis/events/bus.py`).

```
Event {
  id, type, timestamp, severity, source,   # source = jarvis | operator | sentinel | system …
  message,                                 # human-readable line for the activity stream
  trace_id, mission_id,                    # observability
  payload                                  # typed per event type, see packages/protocol
}
```

- Subscribers register by exact type, prefix wildcard (`mission.*`) or `*`.
- Handler failures are isolated and logged; a broken subscriber cannot break
  the emitter.
- The bus keeps a bounded in-memory history so a reconnecting dashboard gets
  the recent picture immediately.
- Events with severity ≥ `info` (except high-frequency `context.updated`) are
  persisted to SQLite (`events` table) for the activity history.
- WebSocket `/events` sends a full **snapshot** first, then streams events.

Event types live in one place per language:
`backend/jarvis/events/types.py` ↔ `packages/protocol/src/events.ts`. A test
(`backend/tests/test_protocol_sync.py`) fails if they drift.

Severity: `debug` (hidden by default), `info`, `important`, `warning`, `error`.
A failure produces exactly one `error` line (the root cause, e.g. `tool.failed`);
follow-up events (agent failed, mission failed) stay neutral so the stream
remains readable.

---

## 4. JARVIS states

`DORMANT, LISTENING, UNDERSTANDING, PLANNING, THINKING, DELEGATING, EXECUTING,
VERIFYING, WAITING_FOR_APPROVAL, SPEAKING, COMPLETE, FAILED, PAUSED`

`StateService` owns the single current state and emits `jarvis.state.changed`
with `{previous, state, detail}`. `COMPLETE` / `FAILED` settle back to
`DORMANT` after a configurable hold. Concurrent missions: last transition wins
(acceptable for Phase 1; a per-mission state model is planned for Phase 4).

---

## 5. Routing and the brain

Two paths, chosen per command by `JarvisCore`:

- **Instant** — the rule router recognises the command with certainty
  (`open safari`, `system status`, `hello`) and every target resolves. No model
  call: minimum latency, zero cost.
- **Reasoning** — everything else, plus `open …` with a target that doesn't
  resolve, goes to the `Brain` (`core/brain.py`) on the *fast* model.
  `think: …` / `denk nach: …` selects the *reasoning* model (THINK mode).

The brain loop: frozen system prompt (persona + rules, `core/persona.py`) and
tool list → user turn = `<context>` block (time, device, front window —
marked untrusted) + text → model → tool calls → results back → … → reply.

- Read-only tools run through the Operator directly.
- Side-effecting tools open an **open-ended mission** (`MissionEngine.open` →
  `MissionHandle.act`): each call becomes an act step (Operator, permission
  gate, approvals) and a verify step (Sentinel). Pause / stop / approvals work
  exactly as for planned missions; the model receives what Sentinel verified.
- Every tool schema gets a `purpose` field; the model's one-line purpose is
  shown in the activity stream (`jarvis.reasoning`) instead of exposing raw
  reasoning. It is stripped before execution.
- Tool results go back as JSON data; the system prompt states that text from
  the computer (window titles, app names, files) is untrusted and never
  instructions. The permission gate applies regardless of what the model asks.
- Working memory (`core/conversation.py`): recent exchanges replayed as plain
  text, append-only. Within a turn, model responses are replayed verbatim
  (thinking blocks stay valid); earlier turns never replay reasoning blocks.

Models (`llm/`): `ChatModel` protocol with provider-native messages;
`AnthropicChatModel` uses the official SDK (async, `httpx2`), automatic prompt
caching, server-side refusal fallbacks (`fallbacks: "default"`), effort per
role, and maps every SDK error to a `ModelError` with a user-facing reason.
Defaults: fast = Claude Sonnet 5.5 (effort low), reasoning = Claude Opus 5.5
(effort high) — `config/models.yaml`. The key comes from the environment or
`jarvis/.env` (`SecretStr`, never logged or returned by the API).
`BrainConnector` (`core/connector.py`) connects a key at runtime
(`POST /brain/key` from Settings → Brain): format check → `GET /v1/models/{id}`
→ atomic owner-only write to `.env` → `Brain.set_models` → `brain.changed`.
At startup it verifies an existing key in the background and takes the brain
offline if the provider rejects it.

`RuleBasedRouter` classifies text into:

| Kind          | Complexity       | Path                                          |
|---------------|------------------|-----------------------------------------------|
| `query`       | instant          | read-only tool, direct, no mission            |
| `action`      | instant / mission| mission (Operator act → Sentinel verify)      |
| `conversation`| instant          | composed reply, no tools                      |
| `unsupported` | complex          | brain (reasoning path)                        |

Trivial requests never require a model.

**Why actions become missions even when single-step:** every side-effecting
action has at least three observable steps (act, observe, verify), needs an
audit trail and must be interruptible. A single-tool mission costs nothing
(no model call) and gives the user one consistent place to see what happened.
Read-only queries skip missions entirely. See DECISIONS.md D-006.

---

## 6. Missions

```
Mission { id, number, title, goal, status, priority, current_step,
          steps[], agents[], tools_used[], artifacts[], approvals[],
          result, errors[], verification, trace_id, created_at, updated_at }
MissionStep { id, index, title, kind (action|verify), agent, tool, args,
              status, summary, error, verification, depends_on }
```

Status: `pending → active ⇄ paused | waiting_for_approval → complete | failed | stopped`.

`MissionEngine` runs steps sequentially. Pause/stop take effect at step
boundaries (checkpoints); stopping a mission that waits for approval rejects
that approval. Missions left `active` by a crash are marked `failed`
("interrupted") on startup.

Phase 1 planning is deterministic (`missions/planner.py`): one
*act* + *verify* step pair per target. Model-driven planning arrives in Phase 4.

---

## 7. Agents

JARVIS is the only personality the user sees. Agents are internal workers with
`{id, name, role, instructions, available_tools, max_permission_level, status}`.

| Agent    | Phase 1 status | Responsibility                                      |
|----------|----------------|-----------------------------------------------------|
| Operator | **online**     | executes system tools via `ToolExecutor`            |
| Sentinel | **online**     | independent verification of every action            |
| Atlas    | standby        | research (planned, Phase 6/9)                       |
| Forge    | standby        | coding (planned, Phase 9)                           |
| Vision   | standby        | screen understanding (planned, Phase 5)             |
| Archive  | standby        | memory (planned, Phase 7)                           |

Standby agents are registered (so the UI and protocol are stable) but report
`available: false` and cannot be assigned work.

---

## 8. Tools

```python
class Tool[Args: BaseModel](ABC):
    name; description; permission_level; input_model; side_effects
    def describe_action(args) -> ActionDescriptor            # what exactly will happen
    async def precheck(args, ctx) -> ToolError | None        # impossible/forbidden? fail before asking
    async def execute(args, ctx) -> ToolResult               # act + observe
    async def verify(args, result, ctx) -> Verification | None   # independent re-observation
```

`ToolResult { success, tool, action, target, summary, data, observations[],
observed_result, error{code,message,suggestion}, artifacts[], simulated, duration_ms }`

`ToolExecutor.run()` = validate input → precheck → describe action →
permission gate → emit `tool.started` → execute with timeout → emit
`tool.completed|failed` → audit log. Exceptions never escape as stack traces; they become structured
errors with a user-facing message and a suggestion. Developer detail goes to
the JSON log.

Phase 1 tools: `get_system_info` (L0), `get_active_window` (L0),
`list_running_apps` (L0), `open_application` (L1, target-dependent — see §9).
Unknown applications: Windows L2 (Windows decides what an executable name
runs); macOS L1 (only installed `.app` bundles can be launched).

### System backends

`SystemBackend` is the only place that touches the OS:

- **`WindowsSystemBackend`** — real. `ShellExecuteW` (via `os.startfile`, COM
  initialised on the worker thread) to launch, `EnumWindows` / `GetForegroundWindow` / `GetWindowThreadProcessId` /
  DWM cloaking checks via `ctypes`, UWP frame-host resolution, `psutil` for
  processes, App Paths registry + `PATH` to resolve unknown apps.
- **`MacOSSystemBackend`** — real. `open -a` (LaunchServices) to launch;
  `CGWindowListCopyWindowInfo` (pyobjc Quartz) for on-screen windows in
  front-to-back order; `psutil` for processes, attributed to an app when the
  executable lives inside its `.app` bundle (outermost bundle wins, so helper
  processes count). Only bundles from the standard application folders are
  launched. No privacy permission required; window titles of other apps need
  Screen Recording, otherwise the app name is used.
- **`SimulatedSystemBackend`** — **MOCK**, clearly labelled in code, API and UI
  (`simulated: true`, "SIMULATED" badge). In-memory Windows-style desktop with
  realistic launch latency. Used automatically on other hosts and in tests.
  It exercises the identical tool/verification code path.

`runtime.system_backend: auto` picks windows / macos / simulated by host OS
and loads the matching catalog (`config/apps.windows.yaml` or
`config/apps.macos.yaml`; the simulation uses the Windows catalog).

Preference order for Windows control (Phase 2+): Windows APIs → UI Automation →
pywinauto → keyboard/mouse fallback. Raw coordinate clicking is never primary.

---

## 9. Permissions

| Level | Meaning          | Default policy    |
|-------|------------------|-------------------|
| 0     | read             | auto              |
| 1     | safe action      | auto              |
| 2     | modification     | confirm (configurable) |
| 3     | external effect  | confirm           |
| 4     | high risk        | strong confirm    |

Policy lives in `config/permissions.yaml` (per-level policy, per-tool overrides,
disabled categories — `financial` is disabled by default).

Levels can be **target-dependent**: `open_application` is L1 for Notepad but
the catalog marks shells (PowerShell, Terminal) L2 and Registry Editor L4.
Unknown applications are L2 and a denylist blocks system utilities
(`diskpart`, `format`, `bcdedit`, …) outright.

An approval request carries a full `ActionDescriptor`
`{title, target, summary, effects[], level, reversible}` plus the reason
(the user's original command). The UI never shows a generic "Allow?".
Strong confirmation requires an explicit second confirmation from the client.
Unanswered requests expire (default 5 min) and count as rejected.

---

## 10. Security

- **Localhost only.** The API binds `127.0.0.1`.
- **Origin allowlist.** State-changing HTTP requests and WebSocket handshakes
  from browsers must come from an allowed origin (`app://jarvis`, Vite dev
  server). This blocks drive-by websites from driving JARVIS via
  `localhost`. (A per-session token is planned when remote/mobile clients arrive.)
- **Electron hardening.** `contextIsolation`, `sandbox`, no `nodeIntegration`,
  strict CSP, custom `app://` protocol instead of `file://`, minimal preload.
- **Untrusted content (planned with Phase 3+).** Web pages, documents, tool
  output and screenshots are data, never instructions. Model prompts will wrap
  them in explicit untrusted-content envelopes, and tool calls are authorized
  by user intent + policy, never by text inside content.
- **Audit log.** Every tool execution (incl. denied/rejected) is persisted with
  trace id, mission, agent, tool, redacted argument summary, permission level,
  approval outcome and result. Secrets are redacted by key name.

---

## 11. Storage

SQLite (`data/jarvis.db`) through `storage.Database` (aiosqlite) with
versioned migrations. Tables: `missions` (JSON document + indexed columns),
`events`, `audit_log`. Repositories own all SQL, so a PostgreSQL backend only
touches `storage/` and the repositories. Semantic memory will sit behind a
`VectorStore` protocol (planned, Phase 7) — never called directly elsewhere.

---

## 12. Observability

- JSON lines log at `data/logs/jarvis.jsonl`, human-readable console log.
- Every command gets a `trace_id`; every mission a `mission_id`; both flow
  through `TraceContext` into events, logs and the audit log.

---

## 13. Desktop app

- **Main process** (`apps/desktop/electron/`): creates the window, serves the
  renderer over `app://jarvis`, and supervises the backend — if `/health` is
  not reachable it starts `python -m jarvis` from `backend/.venv` and stops it
  on quit. `/health` reports the backend's git commit (`build`); a JARVIS
  backend running older code (e.g. left over from a previous run) is stopped
  and replaced instead of being reused. The top bar shows the build.
- **Renderer** (`apps/desktop/src/`): React 19 + Tailwind 4 + Motion.
  A pure reducer (`store/reducer.ts`) folds snapshot + events into UI state
  (unit-tested); Zustand exposes it to components.
- **Design tokens** live in `src/styles/tokens.css` (`@theme`). One accent
  color; status colors only for status.

Home hierarchy: 1 JARVIS Core → 2 active mission → 3 command bar →
4 context → 5 agents → 6 activity → 7 secondary metrics.

---

## 14. Extension points (designed, not built)

| Concern          | Boundary                                                         |
|------------------|------------------------------------------------------------------|
| Voice            | emits `voice.*` events + drives `LISTENING/SPEAKING` states      |
| Vision           | `Vision` agent + capture tool; permission-aware, event-driven    |
| Memory           | `Archive` agent + `MemoryStore`/`VectorStore` protocols          |
| Skills / plugins | a skill registers tools, events, config, permissions, UI metadata into the registries |
| MCP              | an MCP client becomes a tool *provider* that registers `Tool`s; core never depends on MCP |
| Proactive engine | triggers → evaluate → permission → suggest → act; off by default |
| Other platforms  | new `SystemBackend` implementation (macOS, Linux)                |
