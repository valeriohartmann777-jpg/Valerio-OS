import type {
  ApexAnalysis,
  ApexAudit,
  ApexCandidate,
  ApexEvaluation,
  ApexJournal,
  ApexMarket,
  ApexMarketName,
  ApexRate,
  ApexSummary,
} from "@jarvis/protocol";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../../../lib/api";
import { Icon } from "../../ui/icons";
import { Button, cx } from "../../ui/primitives";
import { EmptyState, LoadingState, Panel, StatusPill, type PillTone } from "../../ui/shell";
import { ApexChart } from "./ApexChart";

/**
 * JARVIS APEX in QuantLab: market structure, support/resistance zones, counted scenario rates
 * and entry / stop / target candidates — each candidate journaled and judged on later bars.
 * Research only: nothing here places an order.
 */

const MARKETS: ApexMarketName[] = ["XAUUSD", "NQ", "MNQ"];
const QUALITY_TONE: Record<string, PillTone> = { OK: "success", WARN: "warning", BLOCK: "danger" };
const STATE_TONE: Record<string, PillTone> = {
  win: "success",
  loss: "danger",
  timeout: "warning",
  expired: "muted",
  missed: "muted",
  pending: "info",
  open: "info",
};

function savedMarket(): ApexMarketName {
  try {
    const value = localStorage.getItem("apex.market");
    return MARKETS.includes(value as ApexMarketName) ? (value as ApexMarketName) : "XAUUSD";
  } catch {
    return "XAUUSD";
  }
}

const when = (epoch: number) => new Date(epoch * 1000).toLocaleString();
const pct = (p: number) => `${(p * 100).toFixed(1)}%`;

export function ApexPanel() {
  const [markets, setMarkets] = useState<ApexMarket[] | null>(null);
  const [market, setMarketState] = useState<ApexMarketName>(savedMarket);
  const [analysis, setAnalysis] = useState<ApexAnalysis | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; suggestion?: string | null } | null>(null);
  const [journal, setJournal] = useState<ApexJournal | null>(null);
  const [evaluations, setEvaluations] = useState<ApexEvaluation[]>([]);

  const setMarket = (next: ApexMarketName) => {
    setMarketState(next);
    setAnalysis(null);
    setSelected(null);
    setError(null);
    try {
      localStorage.setItem("apex.market", next);
    } catch {
      /* per-device convenience only */
    }
  };

  const loadSide = useCallback(async () => {
    const [m, j, e] = await Promise.all([api.apexMarkets(), api.apexJournal(market), api.apexEvaluations(market)]);
    setMarkets(m);
    setJournal(j);
    setEvaluations(e);
  }, [market]);

  useEffect(() => {
    loadSide().catch(() => setError({ message: "The backend isn't reachable." }));
  }, [loadSide]);

  const running = evaluations.find((e) => e.state === "RUNNING");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void loadSide(), 1000);
    return () => clearInterval(timer);
  }, [running, loadSide]);

  const info = markets?.find((m) => m.market === market);
  const fixture = info?.fixture ?? false;
  const decimals = 2; // gold quotes in cents, NQ/MNQ in quarter points

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await api.apexAnalyze(market, { refresh: refresh && !fixture });
      setAnalysis(out);
      setSelected(out.candidates[0]?.id ?? null);
      await loadSide();
    } catch (err) {
      setError(err instanceof ApiError ? { message: err.message, suggestion: err.suggestion } : { message: "That didn't work." });
    } finally {
      setBusy(false);
    }
  };

  const candidate = useMemo(() => analysis?.candidates.find((c) => c.id === selected) ?? null, [analysis, selected]);

  return (
    <div className="flex flex-col gap-5" data-testid="apex-panel">
      <Panel
        title="APEX · structure, zones, candidates"
        aside={
          <span className="flex items-center gap-2 text-[12px] text-fg-muted">
            <Icon name="lock" size={13} /> Research only — no orders
          </span>
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          <div role="group" aria-label="Market" className="flex gap-1.5">
            {MARKETS.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMarket(m)}
                aria-pressed={market === m}
                className={cx(
                  "h-9 rounded-lg border px-3.5 text-[13px] font-bold",
                  market === m ? "border-quant bg-quant/15 text-fg" : "border-control/70 text-fg-muted hover:text-fg",
                )}
                data-testid={`apex-market-${m}`}
              >
                {m}
              </button>
            ))}
          </div>
          <Button variant="primary" size="lg" onClick={() => void run()} disabled={busy} reason={busy ? "Analysing…" : null} data-testid="apex-analyze">
            <Icon name="target" size={15} /> {busy ? "Analysing…" : "Analyse now"}
          </Button>
          {!fixture && (
            <label className="flex items-center gap-2 text-[13px] text-fg-muted">
              <input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} data-testid="apex-refresh" />
              Update free data first (Dukascopy; the Data Vault is never charged)
            </label>
          )}
        </div>
        <p className="mt-3 max-w-[860px] text-[13px] leading-relaxed text-fg-muted">
          {info?.label ?? market}. APEX reads only bars that had closed at the moment of analysis. Every probability is a
          counted rate from earlier data with its sample size and 95% interval; candidates follow fixed rules and are
          written to a journal that later bars judge — wins and losses alike.
        </p>
        {error && (
          <div className="mt-3 rounded-xl border border-danger/40 bg-danger/10 p-3 text-[13px]" role="alert" data-testid="apex-error">
            <p className="font-semibold text-danger">{error.message}</p>
            {error.suggestion && <p className="mt-1 text-fg-muted">{error.suggestion}</p>}
          </div>
        )}
      </Panel>

      {!analysis && !error && (
        <EmptyState icon="target" title={`No ${market} analysis yet`}>
          Press “Analyse now”. It takes a second on local data.
        </EmptyState>
      )}

      {analysis && <Analysis analysis={analysis} candidate={candidate} onSelect={setSelected} decimals={decimals} />}

      <Journal journal={journal} market={market} onResolved={() => void loadSide()} decimals={decimals} />
      <TrackRecord evaluations={evaluations} market={market} onStarted={() => void loadSide()} />
    </div>
  );
}

