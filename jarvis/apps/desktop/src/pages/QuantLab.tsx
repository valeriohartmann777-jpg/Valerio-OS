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
import { Badge, ResearchOnly } from "../components/quantlab/ui";
import { Button, cx } from "../components/ui/primitives";
import { ApiError, api } from "../lib/api";
import { useJarvis } from "../store/store";

/**
 * QuantLab — the research terminal: idea (video, text, link) → claims → blueprint → verified
 * data → honest simulation → validation → audit → dossier. Research only: nothing here can
 * place an order.
 */

const SECTIONS: { id: Section; modes: Mode[] }[] = [
  { id: "Idea Inbox", modes: ["Simple", "Research", "Institutional"] },
  { id: "Research Room", modes: ["Simple", "Research", "Institutional"] },
  { id: "Overview", modes: ["Simple", "Research", "Institutional"] },
  { id: "Strategy Studio", modes: ["Simple", "Research", "Institutional"] },
  { id: "Data Hub", modes: ["Simple", "Research", "Institutional"] },
  { id: "Backtest Lab", modes: ["Simple", "Research", "Institutional"] },
  { id: "Validation", modes: ["Research", "Institutional"] },
  { id: "Trade Explorer", modes: ["Research", "Institutional"] },
  { id: "Experiments", modes: ["Research", "Institutional"] },
  { id: "Risk & Execution", modes: ["Institutional"] },
  { id: "Reports", modes: ["Simple", "Research", "Institutional"] },
  { id: "Equity lab (R1)", modes: ["Research", "Institutional"] },
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

export function QuantLab() {
  const [mode, setModeState] = useState<Mode>(savedMode);
  const [section, setSection] = useState<Section>("Idea Inbox");
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
    if (!SECTIONS.find((s) => s.id === section)?.modes.includes(m)) setSection("Idea Inbox");
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
    <div className="h-full overflow-y-auto" data-testid="quantlab-page">
      <div className="mx-auto max-w-[1400px] px-8 py-8">
        <header className="mb-6 flex flex-wrap items-end gap-3">
          <div className="mr-auto">
            <p className="font-mono text-2xs tracking-[0.2em] text-fg-faint">JARVIS / QUANTLAB / {section.toUpperCase()}</p>
            <h1 className="mt-1 text-2xl font-light tracking-[-0.01em] text-fg">QuantLab</h1>
            <p className="mt-1 text-[13px] text-fg-muted">
              Futures research terminal · NQ · MNQ · ES · MES ·{" "}
              {busy ? <span className="text-ql">work in progress</span> : "idle"}
            </p>
          </div>
          {hub?.fixture ? (
            <FixtureBadge label="Fixture provider" />
          ) : (
            <Badge tone={hub?.status === "CONNECTED" ? "positive" : "muted"} icon={hub?.status === "CONNECTED" ? "●" : "○"} testId="ql-provider">
              {hub?.status === "CONNECTED" ? "Databento" : "Databento not connected"}
            </Badge>
          )}
          <ResearchOnly />
          <div className="flex rounded-lg border border-ql-border p-0.5" role="radiogroup" aria-label="Detail level">
            {MODES.map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={mode === m}
                onClick={() => setMode(m)}
                className={cx("rounded-md px-2.5 py-1 text-[12px]", mode === m ? "bg-ql-raised text-fg" : "text-fg-faint hover:text-fg-muted")}
                data-testid={`ql-mode-${m.toLowerCase()}`}
              >
                {m}
              </button>
            ))}
          </div>
          <Button
            variant="danger"
            disabled={!busy}
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
        </header>

        <nav className="mb-6 flex gap-1 overflow-x-auto rounded-xl border border-ql-border bg-ql-surface p-1" aria-label="QuantLab">
          {SECTIONS.filter((s) => s.modes.includes(mode)).map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                setSection(s.id);
                if (s.id === "Idea Inbox") setSourceId(null);
              }}
              className={cx(
                "flex-1 whitespace-nowrap rounded-lg px-3 py-1.5 text-[13px] transition-colors",
                section === s.id ? "bg-ql-raised text-fg" : "text-fg-faint hover:text-fg-muted",
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
          <BacktestLab runId={runId} runs={runs} tick={tick} mode={mode} onPick={setRunId} onTrade={(n) => { setTradeFocus(n); setSection(mode === "Simple" ? "Backtest Lab" : "Trade Explorer"); }} />
        )}
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
      </div>
    </div>
  );
}
