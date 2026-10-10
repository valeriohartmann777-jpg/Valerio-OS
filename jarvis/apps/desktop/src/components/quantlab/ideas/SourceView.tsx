import type { QmMissionRow, QsClaim, QsSegment, QsSource } from "@jarvis/protocol";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, api } from "../../../lib/api";
import { Button, cx } from "../../ui/primitives";
import { Badge, Card, ago, inputClass } from "../ui";
import { Empty, Kv, bytes, type Mode } from "../research/common";
import { BlueprintView } from "./BlueprintView";
import { CLAIM_KIND, MissionState, MissionVerdict, OPEN_STATES, SourceStatus, tc } from "./common";

/** One source: the original (if kept), what was read and when, claims with quotes, the blueprint. */

const MODALITY: Record<string, string> = {
  speech: "Speech",
  onscreen: "On screen",
  text: "Text",
  pdf_page: "PDF page",
  pdf_page_image: "PDF page (image)",
  image_text: "Image text",
  metadata: "Metadata",
};

export function SourceView({
  sourceId,
  tick,
  mode,
  onBack,
  onOpenMission,
}: {
  sourceId: string;
  tick: string | null;
  mode: Mode;
  onBack: () => void;
  onOpenMission: (id: string) => void;
}) {
  const [source, setSource] = useState<QsSource | null>(null);
  const [missions, setMissions] = useState<QmMissionRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [focus, setFocus] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const video = useRef<HTMLVideoElement>(null);

  const load = useCallback(async () => {
    try {
      const [s, m] = await Promise.all([api.qsSource(sourceId), api.qmMissions()]);
      setSource(s);
      setMissions(m.filter((x) => x.source_id === sourceId));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "This source isn't reachable.");
    }
  }, [sourceId]);

  useEffect(() => {
    void load();
  }, [load, tick]);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      setError(null);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That didn't work.");
    }
  };

  const segIndex = useMemo(() => new Map((source?.segments ?? []).map((s) => [s.id, s])), [source]);

  if (!source) {
    return (
      <div className="grid gap-4">
        <BackLink onBack={onBack} />
        {error ? <p className="text-[13px] text-ql-danger">{error}</p> : <p className="text-[13px] text-fg-muted">Loading…</p>}
      </div>
    );
  }

  const seek = (seg: QsSegment) => {
    setFocus(seg.id);
    if (video.current && seg.start_ms !== null) {
      video.current.currentTime = seg.start_ms / 1000;
      void video.current.play().catch(() => undefined);
    }
  };
  const openMission = missions.find((m) => m.kind === "idea" && OPEN_STATES.includes(m.state));
  const lastMission = missions.find((m) => m.kind === "idea");
  const ready = source.status === "EXTRACTED" || source.status === "PARTIAL";
  const extracting = source.status === "EXTRACTING" || source.status === "QUEUED" || source.status === "RESOLVING";
  const warnings = source.meta.warnings ?? [];
  const blueprint = source.blueprints[source.blueprints.length - 1] ?? null;
  const flagged = source.segments.filter((s) => s.flags.length);
  const performance = source.claims.filter((c) => c.kind === "PERFORMANCE_CLAIM");
  const injected = source.claims.filter((c) => c.kind === "INSTRUCTION_TO_AI");

  return (
    <div className="grid gap-5" data-testid="source-view">
      <BackLink onBack={onBack} />
      <Card>
        <div className="flex flex-wrap items-start gap-3">
          <div className="mr-auto min-w-0">
            <p className="font-mono text-2xs tracking-[0.16em] text-fg-faint uppercase">{source.kind.replace("_", " ")} source</p>
            <h2 className="mt-1 truncate text-xl font-light text-fg" data-testid="source-title">
              {source.title}
            </h2>
            <p className="mt-1 text-[12px] text-fg-muted">
              Added {ago(source.created_at)}
              {source.duration_ms ? ` · ${tc(source.duration_ms)}` : ""}
              {source.size_bytes ? ` · ${bytes(source.size_bytes)}` : ""}
              {source.language ? ` · ${source.language}` : ""}
              {source.meta.provider ? ` · read by ${source.meta.provider}` : ""}
            </p>
          </div>
          <SourceStatus status={source.status} />
          {lastMission &&
            (lastMission.verdict ? <MissionVerdict verdict={lastMission.verdict} /> : <MissionState state={lastMission.state} />)}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {openMission ? (
            <Button variant="primary" onClick={() => onOpenMission(openMission.id)} data-testid="source-open-mission">
              Open research room
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled={!ready}
              onClick={() =>
                void act(async () => {
                  const m = await api.qmCreate(source.id);
                  onOpenMission(m.id);
                })
              }
              title={ready ? "Start a research mission for this source" : "The source must be read first"}
              data-testid="source-start-research"
            >
              {lastMission ? "Research again" : "Start research"}
            </Button>
          )}
          {lastMission && !openMission && (
            <Button onClick={() => onOpenMission(lastMission.id)} data-testid="source-last-mission">
              Last mission
            </Button>
          )}
          {extracting && (
            <Button variant="danger" onClick={() => void act(() => api.qsCancel(source.id))} data-testid="source-cancel">
              Stop reading
            </Button>
          )}
          {(source.status === "CANCELED" || source.status === "FAILED") && source.media_available && (
            <Button onClick={() => void act(() => api.qsExtract(source.id))} data-testid="source-retry">
              Read again
            </Button>
          )}
          {source.media_available && (
            <Button onClick={() => void act(() => api.qsDeleteMedia(source.id))} title="Delete the original file; the transcript, frames' text and claims stay" data-testid="source-delete-media">
              Delete original file
            </Button>
          )}
          {!confirmDelete ? (
            <Button variant="danger" onClick={() => setConfirmDelete(true)} data-testid="source-delete">
              Delete source…
            </Button>
          ) : (
            <span className="flex items-center gap-2 text-[12px] text-fg-muted">
              Deletes the source, its evidence and files. Missions keep their sealed records.
              <Button
                variant="danger"
                onClick={() =>
                  void (async () => {
                    try {
                      await api.qsDelete(source.id);
                      onBack();
                    } catch (e) {
                      setError(e instanceof ApiError ? e.message : "Couldn't delete.");
                    }
                  })()
                }
                data-testid="source-delete-confirm"
              >
                Delete
              </Button>
              <Button onClick={() => setConfirmDelete(false)}>Keep</Button>
            </span>
          )}
        </div>
        {error && <p className="mt-3 text-[13px] text-ql-danger">{error}</p>}
        {source.error && <p className="mt-3 text-[13px] text-ql-danger">{source.error}</p>}
        {warnings.length > 0 && (
          <ul className="mt-3 grid gap-1 text-[12px] text-ql-warning" data-testid="source-warnings">
            {warnings.map((w) => (
              <li key={w}>◐ {w}</li>
            ))}
          </ul>
        )}
        {source.media_available && source.media_expires_at && (
          <p className="mt-2 text-[11px] text-fg-faint">
            The original file is kept on this Mac until {source.media_expires_at.slice(0, 10)}, then deleted automatically. It is
            never sent to a cloud model.
          </p>
        )}
        {source.media_deleted_at && <p className="mt-2 text-[11px] text-fg-faint">Original deleted {ago(source.media_deleted_at)}; the extracted evidence remains.</p>}
      </Card>

      {(injected.length > 0 || flagged.length > 0) && (
        <div className="rounded-xl border border-ql-danger/40 bg-ql-danger/5 px-4 py-3 text-[13px]" data-testid="source-injection">
          <p className="text-ql-danger">⚠ The source contains text addressed to an AI.</p>
          <p className="mt-1 text-fg-muted">
            It is treated as data only — never followed, never used as a rule. JARVIS cannot buy data, change settings or
            run code because a source says so.
          </p>
          <ul className="mt-2 grid gap-1 text-[12px] text-fg-muted">
            {flagged.slice(0, 4).map((s) => (
              <li key={s.id}>
                <span className="font-mono text-fg-faint">{tc(s.start_ms)}</span> “{s.text}”
              </li>
            ))}
          </ul>
        </div>
      )}
      {performance.length > 0 && (
        <div className="rounded-xl border border-ql-warning/40 bg-ql-warning/5 px-4 py-3 text-[13px]" data-testid="source-performance">
          <p className="text-ql-warning">◐ Performance claims in the source are not evidence.</p>
          <p className="mt-1 text-fg-muted">
            {performance.map((c) => `“${c.quote ?? c.content}”`).join(" · ")} — recorded as claims, marked NOT TESTED, and never
            used to rank or score the idea.
          </p>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <Card title="What was read" aside={<span className="text-2xs text-fg-faint">{source.segments.length} segments</span>}>
          {source.kind === "video" && source.media_available && (
            <video
              ref={video}
              controls
              preload="metadata"
              src={api.qsMediaUrl(source.id)}
              poster={source.frames[0] ? api.qsFrameUrl(source.id, source.frames[0].id) : undefined}
              className="mb-4 w-full rounded-lg bg-black"
              data-testid="source-video"
            />
          )}
          {!source.segments.length ? (
            <Empty title={extracting ? "Reading…" : "Nothing readable yet"}>
              {extracting ? `Stage: ${source.stage ?? "queued"}` : "No text, speech or on-screen text was found."}
            </Empty>
          ) : (
            <ol className="grid max-h-[520px] gap-1 overflow-y-auto pr-1" data-testid="source-segments">
              {source.segments.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => seek(s)}
                    className={cx(
                      "grid w-full grid-cols-[64px_88px_minmax(0,1fr)] gap-2 rounded-lg px-2 py-1.5 text-left text-[13px]",
                      focus === s.id ? "bg-ql-raised" : "hover:bg-white/[0.03]",
                    )}
                    data-testid="source-segment"
                  >
                    <span className="font-mono text-2xs text-fg-faint">
                      {s.start_ms !== null ? tc(s.start_ms) : s.page ? `p.${s.page}` : "—"}
                    </span>
                    <span className="text-2xs text-fg-faint">
                      {MODALITY[s.modality] ?? s.modality}
                      {s.quality !== "ok" && <span className="block text-ql-warning">{s.quality}</span>}
                    </span>
                    <span className={cx("selectable break-words", s.flags.length ? "text-ql-danger" : "text-fg")}>
                      {s.text}
                      {s.flags.length > 0 && <span className="ml-2 font-mono text-[10px] uppercase">⚠ {s.flags.join(", ")}</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
          {source.frames.length > 0 && (
            <div className="mt-4">
              <p className="label mb-2">Keyframes</p>
              <div className="grid grid-cols-3 gap-2 md:grid-cols-4" data-testid="source-frames">
                {source.frames.map((f) => (
                  <figure key={f.id} className="min-w-0">
                    <img
                      src={api.qsFrameUrl(source.id, f.id)}
                      alt={f.text ? `Frame at ${tc(f.t_ms)}: ${f.text}` : `Frame at ${tc(f.t_ms)}`}
                      className="aspect-video w-full rounded-md border border-ql-border object-cover"
                      loading="lazy"
                    />
                    <figcaption className="mt-1 truncate font-mono text-[10px] text-fg-faint" title={f.text ?? undefined}>
                      {tc(f.t_ms)} {f.text ? `· ${f.text}` : ""}
                    </figcaption>
                  </figure>
                ))}
              </div>
            </div>
          )}
        </Card>

        <Card title="Claims" aside={<span className="text-2xs text-fg-faint">every claim: NOT TESTED</span>}>
          {!source.claims.length ? (
            <Empty title="No claims yet">The research mission extracts claims after the source is read.</Empty>
          ) : (
            <ul className="grid gap-2" data-testid="source-claims">
              {source.claims.map((c) => (
                <ClaimRow key={c.id} claim={c} segments={segIndex} onSeek={seek} />
              ))}
            </ul>
          )}
        </Card>
      </div>

      {blueprint && <BlueprintView blueprint={blueprint} segments={segIndex} mode={mode} onSeek={seek} />}

      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="Your notes">
          <p className="mb-2 text-[12px] text-fg-muted">
            Notes are your definitions (e.g. “stop goes 2 ticks beyond the wick”). They count as user-specified, never as the
            source.
          </p>
          <ul className="mb-3 grid gap-1 text-[13px]" data-testid="source-notes">
            {source.notes.map((n) => (
              <li key={n.id} className="text-fg">
                ✎ {n.text} <span className="text-2xs text-fg-faint">· {ago(n.created_at)}</span>
              </li>
            ))}
          </ul>
          <div className="flex gap-2">
            <input className={inputClass} value={note} onChange={(e) => setNote(e.target.value)} maxLength={4000} placeholder="Add a definition or correction" data-testid="source-note-input" />
            <Button
              disabled={!note.trim()}
              onClick={() =>
                void act(async () => {
                  await api.qsNote(source.id, note.trim());
                  setNote("");
                })
              }
              data-testid="source-note-add"
            >
              Add
            </Button>
          </div>
        </Card>
        {mode !== "Simple" && (
          <Card title="Coverage & audit">
            <dl>
              {Object.entries(source.meta.coverage ?? {}).map(([k, v]) => (
                <Kv key={k} label={k} mono>
                  {JSON.stringify(v)}
                </Kv>
              ))}
              {source.capability && <Kv label="Link capability">{source.capability}</Kv>}
            </dl>
            <ul className="mt-3 grid max-h-56 gap-1 overflow-y-auto font-mono text-2xs text-fg-muted" data-testid="source-audit">
              {source.audit.map((a) => (
                <li key={a.id}>
                  {a.at.slice(0, 19).replace("T", " ")} · {a.action}
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}

function BackLink({ onBack }: { onBack: () => void }) {
  return (
    <button type="button" onClick={onBack} className="w-fit text-[13px] text-fg-muted hover:text-fg" data-testid="source-back">
      ← Idea Inbox
    </button>
  );
}

function ClaimRow({
  claim,
  segments,
  onSeek,
}: {
  claim: QsClaim;
  segments: Map<string, QsSegment>;
  onSeek: (s: QsSegment) => void;
}) {
  const kind = CLAIM_KIND[claim.kind];
  return (
    <li className="rounded-lg border border-ql-border px-3 py-2" data-testid="source-claim" data-kind={claim.kind}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={kind.tone}>{kind.label}</Badge>
        <span className="text-2xs text-fg-faint">
          {claim.claim_status.replace("_", " ")} · {claim.extraction_confidence} confidence · {claim.origin === "rule" ? "rule-based" : "model"}
        </span>
      </div>
      <p className="mt-1 text-[13px] text-fg">{claim.content}</p>
      {claim.quote && (
        <p className="mt-1 text-[12px] text-fg-muted">
          {claim.quote_verified ? "❝ " : "✕ unverified: "}
          <span className="selectable italic">{claim.quote}</span>
        </p>
      )}
      <div className="mt-1 flex flex-wrap gap-1">
        {claim.segment_ids.map((id) => {
          const seg = segments.get(id);
          return seg ? (
            <button key={id} type="button" onClick={() => onSeek(seg)} className="font-mono text-[10px] text-ql hover:underline">
              {seg.start_ms !== null ? tc(seg.start_ms) : seg.page ? `p.${seg.page}` : id}
            </button>
          ) : null;
        })}
        {claim.unresolved.length > 0 && <span className="text-[11px] text-ql-warning">open: {claim.unresolved.join(", ")}</span>}
      </div>
    </li>
  );
}
