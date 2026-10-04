# JARVIS

A personal intelligence operating system: one coherent intelligence between you
and your computer that understands, plans, delegates, acts, **verifies** and
reports — with every step visible in a calm command center.

**Status:** Phase 1 (foundation) and Phase 3 (model intelligence) complete,
real system control on Windows and macOS — see [ROADMAP.md](ROADMAP.md) and
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

```
"Open Notepad."
 → intent classified → Mission 001 → Operator launches Notepad (observed before/after)
 → Sentinel re-observes independently → verified → "Notepad is open."
```

| Idle | Verified action |
|---|---|
| ![JARVIS online](docs/screenshots/online.png) | ![Notepad opened and verified](docs/screenshots/notepad-verified.png) |
| **Approval request** | **Failure, explained** |
| ![Approval card](docs/screenshots/approval.png) | ![Failure](docs/screenshots/failure.png) |

_Screenshots from the Electron E2E run (simulated desktop on Linux)._

## What works today

- **Desktop command center** (Electron + React): JARVIS Core visual with explicit
  states, active mission with live steps, approval requests, context, agents,
  missions, activity stream, command bar, mission detail, settings.
- **Conversation**: everything you typed or said and every answer stays
  visible as a chat on Home — across restarts and updates.
- **Learning (trading research)**: JARVIS studies scalping and day trading on
  NQ and XAUUSD on its own — Claude writes strategies, JARVIS backtests them on
  minute data, and only results that hold up on data Claude never tuned on
  count. $3/day limit; it stops itself when nothing new holds up. Learning
  page; ask "what have you learned?" in the chat.
- **Backend** (FastAPI): event bus + WebSocket stream, state service, router,
  missions with pause/resume/stop, Operator + Sentinel agents, tool framework,
  permission levels 0–4 with approvals, SQLite persistence, audit log,
  structured JSON logs with trace/mission ids.
- **Real system control on Windows and macOS** — every action observed before
  and after and verified independently:
  - apps & windows: open (or bring to front), hide, quit (asks first), what's
    in front, what's running, system status
  - web: open pages, search Google / YouTube / Maps / Wikipedia
  - files in Desktop, Documents, Downloads: find, list, read (text, PDF,
    Word, RTF), open — never secrets, hidden files or programs
  - sound: volume, mute, play / pause / skip in Spotify or Apple Music
  - Windows: ShellExecute, EnumWindows/DWM, UWP-aware.
  - macOS: `open -a` (LaunchServices), CGWindowList + bundle-aware process
    detection. No privacy permission needed; with Screen Recording allowed,
    window titles of other apps become visible too.
- **Reasoning (Claude)**: anything beyond the built-in commands goes to Claude
  Sonnet 5.5, which plans and calls the same tools through the same
  Operator → approval → Sentinel chain (side effects become verified missions).
  Prefix `think:` / `denk nach:` for Claude Opus 5.5. Needs an Anthropic API
  key (Settings → Brain); without it the dashboard shows *Brain: Offline*.
- **Instant commands (no model call)**: `open <app>` / `open <app> and <app>`
  (also German *öffne*, *starte*), `what's the active window`,
  `list running apps`, `system status`, `hello`, `help`.

On other hosts (Linux) the desktop is **simulated** (clearly badged in the UI);
CPU/RAM/host metrics are always real.

## Talk to JARVIS

1. Create an account and an API key at <https://elevenlabs.io> (the free plan
   is enough to try it).
