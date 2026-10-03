# Implementation status

_Last updated: 2026-10-03_

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
- Electron app: backend supervisor, `app://` protocol + CSP, single instance,
  Windows title-bar overlay
- Dashboard: JARVIS Core, state line, active mission, approval card, context,
  memory placeholder (honest), agents, missions, activity stream (debug toggle),
  command bar (history, ⌘/Ctrl+K), mission detail, settings (read-only)
- Tests: 70 pytest (bus, permissions, router, tools, missions, API/WS, settings,
  protocol sync) · 9 Vitest (reducer, formatting) · 10-step Electron E2E

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

## IN PROGRESS
- —

## NEXT (Phase 2 — real system control)
1. Exercise the dashboard on the MacBook (`open safari / finder / terminal`,
   active window, running apps); on Windows: `scripts/smoke.py notepad`
2. Volume (`get_volume` / `set_volume`, Core Audio)
3. Window management via UI Automation (focus / minimize / maximize / close)
4. File tools (`list_directory`, `read_file`, `search_files`) with path allowlist
5. Context: clipboard metadata, cursor, monitors, window list in the UI
6. Settings page: edit permission policy

## BLOCKED
- Real-OS runs could not happen in the development container (Linux).
  Windows: type-checked against the Windows API stubs. macOS: type-checked for
  darwin; bundle detection, app index, launch errors and verification are
  tested with real processes inside fake `.app` bundles, and the full smoke
  test passed on a real Mac (see above). Windows still needs its first real run.
- Windows-only scripts (`setup.ps1`, `dev.ps1`) were written but not executed.
