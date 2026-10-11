import type { QhDataset, QrOverview, QrRunRow } from "@jarvis/protocol";
import { useCallback, useEffect, useState } from "react";

import { EquityLab } from "../components/quantlab/EquityLab";
import { DataHub } from "../components/quantlab/hub/DataHub";
import { IdeaInbox } from "../components/quantlab/ideas/IdeaInbox";
import { ResearchRoom } from "../components/quantlab/ideas/ResearchRoom";
import { SourceView } from "../components/quantlab/ideas/SourceView";
import { FixtureBadge, useQuantLabTick, type Mode } from "../components/quantlab/research/common";
import { Experiments, TradeExplorer } from "../components/quantlab/research/Explorer";
import { Overview, type Section } from "../components/quantlab/research/Overview";
import { BacktestLab, Reports, RiskExecution, ValidationLab } from "../components/quantlab/research/Results";
import { StrategyStudio } from "../components/quantlab/research/StrategyStudio";
import { BacktestLauncher } from "../components/quantlab/vault/BacktestLauncher";
import { DataVault } from "../components/quantlab/vault/DataVault";
import { Badge, ResearchOnly } from "../components/quantlab/ui";
import { Icon } from "../components/ui/icons";
import { Button, cx } from "../components/ui/primitives";
import { PageHeader, WorkspaceModeBadge } from "../components/ui/shell";
import { ApiError, api } from "../lib/api";
import { go } from "../lib/history";
import { useJarvis } from "../store/store";
import { Bots } from "./Bots";

/**
 * QuantLab — the research terminal: idea (video, text, link) → claims → blueprint → verified
 * data → honest simulation → validation → audit → dossier. Research only: nothing here can
 * place an order.
 */

const SECTIONS: { id: Section; modes: Mode[] }[] = [
  { id: "Backtest Lab", modes: ["Simple", "Research", "Institutional"] },
  { id: "Data Vault", modes: ["Simple", "Research", "Institutional"] },
  { id: "Idea Inbox", modes: ["Simple", "Research", "Institutional"] },
  { id: "Research Room", modes: ["Simple", "Research", "Institutional"] },
  { id: "Overview", modes: ["Simple", "Research", "Institutional"] },
  { id: "Strategy Studio", modes: ["Simple", "Research", "Institutional"] },
  { id: "Data Hub", modes: ["Simple", "Research", "Institutional"] },
  { id: "Validation", modes: ["Research", "Institutional"] },
  { id: "Trade Explorer", modes: ["Research", "Institutional"] },
  { id: "Experiments", modes: ["Research", "Institutional"] },
  { id: "Risk & Execution", modes: ["Institutional"] },
  { id: "Reports", modes: ["Simple", "Research", "Institutional"] },
  { id: "Equity lab (R1)", modes: ["Research", "Institutional"] },
  { id: "MT5 Bots", modes: ["Simple", "Research", "Institutional"] },
];
const MODES: Mode[] = ["Simple", "Research", "Institutional"];

function savedMode(): Mode {
  try {
    const value = localStorage.getItem("quantlab.mode");
    return MODES.includes(value as Mode) ? (value as Mode) : "Simple";
  } catch {
    return "Simple";
  }
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-+$/, "");
// Old links: #/bots and #/quantlab/bots open the MT5 Bots section.
const ALIASES: Record<string, Section> = { bots: "MT5 Bots" };

/** The section in the address (#/quantlab/<slug>); Backtest Lab when none or unknown. */
function sectionOf(value: string | undefined): Section {
  if (!value) return "Backtest Lab";
  return ALIASES[value] ?? SECTIONS.find((s) => slug(s.id) === value)?.id ?? "Backtest Lab";
}

