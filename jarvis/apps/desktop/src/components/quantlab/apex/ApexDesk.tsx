import type { ApexAnalysis, ApexMapZone, ApexMarketContext, ApexMarketName, ApexProbabilityModel } from "@jarvis/protocol";
import { useEffect, useState } from "react";

import { api, ApiError } from "../../../lib/api";
import { Icon } from "../../ui/icons";
import { Button, cx } from "../../ui/primitives";
import { EmptyState, Panel, StatusPill, type PillTone } from "../../ui/shell";
import { type Checkpoint, dataAgeMinutes, decide, findZone, MODE_TONE, ZONE_SETUPS } from "./apexView";

/**
 * The APEX workstation pieces around the chart: the eight checkpoints, the mode / source / age
 * line, the decision panel (bias, buy and sell zone, path scenario, the three setups, the gate),
 * the zone evidence drawer and the bottom tabs Market Context, Trade Replay and ULTRON Research.
 * Calm by design: what isn't measured says so; nothing here places an order.
 */

const fmtTime = (t: number | string | null | undefined) =>
  t == null ? "—" : new Date(typeof t === "number" ? t * 1000 : t).toLocaleString();

export function CheckpointStrip({ items }: { items: Checkpoint[] }) {
  return (
    <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8" data-testid="apex-checkpoints" aria-label="Independent checkpoints">
      {items.map((c) => (
        <li
          key={c.key}
          className="flex min-w-0 flex-col gap-1 rounded-xl border border-hairline-strong bg-surface/80 px-3 py-2"
          title={c.detail}
          data-testid={`apex-checkpoint-${c.key}`}
          data-passed={c.passed ? "yes" : "no"}
          data-tone={c.tone}
        >
          <span className="flex items-center gap-1.5 text-[11px] font-semibold text-fg-faint">
            <Icon name={c.passed ? "check" : "x"} size={12} className={c.passed ? "text-success" : "text-fg-faint"} />
            {c.label}
          </span>
          <StatusPill tone={c.tone}>{c.text}</StatusPill>
        </li>
      ))}
    </ol>
  );
}

export function ModeLine({ analysis }: { analysis: ApexAnalysis }) {
  const age = dataAgeMinutes(analysis);
  const cap = analysis.capability;
  const mode = analysis.mode ?? (analysis.data.synthetic ? "SYNTHETIC" : "HISTORICAL");
  const bidOnly = cap?.market_type === "spot_quote" && !cap.has_ask;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-fg-muted" data-testid="apex-mode-line">
      <StatusPill tone={MODE_TONE[mode] ?? "muted"} testId="apex-mode">
        {mode}
      </StatusPill>
      <span>
        as of <b className="text-fg">{fmtTime(analysis.as_of)}</b>
      </span>
      <span data-testid="apex-age">last bar {age == null ? "—" : `${age} min old`}</span>
      <span>
        source <b className="text-fg">{analysis.data.label}</b>
        {cap ? ` · ${cap.market_type.replace("_", " ")} · ${cap.schema}` : ""}
      </span>
      {analysis.zone_map?.zones_as_of_utc && <span>zones built at {fmtTime(analysis.zone_map.zones_as_of_utc)}</span>}
      {bidOnly && (
        <span className="font-semibold text-warning" data-testid="apex-ask-required">
          ASK REQUIRED FOR REALISTIC EXECUTION
        </span>
      )}
      {cap && !cap.live_entitlement && <span>no live entitlement — stored history</span>}
    </div>
  );
}

const VERDICT_TONE: Record<string, string> = {
  LONG: "border-quant/60 bg-quant/10 text-quant",
  SHORT: "border-danger/60 bg-danger/10 text-danger",
  WAIT: "border-warning/50 bg-warning/10 text-warning",
  "NO TRADE": "border-hairline-strong bg-surface-2/50 text-fg",
};

