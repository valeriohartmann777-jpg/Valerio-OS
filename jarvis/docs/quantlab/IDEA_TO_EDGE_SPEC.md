# QuantLab 3.0 — Idea-to-Edge: specification as built

Binding source: [`IDEA_TO_EDGE_DIRECTIVE.md`](IDEA_TO_EDGE_DIRECTIVE.md) (the owner's master prompt,
copied verbatim). This document describes what the code does today. Status per release:
[`IDEA_TO_EDGE_RELEASE_STATUS.md`](IDEA_TO_EDGE_RELEASE_STATUS.md). Evidence:
[`IDEA_TO_EDGE_TEST_EVIDENCE.md`](IDEA_TO_EDGE_TEST_EVIDENCE.md).

## 1. What it does

One inbox takes a strategy idea in whatever form the owner has it, turns it into exact
testable rules with provenance, and runs a research mission that ends in an honest verdict
and a dossier. A bounded evolution loop can then test reasoned variants.

```
saved video / audio / screenshot / PDF / text / TikTok or YouTube link
  → intake (magic bytes, size caps, SHA-256 dedupe, private storage, retention)
  → extraction on this Mac (speech with timecodes, on-screen text, keyframes, PDF/image text)
  → claims (model proposes, code checks every quote word for word; boasts and
    instructions-to-AI are always recorded as claims)
  → blueprint (FuturesSpec + per-field provenance + ambiguity catalog + one grouped question)
  → boundary check (SENTINEL) → freeze version → test protocol (CIPHER)
  → data (VECTOR: reuse library → quote → owner approval → download → dataset)
  → baseline validation run (holdout sealed) → SENTINEL audit (second engine)
  → verdict (A11 taxonomy) → dossier (source facts / test facts / interpretation)
  → optional evolution: diagnose → propose → test → compare (Pareto) → holdout once
```

Nothing in this path can place an order, connect a broker or move money. The only
spend paths are model tokens (capped per mission, checked before every call) and Databento
data (always quote + explicit owner approval in the UI).

## 2. Entry modalities (A2)

| Input | Handling | Result |
|---|---|---|
| Pasted text | normalized, exact character spans per paragraph | `text` source, `EXTRACTED` |
| `.txt` / `.md` | bytes stored, same as text | `text_file` |
| MP4 / MOV / M4V / WebM / MKV | probe → speech (Moonshine base-en, or Whisper for other languages once installed) → frames sampled, OCR on every sample, keyframes on text/scene change or every 6 s | `video`, segments `speech` + `onscreen` with `start_ms`/`end_ms` |
| MP3 / M4A / WAV / AAC / OGG / FLAC | speech only | `audio` |
| PNG / JPEG / WebP / HEIC | OCR | `image` |
| PDF | text layer per page (pypdf) plus OCR of images embedded in the page; first 200 pages | `pdf` |
| TikTok / YouTube link | public oEmbed metadata only (title, author) — never a download | `METADATA_ONLY`/`EMBED_ONLY`, then `NEEDS_UPLOAD`; the owner attaches the saved file, which stays linked |
| Any other link | refused with an upload hint | `REQUIRES_UPLOAD` |

Limits (`detect.LIMITS`): video 512 MB and 15 minutes (`intake.max_video_minutes`), audio 200 MB,
image 25 MB, PDF 40 MB, text 2 MB. Archives, executables and extension/content mismatches are
refused before decoding. The worker runs as a separate process
(`python -P -m jarvis.quantlab.intake.worker`) inside the ULTRON sandbox (Seatbelt on macOS,
network namespace on Linux) with CPU/memory/file rlimits and a timeout proportional to the media
length.

## 3. Claims (A4, A5)

`ideas/claims.py`. ATLAS receives the source as quoted, untrusted data (`<source>` block with
segment ids, modality, time, quality and an explicit warning on instruction-like text) plus the
owner's notes. It must call `submit_claims`; code then checks:

- every cited segment exists; every quote appears in the cited segments word for word
  (case/punctuation-insensitive), otherwise the submission is refused and retried;
- kinds are `RULE`, `PERFORMANCE_CLAIM`, `CONTEXT`, `MARKETING`, `INSTRUCTION_TO_AI`, `OTHER`;
- `augment()` adds a `PERFORMANCE_CLAIM` for every "x% win rate"-style statement and an
  `INSTRUCTION_TO_AI` claim for every flagged segment, whatever the model did.

Every claim carries `trading_truth: NOT_TESTED`. Source trust is never an input to any metric.

## 4. Blueprint (A4)

`ideas/blueprint.py`, `ideas/catalog.py`, `futures/spec.py`.

- The rules are a constrained `FuturesSpec` (no generated code). Rule types:
  `opening_range_breakout`, `ma_crossover` (optionally on completed higher-timeframe bars),
  `level_sweep_reclaim`, `opening_range_retest`; filters; stops incl. `setup_extreme`.
- Every critical field has a provenance class: `EXPLICIT_SOURCE` (cites claims),
  `USER_SPECIFIED`, `INFERRED_NONCRITICAL`, `DEFAULT_RESEARCH_ASSUMPTION`, `MISSING_BLOCKING`.
  Critical fields cannot be inferred.
- The ambiguity catalog defines terms with fixed alternatives as spec patches
  (`ny_open`, `opening_range`, `liquidity_sweep`, `retest`, `stop_beyond_wick`, `r_multiple`,
  `candle_timeframe`). Unsupported concepts (FVG, order blocks, VWAP, news, …) are listed as
  untestable, never replaced silently.