2. **Settings → Voice**: paste the key, **Connect**.
3. Say **"Hey JARVIS"** (you'll hear a short chime), then your request — or
   click the microphone next to the command bar. JARVIS answers out loud.

"Hey JARVIS" is recognised on the computer; audio goes to ElevenLabs only
after it (or after a click). The first time, macOS asks whether JARVIS may use
the microphone. Settings → Voice turns the wake word off, makes JARVIS speak
replies to typed commands too, and plays a sample. Voice and model are in
`config/voice.yaml`.

## Let JARVIS learn

1. Connect Claude (below) — learning uses the same key.
2. Open **Learning** in the top bar and click **Start learning**.
3. The first start downloads about five years of free minute data for NQ and
   XAUUSD from Dukascopy (a few minutes). Then a research round runs every
   15 minutes until the day's $3 are used up.

It only learns: no broker, no orders. Budget, models, costs per trade and the
test periods are in `config/learning.yaml`. A finding counts as validated only
after it passed in-sample and out-of-sample; the holdout result (shown to you,
never to Claude) says whether it also held on unseen data. After 20 rounds
without a new validated finding learning stops itself — start it again to
give it another 20.

## Connect Claude

1. Create an API key at <https://console.anthropic.com> (Settings → API keys).
2. In JARVIS click *Brain: Offline — connect* (or open **Settings → Brain**),
   paste the key, press **Connect**.
3. JARVIS checks the key with Anthropic (free models endpoint, no tokens),
   stores it in `jarvis/.env` (git-ignored, owner-only) and switches the brain
   on immediately — no restart. The context panel shows *Brain: Claude Sonnet 5.5*.

Alternatively put `ANTHROPIC_API_KEY=sk-ant-...` into `jarvis/.env` (or the
environment) and restart; the key is verified at startup and a rejected key
shows up as *Offline* with the reason. Models, effort and limits are in
`config/models.yaml`.

## Requirements

- Windows 10/11 or macOS, Python **3.12+**, Node.js **20+**

## Setup & run (Windows)

```powershell
git clone https://github.com/valeriohartmann777-jpg/Valerio-OS C:\dev\Valerio-OS
cd C:\dev\Valerio-OS\jarvis          # or copy this folder to C:\dev\jarvis
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
npm run dev
```

`npm run dev` starts Vite and Electron; Electron starts the Python backend from
`backend\.venv` automatically (or reuses one already running on port 8765) and
stops it when you quit. After `git pull`, just run `npm run dev` again: new
dependencies are installed automatically, and a running JARVIS of another
version hands over to the new one by itself (the same version only brings its
window to the front).

Production-style run (built renderer served over `app://`):

```powershell
npm start
```

Backend only (e.g. for API work): `backend\.venv\Scripts\python -m jarvis`
→ http://127.0.0.1:8765/docs

Try the real system tool without the UI:

```bash
backend/.venv/bin/python scripts/smoke.py textedit          # macOS
backend\.venv\Scripts\python scripts\smoke.py notepad        # Windows
```

## Setup & run (macOS / Linux)

macOS ships Python 3.9; install a current one first (`brew install python@3.13`
or the installer from python.org).

```bash
git clone -b claude/jarvis-foundation-mxnsz4 https://github.com/valeriohartmann777-jpg/Valerio-OS.git ~/dev/Valerio-OS
cd ~/dev/Valerio-OS/jarvis
./scripts/setup.sh && npm run dev
```

### Install as a Mac app (recommended)

```bash
cd ~/dev/Valerio-OS/jarvis
./scripts/install-mac-app.sh
```

This creates `~/Applications/JARVIS.app` (Dock, Spotlight — no terminal
needed afterwards). It starts JARVIS from this folder, so it stays current:
when a new version is available, the top bar shows **Update** — one click
downloads it, installs changed packages, rebuilds and restarts JARVIS. Your
key in `jarvis/.env` and all data stay where they are. macOS asks once more
for folder and music permissions, now for "JARVIS" instead of Terminal.

On macOS JARVIS controls the real desktop: `open textedit`, `open safari`,
`öffne den rechner`, `open spotify`, `open terminal` (asks for approval), or any
installed app by name. Commands: `what's the active window`, `list running apps`.

macOS asks once before JARVIS may look into Desktop, Documents or Downloads
("Terminal would like to access files…" while you run it from the terminal)
and once before it may control Spotify or Music (Automation) — allow both.
If you declined, JARVIS says so and points to System Settings → Privacy &
Security.

## Tests

```bash
scripts/check.sh                      # ruff, mypy (+win32), pytest, typecheck, vitest
npm run build && npm run test:e2e     # drives the real Electron app (xvfb-run -a on Linux)
```

## Layout

```
apps/desktop/        Electron main (electron/) + React renderer (src/)
backend/jarvis/      api · core · missions · agents · tools · permissions · events · storage · observability
packages/protocol/   typed event/API contract shared with the UI
config/              jarvis · permissions · personality · apps · models (.yaml)
docs/                implementation status
scripts/             setup, dev, checks, real-system smoke test
tests/e2e/           Electron end-to-end test
data/                SQLite + logs at runtime (git-ignored)
```

Architecture: [ARCHITECTURE.md](ARCHITECTURE.md) · Decisions: [DECISIONS.md](DECISIONS.md)

## Configuration

- `config/jarvis.yaml` — server, runtime timings, storage
- `config/permissions.yaml` — policy per level, overrides, disabled categories
- `config/apps.windows.yaml`, `config/apps.macos.yaml` — application catalogs
  (aliases incl. German, launch targets, process names, risk level)
- `config/personality.yaml` — tone and reply templates
- `config/models.yaml` — Claude models, effort and limits
- `config/files.yaml` — folders the file tools may see, never-read patterns
- `config/web.yaml` — search engines for `search_web`
- `config/voice.yaml` — voice, speech models, wake-word sensitivity
- `.env` — overrides and the Anthropic API key (written by Settings → Brain); see `.env.example`
