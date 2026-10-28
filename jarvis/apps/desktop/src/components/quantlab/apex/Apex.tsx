import type {
  ApexAnalysis,
  ApexAudit,
  ApexCandidate,
  ApexChartData,
  ApexDataStatus,
  ApexEvaluation,
  ApexJournal,
  ApexMarket,
  ApexMarketName,
  ApexRate,
  ApexScenario,
  ApexStrategyName,
  ApexSummary,
  ApexTimeframe,
} from "@jarvis/protocol";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError } from "../../../lib/api";
import { headerState, useReadiness, useSupervisor } from "../../../lib/backend";
import { useJarvis } from "../../../store/store";
import { Icon } from "../../ui/icons";
import { Button, cx } from "../../ui/primitives";
import { EmptyState, LoadingState, Panel, StatusPill, type PillTone } from "../../ui/shell";
import { AnalystPanel, StatusStrip } from "./ApexAnalyst";
import { ApexChart } from "./ApexChart";
import { ApexData, CauseList, SpreadText } from "./ApexData";
import { FitnessView, SignalJournal } from "./ApexSignals";
import { ApexValidationPanel } from "./ApexValidation";
import { STRATEGIES, signalsOf, statusChips } from "./analyst";
import { structureKey, TIMEFRAMES } from "./chartView";

/**
 * JARVIS APEX — one screen for the trade: symbol, timeframe, a large chart with the zones,
 * market structure, long / short / no-trade scenarios with their counted rates, rule-based
 * entry / stop / target candidates, the data status and JARVIS's written reading. Journal,
 * track record, data and SENTINEL's re-check live in their own sub-areas.
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

type Area = "analysis" | "journal" | "track" | "validation" | "data" | "sentinel";
const AREAS: { id: Area; label: string }[] = [
  { id: "analysis", label: "Chart & analysis" },
  { id: "journal", label: "Signal journal" },
  { id: "track", label: "Backtest" },
  { id: "validation", label: "Validation" },
  { id: "data", label: "Data fitness" },
  { id: "sentinel", label: "SENTINEL check" },
];

function saved<T extends string | number>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    const found = allowed.find((a) => String(a) === raw);
    return found ?? fallback;
  } catch {
    return fallback;
  }
}

function remember(key: string, value: string | number): void {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    /* per-device convenience only */
  }
}

const when = (epoch: number) => new Date(epoch * 1000).toLocaleString();
const pct = (p: number) => `${(p * 100).toFixed(1)}%`;

