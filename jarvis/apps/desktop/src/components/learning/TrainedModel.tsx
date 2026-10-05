import type { TrainingComparison, TrainingPhase, TrainingReport, TrainingRun, TrainingStatus } from "@jarvis/protocol";
import { useState } from "react";

import { Button, Empty, Meter, StatusDot, type Tone, cx, textTone } from "../ui/primitives";
import { ApiError, api } from "../../lib/api";
import { clock, signedPercent, trainingLabel, trainingVerdict } from "../../lib/format";

const STATE_TONE: Record<TrainingPhase, Tone> = {
  off: "faint",
  waiting: "muted",
  preparing: "accent",
  training: "accent",
  error: "danger",
};

const MARKETS: Record<string, string> = { NQ: "NQ", XAUUSD: "Gold" };

/** JARVIS's own support/resistance model: trained here, judged on months it never saw. */
export function TrainedModel({ training }: { training: TrainingStatus }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const report = training.report;
  const working = training.state === "training" || training.state === "preparing";

  const act = async (call: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await call();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-12" data-testid="trained-model">
      <div className="flex items-start justify-between gap-8">
        <div>
          <h2 className="label mb-1">Trained model</h2>
          <p className="max-w-[660px] text-xs leading-relaxed text-fg-faint">
            JARVIS trains its own model of when support and resistance hold — on every touch of the briefing&apos;s key
            levels since 2021, locally on this computer, at no cost. It is judged month by month on data it never
            trained on, against a baseline that only knows the kind of level and how the touching candle closed.
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            onClick={() => void act(() => api.setTraining(!training.enabled))}
            disabled={busy}
            title="Retrain by itself after each new trading day"
            data-testid="training-toggle"
          >
            {training.enabled ? "Daily: on" : "Daily: off"}
          </Button>
          <Button
            variant="primary"
            onClick={() => void act(() => api.trainNow())}
            disabled={busy || working}
            data-testid="training-run"
          >
            Train now
          </Button>
        </div>
      </div>
      {error && <p className="mt-3 text-[13px] text-danger">{error}</p>}

      <div className="mt-4 rounded-xl border border-hairline bg-canvas-2 px-5 py-4">
        <div className="flex items-center justify-between gap-6">
          <div className="flex min-w-0 items-center gap-2 text-[13px] text-fg" data-testid="training-state">
            <StatusDot tone={STATE_TONE[training.state]} live={working} />
            {trainingLabel(training.state)}
            <span className={cx("min-w-0 truncate text-xs", training.state === "error" ? "text-danger" : "text-fg-faint")}>
              {training.state === "off" && !report
                ? "· Turn on daily training or train now."
                : training.detail
                  ? `· ${training.detail}`
                  : ""}
            </span>
          </div>
          {report && <Verdict report={report} />}
        </div>
        {working && training.progress !== null && (
          <div className="mt-3">
            <Meter value={training.progress * 100} tone="accent" />
          </div>
        )}
        {report ? <ReportBody report={report} training={training} /> : null}
      </div>

      {training.history.length > 0 && <History runs={training.history} />}
    </section>
  );
}

function Verdict({ report }: { report: TrainingReport }) {
  const verdict = trainingVerdict(report.status);
  return (
    <span
      className={cx("inline-flex shrink-0 cursor-help items-center gap-1.5 text-xs", textTone(verdict.tone))}
      title={`${verdict.explanation} (${report.reason})`}
      data-testid="training-verdict"
    >
      <StatusDot tone={verdict.tone} />
      {verdict.label}
    </span>
  );
}

