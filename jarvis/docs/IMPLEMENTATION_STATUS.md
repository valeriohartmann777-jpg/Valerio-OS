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

### Conversation (persistent chat)
- Everything typed or said (`command.received`) and every JARVIS answer
  (`jarvis.message`) stays visible as a chat in the middle of Home, below the
  core — across restarts and updates (read from the event store, new index on
  `events(type, timestamp)`)
- `GET /conversation?limit=&before=` pages backwards; the renderer loads the
  newest 100 after every snapshot and older ones on *Earlier messages*
- Day dividers, spoken requests marked, error reason + suggestion, link to the
  mission, a working indicator while JARVIS is busy; stays on the newest
  message unless you scrolled up
- The core's headline now only states what JARVIS is doing
- Tests: API (order, paging, restart persistence, bad cursor), reducer merge,
  E2E — replies read from the chat, and the chat is still there after a
  restart (18 steps total)

### Learning: self-directed trading research (NQ, XAUUSD)
- Market data: Dukascopy's data API (`jetta.dukascopy.com/v1`, delta-encoded
  JSON per day; decoding checked against dukascopy-node's own fixture),
  instrument codes looked up from Dukascopy's list, throttled (≈4 requests/s),
  OHLC/time/price validation, monthly `.npz` cache with fetched days, retries,
  early stop when the source is down, single failed days fetched again later;
  NQ = USA Tech 100 CFD