function DataBanner({ analysis }: { analysis: ApexAnalysis }) {
  const q = analysis.quality;
  return (
    <div className="flex flex-col gap-2" data-testid="apex-data">
      {analysis.data.synthetic && (
        <p className="rounded-xl border border-warning/50 bg-warning/10 px-3 py-2 text-[13px] font-bold text-warning" data-testid="apex-synthetic">
          SYNTHETIC TEST DATA — generated prices, not the market. Nothing below says anything about real {analysis.market}.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2 text-[13px] text-fg-muted">
        <StatusPill tone={QUALITY_TONE[q.status] ?? "muted"} testId="apex-quality">{`Data ${q.status}`}</StatusPill>
        <span>{analysis.data.label}</span>
        <span className="text-fg-faint">· as of {when(analysis.as_of)}{analysis.replay ? " (replay)" : ""}</span>
        <span className="font-mono text-[12px] text-fg-faint" title="SHA-256 of the bars this analysis saw">
          · {analysis.data.sha256.slice(0, 12)}
        </span>
      </div>
      {analysis.data.proxy && <p className="text-[13px] text-warning">{analysis.data.proxy}</p>}
      {q.findings.filter((f) => f.level !== "INFO").length > 0 && (
        <ul className="list-disc pl-5 text-[12px] text-fg-muted">
          {q.findings
            .filter((f) => f.level !== "INFO")
            .map((f) => (
              <li key={f.code}>
                <b className={f.level === "BLOCK" ? "text-danger" : "text-warning"}>{f.code}</b> {f.message}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}

function Analysis({
  analysis,
  candidate,
  onSelect,
  decimals,
}: {
  analysis: ApexAnalysis;
  candidate: ApexCandidate | null;
  onSelect: (id: string) => void;
  decimals: number;
}) {
  const fmt = (p: number) => p.toFixed(decimals);
  if (analysis.blocked) {
    return (
      <Panel title="Analysis blocked">
        <DataBanner analysis={analysis} />
      </Panel>
    );
  }
  return (
    <>
      <Panel title={`${analysis.market} · ${analysis.price != null ? fmt(analysis.price) : "—"}`} aside={<span className="text-[12px] text-fg-muted">ATR(15m) {analysis.atr15?.toFixed(decimals)}</span>}>
        <DataBanner analysis={analysis} />
        <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,2.2fr)_minmax(0,1fr)]">
          <ApexChart bars={analysis.chart} zones={analysis.zones} candidate={candidate} price={analysis.price ?? null} decimals={decimals} />
          <div className="flex flex-col gap-3" data-testid="apex-structure">
            {Object.entries(analysis.structure).map(([minutes, s]) => (
              <div key={minutes} className="rounded-xl border border-hairline-strong bg-surface-2/40 p-3 text-[13px]">
                <div className="flex items-center justify-between">
                  <b className="text-fg">{minutes === "60" ? "1 hour" : `${minutes} minutes`}</b>
                  <StatusPill tone={s.trend === "up" ? "success" : s.trend === "down" ? "danger" : "muted"} testId={`apex-trend-${minutes}`}>
                    {s.trend}
                  </StatusPill>
                </div>
                <p className="mt-1.5 text-fg-muted">
                  Last high {s.last_high ? `${fmt(s.last_high.price)} ${s.last_high.label}` : "—"} · last low{" "}
                  {s.last_low ? `${fmt(s.last_low.price)} ${s.last_low.label}` : "—"}
                </p>
                {s.events.length > 0 && (
                  <p className="mt-1 text-fg-faint">
                    {(() => {
                      const e = s.events[s.events.length - 1];
                      return e ? `${e.type} ${e.direction} through ${fmt(e.level)} · ${when(e.t)}` : "";
                    })()}
                  </p>
                )}
              </div>
            ))}
            <p className="text-[12px] leading-relaxed text-fg-faint">
              Swings count only once closed bars on both sides confirm them. HH/HL = up, LH/LL = down; BOS = break with the trend,
              CHoCH = break against it.
            </p>
          </div>
        </div>
      </Panel>

      <SentinelCheck analysis={analysis} />

      <Panel title="Zones" aside={<span className="text-[12px] text-fg-muted">hold rate = counted, before this moment</span>}>
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[12px] text-fg-faint">
                <th className="pb-2 font-semibold">Zone</th>
                <th className="pb-2 font-semibold">Range</th>
                <th className="pb-2 font-semibold">Why</th>
                <th className="pb-2 text-right font-semibold">Tests</th>
                <th className="pb-2 text-right font-semibold">Away</th>
                <th className="pb-2 text-right font-semibold">Held on touch</th>
              </tr>
            </thead>
            <tbody>
              {analysis.zones.map((z) => (
                <tr key={z.id} className="border-t border-hairline-strong/60" data-testid="apex-zone" data-side={z.side}>
                  <td className={cx("py-1.5 font-bold", z.side === "support" ? "text-quant" : "text-danger")}>
                    {z.id}
                    {z.inside && <span className="ml-1 text-[11px] text-warning">price inside</span>}
                  </td>
                  <td className="py-1.5 font-mono text-fg">
                    {fmt(z.low)} – {fmt(z.high)}
                  </td>
                  <td className="py-1.5 text-fg-muted">{z.reasons.join(", ")}</td>
                  <td className="py-1.5 text-right font-mono text-fg-muted">{z.tests}</td>
                  <td className="py-1.5 text-right font-mono text-fg-muted">{z.inside ? "0" : `${z.distance_atr.toFixed(1)} ATR`}</td>
                  <td className="py-1.5 text-right">
                    <Rate rate={z.rate} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Scenarios" aside={<span className="text-[12px] text-fg-muted">{analysis.base_rates?.touches ?? 0} decided touches before this moment</span>}>
        <ul className="flex flex-col gap-2">
          {analysis.scenarios.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-hairline-strong bg-surface-2/40 px-3 py-2 text-[13px]" data-testid="apex-scenario">
              <span className="text-fg">{s.statement}</span>
              <Rate rate={s.rate} />
            </li>
          ))}
        </ul>
        {analysis.base_rates && <p className="mt-3 text-[12px] leading-relaxed text-fg-faint">{analysis.base_rates.definition}</p>}
      </Panel>

      <Panel title="Trade candidates" aside={<span className="text-[12px] text-fg-muted">rules, not predictions</span>}>
        {analysis.candidates.length === 0 && <p className="text-[13px] text-fg-muted">No candidate at this moment.</p>}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {analysis.candidates.map((c) => (
            <CandidateCard key={c.id} c={c} selected={candidate?.id === c.id} onSelect={() => onSelect(c.id)} decimals={decimals} />
          ))}
        </div>
        {analysis.rejected.length > 0 && (
          <ul className="mt-3 flex flex-col gap-1 text-[12px] text-fg-muted">
            {analysis.rejected.map((r, k) => (
              <li key={k} data-testid="apex-rejected">
                <b className="text-fg-faint">{r.direction ? `${r.direction} at ${r.zone}` : "No candidate"}:</b> {r.reason}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}

/** SENTINEL re-computes the stored analysis independently and re-judges its candidates. */
function SentinelCheck({ analysis }: { analysis: ApexAnalysis }) {
  const [audit, setAudit] = useState<ApexAudit | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setAudit(null);
    setError(null);
  }, [analysis.id]);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setAudit(await api.apexAudit(analysis.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel
      title="SENTINEL check"
      aside={
        <Button onClick={() => void run()} disabled={busy} reason={busy ? "Checking…" : null} data-testid="apex-audit">
          <Icon name="check" size={14} /> Ask SENTINEL to re-check
        </Button>
      }
      testId="apex-audit-panel"
    >
      <p className="text-[13px] text-fg-muted">
        SENTINEL recomputes this analysis from the data now on disk — which by now may include later bars — and must get
        exactly the same zones, rates and candidates (proof that nothing looked ahead). It also re-judges the journaled
        candidates on the latest bars.
      </p>
      {error && <p className="mt-2 text-[13px] text-danger">{error}</p>}
      {audit?.result && (
        <div className="mt-3" data-testid="apex-audit-result" data-passed={audit.passed ? "yes" : "no"}>
          <StatusPill tone={audit.passed ? "success" : "danger"}>{audit.passed ? "All checks passed" : "Check failed"}</StatusPill>
          <ul className="mt-2 flex flex-col gap-1 text-[13px]">
            {audit.result.checks.map((c) => (
              <li key={c.code} className="flex gap-2" data-testid="apex-audit-check" data-passed={c.passed ? "yes" : "no"}>
                <Icon name={c.passed ? "check" : "x"} size={14} className={c.passed ? "mt-0.5 text-success" : "mt-0.5 text-danger"} />
                <span>
                  <b className="text-fg">{c.code}</b> <span className="text-fg-muted">{c.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

function CandidateCard({ c, selected, onSelect, decimals }: { c: ApexCandidate; selected: boolean; onSelect: () => void; decimals: number }) {
  const fmt = (p: number) => p.toFixed(decimals);
  const long = c.direction === "long";
  const track = c.track_record;
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cx("rounded-xl border p-3 text-left text-[13px]", selected ? "border-cta bg-cta/5" : "border-hairline-strong bg-surface-2/40 hover:border-control")}
      data-testid="apex-candidate"
      data-direction={c.direction}
    >
      <div className="flex items-center justify-between gap-2">
        <b className={long ? "text-quant" : "text-danger"}>
          {long ? "LONG" : "SHORT"} at {c.zone} · limit {fmt(c.entry.price)}
        </b>
        <span className="text-[12px] text-fg-faint">{c.with_trend == null ? "1h range" : c.with_trend ? "with 1h trend" : "against 1h trend"}</span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
        <dt className="text-fg-faint">Stop</dt>
        <dd className="font-mono text-fg">{fmt(c.stop)}</dd>
        {c.targets.map((t, k) => (
          <Fragment2 key={t.price} label={`Target ${k + 1}`} value={`${fmt(t.price)} · ${t.rr}R`} />
        ))}
        <dt className="text-fg-faint">Risk</dt>
        <dd className="font-mono text-fg">
          {c.risk_points} pts · ${c.risk_usd.toLocaleString()} per {c.unit}
        </dd>
        <dt className="text-fg-faint">Valid until</dt>
        <dd className="text-fg">{when(c.valid_until)}</dd>
      </dl>
      <p className="mt-2 text-[12px] text-fg-muted">
        Zone held on touch: <Rate rate={c.zone_rate ?? null} inline />
      </p>
      <p className="mt-1 text-[12px] text-fg-muted" data-testid="apex-candidate-track">
        {track
          ? `Rule track record ${track.period[0]} → ${track.period[1]}${track.synthetic ? " (synthetic data)" : ""}: ${track.verdict?.text ?? ""}`
          : "No track record for this rule yet — run one below before trusting it."}
      </p>
    </button>
  );
}

function Fragment2({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-fg-faint">{label}</dt>
      <dd className="font-mono text-fg">{value}</dd>
    </>
  );
}

function Rate({ rate, inline }: { rate: ApexRate | null; inline?: boolean }) {
  if (!rate) return <span className="text-fg-faint">—</span>;
  if (rate.status !== "ok" || rate.p == null) {
    return (
      <span className="text-fg-faint" title={`${rate.n} cases`}>
        insufficient data ({rate.n})
      </span>
    );
  }
  const text = `${pct(rate.p)} (${rate.ci ? `${pct(rate.ci[0])}–${pct(rate.ci[1])}` : "—"}, n=${rate.n}, ${rate.days} days)`;
  return inline ? <span className="text-fg">{text}</span> : <span className="font-mono text-[12px] text-fg">{text}</span>;
}

function Journal({ journal, market, onResolved, decimals }: { journal: ApexJournal | null; market: string; onResolved: () => void; decimals: number }) {
  const [busy, setBusy] = useState(false);
  if (!journal) return <LoadingState>Loading the journal…</LoadingState>;
  const recheck = async () => {
    setBusy(true);
    try {
      await api.apexResolve(market);
      onResolved();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel
      title={`Candidate journal · ${market}`}
      aside={
        <Button onClick={() => void recheck()} disabled={busy} reason={busy ? "Checking…" : null} data-testid="apex-resolve">
          Re-check outcomes
        </Button>
      }
      testId="apex-journal"
    >
      <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <SummaryLine title="Real data" s={journal.stats.real} />
        <SummaryLine title="Synthetic test data" s={journal.stats.synthetic} />
      </div>
      {journal.candidates.length === 0 ? (
        <p className="text-[13px] text-fg-muted">Nothing journaled yet — every analysis adds its candidates here, unchanged.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[12px] text-fg-faint">
                <th className="pb-2 font-semibold">Made</th>
                <th className="pb-2 font-semibold">Side</th>
                <th className="pb-2 font-semibold">Entry / stop / target</th>
                <th className="pb-2 font-semibold">Outcome</th>
                <th className="pb-2 text-right font-semibold">R</th>
              </tr>
            </thead>
            <tbody>
              {journal.candidates.slice(0, 40).map((c) => (
                <tr key={c.id} className="border-t border-hairline-strong/60" data-testid="apex-journal-row" data-state={c.outcome?.state ?? "pending"}>
                  <td className="py-1.5 text-fg-muted">
                    {when(c.created_at)}
                    {c.synthetic && <span className="ml-1 text-[11px] text-warning">synthetic</span>}
                    {c.replay && <span className="ml-1 text-[11px] text-fg-faint">replay</span>}
                  </td>
                  <td className={cx("py-1.5 font-bold", c.direction === "long" ? "text-quant" : "text-danger")}>{c.direction}</td>
                  <td className="py-1.5 font-mono text-fg">
                    {c.entry.price.toFixed(decimals)} / {c.stop.toFixed(decimals)} / {c.targets[0]?.price.toFixed(decimals)}
                  </td>
                  <td className="py-1.5">
                    <StatusPill tone={STATE_TONE[c.outcome?.state ?? "pending"] ?? "muted"}>{c.outcome?.state ?? "pending"}</StatusPill>
                  </td>
                  <td className="py-1.5 text-right font-mono text-fg">{c.outcome?.r != null ? c.outcome.r.toFixed(2) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function SummaryLine({ title, s }: { title: string; s: ApexSummary }) {
  return (
    <div className="rounded-xl border border-hairline-strong bg-surface-2/40 px-3 py-2 text-[12px] text-fg-muted">
      <b className="text-fg">{title}:</b> {s.candidates} judged · {s.filled} filled
      {s.status === "ok" && s.win_rate != null
        ? ` · win ${pct(s.win_rate)} · ${s.expectancy_r.mean?.toFixed(2)} R per trade`
        : ` · too few to summarise`}
    </div>
  );
}

function TrackRecord({ evaluations, market, onStarted }: { evaluations: ApexEvaluation[]; market: string; onStarted: () => void }) {
  const [days, setDays] = useState(120);
  const [error, setError] = useState<string | null>(null);
  const latest = evaluations[0];
  const running = latest?.state === "RUNNING";
  const start = async () => {
    setError(null);
    try {
      await api.apexEvaluate(market, days);
      onStarted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    }
  };
  const r = latest?.state === "COMPLETED" ? latest.result : null;
  return (
    <Panel title={`Rule track record · ${market}`} testId="apex-track">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-[13px] text-fg-muted">
          Last
          <input
            type="number"
            min={10}
            max={730}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="h-9 w-20 rounded-md border border-control/70 bg-surface-2 px-2 text-fg"
            aria-label="Days of history"
            data-testid="apex-days"
          />
          days
        </label>
        <Button onClick={() => void start()} disabled={running} reason={running ? "A track record is already running." : null} data-testid="apex-evaluate">
          <Icon name="play" size={14} /> Run track record
        </Button>
        {running && <span className="text-[13px] text-fg-muted">running… {Math.round((latest?.progress ?? 0) * 100)}%</span>}
        {error && <span className="text-[13px] text-danger">{error}</span>}
      </div>
      <p className="mt-2 text-[12px] leading-relaxed text-fg-faint">
        Walks through the period as if live: at every hour of the session window the same rules as above make candidates
        from the data known then; later bars judge them (conservative fills, costs included). The last 30% is reported
        separately as out-of-sample.
      </p>
      {latest?.state === "FAILED" && <p className="mt-2 text-[13px] text-danger">Last run failed: {latest.error}</p>}
      {r && (
        <div className="mt-3" data-testid="apex-track-result">
          {r.data.synthetic && <p className="mb-2 text-[12px] font-bold text-warning">On SYNTHETIC test data — says nothing about the market.</p>}
          <p className="mb-2 text-[13px] text-fg" data-testid="apex-verdict" data-code={r.verdict.code}>
            <b>{r.verdict.code.replace(/_/g, " ")}</b> — {r.verdict.text}
          </p>
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[12px] text-fg-faint">
                <th className="pb-2 font-semibold">Period</th>
                <th className="pb-2 text-right font-semibold">Filled</th>
                <th className="pb-2 text-right font-semibold">Win rate (95%)</th>
                <th className="pb-2 text-right font-semibold">R per trade (95%)</th>
                <th className="pb-2 text-right font-semibold">PF</th>
                <th className="pb-2 text-right font-semibold">Max DD</th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ["All", r.all],
                  ["In-sample", r.in_sample],
                  ["Out-of-sample", r.out_of_sample],
                ] as [string, ApexSummary][]
              ).map(([name, s]) => (
                <tr key={name} className="border-t border-hairline-strong/60" data-testid="apex-track-row">
                  <td className="py-1.5 text-fg">{name}</td>
                  <td className="py-1.5 text-right font-mono">{s.filled}</td>
                  <td className="py-1.5 text-right font-mono">
                    {s.win_rate != null && s.win_ci ? `${pct(s.win_rate)} (${pct(s.win_ci[0])}–${pct(s.win_ci[1])})` : "insufficient"}
                  </td>
                  <td className="py-1.5 text-right font-mono">
                    {s.expectancy_r.mean != null
                      ? `${s.expectancy_r.mean.toFixed(2)}${s.expectancy_r.ci ? ` (${s.expectancy_r.ci[0].toFixed(2)}…${s.expectancy_r.ci[1].toFixed(2)})` : ""}`
                      : "insufficient"}
                  </td>
                  <td className="py-1.5 text-right font-mono">{s.profit_factor ?? "—"}</td>
                  <td className="py-1.5 text-right font-mono">{s.max_drawdown_r} R</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[12px] text-fg-faint">
            {r.start} → {r.end} · {r.decisions} decision points · data: {r.data.label}
          </p>
        </div>
      )}
      {!latest && <p className="mt-3 text-[13px] text-fg-muted">No track record yet for {market}.</p>}
    </Panel>
  );
}
