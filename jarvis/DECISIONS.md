# Architectural decisions

Format: Decision · Reason · Alternatives considered · Consequences · Date

---

## D-001 — JARVIS lives in `jarvis/` inside this repository

- **Decision:** The project is a self-contained folder `jarvis/` in the
  `Valerio-OS` repository. On Windows it is used from the clone or copied to
  `C:\dev\jarvis`.
- **Reason:** The repository already contains an unrelated Next.js app
  ("Vitality") at its root. Development happened in a Linux cloud container,
  so `C:\dev\jarvis` could not be created directly.
- **Alternatives:** Replace the root project (destructive); separate repository
  (not available in this session).
- **Consequences:** All paths in JARVIS are relative to `jarvis/`. Moving it to
  its own repository later is a plain folder move.
- **Date:** 2026-10-03

## D-002 — Backend package is `backend/jarvis/` (not `backend/api` etc. at top level)

- **Decision:** One installable Python package `jarvis` with sub-packages
  `api, core, missions, agents, tools, permissions, events, storage, llm (planned),
  observability`.
- **Reason:** Clean absolute imports (`jarvis.tools…`), editable install,
  `python -m jarvis`, no `sys.path` hacks.
- **Alternatives:** Top-level packages directly under `backend/` as in the
  original sketch.
- **Consequences:** `backend/` holds packaging/tests; code lives in `backend/jarvis`.
  Voice, vision and memory packages are created when their phase starts rather
  than as empty folders.
- **Date:** 2026-10-03

## D-003 — Single `packages/protocol` instead of `shared-types` + `protocol`

- **Decision:** One TypeScript package for event types, domain models and API
  types; a pytest (`test_protocol_sync.py`) fails when Python and TS event
  types / states / severities drift.
- **Reason:** Two packages would split one contract. Code generation from
  Pydantic JSON Schema is possible later but not worth a toolchain yet.
- **Alternatives:** Generated types (openapi-typescript); hand-written types in
  the app.
- **Consequences:** Model shapes beyond enums are kept in sync by review and
  the E2E test; move to generation when the API grows.
- **Date:** 2026-10-03

## D-004 — Rule-based router in Phase 1

- **Decision:** Deterministic regex router for the small Phase 1 command set,
  behind a `Router` protocol.
- **Reason:** Instant, free, predictable; no model provider is connected yet.
  The spec requires trivial actions never to wait on deep reasoning — the rule
  path will remain the fast path after Phase 3.
- **Alternatives:** Calling a model for every command.
- **Consequences:** Unsupported phrasing gets an honest "needs model
  integration" reply instead of a guess.
- **Date:** 2026-10-03

## D-005 — Windows launching via ShellExecute (`os.startfile`) + snapshot diff verification

- **Decision:** Launch with ShellExecute (COM initialised on the worker
  thread); prove success by diffing window/process snapshots taken before and
  after (`EnumWindows`, DWM cloaking, UWP frame-host resolution, psutil), then
  re-observe independently in Sentinel.
- **Reason:** ShellExecute behaves like the Run dialog: App Paths, app-execution
  aliases (Windows 11 Notepad), URIs (`ms-settings:`, `spotify:`) and UAC
  elevation all work. The PID returned by `CreateProcess` is unreliable for
  verification (launcher stubs hand off to other processes), so evidence is
  matched by process name and new window handles.
- **Alternatives:** `subprocess.Popen` + PID tracking; pywinauto `Application.start`.
- **Consequences:** No dependency beyond `psutil` for Phase 1. pywinauto / UI
  Automation arrive in Phase 2 for interacting inside windows.
- **Date:** 2026-10-03

## D-006 — Every side-effecting action is a mission; read-only queries are not

- **Decision:** `open notepad` creates a (lightweight) mission with an *act*
  step (Operator) and a *verify* step (Sentinel). Queries (`active window`,
  `system status`) run directly with no mission.
