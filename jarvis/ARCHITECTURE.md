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

| Group | Tools (level) | Verified by |
|---|---|---|
| System | `get_system_info`, `get_active_window`, `list_running_apps` (L0); `open_application` (L1, target-dependent — §9); `hide_application` (L1); `quit_application` (L2) | windows / processes re-observed |
| Web | `open_url`, `search_web` (L1; local-network URLs L2) | a browser window in front |
| Files | `find_files`, `list_folder`, `read_file` (L0); `open_file` (L1, unknown types L2) | a window showing the file / its app |
| Media | `get_volume`, `now_playing` (L0); `set_volume`, `media_control` (L1) | volume / player state read back |

Unknown applications: Windows L2 (Windows decides what an executable name
runs); macOS L1 (only installed `.app` bundles can be launched). Every tool
the model can call must be in the Operator's `available_tools`
(`agents/catalog.py`) — a test fails when they drift apart.

**Files** (`tools/files.py`): `FileAccess.check` resolves every path (`~`,
`..`, symlinks) and requires it to lie inside a root from
`config/files.yaml` (Desktop, Documents, Downloads by default); hidden files
and blocked patterns (`.env`, keys, password files — accent-insensitive) are
refused. `read_file` extracts text from plain text, PDF (`pypdf`), Word/RTF
(`textutil` on macOS, a `.docx` reader elsewhere) and cuts it at
`max_read_chars`. `open_file` never opens programs or scripts (by extension
or executable bit).

**Exposure rule** (`ToolExecutor`): a tool with `reads_private_data`
(`read_file`) adds the item to `TraceContext.exposed`, a set shared by every
context derived from one request. A tool with `sends_data_out` (`open_url`,
`search_web`) is raised to level 2 while that set is non-empty, and the
approval card says why. Text inside a file can therefore never silently make
JARVIS carry the file's content to a website.

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
  Screen Recording, otherwise the app name is used. `open <url|path>` for web
  pages and documents; `NSRunningApplication` (AppKit) to hide/quit apps — the
  Dock's own mechanism, no permission prompt; volume via `osascript` (`set
  volume`, no permission); Spotify / Apple Music via AppleScript, which macOS
  asks the user to allow once (Automation). Only players that are already
  running are addressed.
- Windows (same class as above): `ShellExecute` for URLs/documents,
  `ShowWindow(SW_MINIMIZE)` and `WM_CLOSE` for hide/quit, volume and media
  keys via `keybd_event` — the level and player state can't be read there, so
  those results are reported as unverifiable instead of verified.
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

### Voice (`backend/jarvis/voice/`)

```
mic (sounddevice, 16 kHz, 80 ms frames)
  └─ wake mode: OpenWakeWord ("hey jarvis", local)  ──score ≥ 0.5──┐
  └─ push-to-talk (POST /voice/listen) ───────────────────────────┤
                                                                   ▼
        UtteranceRecorder (energy VAD, pre-roll, pause/length end) → PCM
        → ElevenLabs Scribe v2 (raw PCM) → strip "Hey Jarvis" → core.submit(source="voice")
        → jarvis.message for that trace → ElevenLabs TTS (pcm_24000) → speaker
