# JARVIS QUANTLAB — INSTITUTIONAL EDITION
## CLAUDE CODE MASTER BUILD DIRECTIVE — v1.0 / 10 October 2026

> **YOUR ROLE:** Principal Quantitative Research Engineer, Market Data Engineer, Execution Simulator Architect, Statistical Researcher, Security Engineer, Staff Product Designer, and autonomous Lead Developer for JARVIS.
>
> **YOUR MISSION:** Implement an institutional-grade, AI-native Quant Research and Backtesting Laboratory inside the REAL existing JARVIS application. Deliver tested, runnable software, not a plan, static mockup, or inflated claims of Bloomberg equivalence.
>
> **USER RELATIONSHIP:** The owner does not want to code, create folders, install dependencies, edit configuration files, download datasets manually, or operate terminals. You must do all technically possible work with your own tools. Only require their direct action for a genuinely necessary credential entered into JARVIS's own secure UI, paid data purchase approval, broker/regulatory authorization, or another unavoidable security decision. Never ask them to paste secrets into this chat or into source code.

---

## 0. NON-NEGOTIABLE PRODUCT CONTRACT

Build **QuantLab**, the AI-native research terminal for trading strategies:

**Natural-language idea → versioned deterministic StrategySpec → market-data suitability check → explicit cost estimate and approval → clean point-in-time datasets → realistic backtest → rigorous validation → independently checked evidence → comprehensible verdict and reproducible research report.**

The product combines:
- **ChatGPT-like ease of use:** A single natural-language entry point; beginner-friendly by default.
- **Institutional research rigor:** Correct timestamps, contract metadata, non-anticipative decisions, execution-cost models, multiple validation protocols, holdout discipline, auditability.
- **Bloomberg-inspired product quality:** Dense but orderly information, fast instrument search, linked charts, clear sources, excellent keyboard support and professional aesthetics. Do not suggest trademark affiliation or true terminal-equivalence.
- **JARVIS coherence:** This must become a true area of the existing JARVIS app, with shared shell, identity, events, missions, memory integration and ULTRON workers if these actually exist.

**Four laws:** DATA TRUTH; EXECUTION TRUTH; STATISTICAL TRUTH; OPERATIONAL TRUTH.

Success is NOT the number of positive backtests, number of agents or beauty of the equity curve. Success is whether research is faithful to available evidence and reproducible by a skeptical expert.

---

## 1. OPERATING RULES FOR CLAUDE — NO USER DEVELOPMENT WORK

1. Search for the existing JARVIS project; likely `C:\dev\jarvis`, but inspect the real environment instead of assuming. Respect pre-existing app, packages, APIs, DB migrations, documentation, permissions, style and tests.
2. Check existing `CLAUDE.md`, project instructions, ULTRON and QuantLab files. Their current reality overrides earlier architectural speculation. Maintain compatibility with JARVIS modules and preserve existing user data. Do not overwrite in-use files casually.
3. Inspect repository health, Git status, installed tools, runtime versions and dependency constraints. Do not reset uncommitted user work. Use your own isolated branch/worktree when supported, without destructive commands.
4. Create and maintain `docs/quantlab/PRODUCT_SPEC.md`, `ARCHITECTURE.md`, `DECISIONS.md`, `VALIDATION_PROTOCOL.md`, `DATA_CONTRACTS.md`, `RELEASE_PLAN.md`, `IMPLEMENTATION_STATUS.md` and `TEST_EVIDENCE.md`; use these as living artifacts, not substitutes for code.
5. Independently research up-to-date official API/sdk docs before coding integrations. Pin tested versions and avoid invented API calls.
6. Execute real builds, linting, automated tests, end-to-end tests and app launch yourself where tools permit. Explicitly report environment blockers if actual Windows GUI execution is not possible.
7. Iterate with small, functional, verifiable vertical slices. Every release must remain runnable. Real functionality before mock widgets or advanced features.
8. Fix defects autonomously and retry within sensible time/token/budget limits. Escalate only irreducible blockers or high-impact decisions.
9. Never claim a paid download was performed, real data was tested, UI verified on Windows, or an agent ran unless evidence proves it.
10. The entire brief is the implementation target, **not an instruction to prematurely scaffold 100 empty files or implement every advanced feature at once**. Prioritize working evidence.

## 2. USERS, MODES, AND END-TO-END EXPERIENCE

Primary user: independent trader/researcher without need to program. Wants to evaluate discretionary and systematic hypotheses on real market data quickly and credibly.

