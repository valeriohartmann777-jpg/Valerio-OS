import type { QrChart, QrMetrics, QrRun, QrRunRow, QrTest } from "@jarvis/protocol";
import { type ReactElement, useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../../../lib/api";
import { Button, cx } from "../../ui/primitives";
import { EquityChart } from "../EquityChart";
import { Badge, Card, Metric } from "../ui";
import { CandleChart, Interval, SignedBars } from "./charts";
import {
  Empty,
  FitnessBadge,
  FixtureBadge,
  Kv,
  Markdown,
  Progress,
  RunBadge,
  RunPicker,
  TestBadge,
  VerdictTag,
  download,
  fmt,
  share,
  tone,
  usd,
  type Mode,
} from "./common";

/** Loads a run and keeps it fresh while it is queued or running. */
export function useRun(runId: string | null, tick: string | null): [QrRun | null, () => void] {
  const [run, setRun] = useState<QrRun | null>(null);
  const load = useCallback(() => {
    if (!runId) {
      setRun(null);
      return;
    }
    api.qrRun(runId).then(setRun).catch(() => setRun(null));
  }, [runId]);
  useEffect(() => {
    load();
  }, [load, tick]);
  const active = run?.status === "QUEUED" || run?.status === "RUNNING";
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(load, 1000);
    return () => clearInterval(timer);
  }, [active, load]);
  return [run, load];
}

function RunHeader({ run, runs, onPick, only }: { run: QrRun | null; runs: QrRunRow[]; onPick: (id: string) => void; only?: "validation" }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <RunPicker runs={runs} value={run?.status === "COMPLETED" ? run.id : null} onChange={onPick} only={only} />
      {run && (
        <>
          <RunBadge status={run.status} />
          {run.fixture && <FixtureBadge />}
          {run.summary?.verdict && <VerdictTag verdict={run.summary.verdict.verdict} />}
          <span className="font-mono text-2xs text-fg-faint">{run.id}</span>
        </>
      )}
    </div>
  );
}

function Running({ run, onCancel }: { run: QrRun; onCancel: () => void }) {
  return (
    <Card title={`${run.kind === "validation" ? "Validation" : "Backtest"} in progress`} testId="qr-running">
      <Progress value={run.progress} label={run.stage ?? (run.status === "QUEUED" ? "queued" : "running")} />
      <Button variant="danger" className="mt-3" onClick={onCancel}>
        Cancel run
      </Button>
    </Card>
  );
}

function Failed({ run }: { run: QrRun }) {
  return (
    <Card title="This run didn't complete" testId="qr-failed">
      <p className="text-[13px] text-ql-danger">{run.error ?? run.status}</p>
      <p className="mt-1 text-2xs text-fg-faint">No partial result is shown — a failed run has no numbers.</p>
    </Card>
  );
}

function blockerFor(run: QrRun | null, onCancel: () => void): ReactElement | null {
  if (!run) return null;
  if (run.status === "QUEUED" || run.status === "RUNNING") return <Running run={run} onCancel={onCancel} />;
  if (run.status !== "COMPLETED" || !run.summary) return <Failed run={run} />;
  return null;
}

// Backtest Lab ---------------------------------------------------------------------------------

export function BacktestLab({ runId, runs, tick, mode, onPick, onTrade }: { runId: string | null; runs: QrRunRow[]; tick: string | null; mode: Mode; onPick: (id: string) => void; onTrade: (n: number) => void }) {
  const [run, reload] = useRun(runId, tick);
  const [chart, setChart] = useState<QrChart | null>(null);
  useEffect(() => {
    if (run?.status !== "COMPLETED") return;
    let live = true;
    api.qrChart(run.id).then((c) => live && setChart(c)).catch(() => live && setChart(null));
    return () => {
      live = false;
    };
  }, [run?.id, run?.status]);
  const blocker = blockerFor(run, () => run && void api.qrCancel(run.id).then(reload));
  if (!runId) return <Empty title="No run selected" testId="qr-backtest-empty">Save a strategy in the Strategy Studio and run it on a verified dataset.<div className="mt-3"><RunPicker runs={runs} value={null} onChange={onPick} /></div></Empty>;
  return (
    <div className="space-y-5" data-testid="qr-backtest">
      <RunHeader run={run} runs={runs} onPick={onPick} />
      {blocker ?? (run?.summary && <BacktestBody run={run} chart={chart} mode={mode} onTrade={onTrade} />)}
    </div>
  );
}