```

- `wakeword.py` ports openWakeWord's streaming inference (melspectrogram →
  speech embedding → `hey_jarvis` model) to `onnxruntime` only; checked
  against the upstream package (identical scores). Models (~4 MB) are
  downloaded once to `data/models`. The first model window after a reset
  reports 0 (priming noise otherwise causes spikes).
- The mic is open only while the wake word is on or a request is recorded.
  While JARVIS speaks, wake detection pauses (no self-wake) and resets after.
- States: `LISTENING` while recording, `UNDERSTANDING` while transcribing,
  `SPEAKING` while playing; `voice.changed` events carry `VoiceStatus`.
- `VoiceService` is built from injectable parts (audio device, detector,
  provider) — the tests drive the whole loop with fakes.
- macOS kills a process that opens the microphone when the responsible app
  lacks `NSMicrophoneUsageDescription`. The app bundle carries it (bundle
  version 2); Electron passes `JARVIS_MIC=0` while the installed bundle is
  older, and reinstalls the bundle itself on start (once, no loops).

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
- **Untrusted content.** Window titles, file contents and tool output are data,
  never instructions: the system prompt says so, `read_file` results carry a
  note, and tool calls are authorized by user intent + policy, never by text
  inside content. After private data was read in a request, anything that can
  send data out needs approval (exposure rule, §8).
- **File allowlist.** File tools see only the configured roots; paths are
  resolved before checking, so `..` and symlinks can't escape; keys, `.env`,
  password files and hidden files are never read or opened.
- **Audit log.** Every tool execution (incl. denied/rejected) is persisted with
  trace id, mission, agent, tool, redacted argument summary, permission level,
  approval outcome and result. Secrets are redacted by key name.

---

## 11. Storage

SQLite (`data/jarvis.db`) through `storage.Database` (aiosqlite) with
versioned migrations. Tables: `missions` (JSON document + indexed columns),
`events`, `audit_log`, `learning_*`, `memories`, `training_runs`. Activity
events older than `storage.event_retention_days` (30) are deleted daily; the
conversation (`command.received`, `jarvis.message`) is kept. Repositories own all SQL, so a PostgreSQL backend only
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
- **Always on**: closing the window hides it — JARVIS keeps running in the
  menu bar (tray on Windows), so learning, training and the briefing go on;
  quit with ⌘Q or *Quit JARVIS* in the menu. The installed Mac app registers
  itself to start at login once (Settings → Always on turns it off). App Nap
  is prevented (`powerSaveBlocker`), and long waits in the backend are
  checked against the wall clock (`util.wait_wall`): asyncio's monotonic
  timers stand still while a Mac sleeps. Linux keeps quit-on-close
  (`JARVIS_BACKGROUND=on` / `off` overrides).
- **Crash recovery**: a backend the app started that exits unexpectedly is
  started again after 1, 3, 10, 30, then every 60 s (quick retries again once
  it ran two minutes). On restart the brain reloads the last day's exchanges
  from the event store, so the chat on screen is still its context.
- **Launch modes**: `dev` (`npm run dev`, Vite), `start` (`npm start`) and
  `app` (the installed `JARVIS.app`). All share one settings folder and so one
  single-instance lock; a newly started different build — or the app replacing
  a terminal-started JARVIS — makes the running one hand over.
- **Mac app** (`scripts/install-mac-app.sh`): a copy of `Electron.app` with
  JARVIS's name, icon, bundle id and privacy texts, signed ad-hoc, in
  `~/Applications`. Its `Resources/app` is a loader
  (`scripts/mac-app/bootstrap.mjs`) that starts JARVIS from the checkout and
  restores `PATH` (node, git, Homebrew) — the bundle never contains JARVIS
  code, so updates need no reinstall.
- **Updates** (`electron/updater.ts`, not in `dev` mode): fetch the tracked
  branch at start and hourly; the top bar offers *Update* when behind. Apply =
  `git merge --ff-only` (never a merge commit, never over local changes) →
  `scripts/preflight.mjs` (changed Python/npm dependencies) →
  `scripts/build-app.mjs` (build into staging folders, swap only on success)
  → reinstall the app bundle only if the Electron version changed → stop the
  backend → `app.relaunch()`. Every failure is shown with its output and
  leaves the running version intact. The E2E test performs a real update from
  a temporary git remote, including the restart.
- **Renderer** (`apps/desktop/src/`): React 19 + Tailwind 4 + Motion.
  A pure reducer (`store/reducer.ts`) folds snapshot + events into UI state
  (unit-tested); Zustand exposes it to components.
- **Conversation**: the chat on Home is not a separate store — it is the
  `command.received` and `jarvis.message` events, read with
  `GET /conversation` (newest first page after each snapshot, older pages on
  demand) and extended live from the event stream.
- **Design tokens** live in `src/styles/tokens.css` (`@theme`). One accent
  color; status colors only for status.

Home hierarchy: 1 JARVIS Core → 2 active mission → 3 command bar →
4 context → 5 agents → 6 activity → 7 secondary metrics.

---

## 13b. Learning (trading research)

`backend/jarvis/learning/` — a background loop (`LearningService`) in which
Claude researches scalping and day trading on NQ and XAUUSD. It only learns:
the research model's tools are `study_levels`, `run_backtest`, `write_note`,
`finish_round` and Anthropic's web search — nothing that touches the computer or a broker.

```
market.py    Dukascopy data API (jetta.dukascopy.com/v1) minute candles → validated,
             throttled, cached (data/market/*.npz)
strategy.py  JSON strategy + rule parser ("close crosses_above or_high(15)")
features.py  numpy evaluation, no look-ahead (tested by truncation)
backtest.py  next-bar entries, stop-first, gaps, costs → trades, stats in R
evaluate.py  in-sample → out-of-sample (Bonferroni bar) → holdout
levels.py    support/resistance studies: touches vs. a matched control (in-sample)
journal.py   SQLite: learning_rounds, learning_tests, learning_notes
prompt.py    system prompt (method, rule language), per-round briefing
service.py   loop: data → budget/stall checks → round → wait
```

- **Focus:** a research focus (default in `config/learning.yaml`: support
  and resistance on NQ and XAUUSD) heads every briefing; the Learning page
  can change it (`learning.focus` in preferences).
- **What the model sees:** in-sample statistics, out-of-sample pass/fail and
  the reason, its own notes, validated findings. Never out-of-sample numbers,
  never the holdout (`journal.prompt_tests`, `validated_for_prompt`).
- **Money:** before every call the day's spend plus a worst-case reserve for
  that call must fit `daily_budget_usd`; spend comes from the API's usage and
  the price table in `config/learning.yaml`.
- **Stagnation:** after `stall_limit` completed rounds without a new
  validated finding the loop disables itself and says so; starting it again
  grants fresh rounds.
- **State:** `learning.enabled` in `data/preferences.json` (resumes after a
  restart); `learning.changed` events carry the status to the UI; the
  Learning page reads `/learning/*`. The chat brain answers questions about
  it with the read-only `learning_report` tool.

---

## 13c. Memory and the morning briefing

- **Memory** (`backend/jarvis/memory/store.py`, migration 5): numbered
  sentences (M1, M2, …) of kind preference / fact / routine / correction.
  Loaded at start, mirrored in memory, listed in the `<context>` block of
  every brain request. Tools `remember` / `forget`; API `/memory`;
  `memory.changed` events feed the Memory panel. `remember` is marked
  `stores_instructions`: after a tool returned private or untrusted text in
  the same request (`reads_private_data`, `returns_untrusted_text`), it needs
  approval.
- **Morning briefing** (`backend/jarvis/briefing/`): `levels.py` computes key
  levels from minute bars (history from the learning cache + today's live
  data, `MarketData.today`), `service.py` annotates them with level studies,
  composes the German text, and posts it as a `jarvis.message`
  (`kind: briefing`, a short `speech` for the voice). Schedule and catch-up
  are checked every minute; settings in `config/briefing.yaml` and
  Settings → Morning briefing (`/briefing`). Tool `market_levels` gives the
  same on request.

## 13d. Model training (`backend/jarvis/training/`)

```
dataset.py   touches of the key levels per session window → features + held/broken
model.py     candidates, baseline, choose on out-of-sample, walk-forward holdout,
             day-clustered t, calibration, permutation importance
store.py     training_runs (migration 7) + the model file data/training/model.pkl
service.py   background loop: new trading day? → sync data → train in a worker
             thread (threadpoolctl limits CPU threads) → keep the model
live.py      odds for the levels near the price now (level_odds tool)
```

- Levels come from `briefing.levels.key_levels` at the start of each session
  window, computed only from bars before it; features use bars up to the
  touching candle's close, labels only bars after it (a test checks that
  rows don't change when later data is cut off).
- Status and the latest report go out as `training.changed` events and in the
  snapshot; API `/training`, `/training/run`, `/training/preferences`.
- `learning_report` includes the model's verdict; the morning briefing adds
  one line about it. `level_odds` scores a level the last candle touched as
  it happened, or imagines the next candle touching it (averaged over past
  candle shapes). The model's number only when it is confirmed for that
  market (D-023).

---

## 13e. Bot Lab (`backend/jarvis/bots/`)

```
mql.py      read/write MQL5 sources (UTF-16/UTF-8, CRLF), inputs, the OnTester deal
            export, exact search/replace edits
mt5.py      find MetaTrader 5 for Mac (app, Wine, prefix, MQL5 folders, Common\Files),
            the test terminal (portable copy), MetaEditor /compile, tester /config runs
stats.py    deals → trades → in-sample / out-of-sample / holdout / unseen statistics,
            monthly table, prop-firm check, $10k scaling
store.py    bots, bot_versions, bot_tests, bot_notes, bot_rounds (migration 8)
prompt.py   what Claude reads and its tools (create_version, backtest, validate, …)
service.py  import, backtests (one MetaTrader run at a time), validation gate,
            install into the user's Experts/JARVIS/, the improvement rounds
```

- One run per backtest covers all periods; Claude gets only in-sample
  numbers, `validate` answers pass/fail, the holdout is stored for the user.
- Status goes out as `bots.changed`; API under `/bots`; `bot_report` lets the
  brain answer "how are my bots doing?".
- The E2E test seeds a fake MetaTrader install whose `wine64` is a script
  playing MetaEditor and the tester, so the real process/path code runs.

## 13f. QuantLab (`backend/jarvis/quantlab/`)

```
spec.py        StrategySpec v0.1 (Pydantic mirror of the handoff schema, unknown fields
               forbidden), canonical JSON + SHA-256, UNSUPPORTED_INSTRUMENT guard
data.py        CSV/Parquet import, column mapping, UTC normalisation (offsets, declared
               IANA zone, DST ambiguity refused), fail-closed QA, Data Passport,
               Parquet snapshot; normalized_sha256 over a library-independent text form
engine.py      reference simulator: exact SMA crossings, next-open fills, Decimal cash,
               ledger (signals, orders, round trips), equity, invariant audit, metrics
validation.py  evidence gates A–E, pre-registered verdict policy, JARVIS assessment
store.py       ql_* tables (migration 9); versions and datasets insert-only (triggers)
service.py     registry, imports, background runs (one at a time, cancellable), staged
               artifacts, reproduce, overview, report text for the brain
```

```
StrategySpec ──validate──▶ ql_strategy_versions (frozen, sha256)
CSV/Parquet ──import/QA──▶ Data Passport + data/quantlab/datasets/<id>/snapshot.parquet
version + dataset ──manifest (sha256 → exp_<id>)──▶ background run
   load snapshot (checksum) → SMA crossings → simulate → audit → metrics (full/train/OOS)
   → gates + verdict → artifacts in .staging-* → rename → ql_artifacts / ql_validations
   events: quantlab.experiment.created → running(stage) → completed | failed | cancelled
```

- Time: timestamps are UTC bar starts; a bar's close is known at
  `bar_start + interval`; fills are the next bar's open (`fill_index =
  signal_index + 1`), so no same-bar fill exists. A signal on the last bar is
  recorded as NOT_EXECUTABLE; an open position is marked, never sold.
- One continuous simulation; the chronological split only attributes bars:
  OOS starts from the equity at the last train close, trades belong to the
  segment they were entered in. No parameter is ever chosen on OOS (there is
  no parameter search in R1 at all).
- Artifacts per experiment: `manifest.json`, `data_qa.json`, `metrics.json`,
  `trades/orders/signals/equity.parquet`, `audit.jsonl`, each with SHA-256 in
  `ql_artifacts`. `results_sha256` covers the exact ledger, equity and metrics.
- API under `/quantlab/*` (no `/api` prefix, as everywhere in JARVIS); ids are
  checked against `^[a-z]{2,3}_[0-9a-f]+$` before they touch a path. There is
  no order, broker or live endpoint; the package imports no network or
  process modules (a test enforces both).
- UI: `pages/QuantLab.tsx` (Overview cockpit, Strategies, Experiments,
  Datasets, Reports) and `components/quantlab/` (Architect, DataLab,
  ExperimentView, EquityChart in plain SVG). It refetches on every
  `quantlab.*` event and after reconnecting.

## 13g. ULTRON (`backend/jarvis/ultron/`)

```
models.py     mission / task states and the allowed task transitions
agents.py     the roster: JARVIS, AXIOM, FORGE, SENTINEL active; six more listed (R2/R3)
plan.py       StrategySpec-like contract for plans: validation, topological order, review rule
policy.py     path globs, argv allowlist, action categories (auto / approval / locked),
              effect signatures
sandbox.py    command runner: no shell, scrubbed env, timeout kills the process group;
              Seatbelt (macOS) / network namespace (Linux) when available
workspace.py  git: repo per mission (new sandbox repo or a JARVIS worktree), worktree +
              branch per task, runtime commits and --no-ff merges, review checkouts, export
tools.py      tool broker: per-agent manifests, every call checked and audited
runner.py     one agent run: model ↔ tools, bounded rounds, pause gate, spend cap
prompts.py    what each agent is told (rules that matter are enforced in code)
scripted.py   test-only scripted model (JARVIS_ULTRON_SCRIPT), labelled in the UI
store.py      ul_* tables (migration 10): missions, tasks, runs, artifacts, approvals, events
service.py    planning, scheduler, produce / review flows, retries, approvals, controls,
              recovery, views
```

```
goal ─▶ JARVIS submit_plan ─▶ validate (code) ─▶ tasks DRAFT/READY
scheduler: READY tasks (≤ max_parallel) ─▶ AXIOM/FORGE in task worktree
   ─▶ runtime commits ─▶ runtime runs checks (sandbox) ─▶ fail: RETRYING (feedback)
   ─▶ pass: patch artifact ─▶ merge into integration branch ─▶ COMPLETE
SENTINEL: fresh review checkout ─▶ runtime re-runs all checks ─▶ agent review
   ─▶ accepted only if checks pass AND verdict pass ─▶ else reopen FORGE (bounded)
all COMPLETE ─▶ final acceptance checks ─▶ report ─▶ export approval (sandbox) / branch (JARVIS)
```

- States are persisted on every transition; events go to `ul_events` (replay)
  and the bus (`ultron.mission.changed`, `ultron.task.changed`,
  `ultron.activity`, `ultron.approval.changed`) for the live dashboard.
- Restart: interrupted runs are recorded as INTERRUPTED; their tasks go back to
  READY and start from a clean tree (uncommitted leftovers are reset — only in
  that task's own worktree).
- Pause clears a gate every agent awaits before its next model call; stop
  cancels the asyncio tasks (killing sandboxed processes) and records which
  tasks were interrupted.
- API under `/ultron/*`; brain tools `ultron_start_mission`, `ultron_status`.
- UI: `pages/Ultron.tsx` (Overview, Mission Control, Agent Matrix, Projects,
  Knowledge, Activity, Permissions) and `components/ultron/` (task graph,
  mission view, artifact viewer with checksum check).

## 13h. QuantLab Institutional Edition (`backend/jarvis/quantlab/{hub,futures}/`)

```
hub/vault.py      OS keystore (keyring) — the only place the Databento key lives
hub/provider.py   Databento adapter (SDK 0.87.0): metadata, symbology, cost, get_range; errors
hub/fixture.py    offline stand-in writing real DBN with synthetic prices (tests / E2E only)
hub/cache.py      raw DBN as delivered + canonical per-UTC-day Parquet (integer prices)
hub/quality.py    QA against CMES/XNYS calendars; rolls; provider conditions; capabilities
hub/service.py    connect · catalog · resolve · quote · approve (bound, capped, single use)
                  · jobs (chunks, cancel, interrupted on restart) · datasets (snapshot+manifest)
futures/sessions.py   exchange_calendars sessions → strategy windows (holidays, early closes, DST)
futures/contracts.py  contract master from definition records (reference table = ASSUMED)
futures/spec.py       FuturesSpec 1.0: ORB / intraday MA, exits, costs, sizing, validation plan
futures/engine.py     event-driven bar engine: ticks, Decimal, orders/fills/trades, known_at
futures/audit.py      independent ledger checks;  metrics.py  ledger-derived metrics
futures/validation.py split · tests · bootstrap · deflated Sharpe · fitness · verdict
futures/architect.py  NL → draft spec via submit_strategy_spec (validated, never auto-saved)
futures/service.py    runs as persistent jobs (manifest hash = run id), artifacts, reports
```

- Tables: `qh_*` (migration 11) and `qr_*` (migration 12). Versions and datasets are
  insert-only (triggers).
- Events: `quantlab.hub.changed`, `quantlab.hub.job`, `quantlab.hub.dataset` and
  `quantlab.research.run`.
- API:
  - `/quantlab/connections/databento*`
  - `/quantlab/data/*`
  - `/quantlab/research/*`
  - `/quantlab/stop-all`

  The approval route needs `confirm: true` and the Origin guard.
- UI: `pages/QuantLab.tsx`, organised as follows.
  - Shell: modes, nav, Stop all.
  - `components/quantlab/hub/DataHub.tsx`.
  - `components/quantlab/research/*`: Studio, Backtest Lab, Validation, Trade Explorer,
    Experiments, Risk & Execution, Reports, charts.
  - The R1 lab lives on as `EquityLab.tsx`.
- Detail: `docs/quantlab/ARCHITECTURE.md`.

## 13i. QuantLab 3.0 Idea-to-Edge (`backend/jarvis/quantlab/{intake,ideas}/`)

```
intake/detect.py     magic bytes → kind/mime/container; size caps; archives refused
intake/urls.py       TikTok/YouTube link parsing, oEmbed metadata only, SSRF guard, ≤ 3 redirects
intake/worker.py     subprocess (sandbox + rlimits): probe · speech · frames (OCR) · image · pdf
intake/media.py      PyAV probe/decode/frame sampling;  asr.py  Moonshine (C API) / Whisper
intake/ocr.py        RapidOCR;  text.py  exact spans;  injection.py  instruction tripwire
intake/store.py      qs_* tables;  service.py  intake, queue, retention, deletion, ready hook
ideas/catalog.py     ambiguous terms with fixed alternatives; unsupported concepts
ideas/claims.py      quoted evidence block; claim validation (verbatim quotes); augment()
ideas/blueprint.py   FuturesSpec + provenance classes, questions, deterministic resolve()
ideas/agents.py      ATLAS / JARVIS / CIPHER prompts and submit tools (run_agent final_tools)
ideas/sentinel.py    boundary + audit reports, independent second engine, narrative guard
ideas/missions.py    MissionService: stages, typed tasks, budgets, waits, approvals, verdict
ideas/evolution.py   diagnose · propose · test · compare (Pareto) · holdout lock
ideas/dossier.py     Markdown + JSON dossier (source facts / test facts / interpretation)
```

- Tables: `qs_*` (migration 13), `qm_*` (migration 14; `qm_trials`/`qm_events` append-only).
- Events: `quantlab.source`, `quantlab.mission`.
- API: `/quantlab/sources*`, `/quantlab/research/missions*`, `/quantlab/research/agents`,
  `/quantlab/strategies/from-source/{id}`. Purchases only via `…/data-approval`
  (`confirm: true`), holdout only via `…/holdout-lock` (`confirm: true`).
- Runtime: `rt.sources`, `rt.quant_missions` (`rt.missions` stays the core MissionEngine);
  `_quant_model(role)` gives Claude per role from `config/ultron.yaml` or the labelled script.
- UI: `components/quantlab/ideas/*` — IdeaInbox (default section), SourceView, BlueprintView,
  ResearchRoom, EvolutionView, DossierView.
- Electron CSP allows keyframe images and the owner's own video from the local backend only.
- Detail: `docs/quantlab/IDEA_TO_EDGE_SPEC.md`, `SOURCE_PROVENANCE_CONTRACT.md`,
  `ULTRON_QUANT_PROTOCOL.md`.

## 13j. AI routing & billing (`backend/jarvis/ai/`)

Every model request goes through `SmartRouter` (D-030). The route order is:

1. Claude plan (official `claude -p` under the owner's claude.ai sign-in, isolated environment,
   tools executed by JARVIS);
2. Claude API (key in the OS keystore, only within approved caps);
3. local model (conversation only);
4. pause.

Consumers declare a capability via `RoleSpec`. The router handles the following:

- Errors are classified as temporary (retry, never pay) or failover reasons; capability
  mismatches go back to the caller.
- A run is pinned to the route it failed over to.
- Every call is booked in `ai_usage`; approvals go to `ai_approvals` (append-only).
- `ai.route` events are emitted.

ULTRON runs keep checkpoints and a tool ledger (`ai_checkpoints`, `ai_tool_ledger`), so a paused
task resumes without repeating a tool. ULTRON and QuantLab missions block on `ai_route` /
`AI_PAUSED` and resume automatically when the probe loop sees a route again.

UI: Settings → AI & Billing and the top-bar route chip. Details:
`docs/AI_ROUTING_ARCHITECTURE.md`, `docs/AI_BILLING_POLICY.md`, `docs/AI_FALLBACK_RUNBOOK.md`.

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
