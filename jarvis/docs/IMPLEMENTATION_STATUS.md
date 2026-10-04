# Implementation status

_Last updated: 2026-10-04_

## DONE

### Phase 1 — Foundation
- Repository structure, ARCHITECTURE / ROADMAP / DECISIONS / README
- Central config (`config/*.yaml` + env overrides), `.env.example`
- Event bus (wildcards, isolation, bounded streams, history) + SQLite event store
- `StateService` with all 13 JARVIS states and auto-settle to DORMANT
- Permission model: levels 0–4, policies, per-tool overrides, disabled
  categories, approval requests with expiry, strong confirmation
- Tool framework: `Tool` interface (`describe_action`, `precheck`, `execute`,
  `verify`), registry, executor (validation, permission gate, timeout,
  structured errors, events, audit log)
- System tools: `open_application`, `get_active_window`, `list_running_apps`,
  `get_system_info`
- `WindowsSystemBackend` (real), `MacOSSystemBackend` (real) and
  `SimulatedSystemBackend` (MOCK, labelled); per-platform app catalogs
- Agents: registry + Operator (acts) + Sentinel (verifies); Atlas, Forge,
  Vision, Archive declared as standby
- Rule-based router (query / action / conversation / unsupported; EN + DE verbs)
- Deterministic planner (act + verify per target), mission engine with
  pause / resume / stop, approval awareness, persistence, crash recovery
- Response composer driven by `personality.yaml`
- FastAPI REST + WebSocket `/events` (snapshot then live events), Origin guard
- Structured JSON logging with `trace_id` / `mission_id`
- Electron app: backend supervisor (replaces backends of older versions),
  `app://` protocol + CSP, single instance with automatic takeover by a newly
  started different version, Windows title-bar overlay
- Dashboard: JARVIS Core, state line, active mission, approval card, context,
  memory placeholder (honest), agents, missions, activity stream (debug toggle),
  command bar (history, ⌘/Ctrl+K), mission detail, settings (read-only)
- Tests: 70 pytest (bus, permissions, router, tools, missions, API/WS, settings,
  protocol sync) · 10 Vitest (reducer, formatting) · 14-step Electron E2E

### Verified in this environment (Linux container, simulated desktop)
- `npm run test:e2e`: app launch → backend auto-start → ONLINE → “Open Notepad.”
  chain → approval (L2) → strong confirmation (L4) → rejection → failure
  explanation → mission detail → settings → idle → backend stopped on quit
- `mypy --platform win32` passes for the Windows backend

### Verified on real hardware
- 2026-10-03, MacBook Air, macOS 26 (Darwin 25.6): `scripts/smoke.py textedit`
  → resolved `/System/Applications/TextEdit.app`, `open -a` launch, outcome
  `brought_to_front` (TextEdit already had a window), Sentinel `verified`
  (1 window, 1 process, has focus). First Quartz import took ~7 s (cold
  bytecode compile); it happens once at backend start.
- 2026-10-04, same MacBook, first live run with Claude: key connected from
  Settings → Brain ("Brain connected — Claude Sonnet 5.5"). "Was läuft gerade
  auf meinem Mac?" → `list_running_apps` → answer in German (9 apps with
  windows, 562 processes). "Ich muss mir schnell was notieren" → Claude chose
  TextEdit → mission → Sentinel verified. Found: the dashboard itself showed as
  "Electron" (fixed: recognised via `JARVIS_UI_PID`, labelled JARVIS).
- 2026-10-04, after the update: automatic takeover (new version started while
  the old one ran → old one handed over), dashboard labelled JARVIS, and
  `think:` → Claude Opus 5.5 all confirmed on the MacBook.

### Phase 3 — Model intelligence
- `llm/`: provider-neutral `ChatModel`, `AnthropicChatModel` (SDK 1.11, async),
  `build_models` from `config/models.yaml` + `ANTHROPIC_API_KEY` (env / `.env`)
- `Brain`: tool loop, `purpose` narration, open-ended verified missions,
  untrusted-data handling, step limit, refusal / truncation handling
- Routing: instant rules vs. reasoning; `think:` → Opus 5.5
- Working memory (recent exchanges), persona system prompt
- Dashboard: Brain status in context panel and settings; THINKING state copy
- Tests: 15 brain tests (scripted model), 14 provider tests (real SDK against a
  mock HTTP transport: request shape, verbatim replay, 9 error classes),
  settings/secret tests; E2E covers the no-key path
- Connect from the dashboard: Settings → Brain takes a pasted key, verifies it
  against the models endpoint, stores it in `.env` (owner-only, atomic) and
  swaps the models at runtime (`brain.changed` event); keys from env/`.env`
  are verified at startup, a rejected key turns the brain *Offline* with the
  reason. Empty credit balance is reported as a billing problem.
  Tests: 17 connector/API tests, 3 provider key-check tests, E2E covers the
  refused-key path
