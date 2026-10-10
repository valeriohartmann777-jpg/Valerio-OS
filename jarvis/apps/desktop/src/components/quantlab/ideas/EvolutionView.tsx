import type { QmComparisonRow, QmMission, QmTrial } from "@jarvis/protocol";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../../../lib/api";
import { Button, cx } from "../../ui/primitives";
import { Badge, Card } from "../ui";
import { fmt, share, tone, usd } from "../research/common";
import { niceTicks } from "../EquityChart";

/**
 * Strategy evolution: the append-only trial ledger (every variant ever tested, failures
 * included), the Pareto comparison and the one-time holdout lock.
 */

export function EvolutionView({ mission, onAct }: { mission: QmMission; onAct: (fn: () => Promise<unknown>) => Promise<void> }) {
  const trials = mission.trials;
  const comparison = mission.refs.comparison;
  const pareto = new Set(comparison?.pareto ?? []);
  const waiting = mission.waiting?.kind === "lock_candidate" ? mission.waiting : null;
  const holdout = mission.refs.holdout;
  const tested = trials.filter((t) => t.outcome === "TESTED" && t.metrics);

  return (
    <Card
      title={`Strategy evolution · ${trials.length} trial(s)`}
      aside={<span className="text-2xs text-fg-faint">append-only ledger · holdout sealed during search</span>}
      testId="qm-evolution"
    >
      {mission.refs.diagnosis?.findings?.length ? (
        <div className="mb-4">
          <p className="label mb-1">CIPHER's diagnosis of the baseline</p>
          <ul className="grid gap-0.5 text-[12px] text-fg-muted">
            {mission.refs.diagnosis.findings.map((f) => (
              <li key={f}>· {f}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {tested.length > 0 && <ParetoChart trials={tested} pareto={pareto} />}

      {comparison?.notes?.length ? (
        <ul className="mt-3 grid gap-0.5 text-[12px] text-fg-muted">
          {comparison.notes.map((n) => (
            <li key={n}>◇ {n}</li>
          ))}
        </ul>
      ) : null}

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[900px] text-left text-[12px]" data-testid="qm-trials">
          <thead className="text-2xs text-fg-faint">
            <tr>
              <th className="py-1 pr-2 font-normal">#</th>
              <th className="py-1 pr-2 font-normal">Variant</th>
              <th className="py-1 pr-2 text-right font-normal">Complexity</th>
              <th className="py-1 pr-2 text-right font-normal">OOS trades</th>
              <th className="py-1 pr-2 text-right font-normal">OOS net</th>
              <th className="py-1 pr-2 text-right font-normal">Win rate</th>
              <th className="py-1 pr-2 text-right font-normal">Profit factor</th>
              <th className="py-1 pr-2 text-right font-normal">Max DD</th>
              <th className="py-1 pr-2 text-right font-normal">DSR</th>
              <th className="py-1 pr-2 font-normal">Audit</th>
              <th className="py-1 font-normal">Status</th>
            </tr>
          </thead>
          <tbody>
            {trials.map((t) => (
              <TrialRow key={t.id} trial={t} pareto={pareto.has(t.id)} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-2xs text-fg-faint">
        Every tested variant counts toward the deflated Sharpe (DSR) of the whole family — selection bias is priced in, not
        hidden. Variants come from a fixed list of allowed changes, each with a mechanism and a falsifiable prediction.
      </p>

      {waiting && <LockCandidate mission={mission} rows={waiting.candidates ?? []} note={waiting.note} onAct={onAct} />}

      {holdout && (
        <div className="mt-4 rounded-lg border border-ql-border px-3 py-2 text-[13px]" data-testid="qm-holdout">
          <p className="text-fg">
            Holdout locked for version <span className="font-mono text-2xs">{holdout.version_id}</span>
            {holdout.status ? ` · holdout test ${holdout.status.toLowerCase().replace(/_/g, " ")}` : ""}
          </p>
          <p className="mt-1 text-[12px] text-fg-muted">
            {holdout.independent === false
              ? "Contaminated: this family's holdout had been looked at before, so this result is not independent evidence and the verdict is capped."
              : "First look at this family's holdout. From now on it is spent: any further variant's holdout result is marked contaminated."}
          </p>
        </div>
      )}
    </Card>
  );
}

function TrialRow({ trial, pareto }: { trial: QmTrial; pareto: boolean }) {
  const [open, setOpen] = useState(false);
  const m = trial.metrics;
  const audit = trial.audit;
  return (
    <>
      <tr className={cx("border-t border-ql-border/60 align-top", open && "bg-white/[0.02]")} data-testid="qm-trial" data-outcome={trial.outcome}>
        <td className="py-1.5 pr-2 font-mono text-2xs text-fg-faint">{trial.iteration === 0 ? "base" : trial.iteration}</td>
        <td className="py-1.5 pr-2">
          <button type="button" className="text-left text-fg hover:underline" onClick={() => setOpen(!open)}>
            {pareto && <span title="On the Pareto front" className="mr-1 text-ql">◆</span>}
            {trial.title}
          </button>
        </td>
        <td className="py-1.5 pr-2 text-right font-mono">{trial.complexity}</td>
        <td className="py-1.5 pr-2 text-right font-mono">{m?.oos_trades ?? "—"}</td>
        <td className={cx("py-1.5 pr-2 text-right font-mono", tone(m?.oos_net))}>{usd(m?.oos_net, true, 0)}</td>
        <td className="py-1.5 pr-2 text-right font-mono">{share(m?.oos_win_rate)}</td>
        <td className="py-1.5 pr-2 text-right font-mono">{fmt(m?.oos_profit_factor)}</td>
        <td className="py-1.5 pr-2 text-right font-mono">{usd(m?.max_drawdown, false, 0)}</td>
        <td className="py-1.5 pr-2 text-right font-mono">{fmt(m?.dsr, 2)}</td>
        <td className="py-1.5 pr-2">
          {audit ? (
            <span className={audit.passed ? "text-ql-positive" : "text-ql-danger"}>{audit.passed ? "✓ passed" : "✕ failed"}</span>
          ) : (
            <span className="text-fg-faint">—</span>
          )}
        </td>
        <td className="py-1.5">
          <span className="text-fg-muted">{trial.outcome.toLowerCase().replace("_", " ")}</span>
          {trial.post_holdout && <Badge tone="warning">after holdout</Badge>}
        </td>
      </tr>
      {open && (
        <tr>
          <td />
          <td colSpan={10} className="pb-3 text-[12px] text-fg-muted">
            {trial.mechanism && <p><span className="text-fg-faint">Mechanism:</span> {trial.mechanism}</p>}
            {trial.prediction && <p><span className="text-fg-faint">Prediction:</span> {trial.prediction}</p>}
            {trial.changes.length > 0 && (
              <p className="font-mono text-2xs">
                {trial.changes.map((c) => `${c.path} = ${JSON.stringify(c.value)}`).join(" · ")}
              </p>
            )}
            {m?.would_be && <p>Fixed verdict function on this variant alone: {m.would_be.replace(/_/g, " ").toLowerCase()}</p>}
          </td>
        </tr>
      )}
    </>
  );
}

const PAD = { top: 16, right: 16, bottom: 36, left: 64 };
const HEIGHT = 240;

/** OOS net vs complexity, one hue: filled ◆ = Pareto front, hollow ○ = dominated, ■ = baseline. */
function ParetoChart({ trials, pareto }: { trials: QmTrial[]; pareto: Set<string> }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<string | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => entry && setWidth(Math.max(320, entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const geo = useMemo(() => {
    const nets = trials.map((t) => t.metrics?.oos_net ?? 0);
    const lo = Math.min(0, ...nets);
    const hi = Math.max(0, ...nets);
    const ticks = niceTicks(lo, hi === lo ? lo + 1 : hi, 4);
    const yMin = Math.min(lo, ticks[0] ?? lo);
    const yMax = Math.max(hi, ticks[ticks.length - 1] ?? hi);
    const maxCx = Math.max(1, ...trials.map((t) => t.complexity));
    const x = (v: number) => PAD.left + (v / maxCx) * (width - PAD.left - PAD.right - 20) + 10;
    const y = (v: number) => PAD.top + (1 - (v - yMin) / (yMax - yMin || 1)) * (HEIGHT - PAD.top - PAD.bottom);
    // Several variants can share a complexity: spread them a few pixels so none hides another.
    const seen = new Map<number, number>();
    const points = trials.map((t) => {
      const n = seen.get(t.complexity) ?? 0;
      seen.set(t.complexity, n + 1);
      const offset = n === 0 ? 0 : (n % 2 ? 1 : -1) * Math.ceil(n / 2) * 9;
      return { t, px: x(t.complexity) + offset, py: y(t.metrics?.oos_net ?? 0) };
    });
    return { ticks, x, y, maxCx, points };
  }, [trials, width]);

  const active = geo.points.find((p) => p.t.id === hover);

  return (
    <div ref={ref} className="relative" data-testid="qm-pareto">
      <div className="mb-2 flex flex-wrap items-center gap-4 text-2xs text-fg-muted" aria-hidden>
        <span className="flex items-center gap-1.5"><svg width="10" height="10"><path d="M5 0 L10 5 L5 10 L0 5 Z" fill="var(--color-ql)" /></svg> Pareto front</span>
        <span className="flex items-center gap-1.5"><svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="none" stroke="var(--color-ql-muted)" strokeWidth="1.5" /></svg> Dominated</span>
        <span className="flex items-center gap-1.5"><svg width="10" height="10"><rect x="1" y="1" width="8" height="8" fill="var(--color-ql-muted)" /></svg> Baseline (source rules)</span>
        <span className="ml-auto text-fg-faint">y: out-of-sample net (USD) · front = not beaten on OOS net, OOS trades, drawdown and complexity at once</span>
      </div>
      <svg width={width} height={HEIGHT} role="img" aria-label="Out-of-sample net result against complexity for every tested variant" onPointerLeave={() => setHover(null)}>
        {geo.ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={width - PAD.right} y1={geo.y(v)} y2={geo.y(v)} stroke="white" strokeOpacity={v === 0 ? 0.25 : 0.05} />
            <text x={PAD.left - 8} y={geo.y(v) + 3} textAnchor="end" className="fill-fg-faint font-mono text-[10px]">
              {usd(v, false, 0)}
            </text>
          </g>
        ))}
        {Array.from({ length: geo.maxCx + 1 }, (_, i) => (
          <text key={i} x={geo.x(i)} y={HEIGHT - PAD.bottom + 16} textAnchor="middle" className="fill-fg-faint font-mono text-[10px]">
            {i}
          </text>
        ))}
        <text x={(width + PAD.left) / 2} y={HEIGHT - 4} textAnchor="middle" className="fill-fg-faint text-[10px]">
          complexity (changed fields + active filters)
        </text>
        {geo.points.map(({ t, px, py }) => {
          const front = pareto.has(t.id);
          const base = t.iteration === 0;
          const lifted = hover === t.id;
          return (
            <g
              key={t.id}
              tabIndex={0}
              onPointerEnter={() => setHover(t.id)}
              onFocus={() => setHover(t.id)}
              onBlur={() => setHover(null)}
              aria-label={`${t.title}: OOS net ${usd(t.metrics?.oos_net, true, 0)}, complexity ${t.complexity}${front ? ", on the Pareto front" : ""}`}
              className="cursor-default outline-none"
            >
              <circle cx={px} cy={py} r={13} fill="transparent" />
              {base ? (
                <rect x={px - 5} y={py - 5} width={10} height={10} fill="var(--color-ql-muted)" stroke="var(--color-ql-surface)" strokeWidth={2} />
              ) : front ? (
                <path d={`M${px} ${py - 7} L${px + 7} ${py} L${px} ${py + 7} L${px - 7} ${py} Z`} fill="var(--color-ql)" stroke="var(--color-ql-surface)" strokeWidth={2} />
              ) : (
                <circle cx={px} cy={py} r={5} fill="var(--color-ql-surface)" stroke="var(--color-ql-muted)" strokeWidth={2} />
              )}
              {lifted && <circle cx={px} cy={py} r={10} fill="none" stroke="white" strokeOpacity={0.4} />}
            </g>
          );
        })}
      </svg>
      {active && (
        <div
          className="pointer-events-none absolute z-10 w-60 rounded-lg border border-ql-border bg-ql-raised px-3 py-2 text-[12px] shadow-lg"
          style={{ left: Math.min(active.px + 12, width - 250), top: Math.max(0, active.py - 10) }}
          role="tooltip"
        >
          <p className={cx("font-mono text-[14px]", tone(active.t.metrics?.oos_net))}>{usd(active.t.metrics?.oos_net, true, 0)}</p>
          <p className="text-fg-muted">{active.t.title}</p>
          <p className="mt-1 font-mono text-2xs text-fg-faint">
            {active.t.metrics?.oos_trades ?? 0} OOS trades · DD {usd(active.t.metrics?.max_drawdown, false, 0)} · complexity {active.t.complexity}
          </p>
        </div>
      )}
    </div>
  );
}

function LockCandidate({
  mission,
  rows,
  note,
  onAct,
}: {
  mission: QmMission;
  rows: QmComparisonRow[];
  note?: string;
  onAct: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [pick, setPick] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  return (
    <div className="mt-5 rounded-xl border border-ql-warning/40 bg-ql-warning/5 px-4 py-3" data-testid="qm-wait-lock">
      <p className="text-[14px] text-fg">Needs you · lock one candidate for its one-time holdout test</p>
      <p className="mt-1 text-[12px] text-fg-muted">{note}</p>
      <div className="mt-3 grid gap-1">
        {rows.map((r) => (
          <label key={r.version_id} className="flex cursor-pointer items-center gap-2 text-[12px]">
            <input type="radio" name="lock" checked={pick === r.version_id} onChange={() => setPick(r.version_id)} />
            <span className="text-fg">{r.title}</span>
            <span className="font-mono text-2xs text-fg-faint">
              OOS {usd(r.oos_net, true, 0)} · {r.oos_trades ?? 0} trades · complexity {r.complexity}
            </span>
          </label>
        ))}
      </div>
      <label className="mt-3 flex items-center gap-2 text-[12px] text-fg">
        <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} data-testid="qm-lock-confirm" />
        I understand the holdout is evaluated once and is spent afterwards
      </label>
      <Button
        className="mt-3"
        variant="warning"
        disabled={!pick || !confirmed}
        onClick={() => pick && void onAct(() => api.qmLock(mission.id, pick))}
        data-testid="qm-lock"
      >
        Lock candidate & open holdout
      </Button>
    </div>
  );
}
