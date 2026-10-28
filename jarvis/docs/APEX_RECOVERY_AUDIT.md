# JARVIS APEX — recovery audit

Owner's report (2026-10-28, macOS): "the backend is not reachable"; APEX shows XAUUSD
`ANALYSIS BLOCKED / DATA BLOCK` with `STALE` (~26,228 trading minutes), `GAPS` (100 %) and `SPIKES`;
the candidate journal is empty. Binding brief: `JARVIS_APEX_Claude_Code_Recovery_Validation_Masterprompt.md`.

## 0. What could and could not be examined

- **This session runs in a cloud container, not on the owner's Mac.** No Remote Control link, no
  other session on the Mac and no desktop bridge was available. The Mac's logs, database and
  market cache could therefore **not** be read. Everything below was reproduced on the same code
  (branch `claude/jarvis-foundation-mxnsz4`) through the app's real start path — Electron →
  `BackendSupervisor` → `python -m jarvis` — under Linux/xvfb.
- The container's network policy refuses `jetta.dukascopy.com` (and Databento): no real market
  data was downloaded here. Real-data results do not exist yet; nothing in this repository
  claims otherwise.

Rollback point before this recovery: tag `apex-recovery-start` (= `40d2f71`).

## 1. Backend reachability (P0)

### Reproduced

| # | Finding | Evidence | Status |
|---|---|---|---|
| R1 | The backend's error output went only to the Electron main process's console. In the Dock app (`JARVIS.app`) nobody sees it. A crash at start therefore looked like an endless "Connecting" with no reason. | `electron/backend.ts` before the fix: `stdout/stderr → process.stdout` only | fixed |
| R2 | A backend that crashed right after starting was restarted **forever** (1, 3, 10, 30, then every 60 s) — no stop, no diagnosis. | same file, `scheduleRestart` | fixed |
| R3 | The Dock app starts JARVIS from the checkout **without** `scripts/preflight.mjs` (that only runs for `npm start` / `npm run dev`). After a `git pull` that changed `backend/pyproject.toml`, the new Python packages were never installed → `ModuleNotFoundError` at import → R2's loop. `scripts/setup.sh` didn't write the dependency stamp either. | `scripts/mac-app/bootstrap.mjs` loads `dist-electron/main.js` directly; `scripts/setup.sh` | fixed |
| R4 | A port held by another program made the real backend fail to bind (`Errno 98/48 … address already in use`) — again R2's loop, no name of the program holding it. | reproduced with a node HTTP server on the port | fixed |
| R5 | Requests made while the backend was still starting failed with "JARVIS backend is not reachable"; APEX never retried, so the message stayed **after** the header had switched to "System online" — the mismatch the owner saw. | E2E run 2026-10-28 11:36 (screenshot `fail.png` in the session log): header "System online", APEX error box | fixed |
| R6 | "System online" meant only "the event stream sent a snapshot". No health check of database, schema, data folder or APEX stood behind it. | `TopBar.tsx`, `store/reducer.ts` | fixed |

**Not proven for the Mac:** which of R3/R4 (or another cause) took the owner's backend down. The
repaired app now *says* which: the window shows the classified cause, the backend's last lines
and the log file `~/Library/Application Support/@jarvis/desktop/logs/backend-console.log`.

### Fixes

- **Health that means something** (`backend/jarvis/api/health_routes.py`):
  `GET /health/live`, `GET /health/ready` (database query, schema = latest migration, data folder
  write, APEX markets — `ready` 200 / `degraded` 503 naming the failing check),
  `GET /system/capabilities` (markets, sources, `orders: false`).
- **Supervisor** (`apps/desktop/electron/backend.ts`):
  - keeps the backend's last 200 lines (keys redacted) and writes a rotating log file;
  - classifies the cause: `MISSING_DEPENDENCY`, `PORT_IN_USE` (names program and pid),
    `DATABASE`, `PERMISSION`, `NO_PYTHON`, `OLD_BACKEND`, `SLOW_START`,
    `DEPENDENCY_INSTALL_FAILED`, `CRASHED`;
  - installs changed Python packages before starting (same stamp as preflight; `setup.sh` now
    writes it too);
  - stops after 5 quick crashes in a row (state `crashed`), so there is no endless loop. A stable
    run resets the count.
  - IPC `jarvis:backend:status | restart | repair` (own window only).
- **Window:**
  - The header shows "Backend online" only when the event stream is up **and** `/health/ready`
    passed. Otherwise it shows "Backend degraded", "Checking backend", "Connecting" or
    "Backend offline".
  - The `BackendBanner` gives the cause, the remedy, and the buttons Retry, Restart backend,
    Repair, and View diagnostics (state, Python, last exit, log file, last lines).
  - APEX reloads when the backend (re)connects and drops stale "not reachable" errors.

### Before → after

| | Before | After |
|---|---|---|
| Missing package after an update | "Connecting" forever, restart every 60 s | Supervisor installs the changed packages before starting; if still missing: "A Python package of the backend is missing — No module named 'x'" + **Repair**, restarts stop after 5 |
| Port taken | silent loop | "Port 8688 is used by another program — held by node (pid 2257)" |
| Backend killed | reconnects (unchanged) | banner while down, header "Backend offline", then "Backend online" and the banner disappears |
| Header | "System online" from the WebSocket alone | "Backend online" = WebSocket + `/health/ready` |

Evidence: `docs/APEX_TEST_EVIDENCE.md` §1.

## 2. Data (P1) — see `docs/apex/APEX_DATA_DIAGNOSIS.md`

Proven in code and reproduced:
- **GAPS 100 %:** a calculation error, triggered by stale data. Zero missing minutes were
  counted, yet the analysis was blocked.
- **SPIKES:** the check counted returns across the weekend, the daily break and missing minutes.
- **STALE that never heals:** empty and 404 days were stored as "fetched" for good, and failed
  updates were hidden.

All three are repaired without loosening a check. `NO_RECENT_DATA` now blocks with the true
reason. Which silent path froze the owner's cache shows in **APEX → Data** after the next update.