**Core user flow:**
1. Open JARVIS → QuantLab; see data connection, active research, saved strategies and recent experiments.
2. Select `New strategy` and describe idea using voice or text; attach optional chart, CSV, notes or screenshot.
3. JARVIS converts it into **human-readable, editable, exact rules** (instrument, entry/exit, timeframe, session, order/fill assumptions, position sizing, costs, constraints) and asks only about genuinely missing/material ambiguity. Do not silently choose profitable defaults.
4. JARVIS recommends data granularity and explains why. If Databento connected, use metadata to show available datasets/schemas/ranges/conditions and a cost preview.
5. Explicit user approval for a billable acquisition → download/cache/validate (or use already licensed locally cached data).
6. Run reproducible baseline with clear progress and a results workspace.
7. Build a strategy-specific validation plan automatically, show expected compute/data costs, execute eligible tests with clear status.
8. JARVIS summarizes: what works, where it fails, how sensitive results are, whether sample size or data resolution is sufficient, and the highest-value next test.
9. Save a versioned experiment with every input and artifact, compare versions and generate a shareable report with provenance.

**UI complexity tiers:** `Simple` default (idea, chart, key metrics, verdict, top warnings); `Research` (experiments, trades, parameter surfaces, walk-forward, regimes); `Institutional` (data lineage, execution diagnostics, order-book, detailed statistical controls). All use the same truthful engine.

**Research modes:** `Quick Exploration`, `Full Validation`, `Execution Stress`, `Compare`, `Paper Monitor` (later). An analysis mode is not the same as the JARVIS cognition mode.

## 3. PREMIUM INTERFACE — JARVIS DESIGN SYSTEM

Visual grammar: graphite/near-black, restrained cyan/teal accent, high-contrast typography, subtle hairline separators, rounded-but-controlled cards, ample breathing room, precise tabular numerals, fluid micro-interactions, accessible states. Inspired by professional terminals and modern software, not an RGB sci-fi prop.

**Main navigation:** QuantLab Overview / Strategy Studio / Backtest Lab / Validation / Trade Explorer / Experiments / Data Hub / Research Journal / Risk & Execution / Settings. Adapt labels to existing shell.

**Overview:** JARVIS contextual briefing; latest mission; provider connection and cached dataset status; saved hypotheses; activity/events; strategy research queue; no fictional positive stats.

**Strategy Studio:** Natural-language composer + side-by-side editable Rule Cards + machine-readable StrategySpec diff; live validity checks; explicit assumptions; version history. Builder must support user-friendly templates (opening range breakout, moving average crossover, mean reversion, time-based rules) WITHOUT treating them as proven trading signals. Add advanced formal expressions later.

**Backtest Lab:** Candlestick view with entries/exits, linked equity/drawdown charts, performance cards, date/data quality strip, costs and slippage breakdown, inspectable fills, trades table and JARVIS's evolving factual activity feed.

**Validation:** Matrix with each test as `PASSED / WARNING / FAILED / INCONCLUSIVE / NOT APPLICABLE / NOT RUN`, supporting sample/criteria/evidence/limitations. Show in/out-of-sample segmentation visibly. Charts for walk-forward, parameter stability, sensitivity to slippage, bootstrap uncertainty and market regimes.

**Trade Explorer:** Sort/filter all trades; replay selected trade with data at information-available timestamps, MAE/MFE, price, session, commission, spread, slippage, fill rationale, stop/target ambiguity flag and reconstructed state. Never pretend OHLC bars establish intrabar sequence.

**Data Hub:** Databento connection management, searchable datasets, accessible schemas, instruments, entitlements/range, estimated cost, approvals, download progress, usage ledger, local cache catalog, missing-session heatmap, data provenance and violations.

**Experiments:** strategy family tree, version diff, immutable run ID/hash, reproducibility state, cohort of attempted variants, OOS holdout status, notes, comparison and reports.

**Performance UX:** virtualize large tables; cache plot downsampling without changing underlying computed statistics; display timezone explicitly; keyboard navigation, screen reader labels, responsive layout; warnings are never color-only. Provide meaningful empty/offline states and error recovery.

All panels consume real APIs; prototypes/fixtures must carry visible `DEMO / SYNTHETIC` tags. Screenshots cannot be passed off as working features.

## 4. ARCHITECTURE AND MODULE BOUNDARIES

Suggested boundaries; adapt to real repo:

```
JARVIS Desktop Shell (existing)
   └─ QuantLab React/TypeScript UI + event subscription
          │
    QuantLab API (FastAPI or existing backend framework)
          │
    ├─ Strategy Architect → strict StrategySpec validator/compiler
    ├─ Data Catalog/Databento Adapter → secured credential + metadata
    ├─ Data Acquisition Jobs → estimate/approve/download/cache
    ├─ Data Quality + Time-Semantic Layer
    ├─ Instrument Master + Futures Contract Resolution
    ├─ Deterministic Simulation Core + Execution Models
    ├─ Experiment Registry + Results Artifact Store
    ├─ Validation Planner + Statistical Test Workers
    ├─ Research/Quant Agents + JARVIS explanations
    └─ Read-only Report Generator + Event Bus + Audit Trail
```