export function ApexPanel() {
  const [markets, setMarkets] = useState<ApexMarket[] | null>(null);
  const [market, setMarketState] = useState<ApexMarketName>(() => saved("apex.market", MARKETS, "XAUUSD"));
  const [minutes, setMinutesState] = useState<ApexTimeframe>(() =>
    saved<ApexTimeframe>("apex.timeframe", TIMEFRAMES.map((t) => t.minutes), 15),
  );
  const [area, setAreaState] = useState<Area>(() => saved("apex.area", AREAS.map((a) => a.id), "analysis"));
  const [strategy, setStrategyState] = useState<ApexStrategyName>(() =>
    saved<ApexStrategyName>("apex.strategy", STRATEGIES.map((s) => s.id), "zone_rejection_5m"),
  );
  const [started, setStarted] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const abort = useRef<AbortController | null>(null);
  const [analyses, setAnalyses] = useState<Partial<Record<ApexMarketName, ApexAnalysis>>>({});
  const [chart, setChart] = useState<ApexChartData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; suggestion?: string | null; code?: string | null } | null>(null);
  const [journal, setJournal] = useState<ApexJournal | null>(null);
  const [evaluations, setEvaluations] = useState<ApexEvaluation[]>([]);
  const [status, setStatus] = useState<ApexDataStatus | null>(null);

  const analysis = analyses[market] ?? null;
  const setArea = (next: Area) => {
    setAreaState(next);
    remember("apex.area", next);
  };
  const setMarket = (next: ApexMarketName) => {
    setMarketState(next);
    setSelected(null);
    setError(null);
    setStatus(null);
    remember("apex.market", next);
  };
  const setMinutes = (next: ApexTimeframe) => {
    setMinutesState(next);
    remember("apex.timeframe", next);
  };
  const setStrategy = (next: ApexStrategyName) => {
    setStrategyState(next);
    remember("apex.strategy", next);
  };

  const loadSide = useCallback(async () => {
    const [m, j, e] = await Promise.all([api.apexMarkets(), api.apexJournal(market), api.apexEvaluations(market)]);
    setMarkets(m);
    setJournal(j);
    setEvaluations(e);
  }, [market]);
  const loadStatus = useCallback(async () => setStatus(await api.apexData(market)), [market]);

  // Loaded again whenever the backend (re)connects: a request that failed while it was
  // starting must not leave a stale "not reachable" behind.
  const connection = useJarvis((s) => s.connection);
  useEffect(() => {
    if (connection !== "online") return;
    setError((e) => (e && e.code === "UNREACHABLE" ? null : e));
    loadSide().catch(() => setError({ message: "The backend isn't reachable.", code: "UNREACHABLE" }));
    loadStatus().catch(() => undefined);
  }, [loadSide, loadStatus, connection]);

  // The latest stored analysis of this market is shown at once (local data, no new download).
  useEffect(() => {
    if (analyses[market]) return;
    let live = true;
    api
      .apexAnalyses(market)
      .then(async (rows) => {
        const latest = rows[0];
        if (!latest || !live) return;
        const full = await api.apexAnalysis(latest.id);
        if (live) setAnalyses((a) => (a[market] ? a : { ...a, [market]: full }));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [market, analyses]);

  const running = evaluations.find((e) => e.state === "RUNNING");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void loadSide(), 1000);
    return () => clearInterval(timer);
  }, [running, loadSide]);

  const repairing = status?.repair?.state === "RUNNING";
  useEffect(() => {
    if (!repairing) return;
    const timer = setInterval(() => void loadStatus(), 1000);
    return () => clearInterval(timer);
  }, [repairing, loadStatus]);

  // The chart in the chosen timeframe — only bars the analysis knew.
  const analysisId = analysis?.id;
  useEffect(() => {
    if (!analysisId) {
      setChart(null);
      return;
    }
    let live = true;
    api
      .apexChart(analysisId, minutes, minutes === 1 ? 480 : 600)
      .then((c) => live && setChart(c))
      .catch(() => live && setChart(null));
    return () => {
      live = false;
    };
  }, [analysisId, minutes]);

  const info = markets?.find((m) => m.market === market);
  const fixture = info?.fixture ?? false;
  const decimals = 2; // gold quotes in cents, NQ/MNQ in quarter points

  // The analysis as a job: elapsed time while it runs, Cancel stops waiting, Retry after a failure.
  useEffect(() => {
    if (started == null) return;
    const timer = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 500);
    return () => clearInterval(timer);
  }, [started]);

  const run = async () => {
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setStarted(Date.now());
    setElapsed(0);
    setError(null);
    setArea("analysis"); // where the job shows — never forced back later if the owner moved on
    try {
      const out = await api.apexAnalyze(market, { refresh: refresh && !fixture, signal: controller.signal });
      setAnalyses((a) => ({ ...a, [market]: out }));
      setSelected(out.candidates[0]?.id ?? null);
      await Promise.all([loadSide(), loadStatus()]);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.code === "CANCELLED"
            ? {
                message: "Analysis cancelled.",
                suggestion: "Waiting stopped. If the backend had already started, it finishes and journals the analysis anyway.",
                code: "CANCELLED",
              }
            : { message: err.message, suggestion: err.suggestion, code: err.status === 0 ? "UNREACHABLE" : err.code }
          : { message: "That didn't work." },
      );
      void loadStatus();
    } finally {
      abort.current = null;
      setStarted(null);
      setBusy(false);
    }
  };
  const cancel = () => abort.current?.abort();

  const repair = async () => {
    try {
      const started = await api.apexRepair(market);
      setStatus((s) => (s ? { ...s, repair: started } : s));
      setArea("data");
      await loadStatus();
    } catch (err) {
      setError(err instanceof ApiError ? { message: err.message, suggestion: err.suggestion } : { message: "The repair didn't start." });
    }
  };

  const candidate = useMemo(() => analysis?.candidates.find((c) => c.id === selected) ?? null, [analysis, selected]);
  const app = headerState(connection, useReadiness(), useSupervisor());
  const leadSignal = analysis ? (signalsOf(analysis, strategy).find((x) => x.entry != null) ?? signalsOf(analysis, strategy)[0] ?? null) : null;
  const chips = statusChips(app, analysis, leadSignal);
  const brief = info?.track_record;
  const calibration = brief && analysis && brief.synthetic === analysis.data.synthetic ? (brief.calibration?.code ?? null) : null;

  return (
    <div className="flex flex-col gap-4" data-testid="apex-panel">
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-hairline-strong bg-surface/90 px-4 py-3">
        <Segmented label="Symbol">
          {MARKETS.map((m) => (
            <SegmentButton key={m} active={market === m} onClick={() => setMarket(m)} testId={`apex-market-${m}`} bold>
              {m}
            </SegmentButton>
          ))}
        </Segmented>
        <Segmented label="Timeframe">
          {TIMEFRAMES.map((t) => (
            <SegmentButton key={t.minutes} active={minutes === t.minutes} onClick={() => setMinutes(t.minutes)} testId={`apex-tf-${t.minutes}`}>
              {t.label}
            </SegmentButton>
          ))}
        </Segmented>
        <Button variant="primary" size="lg" onClick={() => void run()} disabled={busy} reason={busy ? "Analysing…" : null} data-testid="apex-analyze">
          <Icon name="target" size={15} /> {busy ? "Analysing…" : "Start analysis"}
        </Button>
        {busy && (
          <span className="flex items-center gap-2 text-[12px] text-fg-muted" data-testid="apex-job" data-state="running">
            {refresh && !fixture ? "updating data, then analysing" : "analysing"} · {elapsed}s
            <Button onClick={cancel} data-testid="apex-cancel">
              <Icon name="x" size={13} /> Cancel
            </Button>
          </span>
        )}
        {!fixture && (
          <label className="flex items-center gap-2 text-[12px] text-fg-muted" title="Free Dukascopy data; the Data Vault is never charged">
            <input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} data-testid="apex-refresh" />
            Update data first
          </label>
        )}
        <DataChip analysis={analysis} status={status} onOpen={() => setArea("data")} />
        <span className="ml-auto flex items-center gap-2 text-[12px] text-fg-muted">
          <Icon name="lock" size={13} /> Research only — no orders
        </span>
      </div>

      <StatusStrip chips={chips} />

      <nav className="flex gap-1 overflow-x-auto rounded-xl border border-hairline-strong bg-surface/80 p-1" aria-label="APEX">
        {AREAS.map((a) => (
          <button
            key={a.id}
            type="button"
            onClick={() => setArea(a.id)}
            aria-current={area === a.id ? "page" : undefined}
            className={cx(
              "min-h-9 flex-1 rounded-lg px-3 text-[13px] font-semibold whitespace-nowrap transition-colors",
              area === a.id ? "bg-surface-2 text-fg shadow-[inset_0_-3px_0_var(--area)]" : "text-fg-muted hover:bg-white/5 hover:text-fg",
            )}
            data-testid={`apex-tab-${a.id}`}
          >
            {a.label}
          </button>
        ))}
      </nav>

      {error && (
        <div className="rounded-xl border border-danger/40 bg-danger/10 p-3 text-[13px]" role="alert" data-testid="apex-error">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-semibold text-danger">{error.message}</p>
            <Button onClick={() => void run()} disabled={busy} data-testid="apex-retry">
              <Icon name="play" size={13} /> Retry
            </Button>
          </div>
          {error.suggestion && <p className="mt-1 text-fg-muted">{error.suggestion}</p>}
          {status && (
            <div className="mt-2">
              <CauseList causes={status.causes} onRepair={() => void repair()} repairing={repairing} compact />
            </div>
          )}
        </div>
      )}

      {area === "analysis" &&
        (analysis ? (
          <AnalysisView
            analysis={analysis}
            chart={chart}
            minutes={minutes}
            candidate={candidate}
            onSelect={setSelected}
            decimals={decimals}
            calibration={calibration}
            status={status}
            onRepair={() => void repair()}
            repairing={repairing}
            onData={() => setArea("data")}
            strategy={strategy}
            onStrategy={setStrategy}
            onValidation={() => setArea("validation")}
          />
        ) : (
          !error && (
            <EmptyState icon="target" title={`No ${market} analysis yet`}>
              Press “Start analysis”. It reads the local data (and fetches the missing free days first).
            </EmptyState>
          )
        ))}
      {area === "journal" && (
        <div className="flex flex-col gap-4">
          <SignalJournal market={market} decimals={decimals} refreshKey={analysis?.id} />
          <Journal journal={journal} market={market} onResolved={() => void loadSide()} decimals={decimals} />
        </div>
      )}
      {area === "track" && (
        <TrackRecord evaluations={evaluations} market={market} onStarted={() => void loadSide()} strategy={strategy} onStrategy={setStrategy} />
      )}
      {area === "validation" && <ApexValidationPanel market={market} strategy={strategy} onStrategy={setStrategy} />}
      {area === "data" && (
        <div className="flex flex-col gap-4">
          <FitnessView market={market} refreshKey={`${analysis?.id ?? ""}|${status?.checked_at ?? ""}`} />
          <ApexData market={market} status={status} onRepair={() => void repair()} onAnalyse={() => void run()} />
        </div>
      )}
      {area === "sentinel" &&
        (analysis ? (
          <SentinelCheck analysis={analysis} />
        ) : (
          <EmptyState icon="check" title="Nothing to re-check yet">
            Make an analysis first; SENTINEL then recomputes it from the data on disk.
          </EmptyState>
        ))}
    </div>
  );
}

