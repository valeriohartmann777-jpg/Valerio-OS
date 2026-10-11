import type { EvalExpect, EvalOverview, QrRunRow, UlArtifact } from "@jarvis/protocol";
import { useEffect, useState } from "react";

import { api, ApiError } from "../../lib/api";
import { trainingVerdict } from "../../lib/format";
import { go } from "../../lib/history";
import { useJarvis } from "../../store/store";
import { verdictLabel } from "../quantlab/research/common";
import { Icon } from "../ui/icons";
import { Button } from "../ui/primitives";
import { EmptyState, Panel, StatusPill, type PillTone } from "../ui/shell";

/**
 * Evaluations — the checks that decide whether anything counts, taken from where they really
 * happened. Nothing here is a score JARVIS gives itself: each row is a stored test result
 * (walk-forward months, out-of-sample backtests, SENTINEL reviews) with a link to its source.
 */
export function Evaluations() {
  const training = useJarvis((s) => s.training);
  const learning = useJarvis((s) => s.learning);
  const [runs, setRuns] = useState<QrRunRow[] | null>(null);
  const [reviews, setReviews] = useState<UlArtifact[] | null>(null);
  useEffect(() => {
    api.qrRuns().then(setRuns, () => setRuns([]));
    api.ulKnowledge().then((k) => setReviews(k.reviews), () => setReviews([]));
  }, []);

  const history = (training?.history ?? []).filter((r) => r.status === "done").slice(0, 6);
  const judged = (runs ?? []).filter((r) => r.verdict).slice(0, 8);
  const counts = learning?.counts ?? {};
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2" data-testid="training-evaluations">
      <Behaviour />
      <Panel title="Trained model · walk-forward" aside={<Source onClick={() => go({ name: "training", section: "model" })}>Trained model</Source>}>
        {history.length === 0 ? (
          <EmptyState title="No finished training run yet">Each run is judged on months the model never saw.</EmptyState>
        ) : (
          <ul className="flex flex-col gap-1.5 text-[13px]" data-testid="eval-model">
            {history.map((run) => {
              const verdict = run.verdict ? trainingVerdict(run.verdict) : null;
              return (
                <li key={run.number} className="flex items-center justify-between gap-3 border-b border-hairline-strong/50 pb-1.5">
                  <span className="text-fg">
                    Run {run.number} <span className="text-fg-faint">· data until {run.data_until ?? "—"}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    {run.holdout_improvement != null && (
                      <span className="font-mono text-[12px] text-fg-muted" title="Improvement over the base rate on unseen months">
                        {(run.holdout_improvement * 100).toFixed(1)} pp
                      </span>
                    )}
                    <StatusPill tone={verdict ? toneOf(verdict.tone) : "muted"}>{verdict ? verdict.label : "No verdict"}</StatusPill>
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>

      <Panel title="Research loop · hypotheses" aside={<Source onClick={() => go({ name: "training", section: "research" })}>Research loop</Source>}>
        <dl className="grid grid-cols-3 gap-3 text-center" data-testid="eval-research">
          <Count label="Backtests" value={counts.tests} />
          <Count label="Rejected" value={counts.rejected} />
          <Count label="Passed in-sample" value={counts.in_sample_passed} />
          <Count label="Validated out-of-sample" value={counts.validated} />
          <Count label="Confirmed later" value={counts.confirmed} />
          <Count label="Invalid" value={counts.invalid} />
        </dl>
        <p className="mt-3 text-[12px] text-fg-faint">A hypothesis counts only after it holds on data it was never tuned on.</p>
      </Panel>

      <Panel title="QuantLab · run verdicts" aside={<Source onClick={() => go({ name: "quantlab", section: "backtest-lab" })}>QuantLab</Source>}>
        {runs === null ? (
          <p className="text-[13px] text-fg-muted">Loading…</p>
        ) : judged.length === 0 ? (
          <EmptyState title="No judged run yet">A validation run (in-sample, out-of-sample, stress) ends with a verdict.</EmptyState>
        ) : (
          <ul className="flex flex-col gap-1.5 text-[13px]" data-testid="eval-quantlab">
            {judged.map((run) => (
              <li key={run.id} className="flex items-center justify-between gap-3 border-b border-hairline-strong/50 pb-1.5">
                <span className="min-w-0 truncate text-fg">
                  {run.name} v{run.version_number}{" "}
                  <span className="text-fg-faint">
                    · {run.symbol} {run.timeframe ?? ""}
                    {run.fixture && " · synthetic data"}
                  </span>
                </span>
                <StatusPill tone={verdictTone(run.verdict!)}>{verdictLabel(run.verdict!)}</StatusPill>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="ULTRON · SENTINEL reviews" aside={<Source onClick={() => go({ name: "ultron", room: "academy" })}>Academy</Source>}>
        {reviews === null ? (
          <p className="text-[13px] text-fg-muted">Loading…</p>
        ) : reviews.length === 0 ? (
          <EmptyState title="No review yet">SENTINEL reviews every software mission independently before it counts as done.</EmptyState>
        ) : (
          <ul className="flex flex-col gap-1.5 text-[13px]" data-testid="eval-reviews">
            {reviews.slice(0, 8).map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-3 border-b border-hairline-strong/50 pb-1.5">
                <span className="min-w-0 truncate text-fg">{r.name}</span>
                <span className="font-mono text-[12px] text-fg-faint">{r.created_at.slice(0, 10)}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

/** The one evaluation JARVIS can run on demand: curated + pinned sentences vs. the local router. */
function Behaviour() {
  const [data, setData] = useState<EvalOverview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.evals().then(setData, () => setError("The backend isn't reachable."));
  }, []);
  const act = async (call: () => Promise<EvalOverview>) => {
    setBusy(true);
    setError(null);
    try {
      setData(await call());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };
  const last = data?.last_run ?? null;
  const failures = last?.results.filter((r) => !r.passed) ?? [];
  const tone: PillTone = !last ? "muted" : failures.length ? "warning" : "success";
  return (
    <Panel
      className="lg:col-span-2"
      title="JARVIS behaviour · does it still understand you?"
      aside={
        <StatusPill tone={tone} testId="eval-behaviour-state">
          {last ? `${last.passed} / ${last.total} understood` : "Not run yet"}
        </StatusPill>
      }
      testId="eval-behaviour"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <p className="max-w-[720px] text-[13px] leading-relaxed text-fg-muted">
          {data ? `${data.cases.length} sentences` : "Sentences"} with what JARVIS must understand from them — kind of request,
          tool, apps. The check runs on this computer against the local command router: free, offline, nothing is opened.
          Answers that go to Claude are judged through your feedback instead. Pin a 👍 reply under Feedback to add it.
        </p>
        <Button variant="primary" onClick={() => void act(api.runEvals)} disabled={busy || !data} reason={!data ? "Waiting for the backend." : busy ? "Running…" : null} data-testid="eval-run">
          <Icon name="play" size={15} /> Run check
        </Button>
      </div>
      {error && <p className="mt-3 text-[13px] text-danger" role="alert">{error}</p>}
      {last && (
        <p className="mt-3 text-[12px] text-fg-faint" data-testid="eval-last" data-passed={last.passed} data-total={last.total}>
          Last run {new Date(last.at).toLocaleString()} · {last.passed} of {last.total} as expected
        </p>
      )}
      {failures.length > 0 && (
        <ul className="mt-3 flex flex-col gap-2" aria-label="Cases JARVIS misunderstood">
          {failures.map((r) => (
            <li key={r.id} className="rounded-xl border border-warning/40 bg-warning/10 p-3 text-[13px]" data-testid="eval-failure">
              <p className="text-fg">“{r.text}”</p>
              <p className="mt-1 text-fg-muted">
                Expected {describe(r.expect)} — understood {describe(r.got)}. Differs in: {r.wrong.join(", ")}.
              </p>
            </li>
          ))}
        </ul>
      )}
      {data && data.pinned.length > 0 && (
        <div className="mt-4">
          <h4 className="text-[12px] font-bold tracking-[0.06em] text-fg-faint uppercase">Pinned from your 👍</h4>
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {data.pinned.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-3 text-[13px]" data-testid="eval-pinned">
                <span className="min-w-0 truncate text-fg">
                  “{c.text}” <span className="text-fg-faint">→ {describe(c.expect)}</span>
                </span>
                <button
                  type="button"
                  onClick={() => void act(() => api.unpinCase(c.id))}
                  className="text-[12px] font-semibold text-fg-muted hover:text-danger"
                  aria-label={`Remove the case “${c.text}”`}
                  data-testid="eval-unpin"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

function describe(e: EvalExpect): string {
  const parts: string[] = [e.kind ?? "any"];
  if (e.tool) parts.push(e.tool);
  if (e.topic) parts.push(e.topic);
  if (e.targets?.length) parts.push(e.targets.join(" + "));
  return parts.join(" · ");
}

function Count({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="rounded-xl bg-surface-2/50 px-2 py-2.5">
      <dd className="font-mono text-[20px] font-bold text-fg">{value ?? 0}</dd>
      <dt className="text-[12px] text-fg-muted">{label}</dt>
    </div>
  );
}

function Source({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="inline-flex items-center gap-1 text-[12px] font-semibold text-[var(--area)] hover:underline">
      {children} <Icon name="arrow" size={13} />
    </button>
  );
}

function toneOf(tone: string): PillTone {
  return tone === "success" ? "success" : tone === "warning" ? "warning" : tone === "danger" ? "danger" : tone === "accent" ? "info" : "muted";
}

function verdictTone(verdict: NonNullable<QrRunRow["verdict"]>): PillTone {
  if (verdict === "ROBUST_UNDER_TESTED_ASSUMPTIONS" || verdict === "PROMISING_RESEARCH_CANDIDATE") return "success";
  if (verdict === "FORWARD_VALIDATION_REQUIRED") return "info";
  if (verdict === "INSUFFICIENT_EVIDENCE") return "warning";
  return "danger";
}
