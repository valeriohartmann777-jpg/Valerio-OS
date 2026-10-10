import type { QlDataset, QlEquity, QlExperiment, QlExperimentRow, QlOverview, QlStrategy } from "@jarvis/protocol";
import { useCallback, useEffect, useState } from "react";

import { Architect } from "./Architect";
import { DataLab } from "./DataLab";
import { EquityChart } from "./EquityChart";
import { Assessment, ExperimentView } from "./ExperimentView";
import {
  Card,
  CheckBadge,
  Hash,
  Metric,
  Provisional,
  ResearchOnly,
  RunStatus,
  SyntheticBadge,
  VerdictBadge,
  ago,
  inputClass,
  money,
  pct,
} from "./ui";
import { Button, cx } from "../ui/primitives";
import { ApiError, api } from "../../lib/api";
import { useJarvis } from "../../store/store";

/**
 * The R1 cash-equity reference lab (SMA crossover on imported bars), kept as it was
 * built: Overview | Strategies | Experiments | Datasets | Reports.
 * Research only: nothing here can place an order.
 */

const SECTIONS = ["Overview", "Strategies", "Experiments", "Datasets", "Reports"] as const;
type Section = (typeof SECTIONS)[number];

/** The newest quantlab.* event id: a change means "refetch". */
function useQuantLabTick(): string | null {
  return useJarvis((s) => {
    for (let i = s.activity.length - 1; i >= 0; i--) {
      const event = s.activity[i];
      if (event?.type.startsWith("quantlab.")) return event.id;
    }
    return null;
  });
}

export function EquityLab() {
  const [section, setSection] = useState<Section>("Overview");
  const [overview, setOverview] = useState<QlOverview | null>(null);
  const [strategies, setStrategies] = useState<QlStrategy[]>([]);
  const [datasets, setDatasets] = useState<QlDataset[]>([]);
  const [experiments, setExperiments] = useState<QlExperimentRow[]>([]);
  const [experimentId, setExperimentId] = useState<string | null>(null);
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tick = useQuantLabTick();
  const connection = useJarvis((s) => s.connection);

  const refresh = useCallback(async () => {
    try {
      const [o, s, d, e] = await Promise.all([api.qlOverview(), api.qlStrategies(), api.qlDatasets(), api.qlExperiments()]);
      setOverview(o);
      setStrategies(s);
      setDatasets(d);
      setExperiments(e);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "QuantLab isn't reachable.");
    }
  }, []);

  // Refetch on every quantlab.* event and after reconnecting (events may have been missed).
  useEffect(() => {
    void refresh();
  }, [refresh, tick, connection]);

  const openExperiment = (id: string) => {
    setExperimentId(id);
    setSection("Experiments");
  };

  return (
    <div data-testid="ql-equity-lab">
      <div>
        <header className="mb-6 flex flex-wrap items-end gap-4">
          <div className="mr-auto">
            <p className="font-mono text-2xs tracking-[0.2em] text-fg-faint">EQUITY REFERENCE LAB (R1) / {section.toUpperCase()}</p>
            <h2 className="mt-1 text-xl font-light tracking-[-0.01em] text-fg">Cash-equity reference lab</h2>
            <p className="mt-1 text-[13px] text-fg-muted">
              {overview?.scope ?? "Research workbench"} ·{" "}
              {overview && overview.counts.running > 0 ? (
                <span className="text-ql">{overview.counts.running} run(s) in progress</span>
              ) : (
                "idle"
              )}
            </p>
          </div>
          <ResearchOnly />
          <Button variant="primary" onClick={() => setSection("Strategies")} data-testid="ql-new-strategy">
            New strategy
          </Button>
        </header>

        <nav className="mb-6 flex gap-1 rounded-xl border border-ql-border bg-ql-surface p-1" aria-label="QuantLab">
          {SECTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => {
                setSection(s);
                if (s === "Experiments") setExperimentId(null);
              }}
              className={cx(
                "flex-1 rounded-lg px-3 py-1.5 text-[13px] transition-colors",
                section === s ? "bg-ql-raised text-fg" : "text-fg-faint hover:text-fg-muted",
              )}
              data-testid={`ql-section-${s.toLowerCase()}`}
            >
              {s}
            </button>
          ))}
        </nav>

        {error && <p className="mb-4 text-[13px] text-ql-danger">{error}</p>}

        {section === "Overview" && (
          <Cockpit overview={overview} onOpen={openExperiment} go={setSection} />
        )}
        {section === "Strategies" && (
          <Strategies
            strategies={strategies}
            datasets={datasets}
            template={overview?.template ?? null}
            onChanged={refresh}
            onStarted={(exp) => {
              void refresh();
              openExperiment(exp.id);
            }}
          />
        )}
        {section === "Experiments" &&
          (experimentId ? (
            <ExperimentPane id={experimentId} tick={tick} onBack={() => setExperimentId(null)} />
          ) : (
            <ExperimentList experiments={experiments} strategies={strategies} datasets={datasets} onOpen={openExperiment} />
          ))}
        {section === "Datasets" && (
          <DataLab
            datasets={datasets}
            fixturesAvailable={overview?.fixtures_available ?? false}
            selected={datasetId}
            onSelect={setDatasetId}
            onImported={(d) => {
              setDatasetId(d.id);
              void refresh();
            }}
          />
        )}
        {section === "Reports" && <Reports experiments={experiments} onOpen={openExperiment} />}
      </div>
    </div>
  );
}

