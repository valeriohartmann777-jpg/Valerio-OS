import type { QmMissionRow, QsSourceRow, QsSpeechModel } from "@jarvis/protocol";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "../../../lib/api";
import { Button, cx } from "../../ui/primitives";
import { Badge, Card, ago, inputClass } from "../ui";
import { Empty, bytes } from "../research/common";
import { MissionState, MissionVerdict, OPEN_STATES, SourceStatus, tc } from "./common";

/**
 * Idea Inbox: drop a saved video, screenshot, PDF or text, or paste a link. JARVIS reads it on
 * this Mac, extracts claims with quotes, compiles a blueprint and starts a research mission.
 */

const ACCEPT = ".mp4,.mov,.m4v,.webm,.mkv,.mp3,.m4a,.wav,.aac,.ogg,.flac,.png,.jpg,.jpeg,.webp,.heic,.pdf,.txt,.md";
const LANGUAGES = [
  { id: "en", label: "English" },
  { id: "de", label: "German" },
  { id: "es", label: "Spanish" },
  { id: "fr", label: "French" },
];
const KIND_ICON: Record<string, string> = {
  video: "▶",
  audio: "♪",
  image: "▣",
  pdf: "▤",
  text: "¶",
  text_file: "¶",
  link: "↗",
};

function isLink(text: string): boolean {
  const t = text.trim();
  return /^https?:\/\/\S+$/i.test(t);
}

