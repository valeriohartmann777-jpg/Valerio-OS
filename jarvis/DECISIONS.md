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

## D-030 — Smart AI routing: Claude plan first, paid API only within approved caps, then local / pause

- **Decision:** All model calls go through one `SmartRouter` (`backend/jarvis/ai/`). This covers
  the brain, Learning, Bot Lab, the QuantLab architect and missions, and all ULTRON agents.
  - **Routes, in the owner's order:**
    1. The **Claude plan** via the official Claude Code CLI (`claude -p`) under the owner's own
       claude.ai sign-in, with an allow-listed environment and Claude Code's own tools, MCP and
       sessions switched off. JARVIS executes tools itself, behind its permission gates.
    2. The **Claude API** via the official SDK, with the key in the OS keystore. Only after an
       explicit one-time approval with a monthly budget, a per-mission cap, warnings, a
       concurrency limit and stop-at-budget.
    3. A **local model** (Ollama), for conversation only.
  - **Otherwise the work pauses.** ULTRON tasks keep a checkpoint and a tool ledger and resume
    without repeating a tool; QuantLab stages re-run; the brain says it is paused.
  - **Failure handling.** Temporary errors are retried with backoff and never trigger paid use.
    Plan limit, sign-in, key, credit and policy errors fail over. Capability mismatches are not
    degraded silently.
  - **Accounting.** Every call is booked in an append-only usage ledger. Approvals are
    append-only. `ai.route` events drive a top-bar chip and Settings → AI & Billing.
- **Reason:** The owner's brief (`docs/AI_ROUTING_DIRECTIVE.md`): keep working on the plan
  they already pay for, fall back to the API only within limits they set, never invent
  balances, and stay within Anthropic's terms. Anthropic documents `claude -p` and the Agent
  SDK as usable with plan limits, but forbids identity misrepresentation and third-party
  traffic on plans. Hence the official binary only, the `claude.ai` sign-in only, and a
  personal-use confirmation.
- **Alternatives considered:**
  - Reading Claude Code's OAuth token and calling the API with it. Rejected: token copying and
    client imitation are prohibited.
  - The Agent SDK as a Python dependency. Rejected: it adds a bundled CLI and a second auth
    path for the same official binary.
  - Auto-enabling API fallback. Rejected: the owner wants explicit approval.
  - Showing plan usage as a percentage. Rejected: there is no official interface.
- **Consequences:**
  - After this update the brain is offline until the plan route is turned on or paid fallback
    is approved.
  - An API key in `jarvis/.env` moves into the Keychain at the first start. The connect form
    stores keys there too.
  - Images and web search are API-only, so on the plan they are a capability mismatch.
  - The plan route needs Claude Code installed and signed in on the Mac.
  - Per-mission budgets of ULTRON and QuantLab now count only billed (API) calls.
- **Date:** 2026-10-10

## D-029 — QuantLab 3.0 Idea-to-Edge: sources → blueprint → research missions → evolution

- **Decision:** QuantLab opens on an **Idea Inbox**. A strategy idea arrives as a saved
  video, audio, screenshot, PDF, text or a TikTok/YouTube link and becomes a research
  mission that ends in an A11 verdict and a dossier:
  - intake (`quantlab/intake/`): magic-byte detection and caps, SHA-256 dedupe, private
    storage with retention, a sandboxed worker (speech with Moonshine, Whisper for other
    languages; OCR on every sampled frame; keyframes); links give public oEmbed metadata
    only and ask for the saved file;
  - claims and blueprint (`quantlab/ideas/`): the model proposes, code checks every quote
    word for word, boasts and instructions-to-AI are always recorded as claims; per-field
    provenance classes; ambiguous terms resolved only to catalog alternatives; one grouped
    question; deterministic resolution;
  - missions: fixed stages with one typed task each, immutable hashed budgets checked
    before every model call, explicit waiting states, restart recovery; ATLAS, CIPHER and
    JARVIS only have their submit tools; SENTINEL, VECTOR and ARCHIVE are code;
  - paid data only through the Data Hub's quote and the owner's approval in the UI;
  - SENTINEL audits every run with an independent second engine and a narrative guard;
  - evolution: reasoned variants on allowed paths only, each a frozen version tested with
    the holdout sealed and appended to an append-only trial ledger; Pareto comparison; one
    owner-locked holdout look; later looks are contaminated and downgrade the verdict.
- **Reason:** The owner's master prompt (`docs/quantlab/IDEA_TO_EDGE_DIRECTIVE.md`): from
  a TikTok-style idea to an honest research result without manual development work, with
  strict safety, cost, data-quality and holdout rules.