function Segmented({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div role="group" aria-label={label} className="flex rounded-lg border border-control/60 p-0.5">
      {children}
    </div>
  );
}

function SegmentButton({
  active,
  onClick,
  testId,
  bold,
  children,
}: {
  active: boolean;
  onClick: () => void;
  testId: string;
  bold?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cx(
        "h-8 rounded-md px-3 text-[13px]",
        bold ? "font-bold" : "font-semibold",
        active ? "bg-quant/20 text-fg" : "text-fg-muted hover:text-fg",
      )}
      data-testid={testId}
    >
      {children}
    </button>
  );
}

/** One chip: is the data good enough, and if not, the main reason — a click opens APEX → Data. */
function DataChip({ analysis, status, onOpen }: { analysis: ApexAnalysis | null; status: ApexDataStatus | null; onOpen: () => void }) {
  const worst = status?.causes.find((c) => c.level === "BLOCK") ?? status?.causes.find((c) => c.level === "WARN");
  const quality = analysis?.quality.status;
  const tone: PillTone =
    quality === "BLOCK" || worst?.level === "BLOCK"
      ? "danger"
      : quality === "WARN" || worst || analysis?.data.synthetic
        ? "warning"
        : status || analysis
          ? "success"
          : "muted";
  const text = analysis?.data.synthetic
    ? "Synthetic test data"
    : worst
      ? `Data: ${worst.code.replace(/_/g, " ").toLowerCase()}`
      : quality
        ? `Data ${quality}`
        : status
          ? "Data checked"
          : "Data …";
  return (
    <button type="button" onClick={onOpen} className="rounded-full" data-testid="apex-data-chip" data-tone={tone} title="Open APEX → Data">
      <StatusPill tone={tone}>{text}</StatusPill>
    </button>
  );
}