- Status is computed: `DRAFT`, `NEEDS_DEFINITION`, `READY_FOR_DATA`, `READY_TO_TEST`, `INVALID`.
- Open terms and blocking fields become one grouped question. `resolve()` applies the answer
  deterministically (no model) and writes a new blueprint version; accepted defaults are
  labelled as defaults everywhere.
- On read, the API adds `what_it_does` (plain-language rules) and `illustration` (a worked
  example with made-up prices, labelled as such).

## 5. Research mission (A6, A7, A11)

`ideas/missions.py`, `ideas/store.py`, `ideas/sentinel.py`, `ideas/dossier.py`. Full protocol:
[`ULTRON_QUANT_PROTOCOL.md`](ULTRON_QUANT_PROTOCOL.md).

Stages: `register → claims → blueprint → boundary → freeze → protocol → data → baseline →
audit → verdict`. Each stage is one typed task with an agent, inputs hash, allowed tools,
actions, artifacts, limitations and cost. Waiting states are explicit (`WAITING_USER`,
`WAITING_APPROVAL`, `BLOCKED`, `PAUSED`); a mission is auto-started when a source is read
(`intake.auto_research`), one live mission per source.

## 6. Evolution (A8)

`ideas/evolution.py`. A child mission of a completed idea mission with its own immutable
budget (paid data is 0 — evolution reuses the parent's dataset):

- **diagnose** the baseline (trade distribution, costs, sessions);
- **propose**: CIPHER submits variants that may only change allowed paths (rule-type
  parameters, stop/target, entry cutoff, filters), each with mechanism and falsifiable prediction;
- **test**: each variant is a new frozen strategy version, run with the holdout sealed,
  audited by SENTINEL, and appended to the trial ledger (append-only by database trigger);
- **compare**: Pareto front on OOS net ↑, OOS trades ↑, drawdown ↓, complexity ↓; the family's
  deflated Sharpe counts every trial;
- loop until `max_iterations`/`max_variants`/model budget;
- **holdout**: the owner locks exactly one candidate (`confirm: true`); its holdout is
  evaluated once. Any later look in the same family is recorded as contaminated and the verdict
  is downgraded.

## 7. UI

QuantLab opens on **Idea Inbox** (drop zone, paste text/link, note, spoken language,
source cards with mission chips). **Source view**: player that seeks to timecodes, segments,
keyframes, claims with quote verification, injection and performance call-outs, blueprint with
provenance table, notes, coverage and audit. **Research Room**: missions, stage stepper,
waiting panels (questions, Databento connection, paid-data approval with maximum and
confirmation, holdout lock), ULTRON roster from real task records, task timeline, verdict,
checks, evolution (trial ledger, Pareto chart, lock), dossier (read, Markdown, JSON).

## 8. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/quantlab/sources` | inbox |
| POST | `/quantlab/sources/intake` | `{text}` or `{url}` (+ `note`) |
| POST | `/quantlab/sources/intake/file` | raw body; `X-Filename`, `X-Note`, `X-Language`, `X-Link-Source` (percent-encoded) |
| GET/POST | `/quantlab/sources/speech-model` | multilingual model status / free install |
| GET | `/quantlab/sources/audit` | source audit log |
| GET/DELETE | `/quantlab/sources/{id}` | detail / delete (cancels its open missions first) |
| POST | `/quantlab/sources/{id}/extract`, `/cancel`, `/notes` | control |
| GET | `/quantlab/sources/{id}/frames/{frame}` , `/media` | keyframe JPEG / original (private, no-store) |
| DELETE | `/quantlab/sources/{id}/media` | delete the original, keep evidence |
| GET/POST | `/quantlab/research/missions` | list / create `{source_id, budget?}` |
| POST | `/quantlab/strategies/from-source/{id}` | same as create |
| GET | `/quantlab/research/agents`, `/activity` | roster from tasks / activity |
| GET | `/quantlab/research/missions/{id}` (+ `/agents`, `/trials`, `/verdict`, `/dossier`, `/dossier.md`) | read |
| POST | `…/answers` | grouped answer `{accept_defaults, choices, values}` |
| POST | `…/data-approval` | `{max_usd, confirm: true}` — the only purchase path |
| POST | `…/data-decline`, `…/pause`, `…/resume`, `…/cancel` | control |
| POST | `…/evolution` | start evolution `{budget?}` |
| POST | `…/holdout-lock` | `{version_id, confirm: true}` |

Events: `quantlab.source`, `quantlab.mission` on the existing WebSocket bus.

## 9. Storage

Migration 13: `qs_sources`, `qs_segments`, `qs_frames`, `qs_notes`, `qs_extractions`,
`qs_claims`, `qs_blueprints`, `qs_audit`. Migration 14: `qm_missions`, `qm_tasks`,
`qm_events`, `qm_trials` (`qm_trials` and `qm_events` reject UPDATE/DELETE by trigger).
Media lives under `<data>/quantlab/sources/<id>/` with mode 0600; dossiers under
`<data>/quantlab/missions/<id>/`.

## 10. Settings

`quantlab.intake`: `speech` (`moonshine` | `whisper` | `off`), `whisper_model`, `default_language`
(`en`), `ocr`, `max_video_minutes` (15), `keep_media_days` (7), `worker_timeout_factor` (6),
`auto_research` (true). Mission budget defaults: model $2.00, paid data $15.00, 6 variants,
150 parameter combinations, 30 compute minutes, 2 iterations, 1 retry; hard ceilings in
`BUDGET_LIMITS`. Agent models: `config/ultron.yaml` (`atlas`, `cipher`, `jarvis`).