- 2026-10-04: the first start on the MacBook got HTTP 503 from the old
  `datafeed.dukascopy.com` .bi5 files — that feed stopped answering in July
  2026 (dukascopy-node issue #254); switched to the data API
- Rule language parsed by a small recursive-descent parser (no code
  execution); 30 features incl. VWAP, opening range, previous session,
  time windows (Asia range), RSI/ATR/EMA; strictly no look-ahead
- Backtest: next-bar entry, stop-first on ambiguous bars, gap fills, costs,
  time stop, session end, trades/day; stats in R incl. by-year
- Scoring: in-sample gates → out-of-sample with Bonferroni bar → holdout
  (never shown to the model); 5 years of 1-minute NQ in under 3 s per test
- Loop: Opus 5.5, up to 3 backtests per round, notes, web search (drops
  itself if the key can't use it), $3/day hard limit from API usage,
  stops itself after 20 rounds without a validated finding, resumes after
  restarts; Learning page, context row, `learning_report` tool for the chat
- The prompt asks for honesty, not survival (D-018; tested)
- Tests: decoding/caching/retries with a mock feed, DST, features vs naive
  versions, look-ahead by truncation, backtest rules, a planted edge is
  validated and noise is not, the loop with a scripted model (budget, stall,
  holdout never in the prompt, invalid strategies, search fallback, errors,
  restart), API, report tool, E2E step (19 steps total)
- Not yet run against the real data API (blocked from the development
  container) — the next start on the MacBook downloads and validates it

### Learning: support and resistance
- New levels in the rule language: pivot_high/pivot_low(n) (confirmed swings,
  no look-ahead), prev_week_high/low (trading weeks from Sunday 18:00 NY),
  round_above/round_below(s), prev_poc/prev_vah/prev_val (previous session's
  volume profile)
- `study_levels`: touches of a level from the right side, held / broken /
  undecided from the touching bar's close, by touch number and year, against
  a matched control group with day-clustered errors; in-sample only
- Calibrated: 100 random-walk studies give z ≈ 0 ± 1 (max 2.2); a rising
  market shows no false support; a planted support gives z ≈ 6 (D-019)
- Research focus (default: S/R on NQ and XAUUSD) in every briefing, editable
  on the Learning page; studies listed there and in `learning_report`
- Tests: new levels incl. look-ahead over two weeks, pivots, weekly levels,
  volume profile, studies (real level, random level, trend, period limits,
  validation), loop with studies and focus, API; E2E edits the focus

### Memory
- Numbered memories (preference / fact / routine / correction) in SQLite,
  in the context of every brain request; `remember` (dedupe, replace) and
  `forget` tools; persona rules (only the user's own words, no secrets,
  corrections become rules)
- Planted memories: after a request read a file or research notes,
  `remember` needs approval (`stores_instructions`; tested with a file that
  tries it)
- Memory panel on Home (list, add, forget), `/memory` API, `memory.changed`
- Tests: store, restart, context in requests, the brain storing a
  preference, the planting attack, API; E2E adds and forgets a memory

### Morning briefing
- Key levels per market from minute data (21 days cached + today live):
  NQ previous regular session high/low/close + POC/VAH/VAL, overnight range;
  gold previous trading day + profile, Asia range; previous week; intact
  15-minute swings; round numbers; coinciding levels merged
- Each level annotated with the best matching level study (side, market,
  kind); German text with German number format; research summary line
- Weekdays 08:00 (local), catch-up until 11:00 after a late start or sleep,
  checked every minute; Settings → Morning briefing (on/off, time, send now);
  system notification when it arrives in the background; spoken as a short
  sentence only
- `market_levels` tool: the same on request in the chat
- Tests: levels against hand-computed values (NQ, gold), swings and merging,
  research notes, text, schedule (catch-up, weekend, once per day), sending,
  API, tool, voice summary; E2E toggles the briefing (20 steps total)
- Not yet run with live data (blocked here) — the first briefing on the
  MacBook is the real test

### Fixes after the first real learning runs (2026-10-05)
- The learning loop ran on the MacBook: 32 tests, 19 notes, 2 validated
  findings (NQ 15m ORB + VWAP, T1 and T26)
- Holdout confirmation now requires significance (D-022); T1 (holdout
  t 0.8) and T26 (t 1.4) show as "positive on unseen data, not significant"
- The verdict is a status line with an explanation on hover, not a
  button-like pill
- The desktop app's first check for an already running backend waits up to
  2.5 s and retries: a single 800 ms request could time out while the app was
  starting, and then a second backend failed on the busy port
- A market-data test that only ran when yesterday wasn't a Saturday counted
  requests in a stale copy of the list — fixed

### Trained support/resistance model (D-023)
- Data set: every touch of the briefing's key levels on 5-minute bars in the
  session windows (NQ 09:30–16:00, gold 03:00–13:30 New York), levels taken
  at the window's start from earlier bars only; 30 features at the touching
  candle's close; label held / broken within 60 minutes; touches broken
  within the candle or undecided are counted and left out
- Model: four candidates (three gradient-boosting sizes, logistic
  regression) chosen on out-of-sample vs. a baseline (level kind + how the
  candle closed); walk-forward monthly on the holdout; day-clustered t;
  calibration table and permutation importance; final model on all data
- Service: checks every 30 minutes for a new trading day, downloads what's
  missing, trains in a worker thread (2 CPU threads), keeps the model file
  and every run (migration 7); "Train now" and "Daily on/off" on the
  Learning page; `training.changed` events; API `/training*`
- Uses: `level_odds` tool (odds near the price now; model only when
  confirmed), `learning_report` (verdict, unseen-month numbers, what
  mattered), one line in the morning briefing
- Tests (22): touch detection and labels by hand, trading-day end, feature
  signs, no look-ahead (rows identical when later data is cut off), market
  merge, clustered t calibrated under the null (≈5 %, naive > 15 %),
  walk-forward never trains on the month it predicts, a real pattern is
  confirmed, no pattern isn't (random walks end to end, 6 more seeds by hand),
  service (train, restart without retraining, train now, data errors,
  on/off), live odds (session, a touched level, outside the session), API,
  report and briefing line. E2E seeds a finished run and checks the card
- Full size measured here: 5.75 years × 2 markets of minute bars → ~40,000
  touches, 29 s, 360 MB peak, 170 KB model
- Not yet run on real data (Dukascopy is blocked here) — the first run on the
  MacBook gives the real verdict

### Always on (D-024)
- Closing the window hides it; menu-bar icon (template image, tinted by
  macOS) with Open JARVIS, Start at Login, Quit JARVIS; Dock click brings the
  window back; a one-time notification says JARVIS keeps running
- Start at login for the installed Mac app, switched on once by itself;
  Settings → Always on shows the mode and toggles it (needs-approval state
  from macOS 13+ shown)
- App Nap prevented while running; background waits checked against the wall
  clock (a Mac sleeping overnight no longer delays learning by hours)
- The supervisor restarts a crashed backend (1, 3, 10, 30, 60 s backoff)
- After a restart or update the brain reloads the last 24 hours of
  exchanges (and the morning briefing) as context
- Activity events older than 30 days are pruned daily; the conversation stays
- Backend start: scikit-learn imported lazily (import time 1.2 s → 0.4 s)
- Tests: restore across a real restart (the model sees the old exchanges),
  pairing and pruning, wall-clock waits with a simulated sleep, no
  scikit-learn at start; E2E: window close keeps JARVIS and its backend
  running and it comes back, a SIGKILLed backend is restarted and the
  dashboard reconnects, quitting still stops everything (22 steps)
- Not testable here: the menu-bar icon and login item on a real Mac

### Bot Lab: the user's MetaTrader 5 EAs (D-025)
- Finds MetaTrader 5 for Mac (app, its Wine, the prefix
  `net.metaquotes.wine.metatrader5`, MQL5 folders, Common\Files); lists EAs
  with source (MetaQuotes' examples excluded); setup check on the Bots page
- Test terminal: a portable copy of the program folder (without history)
  plus the user's MQL5 folder and saved logins; "Open test terminal" to log
  in once; the user's MetaTrader is detected and never touched
- Versions: v0 = the original (re-imported if the file changes); new
  versions are exact edits, compiled with the deal export appended (an own
  OnTester keeps working); quoted includes go along; DLL imports, web
  requests, sockets and file operations are refused
- Backtests: tester .ini (symbol, timeframe, model, deposit, leverage,
  inputs), one run over all periods, one MetaTrader run at a time, retry once
  when the first run only downloaded history, hung test terminal killed
  (only JARVIS's)
- Statistics per period: trades, net, profit factor, win rate, compounded
  return, per-trade t, max drawdown and worst day (closed balance), monthly
  table; prop-firm check; $10k projection for prop firm and own account
- Claude rounds: create_version / backtest / validate / write_note /
  finish_round; in-sample numbers only; Bonferroni-rising out-of-sample bar;
  $3/day; stops after 12 rounds without a validated version
- "Copy to MetaTrader" writes `Experts/JARVIS/<EA>/<EA>_vN.mq5` (+ includes)
- Tests (17): inputs, export hook, edits, encodings, refusals, includes, Mac
  discovery, Windows paths, .ini, compile log, test-terminal copy, compile +
  backtest through Wine (simulated, with the retry), deals → trades,
  periods, drawdown, prop check, scaling, the lab (import, backtest, gate,
  versions, install, Bonferroni), a scripted Claude round, API, bot_report.
  E2E: fake MetaTrader install whose Wine plays MetaEditor and the tester —
  set up, import, backtest, results, copy (23 steps)
- Not yet run against the real MetaTrader on the MacBook

### QuantLab R0 + R1 (D-026) — details in `docs/quantlab/IMPLEMENTATION_STATUS.md`
- Handoff package in `docs/quantlab-handoff/`; module `backend/jarvis/quantlab/`, page
  QuantLab (Overview · Strategies · Experiments · Datasets · Reports)
- StrategySpec v0.1 (strict, hashed, frozen versions), Data Passport importer (CSV/Parquet,
  fail-closed QA, Parquet snapshots), deterministic reference engine (next-open fills, exact
  decimal accounting, invariant audit), experiment registry (manifest-hash ids, staged
  checksummed artifacts, background runs with `quantlab.*` events, cancel, reproduce),
  chronological OOS, evidence gates A–E, verdict INVALID / FAILED / INCONCLUSIVE, JARVIS
  assessment, `quantlab_report` brain tool
- Tests: 48 backend tests covering the handoff matrix Q-001…Q-022 against its fixtures; 4
  vitest cases; E2E step (broken file refused, fixture passported, spec frozen, run judged,
  trade detail, reproduce) — **24/24 E2E steps** pass
- No real market data used yet: all results are SYNTHETIC / TEST ONLY engineering checks
- Screenshots: `docs/screenshots/quantlab-*.png`

### ULTRON R0 + R1 (D-027) — details in `docs/ultron/IMPLEMENTATION_STATUS.md`
- Handoff in `docs/ultron-handoff/`, gap analysis in `docs/ultron/INTEGRATION_PLAN.md`;
  module `backend/jarvis/ultron/`, page ULTRON (Overview · Mission Control · Agent Matrix ·
  Projects · Knowledge · Activity · Permissions)
- JARVIS plans (charter + task DAG validated in code); AXIOM, FORGE, SENTINEL work in a git
  worktree per task through an audited tool broker; commands sandboxed (no shell, no
  network, scrubbed env, timeouts; Seatbelt on macOS, network namespace on Linux)
- The runtime runs the checks itself, merges, and gives SENTINEL a throwaway checkout;
  rejected work goes back to FORGE; bounded retries; spend cap before every model call;
  approval-gated export (effect signature); locked: network, installs, merging into the
  running checkout, deploys, messages, credentials, money
- Pause / resume / stop / emergency stop; restart recovery; brain tools
  `ultron_start_mission`, `ultron_status`
- Tests: 18 backend tests (acceptance mission, restart, pause, stop, crash recovery,
  review loop, retries, budget, plans, policy, sandbox, approvals, API, brain); E2E step in
  the real app — **25/25 E2E steps** pass
- Agent replies in tests/E2E are scripted (labelled in the UI); no Anthropic key exists in
  this container, so the first real-model mission is still to run on the MacBook
- Screenshots: `docs/screenshots/ultron-*.png`

## IN PROGRESS
- —

## NEXT


0. Voice on the MacBook: connect ElevenLabs, "Hey JARVIS", push-to-talk
0. Learning on the MacBook: first Dukascopy download, first rounds
0. Trained model on the MacBook: first real verdict (Learning page)
0. Bot Lab on the MacBook: setup check, test terminal login, first backtest of the gold EA
0. QuantLab on the MacBook: import real daily bars of one liquid ETF and run one frozen spec
0. ULTRON on the MacBook: first real-model mission (e.g. a CSV → JSON CLI with tests, $3 budget)

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
