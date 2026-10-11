import type { JarvisEvent, JarvisMessagePayload, Mission } from "@jarvis/protocol";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { api } from "../../lib/api";
import { rate, useFeedback } from "../../lib/feedback";
import { clock, missionNumber, stateLabel } from "../../lib/format";
import { dispatch, useJarvis } from "../../store/store";
import { Icon } from "../ui/icons";

const PAGE = 50;
const IDLE = new Set(["DORMANT", "COMPLETE", "FAILED"]);

/**
 * The persistent chat: what you typed or said, and what JARVIS answered.
 * Sticks to the newest message unless you scrolled up to read.
 */
export function Conversation() {
  const entries = useJarvis((s) => s.conversation);
  const complete = useJarvis((s) => s.conversationComplete);
  const jarvis = useJarvis((s) => s.jarvis);
  const missions = useJarvis((s) => s.missions);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [loading, setLoading] = useState(false);

  const last = entries.at(-1);
  const waiting = last?.type === "command.received" && !IDLE.has(jarvis.state);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [entries.length, waiting]);

  // The area also shrinks and grows (mission cards, approvals, window size):
  // stay on the newest message through that too.
  useEffect(() => {
    const el = scroller.current;
    const content = el?.firstElementChild;
    if (!el || !content) return;
    const observer = new ResizeObserver(() => {
      if (stick.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(el);
    observer.observe(content);
    return () => observer.disconnect();
  }, []);

  const loadEarlier = async () => {
    const first = entries[0];
    if (!first || loading) return;
    setLoading(true);
    const el = scroller.current;
    const fromBottom = el ? el.scrollHeight - el.scrollTop : 0;
    try {
      const older = await api.conversation(PAGE, first.timestamp);
      dispatch({ type: "conversation", events: older, complete: older.length < PAGE, older: true });
      requestAnimationFrame(() => {
        if (el) el.scrollTop = el.scrollHeight - fromBottom; // keep the reading position
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      ref={scroller}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
      }}
      className="min-h-0 w-full flex-1 overflow-y-auto"
      data-testid="conversation"
    >
      <div className="mx-auto flex max-w-[680px] flex-col gap-4 px-2 pt-2 pb-6">
        {entries.length === 0 ? (
          <p className="pt-6 text-center text-[13px] text-fg-faint">
            Ask anything — type below, click the microphone or say “Hey JARVIS”.
          </p>
        ) : !complete ? (
          <button
            type="button"
            onClick={() => void loadEarlier()}
            className="mx-auto text-xs text-fg-faint hover:text-fg-muted"
          >
            {loading ? "Loading…" : "Earlier messages"}
          </button>
        ) : null}
        {entries.map((event, index) => (
          <Entry
            key={event.id}
            event={event}
            previous={entries[index - 1]}
            mission={missionFor(event, missions)}
          />
        ))}
        {waiting && (
          <div className="flex items-center gap-2 text-[13px] text-fg-faint" data-testid="jarvis-working">
            <Dots />
            {stateLabel(jarvis.state)}
            {jarvis.detail && jarvis.state !== "UNDERSTANDING" && (
              <span className="truncate">· {jarvis.detail}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function missionFor(event: JarvisEvent, missions: Mission[]): Mission | undefined {
  if (event.type !== "jarvis.message") return undefined;
  return missions.find((m) => m.id === event.mission_id || (event.trace_id && m.trace_id === event.trace_id));
}

function Entry({
  event,
  previous,
  mission,
}: {
  event: JarvisEvent;
  previous: JarvisEvent | undefined;
  mission: Mission | undefined;
}) {
  const at = new Date(event.timestamp);
  const day = dayLabel(at);
  const newDay = !previous || dayLabel(new Date(previous.timestamp)) !== day;
  return (
    <>
      {newDay && (
        <div className="my-1 flex items-center gap-3 text-2xs tracking-[0.18em] text-fg-faint uppercase">
          <span className="h-px flex-1 bg-hairline" />
          {day}
          <span className="h-px flex-1 bg-hairline" />
        </div>
      )}
      {event.type === "command.received" ? <Said event={event} at={at} /> : <Answer event={event} at={at} mission={mission} />}
    </>
  );
}

function Said({ event, at }: { event: JarvisEvent; at: Date }) {
  const payload = event.payload as { text?: string; source?: string };
  const spoken = payload.source === "voice";
  return (
    <div className="flex flex-col items-end gap-1" data-testid="you-said">
      <p className="selectable max-w-[80%] rounded-2xl rounded-br-md bg-surface-2 px-3.5 py-2 text-[14px] leading-relaxed whitespace-pre-wrap text-fg">
        {payload.text}
      </p>
      <span className="flex items-center gap-1.5 font-mono text-2xs text-fg-faint">
        {spoken && <MicGlyph />}
        {spoken ? "spoken · " : ""}
        {clock(at)}
      </span>
    </div>
  );
}

function Answer({ event, at, mission }: { event: JarvisEvent; at: Date; mission: Mission | undefined }) {
  const payload = event.payload as unknown as JarvisMessagePayload & { kind?: string };
  return (
    <div className="flex flex-col items-start gap-1" data-testid="jarvis-reply">
      <span className="text-2xs font-medium tracking-[0.2em] text-accent/80 uppercase">
        Jarvis{payload.kind === "briefing" && <span className="text-fg-faint"> · Morning briefing</span>}
      </span>
      <p className="selectable max-w-[92%] text-[14px] leading-relaxed whitespace-pre-wrap text-fg">
        {payload.text}
      </p>
      {payload.error && (
        <dl className="grid max-w-[92%] grid-cols-[76px_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[13px]">
          <dt className="text-fg-faint">Reason</dt>
          <dd className="text-fg-muted">{payload.error.message}</dd>
          {payload.error.suggestion && (
            <>
              <dt className="text-fg-faint">Suggested</dt>
              <dd className="text-fg-muted">{payload.error.suggestion}</dd>
            </>
          )}
        </dl>
      )}
      <span className="flex items-center gap-2 font-mono text-2xs text-fg-faint">
        {clock(at)}
        <Rate eventId={event.id} />
        {mission && (
          <button
            type="button"
            className="hover:text-fg-muted"
            onClick={() => dispatch({ type: "navigate", view: { name: "mission", id: mission.id } })}
          >
            · Mission {missionNumber(mission.number)} →
          </button>
        )}
      </span>
    </div>
  );
}

/** 👍 / 👎 on a reply — evidence for the Training Hub, never an automatic change. */
function Rate({ eventId }: { eventId: string }) {
  const feedback = useFeedback();
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);
  const rated = feedback?.ratings[eventId];
  const send = async (value: 1 | -1, text?: string) => {
    setFailed(false);
    try {
      await rate(eventId, value, text);
      setAsking(false);
      setNote("");
    } catch {
      setFailed(true);
    }
  };
  return (
    <span className="flex items-center gap-1 font-sans" data-testid="reply-rating" data-rated={rated ?? 0}>
      <button
        type="button"
        onClick={() => void send(1)}
        aria-pressed={rated === 1}
        title="Good answer"
        aria-label="Good answer"
        className={rated === 1 ? "text-success" : "hover:text-fg-muted"}
        data-testid="rate-up"
      >
        <Icon name="thumbUp" size={13} />
      </button>
      <button
        type="button"
        onClick={() => setAsking((a) => !a)}
        aria-pressed={rated === -1}
        aria-expanded={asking}
        title="Wrong or unhelpful — tell JARVIS why"
        aria-label="Wrong or unhelpful"
        className={rated === -1 ? "text-danger" : "hover:text-fg-muted"}
        data-testid="rate-down"
      >
        <Icon name="thumbDown" size={13} />
      </button>
      {failed && (
        <span className="text-danger" role="alert">
          not saved — backend unreachable
        </span>
      )}
      {asking && (
        <form
          className="flex items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            void send(-1, note);
          }}
        >
          <input
            autoFocus
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What was wrong? (optional)"
            className="h-7 w-56 rounded-md border border-control/70 bg-surface-2 px-2 text-[12px] text-fg placeholder:text-fg-faint"
            aria-label="What was wrong"
            data-testid="rate-note"
          />
          <button type="submit" className="rounded-md bg-surface-2 px-2 py-1 text-[12px] font-semibold text-fg" data-testid="rate-send">
            Save
          </button>
        </form>
      )}
    </span>
  );
}

function dayLabel(date: Date): string {
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

function Dots() {
  return (
    <span className="inline-flex gap-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1 rounded-full bg-accent/70"
          style={{ animation: `beacon 1.2s ease-in-out ${i * 0.18}s infinite` }}
        />
      ))}
    </span>
  );
}

function MicGlyph() {
  return (
    <svg width="9" height="9" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
      <rect x="5.5" y="1.5" width="5" height="8.5" rx="2.5" />
      <path d="M3 7.5a5 5 0 0 0 10 0M8 12.5v2" strokeLinecap="round" />
    </svg>
  );
}
