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
- `WindowsSystemBackend` (real) and `SimulatedSystemBackend` (MOCK, labelled)
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

## IN PROGRESS
- —

## NEXT (Phase 2 — real system control)
1. Run `scripts/windows_smoke.py notepad|calculator|settings` and the app on a
   real Windows 11 machine; fix anything the simulation could not reveal
2. Volume (`get_volume` / `set_volume`, Core Audio)
3. Window management via UI Automation (focus / minimize / maximize / close)
4. File tools (`list_directory`, `read_file`, `search_files`) with path allowlist
5. Context: clipboard metadata, cursor, monitors, window list in the UI
6. Settings page: edit permission policy

## BLOCKED
- Real-Windows verification of `WindowsSystemBackend` could not run in the
  development container (Linux). The code is type-checked against the Windows
  API stubs and exercised through the identical tool path in simulation; it
  still needs one run on Windows (item 1 above).
- Windows-only scripts (`setup.ps1`, `dev.ps1`) were written but not executed.