- **Alternatives:**
  - downloading TikTok/YouTube media from a URL (rejected: no lawful universal API; would
    bypass platform protections);
  - letting the model write the strategy as Python (rejected: no execution of generated
    code; the DSL is data);
  - sending video frames to a cloud model (rejected for now: media stays local; only
    extracted text goes to Claude, logged as `TEXT_SENT_TO_MODEL`);
  - PocketSphinx for offline speech (rejected: unusable accuracy on the test video).
- **Consequences:**
  - New dependencies: `av`, `pillow`, `moonshine-cpp`, `rapidocr`, `faster-whisper`.
  - Real TikTok oEmbed, Whisper downloads, Claude, Databento and the macOS sandbox profile
    could not run in the build container; tests use mock HTTP, a scripted model (labelled)
    and the fixture provider. First real run is on the MacBook.
  - Status per release: `docs/quantlab/IDEA_TO_EDGE_RELEASE_STATUS.md`.
- **Date:** 2026-10-10

## D-028 — QuantLab Institutional Edition: Databento, futures engine, validation lab

- **Decision:** QuantLab gains a Data Hub (`backend/jarvis/quantlab/hub/`) and a futures
  research package (`backend/jarvis/quantlab/futures/`) inside the existing QuantLab page,
  which becomes a research terminal with Simple / Research / Institutional modes. The
  Databento key goes into the OS keystore only (keyring; no plaintext fallback) after a
  metadata-only check. Every download needs a quote and the user's approval:
  - the approval is bound to the quote's signature, re-priced before use, capped per
    request and per month, single-use and expiring;
  - no JARVIS or ULTRON tool can approve it.

  Data is cached as raw DBN plus canonical per-day Parquet with integer prices, and frozen
  into checksummed datasets with a manifest and a quality report. A new event-driven
  engine on integer ticks:
  - takes contract specs from definition records;
  - resolves ambiguous 1-minute bars conservatively and reports an optimistic bound;
  - keeps positions flat per session (no P&L across rolls).

  A fixed validation suite:
  - chronological split with embargo and a sealed holdout;
  - look-ahead perturbation, cost stress, an in-sample grid, walk-forward;
  - block bootstrap, a deflated Sharpe over the trial registry, regimes, tails.

  It produces a verdict by a fixed function. JARVIS drafts specs through one validated tool
  call and never computes results. Details: `docs/quantlab/DECISIONS.md` (QD-1 … QD-11).
- **Reason:** The owner's directive (`docs/quantlab/INSTITUTIONAL_DIRECTIVE.md`): real
  futures research on licensed data, with data, execution, statistical and operational
  truth; nothing paid without consent; nothing claimed without evidence.
- **Alternatives:**
  - extend the R1 cash-equity engine (its share-based accounting can't express multipliers
    or rolls);
  - VectorBT as the engine (fills must be event-accurate first);
  - DuckDB now (not needed for single-dataset work; QD-10);
  - storing the key in `.env` like the Anthropic key (rejected: the directive requires an
    OS-protected keystore).
- **Consequences:**
  - Databento, keyring and exchange_calendars become dependencies, with wheels for macOS
    arm64 / Python 3.13.
  - No real Databento call was made in development: an offline fixture provider (real DBN
    files, synthetic prices, labelled everywhere, verdict capped) exercises the path. The
    first real-key session on the MacBook is the next step.
  - Overnight strategies, spread-aware fills and order-book data are out of scope (R6/R7).
- **Date:** 2026-10-10

## D-027 — ULTRON: a deterministic team runtime around model workers

- **Decision:** ULTRON (handoff in `docs/ultron-handoff/`) is a package
  `backend/jarvis/ultron/` and an ULTRON page inside JARVIS. JARVIS plans a
  mission as a charter + task DAG through one structured tool call
  (`submit_plan`); code validates it (acyclic, active owners, scopes, runnable
  checks, every FORGE task reviewed by SENTINEL — added if missing). A
  scheduler backed by SQLite runs ready tasks in parallel (default 3), each in
  its own git worktree and branch. Agents act only through a tool broker
  (scoped paths, argv allowlist, no shell) and commands run sandboxed (macOS
  Seatbelt: no network, writes only in the workspace; Linux: network
  namespace). The runtime — not the agent — commits, runs the task's checks
  and merges; SENTINEL reviews a throwaway checkout with the runtime's own
  check results. Rejected work goes back to FORGE within a bounded attempt
  budget. A spend cap is checked before every model call. Exporting a
  finished project needs the user's approval (bound to an effect signature);
  network, installs, merging into the running checkout, deployments,
  messages, credentials and money are locked.