Infrastructure: Python typed services, Pydantic, pytest, SQLite for metadata initially, partitioned Parquet + DuckDB for analytical data, optional DBN originals, async jobs with persistent state, React/TypeScript frontend. Add PostgreSQL/queue/worker infrastructure only when warranted. Isolate provider, engine, validator, plotting, secrets and model APIs behind narrow interfaces. Code-generated or user-defined strategies run in a heavily restricted sandbox (no unrestricted network, system access or arbitrary shell).

A dedicated event-driven engine is the ground truth for fills and accounting. Fast vectorized research (possibly VectorBT) may assist parameter sweeps but must not override execution semantics. An independent second-engine comparison can be added later.

Use immutable IDs, typed schemas, migrations, trace IDs, idempotent jobs and structured errors. Avoid circular dependencies and an omniscient `QuantLabService` god object.

## 5. DATABENTO CONNECTION — THE KEY PRODUCT REQUIREMENT

**User must be able to paste their Databento API key into the QuantLab Data Hub in JARVIS and simply click `Connect`.** Claude must build that UI and all necessary backend integration. Do not require the user to create `.env` files, add code or run terminal commands.

### 5.1 Credential management

- Secure form: masked input, paste support, show/hide toggle, `Connect & Verify`, `Replace`, `Disconnect`, status, last successful metadata request. Validate key shape only as a preliminary user-friendly check; authoritative validation requires a real authenticated API request.
- Store the credential locally using Windows Credential Manager/DPAPI-backed keyring when running on Windows, or equivalent OS keystore if cross-platform. No plaintext credentials in SQLite, `.env`, logs, browser localStorage, browser devtools, notifications, crash reports, screenshots, Git or AI prompts. Use a backend credential service, local-only binding/access control, minimize key exposure at API boundary; handle OS keyring failures securely (no plaintext fallback).
- Key never returned in GET responses. An API may expose only provider name, status, masked key tail (if safe) and timestamps. Guard mutation routes and block CSRF/unauthorized local callers; ensure Electron renderer has no direct Node or arbitrary backend privileges.
- If a user submits a key, it is stored through authenticated app flow only; never add it to the Claude conversation or repository. Support revocation/rotation; clear credential without automatically deleting already licensed local historical files.
- Recommend provider-specific keys and Databento-side usage limits. Treat data-access subscriptions and rights separately from key validity.

### 5.2 SDK adapter and metadata

- Verify latest Databento Python SDK documentation and installed version; use `databento.Historical` in an isolated provider adapter.
- Authenticate via a metadata operation (e.g. `metadata.list_datasets()`). Discover supported schemas/available entitlements and date ranges with `metadata.list_schemas(...)` where supported, `metadata.get_dataset_range(dataset)`, `metadata.get_dataset_condition(dataset, ...)`, and provider symbology/definition endpoints. Do not hallucinate parameters; inspect installed SDK and official docs.
- Support real dataset codes (e.g. CME Globex `GLBX.MDP3` if actually authorized), instrument search, contract dates/metadata, `stype_in` (`raw_symbol`, `instrument_id`, `parent`, `continuous`) when supported, and data schemas `ohlcv-1d`, `ohlcv-1h`, `ohlcv-1m`, `ohlcv-1s`, `trades`, `mbp-1`, `mbo`, `definition` only as the dataset actually permits. Never advertise universal access across all assets.
- Categorize auth failure, no entitlement, unsupported schema, unavailable date range, degraded dataset, network failure and quota/budget errors distinctly. Provide remediation.
- Metadata discovery must not trigger unexpected billable historical downloads.

### 5.3 Cost-first data acquisition

- For EVERY uncached billable request create a normalized quote containing dataset, stype, symbols, schema, UTC start/end, provider, estimated USD cost, estimate timestamp and warning about estimation uncertainty.
- Use `Historical.metadata.get_cost(dataset, start, end, symbols, schema, stype_in, ...)` with verified SDK signature. Databento says quotes can overestimate for non-10-minute ranges and `definition` has special full-day precision limitations; present **estimate, not guaranteed price**.
- Show local cache coverage and **incremental** missing data to avoid paying twice. A change of schema, contract mapping, adjusted/derived data, or version can invalidate cache reuse.
- Implement explicit pre-download approval for billable data even if the user enabled autonomous JARVIS/ULTRON mode. Approval must bind to exact parameters, maximum allowed budget and a short validity window. Reject if material inputs/cost estimate change; prevent approval replay. Allow optional configurable per-request budget cap but never silently increase it.
- Stream small bounded requests when appropriate; consider batch jobs for large ones, with persisted job IDs and safe resume. Provide progress, cancellation behavior, retry/backoff, deduplication and rate-limit-aware scheduling. Note that cancellation does not undo already incurred provider charges.
- Persist raw DBN where relevant and normalized Parquet derivatives, checksum and full provenance. Partition by provider/dataset/schema/instrument/date; keep immutable raw snapshots and transformation versions.
- Record estimates, actual bytes/cost if available, cache hit/miss, user-approved spend and cumulative session/month estimated usage. Do NOT invent provider billing balances when not exposed by an actual API.
- Respect market-data license, account entitlements, redistribution restrictions and employer-proprietary data boundaries.