function ReportBody({ report, training }: { report: TrainingReport; training: TrainingStatus }) {
  const markets = Object.entries(report.holdout_by_market);
  const bins = report.calibration.filter((bin) => bin.touches > 0);
  const top = report.importance.slice(0, 6);
  return (
    <>
      <table className="mt-4 w-full text-xs">
        <thead className="text-fg-faint">
          <tr className="text-left">
            <th className="py-1 font-normal">Period</th>
            <th className="py-1 text-right font-normal">Touches</th>
            <th className="py-1 text-right font-normal">Held</th>
            <th className="py-1 text-right font-normal" title="How much better than the baseline it predicts (log loss saved)">
              vs. baseline
            </th>
            <th className="py-1 text-right font-normal" title="Touches on the same day count as one">
              t
            </th>
            <th className="py-1 text-right font-normal" title="0.5 = guessing, 1 = perfect">
              AUC model / baseline
            </th>
          </tr>
        </thead>
        <tbody className="font-mono text-fg-muted tabular">
          <ComparisonRow label="Choosing (out-of-sample)" result={report.out_of_sample} need={report.t_required_oos} />
          <ComparisonRow
            label={`Unseen months${report.holdout_months ? ` · ${report.holdout_months}` : ""}`}
            result={report.holdout}
            need={report.t_required_holdout}
          />
          {markets.map(([name, result]) => (
            <ComparisonRow key={name} label={`  ${MARKETS[name] ?? name}`} result={result} faint />
          ))}
        </tbody>
      </table>

      <div className="mt-5 grid grid-cols-2 gap-10">
        <div>
          <h3 className="label mb-2">Do its percentages hold? (unseen months)</h3>
          {bins.length === 0 ? (
            <Empty>Not judged yet.</Empty>
          ) : (
            <ul className="space-y-1 text-xs" data-testid="training-calibration">
              {bins.map((bin) => (
                <li key={bin.from} className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 font-mono tabular">
                  <span className="font-sans text-fg-faint">
                    Said {Math.round((bin.predicted ?? 0) * 100)}% <span className="text-fg-faint/70">({bin.touches})</span>
                  </span>
                  <span className="text-fg-muted">held {Math.round((bin.held ?? 0) * 100)}%</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h3 className="label mb-2">What mattered most</h3>
          {top.length === 0 ? (
            <Empty>Nothing beyond chance.</Empty>
          ) : (
            <ol className="space-y-1 text-xs" data-testid="training-importance">
              {top.map((item, rank) => (
                <li key={item.feature} className="grid grid-cols-[16px_minmax(0,1fr)] gap-2 text-fg-muted">
                  <span className="font-mono text-fg-faint tabular">{rank + 1}</span>
                  <span className="truncate">{item.label}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      <p className="mt-4 text-2xs leading-relaxed text-fg-faint">
        {report.chosen ?? "No model"} · {report.touches.decided.toLocaleString("en-US")} decided touches
        {training.model ? ` · learned from data until ${training.model.data_until}` : ""}
        {report.usable_for.length > 0
          ? ` · JARVIS gives its odds for ${report.usable_for.map((m) => MARKETS[m] ?? m).join(" and ")} (ask during the session).`
          : " · JARVIS only gives the baseline's odds until the model proves itself."}
      </p>
    </>
  );
}

function ComparisonRow({
  label,
  result,
  need,
  faint,
}: {
  label: string;
  result: TrainingComparison | null;
  need?: number;
  faint?: boolean;
}) {
  if (!result || result.touches === 0) return null;
  const improvement = result.improvement ?? 0;
  const passed = need !== undefined && improvement > 0 && result.t >= need;
  return (
    <tr className={cx("border-t border-hairline", faint && "text-fg-faint")}>
      <td className="py-1.5 font-sans whitespace-pre text-fg-faint">{label}</td>
      <td className="py-1.5 text-right">{result.touches.toLocaleString("en-US")}</td>
      <td className="py-1.5 text-right">{result.held_rate === null ? "–" : `${Math.round(result.held_rate * 100)}%`}</td>
      <td className={cx("py-1.5 text-right", improvement > 0 ? "text-fg" : "text-fg-faint")}>{signedPercent(improvement)}</td>
      <td className={cx("py-1.5 text-right", passed && "text-success")} title={need ? `needs ${need.toFixed(2)}` : undefined}>
        {result.t.toFixed(1)}
      </td>
      <td className="py-1.5 text-right">
        {result.auc_model?.toFixed(3) ?? "–"} / {result.auc_baseline?.toFixed(3) ?? "–"}
      </td>
    </tr>
  );
}

function History({ runs }: { runs: TrainingRun[] }) {
  return (
    <ul className="mt-3 divide-y divide-hairline border-y border-hairline" data-testid="training-history">
      {runs.slice(0, 8).map((run) => (
        <li key={run.number} className="grid grid-cols-[64px_minmax(0,1fr)_auto] items-baseline gap-4 py-2 text-xs">
          <span className="font-mono text-2xs text-fg-faint">Run {run.number}</span>
          <span className={cx("min-w-0 truncate", run.status === "done" ? "text-fg-muted" : "text-warning")}>
            {run.status === "done" && run.verdict
              ? `${trainingVerdict(run.verdict).label}${
                  run.holdout_improvement !== null && run.holdout_t !== null
                    ? ` · unseen ${signedPercent(run.holdout_improvement)}, t ${run.holdout_t.toFixed(1)}`
                    : ""
                }`
              : run.status === "running"
                ? "In progress…"
                : (run.error ?? run.status)}
          </span>
          <span className="font-mono text-2xs text-fg-faint tabular">
            {run.data_until ? `data until ${run.data_until} · ` : ""}
            {clock(run.started_at)}
            {run.seconds !== null ? ` · ${Math.round(run.seconds)} s` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