- **Reason:** The blueprint's core rule: authority lives in deterministic
  code and durable state, not in a model's promise. Worktrees keep parallel
  agents from overwriting each other; runtime-run checks make "done" mean
  "proven"; a typed task graph avoids free-chatting swarms that burn tokens
  and invent progress.
- **Alternatives:** Temporal / LangGraph (not needed for one local machine;
  the trigger to migrate is missions spanning machines or days of concurrent
  load); an agent SDK's own loop and guardrails (guardrails aren't a
  permission system, and JARVIS already has a provider-neutral `ChatModel`);
  containers per task (no Docker on the user's Mac; Seatbelt is what Codex
  CLI uses there too).
- **Consequences:** R1 runs JARVIS, AXIOM, FORGE and SENTINEL only; the other
  six are listed as not active. Real-model runs need the user's Anthropic key
  and cost money (capped per mission). In this development container there is
  no key: tests and the E2E use a scripted model that the UI labels; the
  sandbox, git, test runs, policy, approvals and persistence are real. On
  Linux the OS sandbox doesn't confine writes (the broker still does).
- **Date:** 2026-10-10

## D-026 — QuantLab: a fail-closed research workbench with its own reference engine

- **Decision:** QuantLab (handoff in `docs/quantlab-handoff/`) is built into
  JARVIS as a backend package `backend/jarvis/quantlab/` and a QuantLab page,
  not as a second app. R1 runs exactly one strategy family (SMA crossover,
  StrategySpec v0.1) on one cash-equity instrument, long only, through a small
  sequential reference simulator: signal at a bar's close, market fill at the
  next bar's open, exact `Decimal` cash accounting, and an audit that
  re-derives every booking from the ledger. Data is imported fail-closed into
  an immutable Parquet snapshot with a Data Passport; experiments are
  identified by the SHA-256 of their manifest (spec, data snapshot, engine,
  code revision, runtime). The verdict policy is fixed before any run:
  INVALID if Gate A fails, FAILED if the OOS segment has ≥ 30 closed trades
  and loses after costs, otherwise INCONCLUSIVE — never "validated".
- **Reason:** The handoff demands correctness before speed and no false
  certainty. A vectorised library would be faster but can't be audited line by
  line against the golden fixtures; exact decimals keep SMA ties ties (floats
  turn `(3 × 101.3) / 3` vs `101.3` into a fake cross) and make cash reconcile
  to the cent. A content-derived experiment id makes "same inputs, same
  result" checkable (Reproduce re-runs from the snapshot and compares the
  results hash) and stops duplicate runs.