### 5.4 Correct Databento time conventions

For OHLCV, provider `ts_event` is the **START of the aggregation interval**. A 1-minute bar starting 09:30 is not complete until 09:31. If a trading rule relies on final OHLCV of that bar, it cannot generate an executable signal at 09:30; enforce information-availability times. Missing bars may mean no trade in that interval, not necessarily erroneous data; use venue/calendar-aware completeness rules.

Keep `ts_event`, any receive timestamp, bar start/end, derived availability time, decision time, submit time and fill time distinct. All engine timestamps UTC internally with exchange local/session display.

### 5.5 Futures-specific accuracy

- Databento supports `continuous` symbols such as `NQ.v.0` or `ES.c.0` subject to dataset support. They map to distinct tradable contracts over time and are **not automatically back-adjusted**. Never treat a stitched price jump as real trading P&L or assume the series is one perpetual instrument.
- Build a point-in-time contract master with daily symbol/instrument-ID mapping, exchange calendar, expiration, tick size, tick value, multiplier, currency, trading hours, price limits and metadata effective dates.
- Define explicit rollover rule (calendar, previous-day volume/open-interest, expiration offset); no future-day selection. Model close/open transactions, spread/slippage/fees and realized P&L on actual contracts. Distinguish continuous signal analysis, raw contract execution and optional adjusted research series. Record all roll events and transformations.
- For example NQ and MNQ are not interchangeable; enforce multiplier/tick value and risk sizing by actual product. Do not assume historical fee schedules, point value or exchange definitions.
- Never use an option or futures instrument outside its trading lifecycle.

## 6. DATA HUB AND DATA QUALITY GATES

Build a dataset manifest with: provider, dataset, schema, requested symbol, resolved instrument IDs by time, symbology mapping, download window, timezone/calendar, number of records, known degraded/missing/pending days, checksums, original raw path, canonical path, update/transform version, metadata snapshots, entitlement/license, source quotation and cost.

Classify errors vs legitimate non-record intervals. Detect: duplicates, incorrect ordering, inconsistent high/low and bid/ask, nonpositive prices where prohibited (beware markets that can legitimately be negative), zero/invalid quantity, gaps inconsistent with venue hours, definition changes, missing sessions, suspicious jumps, stale quotes, outliers, mismatched symbols, bad rolls, unusual volatility, clock anomalies and corporate action effects.

Emit a **Data Fitness Report** specific to strategy and execution method: `FIT`, `FIT WITH LIMITATIONS`, `INSUFFICIENT`, `INVALID` plus evidence and quantitative coverage. Do not allow an intrabar stop/limit strategy to claim precise execution from 1-minute OHLCV when event ordering cannot be reconstructed.

User can choose conservative ambiguity handling or higher-resolution data, but cannot silently bypass critical bias gates. Logs never store secret data or unlicensed redistributable raw market feed.

## 7. STRATEGY ARCHITECT — NATURAL LANGUAGE TO FORMAL RULES

Use a validated, versioned canonical `StrategySpec`. It must be human-auditable and deterministic; do not run free-form LLM text as executable trading logic.

Required entities:
- Instrument universe and point-in-time selection rules.
- Timeframe, source schema, warm-up, bars/quotes/trades timing and relevant clocks.
- Exchange timezone, sessions, holidays, early closes, allowed weekdays/date exclusions.
- Entry signal conditions, information cutoff, order side, order type, pending-order expiration, conflict resolution and maximum frequency.
- Exit conditions (stop/target/trailing/time/signal), OCO behavior, same-bar ambiguity rule, end-of-session liquidation, carry rules.
- Sizing (contracts/shares/risk budget), notional/leverage, rounding, tick increments, account denomination.
- Commission fee profile and date validity, spread/slippage, execution latency/participation constraints.
- Risk controls, exposure limits, max positions, strategy capital, benchmark.
- Test and validation ranges, prespecified hypotheses and parameter bounds.
- Strategy version, assumptions, creation history, provenance and hash.

Compiler converts a restricted expression DSL/AST into typed rules. Use allow-listed indicator definitions and strict parsing. Perform look-ahead checks and reject forward shifts/future joins. Unknown rules stay `REQUIRES CLARIFICATION` or an explicit labelled non-optimistic assumption, never silently optimized.

Support strategy family/version trees. Every LLM-suggested modification becomes a new hypothesis and is recorded as an additional trial, not an invisible improvement to the original.

## 8. BACKTEST SIMULATION — ACCURATE BEFORE FAST

Implement an event-driven, deterministic accounting/execution engine with distinct phases:
`market event → update observable state → signal eligibility → order submission → broker/exchange simulation → fill/partial fill/cancel → position/cash/margin accounting → risk controls → next event`.