// Overview ------------------------------------------------------------------------------

function Cockpit({
  overview,
  onOpen,
  go,
}: {
  overview: QlOverview | null;
  onOpen: (id: string) => void;
  go: (s: Section) => void;
}) {
  const latest = overview?.latest ?? null;
  const newest = overview?.experiments[0] ?? null;
  const latestId = latest?.id ?? null;
  const [equity, setEquity] = useState<QlEquity | null>(null);
  useEffect(() => {
    if (!latestId) return;
    let live = true;
    api
      .qlEquity(latestId, 600)
      .then((e) => live && setEquity(e))
      .catch(() => live && setEquity(null));
    return () => {
      live = false;
    };
  }, [latestId]);

  if (!overview) return <p className="text-[13px] text-fg-faint">Loading…</p>;
  if (!latest) return <GettingStarted overview={overview} go={go} />;
  const s = latest.summary;
  if (!s) return null;
  const { full, oos } = s.metrics;
  const counts = (["A", "B", "C", "D", "E"] as const).map((gate) => {
    const checks = s.checks.filter((c) => c.gate === gate);
    return {
      gate,
      pass: checks.filter((c) => c.result === "PASS").length,
      warn: checks.filter((c) => c.result === "WARN").length,
      fail: checks.filter((c) => c.result === "FAIL").length,
      open: checks.filter((c) => c.result === "NOT_RUN").length,
    };
  });
  const warnings = s.checks.filter((c) => c.result === "WARN" || c.result === "FAIL").slice(0, 3);

  return (
    <div className="space-y-5" data-testid="ql-cockpit">
      {newest && newest.id !== latest.id && newest.status !== "completed" && (
        <Card testId="ql-newest-run">
          <div className="flex items-center gap-3 text-[13px]">
            <RunStatus status={newest.status} />
            <span className="text-fg-muted">
              Newest run {newest.name ?? ""}: {newest.status === "failed" ? newest.error : newest.stage?.replace(/_/g, " ") ?? newest.status}.
              The result below is the previous completed run.
            </span>
            <Button className="ml-auto" onClick={() => onOpen(newest.id)}>
              Open
            </Button>
          </div>
        </Card>
      )}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Card
          title="Latest experiment"
          aside={
            <div className="flex gap-2">
              {s.synthetic && <SyntheticBadge />}
              <ResearchOnly />
            </div>
          }
        >
          <button type="button" className="block w-full text-left" onClick={() => onOpen(latest.id)}>
            <p className="text-lg font-light text-fg">
              {s.name} <span className="text-fg-muted">v{latest.strategy_version} · {s.symbol}</span>
            </p>
            <p className="mt-1 text-[13px] text-fg-faint">
              {latest.dataset?.passport.coverage_start_utc?.slice(0, 10)} → {latest.dataset?.passport.coverage_end_utc?.slice(0, 10)} · finished{" "}
              {ago(latest.finished_at)} · <Hash value={latest.id} />
            </p>
          </button>
          <dl className="mt-5 grid grid-cols-3 gap-4">
            <Metric label="Net P&L" value={money(full.net_pnl, s.currency)} sub={pct(full.return_pct)} />
            <Metric label="Max DD" value={pct(full.max_drawdown_pct, 2, false)} sub={money(-full.max_drawdown_abs, s.currency)} />
            <Metric label="OOS net" value={money(oos.net_pnl, s.currency)} sub={`${oos.trades_closed} OOS trades`} />
          </dl>
        </Card>
        <Card title="JARVIS assessment" aside={<VerdictBadge verdict={s.verdict} />} testId="ql-cockpit-verdict">
          <p className="text-[15px] leading-snug text-fg">{s.assessment.headline}</p>
          <p className="mt-2 text-[13px] text-fg-muted">{s.meaning}</p>
          <p className="mt-3 text-[13px] text-fg-muted">
            <span className="text-fg-faint">Next test: </span>
            {s.assessment.next_test}
          </p>
        </Card>
      </div>

      <Card title="Equity net of costs · train / out-of-sample" aside={<Provisional />}>
        {equity ? (
          <EquityChart
            points={equity.points}
            initialCash={full.initial_cash}
            currency={s.currency}
            oosStart={equity.oos_start}
            totalPoints={equity.total_points}
          />
        ) : (
          <p className="text-[13px] text-fg-faint">Loading the curve…</p>
        )}
      </Card>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Validation matrix">
          <table className="w-full text-[13px]">
            <thead className="text-2xs text-fg-faint">
              <tr>
                <th className="py-1 text-left font-medium">Gate</th>
                <th className="py-1 font-medium">✓</th>
                <th className="py-1 font-medium">!</th>
                <th className="py-1 font-medium">✕</th>
                <th className="py-1 font-medium">○</th>
              </tr>
            </thead>
            <tbody className="text-center font-mono text-fg-muted tabular">
              {counts.map((c) => (
                <tr key={c.gate} className="border-t border-ql-border/60">
                  <td className="py-1.5 text-left font-sans text-fg">Gate {c.gate}</td>
                  <td className={cx(c.pass > 0 && "text-ql-positive")}>{c.pass}</td>
                  <td className={cx(c.warn > 0 && "text-ql-warning")}>{c.warn}</td>
                  <td className={cx(c.fail > 0 && "text-ql-danger")}>{c.fail}</td>
                  <td>{c.open}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-2xs text-fg-faint">✓ pass · ! warn · ✕ fail · ○ not run in R1</p>
        </Card>
        <Card title="Top warnings">
          <ul className="space-y-3">
            {warnings.map((c) => (
              <li key={c.id} className="text-[13px]">
                <div className="flex items-center gap-2">
                  <CheckBadge result={c.result} />
                  <span className="text-fg">{c.title}</span>
                </div>
                <p className="mt-1 line-clamp-3 text-fg-muted">{c.observations}</p>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Recent experiments">
          <ul className="-mx-2 space-y-0.5">
            {overview.experiments.slice(0, 6).map((e) => (
              <li key={e.id}>
                <button
                  type="button"
                  onClick={() => onOpen(e.id)}
                  className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-white/[0.03]"
                >
                  <span className="truncate text-fg-muted">
                    {e.name ?? "Run"} <span className="text-fg-faint">v{e.strategy_version}</span>
                  </span>
                  {e.verdict ? <VerdictBadge verdict={e.verdict} /> : <RunStatus status={e.status} />}
                </button>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <AskJarvis />
    </div>
  );
}

function GettingStarted({ overview, go }: { overview: QlOverview; go: (s: Section) => void }) {
  const steps = [
    {
      n: 1,
      title: "Bring data",
      text: "Import CSV or Parquet bars with provider and licence — or load a synthetic fixture to see the mechanics.",
      done: overview.counts.datasets > 0,
      action: () => go("Datasets"),
      label: "Open Data Lab",
    },
    {
      n: 2,
      title: "Write the hypothesis",
      text: "An SMA-crossover StrategySpec, every assumption visible, frozen by its hash before any run.",
      done: overview.counts.strategies > 0,
      action: () => go("Strategies"),
      label: "Open Architect",
    },
    {
      n: 3,
      title: "Run it once",
      text: "Signal at the close, fill at the next open, costs included, last part held out — then a critical verdict.",
      done: overview.counts.experiments > 0,
      action: () => go("Strategies"),
      label: "Run an experiment",
    },
  ];
  return (
    <div className="space-y-5" data-testid="ql-empty">
      <Card>
        <p className="text-lg font-light text-fg">No experiment yet.</p>
        <p className="mt-1 max-w-[680px] text-[13px] text-fg-muted">
          QuantLab doesn't show a curve until a real run produced one. Three steps, in this order:
        </p>
        <ol className="mt-5 grid gap-4 md:grid-cols-3">
          {steps.map((s) => (
            <li key={s.n} className="rounded-xl border border-ql-border bg-ql-raised p-4">
              <p className="font-mono text-2xs text-fg-faint">
                {s.done ? "✓ DONE" : `STEP ${s.n}`}
              </p>
              <p className="mt-1 text-[15px] text-fg">{s.title}</p>
              <p className="mt-1 text-[13px] text-fg-muted">{s.text}</p>
              <Button className="mt-3" onClick={s.action}>
                {s.label}
              </Button>
            </li>
          ))}
        </ol>
      </Card>
      <AskJarvis />
    </div>
  );
}

export function AskJarvis() {
  const [text, setText] = useState("");
  const [trace, setTrace] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reply = useJarvis((s) => (trace && s.lastMessage?.traceId === trace ? s.lastMessage.text : null));
  const ask = async (question: string) => {
    if (!question.trim()) return;
    setError(null);
    try {
      const accepted = await api.chat(question.trim());
      setTrace(accepted.trace_id);
      setText("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "JARVIS isn't reachable.");
    }
  };
  const suggestions = [
    "What can I conclude from the latest QuantLab run?",
    "Which assumption in the latest experiment is the weakest?",
    "What should the next pre-registered test be?",
  ];
  return (
    <Card title="Command JARVIS">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(text);
        }}
      >
        <input
          className={inputClass}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask about the research — JARVIS answers from the QuantLab record, critically."
          aria-label="Ask JARVIS about QuantLab"
        />
        <Button type="submit" variant="primary" disabled={!text.trim()}>
          Ask
        </Button>
      </form>
      <div className="mt-3 flex flex-wrap gap-2">
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            className="rounded-lg border border-ql-border px-2.5 py-1 text-2xs text-fg-muted hover:border-ql/40 hover:text-fg"
            onClick={() => void ask(s)}
          >
            {s}
          </button>
        ))}
      </div>
      {trace && (
        <p className="selectable mt-4 whitespace-pre-wrap text-[13px] leading-relaxed text-fg">
          {reply ?? <span className="text-fg-faint">JARVIS is thinking…</span>}
        </p>
      )}
      {error && <p className="mt-3 text-[13px] text-ql-danger">{error}</p>}
    </Card>
  );
}

// Strategies ----------------------------------------------------------------------------

function Strategies({
  strategies,
  datasets,
  template,
  onChanged,
  onStarted,
}: {
  strategies: QlStrategy[];
  datasets: QlDataset[];
  template: QlOverview["template"] | null;
  onChanged: () => Promise<void>;
  onStarted: (exp: QlExperiment) => void;
}) {
  const [mode, setMode] = useState<"list" | "new" | { edit: QlStrategy }>(strategies.length ? "list" : "new");
  if (mode === "new" || typeof mode === "object") {
    return (
      <Architect
        key={typeof mode === "object" ? mode.edit.id : "new"}
        template={template}
        editing={typeof mode === "object" ? mode.edit : null}
        onSaved={() => {
          void onChanged();
          setMode("list");
        }}
        onCancel={() => setMode("list")}
      />
    );
  }
  return (
    <div className="space-y-5" data-testid="ql-strategies">
      <div className="flex justify-end">
        <Button variant="primary" onClick={() => setMode("new")}>
          New strategy
        </Button>
      </div>
      {strategies.length === 0 && <p className="text-[13px] text-fg-faint">No strategies yet.</p>}
      {strategies.map((s) => (
        <StrategyCard key={s.id} strategy={s} datasets={datasets} onEdit={() => setMode({ edit: s })} onStarted={onStarted} />
      ))}
    </div>
  );
}

function StrategyCard({
  strategy,
  datasets,
  onEdit,
  onStarted,
}: {
  strategy: QlStrategy;
  datasets: QlDataset[];
  onEdit: () => void;
  onStarted: (exp: QlExperiment) => void;
}) {
  const latest = strategy.versions[strategy.versions.length - 1] as QlStrategy["versions"][number];
  const usable = datasets.filter((d) => d.usable);
  const matching = usable.filter(
    (d) => d.symbol.toUpperCase() === latest.spec.instrument.symbol.toUpperCase() && d.frequency === latest.spec.timeframe.bar_interval,
  );
  const [version, setVersion] = useState(latest.id);
  const [dataset, setDataset] = useState(matching[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chosen = strategy.versions.find((v) => v.id === version) ?? latest;


  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      onStarted(await api.qlRun(version, dataset));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't start the run.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card testId="ql-strategy-card">
      <div className="flex flex-wrap items-start gap-3">
        <div className="mr-auto min-w-0">
          <p className="text-lg font-light text-fg">{strategy.name}</p>
          <p className="mt-0.5 max-w-[760px] text-[13px] text-fg-muted">{strategy.hypothesis}</p>
        </div>
        <Button onClick={onEdit}>New version</Button>
      </div>
      <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div>
          <p className="mb-2 label">Versions · frozen</p>
          <ul className="space-y-2">
            {strategy.versions
              .slice()
              .reverse()
              .map((v) => (
                <li key={v.id} className={cx("rounded-xl border p-3", v.id === version ? "border-ql/40 bg-ql/5" : "border-ql-border")}>
                  <button type="button" className="block w-full text-left" onClick={() => setVersion(v.id)}>
                    <p className="flex items-center gap-2 text-[13px] text-fg">
                      v{v.number} <Hash value={v.spec_sha256} />
                      <span className="text-2xs text-fg-faint">{ago(v.created_at)}</span>
                    </p>
                    <p className="mt-1 text-[12px] leading-relaxed text-fg-muted">{v.summary}</p>
                    {v.warnings.map((w) => (
                      <p key={w} className="mt-1 text-2xs text-ql-warning">
                        ! {w}
                      </p>
                    ))}
                  </button>
                </li>
              ))}
          </ul>
        </div>
        <div className="rounded-xl border border-ql-border bg-ql-raised p-4">
          <p className="mb-3 label">Run v{chosen.number}</p>
          {usable.length === 0 ? (
            <p className="text-[13px] text-fg-faint">Import an accepted dataset first.</p>
          ) : (
            <>
              <select className={inputClass} value={dataset} onChange={(e) => setDataset(e.target.value)} data-testid="ql-run-dataset">
                <option value="">Choose a dataset…</option>
                {usable.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.symbol} · {d.frequency} · {d.rows} bars{d.synthetic ? " · SYNTHETIC" : ""}
                  </option>
                ))}
              </select>
              <p className="mt-2 text-2xs text-fg-faint">
                Same version + same data = same experiment id: running twice returns the first result.
              </p>
              {error && <p className="mt-2 text-[13px] text-ql-danger">{error}</p>}
              <Button variant="primary" className="mt-3 w-full" disabled={!dataset || busy} onClick={run} data-testid="ql-run">
                {busy ? "Starting…" : "Run experiment"}
              </Button>
            </>
          )}
        </div>
      </div>
    </Card>
  );
}

// Experiments ---------------------------------------------------------------------------

function ExperimentList({
  experiments,
  strategies,
  datasets,
  onOpen,
}: {
  experiments: QlExperimentRow[];
  strategies: QlStrategy[];
  datasets: QlDataset[];
  onOpen: (id: string) => void;
}) {
  const names = new Map(strategies.flatMap((s) => s.versions.map((v) => [v.id, s.name] as const)));
  const sets = new Map(datasets.map((d) => [d.id, d]));
  return (
    <Card title={`Experiment registry · ${experiments.length}`} testId="ql-registry">
      {experiments.length === 0 ? (
        <p className="text-[13px] text-fg-faint">No experiments yet. Every run — good, bad or failed — is recorded here.</p>
      ) : (
        <table className="w-full text-left text-[13px]">
          <thead className="text-2xs text-fg-faint">
            <tr>
              {["Strategy", "Data", "Status / verdict", "Net", "OOS net", "Max DD", "Created", "Id"].map((h) => (
                <th key={h} className="px-2 py-1.5 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {experiments.map((e) => (
              <tr
                key={e.id}
                tabIndex={0}
                onClick={() => onOpen(e.id)}
                onKeyDown={(ev) => ev.key === "Enter" && onOpen(e.id)}
                className="cursor-pointer border-t border-ql-border/60 hover:bg-white/[0.03]"
                data-testid="ql-experiment-row"
              >
                <td className="px-2 py-2 text-fg">
                  {e.name ?? names.get(e.strategy_version_id) ?? "—"} <span className="text-fg-faint">v{e.strategy_version}</span>
                </td>
                <td className="px-2 py-2 text-fg-muted">
                  {sets.get(e.dataset_id)?.symbol ?? "—"}
                  {e.synthetic && <span className="ml-2 font-mono text-2xs text-ql-warning">SYNTHETIC</span>}
                </td>
                <td className="px-2 py-2">{e.verdict ? <VerdictBadge verdict={e.verdict} /> : <RunStatus status={e.status} />}</td>
                <td className="px-2 py-2 font-mono text-fg-muted tabular">{e.net_pnl === null ? "—" : money(e.net_pnl, e.currency ?? "")}</td>
                <td className="px-2 py-2 font-mono text-fg-muted tabular">{e.oos_net_pnl === null ? "—" : money(e.oos_net_pnl, e.currency ?? "")}</td>
                <td className="px-2 py-2 font-mono text-fg-muted tabular">{e.max_drawdown_pct === null ? "—" : pct(e.max_drawdown_pct, 1, false)}</td>
                <td className="px-2 py-2 text-fg-faint">{ago(e.created_at)}</td>
                <td className="px-2 py-2">
                  <Hash value={e.id} length={10} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function ExperimentPane({ id, tick, onBack }: { id: string; tick: string | null; onBack: () => void }) {
  const [experiment, setExperiment] = useState<QlExperiment | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api
      .qlExperiment(id)
      .then((e) => {
        if (live) {
          setExperiment(e);
          setError(null);
        }
      })
      .catch((err) => live && setError(err instanceof ApiError ? err.message : "Not found."));
    return () => {
      live = false;
    };
  }, [id, tick]);
  // A run in progress is polled as well, in case an event is missed.
  useEffect(() => {
    if (!experiment || (experiment.status !== "queued" && experiment.status !== "running")) return;
    const timer = window.setInterval(() => {
      api.qlExperiment(id).then(setExperiment).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [id, experiment]);

  return (
    <div className="space-y-4">
      <button type="button" className="text-[13px] text-fg-muted hover:text-fg" onClick={onBack}>
        ← Registry
      </button>
      {error && <p className="text-[13px] text-ql-danger">{error}</p>}
      {experiment && experiment.id === id && (
        <ExperimentView
          experiment={experiment}
          onRerun={async () => {
            setExperiment(await api.qlRun(experiment.manifest.strategy_version_id, experiment.manifest.dataset_id));
          }}
        />
      )}
    </div>
  );
}

// Reports -------------------------------------------------------------------------------

function Reports({ experiments, onOpen }: { experiments: QlExperimentRow[]; onOpen: (id: string) => void }) {
  const completed = experiments.filter((e) => e.status === "completed");
  const [details, setDetails] = useState<Record<string, QlExperiment>>({});
  useEffect(() => {
    let live = true;
    Promise.all(completed.slice(0, 10).map((e) => api.qlExperiment(e.id)))
      .then((list) => live && setDetails(Object.fromEntries(list.map((x) => [x.id, x]))))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [completed.map((e) => e.id).join()]);
  if (completed.length === 0) {
    return <p className="text-[13px] text-fg-faint">Reports appear once an experiment has completed.</p>;
  }
  return (
    <div className="space-y-5" data-testid="ql-reports">
      {completed.slice(0, 10).map((e) => {
        const detail = details[e.id];
        return detail ? (
          <div key={e.id}>
            <div className="mb-2 flex items-center gap-2 text-[13px] text-fg-muted">
              <button type="button" className="hover:text-fg" onClick={() => onOpen(e.id)}>
                {e.name} v{e.strategy_version} on {e.symbol} →
              </button>
              {e.synthetic && <SyntheticBadge />}
            </div>
            <Assessment experiment={detail} />
          </div>
        ) : null;
      })}
    </div>
  );
}