function DataBanner({
  analysis,
  status,
  onRepair,
  repairing,
  onData,
}: {
  analysis: ApexAnalysis;
  status: ApexDataStatus | null;
  onRepair: () => void;
  repairing: boolean;
  onData: () => void;
}) {
  const q = analysis.quality;
  const update = analysis.data.update;
  const issues = q.findings.filter((f) => f.level !== "INFO");
  return (
    <div className="flex flex-col gap-2" data-testid="apex-data">
      {analysis.data.synthetic && (
        <p className="rounded-xl border border-warning/50 bg-warning/10 px-3 py-2 text-[13px] font-bold text-warning" data-testid="apex-synthetic">
          SYNTHETIC TEST DATA — generated prices, not the market. Nothing below says anything about real {analysis.market}.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2 text-[12px] text-fg-muted">
        <StatusPill tone={QUALITY_TONE[q.status] ?? "muted"} testId="apex-quality" title="The data quality gate's verdict — freshness and validation are shown separately">{`Gate ${q.status}`}</StatusPill>
        <span>{analysis.data.label}</span>
        <span className="text-fg-faint">
          · as of {when(analysis.as_of)}
          {analysis.replay ? " (replay)" : ""}
          {q.summary.last_close ? ` · newest bar ${when(q.summary.last_close)}` : ""}
        </span>
        {update?.requested && (
          <span className={update.ok ? "text-fg-faint" : "font-semibold text-danger"} data-testid="apex-update" data-ok={update.ok ? "yes" : "no"}>
            · {update.ok ? "updated" : `update failed: ${update.error}`}
          </span>
        )}
        <span className="font-mono text-fg-faint" title="SHA-256 of the bars this analysis saw">
          · {analysis.data.sha256.slice(0, 12)}
        </span>
        <button type="button" onClick={onData} className="font-semibold text-quant hover:underline" data-testid="apex-data-details">
          Data details
        </button>
      </div>
      {analysis.data.proxy && <p className="text-[12px] text-warning">{analysis.data.proxy}</p>}
      {issues.length > 0 && (
        <ul className="flex flex-col gap-1 text-[12px] text-fg-muted">
          {issues.map((f) => (
            <li key={f.code} data-testid="apex-finding" data-code={f.code}>
              <b className={f.level === "BLOCK" ? "text-danger" : "text-warning"}>{f.code.replace(/_/g, " ")}</b> {f.message}
            </li>
          ))}
        </ul>
      )}
      {(analysis.blocked || q.stale || update?.ok === false) && status && (
        <CauseList causes={status.causes} onRepair={onRepair} repairing={repairing} compact />
      )}
    </div>
  );
}

function AnalysisView({
  analysis,
  chart,
  minutes,
  candidate,
  onSelect,
  decimals,
  calibration,
  status,
  onRepair,
  repairing,
  onData,
  strategy,
  onStrategy,
  onValidation,
}: {
  analysis: ApexAnalysis;
  chart: ApexChartData | null;
  minutes: ApexTimeframe;
  candidate: ApexCandidate | null;
  onSelect: (id: string) => void;
  decimals: number;
  calibration: string | null;
  status: ApexDataStatus | null;
  onRepair: () => void;
  repairing: boolean;
  onData: () => void;
  strategy: ApexStrategyName;
  onStrategy: (s: ApexStrategyName) => void;
  onValidation: () => void;
}) {
  const fmt = (p: number) => p.toFixed(decimals);
  const frame = TIMEFRAMES.find((t) => t.minutes === minutes)?.name ?? `${minutes} minutes`;
  const fallback = minutes === 15 && !chart ? analysis.chart.map((b) => [...b, 0] as [number, number, number, number, number, number]) : null;
  const bars = chart?.minutes === minutes ? chart.bars : (fallback ?? []);
  const structure = analysis.structure[structureKey(minutes)];
  return (
    <div className="flex flex-col gap-4">
      <Panel padded>
        <DataBanner analysis={analysis} status={status} onRepair={onRepair} repairing={repairing} onData={onData} />
      </Panel>
      <div className="grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,2.3fr)_minmax(360px,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Panel
            title={`${analysis.market} · ${analysis.price != null ? fmt(analysis.price) : "—"} · ${frame}`}
            aside={
              <span className="text-[12px] text-fg-muted">
                {chart?.prices === "bid" ? "bid prices · " : ""}
                {analysis.atr15 != null ? `ATR(15m) ${analysis.atr15.toFixed(decimals)} · ` : ""}scroll = zoom · drag = pan
              </span>
            }
          >
            {chart && !chart.same_data && (
              <p className="mb-2 text-[12px] text-warning" data-testid="apex-chart-changed">
                The cache changed since this analysis (missing days were filled) — the chart shows the data now; analyse again
                for an analysis on it.
              </p>
            )}
            {bars.length > 0 ? (
              <ApexChart
                bars={bars}
                minutes={minutes}
                forming={chart?.minutes === minutes ? chart.forming : false}
                zones={analysis.zones}
                structure={structure}
                candidate={candidate}
                price={analysis.price ?? null}
                decimals={decimals}
              />
            ) : (
              <LoadingState>Loading the {frame} chart…</LoadingState>
            )}
            <Legend />
          </Panel>
          {!analysis.blocked && <ZonesTable analysis={analysis} decimals={decimals} calibrated={calibration === "CALIBRATED"} />}
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <AnalystPanel
            analysis={analysis}
            strategy={strategy}
            onStrategy={onStrategy}
            onValidation={onValidation}
            onSelect={onSelect}
            decimals={decimals}
          />
          {!analysis.blocked && <StructureCard analysis={analysis} decimals={decimals} />}
          {!analysis.blocked && <ScenarioBoard analysis={analysis} calibration={calibration} />}
          {!analysis.blocked && <Candidates analysis={analysis} candidate={candidate} onSelect={onSelect} decimals={decimals} calibrated={calibration === "CALIBRATED"} />}
          {analysis.data.spread && (
            <p className="px-1 text-[12px] text-fg-muted">
              Spread: <SpreadText spread={analysis.data.spread} />
            </p>
          )}
        </div>
      </div>
      <JarvisPanel analysis={analysis} />
    </div>
  );
}

function Legend() {
  const item = (color: string, text: string, dashed = false) => (
    <span className="flex items-center gap-1.5">
      <span className="inline-block h-0 w-4 border-t-2" style={{ borderColor: color, borderStyle: dashed ? "dashed" : "solid" }} />
      {text}
    </span>
  );
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-fg-faint">
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-2.5 w-4 rounded-sm bg-[rgb(66_215_182/0.3)]" /> support zone
      </span>
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-2.5 w-4 rounded-sm bg-[rgb(255_116_125/0.3)]" /> resistance zone
      </span>
      {item("#ffe16b", "entry · BOS/CHoCH")}
      {item("#ff747d", "stop")}
      {item("#4cdfac", "target")}
      {item("#becfe7", "price at the moment", true)}
      <span>▲▼ confirmed swings (HH/HL/LH/LL) · keys: ← → + − 0</span>
    </div>
  );
}

function StructureCard({ analysis, decimals }: { analysis: ApexAnalysis; decimals: number }) {
  const fmt = (p: number) => p.toFixed(decimals);
  return (
    <Panel title="Market structure" testId="apex-structure">
      <div className="flex flex-col gap-2.5">
        {Object.entries(analysis.structure).map(([key, s]) => {
          const e = s.events[s.events.length - 1];
          return (
            <div key={key} className="rounded-xl border border-hairline-strong bg-surface-2/40 p-3 text-[13px]">
              <div className="flex items-center justify-between">
                <b className="text-fg">{key === "60" ? "1 hour" : `${key} minutes`}</b>
                <StatusPill tone={s.trend === "up" ? "success" : s.trend === "down" ? "danger" : "muted"} testId={`apex-trend-${key}`}>
                  {s.trend === "up" ? "uptrend" : s.trend === "down" ? "downtrend" : "range"}
                </StatusPill>
              </div>
              <p className="mt-1.5 text-fg-muted">
                High {s.last_high ? `${fmt(s.last_high.price)} ${s.last_high.label}` : "—"} · low{" "}
                {s.last_low ? `${fmt(s.last_low.price)} ${s.last_low.label}` : "—"}
              </p>
              {e && (
                <p className="mt-1 text-fg-faint">
                  {e.type} {e.direction} through {fmt(e.level)} · {when(e.t)}
                </p>
              )}
            </div>
          );
        })}
        <p className="text-[12px] leading-relaxed text-fg-faint">
          Swings count once closed bars on both sides confirm them. HH + HL = uptrend, LH + LL = downtrend; BOS = break with the
          trend, CHoCH = break against it.
        </p>
      </div>
    </Panel>
  );
}

/** Long, short and no-trade — each built from the counted hold / break rates of S1 and R1. */
function ScenarioBoard({ analysis, calibration }: { analysis: ApexAnalysis; calibration: string | null }) {
  const by = Object.fromEntries(analysis.scenarios.map((s) => [s.id, s]));
  const pick = (...ids: string[]) => ids.map((id) => by[id]).filter((s): s is ApexScenario => Boolean(s));
  const calibrated = calibration === "CALIBRATED";
  const noTrade = analysis.candidates.length === 0;
  return (
    <Panel
      title="Scenarios"
      aside={
        <StatusPill tone={calibrated ? "success" : calibration === "MISCALIBRATED" ? "danger" : "muted"} testId="apex-calibration-state">
          {calibrated ? "rates calibrated" : calibration === "MISCALIBRATED" ? "rates miscalibrated" : "calibration not checked"}
        </StatusPill>
      }
    >
      <div className="flex flex-col gap-3">
        <ScenarioGroup title="Long" tone="text-quant" items={pick("S1-hold", "R1-break")} calibrated={calibrated} />
        <ScenarioGroup title="Short" tone="text-danger" items={pick("R1-hold", "S1-break")} calibrated={calibrated} />
        <div className="rounded-xl border border-hairline-strong bg-surface-2/40 p-3 text-[13px]" data-testid="apex-no-trade" data-active={noTrade ? "yes" : "no"}>
          <b className="text-fg">No trade</b>
          {noTrade ? (
            <ul className="mt-1.5 flex flex-col gap-1 text-[12px] text-fg-muted">
              {(analysis.rejected.length ? analysis.rejected : [{ template: "zone_reaction", reason: "No rule fired at this moment." }]).map((r, k) => (
                <li key={k} data-testid="apex-rejected">
                  {"direction" in r && r.direction ? <b className="text-fg-faint">{`${r.direction} at ${r.zone}: `}</b> : null}
                  {r.reason}
                </li>
              ))}
              <li className="text-fg-faint">Standing aside is the default when no candidate passes its rules.</li>
            </ul>
          ) : (
            <p className="mt-1.5 text-[12px] text-fg-muted">
              Zone-reaction plans are limits at the zones; 5-minute plans are stop entries after a confirmed rejection. Either
              expires unfilled if price never gets there — nothing happens then.
            </p>
          )}
        </div>
      </div>
      <p className="mt-3 text-[12px] leading-relaxed text-fg-faint">
        {calibrated
          ? "The stated rates matched what later touches did in the last track record — counted, not promised."
          : "Shares of earlier touches before this moment, with sample size and 95% interval. Not yet shown to match later touches — read them as past frequencies, not probabilities."}
      </p>
    </Panel>
  );
}

function ScenarioGroup({ title, tone, items, calibrated }: { title: string; tone: string; items: ApexScenario[]; calibrated: boolean }) {
  return (
    <div className="rounded-xl border border-hairline-strong bg-surface-2/40 p-3 text-[13px]">
      <b className={tone}>{title}</b>
      {items.length === 0 && <p className="mt-1 text-[12px] text-fg-faint">No zone on this side.</p>}
      <ul className="mt-1.5 flex flex-col gap-1.5">
        {items.map((s) => (
          <li key={s.id} className="flex flex-col gap-0.5" data-testid="apex-scenario" data-id={s.id}>
            <span className="text-fg">{s.statement}</span>
            <Rate rate={s.rate} calibrated={calibrated} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function ZonesTable({ analysis, decimals, calibrated }: { analysis: ApexAnalysis; decimals: number; calibrated: boolean }) {
  const fmt = (p: number) => p.toFixed(decimals);
  return (
    <Panel title="Support & resistance zones" aside={<span className="text-[12px] text-fg-muted">held on touch = counted before this moment</span>}>
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-left text-[12px] text-fg-faint">
              <th className="pb-2 font-semibold">Zone</th>
              <th className="pb-2 font-semibold">Range</th>
              <th className="pb-2 font-semibold">Why</th>
              <th className="pb-2 pl-3 text-right font-semibold">Tests</th>
              <th className="pb-2 pl-3 text-right font-semibold">Away</th>
              <th className="pb-2 pl-3 text-right font-semibold">Held on touch</th>
            </tr>
          </thead>
          <tbody>
            {analysis.zones.map((z) => (
              <tr key={z.id} className="border-t border-hairline-strong/60" data-testid="apex-zone" data-side={z.side}>
                <td className={cx("py-1.5 font-bold", z.side === "support" ? "text-quant" : "text-danger")}>
                  {z.id}
                  {z.inside && <span className="ml-1 text-[11px] text-warning">price inside</span>}
                </td>
                <td className="py-1.5 pr-3 font-mono whitespace-nowrap text-fg">
                  {fmt(z.low)} – {fmt(z.high)}
                </td>
                <td className="py-1.5 text-fg-muted">{z.reasons.join(", ")}</td>
                <td className="py-1.5 pl-3 text-right font-mono text-fg-muted">{z.tests}</td>
                <td className="py-1.5 pl-3 text-right font-mono whitespace-nowrap text-fg-muted">{z.inside ? "0" : `${z.distance_atr.toFixed(1)} ATR`}</td>
                <td className="py-1.5 pl-3 text-right">
                  <Rate rate={z.rate} calibrated={calibrated} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {analysis.base_rates && <p className="mt-3 text-[12px] leading-relaxed text-fg-faint">{analysis.base_rates.definition}</p>}
    </Panel>
  );
}

function Candidates({
  analysis,
  candidate,
  onSelect,
  decimals,
  calibrated,
}: {
  analysis: ApexAnalysis;
  candidate: ApexCandidate | null;
  onSelect: (id: string) => void;
  decimals: number;
  calibrated: boolean;
}) {
  return (
    <Panel title="Entry · stop · target" aside={<span className="text-[12px] text-fg-muted">rules, not predictions</span>}>
      {analysis.candidates.length === 0 ? (
        <p className="text-[13px] text-fg-muted" data-testid="apex-no-candidate">
          {analysis.quality.stale ? "Data is stale — no candidates until it is updated." : "No candidate passes its rules right now (see No trade)."}
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {analysis.candidates.map((c) => (
            <CandidateCard key={c.id} c={c} selected={candidate?.id === c.id} onSelect={() => onSelect(c.id)} decimals={decimals} calibrated={calibrated} />
          ))}
          <p className="text-[12px] text-fg-faint">Click a candidate to draw its entry, stop and targets on the chart.</p>
        </div>
      )}
    </Panel>
  );
}

function JarvisPanel({ analysis }: { analysis: ApexAnalysis }) {
  const story = analysis.narrative;
  if (!story) return null;
  const ask = () => window.dispatchEvent(new CustomEvent("jarvis:draft", { detail: story.question }));
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Icon name="spark" size={15} className="text-quant" /> JARVIS
        </span>
      }
      aside={
        <Button onClick={ask} data-testid="apex-ask-jarvis" title="Puts the question in the command bar — edit it or press Enter">
          <Icon name="chat" size={14} /> Ask JARVIS about it
        </Button>
      }
      testId="apex-jarvis"
    >
      <p className="text-[14px] font-semibold text-fg" data-testid="apex-jarvis-headline">
        {story.headline}
      </p>
      <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-[13px] text-fg-muted">
        {story.points.map((p, k) => (
          <li key={k}>{p}</li>
        ))}
      </ul>
      <ul className="mt-3 flex flex-col gap-0.5 text-[12px] text-fg-faint">
        {story.caveats.map((c, k) => (
          <li key={k}>{c}</li>
        ))}
      </ul>
    </Panel>
  );
}

function CandidateCard({
  c,
  selected,
  onSelect,
  decimals,
  calibrated,
}: {
  c: ApexCandidate;
  selected: boolean;
  onSelect: () => void;
  decimals: number;
  calibrated: boolean;
}) {
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
      <p className="mb-1 text-[11px] font-semibold text-fg-faint" data-testid="apex-candidate-strategy">
        {c.template}
        {c.strategy_version ? ` ${c.strategy_version}` : ""}
        {c.trigger ? ` · trigger: ${c.trigger.rule}` : ""}
      </p>
      <div className="flex items-center justify-between gap-2">
        <b className={long ? "text-quant" : "text-danger"}>
          {long ? "LONG" : "SHORT"} at {c.zone} · {c.entry.type} {fmt(c.entry.price)}
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
        Zone held on touch: <Rate rate={c.zone_rate ?? null} calibrated={calibrated} inline />
      </p>
      <p className="mt-1 text-[12px] text-fg-muted" data-testid="apex-candidate-track">
        {track
          ? `Rule track record ${track.period[0]} → ${track.period[1]}${track.synthetic ? " (synthetic data)" : ""}: ${track.verdict?.text ?? ""}`
          : "No track record for this rule yet — run one (Track record) before trusting it."}
      </p>
    </button>
  );
}

function Rate({ rate, inline, calibrated }: { rate: ApexRate | null; inline?: boolean; calibrated?: boolean }) {
  if (!rate) return <span className="text-fg-faint">—</span>;
  if (rate.status !== "ok" || rate.p == null) {
    return (
      <span className="text-fg-faint" title={`${rate.n} cases`}>
        insufficient data ({rate.n})
      </span>
    );
  }
  const text = `${pct(rate.p)} (${rate.ci ? `${pct(rate.ci[0])}–${pct(rate.ci[1])}` : "—"}, n=${rate.n}, ${rate.days} days)`;
  const tag = calibrated ? "calibrated" : "past share";
  return (
    <span className={inline ? "text-fg" : "font-mono text-[12px] text-fg"} title={calibrated ? "Matched later touches in the last track record" : "Counted share of earlier touches — not checked against later ones yet"}>
      {text} <span className="font-sans text-[11px] text-fg-faint">{tag}</span>
    </span>
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

function Fragment2({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-fg-faint">{label}</dt>
      <dd className="font-mono text-fg">{value}</dd>
    </>
  );
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

/** Are the stated hold rates right? Stated vs. observed on the touches of the same period. */
function Calibration({ c }: { c: NonNullable<ApexEvaluation["result"]>["calibration"] & object }) {
  return (
    <div className="mt-4 border-t border-hairline-strong pt-3" data-testid="apex-calibration" data-code={c.verdict.code}>
      <h4 className="text-[13px] font-black tracking-[0.04em] text-fg uppercase">Calibration of the stated rates</h4>
      <p className="mt-1 text-[13px] text-fg">
        <b>{c.verdict.code}</b> — {c.verdict.text}
      </p>
      <p className="mt-1 text-[12px] text-fg-muted">
        {c.touches} touches judged ({c.skipped} without enough history) · Brier {c.brier ?? "—"} vs. {c.brier_reference ?? "—"} for a
        side-only rate · skill {c.skill ?? "—"} (above 0 = the kinds of level add information)
      </p>
      {c.bins.length > 0 && (
        <table className="mt-2 w-full text-[13px]">
          <thead>
            <tr className="text-left text-[12px] text-fg-faint">
              <th className="pb-1.5 font-semibold">Stated rate</th>
              <th className="pb-1.5 text-right font-semibold">Touches</th>
              <th className="pb-1.5 text-right font-semibold">Stated (mean)</th>
              <th className="pb-1.5 text-right font-semibold">Observed (95%)</th>
              <th className="pb-1.5 text-right font-semibold">Fits</th>
            </tr>
          </thead>
          <tbody>
            {c.bins.map((b) => (
              <tr key={b.range.join("-")} className="border-t border-hairline-strong/60" data-testid="apex-calibration-bin">
                <td className="py-1 font-mono">
                  {pct(b.range[0])}–{pct(b.range[1])}
                </td>
                <td className="py-1 text-right font-mono">{b.n}</td>
                <td className="py-1 text-right font-mono">{pct(b.stated)}</td>
                <td className="py-1 text-right font-mono">
                  {pct(b.observed)} {b.ci ? `(${pct(b.ci[0])}–${pct(b.ci[1])})` : ""}
                </td>
                <td className={cx("py-1 text-right font-bold", b.inside ? "text-success" : "text-danger")}>{b.inside ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function TrackRecord({
  evaluations,
  market,
  onStarted,
  strategy,
  onStrategy,
}: {
  evaluations: ApexEvaluation[];
  market: string;
  onStarted: () => void;
  strategy: ApexStrategyName;
  onStrategy: (s: ApexStrategyName) => void;
}) {
  const [days, setDays] = useState(120);
  const [error, setError] = useState<string | null>(null);
  const latest = evaluations.find((e) => e.template === strategy);
  const running = evaluations.some((e) => e.state === "RUNNING");
  const start = async () => {
    setError(null);
    try {
      await api.apexEvaluate(market, days, strategy);
      onStarted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    }
  };
  const r = latest?.state === "COMPLETED" ? latest.result : null;
  return (
    <Panel title={`Backtest · ${market}`} testId="apex-track">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented label="Strategy">
          {STRATEGIES.map((s) => (
            <SegmentButton key={s.id} active={strategy === s.id} onClick={() => onStrategy(s.id)} testId={`apex-track-strategy-${s.id}`}>
              {s.label}
            </SegmentButton>
          ))}
        </Segmented>
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
        Walks through the period as if live: at every decision point of the session window (each hour for the zone reaction,
        each 5-minute close for the 5-minute rejection) the same rules as above make candidates from the data known then;
        later bars judge them — conservative fills on the right bid/ask side, costs included. The last 30% is reported
        separately; the binding test is the Validation tab.
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
            {r.execution ? ` · ${r.execution.assumption.replace(/_/g, " ")}` : ""}
          </p>
          {r.calibration && <Calibration c={r.calibration} />}
        </div>
      )}
      {!latest && <p className="mt-3 text-[13px] text-fg-muted">No backtest yet for {strategy} on {market}.</p>}
    </Panel>
  );
}
