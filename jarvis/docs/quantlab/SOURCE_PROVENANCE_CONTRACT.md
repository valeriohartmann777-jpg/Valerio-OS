# Source provenance contract

What JARVIS may say about a source, and how every statement stays traceable to it. Binding
for the intake (`backend/jarvis/quantlab/intake/`), claims and blueprints
(`backend/jarvis/quantlab/ideas/`), the dossier and the UI.

## 1. Three planes that never mix

| Plane | Contains | Never contains |
|---|---|---|
| **Source facts** | what was said, shown or written, where and when | anything about whether it works |
| **Test facts** | sealed runs: data passport, trades, metrics, tests, audit hashes | anything the source claimed |
| **Interpretation** | the verdict and JARVIS's note, checked against the test facts | numbers that are not in the evidence |

A performance statement in a source ("90% win rate") is a source fact of kind
`PERFORMANCE_CLAIM`. It is never displayed as a result, never used to rank, score or select a
strategy, and every claim carries `trading_truth: NOT_TESTED`. Source trust is not an input to
any metric.

## 2. Source asset (`qs_sources`)

| Field | Meaning |
|---|---|
| `id` | stable id, used by every derived record |
| `kind` | `text`, `text_file`, `pdf`, `video`, `audio`, `image`, `link` |
| `fingerprint` | SHA-256 of the exact bytes (or the normalized link); identical input = the same source |
| `status` | `QUEUED`, `RESOLVING`, `EXTRACTING`, `EXTRACTED`, `PARTIAL`, `NEEDS_UPLOAD`, `REJECTED`, `FAILED`, `CANCELED` |
| `capability` / `fallback` | for links: `METADATA_ONLY`, `EMBED_ONLY`, `REQUIRES_UPLOAD`, `UNAVAILABLE` |
| `media_path` | private file (0600) under the data dir; never returned by the API |
| `media_expires_at` / `media_deleted_at` | retention (`keep_media_days`), owner deletion |
| `linked_source_id` | an uploaded file attached to a link source |
| `meta.coverage` | what was actually read: speech seconds and provider, frames sampled, OCR provider, pages |
| `meta.warnings` | everything that was not read or read badly |

`PARTIAL` means some evidence is missing or low quality; the warnings say which.

## 3. Evidence segment (`qs_segments`)

One continuous piece of evidence. Exactly one locator applies:

| Modality | Locator | Provider |
|---|---|---|
| `text` | `char_start`, `char_end` into the normalized text (exact spans) | — |
| `speech` | `start_ms`, `end_ms` of the voiced chunk | `moonshine-base-en` or `faster-whisper-<size>` |
| `onscreen` | `start_ms`, `end_ms` of the run of frames showing the same text; `frame_id` of its keyframe | `rapidocr-ppocr` |
| `pdf_page` | `page`, `char_start`, `char_end` | `pypdf` |
| `pdf_page_image`, `image_text` | `page` (PDF) or none (image) | `rapidocr-ppocr` |
| `metadata` | link title/author from public oEmbed | provider name |

Each segment has `quality` (`ok`, `low`, `unintelligible`), an optional `confidence`, and
`flags` (`instruction_like` when the injection tripwire matches). Segments are replaced as a
whole on re-extraction; the audit log records each extraction.

**Never fabricated:** no transcript text without an ASR result, no timecode that is not the
measured chunk or frame time, no on-screen text that OCR did not return, no price level or
indicator value that the source did not state. When speech is unclear the segment is stored
with its quality, not "cleaned up".

## 4. Keyframe (`qs_frames`)

JPEG under the source folder, primary key `(source_id, id)`, with `t_ms`, `sha256`, size,
`scene_score` and the OCR text. Stored when the on-screen text changes, the scene changes
(score ≥ 0.08) or every 6 seconds. Served only by `GET /quantlab/sources/{id}/frames/{frame}`.

## 5. Claim (`qs_claims`)

Proposed by ATLAS, checked by code (`ideas/claims.py`):

- `segment_ids` must exist; at least one is required.
- `quote`, if given, must appear in the cited segments word for word (case and punctuation
  insensitive). Otherwise the whole submission is refused with the reason and the model retries.
  `quote_verified` records the result.
- `kind`, `extraction_confidence`, `claim_status` (`defined`, `partially_defined`, `undefined`,
  `unsupported`) and `unresolved` (what is missing) are required.
- Rule-based claims are always added (`origin: rule`): one `PERFORMANCE_CLAIM` per boast,
  one `INSTRUCTION_TO_AI` per flagged segment.

## 6. Blueprint field provenance (`qs_blueprints.provenance`)

Every critical field of the `FuturesSpec` has exactly one class:

| Class | Meaning | Requirement |
|---|---|---|
| `EXPLICIT_SOURCE` | the source states it | cites claim ids (hence segments) |
| `USER_SPECIFIED` | the owner defined it | an answer or a note id |
| `INFERRED_NONCRITICAL` | inferred | never for entry, exit, risk or session fields |
| `DEFAULT_RESEARCH_ASSUMPTION` | a declared test default | named in the dossier and in every result |
| `MISSING_BLOCKING` | undefined | the blueprint cannot be tested |

Ambiguous terms are resolved only by choosing one of the catalog's alternatives
(`ideas/catalog.py`), recorded with basis `source`, `user`, `default` or `open`. An `open`
material term blocks testing. Unsupported concepts are listed with a reason and are never
approximated silently.

## 7. Untrusted content

- Extracted text reaches a model only inside a quoted `<source>` block, after the system
  prompt, labelled as data; instruction-like segments are marked in that block.
- No model output is executed. The DSL is data; the engine is fixed code.
- Tools available to ATLAS, CIPHER and JARVIS in a mission are only their submit tools.
  None can buy data, change settings, call the network or touch files.
- Every model call that receives source text is logged as `TEXT_SENT_TO_MODEL` with the
  character count and `media_sent: false`. Video, audio and images never leave the computer.

## 8. Rights, retention, deletion

- JARVIS never downloads TikTok, YouTube or other platform media and never uses cookies or
  logins. Links yield public metadata only; the owner uploads files they are allowed to use.
- Originals are kept for `keep_media_days` (default 7), then deleted by the hourly purge; the
  owner can delete the original or the whole source at any time. Derived evidence (text,
  timecodes, keyframes) stays until the source is deleted.
- Deleting a source cancels its open research missions first; their task, event and trial
  records remain (append-only).
- The dossier records lineage (source id, fingerprint, extraction ids, blueprint version, spec
  hash, dataset id, run id, audit hashes) and states that it contains derived text, not media.