function BacktestBody({ run, chart, mode, onTrade }: { run: QrRun; chart: QrChart | null; mode: Mode; onTrade: (n: number) => void }) {
  const s = run.summary!;
  const m = s.metrics;
  const oos = s.segments.oos;
  const candles = useMemo(() => (chart?.candles ?? []).map((c) => ({ label: c.session, o: c.o, h: c.h, l: c.l, c: c.c })), [chart]);
  const index = useMemo(() => new Map(candles.map((c, i) => [c.label, i])), [candles]);
  const markers = useMemo(
    () =>
      (chart?.markers ?? []).flatMap((t) => {
        const i = index.get(t.session);
        if (i === undefined) return [];
        const dir = t.direction === "LONG" ? 1 : -1;
        return [
          { index: i, price: t.entry, kind: "entry" as const, direction: dir as 1 | -1, label: `#${t.number} ${t.direction} entry ${t.entry}` },
          { index: i, price: t.exit, kind: "exit" as const, direction: dir as 1 | -1, win: t.net > 0, label: `#${t.number} exit ${t.exit} · ${usd(t.net, true)}` },
        ];
      }),
    [chart, index],
  );
  const equity = useMemo(() => {
    if (!chart) return [];
    const oosFirst = chart.split?.oos.first ?? null;
    return chart.equity.map((p) => {
      const peak = p.equity - p.drawdown;
      const segment: "oos" | "train" = oosFirst && p.t.slice(0, 10) >= oosFirst ? "oos" : "train";
      return { ts: p.t, equity: p.equity, drawdown: peak > 0 ? p.drawdown / peak : 0, segment };
    });
  }, [chart]);
  return (
    <>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Card title={`${s.name} · v${run.manifest.version_number}`} aside={<FitnessBadge status={s.fitness.status} />}>
          <dl className="grid grid-cols-3 gap-4">
            <Metric label="Net P&L (conservative)" value={<span className={tone(m.net_pnl)} data-testid="qr-net">{usd(m.net_pnl, true)}</span>} sub={`${m.trades} trades · fees ${usd(m.fees)}`} />
            <Metric label="Out-of-sample net" value={<span className={tone(oos.net_pnl)}>{usd(oos.net_pnl, true)}</span>} sub={`${oos.trades} OOS trades`} hint="Sessions after the in-sample period; nothing was tuned on them." />
            <Metric label="Max drawdown" value={usd(m.max_drawdown_intraday ?? m.max_drawdown)} sub={share(m.max_drawdown_intraday_pct ?? m.max_drawdown_pct, 2)} />
            <Metric label="Win rate" value={share(m.win_rate)} sub={`payoff ${fmt(m.payoff_ratio)}`} />
            <Metric label="Profit factor" value={fmt(m.profit_factor)} hint="Undefined (n/a) without losing trades." />
            <Metric label="Sharpe (OOS, daily √252)" value={fmt(oos.sharpe)} hint="Risk-free 0; needs ≥ 20 sessions." />
          </dl>
          <p className="mt-3 text-2xs text-fg-faint">
            Optimistic bound (ambiguous minutes resolved favourably): {usd(s.optimistic.net_pnl, true)} overall,{" "}
            {usd(s.optimistic.oos_net, true)} out-of-sample. Results from {s.dataset.symbol} {s.dataset.start} → {s.dataset.end}; the
            final {s.split.holdout.sessions} sessions are {s.segments.holdout ? "included as holdout" : "sealed"}.
          </p>
        </Card>
        <Card title="Costs & execution">
          <dl>
            <Kv label="Fees (both sides)">{usd(m.fees)}</Kv>
            <Kv label="Modelled slippage">{usd(m.slippage_cost)} · {s.risk.slippage_ticks} tick(s) per fill</Kv>
            <Kv label="Cost share of gross">{share(m.cost_share_of_gross)}</Kv>
            <Kv label="Exit reasons">{Object.entries(m.exit_reasons ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</Kv>
            <Kv label="Ambiguous minutes">{m.ambiguous_trades} trade(s) · {m.ambiguous_sessions} day(s) excluded</Kv>
            <Kv label="Exposure">{share(m.exposure)} of session time · avg hold {fmt(m.avg_hold_minutes, 1)} min</Kv>
          </dl>
        </Card>
      </div>
      <Card title="Sessions and trades" aside={<span className="text-2xs text-fg-faint">one candle per strategy session (New York regular hours), prices unadjusted</span>}>
        {chart ? (
          <CandleChart
            candles={candles}
            markers={markers}
            ariaLabel={`Session candles of ${s.dataset.symbol} with ${chart.markers.length} trades marked`}
            testId="qr-session-chart"
            detail={(i) =>
              (chart.markers ?? [])
                .filter((t) => index.get(t.session) === i)
                .map((t) => `#${t.number} ${t.direction} ${t.entry} → ${t.exit} · ${usd(t.net, true)} (${t.segment})`)
            }
          />
        ) : (
          <p className="text-[13px] text-fg-faint">Loading…</p>
        )}
      </Card>
      <Card title="Equity net of costs · in-sample / out-of-sample">
        {equity.length > 1 ? (
          <EquityChart points={equity} initialCash={s.metrics.capital ?? chart?.capital ?? 0} currency="USD" oosStart={chart?.split?.oos.first ?? null} totalPoints={equity.length} />
        ) : (
          <p className="text-[13px] text-fg-faint">Not enough points.</p>
        )}
      </Card>
      <SegmentTable s={s} />
      {mode !== "Simple" && <RecentTrades runId={run.id} onTrade={onTrade} />}
    </>
  );
}

function SegmentTable({ s }: { s: NonNullable<QrRun["summary"]> }) {
  const parts: [string, QrMetrics | undefined][] = [
    ["In-sample", s.segments.insample],
    ["Out-of-sample", s.segments.oos],
    ["Holdout", s.segments.holdout],
  ];
  const rows: [string, (m: QrMetrics) => string][] = [
    ["Sessions", (m) => fmt(m.sessions, 0)],
    ["Trades", (m) => fmt(m.trades, 0)],
    ["Net P&L", (m) => usd(m.net_pnl, true)],
    ["Expectancy / trade", (m) => usd(m.expectancy, true)],
    ["Expectancy (R)", (m) => fmt(m.expectancy_r, 3)],
    ["Win rate", (m) => share(m.win_rate)],
    ["Profit factor", (m) => fmt(m.profit_factor)],
    ["Sharpe", (m) => fmt(m.sharpe)],
    ["Sortino", (m) => fmt(m.sortino)],
    ["Max drawdown", (m) => usd(m.max_drawdown)],
    ["Avg MAE / MFE (ticks)", (m) => `${fmt(m.avg_mae_ticks, 1)} / ${fmt(m.avg_mfe_ticks, 1)}`],
    ["Max losing streak", (m) => fmt(m.max_loss_streak, 0)],
  ];
  return (
    <Card title="By segment" testId="qr-segments">
      <table className="w-full text-left text-[13px]">
        <thead className="text-2xs text-fg-faint">
          <tr>
            <th className="py-1 font-medium">Metric</th>
            {parts.map(([label]) => (
              <th key={label} className="text-right font-medium">{label}</th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular text-fg-muted">
          {rows.map(([label, f]) => (
            <tr key={label} className="border-t border-ql-border/60">
              <td className="py-1.5 text-fg">{label}</td>
              {parts.map(([part, m]) => (
                <td key={part} className="text-right font-mono text-2xs">{m ? f(m) : "sealed"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-2xs text-fg-faint">
        n/a means undefined — e.g. a profit factor without losses or a Sharpe ratio on fewer than 20 sessions. Nothing is
        filled in.
      </p>
    </Card>
  );
}

function RecentTrades({ runId, onTrade }: { runId: string; onTrade: (n: number) => void }) {
  const [rows, setRows] = useState<Awaited<ReturnType<typeof api.qrTrades>> | null>(null);
  useEffect(() => {
    void api.qrTrades(runId, 0, 12).then(setRows);
  }, [runId]);
  if (!rows) return null;
  return (
    <Card title={`Trades (${rows.total})`} aside={<span className="text-2xs text-fg-faint">all of them in the Trade Explorer</span>}>
      <table className="w-full text-left text-[12px]">
        <tbody className="tabular text-fg-muted">
          {rows.rows.map((t) => (
            <tr key={`${t.segment}-${t.number}`} className="cursor-pointer border-t border-ql-border/60 hover:bg-white/[0.03]" onClick={() => onTrade(t.number)}>
              <td className="py-1 font-mono text-2xs text-fg-faint">#{t.number}</td>
              <td>{t.session}</td>
              <td>{t.direction}</td>
              <td className="font-mono text-2xs">{t.entry_price} → {t.exit_price}</td>
              <td>{t.exit_reason}</td>
              <td className={cx("text-right", tone(t.net))}>{usd(t.net, true)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

// Validation -----------------------------------------------------------------------------------

export function ValidationLab({ runId, runs, tick, mode, onPick, onRun }: { runId: string | null; runs: QrRunRow[]; tick: string | null; mode: Mode; onPick: (id: string) => void; onRun: (id: string) => void }) {
  const [run, reload] = useRun(runId, tick);
  const blocker = blockerFor(run, () => run && void api.qrCancel(run.id).then(reload));
  if (!runId) return <Empty title="No validation yet" testId="qr-validation-empty">Run a full validation from the Strategy Studio.<div className="mt-3"><RunPicker runs={runs} value={null} onChange={onPick} only="validation" /></div></Empty>;
  return (
    <div className="space-y-5" data-testid="qr-validation">
      <RunHeader run={run} runs={runs} onPick={onPick} />
      {blocker ?? (run?.summary && <ValidationBody run={run} mode={mode} onRun={onRun} />)}
    </div>
  );
}

function ValidationBody({ run, mode, onRun }: { run: QrRun; mode: Mode; onRun: (id: string) => void }) {
  const s = run.summary!;
  const [open, setOpen] = useState<string | null>(null);
  if (run.kind !== "validation") {
    return (
      <Card title="This run is a backtest">
        <p className="text-[13px] text-fg-muted">A backtest has no gates and no verdict. Run the full validation on the same version and dataset.</p>
        <Button
          className="mt-3"
          variant="primary"
          onClick={async () => onRun((await api.qrStart({ version_id: run.version_id, dataset_id: run.dataset_id, kind: "validation" })).id)}
          data-testid="qr-validate-this"
        >
          Run full validation
        </Button>
      </Card>
    );
  }
  const v = s.verdict!;
  return (
    <>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <Card title="Evidence verdict" aside={<VerdictTag verdict={v.verdict} testId="qr-verdict-main" />} testId="qr-verdict-card">
          {v.would_be && (
            <p className="mb-2 text-[13px] text-ql-warning">
              Synthetic data: capped at Insufficient evidence. The gates alone would say <strong>{v.would_be.replace(/_/g, " ").toLowerCase()}</strong>.
            </p>
          )}
          <ul className="space-y-1 text-[13px] text-fg">
            {v.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          {v.next_steps.length > 0 && (
            <>
              <p className="label mb-1 mt-4">Next steps</p>
              <ul className="space-y-1 text-[13px] text-fg-muted">
                {v.next_steps.map((n) => (
                  <li key={n}>→ {n}</li>
                ))}
              </ul>
            </>
          )}
          <p className="mt-4 text-2xs text-fg-faint">{v.never}</p>
        </Card>
        <SplitCard run={run} onRun={onRun} />
      </div>
      <Card title="Evidence matrix" aside={<span className="text-2xs text-fg-faint">click a test for its assumptions and evidence</span>} testId="qr-matrix">
        <table className="w-full text-left text-[13px]">
          <thead className="text-2xs text-fg-faint">
            <tr>
              <th className="py-1 font-medium">Test</th>
              <th className="font-medium">Status</th>
              <th className="font-medium">Reading</th>
            </tr>
          </thead>
          <tbody>
            {s.tests.map((t) => (
              <TestRow key={t.id} test={t} open={open === t.id} onToggle={() => setOpen(open === t.id ? null : t.id)} mode={mode} />
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}

function TestRow({ test, open, onToggle, mode }: { test: QrTest; open: boolean; onToggle: () => void; mode: Mode }) {
  return (
    <>
      <tr className="cursor-pointer border-t border-ql-border/60 align-top hover:bg-white/[0.02]" onClick={onToggle} data-testid="qr-test" data-id={test.id} data-status={test.status}>
        <td className="py-2 pr-3 text-fg">{test.name}</td>
        <td className="py-2 pr-3"><TestBadge status={test.status} /></td>
        <td className="py-2 text-fg-muted">{headline(test)}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={3} className="pb-4">
            <div className="rounded-xl border border-ql-border bg-ql-raised p-4">
              <TestDetail test={test} />
              <div className="mt-3 grid gap-3 text-[13px] md:grid-cols-2">
                <div>
                  <p className="label mb-1">Assumptions</p>
                  {test.assumptions.length ? test.assumptions.map((a) => <p key={a} className="text-fg-muted">{a}</p>) : <p className="text-fg-faint">—</p>}
                </div>
                <div>
                  <p className="label mb-1">Needs</p>
                  <p className="text-fg-muted">{test.requirements}</p>
                  <p className="label mb-1 mt-3">How to read it</p>
                  <p className="text-fg-muted">{test.interpretation}</p>
                </div>
              </div>
              {mode === "Institutional" && (
                <pre className="selectable mt-3 max-h-48 overflow-auto rounded-lg bg-black/30 p-2 font-mono text-[10px] text-fg-faint">{JSON.stringify(test.metric, null, 1)}</pre>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

type Any = Record<string, unknown>;

function headline(t: QrTest): string {
  const m = t.metric as Any;
  switch (t.id) {
    case "OUT_OF_SAMPLE":
      return `${m.oos_trades} OOS trades, net ${usd(m.oos_net as number, true)}`;
    case "BASELINE":
      return `Strategy ${usd(m.strategy_oos_net as number, true)} vs naive ${usd(m.benchmark_oos_net as number, true)} (OOS)`;
    case "COST_STRESS":
      return ((m.scenarios as Any[]) ?? []).map((r) => `${r.multiplier}× → ${usd(r.oos_net as number, true)}`).join(" · ");
    case "WALK_FORWARD":
      return m.windows ? `${(m.windows as unknown[]).length} windows, stitched test net ${usd(m.total_test_net as number, true)}` : t.requirements;
    case "PARAMETER_SENSITIVITY":
      return m.grid ? `${share(m.share_positive as number)} of ${(m.grid as unknown[]).length} grid points positive in-sample${m.isolated_peak ? " · isolated peak" : ""}` : t.interpretation;
    case "BOOTSTRAP":
      return m.mean_daily_ci90 ? `90% CI of mean daily P&L ${usd((m.mean_daily_ci90 as number[])[0], true)} … ${usd((m.mean_daily_ci90 as number[])[1], true)}` : t.requirements;
    case "SELECTION_BIAS":
      return m.dsr !== null && m.dsr !== undefined ? `Deflated Sharpe probability ${fmt(m.dsr as number, 3)} after ${m.trials} variant(s)` : `${m.trials ?? "?"} variant(s); ${m.reason}`;
    case "AMBIGUITY":
      return `OOS ${usd(m.oos_conservative_net as number, true)} conservative vs ${usd(m.oos_optimistic_net as number, true)} optimistic`;
    case "HOLDOUT":
      return m.holdout_net !== undefined ? `Holdout ${m.holdout_trades} trades, net ${usd(m.holdout_net as number, true)}${m.independent ? "" : " · not independent"}` : t.assumptions[0] ?? "sealed";
    case "DATA_INTEGRITY":
      return `Fitness: ${String(m.fitness).replace(/_/g, " ").toLowerCase()}`;
    case "LEAKAGE":
      return m.identical === undefined ? t.requirements : m.identical ? `${m.trades_before_cut} earlier trades unchanged when the future was perturbed` : "past trades changed — look-ahead!";
    default:
      return t.interpretation;
  }
}

function TestDetail({ test }: { test: QrTest }) {
  const m = test.metric as Any;
  if (test.id === "WALK_FORWARD" && Array.isArray(m.windows)) {
    return <SignedBars items={(m.windows as Any[]).map((w) => ({ label: String(w.test).split(" → ")[0] ?? "", value: Number(w.test_net), note: `params ${JSON.stringify(w.params)} · ${w.test_trades} trades` }))} ariaLabel="Walk-forward test-window net P&L" testId="qr-wf-chart" />;
  }
  if (test.id === "COST_STRESS" && Array.isArray(m.scenarios)) {
    return <SignedBars items={(m.scenarios as Any[]).map((r) => ({ label: `${r.multiplier}× costs`, value: Number(r.oos_net), note: `${r.oos_trades} OOS trades` }))} ariaLabel="Out-of-sample net under cost stress" />;
  }
  if (test.id === "SUBPERIODS" && m.months) {
    return <SignedBars items={Object.entries(m.months as Record<string, number>).map(([k, v]) => ({ label: k, value: v }))} ariaLabel="Net P&L by month" />;
  }
  if (test.id === "BOOTSTRAP" && Array.isArray(m.mean_daily_ci90)) {
    const [lo, hi] = m.mean_daily_ci90 as number[];
    return (
      <div>
        <p className="text-[13px] text-fg-muted">Mean daily P&L, 90% interval (block bootstrap)</p>
        <Interval low={lo ?? 0} high={hi ?? 0} />
        <p className="text-2xs text-fg-faint">P(total ≤ 0) {share(m.probability_total_loss as number)} · 95th percentile drawdown {usd(m.max_drawdown_p95 as number)}</p>
      </div>
    );
  }
  if (test.id === "PARAMETER_SENSITIVITY" && Array.isArray(m.grid)) {
    const rows = m.grid as { params: Record<string, number>; is_net: number; trades: number }[];
    const max = Math.max(1, ...rows.map((r) => Math.abs(r.is_net)));
    return (
      <table className="w-full text-left text-[12px]" data-testid="qr-grid">
        <thead className="text-2xs text-fg-faint">
          <tr>
            <th className="py-1 font-medium">Parameters</th>
            <th className="text-right font-medium">In-sample net</th>
            <th className="text-right font-medium">Trades</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={JSON.stringify(r.params)} className="border-t border-ql-border/60">
              <td className="py-1 font-mono text-2xs text-fg-muted">{Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(", ")}</td>
              <td className="text-right font-mono text-2xs">
                <span
                  className="rounded px-1.5 py-0.5"
                  style={{ background: `color-mix(in srgb, ${r.is_net >= 0 ? "var(--color-ql)" : "var(--color-ql-danger)"} ${Math.round((Math.abs(r.is_net) / max) * 45)}%, transparent)` }}
                >
                  {usd(r.is_net, true)}
                </span>
              </td>
              <td className="text-right font-mono text-2xs text-fg-muted">{r.trades}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  if (test.id === "REGIMES" && m.oos_by_regime) {
    return (
      <div className="grid gap-4 md:grid-cols-2 text-[12px]">
        {(["oos_by_regime", "by_weekday_is_oos"] as const).map((key) => (
          <table key={key} className="w-full">
            <caption className="label mb-1 text-left">{key === "oos_by_regime" ? "Out-of-sample by volatility regime" : "By weekday (context only)"}</caption>
            <tbody>
              {Object.entries((m[key] as Record<string, { trades: number; net: number }>) ?? {}).map(([k, v]) => (
                <tr key={k} className="border-t border-ql-border/60">
                  <td className="py-1 text-fg">{k}</td>
                  <td className="text-right font-mono text-2xs text-fg-muted">{v.trades} trades</td>
                  <td className={cx("text-right font-mono text-2xs", tone(v.net))}>{usd(v.net, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
      </div>
    );
  }
  return null;
}

function SplitCard({ run, onRun }: { run: QrRun; onRun: (id: string) => void }) {
  const s = run.summary!;
  const sp = s.split;
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const total = sp.insample.sessions + sp.oos.sessions + sp.holdout.sessions + sp.embargo_sessions.length || 1;
  const parts = [
    { label: "In-sample", n: sp.insample.sessions, cls: "bg-ql-muted/40", range: `${sp.insample.first} → ${sp.insample.last}` },
    { label: "Out-of-sample", n: sp.oos.sessions, cls: "bg-ql/60", range: `${sp.oos.first} → ${sp.oos.last}` },
    { label: s.segments.holdout ? "Holdout (evaluated)" : "Holdout (sealed)", n: sp.holdout.sessions, cls: s.segments.holdout ? "bg-ql-warning/60" : "bg-white/[0.08]", range: `${sp.holdout.first} → ${sp.holdout.last}` },
  ];
  return (
    <Card title="Chronological split" testId="qr-split">
      <div className="flex h-3 overflow-hidden rounded-full" aria-hidden>
        {parts.map((p) => (
          <div key={p.label} className={cx(p.cls, "border-r-2 border-ql-surface")} style={{ width: `${(p.n / total) * 100}%` }} />
        ))}
      </div>
      <ul className="mt-3 space-y-1 text-[13px]">
        {parts.map((p) => (
          <li key={p.label} className="flex justify-between gap-3">
            <span className="text-fg">{p.label}</span>
            <span className="font-mono text-2xs text-fg-muted">{p.n} sessions · {p.range}</span>
          </li>
        ))}
        <li className="text-2xs text-fg-faint">{sp.embargo_sessions.length} embargo session(s) between segments are used by nothing.</li>
      </ul>
      {!s.segments.holdout && !run.include_holdout && sp.holdout.sessions > 0 && (
        <div className="mt-4 border-t border-ql-border pt-3" data-testid="qr-holdout">
          <p className="text-[13px] text-fg-muted">
            The holdout is untouched data. Evaluate it once, when the rules are final — every later look makes it less
            independent, and that is recorded.
          </p>
          <label className="mt-2 flex items-start gap-2 text-[13px] text-fg">
            <input type="checkbox" className="mt-1" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} data-testid="qr-holdout-confirm" />
            This version is final. Unseal the holdout for it.
          </label>
          <Button
            className="mt-2"
            variant="warning"
            disabled={!confirm || busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                const next = await api.qrStart({ version_id: run.version_id, dataset_id: run.dataset_id, kind: "validation", include_holdout: true, confirm_holdout: true });
                onRun(next.id);
              } catch (e) {
                setError(e instanceof ApiError ? e.message : "Couldn't start.");
              } finally {
                setBusy(false);
              }
            }}
            data-testid="qr-holdout-run"
          >
            Evaluate sealed holdout (once)
          </Button>
          {error && <p className="mt-2 text-[13px] text-ql-danger">{error}</p>}
        </div>
      )}
    </Card>
  );
}

// Risk & Execution -----------------------------------------------------------------------------

export function RiskExecution({ runId, runs, tick, onPick }: { runId: string | null; runs: QrRunRow[]; tick: string | null; onPick: (id: string) => void }) {
  const [run, reload] = useRun(runId, tick);
  const blocker = blockerFor(run, () => run && void api.qrCancel(run.id).then(reload));
  if (!runId) return <Empty title="No run selected"><RunPicker runs={runs} value={null} onChange={onPick} /></Empty>;
  const s = run?.summary;
  return (
    <div className="space-y-5" data-testid="qr-risk">
      <RunHeader run={run} runs={runs} onPick={onPick} />
      {blocker ??
        (s && (
          <>
            <div className="grid gap-5 lg:grid-cols-3">
              <Card title="Exposure & leverage">
                <dl>
                  <Kv label="Max notional">{usd(s.risk.max_notional)}</Kv>
                  <Kv label="Avg notional">{usd(s.risk.avg_notional)}</Kv>
                  <Kv label="Max leverage">{fmt(s.risk.max_leverage)}× of {usd(s.metrics.capital)}</Kv>
                  <Kv label="Margin check">
                    {s.risk.margin_required === null ? "not modelled (no margin given)" : `${usd(s.risk.margin_required)} — ${s.risk.margin_ok ? "within capital" : "exceeds capital"}`}
                  </Kv>
                  <Kv label="Time in market">{share(s.metrics.exposure)}</Kv>
                </dl>
              </Card>
              <Card title="Execution assumptions">
                <dl>
                  <Kv label="Fee per side">{usd(s.risk.fee_per_side)} per contract</Kv>
                  <Kv label="Slippage">{s.risk.slippage_ticks} tick(s) per market/stop fill</Kv>
                  <Kv label="Ambiguous trades">{s.metrics.ambiguous_trades} (conservative: stop first)</Kv>
                  <Kv label="Excluded days">{s.metrics.ambiguous_sessions} (both breakouts in one minute)</Kv>
                  <Kv label="Conservative vs optimistic">{usd(s.metrics.net_pnl, true)} vs {usd(s.optimistic.net_pnl, true)}</Kv>
                </dl>
              </Card>
              <Card title="Sessions">
                <dl>
                  {Object.entries(s.session_status).map(([k, v]) => (
                    <Kv key={k} label={k.replace(/_/g, " ").toLowerCase()}>{v}</Kv>
                  ))}
                </dl>
              </Card>
            </div>
            <Card title="Contracts traded">
              <table className="w-full text-left text-[13px]">
                <thead className="text-2xs text-fg-faint">
                  <tr>
                    {["Contract", "Instrument id", "Tick", "Tick value", "Multiplier", "Source"].map((h) => (
                      <th key={h} className="py-1 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="text-fg-muted">
                  {s.contracts.map((c) => (
                    <tr key={c.instrument_id} className="border-t border-ql-border/60">
                      <td className="py-1.5 text-fg">{c.raw_symbol}</td>
                      <td className="font-mono text-2xs">{c.instrument_id}</td>
                      <td className="font-mono text-2xs">{c.tick_size}</td>
                      <td className="font-mono text-2xs">${c.tick_value}</td>
                      <td className="font-mono text-2xs">×{c.multiplier}</td>
                      <td>
                        <Badge tone={c.provenance === "definition" ? "positive" : "warning"}>{c.provenance === "definition" ? "Provider definition" : "Assumed"}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
            <Card title="Ledger audit" aside={<span className="text-2xs text-fg-faint">re-derived from bars and ticks, independent of the engine's bookkeeping</span>}>
              <table className="w-full text-left text-[13px]" data-testid="qr-audit">
                <tbody>
                  {s.audit.conservative.map((c) => (
                    <tr key={c.id} className="border-t border-ql-border/60">
                      <td className="py-1.5 text-fg">{c.title}</td>
                      <td><Badge tone={c.result === "PASS" ? "positive" : "danger"} icon={c.result === "PASS" ? "✓" : "✕"}>{c.result}</Badge></td>
                      <td className="text-right font-mono text-2xs text-fg-muted">{c.checked} checked</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </>
        ))}
    </div>
  );
}

// Reports --------------------------------------------------------------------------------------

export function Reports({ runId, runs, onPick }: { runId: string | null; runs: QrRunRow[]; onPick: (id: string) => void }) {
  const [text, setText] = useState<string | null>(null);
  const [repro, setRepro] = useState<string | null>(null);
  useEffect(() => {
    setText(null);
    setRepro(null);
    if (!runId) return;
    api.qrReport(runId).then(setText).catch(() => setText(null));
  }, [runId]);
  const run = runs.find((r) => r.id === runId);
  return (
    <div className="space-y-5" data-testid="qr-reports">
      <div className="flex flex-wrap items-center gap-3">
        <RunPicker runs={runs} value={runId} onChange={onPick} />
        {text && run && (
          <>
            <Button onClick={() => download(`${run.name.replace(/\W+/g, "_")}_v${run.version_number}_${run.kind}.md`, text)} data-testid="qr-download-report">
              Download report (.md)
            </Button>
            <Button
              onClick={async () => {
                const r = await api.qrReproduce(run.id);
                setRepro(r.identical ? `Identical — ${r.trades} trades recomputed from the same manifest.` : "DIFFERENT — the rerun didn't match.");
              }}
              data-testid="qr-reproduce"
            >
              Reproduce
            </Button>
            {repro && <span className={cx("text-[13px]", repro.startsWith("Identical") ? "text-ql-positive" : "text-ql-danger")} data-testid="qr-repro">{repro}</span>}
          </>
        )}
      </div>
      {!runId ? (
        <Empty title="Pick a completed run">Reports contain derived statistics, rules and provenance — never the raw licensed market data.</Empty>
      ) : text ? (
        <Card testId="qr-report">
          <Markdown text={text} />
        </Card>
      ) : (
        <p className="text-[13px] text-fg-faint">Loading…</p>
      )}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  return <p className="text-[13px] text-ql-danger">{error instanceof ApiError ? error.message : "Something went wrong."}</p>;
}
