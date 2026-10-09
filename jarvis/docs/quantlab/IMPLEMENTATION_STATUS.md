# QuantLab Implementation Status

Updated: 2026-10-09 (T0 — reconnaissance done, implementation starting)
Repo location: `Valerio-OS/jarvis` (the repo root holds an unrelated Next.js project, untouched)
Branch: `claude/jarvis-foundation-mxnsz4`
Stack: Electron + React 19 + Tailwind 4 + Zustand (`apps/desktop`), shared TS types
(`packages/protocol`), FastAPI + Pydantic + aiosqlite backend (`backend/jarvis`), EventBus →
WebSocket, versioned SQLite migrations, pytest / vitest / Playwright-Electron E2E.

## Observed integration points (Plan A: React frontend + Python backend exist)
- Backend domain package: `backend/jarvis/quantlab/` (same shape as `bots/`, `training/`).
- HTTP routes: `backend/jarvis/api/routes.py`, no `/api` prefix (existing convention) → `/quantlab/*`.
- Events: `EventType` in `backend/jarvis/events/types.py` ↔ `packages/protocol/src/events.ts`
  (kept in sync by `tests/test_protocol_sync.py`) → `quantlab.*` namespace.
- Storage: migration 9 in `backend/jarvis/storage/database.py`; artifacts under `data/quantlab/`.
- Composition root: `backend/jarvis/runtime.py`; brain tools via `tools/` registry.
- UI: `View` union in `src/store/reducer.ts`, nav in `components/TopBar.tsx`, page switch in `App.tsx`.

## Test baseline before QuantLab (commit fa257f4)
- Backend: `cd backend && .venv/bin/python -m pytest -q` → 354 passed.
- Full check (`./scripts/check.sh`: ruff, format, mypy ×3 platforms, pytest, tsc, vitest) and
  E2E (`xvfb-run -a npm run test:e2e`, 23/23) passed at fa257f4.
