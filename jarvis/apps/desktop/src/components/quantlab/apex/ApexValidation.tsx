import type { ApexForward, ApexStrategyName, ApexTrial, ApexValidation as Run, ApexValidationStats } from "@jarvis/protocol";
import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../../../lib/api";
import { Icon } from "../../ui/icons";
import { Button, cx } from "../../ui/primitives";
import { Panel, StatusPill } from "../../ui/shell";
import { STRATEGIES, VALIDATION_TEXT, VALIDATION_TONE } from "./analyst";

/**
 * APEX → Validation: runs the pre-registered protocol (config/apex_validation.yaml) as a
 * background job — progress, cancel, retry — and shows every criterion with what it measured,
 * the folds, the cost stress, the parameter neighbours, the random baseline, the holdout
 * (opened once per strategy version) and the trial register.
 */

const r = (x: number | null | undefined) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(3)} R`);
const day = (iso: string) => iso.slice(0, 10);

export function ApexValidationPanel({ market, strategy, onStrategy }: { market: string; strategy: ApexStrategyName; onStrategy: (s: ApexStrategyName) => void }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [full, setFull] = useState<Run | null>(null);
  const [trials, setTrials] = useState<{ real: ApexTrial[]; synthetic: ApexTrial[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [all, t] = await Promise.all([api.apexValidations(market), api.apexTrials(market, strategy)]);
    setRuns(all);
    setTrials(t);
    const mine = all.find((v) => v.strategy === strategy);
    setFull(mine && mine.state !== "RUNNING" ? await api.apexValidation(mine.id) : (mine ?? null));
  }, [market, strategy]);

  useEffect(() => {
    load().catch(() => setError("The validations couldn't be loaded."));
  }, [load]);

  const running = runs?.find((v) => v.state === "RUNNING") ?? null;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void load(), 1000);
    return () => clearInterval(timer);
  }, [running, load]);

  const start = async () => {
    setError(null);
    try {
      await api.apexValidate(market, strategy);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message}${err.suggestion ? ` — ${err.suggestion}` : ""}` : "The validation didn't start.");
    }
  };
  const cancel = async () => {
    if (!running) return;
    try {
      await api.apexCancelValidation(running.id);
    } finally {
      await load();
    }
  };

  const result = full?.state === "COMPLETED" ? full.result : null;
  const dev = result?.development;
  const verdict = result?.verdict;
  const list = trials ? [...trials.real, ...trials.synthetic] : [];
  return (
    <Panel
      title={`Validation · ${market}`}
      aside={<span className="text-[12px] text-fg-muted">pre-registered protocol — numbers fixed before any holdout</span>}
      testId="apex-validation"
    >
      <div className="flex flex-wrap items-center gap-3">
        <div role="group" aria-label="Strategy" className="flex rounded-lg border border-control/60 p-0.5">
          {STRATEGIES.map((s) => (
            <button
              key={s.id}
              type="button"
              aria-pressed={strategy === s.id}
              onClick={() => onStrategy(s.id)}
              className={cx("h-8 rounded-md px-3 text-[12px] font-semibold", strategy === s.id ? "bg-quant/20 text-fg" : "text-fg-muted hover:text-fg")}
              data-testid={`apex-validation-strategy-${s.id}`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <Button
          variant="primary"
          onClick={() => void start()}
          disabled={Boolean(running)}
          reason={running ? "A validation is running." : null}
          data-testid="apex-validate"
        >
          <Icon name="play" size={14} /> {full && full.state !== "RUNNING" ? "Run again" : "Run validation"}
        </Button>
        {running && (
          <>
            <span className="text-[13px] text-fg-muted" data-testid="apex-validation-progress">
              {running.strategy} · running… {Math.round(running.progress * 100)}%
            </span>
            <Button onClick={() => void cancel()} data-testid="apex-validation-cancel">
              <Icon name="x" size={14} /> Cancel
            </Button>
          </>
        )}
        {error && (
          <span className="text-[13px] text-danger" data-testid="apex-validation-error">
            {error}
          </span>
        )}
      </div>
      <p className="mt-2 text-[12px] leading-relaxed text-fg-faint">
        Development folds with an embargo of the full outcome window, a random baseline with the same risk and costs judged against
        α divided by every trial registered, 1.5× cost stress, the one-step parameter neighbours — and an untouched holdout that
        opens only when all of that passed, once per strategy version. Each run adds its trials to the register.
      </p>

      {full && full.state === "FAILED" && <p className="mt-3 text-[13px] text-danger">Last run failed: {full.error}</p>}
      {full && full.state === "CANCELLED" && (
        <p className="mt-3 text-[13px] text-fg-muted" data-testid="apex-validation-cancelled">
          Last run was cancelled — run it again to get a verdict.
        </p>
      )}
      {result && verdict && dev && (
        <div className="mt-4 flex flex-col gap-4" data-testid="apex-validation-result" data-state={verdict.validation_state}>
          {result.data.synthetic && (
            <p className="text-[12px] font-bold text-warning">On SYNTHETIC test data — this checks the mechanics, never the market.</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={VALIDATION_TONE[verdict.validation_state]} testId="apex-validation-state">
              {verdict.validation_state.replace(/_/g, " ")}
            </StatusPill>
            <span className="text-[13px] text-fg">{verdict.text}</span>
          </div>
          <p className="text-[12px] text-fg-faint">
            {VALIDATION_TEXT[verdict.validation_state]} · protocol {result.protocol_version} · {result.strategy} {result.version} · data{" "}
            {result.data.label} ({result.data.sha256.slice(0, 12)}) · development {day(result.split.dev[0])} → {day(result.split.dev[1])} ·
            holdout {day(result.split.holdout[0])} → {day(result.split.holdout[1])} (frozen) · embargo {dev.embargo_minutes} min
          </p>

          <table className="w-full text-[13px]" data-testid="apex-criteria">
            <thead>
              <tr className="text-left text-[12px] text-fg-faint">
                <th className="pb-1.5 font-semibold">Criterion</th>
                <th className="pb-1.5 font-semibold">Required</th>
                <th className="pb-1.5 font-semibold">Measured</th>
                <th className="pb-1.5 text-right font-semibold">Passed</th>
              </tr>
            </thead>
            <tbody>
              {dev.criteria.map((c) => (
                <tr key={c.code} className="border-t border-hairline-strong/60" data-testid="apex-criterion" data-code={c.code} data-passed={c.passed ? "yes" : "no"}>
                  <td className="py-1 font-bold text-fg">{c.code.replace(/_/g, " ")}</td>
                  <td className="py-1 text-fg-muted">{c.required}</td>
                  <td className="py-1 font-mono text-fg">{c.measured}</td>
                  <td className={cx("py-1 text-right font-bold", c.passed ? "text-success" : "text-danger")}>{c.passed ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <StatsTable
              title="Development folds"
              rows={[["All", dev.stats], ...dev.folds.map((f) => [`Fold ${f.fold}`, f] as [string, ApexValidationStats])]}
              testId="apex-folds"
            />
            <StatsTable
              title="Robustness"
              rows={[
                [`Costs × ${dev.cost_stress.factor}`, dev.cost_stress],
                ...dev.neighbours.map(
                  (n) =>
                    [
                      Object.entries(n.change)
                        .map(([k, v]) => `${k} ${v}`)
                        .join(", "),
                      n,
                    ] as [string, ApexValidationStats],
                ),
              ]}
              testId="apex-robustness"
            />
          </div>
          <p className="text-[12px] text-fg-muted" data-testid="apex-baseline">
            Random baseline ({dev.baseline.runs} runs, same days, random time and side, same risk/target and costs): mean {r(dev.baseline.mean_r)}
            {dev.baseline.p95_r != null ? `, 95th percentile ${r(dev.baseline.p95_r)}` : ""} · p = {dev.baseline.p_value ?? "—"} against α{" "}
            {dev.trials.alpha_corrected} ({dev.trials.alpha} / {dev.trials.total} trials).
          </p>
          <div className="rounded-xl border border-hairline-strong bg-surface-2/40 px-3 py-2 text-[13px]" data-testid="apex-holdout" data-opened={result.holdout ? "yes" : "no"}>
            <b className="text-fg">Holdout</b>{" "}
            {result.holdout ? (
              <span className="text-fg-muted">
                opened by {result.holdout.opened_by} · {result.holdout.stats.filled} filled · mean {r(result.holdout.stats.mean_r)}
                {result.holdout.stats.ci ? ` [${r(result.holdout.stats.ci[0])}, ${r(result.holdout.stats.ci[1])}]` : ""} ·{" "}
                {result.holdout.passed ? "passed" : result.holdout.enough ? "failed" : "too few trades"}
              </span>
            ) : (
              <span className="text-fg-muted">still closed — it opens only after every development criterion passed.</span>
            )}
          </div>
        </div>
      )}

      <ForwardLog market={market} />

      <div className="mt-4 border-t border-hairline-strong pt-3" data-testid="apex-trials">
        <h4 className="text-[13px] font-black tracking-[0.04em] text-fg uppercase">Trial register · {market} · {strategy}</h4>
        {list.length === 0 ? (
          <p className="mt-1 text-[12px] text-fg-muted">No trials yet. Every parameter set a validation measures is registered here — append-only.</p>
        ) : (
          <table className="mt-2 w-full text-[12px]">
            <thead>
              <tr className="text-left text-fg-faint">
                <th className="pb-1 font-semibold">#</th>
                <th className="pb-1 font-semibold">Kind</th>
                <th className="pb-1 font-semibold">Version</th>
                <th className="pb-1 font-semibold">Changed</th>
                <th className="pb-1 text-right font-semibold">Filled</th>
                <th className="pb-1 text-right font-semibold">Mean R</th>
                <th className="pb-1 text-right font-semibold">Data</th>
              </tr>
            </thead>
            <tbody>
              {list.slice(-40).map((t) => (
                <tr key={t.id} className="border-t border-hairline-strong/60" data-testid="apex-trial">
                  <td className="py-0.5 font-mono">{t.id}</td>
                  <td className="py-0.5">{t.kind}</td>
                  <td className="py-0.5 font-mono">{t.version}</td>
                  <td className="py-0.5 font-mono text-fg-muted">{t.kind === "neighbour" ? changed(t.params, list.find((x) => x.validation_id === t.validation_id && x.kind === "registered")?.params) : "registered"}</td>
                  <td className="py-0.5 text-right font-mono">{t.summary.filled}</td>
                  <td className="py-0.5 text-right font-mono">{r(t.summary.mean_r)}</td>
                  <td className="py-0.5 text-right">{t.synthetic ? "synthetic" : "real"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}

function changed(params: Record<string, number>, base: Record<string, number> | undefined): string {
  if (!base) return "";
  return Object.entries(params)
    .filter(([k, v]) => base[k] !== v)
    .map(([k, v]) => `${k} ${v}`)
    .join(", ");
}

function StatsTable({ title, rows, testId }: { title: string; rows: [string, ApexValidationStats][]; testId: string }) {
  return (
    <div data-testid={testId}>
      <h4 className="text-[12px] font-black tracking-[0.04em] text-fg-faint uppercase">{title}</h4>
      <table className="mt-1 w-full text-[12px]">
        <thead>
          <tr className="text-left text-fg-faint">
            <th className="pb-1 font-semibold" />
            <th className="pb-1 text-right font-semibold">Filled</th>
            <th className="pb-1 text-right font-semibold">Days</th>
            <th className="pb-1 text-right font-semibold">Mean R (95%)</th>
            <th className="pb-1 text-right font-semibold">PF</th>
            <th className="pb-1 text-right font-semibold">Max DD</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, s]) => (
            <tr key={name} className="border-t border-hairline-strong/60">
              <td className="py-0.5 text-fg">{name}</td>
              <td className="py-0.5 text-right font-mono">{s.filled}</td>
              <td className="py-0.5 text-right font-mono">{s.trade_days}</td>
              <td className="py-0.5 text-right font-mono">
                {r(s.mean_r)}
                {s.ci ? ` [${s.ci[0].toFixed(2)}, ${s.ci[1].toFixed(2)}]` : ""}
              </td>
              <td className="py-0.5 text-right font-mono">{s.profit_factor ?? "—"}</td>
              <td className="py-0.5 text-right font-mono">{s.max_drawdown_r.toFixed(1)} R</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The paper forward log: live analyses on a schedule; their plans, judged later, are the
 * forward evidence (never replays, never test data for a real verdict). */
function ForwardLog({ market }: { market: string }) {
  const [f, setF] = useState<ApexForward | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => setF(await api.apexForward(market)), [market]);
  useEffect(() => {
    load().catch(() => setF(null));
  }, [load]);
  const toggle = async () => {
    if (!f) return;
    setBusy(true);
    try {
      await api.apexSetForward(!f.enabled);
      await load();
    } finally {
      setBusy(false);
    }
  };
  const runNow = async () => {
    setBusy(true);
    try {
      await api.apexForwardRun();
      await load();
    } finally {
      setBusy(false);
    }
  };
  if (!f) return null;
  const last = f.markets[market];
  const evidence = f.evidence[market] ?? {};
  return (
    <div className="mt-4 border-t border-hairline-strong pt-3" data-testid="apex-forward" data-enabled={f.enabled ? "yes" : "no"}>
      <div className="flex flex-wrap items-center gap-3">
        <h4 className="text-[13px] font-black tracking-[0.04em] text-fg uppercase">Paper forward log</h4>
        <StatusPill tone={f.enabled ? "success" : "muted"}>{f.enabled ? `on · every ${f.interval_minutes} min in session` : "off"}</StatusPill>
        <Button onClick={() => void toggle()} disabled={busy} data-testid="apex-forward-toggle">
          {f.enabled ? "Switch off" : "Switch on"}
        </Button>
        <Button onClick={() => void runNow()} disabled={busy} data-testid="apex-forward-run">
          <Icon name="play" size={13} /> Run one round now
        </Button>
        {last && (
          <span className="text-[12px] text-fg-muted" data-testid="apex-forward-last" data-state={last.state}>
            last round {new Date(last.at * 1000).toLocaleTimeString()}:{" "}
            {last.state === "ok" ? "analysed" : last.state === "outside_session" ? "outside the session" : `failed — ${last.error}`}
          </span>
        )}
      </div>
      <p className="mt-1 text-[12px] text-fg-faint">
        While on, JARVIS analyses each market every {f.interval_minutes} minutes inside its session and records the signals as they are — no
        orders. Their plans, judged on later bars, are the forward evidence: ≥ 30 filled with the day-bootstrap interval above 0 lifts
        an out-of-sample-supported version to FORWARD SUPPORTED.
      </p>
      <table className="mt-2 w-full text-[12px]">
        <thead>
          <tr className="text-left text-fg-faint">
            <th className="pb-1 font-semibold">Strategy</th>
            <th className="pb-1 text-right font-semibold">Live plans</th>
            <th className="pb-1 text-right font-semibold">Filled</th>
            <th className="pb-1 text-right font-semibold">Mean R (95%)</th>
            <th className="pb-1 text-right font-semibold">Still needed</th>
          </tr>
        </thead>
        <tbody>
          {Object.values(evidence).map((e) =>
            e ? (
              <tr key={e.strategy} className="border-t border-hairline-strong/60" data-testid="apex-forward-evidence" data-passed={e.passed ? "yes" : "no"}>
                <td className="py-0.5 font-mono">
                  {e.strategy} {e.version}
                  {e.synthetic ? <span className="ml-1 font-sans text-warning">test data</span> : null}
                </td>
                <td className="py-0.5 text-right font-mono">{e.plans}</td>
                <td className="py-0.5 text-right font-mono">{e.filled}</td>
                <td className="py-0.5 text-right font-mono">
                  {r(e.mean_r)}
                  {e.ci ? ` [${e.ci[0].toFixed(2)}, ${e.ci[1].toFixed(2)}]` : ""}
                </td>
                <td className="py-0.5 text-right font-mono">{e.needed}</td>
              </tr>
            ) : null,
          )}
        </tbody>
      </table>
    </div>
  );
}