- **Reason:** A side-effecting action is inherently multi-step
  (observe → act → observe → verify), must be auditable and interruptible, and
  the UI needs one consistent place to show it. Without a model call a mission
  costs nothing.
- **Alternatives:** Instant actions bypass missions entirely (spec §14 example).
- **Consequences:** Mission numbers grow quickly; the mission list shows recent
  ones only. If this becomes noisy, single-step missions can be collapsed in the
  UI without changing the backend.
- **Date:** 2026-10-03

## D-007 — Simulated desktop backend for non-Windows hosts (clearly marked MOCK)

- **Decision:** `SimulatedSystemBackend` models windows/processes in memory with
  launch latency. Selected automatically off Windows; every result carries
  `simulated: true`; the UI shows a SIMULATED badge.
- **Reason:** The full chain must be developed and tested end-to-end in CI/Linux
  without faking anything on the user's real machine.
- **Alternatives:** Skip tests off Windows; mock individual tools.
- **Consequences:** Tool, executor, agents, missions and UI run the identical
  code path in simulation; only `tools/system/windows.py` is Windows-specific
  (type-checked with `mypy --platform win32`, smoke-tested with
  `scripts/smoke.py`).
- **Date:** 2026-10-03

## D-008 — Target-dependent permission levels + precheck before approval

- **Decision:** A tool describes each concrete action (`ActionDescriptor`) and
  its level may depend on the target (Notepad L1, PowerShell L2, Registry
  Editor L4, unknown apps L2). `precheck` rejects impossible/forbidden actions
  (denylist, not installed) *before* any approval is requested.
- **Reason:** "Open application" is not uniformly safe; asking to approve
  something that cannot run is noise.
- **Alternatives:** Static level per tool.
- **Consequences:** Approval cards always show a real, executable action.
- **Date:** 2026-10-03

## D-009 — Localhost API protected by an Origin allowlist; Electron serves `app://`

- **Decision:** State-changing HTTP requests and WebSocket handshakes with a
  browser `Origin` must come from `app://jarvis` or the Vite dev server.
  The packaged renderer is served from a privileged `app://` scheme with a CSP
  header (no `file://`).
- **Reason:** Any website could otherwise call `http://127.0.0.1:8765/chat` and
  launch applications. Origin checks stop drive-by attacks without user friction.
- **Alternatives:** Per-session bearer token (planned when remote/mobile
  clients arrive); no protection.
- **Consequences:** Local non-browser clients (curl, tests) still work.
- **Date:** 2026-10-03

## D-010 — Frontend stack versions

- **Decision:** React 19, Vite 8, Tailwind 4 (CSS-first `@theme` tokens),
  Motion 14, Zustand 5, TypeScript 7, Electron 44, Vitest 5, playwright-core
  for Electron E2E.
- **Reason:** Current stable releases at project start; Tailwind 4 makes the
  design-token system native CSS.
- **Consequences:** Vite config is `vite.config.mts` (ESM). Fonts (Inter,
  JetBrains Mono) are bundled locally — no network needed at runtime.
- **Date:** 2026-10-03

## D-013 — Claude as the brain: rules first, Sonnet 5.5 for routine, Opus 5.5 for THINK

- **Decision:** Keep the rule router as the instant path; send everything else
  to Claude Sonnet 5.5 (effort `low`) and `think:` requests to Claude Opus 5.5
  (effort `high`). Manual tool loop over our own `Tool` registry; side effects
  run as open-ended missions (act + verify). Server-side refusal fallbacks on.
- **Reason:** The spec separates a fast brain from a deep brain and forbids
  deep reasoning for trivial actions. A manual loop (instead of the SDK's beta
  tool runner) keeps every call inside the existing permission gate, approval
  flow, mission UI and audit log, and lets Sentinel's verification flow back
  to the model.
- **Alternatives:** Opus 5.5 for everything (simpler, one cache namespace,
  slower and ~2× the cost for chat turns); the SDK tool runner (beta, would
  bypass mission semantics); model-based intent classification for every
  command (adds latency and cost to "open safari").
