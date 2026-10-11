import type { EvalOverview, FeedbackItem } from "@jarvis/protocol";
import { useEffect, useState } from "react";

import { api, ApiError } from "../../lib/api";
import { loadFeedback, useFeedback } from "../../lib/feedback";
import { useJarvis } from "../../store/store";
import { Icon } from "../ui/icons";
import { Button } from "../ui/primitives";
import { EmptyState, LoadingState, Panel, StatusPill } from "../ui/shell";

const ROUTE_LABEL: Record<string, string> = {
  instant: "Instant (local rules)",
  reasoning: "Reasoning (AI route)",
  think: "Think (AI route, deep)",
  unknown: "Unknown route",
};

/**
 * The owner's 👍 / 👎 on JARVIS's replies. Evidence, not training: nothing here changes a model
 * or a rule by itself. A correction becomes part of JARVIS only when the owner keeps it as a
 * memory, which JARVIS then has in mind in every conversation.
 */
export function FeedbackSection() {
  const feedback = useFeedback();
  const [evals, setEvals] = useState<EvalOverview | null>(null);
  useEffect(() => {
    void loadFeedback();
    api.evals().then(setEvals, () => undefined);
  }, []);
  if (!feedback) return <LoadingState>Loading your ratings…</LoadingState>;
  const total = feedback.up + feedback.down;
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]" data-testid="training-feedback">
      <Panel title="Rated replies" aside={<StatusPill tone="muted">{`${total} rated`}</StatusPill>}>
        {feedback.items.length === 0 ? (
          <EmptyState icon="thumbUp" title="Nothing rated yet">
            Use 👍 / 👎 under any reply on Home. A 👎 can say what was wrong; it shows up here with the question you asked.
          </EmptyState>
        ) : (
          <ul className="flex flex-col gap-2.5">
            {feedback.items.map((item) => (
              <Rated key={item.id} item={item} pinned={evals?.pinned_events.includes(item.event_id) ?? null} onPinned={setEvals} />
            ))}
          </ul>
        )}
      </Panel>
      <div className="flex flex-col gap-5">
        <Panel title="By route">
          {feedback.by_route.length === 0 ? (
            <p className="text-[13px] text-fg-muted">No ratings yet.</p>
          ) : (
            <table className="w-full text-[13px]" data-testid="feedback-by-route">
              <thead>
                <tr className="text-left text-[12px] text-fg-faint">
                  <th className="pb-1.5 font-semibold">Route</th>
                  <th className="pb-1.5 text-right font-semibold">👍</th>
                  <th className="pb-1.5 text-right font-semibold">👎</th>
                </tr>
              </thead>
              <tbody>
                {feedback.by_route.map((r) => (
                  <tr key={r.route} className="border-t border-hairline-strong/60">
                    <td className="py-1.5 text-fg">{ROUTE_LABEL[r.route] ?? r.route}</td>
                    <td className="py-1.5 text-right font-mono text-fg-muted">{r.up}</td>
                    <td className="py-1.5 text-right font-mono text-fg-muted">{r.down}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
        <Panel title="What a rating does">
          <ul className="space-y-2 text-[13px] leading-relaxed text-fg-muted">
            <li>It is stored with the question, the answer and the route that produced it.</li>
            <li>It changes nothing automatically — no model, prompt or rule is adjusted behind your back.</li>
            <li>
              <b className="text-fg">Pin as test case</b> keeps how JARVIS understood a 👍 request; Evaluations then checks that it
              still does.
            </li>
            <li>
              <b className="text-fg">Keep as correction</b> turns a 👎 note into a memory. JARVIS then has it in mind in every
              conversation; remove it any time under Knowledge.
            </li>
          </ul>
        </Panel>
      </div>
    </div>
  );
}

/** A memory holds at most 300 characters, whitespace collapsed (backend `memory/store.py`). */
function asCorrection(item: FeedbackItem): string {
  const asked = item.command ? `When I ask “${clip(item.command, 120)}”: ` : "";
  return clip(`${asked}${item.note ?? ""}`, 300);
}

function clip(text: string, max: number): string {
  const clean = text.split(/\s+/).filter(Boolean).join(" ");
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}

function Rated({ item, pinned, onPinned }: { item: FeedbackItem; pinned: boolean | null; onPinned: (o: EvalOverview) => void }) {
  const correction = asCorrection(item);
  const kept = useJarvis((s) => s.memories.some((m) => m.text.toLowerCase() === correction.toLowerCase()));
  const [error, setError] = useState<string | null>(null);
  const keep = async () => {
    setError(null);
    try {
      await api.addMemory(correction, "correction");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    }
  };
  const pin = async () => {
    setError(null);
    try {
      onPinned(await api.pinCase(item.event_id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    }
  };
  const down = item.rating < 0;
  return (
    <li className="rounded-xl border border-hairline-strong bg-surface-2/40 p-3" data-testid="feedback-item" data-rating={item.rating}>
      <div className="flex items-start gap-2.5">
        <Icon name={down ? "thumbDown" : "thumbUp"} size={16} className={down ? "mt-0.5 text-danger" : "mt-0.5 text-success"} label={down ? "Rated wrong" : "Rated good"} />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] text-fg-faint">
            {item.command ? <>You asked: <span className="text-fg">“{item.command}”</span></> : "Question not recorded"}
            {item.route && <span className="ml-2 font-mono text-[12px]">· {item.route}</span>}
          </p>
          <p className="mt-1 line-clamp-3 text-[13px] text-fg-muted">{item.reply}</p>
          {item.note && (
            <p className="mt-1.5 text-[13px] text-fg" data-testid="feedback-note">
              <b>What was wrong:</b> {item.note}
            </p>
          )}
          {!down && item.command && (
            <div className="mt-2 flex items-center gap-2">
              <Button
                onClick={() => void pin()}
                disabled={pinned !== false}
                reason={pinned ? "Already a test case — see Evaluations." : pinned === null ? "Loading the test cases…" : null}
                data-testid="feedback-pin"
              >
                {pinned ? "Pinned as test case" : "Pin as test case"}
              </Button>
              {error && <span className="text-[12px] text-danger">{error}</span>}
            </div>
          )}
          {down && item.note && (
            <div className="mt-2 flex items-center gap-2">
              <Button onClick={() => void keep()} disabled={kept} reason={kept ? "Already kept as a memory." : null} data-testid="feedback-keep">
                {kept ? "Kept as correction" : "Keep as correction"}
              </Button>
              {error && <span className="text-[12px] text-danger">{error}</span>}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