export function IdeaInbox({
  tick,
  onOpenSource,
  onOpenMission,
}: {
  tick: string | null;
  onOpenSource: (id: string) => void;
  onOpenMission: (id: string) => void;
}) {
  const [sources, setSources] = useState<QsSourceRow[]>([]);
  const [missions, setMissions] = useState<QmMissionRow[]>([]);
  const [speech, setSpeech] = useState<QsSpeechModel | null>(null);
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [language, setLanguage] = useState("en");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [s, m, sp] = await Promise.all([api.qsSources(), api.qmMissions(), api.qsSpeechModel()]);
      setSources(s);
      setMissions(m);
      setSpeech(sp);
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : "Sources aren't reachable." });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, tick]);

  const done = (row: QsSourceRow, what: string) => {
    setMessage({
      tone: "ok",
      text: row.duplicate ? `Already in the inbox: “${row.title}”. Nothing was added twice.` : `${what}: “${row.title}”.`,
    });
    setText("");
    setNote("");
    void load();
  };
  const fail = (e: unknown) =>
    setMessage({ tone: "error", text: e instanceof ApiError ? e.message : "That didn't work." });

  const upload = async (file: File, linkSource?: string) => {
    setBusy(true);
    setMessage(null);
    try {
      done(await api.qsUpload(file, { note: note.trim() || undefined, language, linkSource }), "Reading");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const submitText = async () => {
    const value = text.trim();
    if (!value) return;
    setBusy(true);
    setMessage(null);
    try {
      if (isLink(value)) done(await api.qsIntakeLink(value, note.trim() || undefined), "Checking the link");
      else done(await api.qsIntakeText(value, note.trim() || undefined), "Reading");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const missionFor = (sourceId: string) => missions.find((m) => m.source_id === sourceId && m.kind === "idea");
  const needsMultilingual = language !== "en" && speech && !speech.installed;

  return (
    <div className="grid gap-5" data-testid="idea-inbox">
      <Card title="New idea" aside={<Badge tone="muted">Research only</Badge>}>
        <div
          className={cx(
            "flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-8 text-center transition-colors",
            dragging ? "border-ql bg-ql/5" : "border-ql-border",
          )}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const file = e.dataTransfer.files[0];
            if (file) void upload(file);
          }}
          data-testid="idea-drop"
        >
          <p className="text-[15px] text-fg">Drop a saved video, screen recording, screenshot, PDF or text file</p>
          <p className="mt-1 text-[12px] text-fg-muted">
            Video up to 15 minutes · read on this Mac (speech + on-screen text + keyframes)
          </p>
          <Button className="mt-3" disabled={busy} onClick={() => fileInput.current?.click()} data-testid="idea-choose-file">
            Choose file…
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void upload(file);
            }}
            data-testid="idea-file-input"
          />
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_260px]">
          <label className="block">
            <span className="mb-1 block text-xs text-ql-muted">…or paste the strategy text, or a TikTok / YouTube link</span>
            <textarea
              className={cx(inputClass, "h-28 resize-y py-2 leading-relaxed")}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="e.g. Wait for the first 15-minute candle after the New York open. If price sweeps the high and closes back inside, short…"
              data-testid="idea-text"
            />
          </label>
          <div className="grid content-start gap-3">
            <label className="block">
              <span className="mb-1 block text-xs text-ql-muted">Your note (optional)</span>
              <input
                className={inputClass}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. he means NQ futures"
                maxLength={4000}
                data-testid="idea-note"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-ql-muted">Spoken language</span>
              <select className={inputClass} value={language} onChange={(e) => setLanguage(e.target.value)} data-testid="idea-language">
                {LANGUAGES.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.label}
                  </option>
                ))}
              </select>
            </label>
            <Button variant="primary" disabled={busy || !text.trim()} onClick={() => void submitText()} data-testid="idea-submit">
              {isLink(text) ? "Check link" : "Add text"}
            </Button>
          </div>
        </div>

        {needsMultilingual && (
          <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-ql-warning/40 bg-ql-warning/5 px-3 py-2 text-[12px] text-fg-muted">
            <span>
              Speech in this language needs the multilingual model ({speech.multilingual}, free, downloaded once).
              {speech.job.status === "running" && " Downloading…"}
              {speech.job.error && ` Last attempt failed: ${speech.job.error}`}
            </span>
            <Button
              disabled={speech.job.status === "running"}
              onClick={async () => {
                try {
                  setSpeech(await api.qsInstallSpeechModel());
                } catch (e) {
                  fail(e);
                }
              }}
              data-testid="idea-install-speech"
            >
              Install speech model
            </Button>
          </div>
        )}

        {message && (
          <p className={cx("mt-3 text-[13px]", message.tone === "error" ? "text-ql-danger" : "text-fg-muted")} role="status" data-testid="idea-message">
            {message.text}
          </p>
        )}
        <p className="mt-3 text-[11px] leading-relaxed text-fg-faint">
          Files stay on this Mac. Only the extracted words (transcript, on-screen text, your notes) go to Claude to find the
          rules — never the video or images. Originals are deleted automatically after the retention period. Links: JARVIS
          reads public metadata only; it never downloads TikTok or YouTube videos — save the video and drop it here.
          Anything in a source that tries to instruct the AI is treated as data and flagged.
        </p>
      </Card>

      <Card title={`Inbox · ${sources.length}`}>
        {!sources.length ? (
          <Empty title="Nothing here yet" testId="idea-empty">
            Drop a video or paste a strategy above. JARVIS turns it into testable rules, asks you only what is truly
            undefined, and backtests it on real data after your approval.
          </Empty>
        ) : (
          <ul className="grid gap-2" data-testid="idea-sources">
            {sources.map((s) => {
              const mission = missionFor(s.id);
              return (
                <li key={s.id} className="rounded-xl border border-ql-border bg-ql-raised/40 px-4 py-3" data-testid="idea-source">
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="button"
                      className="mr-auto flex min-w-0 items-center gap-3 text-left"
                      onClick={() => onOpenSource(s.id)}
                      data-testid={`idea-open-${s.id}`}
                    >
                      <span className="w-5 text-center font-mono text-fg-faint" aria-hidden>
                        {KIND_ICON[s.kind] ?? "·"}
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-[14px] text-fg">{s.title}</span>
                        <span className="block text-2xs text-fg-faint">
                          {s.kind.replace("_", " ")}
                          {s.duration_ms ? ` · ${tc(s.duration_ms)}` : ""}
                          {s.size_bytes ? ` · ${bytes(s.size_bytes)}` : ""} · {ago(s.created_at)}
                          {s.stage ? ` · ${s.stage}…` : ""}
                        </span>
                      </span>
                    </button>
                    <SourceStatus status={s.status} />
                    {mission ? (
                      <button type="button" onClick={() => onOpenMission(mission.id)} className="flex items-center gap-2" data-testid="idea-mission-chip">
                        {mission.verdict ? <MissionVerdict verdict={mission.verdict} /> : <MissionState state={mission.state} />}
                      </button>
                    ) : null}
                  </div>
                  {s.status === "NEEDS_UPLOAD" && (
                    <AttachUpload source={s} busy={busy} onFile={(f) => void upload(f, s.id)} />
                  )}
                  {s.error && <p className="mt-2 text-[12px] text-ql-danger">{s.error}</p>}
                  {mission && OPEN_STATES.includes(mission.state) && mission.waiting && (
                    <p className="mt-2 text-[12px] text-ql-warning">
                      Waiting for you —{" "}
                      <button type="button" className="underline" onClick={() => onOpenMission(mission.id)}>
                        open the research room
                      </button>
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}

function AttachUpload({ source, busy, onFile }: { source: QsSourceRow; busy: boolean; onFile: (f: File) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const meta = source.meta.metadata ?? {};
  return (
    <div className="mt-2 flex flex-wrap items-center gap-3 rounded-lg border border-ql-border px-3 py-2 text-[12px] text-fg-muted">
      <span className="mr-auto">
        {typeof meta.author_name === "string" ? `By ${meta.author_name}. ` : ""}
        JARVIS can see this link's public title only. Save the video (if you are allowed to) and attach it here — it stays
        linked to this source.
      </span>
      <Button disabled={busy} onClick={() => ref.current?.click()} data-testid="idea-attach">
        Attach saved video…
      </Button>
      <input
        ref={ref}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) onFile(file);
        }}
      />
    </div>
  );
}