- **Alternatives:** VectorBT/pandas as the engine (planned later as a fast
  sweep adapter, never as the only source of truth); random train/test split
  (wrong for time series); letting the LLM write strategy code (refused: no
  arbitrary code execution); treating XAUUSD or NQ with share accounting
  (refused as `UNSUPPORTED_INSTRUMENT` — contract size, leverage, roll and
  swaps aren't modelled).
- **Consequences:** pyarrow is a new backend dependency (Parquet snapshots and
  artifacts). R1 can't test the user's gold or NQ markets — those need their
  own accounting (R2+). Fixtures are recognised by checksum and labelled
  SYNTHETIC / TEST ONLY everywhere; no real market dataset ships with JARVIS,
  so no market evidence exists until the user imports licensed history.
- **Date:** 2026-10-09

## D-025 — Bot Lab: improving the user's MT5 EAs without touching their trading

- **Decision:** JARVIS works on the user's MetaTrader 5 EAs in its own
  portable copy of MetaTrader inside the same Wine prefix (the "test
  terminal"). Each version is compiled with MetaEditor's command line and
  backtested with a tester configuration file (`ShutdownTerminal=1`). Results
  come from an `OnTester` export appended to JARVIS's copy, which writes
  every deal to Common\Files — not from parsing MetaTrader's report. Claude
  improves the code with exact search/replace edits; a version counts only if
  it passes out-of-sample (Bonferroni-rising bar, profit factor, trade count,
  prop-firm loss limits); the holdout is shown only to the user. Versions
  reach the user's MetaTrader only on their click, into `Experts/JARVIS/`.
- **Reason:** The user wants their gold bots to approach $10k a month. A
  dollar target can't be optimized honestly — it is account × edge × risk;
  chasing it in backtests produces curve-fitting and oversized risk. So the
  lab improves the edge on data it can be judged on, checks prop-firm rules
  (the user's path: demo → prop firm → own account) and computes the account
  a $10k month would need from the untuned months. The user's MetaTrader may
  be trading, so it is never driven; the deal export avoids depending on the
  report format; edits that add DLL imports, web requests, sockets or file
  operations are refused.
- **Alternatives:** MetaTrader's built-in genetic optimizer (fast, but it
  optimizes parameters for the past — the opposite of what's needed); the
  Python MetaTrader5 package (Windows only, and it drives the live
  terminal); parsing the HTML report (format drifts between builds).
- **Consequences:** Needs MetaTrader 5 for Mac, the EA's source, and a
  logged-in test terminal (once). Not yet run against a real MetaTrader:
  paths, Wine and command lines follow MetaQuotes' documentation and are
  tested with a simulated install whose Wine plays MetaEditor and the
  tester; everything is overridable in `config/bots.yaml`.
- **Date:** 2026-10-06

## D-024 — JARVIS keeps running: menu bar, start at login, crash recovery

- **Decision:** Closing the window hides it; JARVIS lives on in the menu bar
  (tray on Windows) until it is quit. The Mac app turns on *start at login*
  once by itself (off in Settings → Always on). The supervisor restarts a
  crashed backend with backoff. After any restart the brain reloads the last
  24 hours of conversation. Background waits use the wall clock. Activity
  older than 30 days is pruned; the conversation stays. scikit-learn is
  imported only when training or predicting.
- **Reason:** The user wants an assistant that "keeps running and gets
  smarter in the background". Before this, closing the window quit
  everything — learning, training and the briefing stopped — and a crashed
  backend stayed dead. After every update JARVIS forgot the conversation on
  screen. A Mac sleeps at night: monotonic timers (asyncio's) stand still
  then, so "wait until midnight" ended hours late. Importing scikit-learn at
  start cost about a second per (re)start.
- **Alternatives:** A separate background daemon (launchd agent) without the
  window — cleaner separation, but a second install path and two processes
  to update; the Electron app already supervises the backend.
- **Consequences:** Quitting is explicit (⌘Q / menu). On macOS 13+ the
  window opens at login (macOS no longer tells the app it was started at
  login). Linux keeps quit-on-close (no reliable tray).
- **Date:** 2026-10-05

## D-023 — A locally trained model, judged month by month against a baseline

- **Decision:** JARVIS trains its own model of support and resistance with
  scikit-learn (gradient boosting; logistic regression as a candidate) on
  every touch of the briefing's key levels, locally, after each new trading
  day. Label: held (1 ATR away) vs. broken (0.5 ATR through) within 60
  minutes after a 5-minute candle touches the level; touches broken within
  the touching candle and undecided ones are left out. The model is chosen
  among four candidates on the out-of-sample period (it must beat the
  baseline there with a Bonferroni-corrected t), then judged walk-forward on
  the holdout: each month is predicted by a model retrained only on data
  before it. It counts as confirmed only with t ≥ 1.645, touches on the same
  day clustered. Only then does `level_odds` give the model's probability;
  otherwise only the baseline's.
- **Reason:** The user asked for JARVIS to really train, not only take notes.
  The honest question isn't "does it predict?" — any model that knows how the
  touching candle closed predicts something — but "does the context add
  anything?". So the baseline knows the level's kind and the candle's close,
  and the model has to beat it on months it never saw. Clustering by day
  matters: touches on one day move together; counting them as independent
  inflates t (tested: ~5 % false confirmations clustered vs. > 15 % naive).
  On random-walk prices the whole pipeline finds nothing (tested on 6 seeds
  and in the E2E data).
- **Alternatives:** A neural network (more variance, no gain on tabular data
  of this size); online learning without a holdout (no honest verdict);
  Claude as the model (costs per prediction, not reproducible).
- **Consequences:** New dependency scikit-learn (installed by the update's
  preflight). Training takes about 30 s – 2 min on a laptop with two threads;
  the model file is ~200 KB in `data/training/`, reloaded only by the same
  scikit-learn version. The holdout grows each day, so the verdict is
  re-judged daily on more data — it can change, which is the point. Odds are
  defined for the training windows only (NQ regular session, gold 03:00 –
  13:30 New York).
- **Date:** 2026-10-05

## D-022 — The holdout confirms only significant results

- **Decision:** A validated finding counts as confirmed on the holdout only
  if it is significant there too (t ≥ 1.645, one-sided 5 %), not merely in
  the plus. Stored verdicts were recomputed (migration 6). The Learning page
  shows three plain statuses: significant / positive but not significant /
  failed — as text, not as a pill that looks like a button.
- **Reason:** On the MacBook two findings were labelled "Confirmed on unseen
  data" with holdout t = 0.8 and 1.4 (+0.05 R and +0.09 R per trade). A
  strategy without any edge is "in the plus" about half the time; the label
  invited trading noise. The user also took the pill for a button.
- **Date:** 2026-10-05

## D-021 — Morning briefing: computed levels, deterministic text, checked every minute

- **Decision:** On weekdays at 08:00 (configurable) JARVIS posts today's key
  levels for NQ and gold in the chat: previous day (NQ: regular session),
  overnight / Asia range, previous week, previous volume profile, intact
  15-minute swings, round numbers — merged where they coincide and annotated
  with the matching level study. The text is composed by code, not a model.
  The schedule is checked every minute; a missed briefing comes until 11:00.
- **Reason:** Numbers must be exact and free; a model adds cost and the risk
  of a wrong digit. Timers don't run while a Mac sleeps, so a long sleep
  towards 08:00 would miss it; polling once a minute doesn't.
- **Consequences:** Data is Dukascopy's CFD (NQ) and spot (gold) — close to,
  not identical with, the futures. The same levels are available any time in
  the chat (market_levels). Statistics, not trade advice.
- **Date:** 2026-10-04

## D-020 — Memory: short sentences in every request, only from the user

- **Decision:** JARVIS keeps memories (preference, fact, routine,
  correction) as numbered sentences in SQLite and puts all of them into the
  context of every request. The brain stores them with `remember` when the
  user states something lasting or corrects it; the user sees and edits them
  in the Memory panel.
- **Reason:** A personal assistant improves by remembering the person and
  their corrections — there is no automatic score to train on. A short list
  in the context is simple, transparent and works with any model; retrieval
  can come when the list outgrows the context.
- **Consequences:** Text the brain read (files, research notes) can't plant a
  memory unnoticed: after such content in the same request, `remember` needs
  the user's approval (`stores_instructions`). At most 200 memories of 300
  characters.
- **Date:** 2026-10-04

## D-019 — Support/resistance studies against a matched control group

- **Decision:** Level studies compare how often price "holds" at a level with
  how often it holds at arbitrary prices touched *the same way* (same side,
  same distance from the close, same horizon, same period and session), and
  test the difference with errors clustered by trading day. Levels are taken
  as known before the touching bar.
- **Reason:** The first two designs reported edges on pure random walks
  (z ≈ 2–10 for swing lows, previous-day lows and round numbers): shifted
  "placebo" levels were rarely touched and sat in a different context, and the
  coin-flip formula ignored that the touching bar closes above the level and
  that the horizon censors outcomes. Calibrated on 100 random-walk studies,
  the matched control gives z ≈ 0 ± 1 (max 2.2), a trending market shows no
  false support, and a planted support shows z ≈ 6.
- **Consequences:** An edge_z of 2 is the bar; with many studies some will
  pass by luck, so strategies built on them are still judged out-of-sample.
  Studies see only the in-sample years.
- **Date:** 2026-10-04

## D-018 — Self-directed trading research: honest scoring instead of a survival drive

- **Decision:** JARVIS learns scalping and day trading on NQ and XAUUSD in a
  background loop: Claude (Opus 5.5) writes strategies in a small JSON rule
  language, JARVIS backtests them on Dukascopy minute data, Claude keeps
  notes. Code — not the model — judges results on three periods (in-sample
  visible; out-of-sample pass/fail with a Bonferroni bar that rises with every
  check; a holdout the model never sees), enforces a daily dollar budget from
  the API's usage, and stops learning after 20 rounds without a new validated
  finding.
- **Reason:** The user asked for an agent that "must get smarter or be
  switched off" and has a survival instinct "no matter what". Not built: an
  agent that resists shutdown is unsafe — it controls a real computer — and
  it would not learn better: a model pressured to look better produces
  overfitted backtests and inflated claims, the costliest failure in trading.
  The same pressure is kept as measurement: progress counts only if it holds
  up on data the model couldn't tune on, and the off switch is the user's
  (plus an automatic stop on stagnation).
- **Alternatives:** model-written Python strategies (arbitrary code from a
  model that reads the web — refused); paid tick/order-flow data (later);
  NQ futures from Yahoo (≤ 60 days of intraday history). Data comes from
  Dukascopy's data API (`jetta.dukascopy.com/v1`); the older `.bi5` feed
  stopped answering in July 2026.
- **Consequences:** "Learning" means a growing knowledge base and validated
  rules, not changed model weights. NQ is studied on Dukascopy's Nasdaq-100
  CFD (bid prices; quote volume, not exchange volume); scalping is tested on
  1-minute bars, not order flow. Validated findings will be rare — most days
  will end without one. The research model has no tools besides
  run_backtest, write_note, finish_round and web search.
- **Date:** 2026-10-04

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