- Live API verified on the MacBook (see above), fast and think paths

### Phase 2 — real system control (first batch)
- Web: `open_url` (http/https only, local network needs approval),
  `search_web` (engines in `config/web.yaml`); verified by a browser in front
- Files: `find_files`, `list_folder`, `read_file` (text, PDF via pypdf,
  Word/RTF via textutil on macOS), `open_file` (programs refused, unknown
  types need approval); folder allowlist with path resolution and secret
  patterns (`config/files.yaml`)
- Windows: `hide_application`, `quit_application` (L2, graceful; JARVIS and the
  desktop shell protected); macOS via `NSRunningApplication`, Windows via
  `ShowWindow` / `WM_CLOSE`
- Sound: `get_volume`, `set_volume`, `media_control`, `now_playing` (macOS
  `osascript`; Windows volume/media keys, reported as unverifiable)
- Exposure rule: after `read_file`, `open_url` / `search_web` need approval
- Operator may run every registered tool (drift test); persona and help text
  updated; Settings shows the visible folders
- `scripts/preflight.mjs` installs changed Python/npm dependencies on start
- macOS folder permission (Files & Folders) denials are reported with the fix,
  never as "no matches"
- Tests: 189 pytest (web 16, files 18, windows 4 + macOS integration with real
  processes, media 8, brain-level prompt-injection test), 14-step E2E
- 2026-10-04: running on the MacBook (build 1098535); the user reports the
  test prompts (YouTube search, Downloads, invoice, volume, music, hide app)
  working

### Mac app and in-app updates
- `scripts/install-mac-app.sh`: `~/Applications/JARVIS.app` (icon, name,
  bundle id, privacy texts, ad-hoc signature), loader that runs the checkout
- App icon: `apps/desktop/assets/icon.svg` → `icon.png` (also the Dock icon
  in `npm run dev` / window icon on Windows/Linux)
- Updater: check at start + hourly, top-bar *Update* with progress, Settings
  row; fast-forward only, staged build (`scripts/build-app.mjs`, ~2 s),
  dependency sync, relaunch; errors shown with output
- Launch modes share one settings folder/lock; the app replaces a
  terminal-started JARVIS
- Installs never rewrite `package-lock.json` (`--no-save`); a lockfile changed
  locally by another npm version is reset before an update instead of
  blocking it (any other local change still blocks, with the reason shown)
- Tests: 7 updater tests against real git repositories (current, update +
  steps, local changes, regenerated lockfile, local commits, failed build,
  offline / no upstream);
  E2E: loader start + takeover, and a full update from a temporary remote
  through the UI including the restart (16 steps total)
- 2026-10-04: installed on the MacBook with `scripts/install-mac-app.sh`
  (`codesign`, `iconutil`, `sips` ran fine); JARVIS.app runs and replaced the
  terminal-started JARVIS. The first real in-app update (to ff189f0) went
  through: Update → download, packages, build → restart on the new version.

### Voice (Phase 8, first version)
- Wake word "Hey JARVIS": openWakeWord inference ported to onnxruntime
  (identical scores to upstream on the same audio), models downloaded once
- Push-to-talk microphone button; energy endpointing with calibration and
  pre-roll; chime on wake
- ElevenLabs Scribe v2 (raw PCM) and TTS (`pcm_24000`, voice Daniel); key
  connect + verify from Settings → Voice; quota/permission/key errors explained
- Spoken replies for spoken requests (typed optional); no self-wake while
  speaking; LISTENING / UNDERSTANDING / SPEAKING states; `voice.changed` events
- Mac app: microphone usage text, bundle version 2, self-reinstall on start
  (once); mic stays closed while the bundle can't use it
- Tests: wake word with the real models, endpointing, ElevenLabs client
  (mock transport), the whole voice loop with fakes (wake → mission → spoken
  reply, push-to-talk, silence, errors, key connect, API), E2E setup step
- Not yet run on the MacBook (needs an ElevenLabs key; mic/speaker are real only there)

## IN PROGRESS
- —

## NEXT


0. Voice on the MacBook: connect ElevenLabs, "Hey JARVIS", push-to-talk

### Phase 2 — remaining
1. Instant rules for common commands ("lauter", "pause", "nächster Song")
2. Context: clipboard metadata, monitors, window list in the UI
3. Settings page: edit permission policy and file roots
4. Windows: first real run (`scripts/smoke.py notepad`)

## BLOCKED
- Real-OS runs could not happen in the development container (Linux).
  Windows: type-checked against the Windows API stubs. macOS: type-checked for
  darwin; bundle detection, app index, launch errors and verification are
  tested with real processes inside fake `.app` bundles, and the full smoke
  test passed on a real Mac (see above). Windows still needs its first real run.
- Windows-only scripts (`setup.ps1`, `dev.ps1`) were written but not executed.