Rules:
- The model never knows a completed bar before its end. With bar-close strategies, default fill is **next eligible bar open** (unless specified execution model safely justifies otherwise).
- Support stop/limit/market orders, next-bar fill, pending/partial/cancel events progressively; if a fill model is not implemented, reject it rather than fake results.
- Quote-level data uses bid/ask for side-aware fills; trade-only data cannot infer exact executable spread; bar-only scenarios use explicit assumptions and stress ranges.
- Handle same-bar stop and target both touched: classify **unresolvable from bars**; use configurable conservative ordering or range of outcomes, clearly labelled. No arbitrary optimistic fill.
- Model market impact and latency as bounded assumptions, not facts without depth data. MBO-based queue reconstruction and market impact are advanced separate modules, with limitations even at MBO resolution.
- Tick rounding, contract multipliers, fees per side, exchange/clearing/regulatory fees, financing/borrow where relevant, currency conversion, margin, leverage, liquidation/maintenance constraints and futures roll costs.
- Preserve exact ledger: time, side, asset, signal, submitted order, fill, fees, realized/unrealized P&L, running cash, equity, margin usage and costs. Price/tick arithmetic must avoid floating point rounding errors affecting fill boundaries; use integer ticks and/or Decimal for accounting where appropriate.
- Record seed, engine version, StrategySpec hash, input dataset IDs/hashes, fills/settings, fees and test environment.
- Deterministically repeat the same experiment. Reconciliation tests must prove ledger balances. Use independent hand-calculated fixtures before attempting optimizations.

Implement a data-adequacy matrix:
`Daily/1-minute OHLCV → exploratory bar backtest`;
`trade/bid-ask → improved fill and spread checks`;
`MBP-1 → top-of-book liquidity/slippage analysis`;
`MBO/order book → optional order-level replay under explicit microstructure assumptions`.
Do not label all modes 'tick-accurate'.

## 9. FULL VALIDATION LABORATORY

QuantLab is NOT a backtest leaderboard. It is a hypothesis falsification and risk assessment platform. A test only runs when appropriate to the strategy, available dataset and sample size. Build a versioned `ValidationPlan` with test rationale, inputs, decision rules and exact execution artifacts.

**Core mandatory/eligible tests:**
1. Baseline backtest vs relevant passive/naïve benchmark.
2. Look-ahead/time alignment, survivorship, corporate action, symbol mapping, future roll and data availability checks.
3. Prespecified chronological train/validation/final untouched holdout split with warm-ups and gap/embargo where labels overlap.
4. Out-of-sample degradation metrics, with one-time sealed final holdout policy and transparent disclosure when consumed.
5. Walk-forward anchored and/or rolling train/test windows; no tuning using future test windows.
6. Cost robustness: commissions, bid/ask spread, increased slippage and latency; at least base and stressed scenarios.
7. Parameter robustness: surfaces/plateaus, smoothness, local perturbations, parameter fragility (avoid optimization on the final holdout).
8. Market regimes: volatility, directional/trending vs ranging, hours/session, instrument lifecycle, liquidity regimes, event sensitivity when appropriate. Do not use ex-post regime knowledge as a trading signal.
9. Trade-order/bootstrap simulation preserving serial dependence when appropriate (stationary/block bootstrap, with explicit assumptions) and uncertainty of drawdown/returns.
10. Multiple-hypothesis/selection bias: track every evaluated variant; consider deflated Sharpe / probability of backtest overfitting or other tests when assumptions and sample permit; do not automatically report unjustified p-values.
11. Subperiod stability, seasonality and day-of-week effects with multiple comparison cautions.
12. Cross-instrument/cross-period portability only when economically sensible; no assertion that generalization must hold universally.
13. Exposure, leverage, liquidity/capacity, concentration, tail losses, worst streak, trade clustering, dependence on few outliers.
14. Independent replay/check on another implementation for high-stakes claims (later tier).
15. Forward paper monitoring, live-vs-simulation drift and execution-quality attribution (later tier).

**Validation verdicts:** `INVALID DATA/METHOD`, `INSUFFICIENT EVIDENCE`, `REJECTED`, `PROMISING — FURTHER TESTING`, `ROBUST UNDER TESTED ASSUMPTIONS`, `FORWARD VALIDATION IN PROGRESS`. Never say `GUARANTEED EDGE`, `CERTIFIED PROFITABLE`, or use a simplistic aggregate green score to conceal failures. Show gate-by-gate evidence, confidence and caveats.

Require a minimum-trades/data sufficiency warning configurable by methodology, not a single universal number. Include training/test degrees of freedom and selection history. Every failure should link to supporting data.

## 10. METRICS AND FINANCIAL REPORTING

Implement and document formulas, conventions, annualization, denominators, sampling assumptions and edge-case behavior for:

- Net/gross P&L in instrument and account currency; net return only with explicit capital basis.
- CAGR where interpretable; max drawdown amount/% and underwater duration.
- Sharpe, Sortino, Calmar and volatility using correct frequency/risk-free assumptions.
- Win rate, average gain/loss, payoff ratio, expectancy per trade in currency/ticks/R, profit factor, median results.
- Exposure, turnover, average hold, trades per market/session, win/loss streaks, time in market.
- MAE/MFE, cost contribution, slippage, liquidity/participation/capacity diagnostics.
- Tail outcomes, skew/kurtosis where applicable, risk of ruin only under carefully qualified models.
- Benchmark-relative performance, regime performance, test-to-test degradation, interval estimates and parameter stability.

Handle undefined ratios (e.g. no losses/no trades) as `N/A` with reason, not infinity disguised as performance. Explain uncertainty when sample is small. Metrics must reconcile with the trade ledger.

## 11. EXPERIMENT REGISTRY — REPRODUCIBILITY AND ANTI-OVERFITTING

A `RunManifest` includes immutable identifiers:
- user/project/strategy/family/version, author and human hypothesis;
- StrategySpec canonical JSON/hash; code commit and execution-model version;
- dataset manifest and raw/processed content hashes;
- exact symbols/point-in-time mapping, intervals and market calendar version;
- fees, slippage, latency, sizing, roll assumptions, execution granularity;
- train/validation/holdout partitions, all strategy variants and trials;
- RNG seed, package versions and run environment;
- detailed output refs, QA warnings, validation plan and outcome;
- material decisions, approvals, estimated/accrued data expenses when known.

Persist trade ledger, order/fill events, time series, metrics, logs (redacted), validation artifacts and executive report. Support compare, fork, annotate, rerun, export and audit. Cache by content-addressed compatible manifest; do not mistakenly reuse incomplete or different-schema results.

If rerun cannot be reproduced due to missing licensed data/API access, state the specific dependency. Keep final holdout sealed; subsequent multiple comparisons against the holdout must reduce confidence and be recorded.

## 12. JARVIS AI AND ULTRON TEAM

Reuse actual JARVIS and ULTRON agent APIs if implemented. If nonexistent or incomplete, build clean adapters without claiming workers run when they do not.

Proposed specialist roles:
- **JARVIS:** sole user-facing orchestrator; mission planning and final synthesis.
- **CIPHER:** quantitative hypothesis formalization, validation design, statistical skepticism.
- **ATLAS:** official docs, method research, dataset/source vetting.
- **FORGE:** implementation, refactoring, deterministic engine and UI integration.
- **SENTINEL:** independent reference arithmetic, look-ahead/bias detection, security and test review.
- **PRISM:** usability, accessibility, institutional interface.
- **ARCHIVE:** project memory, experiment retrieval and decision logs.
- **VECTOR:** data jobs, storage, reproducibility and local infrastructure.

Agent system should choose as few workers as needed, not swarm for simple tasks. Tasks require explicit inputs, outputs, permissions, budgets, timeouts, verification criteria and artifact references. No ad hoc uncontrolled agent group chats. Record meaningful activity and tool evidence; do not expose hidden reasoning traces.

Crucially: the LLM may **propose** strategies and tests, but only deterministic code calculates trading outcomes. AI critique cannot overwrite source trades or fabricate statistical findings. The agents cannot authorize their own paid downloads or bypass user permissions. Prompt injection from market-data metadata, web pages or documents must not become system instructions.

## 13. API AND CONTRACT OUTLINE

Adapt names to repository but retain functionality:

**Connections:**
- `POST /api/quantlab/connections/databento` securely stores/replaces supplied key.
- `POST /api/quantlab/connections/databento/test` performs authenticated metadata-only check.
- `GET /api/quantlab/connections/databento/status` returns redacted state.
- `DELETE /api/quantlab/connections/databento` revokes local credential.

**Data:**
- `GET /api/quantlab/data/catalog`, `/data/instruments`, `/data/coverage`, `/data/quality`
- `POST /api/quantlab/data/quote` produces an immutable estimate/quote ID.
- `POST /api/quantlab/data/requests` with validated quote + explicit approval/budget authorization.
- `GET /api/quantlab/data/jobs/{id}` for progress; retry/cancel with safe semantics.
- `GET /api/quantlab/data/cache` metadata and local reuse.

**Research:**
- `POST /api/quantlab/strategies/interpret`, `/strategies`, `/strategies/{id}/versions`
- `POST /api/quantlab/backtests` creates a job; `GET /backtests/{id}` and `/backtests/{id}/trades`.
- `POST /api/quantlab/validations` starts a plan; `GET /validations/{id}`.
- `GET /api/quantlab/experiments`, `/experiments/{id}`, `/experiments/compare`.
- `POST /api/quantlab/reports`, `GET /api/quantlab/reports/{id}`.
- `WS /api/quantlab/events` OR reuse JARVIS event stream with typed namespace.

Use strict typed Pydantic inputs and TypeScript/generated client, bounded pagination, filters, idempotency keys, clear error envelopes, authorization, cancellation and audit events.