export function DecisionPanel({
  analysis,
  decimals,
  onZone,
  onCandidate,
}: {
  analysis: ApexAnalysis;
  decimals: number;
  onZone: (id: string) => void;
  onCandidate: (id: string) => void;
}) {
  const fmt = (p: number | null | undefined) => (p == null ? "—" : p.toFixed(decimals));
  const d = decide(analysis);
  const zm = analysis.zone_map;
  const paths = analysis.paths;
  const bias = analysis.bias;
  const ctx = analysis.context;
  const plan = d.plan;
  const tsp = plan?.trade_success_probability;
  return (
    <Panel title="APEX decision" testId="apex-decision" aside={<span className="text-[11px] text-fg-faint">paper only · never an order</span>}>
      <div className="flex flex-col gap-3 text-[13px]">
        <div className={cx("rounded-xl border px-3 py-2.5", VERDICT_TONE[d.verdict])} data-testid="apex-verdict-card" data-verdict={d.verdict}>
          <p className="text-[18px] font-black tracking-tight">{d.verdict}</p>
          <p className="text-fg">{d.headline}</p>
          {d.reasons.length > 0 && (
            <ul className="mt-1 list-disc pl-4 text-[12px] text-fg-muted">
              {d.reasons.slice(0, 5).map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </div>

        {plan && (
          <button
            type="button"
            onClick={() => onCandidate(plan.id)}
            className="rounded-xl border border-hairline-strong bg-surface-2/40 px-3 py-2 text-left hover:border-control"
            data-testid="apex-decision-plan"
            data-eligible={plan.eligibility?.eligible ? "yes" : "no"}
          >
            <p className="text-[11px] font-semibold text-fg-faint">
              {plan.eligibility?.eligible ? "Qualified paper plan" : "Research plan — not a signal"} · {plan.setup?.replace(/_/g, " ")}
            </p>
            <p className="font-mono text-fg">
              {plan.direction.toUpperCase()} stop {fmt(plan.entry.price)} · SL {fmt(plan.stop)} · TP1 {fmt(plan.targets[0]?.price)}
              {plan.targets[1] ? ` · TP2 ${fmt(plan.targets[1].price)}` : ""}
            </p>
            <p className="text-[12px] text-fg-muted">
              {plan.targets[0]?.rr} R after costs · risk {plan.risk_usd} USD per {plan.unit} before fees · break-even P{" "}
              {plan.break_even_probability ?? "—"} after 1.5× costs
            </p>
            <p className="text-[12px]" data-testid="apex-decision-probability">
              {tsp?.validated && tsp.value != null ? (
                <span className="text-fg">
                  P(TP before SL) {Math.round(tsp.value * 100)}% [{tsp.uncertainty_lower}–{tsp.uncertainty_upper}], n_eff {tsp.effective_sample_size}
                </span>
              ) : (
                <span className="font-semibold text-fg-muted">PROBABILITY NOT VALIDATED</span>
              )}
            </p>
          </button>
        )}

        {plan?.eligibility && (
          <ul className="grid grid-cols-1 gap-1 text-[12px]" data-testid="apex-gate">
            {plan.eligibility.checks.map((c) => (
              <li key={c.check} className="flex items-start gap-1.5" data-testid="apex-gate-check" data-passed={c.passed ? "yes" : "no"}>
                <Icon name={c.passed ? "check" : "x"} size={12} className={cx("mt-0.5 shrink-0", c.passed ? "text-success" : "text-danger")} />
                <span>
                  <b className="text-fg">{c.check.replace(/_/g, " ").toLowerCase()}</b> <span className="text-fg-muted">— {c.reason}</span>
                </span>
              </li>
            ))}
          </ul>
        )}

        <Row title="Bias (not a signal)" testId="apex-bias">
          {bias ? (
            <>
              <StatusPill tone={bias.state === "BULLISH" ? "success" : bias.state === "BEARISH" ? "danger" : "muted"}>{bias.state}</StatusPill>{" "}
              <span className="text-fg-muted">
                {Object.entries(bias.timeframes)
                  .map(([k, v]) => `${k} ${v.trend}`)
                  .join(" · ")}
              </span>
              {bias.invalidation && <span className="block text-[12px] text-fg-faint">Invalid on {bias.invalidation}.</span>}
            </>
          ) : (
            <span className="text-fg-muted">—</span>
          )}
        </Row>

        <Row title="Buy zone · sell zone" testId="apex-watch-zones">
          {zm ? (
            <div className="flex flex-col gap-1">
              {(
                [
                  ["BUY ZONE", zm.buy_zone, "text-quant"],
                  ["SELL ZONE", zm.sell_zone, "text-danger"],
                ] as const
              ).map(([label, z, color]) => (
                <button
                  key={label}
                  type="button"
                  disabled={!z}
                  onClick={() => z && onZone(z.zone_id)}
                  className="flex items-baseline gap-2 text-left disabled:opacity-60"
                  data-testid={label === "BUY ZONE" ? "apex-buy-zone" : "apex-sell-zone"}
                >
                  <b className={cx("shrink-0 whitespace-nowrap", color)}>{label}</b>
                  {z ? (
                    <span className="font-mono text-fg">
                      {label === "BUY ZONE" ? "S" : "R"}
                      {z.rank} {fmt(z.bounds.low)}–{fmt(z.bounds.high)} · {z.distance_atr} ATR · {z.state} · score {z.quality_score}
                    </span>
                  ) : (
                    <span className="text-fg-muted">none ranked</span>
                  )}
                </button>
              ))}
              <span className="text-[11px] text-fg-faint">The zone score (0–100) is evidence strength, never a probability.</span>
            </div>
          ) : (
            <span className="text-fg-muted">No zone map in this analysis.</span>
          )}
        </Row>

        <Row title="Path scenario" testId="apex-path">
          {paths?.available && paths.shares && paths.barriers ? (
            <>
              <span className="text-fg">
                within {paths.horizon_minutes} min: upper {fmt(paths.barriers.upper)} first {Math.round(paths.shares.UPPER_FIRST * 100)}% · lower{" "}
                {fmt(paths.barriers.lower)} first {Math.round(paths.shares.LOWER_FIRST * 100)}% · neither{" "}
                {Math.round(paths.shares.NEITHER * 100)}%
              </span>
              <span className="block text-[12px] text-fg-faint">
                {paths.status.replace(/_/g, " ")} · {paths.effective_samples} effective samples — counted, not calibrated; never a trade&apos;s chance.
              </span>
            </>
          ) : (
            <span className="text-fg-muted">{paths?.reason ?? paths?.note ?? "Not enough history for the path scenario."}</span>
          )}
        </Row>

        <Row title="Setups" testId="apex-setups">
          <ul className="flex flex-col gap-1">
            {ZONE_SETUPS.map((s) => {
              const dec = analysis.strategies?.[s.id];
              return (
                <li key={s.id} className="flex flex-col" data-testid="apex-setup" data-setup={s.id} data-state={dec?.setup_state ?? "—"}>
                  <span>
                    <b className="text-fg">{s.label}</b>{" "}
                    <StatusPill tone={STATE_TONE[dec?.setup_state ?? ""] ?? "muted"}>{(dec?.setup_state ?? "—").replace(/_/g, " ")}</StatusPill>
                  </span>
                  {dec?.setups.map((x) => (
                    <span key={x.direction} className="text-[12px] text-fg-muted">
                      {x.direction}: {(x.stage ?? "").replace(/_/g, " ").toLowerCase()} — {x.reasons?.[0] ?? ""}
                    </span>
                  ))}
                </li>
              );
            })}
          </ul>
        </Row>

        <Row title="Event risk · feed" testId="apex-risk">
          <span className="block">
            Macro: <StatusPill tone={ctx?.macro.status === "CONFIRMED" ? "success" : ctx?.macro.status === "CONTRADICTED" ? "danger" : "muted"}>{ctx?.macro.status ?? "—"}</StatusPill>{" "}
            <span className="text-fg-muted">{ctx?.macro.reason}</span>
          </span>
          {analysis.auction && (
            <span className="block text-[12px] text-fg-muted">
              VWAP {analysis.auction.vwap.status === "UNAVAILABLE" ? "unavailable" : `${fmt(analysis.auction.vwap.value)} (${analysis.auction.vwap.status})`} · delta{" "}
              {analysis.auction.delta.status.toLowerCase()} · {analysis.auction.absorption.label}
            </span>
          )}
        </Row>
      </div>
    </Panel>
  );
}

const STATE_TONE: Record<string, PillTone> = {
  ENTRY_ARMED: "success",
  RESEARCH_ONLY: "info",
  TRIGGER_PENDING: "warning",
  WATCH: "warning",
  NO_TRADE: "muted",
  BLOCKED: "danger",
};

function Row({ title, testId, children }: { title: string; testId?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-hairline-strong bg-surface-2/30 px-3 py-2" data-testid={testId}>
      <h4 className="mb-1 text-[11px] font-semibold text-fg-faint">{title}</h4>
      {children}
    </section>
  );
}

const EVIDENCE_TONE: Record<string, PillTone> = {
  CONFIRMED: "success",
  CONTRADICTED: "danger",
  PROXY: "warning",
  STALE: "warning",
  UNAVAILABLE: "muted",
  NOT_APPLICABLE: "muted",
};

/** Why a zone is on the map: members, evidence per factor (with status), episodes, lifecycle. */
export function ZoneDrawer({ analysis, zoneId, decimals, onClose }: { analysis: ApexAnalysis; zoneId: string; decimals: number; onClose: () => void }) {
  const z: ApexMapZone | null = findZone(analysis, zoneId);
  if (!z) return null;
  const name = `${z.side === "SUPPORT" ? "S" : "R"}${z.rank ?? ""}`;
  return (
    <Panel
      title={`Why ${name} ${z.bounds.low.toFixed(decimals)}–${z.bounds.high.toFixed(decimals)}`}
      testId="apex-zone-drawer"
      aside={
        <Button onClick={onClose} data-testid="apex-zone-close">
          <Icon name="x" size={13} /> Close
        </Button>
      }
    >
      <div className="grid grid-cols-1 gap-3 text-[13px] lg:grid-cols-2">
        <div>
          <p className="text-fg">
            {z.state} · score {z.quality_score}/100 (not a probability) · {z.touches} touch episode(s), {z.rejections} rejection(s) · origin{" "}
            {z.timeframe_of_origin}
          </p>
          <p className="text-[12px] text-fg-muted">
            formed {fmtTime(z.formation_time_utc)} · confirmed {fmtTime(z.confirmed_time_utc)} · source {z.source_ids.join(", ")}
          </p>
          <p className="mt-1 text-[12px] text-fg-muted">Members: {Array.from(new Set(z.labels)).join(", ")}</p>
          {z.counterevidence.length > 0 && <p className="mt-1 text-[12px] text-danger">Against it: {z.counterevidence.join("; ")}</p>}
        </div>
        <ul className="flex flex-col gap-1" data-testid="apex-zone-evidence">
          {z.evidence.map((e) => (
            <li key={e.factor} className="flex items-start gap-2" data-testid="apex-zone-factor" data-status={e.status}>
              <StatusPill tone={EVIDENCE_TONE[e.status] ?? "muted"}>{e.status}</StatusPill>
              <span>
                <b className="text-fg">{e.factor.replace(/_/g, " ")}</b>
                {e.points != null ? ` ${e.factor === "score_components" ? "=" : "+"}${e.points}` : ""}{" "}
                <span className="text-fg-muted">
                  {typeof e.detail === "string"
                    ? e.detail
                    : Object.entries(e.detail ?? {})
                        .map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`)
                        .join(" · ")}
                </span>
              </span>
            </li>
          ))}
        </ul>
        <div className="lg:col-span-2">
          <p className="text-[11px] font-semibold text-fg-faint">Episodes</p>
          <ul className="flex flex-wrap gap-1.5 text-[12px]">
            {z.episodes.length === 0 && <li className="text-fg-muted">no touch yet</li>}
            {z.episodes.map((e) => (
              <li key={`${e.start}-${e.kind}`} className="rounded-md border border-hairline-strong px-2 py-0.5" data-testid="apex-zone-episode">
                {e.kind.replace(/_/g, " ")} · {fmtTime(e.start)}
                {e.reaction_atr != null ? ` · ${e.reaction_atr} ATR` : ""}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] font-semibold text-fg-faint">Lifecycle</p>
          <ol className="flex flex-wrap gap-1.5 text-[12px] text-fg-muted">
            {z.transitions.map((t) => (
              <li key={`${t.state}-${t.at}`}>
                {t.state} ({fmtTime(t.at)}: {t.reason})
              </li>
            ))}
          </ol>
        </div>
      </div>
    </Panel>
  );
}

const SAMPLE = `event,scheduled,timezone,importance,category,actual,released
US CPI m/m,2026-03-12 08:30,America/New_York,high,inflation,0.3%,2026-03-12 08:30`;

/** Macro events known at the moment, the blackout, related markets, and the calendar import. */
export function MarketContextTab({ market, analysis }: { market: ApexMarketName; analysis: ApexAnalysis | null }) {
  const [ctx, setCtx] = useState<ApexMarketContext | null>(analysis?.context ?? null);
  const [csv, setCsv] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const asOf = analysis?.as_of;
  useEffect(() => {
    let live = true;
    api
      .apexContext(market, asOf)
      .then((c) => live && setCtx(c))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [market, asOf]);
  const load = async () => {
    try {
      const out = await api.apexMacroImport(csv, "owner import");
      setNote(`${out.added} new of ${out.parsed} event(s)${out.refused.length ? `; refused: ${out.refused.map((r) => `line ${r.line} (${r.reason})`).join(", ")}` : ""}.`);
      setCtx(await api.apexContext(market, asOf));
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : "The import didn't work.");
    }
  };
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2" data-testid="apex-context">
      <Panel title="Macro and related markets — as known at the moment">
        {!ctx ? (
          <p className="text-[13px] text-fg-muted">Loading…</p>
        ) : (
          <div className="flex flex-col gap-2 text-[13px]">
            <p>
              <StatusPill tone={ctx.macro.status === "CONFIRMED" ? "success" : ctx.macro.status === "CONTRADICTED" ? "danger" : "muted"} testId="apex-macro-status">
                {ctx.macro.status}
              </StatusPill>{" "}
              <span className="text-fg-muted">{ctx.macro.reason}</span>
            </p>
            <p className="text-[12px] text-fg-muted">
              Blackout {ctx.blackout.before_minutes} min before / {ctx.blackout.after_minutes} min after a high-impact US release
              {ctx.blackout.active ? " — ACTIVE now" : ""}.
            </p>
            <ul className="flex flex-col gap-1" data-testid="apex-macro-events">
              {ctx.events.map((e) => (
                <li key={`${e.event}-${e.scheduled_utc}`} data-testid="apex-macro-event">
                  <b className="text-fg">{e.event}</b> · {e.importance} · {fmtTime(e.scheduled_utc)} ({e.minutes_away} min) ·{" "}
                  {e.actual != null ? `actual ${e.actual}` : "no value yet"}
                </li>
              ))}
              {ctx.events.length === 0 && <li className="text-fg-muted">No event in the window.</li>}
            </ul>
            <ul className="mt-1 flex flex-col gap-0.5 text-[12px]" data-testid="apex-related">
              {ctx.related_markets.map((m) => (
                <li key={m.market}>
                  <StatusPill tone="muted">{m.status}</StatusPill> {m.market} — <span className="text-fg-muted">{m.reason}</span>
                </li>
              ))}
            </ul>
            <p className="text-[12px] text-fg-faint">{ctx.unscheduled_risk}</p>
          </div>
        )}
      </Panel>
      <Panel title="Import a macro calendar (CSV) — nothing is fetched online">
        <p className="mb-2 text-[12px] text-fg-muted">
          Columns: event, scheduled (with offset, or local time plus a timezone column), importance, category, actual, released, schedule_known.
          An actual value is visible to an analysis only after its release time.
        </p>
        <textarea
          value={csv}
          onChange={(e) => setCsv(e.target.value)}
          placeholder={SAMPLE}
          rows={6}
          className="w-full rounded-lg border border-control/60 bg-canvas-2 p-2 font-mono text-[12px] text-fg"
          data-testid="apex-macro-csv"
        />
        <div className="mt-2 flex items-center gap-2">
          <Button onClick={() => void load()} disabled={csv.trim().length < 10} reason={csv.trim().length < 10 ? "Paste a CSV first." : null} data-testid="apex-macro-import">
            Import
          </Button>
          {note && (
            <span className="text-[12px] text-fg-muted" role="status" data-testid="apex-macro-note">
              {note}
            </span>
          )}
        </div>
      </Panel>
    </div>
  );
}

/** Stored analyses are immutable snapshots: reopen one exactly as it was, or replay a moment. */
export function TradeReplay({ market, onOpen }: { market: ApexMarketName; onOpen: (a: ApexAnalysis) => void }) {
  const [rows, setRows] = useState<{ id: string; as_of: number; synthetic: boolean }[]>([]);
  const [at, setAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api
      .apexAnalyses(market)
      .then((r) => live && setRows(r.slice(0, 30)))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [market]);
  const open = async (id: string) => {
    try {
      onOpen(await api.apexAnalysis(id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't open it.");
    }
  };
  const replay = async () => {
    const moment = Math.floor(new Date(at).getTime() / 1000);
    if (!Number.isFinite(moment)) return;
    setBusy(true);
    setError(null);
    try {
      onOpen(await api.apexAnalyze(market, { asOf: moment }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The replay didn't work.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel title="Trade replay — past moments, labelled REPLAY" testId="apex-replay">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[13px]">
        <label className="flex items-center gap-2">
          Replay the moment
          <input
            type="datetime-local"
            value={at}
            onChange={(e) => setAt(e.target.value)}
            className="rounded-lg border border-control/60 bg-canvas-2 px-2 py-1 text-fg"
            data-testid="apex-replay-at"
          />
        </label>
        <Button onClick={() => void replay()} disabled={!at || busy} reason={!at ? "Pick a moment." : busy ? "Replaying…" : null} data-testid="apex-replay-run">
          Replay
        </Button>
        <span className="text-[12px] text-fg-faint">Only bars closed by then are used; a replay never sends an alert.</span>
      </div>
      {error && <p className="mb-2 text-[13px] text-danger">{error}</p>}
      {rows.length === 0 ? (
        <EmptyState icon="target" title="No stored analysis yet">
          Every analysis is stored once and never changed; it appears here.
        </EmptyState>
      ) : (
        <ul className="flex flex-col gap-1 text-[13px]">
          {rows.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-2 border-t border-hairline-strong/60 py-1" data-testid="apex-replay-row">
              <span>
                {fmtTime(r.as_of)} {r.synthetic && <StatusPill tone="warning">test data</StatusPill>} <span className="font-mono text-[11px] text-fg-faint">{r.id}</span>
              </span>
              <Button onClick={() => void open(r.id)} data-testid="apex-replay-open">
                Open snapshot
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** What ULTRON's research produced for this market: probability heads with their grading. */
export function UltronResearch({ market, children }: { market: ApexMarketName; children?: React.ReactNode }) {
  const [models, setModels] = useState<ApexProbabilityModel[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    api
      .apexProbability(market)
      .then((m) => live && setModels(m))
      .catch(() => live && setModels([]));
    return () => {
      live = false;
    };
  }, [market, tick]);
  const act = async (what: () => Promise<{ state: string }>) => {
    try {
      const out = await what();
      setNote(`Now ${out.state}.`);
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : "That didn't work.");
    }
    setTick((t) => t + 1);
  };
  return (
    <div className="flex flex-col gap-4" data-testid="apex-ultron">
      <Panel title="P(TP before SL) heads — fitted in setup validations">
        <p className="mb-2 text-[12px] text-fg-muted">
          CIPHER proposes, SENTINEL grades: each head is fitted, calibrated and tested once inside the development window (protocol 1.2.0) and stored
          immutably — failures included. Only a VALIDATED head on real data can qualify a plan (≥ 0.70).
        </p>
        {models == null ? (
          <p className="text-[13px] text-fg-muted">Loading…</p>
        ) : models.length === 0 ? (
          <p className="text-[13px] text-fg-muted" data-testid="apex-heads-empty">
            No head yet — run a validation of sweep_reclaim, rejection_rotation or breakout_retest (APEX → Validation).
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5 text-[13px]">
            {models.map((m) => (
              <li key={m.id} className="rounded-lg border border-hairline-strong px-3 py-1.5" data-testid="apex-head" data-verdict={m.verdict.code}>
                <b className="text-fg">{m.setup}</b> {m.version} · {m.source_id}
                {m.synthetic && <StatusPill tone="warning">test data</StatusPill>}{" "}
                <StatusPill tone={m.validated ? "success" : "muted"}>{m.verdict.code.replace(/_/g, " ")}</StatusPill>
                <span className="block text-[12px] text-fg-muted">
                  {m.verdict.text} Trades fit / calibration / test: {m.counts.fit ?? 0} / {m.counts.calibration ?? 0} / {m.counts.test ?? 0}
                  {m.metrics ? ` · Brier ${m.metrics.brier} vs base ${m.metrics.brier_base_rate} · slope ${m.metrics.calibration_slope}` : ""}
                </span>
                <span className="mt-1 flex flex-wrap items-center gap-2 text-[12px]" data-testid="apex-head-promotion" data-state={m.promotion ?? "NONE"}>
                  Promotion: <b className="text-fg">{m.promotion ?? "NONE"}</b>
                  {(m.promotion_events ?? [])
                    .filter((e) => e.review)
                    .slice(-1)
                    .flatMap((e) => e.review?.checks.filter((c) => !c.passed) ?? [])
                    .map((c) => (
                      <span key={c.check} className="text-danger">
                        {c.check}: {c.reason}
                      </span>
                    ))}
                  {(m.promotion ?? "NONE") !== "APPROVED" && m.promotion !== "REVIEWED" && (
                    <Button onClick={() => void act(() => api.apexPropose(m.id))} data-testid="apex-head-propose">
                      Ask SENTINEL to review
                    </Button>
                  )}
                  {m.promotion === "REVIEWED" && (
                    <Button
                      variant="primary"
                      onClick={() => {
                        if (window.confirm("Approve this head? Its probabilities may then qualify paper signals (never orders).")) void act(() => api.apexApprove(m.id));
                      }}
                      data-testid="apex-head-approve"
                    >
                      Approve (owner)
                    </Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
        {note && (
          <p className="mt-2 text-[12px] text-fg-muted" role="status" data-testid="apex-head-note">
            {note}
          </p>
        )}
      </Panel>
      {children}
    </div>
  );
}
