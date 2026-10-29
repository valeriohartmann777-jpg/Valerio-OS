import type { ApexAnalysis, ApexSignal, ApexStrategyName } from "@jarvis/protocol";

import { cx } from "../../ui/primitives";
import { Panel, StatusPill } from "../../ui/shell";
import { STRATEGIES, SIGNAL_TONE, VALIDATION_TEXT, VALIDATION_TONE, dataReadiness, decisionOf, executionText, signalsOf, type Chip } from "./analyst";

/**
 * The Analyst Panel: for one strategy, in reading order — is the data ready, what is the
 * structure, is there a setup, did it trigger, what would the order plan be, what does it
 * risk after costs, what evidence stands behind the rule, and why (not) to trade.
 */

const when = (iso: string | number) => new Date(typeof iso === "number" ? iso * 1000 : iso).toLocaleString();

export function StatusStrip({ chips }: { chips: Chip[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="apex-status-strip">
      {chips.map((c) => (
        <span key={c.key} className="flex items-center gap-1.5 text-[12px] text-fg-faint" data-testid={`apex-status-${c.key}`} data-tone={c.tone}>
          {c.label}
          <StatusPill tone={c.tone}>{c.text}</StatusPill>
        </span>
      ))}
    </div>
  );
}

export function AnalystPanel({
  analysis,
  strategy,
  onStrategy,
  onValidation,
  onSelect,
  decimals,
}: {
  analysis: ApexAnalysis;
  strategy: ApexStrategyName;
  onStrategy: (s: ApexStrategyName) => void;
  onValidation: () => void;
  onSelect: (candidateId: string) => void;
  decimals: number;
}) {
  const fmt = (p: number | null | undefined) => (p == null ? "—" : p.toFixed(decimals));
  const data = dataReadiness(analysis);
  const decision = decisionOf(analysis, strategy);
  const signals = signalsOf(analysis, strategy);
  const lead: ApexSignal | null = signals.find((s) => s.entry != null) ?? signals[0] ?? null;
  const meta = STRATEGIES.find((s) => s.id === strategy);
  const s15 = analysis.structure["15"];
  const s60 = analysis.structure["60"];
  const plans = signals.filter((s) => s.entry != null);
  const why = decision ? [...decision.reasons, ...decision.rejected.map((r) => r.reason)] : [];
  const notTrade = Array.from(new Set(lead && lead.entry == null ? [...lead.reasons, ...why] : why));
  return (
    <Panel
      title="Analyst"
      aside={
        lead && (
          <StatusPill tone={SIGNAL_TONE[lead.status]} testId="apex-signal-status">
            {lead.status.replace(/_/g, " ")}
          </StatusPill>
        )
      }
      testId="apex-analyst"
    >
      <div role="tablist" aria-label="Strategy" className="mb-3 flex rounded-lg border border-control/60 p-0.5">
        {STRATEGIES.map((s) => (
          <button
            key={s.id}
            type="button"
            role="tab"
            aria-selected={strategy === s.id}
            onClick={() => onStrategy(s.id)}
            className={cx("h-8 flex-1 rounded-md px-2 text-[12px] font-semibold", strategy === s.id ? "bg-quant/20 text-fg" : "text-fg-muted hover:text-fg")}
            data-testid={`apex-strategy-${s.id}`}
          >
            {s.label}
          </button>
        ))}
      </div>
      {!decision ? (
        <p className="text-[13px] text-fg-muted">This analysis was made by an older engine without per-strategy decisions — analyse again.</p>
      ) : (
        <dl className="flex flex-col gap-2.5 text-[13px]" data-testid="apex-analyst-sections" data-strategy={strategy}>
          <Section title={data.ready ? "Data ready" : "Data blocked"} testId="apex-analyst-data" state={data.ready ? "ready" : "blocked"}>
            <StatusPill tone={data.tone}>{data.text}</StatusPill> <span className="text-fg-muted">{data.reason}</span>
          </Section>
          <Section title="Market structure">
            <span className="text-fg">
              15 min {s15?.trend ?? "—"} · 1 hour {s60?.trend ?? "—"}
            </span>
            {lead && <span className="ml-1 text-fg-faint">({lead.market_structure.toLowerCase()})</span>}
          </Section>
          <Section title="Setup" testId="apex-analyst-setup">
            {decision.setups.length ? (
              decision.setups.map((s) => (
                <span key={`${s.direction}-${s.zone}`} className="block text-fg" data-stage={s.stage}>
                  {s.stage
                    ? `${s.direction}: ${s.stage.replace(/_/g, " ").toLowerCase()} — ${s.reasons?.[0] ?? ""}`
                    : `${s.direction} at ${s.zone} · ${s.distance_atr} ATR away`}
                </span>
              ))
            ) : (
              <span className="text-fg-muted">No zone within reach — no setup.</span>
            )}
            <span className="block text-[12px] text-fg-faint">Rule: {meta?.entry} (version {decision.version}).</span>
          </Section>
          {strategy !== "zone_reaction" && (
            <Section title="Trigger" testId="apex-analyst-trigger" state={decision.trigger ? "confirmed" : "waiting"}>
              {decision.trigger ? (
                <span className="text-fg">
                  {decision.trigger.rule} — bar closed {when(decision.trigger.closed_utc)}
                </span>
              ) : (
                <span className="text-fg-muted">
                  {decision.status === "WAITING_FOR_TRIGGER" ? "Waiting for the setup's confirmed 5-minute trigger — nothing to order yet." : "None."}
                </span>
              )}
            </Section>
          )}
          <Section title="Entry · SL · TP" testId="apex-analyst-plan">
            {plans.length === 0 ? (
              <span className="text-fg-muted">No order plan ({(lead?.status ?? decision.status).replace(/_/g, " ").toLowerCase()}).</span>
            ) : (
              plans.map((p) => (
                <button
                  key={p.signal_id}
                  type="button"
                  onClick={() => p.candidate_id && onSelect(p.candidate_id)}
                  className="block w-full rounded-lg border border-hairline-strong bg-surface-2/40 px-2 py-1.5 text-left hover:border-control"
                  data-testid="apex-analyst-order"
                  data-direction={p.direction}
                >
                  <b className={p.direction === "LONG" ? "text-quant" : "text-danger"}>
                    {p.direction} {p.entry_type.toLowerCase()} {fmt(p.entry)}
                  </b>
                  <span className="ml-2 font-mono text-fg">
                    SL {fmt(p.stop_loss)} · TP1 {fmt(p.take_profit_1)}
                    {p.take_profit_2 != null ? ` · TP2 ${fmt(p.take_profit_2)}` : ""}
                  </span>
                </button>
              ))
            )}
          </Section>
          <Section title="R:R after costs" testId="apex-analyst-rr">
            {plans.length ? (
              <span className="font-mono text-fg">{plans.map((p) => `${p.risk_reward_after_costs} R`).join(" · ")}</span>
            ) : (
              <span className="text-fg-muted">—</span>
            )}
            <span className="block text-[12px] text-fg-faint" data-testid="apex-analyst-execution">
              {executionText(analysis)}
            </span>
          </Section>
          <Section title="Evidence" testId="apex-analyst-evidence" state={lead?.validation_state ?? "UNVALIDATED"}>
            {lead && (
              <>
                <StatusPill tone={VALIDATION_TONE[lead.validation_state]}>{lead.validation_state.replace(/_/g, " ")}</StatusPill>{" "}
                <span className="text-fg-muted">{VALIDATION_TEXT[lead.validation_state]}</span>
              </>
            )}
            <span className="block text-[12px] text-fg-faint">
              A probability appears only from a validated, calibrated P(TP before SL) head of this setup version — otherwise PROBABILITY NOT
              VALIDATED. Zone scores and counted rates are never that probability.{" "}
              <button type="button" onClick={onValidation} className="font-semibold text-quant hover:underline" data-testid="apex-analyst-validate">
                Validation
              </button>
            </span>
          </Section>
          {plans.length > 0 && (
            <Section title="Why this setup" testId="apex-analyst-why">
              {plans.map((p) => (
                <span key={p.signal_id} className="block text-fg">
                  {p.reasons.join(" ")}
                </span>
              ))}
            </Section>
          )}
          <Section title="Why not trade" testId="apex-analyst-why-not">
            {notTrade.length ? (
              <ul className="flex list-disc flex-col gap-0.5 pl-4 text-fg-muted">
                {notTrade.slice(0, 6).map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : (
              <span className="text-fg-muted">
                {lead?.validation_state === "OOS_SUPPORTED" || lead?.validation_state === "FORWARD_SUPPORTED"
                  ? "Nothing blocks the plan — still paper only."
                  : "The rule itself is not validated: treat the plan as a research candidate, not a trade."}
              </span>
            )}
          </Section>
        </dl>
      )}
    </Panel>
  );
}

function Section({ title, children, testId, state }: { title: string; children: React.ReactNode; testId?: string; state?: string }) {
  return (
    <div className="rounded-xl border border-hairline-strong bg-surface-2/40 px-3 py-2" data-testid={testId} data-state={state}>
      <dt className="mb-1 text-[11px] font-black tracking-[0.06em] text-fg-faint uppercase">{title}</dt>
      <dd>{children}</dd>
    </div>
  );
}
