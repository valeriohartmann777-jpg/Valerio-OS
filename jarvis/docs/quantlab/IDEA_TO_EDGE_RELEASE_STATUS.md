# Idea-to-Edge — release status (A16)

As of 10 October 2026, branch `claude/jarvis-foundation-mxnsz4`. Legend:

- **DONE** — implemented and wired end to end.
- **TESTED** — covered by automated tests and/or the desktop E2E (see
  [`IDEA_TO_EDGE_TEST_EVIDENCE.md`](IDEA_TO_EDGE_TEST_EVIDENCE.md)).
- **MOCKED** — exercised only against a stand-in (scripted model, fixture provider, mock HTTP).
- **BLOCKED** — cannot be verified in the build container; needs the owner's Mac, account or
  network access. Nothing here is claimed to work until it has been seen working there.
- **NEXT** — not built yet.

## R0 — Inspect & reconcile

| | |
|---|---|
| DONE | Repo, QuantLab (R1 lab, Institutional futures core, Data Hub), ULTRON and the mission bus mapped; Idea-to-Edge built on top of them (same Data Hub, research registry, validation suite, ULTRON runner). Migrations 13/14 are additive. QuantLab now opens on the Idea Inbox; every earlier section and test id is unchanged. |
| TESTED | Full backend suite and the full desktop E2E, including every pre-existing step. |
| MOCKED | — |
| BLOCKED | — |
| NEXT | — |

## R1 — One-box Idea Intake

| | |
|---|---|
| DONE | Drop zone / paste text / paste link; `.txt`, `.md`, MP4/MOV/M4V/WebM/MKV, audio, images, PDF; magic-byte detection, size and duration caps, archive refusal; SHA-256 dedupe; private storage (0600) with retention and deletion; sandboxed worker with rlimits; Moonshine speech with timecodes (English), Whisper path for other languages (free one-time model install from the UI); OCR on every sampled frame, keyframes; PDF text + embedded-image OCR; injection tripwire; TikTok/YouTube oEmbed metadata with upload fallback and attach-to-link; SSRF guard; source view with player seeking to timecodes, segments, keyframes, coverage, audit. |
| TESTED | IN-01…IN-06, UX-02 (backend); video upload → speech + on-screen text + keyframes + injection + boast call-outs in the real app (E2E). |
| MOCKED | TikTok/YouTube oEmbed responses (`httpx` MockTransport) — real endpoints are not reachable from the container. |
| BLOCKED | Real TikTok oEmbed call; faster-whisper model download (Hugging Face blocked here) and German/other-language speech; the macOS Seatbelt profile for the worker and the `moonshine-cpp` macOS wheel (needs macOS 15+) have not run on the owner's Mac yet. |
| NEXT | Verify on the MacBook with a saved TikTok video. Authorized TikTok Display API (OAuth, the owner's own videos) is not built. |

The fixture video proves the pipeline on one known file; it does not prove that arbitrary
TikTok exports transcribe well.

## R2 — Exact Strategy Blueprint

| | |
|---|---|
| DONE | Claims with verbatim-checked quotes; boasts and AI-instructions always recorded; FuturesSpec DSL extended (`level_sweep_reclaim`, `opening_range_retest`, higher-timeframe MA, filters, `setup_extreme` stop, prior-session levels from the real previous session); per-field provenance classes; ambiguity catalog with fixed alternatives; unsupported-concept list; computed status; one grouped question; deterministic `resolve()`; plain-language rules and a labelled worked example; blueprint view with provenance table. No generated code is executed. |
| TESTED | SP-01…SP-04 (backend, hand-calculated DSL cases incl. the A14 example); question form in the real app (E2E). |
| MOCKED | Claude's claim and blueprint submissions are scripted in tests and E2E. |
| BLOCKED | Quality of real Claude extraction on real videos — needs the owner's Claude key. |
| NEXT | Field-level blueprint editor (today: grouped answers and notes; frozen versions can be edited in Strategy Studio). |

## R3 — First truthful test

| | |
|---|---|
| DONE | Missions reuse the Institutional Data Hub: library reuse → fresh quote → explicit approval with maximum and mission cap → download (cached days never re-bought) → dataset with passport; deterministic engine with costs, ledger, charts. Approval only through the UI (`confirm: true`); no agent can approve. |
| TESTED | DATA-01…DATA-03, SIM-01, SIM-02; approval panel and purchase in the real app with the fixture provider (E2E). |
| MOCKED | Databento — the offline fixture provider (real DBN format, synthetic prices, labelled everywhere). |
| BLOCKED | A real Databento quote and purchase — needs the owner's key in the Data Hub and an approval. |
| NEXT | — |

## R4 — Evidence Gates

| | |
|---|---|
| DONE | Protocol pre-registered and hashed before data; chronological OOS with embargo; sealed holdout; validation suite; SENTINEL boundary and audit reports (second engine trade by trade, ledger sums, signal-before-fill, holdout state, verdict recomputation, trial count, synthetic cap), sealed with SHA-256; A11 verdict mapping; narrative guard; dossier with three separate planes (Markdown + JSON). |
| TESTED | AG-02, VAL-01; second engine agreement on 14 scenarios × 2 fill modes × 2 cost multipliers; verdict, audit and dossier in the real app (E2E). |
| MOCKED | Closing note text is scripted. |
| BLOCKED | — |
| NEXT | — |

## R5 — Quant Council

| | |
|---|---|
| DONE | Durable missions with typed tasks per stage, inputs hashes, actions, artifacts, limitations, costs; explicit waiting states; pause / resume / cancel; restart recovery; immutable hashed budgets checked before every model call; Research Room with stage stepper, roster from real tasks, task timeline, checks, verdict; evolution as child missions with lineage. |
| TESTED | AG-01 (alternatives with provenance), AG-03, UX-01, UX-03 (backend); the whole room in the real app (E2E). |
| MOCKED | ATLAS / CIPHER / JARVIS replies are scripted (labelled "Scripted test model" in the UI). |
| BLOCKED | Real multi-agent runs on Claude — needs the owner's Claude key; cost per mission on real models not yet measured. |
| NEXT | A9 "challenge session" (Strategy Council debate) is not built. Running ATLAS and CIPHER interpretations as competing parallel trials is not built; alternatives are offered as owner choices. |

## R6 — Bounded improvement

| | |
|---|---|
| DONE | Evolution engine: diagnose → propose (allowed paths only, mechanism + prediction) → test (new frozen version, holdout sealed, audited) → compare (Pareto) → repeat within budget → one-time holdout lock by the owner; append-only trial ledger (database triggers); family-wide deflated Sharpe; contamination recorded and the verdict downgraded after any further holdout look. Walk-forward, bootstrap, regimes and parameter sensitivity come from the existing validation suite. |
| TESTED | VAL-01, VAL-02 (backend); evolution, Pareto chart and lock in the real app (E2E). |
| MOCKED | CIPHER's variant proposals are scripted. |
| BLOCKED | — |
| NEXT | A visual version graph (today: trial table, Pareto chart, parent/child mission links). |

## R7 — Institutional polish

| | |
|---|---|
| DONE | Media rights handling (retention, deletion, lineage, no media to models); restart recovery; execution fidelity stated in every dossier (`BAR_ONLY_CONSERVATIVE`). |
| TESTED | Retention/deletion/restart (backend). |
| MOCKED | — |
| BLOCKED | — |
| NEXT | Tick/MBP-1/MBO replay for intrabar ordering; more instruments beyond CME equity index futures; richer performance UI; cost reconciliation of actual Databento billing against quotes. |