- **Consequences:** Earlier turns are replayed as plain text only — the
  history stays append-only (cache-friendly) and never replays reasoning
  blocks, which newer models bind to the exact conversation. Without
  `ANTHROPIC_API_KEY` the instant path keeps working and the UI says
  *Brain: Offline*. Models are swappable in `config/models.yaml`.
- **Date:** 2026-10-03

## D-017 — Voice: local wake word, ElevenLabs for speech in and out

- **Decision:** "Hey JARVIS" is detected on the computer with the
  openWakeWord `hey_jarvis` model (ported inference on `onnxruntime`);
  requests and replies go to ElevenLabs (Scribe v2, `eleven_multilingual_v2`,
  voice "Daniel"). Audio is captured in the backend (`sounddevice`), not the
  renderer.
- **Reason:** The user chose quality over local-only. A cloud wake word would
  stream the room's audio all day; a local one keeps everything before
  "Hey JARVIS" on the machine. ElevenLabs gives one key for both directions,
  the most natural voices and German support. Backend capture works while
  the window is hidden and keeps the whole loop testable in Python.
- **Alternatives:** the `openwakeword` package (pulls scipy, scikit-learn and
  an unavailable TFLite runtime on Linux); Porcupine (needs another account
  key); OpenAI Whisper/TTS (good, second choice); macOS `say` + on-device
  dictation (private, robotic, no API for dictation).
- **Consequences:** Needs an ElevenLabs key and credits (free tier ≈ 10 min of
  speech per month). Replies are synthesized whole, then played (~1 s extra);
  streaming comes later. The pre-trained wake-word model is CC BY-NC-SA —
  personal use only. On macOS the app bundle had to gain a microphone usage
  text (bundle version 2, self-reinstall; privacy permissions are asked again).
- **Date:** 2026-10-04

## D-016 — Mac app = loader bundle around the checkout; updates = fast-forward + staged build + relaunch

- **Decision:** `scripts/install-mac-app.sh` turns a copy of the Electron
  runtime into `~/Applications/JARVIS.app` (name, icon, bundle id, privacy
  texts, ad-hoc signature) whose only content is a loader that starts JARVIS
  from the git checkout. The in-app updater fast-forwards the checkout,
  installs changed dependencies, builds into staging folders, swaps them in
  and relaunches.
- **Reason:** Three of the first real-world problems were update mechanics
  (old instance still running, update not started), not JARVIS itself. A
  packaged, self-contained app (electron-builder + PyInstaller'd backend +
  release feed) needs a macOS build machine, signing/notarization and a
  release pipeline — heavy for a single-user system developed from a cloud
  container. The loader keeps one source of truth (the checkout) and makes an
  update exactly what `git pull && npm run dev` did, minus the terminal.
- **Alternatives:** electron-builder `.dmg` + `autoUpdater` (proper
  distribution, later if JARVIS ever ships to others); an AppleScript/shell
  launcher (Dock would still show "Electron", no single-instance semantics);
  keep the terminal workflow.
- **Consequences:** The app depends on the checkout path (it says so if the
  folder is gone). Ad-hoc signatures change when the Electron version changes;
  then macOS asks for privacy permissions again. Updates refuse to run over
  local changes or local commits and say why. Updates are off in `npm run dev`
  (developers use git) and with `JARVIS_UPDATES=off` (tests).
- **Date:** 2026-10-04

## D-015 — Phase 2 tools: permission-free OS paths, folder allowlist, exposure rule

- **Decision:** Web, file, window and sound tools use only mechanisms that
  need no macOS privacy permission where one exists (`open`,
  `NSRunningApplication`, `set volume`); music control uses AppleScript to
  Spotify / Apple Music (one Automation prompt) and only addresses players
  that already run. File tools see only `config/files.yaml` roots, resolve
  paths before checking and refuse secrets, hidden files and programs. A
  request that has read file contents needs approval for anything that can
  send data out (`open_url`, `search_web`).
