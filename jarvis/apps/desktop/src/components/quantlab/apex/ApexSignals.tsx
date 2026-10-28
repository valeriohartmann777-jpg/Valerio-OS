import type { ApexFitness, ApexSignal } from "@jarvis/protocol";
import { useEffect, useState } from "react";

import { api } from "../../../lib/api";
import { cx } from "../../ui/primitives";
import { LoadingState, Panel, StatusPill, type PillTone } from "../../ui/shell";
import { SIGNAL_TONE, VALIDATION_TONE } from "./analyst";

/**
 * APEX → Signal journal: every strategy decision as it was recorded at its moment (immutable),
 * with the outcome judged later on the same source's bars. Replays and generated test data are
 * marked; nothing here is ever rewritten.
 */

const OUTCOME_TONE: Record<string, PillTone> = { win: "success", loss: "danger", timeout: "warning", expired: "muted", missed: "muted", pending: "info", open: "info" };

export function SignalJournal({ market, decimals, refreshKey }: { market: string; decimals: number; refreshKey: string | undefined }) {
  const [rows, setRows] = useState<ApexSignal[] | null>(null);
  const [onlyPlans, setOnlyPlans] = useState(false);
  useEffect(() => {
    let live = true;
    api
      .apexSignals(market)
      .then((s) => live && setRows(s))
      .catch(() => live && setRows([]));
    return () => {
      live = false;
    };
  }, [market, refreshKey]);
  if (!rows) return <LoadingState>Loading the signal journal…</LoadingState>;
  const shown = onlyPlans ? rows.filter((s) => s.entry != null) : rows;
  const fmt = (p: number | null) => (p == null ? "—" : p.toFixed(decimals));
  return (
    <Panel
      title={`Signal journal · ${market}`}
      aside={
        <label className="flex items-center gap-2 text-[12px] text-fg-muted">
          <input type="checkbox" checked={onlyPlans} onChange={(e) => setOnlyPlans(e.target.checked)} data-testid="apex-signals-plans" />
          only order plans
        </label>
      }
      testId="apex-signals"
    >
      <p className="mb-2 text-[12px] text-fg-faint">
        Written once at the moment of the analysis — status, plan and validation state as they were then. Outcomes are added
        later as separate records; the original is never changed.
      </p>
      {shown.length === 0 ? (
        <p className="text-[13px] text-fg-muted">No signals yet — every analysis records one per strategy decision.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-fg-faint">
                <th className="pb-1.5 font-semibold">As of</th>
                <th className="pb-1.5 font-semibold">Strategy</th>
                <th className="pb-1.5 font-semibold">Status</th>
                <th className="pb-1.5 font-semibold">Plan</th>
                <th className="pb-1.5 pr-3 text-right font-semibold">R:R</th>
                <th className="pb-1.5 font-semibold">Validation</th>
                <th className="pb-1.5 font-semibold">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {shown.slice(0, 80).map((s) => (
                <tr key={s.signal_id} className="border-t border-hairline-strong/60 align-top" data-testid="apex-signal-row" data-status={s.status}>
                  <td className="py-1 text-fg-muted">
                    {new Date(s.as_of_utc).toLocaleString()}
                    {s.synthetic && <span className="ml-1 text-[11px] text-warning">synthetic</span>}
                    {s.replay && <span className="ml-1 text-[11px] text-fg-faint">replay</span>}
                  </td>
                  <td className="py-1 font-mono text-fg">
                    {s.strategy_id} {s.strategy_version}
                  </td>
                  <td className="py-1">
                    <StatusPill tone={SIGNAL_TONE[s.status]}>{s.status.replace(/_/g, " ")}</StatusPill>
                  </td>
                  <td className="py-1 font-mono text-fg">
                    {s.entry == null ? (
                      <span className="font-sans text-fg-faint" title={s.reasons.join(" ")}>
                        {s.reasons[0]?.slice(0, 70) ?? "—"}
                      </span>
                    ) : (
                      <span className={cx(s.direction === "LONG" ? "text-quant" : "text-danger")}>
                        {s.direction} {s.entry_type.toLowerCase()} {fmt(s.entry)} · SL {fmt(s.stop_loss)} · TP {fmt(s.take_profit_1)}
                      </span>
                    )}
                  </td>
                  <td className="py-1 pr-3 text-right font-mono">{s.risk_reward_after_costs ?? "—"}</td>
                  <td className="py-1">
                    <StatusPill tone={VALIDATION_TONE[s.validation_state]}>{s.validation_state.replace(/_/g, " ")}</StatusPill>
                  </td>
                  <td className="py-1">
                    {s.candidate_id ? (
                      <StatusPill tone={OUTCOME_TONE[s.outcome?.state ?? "pending"] ?? "muted"}>
                        {`${s.outcome?.state ?? "pending"}${s.outcome?.r != null ? ` ${s.outcome.r.toFixed(2)} R` : ""}`}
                      </StatusPill>
                    ) : (
                      <span className="text-fg-faint">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/** APEX → Data fitness: coverage, gaps by reason, spikes, quote side, contract, execution. */
export function FitnessView({ market, refreshKey }: { market: string; refreshKey: string | undefined }) {
  const [f, setF] = useState<ApexFitness | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setError(null);
    api
      .apexFitness(market)
      .then((x) => live && setF(x))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "Not available."));
    return () => {
      live = false;
    };
  }, [market, refreshKey]);
  if (error) {
    return (
      <Panel title={`Data fitness · ${market}`} testId="apex-fitness">
        <p className="text-[13px] text-danger" data-testid="apex-fitness-error">
          {error}
        </p>
      </Panel>
    );
  }
  if (!f) return <LoadingState>Checking the data…</LoadingState>;
  const tone: PillTone = f.status === "OK" && !f.synthetic ? "success" : f.status === "OK" || f.status === "WARN" ? "warning" : "danger";
  const share = f.coverage?.share;
  return (
    <Panel
      title={`Data fitness · ${market}`}
      aside={
        <StatusPill tone={tone} testId="apex-fitness-status">
          {f.synthetic && f.status === "OK" ? "Gate OK · test data" : f.status.replace(/_/g, " ")}
        </StatusPill>
      }
      testId="apex-fitness"
    >
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-2">
        <Item label="Source">
          {f.label} <span className="text-fg-faint">({f.source_id})</span>
        </Item>
        <Item label="Symbol · contract">
          {f.symbol} · {f.contract}
        </Item>
        <Item label="Quote side" testId="apex-fitness-side">
          {f.quote_side === "bid" ? "bid only" : f.quote_side === "trades" ? "traded prices" : "generated"}
        </Item>
        <Item label="Granularity">{f.granularity}</Item>
        <Item label="Last closed bar">{f.last_closed_text ?? "—"}</Item>
        <Item label="Span">{f.span_days != null ? `${f.span_days} days · ${f.bars.toLocaleString()} bars` : "—"}</Item>
        <Item label="Expected bars" testId="apex-fitness-coverage">
          {f.coverage ? `${f.coverage.present_bars.toLocaleString()} of ${f.coverage.expected_bars.toLocaleString()} (${share != null ? (share * 100).toFixed(2) : "—"}%)` : "—"}
        </Item>
        <Item label="Execution">{f.execution_text}</Item>
      </dl>
      {f.gaps && (
        <div className="mt-3">
          <h4 className="text-[12px] font-black tracking-[0.04em] text-fg-faint uppercase">Gaps by reason · {f.gaps.missing_minutes} missing minutes</h4>
          <ul className="mt-1 flex flex-col gap-1 text-[13px]">
            {f.gaps.by_reason.map((g) => (
              <li key={g.reason} data-testid="apex-gap" data-reason={g.reason} data-minutes={g.minutes}>
                <b className="text-fg">{g.minutes}</b> <span className="text-fg-muted">{g.text}</span>
                {g.examples && g.examples.length > 0 && (
                  <span className="ml-1 font-mono text-[12px] text-fg-faint">
                    {g.examples
                      .slice(0, 4)
                      .map((e) => (typeof e === "string" ? e : `${e.from} (${e.minutes} min)`))
                      .join(", ")}
                  </span>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-[12px] text-fg-faint">{f.gaps.calendar}</p>
        </div>
      )}
      <div className="mt-3">
        <h4 className="text-[12px] font-black tracking-[0.04em] text-fg-faint uppercase">Spikes · {f.spikes.length}</h4>
        {f.spikes.length === 0 ? (
          <p className="text-[12px] text-fg-muted">None found on consecutive minutes.</p>
        ) : (
          <ul className="mt-1 font-mono text-[12px] text-fg-muted">
            {f.spikes.slice(0, 8).map((s) => (
              <li key={s.t} data-testid="apex-spike">
                {s.at} · {s.kind} · {s.sigma}σ
              </li>
            ))}
          </ul>
        )}
      </div>
      {f.hints.length > 0 && (
        <ul className="mt-3 flex list-disc flex-col gap-0.5 pl-5 text-[12px] text-fg-muted" data-testid="apex-fitness-hints">
          {f.hints.map((h) => (
            <li key={h}>{h}</li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Item({ label, children, testId }: { label: string; children: React.ReactNode; testId?: string }) {
  return (
    <div className="flex gap-2" data-testid={testId}>
      <dt className="w-32 shrink-0 text-fg-faint">{label}</dt>
      <dd className="text-fg">{children}</dd>
    </div>
  );
}