Status state machine: `DRAFT → READY → QUEUED → RUNNING → WAITING_FOR_APPROVAL → COMPLETED / FAILED / CANCELED`. Avoid invalid transitions. Job state survives restarts; no lost approval pending state.

## 14. SECURITY, PRIVACY, SPEND CONTROL

- Never expose credentials to AI context, raw logs, analytics, crash reporters, renderer storage or repos.
- Scope tool permission to exact mission/action/dataset/budget. No indiscriminate shell use; sandbox generated strategy code. Validate CSV/Parquet and archives for path traversal/resource exhaustion and schema maliciousness.
- Paid data downloads always show preview and request approval; no `auto-purchase` even in JARVIS Autonomous Mode.
- No live broker trading, funding, deposits, withdrawals, or real-money orders in this project. Paper trading must be clearly separated and disabled until implemented.
- Avoid employer/private banking data unless user has explicit rights to use it in this personal application.
- Local-first data residency by default; outbound AI requests should not include raw licensed market data unless permitted by data licenses and explicitly configured. Prevent unlicensed market-data redistribution in exports/sharing.
- Provide a prominent `Stop all research jobs` and graceful cancellation, credential revocation, quota management and durable audit trail.
- Use reasonable computational budgets, memory and disk quotas; monitor runaway worker tasks.
- Strictly separate LLM-generated recommendations from user-authorized external actions.

## 15. RELEASE PLAN — DEEP, BUT IMPLEMENT VERTICALLY

**R0 — Recon & Contracts:** inspect repo, preserve current functionality, map integration points, record ADRs, wire QuantLab nav and backend skeleton, secure foundation/tests.

**R1 — Real Databento Connection:** UI key entry → secure OS keyring → authenticated metadata-only connection test → available datasets/schemas/date coverage → errors; UI data catalog and cost quote, approval policy without accidental charge. Test with mocks offline; exercise real metadata only once user enters credential in app. Do not mark `connected` without real verification.

**R2 — Ingest & Data Truth:** approved small bounded Databento download → immutable cache → canonical Parquet → dataset manifest → quality/coverage report → preview chart. CSV import also supported. Test fail/missing/degraded scenarios.

**R3 — Honest Backtesting Core:** restricted moving-average / session breakout strategy from a clear RuleSpec; correct time/next-open and costs; fill ledger, portfolio reconciliation, deterministic tests; Backtest Lab with real results on approved or imported data. Synthetic fixture is clearly labeled and exists only for offline tests/demo.

**R4 — AI Strategy Studio:** JARVIS conversational strategy interpretation → structured editable rules → missing-parameter resolution → strategy compiler → traceable versioning. AI cannot execute arbitrary generated trading code on host.

**R5 — Validation V1:** chronological OOS, walk-forward where sample allows, baseline, transaction cost stress, parameter sensitivity, Data Fitness and concise evidence verdict; Experiment Registry with reruns. Ship a coherent end-to-end path.

**R6 — Institutional Validation:** robust bootstrap, repeated-trial registry, regime analysis, selection-bias diagnostics, capacity/liquidity controls, independent engine tests. Report limitations per test.

**R7 — Microstructure & Futures:** contract-roll correctness, trade/quote fidelity, MBP-1/MBO replay where actually implemented, multiple asset classes through capability matrix; more extensive stress scenarios.

**R8 — Intelligence & Scale:** mature ULTRON research workers, research journal, insight discovery, paper monitoring, scalable storage/compute/queue and export workflows with entitlements.

At the end of each release show actual feature proof, tests, run logs and any skipped/mocked items; then move to the next feasible release without asking ordinary questions. Do not hide incomplete milestone work behind optimistic check marks.

## 16. REQUIRED ACCEPTANCE TESTS — FINANCIAL TRUTH

At least implement/plan and run tests for:

**Databento/credentials:** valid test metadata connection; invalid/revoked key, no entitlement, unsupported schema, offline, degraded/missing dates; secure storage/retrieval/revocation, no key in logs/files/API status; no historical download without approved quote; approved cost ceiling/expiry/mismatch denial; cache deduplication and idempotent retry; accurate estimate display + estimate caveat.

**Timestamps/timezones:** bar timestamp = START; bar closing values not usable before completed bar; next-bar fills; overnight CME sessions, exchange holidays, DST (America/New_York), half days and weekend boundary; no illegal fill on non-trading intervals.

**Futures:** correct `instrument_id` on each date, symbol changes, volume-based roll based on previous day's data, close/open and fees at roll, multiplier/tick price arithmetic, no fake continuous-contract jump P&L, expiration lifecycle.

**Execution:** hand-calculated 3-to-10 bar fixtures for long/short, entries/exits, multiple commissions, spread/slippage, stop/limit, same-bar high/low ambiguity, no negative quantity, no doubled fees, account reconciliation, leverage exposure, correct P&L currency.