export function QuantLab() {
  const [mode, setModeState] = useState<Mode>(savedMode);
  const view = useJarvis((s) => s.view);
  const section = sectionOf(view.name === "quantlab" ? view.section : undefined);
  const setSection = useCallback((next: Section) => go({ name: "quantlab", section: slug(next) }), []);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [missionId, setMissionId] = useState<string | null>(null);
  const [overview, setOverview] = useState<QrOverview | null>(null);
  const [runs, setRuns] = useState<QrRunRow[]>([]);
  const [datasets, setDatasets] = useState<QhDataset[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [tradeFocus, setTradeFocus] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState<string | null>(null);
  const tick = useQuantLabTick();
  const connection = useJarvis((s) => s.connection);

  const setMode = (m: Mode) => {
    setModeState(m);
    try {
      localStorage.setItem("quantlab.mode", m);
    } catch {
      /* per-viewer convenience only */
    }
    if (!SECTIONS.find((s) => s.id === section)?.modes.includes(m)) setSection("Backtest Lab");
  };

  const refresh = useCallback(async () => {
    try {
      const [o, r, d] = await Promise.all([api.qrOverview(), api.qrRuns(), api.qhDatasets()]);
      setOverview(o);
      setRuns(r);
      setDatasets(d);
      setError(null);
      setRunId((current) => current ?? r.find((x) => x.status === "COMPLETED")?.id ?? null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "QuantLab isn't reachable.");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, tick, connection]);

  const openRun = (id: string, target: Section = "Backtest Lab") => {
    setRunId(id);
    setTradeFocus(null);
    setSection(target);
  };
  const openSource = (id: string) => {
    setSourceId(id);
    setSection("Idea Inbox");
  };
  const openMission = (id: string) => {
    setMissionId(id);
    setSection("Research Room");
  };
  const busy = runs.some((r) => r.status === "QUEUED" || r.status === "RUNNING") || (overview?.hub.jobs_running ?? 0) > 0;
  const hub = overview?.hub;

  return (
    <div className="h-full overflow-y-auto px-7" data-testid="quantlab-page" style={{ "--area": "var(--color-quant)" } as React.CSSProperties}>
      <div className="mx-auto max-w-[1400px] pb-8">
        <PageHeader
          eyebrow={`JARVIS / Personal QuantLab / ${section}`}
          title="QuantLab."
          subtitle={
            <>
              Your private trading research: strategies, local market data, backtests. Futures NQ · MNQ · ES · MES ·{" "}
              {busy ? <span className="text-ql">work in progress</span> : "idle"}
            </>
          }
          aside={
            <>
              <WorkspaceModeBadge mode="solo" />
              {hub?.fixture ? (
                <FixtureBadge label="Fixture provider" />
              ) : (
                <Badge tone={hub?.status === "CONNECTED" ? "positive" : "muted"} icon={hub?.status === "CONNECTED" ? "●" : "○"} testId="ql-provider">
                  {hub?.status === "CONNECTED" ? "Databento" : "Databento not connected"}
                </Badge>
              )}
              <ResearchOnly />
            </>
          }
        />
        <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-quant/30 bg-quant/5 px-4 py-2.5 text-[13px] text-fg-muted" data-testid="ql-solo-banner">
          <Icon name="lock" size={16} className="shrink-0 text-quant" />
          <span className="min-w-0 flex-1">
            <b className="text-fg">Solo mode.</b> ULTRON&apos;s agents can&apos;t read or change anything here unless you share a strategy with
            the team.
          </span>
          <button type="button" onClick={() => go({ name: "ultron", room: "quantlab" })} className="inline-flex items-center gap-1.5 font-semibold text-quant hover:underline" data-testid="ql-to-team">
            Work on it with ULTRON <Icon name="arrow" size={14} />
          </button>
          <div className="flex rounded-lg border border-ql-border p-0.5" role="radiogroup" aria-label="Detail level">
            {MODES.map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={mode === m}
                onClick={() => setMode(m)}
                className={cx("rounded-md px-2.5 py-1 text-[12px]", mode === m ? "bg-ql-raised text-fg" : "text-fg-muted hover:text-fg")}
                data-testid={`ql-mode-${m.toLowerCase()}`}
              >
                {m}
              </button>
            ))}
          </div>
          <Button
            variant="danger"
            disabled={!busy}
            reason="Nothing is running."
            onClick={async () => {
              const r = await api.qlStopAll();
              setStopped(`Stopped ${r.runs.length} run(s) and ${r.downloads.length} download(s). Charges for data already delivered stay.`);
              void refresh();
            }}
            title="Stop every research run and data download"
            data-testid="ql-stop-all"
          >
            Stop all
          </Button>
        </div>

        <nav className="mb-6 flex gap-1 overflow-x-auto rounded-xl border border-hairline-strong bg-surface/80 p-1" aria-label="QuantLab">
          {SECTIONS.filter((s) => s.modes.includes(mode) || s.id === section).map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                setSection(s.id);
                if (s.id === "Idea Inbox") setSourceId(null);
              }}
              className={cx(
                "min-h-10 flex-1 whitespace-nowrap rounded-lg px-3 text-[13px] font-semibold transition-colors",
                section === s.id ? "bg-surface-2 text-fg shadow-[inset_0_-3px_0_var(--area)]" : "text-fg-muted hover:bg-white/5 hover:text-fg",
              )}
              aria-current={section === s.id ? "page" : undefined}
              data-testid={`ql-nav-${slug(s.id)}`}
            >
              {s.id}
            </button>
          ))}
        </nav>
        {error && <p className="mb-4 text-[13px] text-ql-danger">{error}</p>}
        {stopped && <p className="mb-4 text-[13px] text-fg-muted" role="status">{stopped}</p>}

        {section === "Idea Inbox" &&
          (sourceId ? (
            <SourceView sourceId={sourceId} tick={tick} mode={mode} onBack={() => setSourceId(null)} onOpenMission={openMission} />
          ) : (
            <IdeaInbox tick={tick} onOpenSource={openSource} onOpenMission={openMission} />
          ))}
        {section === "Research Room" && (
          <ResearchRoom
            tick={tick}
            mode={mode}
            missionId={missionId}
            onPick={setMissionId}
            onOpenSource={openSource}
            onDataHub={() => setSection("Data Hub")}
          />
        )}
        {section === "Overview" && <Overview overview={overview} runs={runs} go={setSection} open={openRun} />}
        {section === "Strategy Studio" && (
          <StrategyStudio mode={mode} tick={tick} datasets={datasets} onRun={(id) => { void refresh(); openRun(id); }} />
        )}
        {section === "Data Hub" && <DataHub mode={mode} tick={tick} onDatasets={refresh} />}
        {section === "Backtest Lab" && (
          <div className="space-y-5">
            <BacktestLauncher
              tick={tick}
              onRun={(id) => {
                setRunId(id);
                setTradeFocus(null);
                void refresh();
              }}
              onVault={() => setSection("Data Vault")}
              onIdeas={() => {
                setSourceId(null);
                setSection("Idea Inbox");
              }}
            />
            <BacktestLab runId={runId} runs={runs} tick={tick} mode={mode} onPick={setRunId} onTrade={(n) => { setTradeFocus(n); setSection(mode === "Simple" ? "Backtest Lab" : "Trade Explorer"); }} />
          </div>
        )}
        {section === "Data Vault" && <DataVault tick={tick} />}
        {section === "Validation" && (
          <ValidationLab runId={runId} runs={runs} tick={tick} mode={mode} onPick={setRunId} onRun={(id) => { void refresh(); openRun(id, "Validation"); }} />
        )}
        {section === "Trade Explorer" && <TradeExplorer runId={runId} runs={runs} focus={tradeFocus} onPick={setRunId} />}
        {section === "Experiments" && (
          <Experiments mode={mode} tick={tick} onOpenRun={(id) => openRun(id)} onEquityLab={() => setSection("Equity lab (R1)")} />
        )}
        {section === "Risk & Execution" && <RiskExecution runId={runId} runs={runs} tick={tick} onPick={setRunId} />}
        {section === "Reports" && <Reports runId={runId} runs={runs} onPick={setRunId} />}
        {section === "Equity lab (R1)" && <EquityLab />}
        {section === "MT5 Bots" && <Bots embedded />}
      </div>
    </div>
  );
}