- **Reason:** Each permission prompt is friction and a reason to distrust the
  system; Accessibility/Screen-Recording-based control is for Phase 5
  (vision). Reading files makes JARVIS useful but turns file text into an
  injection channel; combined with a URL opener it could leak data. Gating
  the outbound step (instead of the read) keeps reading cheap and makes the
  risky combination visible on the approval card.
- **Alternatives:** Spotlight (`mdfind`) for search (faster, but macOS-only
  and indexes everything incl. Library); approval for every `read_file`
  (safe but tiring); media keys via CGEvent (needs Accessibility); `pycaw`
  for Windows volume (extra dependency, untested here).
- **Consequences:** Windows volume level and media state can't be read, so
  those results are honestly *unverifiable*. Minimising a single window on
  macOS (vs. hiding the app) waits for Accessibility. `pypdf` is a new
  dependency — `scripts/preflight.mjs` now installs changed dependencies on
  `npm run dev`, so updating stays `git pull && npm run dev`.
- **Date:** 2026-10-04

## D-014 — The API key is connected from the dashboard, verified, then stored in `.env`

- **Decision:** `POST /brain/key` (Origin-guarded) takes a pasted key, checks
  it with `GET /v1/models/{id}` for each configured model, writes it to
  `jarvis/.env` (atomic, mode 0600) and swaps the brain's models in place.
  Keys from the environment / `.env` are verified once at startup; a key the
  provider rejects takes the brain offline with the reason, network errors
  don't.
- **Reason:** Editing a dotfile from a terminal and restarting proved to be
  the main obstacle on the first real Mac run — with no feedback on whether
  the key was read, mistyped or rejected. The models endpoint authenticates
  the key and resolves the model without spending tokens.
- **Alternatives:** OS keychain (better at rest, but a second source of truth
  and a native dependency per OS; possible later behind the same endpoint);
  verifying with a 1-token message (would also catch an empty balance, but
  costs money and depends on per-model parameters); keep "edit `.env` and
  restart".
- **Consequences:** The key is never logged, emitted or returned — only its
  last four characters (`key_hint`). An empty credit balance is only seen on
  the first real request and is reported as a billing problem there. A key in
  the shell environment still wins over `.env` after a restart.
- **Date:** 2026-10-03

## D-012 — Real macOS backend (`open -a` + CGWindowList + bundle-aware psutil)

- **Decision:** Add `MacOSSystemBackend` and a macOS app catalog; `auto`
  selects it on macOS. Launch with `open -a <bundle>`, observe windows with
  `CGWindowListCopyWindowInfo` (pyobjc-framework-Quartz, macOS-only
  dependency) and attribute processes to apps via their `.app` bundle path.
- **Reason:** The primary machine turned out to be a MacBook; on it everything
  was simulated. These APIs need no privacy permission (unlike AppleScript /
  System Events or Accessibility), and `NSWorkspace.runningApplications` is
  stale without a Cocoa run loop, whereas CGWindowList and psutil are queried
  fresh every time.
- **Alternatives:** AppleScript via `osascript` (permission prompts),
  NSWorkspace (stale state in a non-Cocoa process), raw ctypes to CoreGraphics
  (untestable, error-prone).
- **Consequences:** Window titles of other apps appear only with the Screen
  Recording permission; without it the app name is shown. If pyobjc is
  missing the backend falls back to the simulation and logs why. Everything
  except the single Quartz call is covered by tests on any OS.
- **Date:** 2026-10-03

## D-011 — The core shrinks when a mission or approval needs the stage

- **Decision:** The JARVIS Core is 300 px when idle and animates to ~150 px
  while a mission card or approval request is shown.
- **Reason:** At 1480×920 the full picture (core, state line, mission/approval
  card, activity) must fit without scrolling; hierarchy stays core → mission →
  command.
- **Consequences:** Size derives from viewport height; inner layers are
  percentage-based so the visual scales cleanly.
- **Date:** 2026-10-03