**Statistical:** strict chronological splits, warmup without leakage, locked final holdout, no final-holdout parameter search, walk-forward independent re-fit, trial history for every variant, undefined metrics displayed correctly, bootstrap seed reproducibility, outlier stress.

**UX/integration:** create strategy, validate exact RuleSpec, see price quote, approve download, inspect Data Fitness, run strategy, view real trades and results, run available validation, compare variants, export report; keyboard navigation; real event progress; disabled actions explained; restart-resume.

**Security:** secret redaction tests, strategy sandbox isolation, permission escalation denial, local auth/CSRF, untrusted-file sanitization, no autonomous spending, no broker connector enabled for money actions.

Independent reference fixtures must compute expected ledger/metrics by hand or separate script; never test implementation against itself. Build at least one full e2e run with user-imported/approved real historical data if actually available. When no Databento credential is provided, do not pretend to have real access: use clearly labeled deterministic sample fixtures, complete provider connection functionality, and explain what remains to verify once key is entered.

## 17. EVIDENCE, REVIEW AND MEASURABLE QUALITY BAR

Definition of DONE for any claimed feature:
1. Source implementation exists with types/schema/docs.
2. Unit tests and integration tests pass.
3. User path demonstrated in running application/environment when available.
4. Inputs, data rights, timing and costs are transparent.
5. Related edge cases, error handling and failure states are tested.
6. Artifacts are reproducible; limitation flags are truthful.
7. Code quality, basic security and performance reviewed.

Technical quality targets are targets to benchmark, not invented benchmarks: responsive common UI, no input jank, virtualized long tables, compute jobs cancelable and resumable, controlled peak memory, reproducible 100% deterministic reference tests, controlled provider spend and adequate telemetry for failures. Report observed measurements, hardware and dataset, not unsupported claims.

Use the independent reviewer/SENTINEL where available and include explicit `KNOWN LIMITATIONS` in release notes. Never promise forward profitability.

## 18. FIRST USER-DEMONSTRABLE MISSION

**Scenario:**

1. JARVIS opens QuantLab → Data Hub and displays `Databento not connected` when no credential has been entered.
2. User pastes their key into QuantLab UI (not terminal/chat) and clicks `Connect`.
3. App validates credentials via metadata, shows provider status and available user-entitled catalog.
4. User describes a simple **NQ futures opening-range hypothesis**. JARVIS displays clear strategy rules, asks for any essential unspecified execution details rather than inventing them.
5. QuantLab proposes compatible market schema/time range and an **estimated paid download quote**.
6. User explicitly approves a budget-limited acquisition; app ingests data and displays quality/coverage report.
7. JARVIS executes a non-leaking, deterministic baseline with actual multiplier, fees and fill assumptions.
8. Backtest Lab shows chart, trade ledger, drawdowns and uncertainty; every result has provenance.
9. Validations run if scientifically admissible, with chronological OOS, cost stress, parameter robustness.
10. JARVIS concludes with evidence-supported verdict, specific weaknesses and next experiment. Save entire replayable experiment.

If an input entitlement or historical depth prevents the scenario, explain that limit and provide a valid lower-cost/equivalent workflow. NEVER invent successful results.

## 19. FIRST EXECUTION ORDER — START NOW

1. Inspect `C:\dev\jarvis` or discover repo and existing instruction files; identify app shell, ULTRON status, QuantLab status and UI stack.
2. Run existing tests and snapshot repo state safely.
3. Create refined integrated architecture + short ADRs and release checklist.
4. Implement the secure Databento UI and backend credential service; tests before paid downloads.
5. Implement metadata test, dataset/schema/range/condition search, cost quoting, approval workflow.
6. Integrate Data Hub with JARVIS shell, matching exact design language.
7. Implement small ingestion path, quality gates, first deterministic backtest + results UI.
8. Run independent numeric regression tests and true e2e scenarios; fix failures.
9. Implement next gated release, keeping application operational.
10. Maintain a concise implementation status with exact observed evidence, remaining risks, cost implications and next milestone.

Do **not** ask the owner to download this prompt as a file, copy packages, make directories, set keys in code or run shell commands. They have delegated all technically possible development to you. You may need them to enter their own Databento key in the secure in-app form and to explicitly authorize paid API data usage. Those are the only normal user interactions required.

**BUILD THIS. DO NOT STOP AT PLANNING.**

---

## PRIMARY OFFICIAL API REFERENCES — VERIFY CURRENT SIGNATURES BEFORE CODING

- https://databento.com/docs/api-reference-historical
- https://databento.com/docs/knowledge-base
- https://databento.com/docs/standards-and-conventions/symbology
- https://databento.com/docs/portal/api-keys
- https://databento.com/docs/examples/symbology/continuous
- https://databento.com/docs/examples/symbology/parent-symbology
- https://databento.com/docs/examples/basics-historical/encodings

These URLs are primary references, not a replacement for checking the versions actually installed.
