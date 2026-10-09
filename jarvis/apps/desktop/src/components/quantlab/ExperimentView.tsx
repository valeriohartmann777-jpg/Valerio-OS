import type {
  QlCheck,
  QlEquity,
  QlExperiment,
  QlLedger,
  QlOrder,
  QlReproduction,
  QlSegmentMetrics,
  QlTrade,
} from "@jarvis/protocol";
import { type ReactNode, useEffect, useState } from "react";

import { ApiError, api } from "../../lib/api";
import { Button, cx } from "../ui/primitives";
import { EquityChart } from "./EquityChart";
import {
  Badge,
  Card,
  CheckBadge,
  Hash,
  Metric,
  Provisional,
  ResearchOnly,
  RunStatus,
  SyntheticBadge,
  VerdictBadge,
  money,
  num,
  pct,
  utc,
} from "./ui";

/** One experiment: its state, the evidence, and JARVIS's critical read. */

const TABS = ["Overview", "Trades", "Assumptions", "Validation", "Audit"] as const;
type Tab = (typeof TABS)[number];
const STAGES = ["loading_snapshot", "causal_simulation", "writing_artifacts"];

const WHY = {
  net: "Final equity minus starting cash, after every fee and slippage — including an open position marked at the last close.",
  oos: "The out-of-sample part was fixed before the run and never used to choose parameters. Only it says anything about generalisation.",
  dd: "Largest fall from a peak of the equity curve (at bar closes). What you would have had to sit through.",
  trades: "Closed round trips. Below 30, win rates and averages are mostly noise.",
  exposure: "Share of bars with a position open. Compare returns only at similar exposure.",
  benchmark: "Buy & hold price return over the same bars — no costs, not exposure-matched.",
};

export function ExperimentView({ experiment, onRerun }: { experiment: QlExperiment; onRerun: () => void }) {
  const [tab, setTab] = useState<Tab>("Overview");
  const s = experiment.summary;
  const done = experiment.status === "completed" && s;

  return (
    <div className="space-y-5" data-testid="ql-experiment" data-status={experiment.status}>
      <Card>
        <div className="flex flex-wrap items-start gap-3">
          <div className="mr-auto min-w-0">
            <p className="label">Experiment</p>
            <h2 className="mt-1 truncate text-xl font-light text-fg">
              {experiment.strategy_name ?? "Strategy"}{" "}
              <span className="text-fg-muted">v{experiment.strategy_version}</span>
              {experiment.dataset && <span className="text-fg-muted"> on {experiment.dataset.symbol}</span>}
            </h2>
            <p className="mt-1 font-mono text-2xs text-fg-faint">
              {experiment.id} · {experiment.manifest.engine_name} {experiment.manifest.engine_version} · code{" "}
              {experiment.manifest.code_revision.slice(0, 7)}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {experiment.manifest.dataset_synthetic && <SyntheticBadge />}
            <ResearchOnly />
            {done ? <VerdictBadge verdict={s.verdict} title={s.meaning} /> : <RunStatus status={experiment.status} />}
          </div>
        </div>
        {done && (
          <p className="mt-4 text-[13px] text-fg-muted">
            <span className="text-fg">{s.assessment.headline}</span> {s.reason}
          </p>
        )}
      </Card>

      {(experiment.status === "queued" || experiment.status === "running") && <Running experiment={experiment} />}
      {(experiment.status === "failed" || experiment.status === "cancelled") && (
        <Card testId="ql-run-failed">
          <p className="text-[13px] text-fg">
            {experiment.status === "cancelled" ? "This run was cancelled." : "This run didn't complete."}
            {experiment.error && (
              <span className="text-fg-muted">
                {" "}
                <span className="font-mono text-2xs text-ql-danger">{experiment.error_code}</span> {experiment.error}
              </span>
            )}
          </p>
          <p className="mt-1 text-2xs text-fg-faint">No results are shown for an incomplete run — nothing from another run fills in.</p>
          <Button className="mt-3" onClick={onRerun}>
            Run again (same inputs)
          </Button>
        </Card>
      )}

      {done && (
        <>
          <nav className="flex gap-1 border-b border-ql-border" aria-label="Experiment">
            {TABS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className={cx(
                  "-mb-px border-b px-3 py-2 text-[13px] transition-colors",
                  tab === t ? "border-ql text-fg" : "border-transparent text-fg-faint hover:text-fg-muted",
                )}
                data-testid={`ql-tab-${t.toLowerCase()}`}
              >
                {t}
              </button>
            ))}
          </nav>
          {tab === "Overview" && <Overview experiment={experiment} />}
          {tab === "Trades" && <Trades experiment={experiment} />}
          {tab === "Assumptions" && <Assumptions experiment={experiment} />}
          {tab === "Validation" && <Validation experiment={experiment} />}
          {tab === "Audit" && <Audit experiment={experiment} />}
        </>
      )}
    </div>
  );
}

function Running({ experiment }: { experiment: QlExperiment }) {
  const [busy, setBusy] = useState(false);
  const at = experiment.stage ? STAGES.indexOf(experiment.stage) : -1;
  return (
    <Card title="Running in the background" testId="ql-running">
      <ol className="space-y-2 text-[13px]">
        {STAGES.map((stage, i) => (
          <li key={stage} className={cx(i < at ? "text-fg-muted" : i === at ? "text-ql" : "text-fg-faint")}>
            {i < at ? "✓" : i === at ? "●" : "○"} {stage.replace(/_/g, " ")}
          </li>
        ))}
      </ol>
      <p className="mt-3 text-2xs text-fg-faint">Real stages only — no estimated percentages.</p>
      <Button
        className="mt-3"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await api.qlCancel(experiment.id);
          } catch {
            /* already finished */
          } finally {
            setBusy(false);
          }
        }}
      >
        Cancel run
      </Button>
    </Card>
  );
}

function SegmentColumn({ title, m, currency }: { title: string; m: QlSegmentMetrics; currency: string }) {
  return (
    <div>
      <p className="mb-2 label">{title}</p>
      <dl className="space-y-1.5 text-[13px]">
        <Line label="Net after costs" value={money(m.net_pnl, currency)} />
        <Line label="Return" value={pct(m.return_pct)} sub={m.return_denominator} />
        <Line label="Fees" value={money(m.fees, currency, false)} />
        <Line label="Max drawdown" value={pct(m.max_drawdown_pct, 2, false)} />
        <Line label="Closed trades" value={String(m.trades_closed)} />
        <Line label="Win rate" value={m.win_rate === null ? "n/a" : pct(m.win_rate, 0, false)} />
        <Line label="Bars" value={m.bars.toLocaleString("en-US")} />
      </dl>
    </div>
  );
}

function Line({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-fg-faint" title={sub}>
        {label}
      </dt>
      <dd className="font-mono text-fg tabular">{value}</dd>
    </div>
  );
}

function Overview({ experiment }: { experiment: QlExperiment }) {
  const s = experiment.summary;
  const [equity, setEquity] = useState<QlEquity | null>(null);
  useEffect(() => {
    let live = true;
    api
      .qlEquity(experiment.id)
      .then((e) => live && setEquity(e))
      .catch(() => live && setEquity(null));
    return () => {
      live = false;
    };
  }, [experiment.id]);
  if (!s) return null;
  const { full, oos, train } = s.metrics;
  const cur = s.currency;
  const warnings = s.checks.filter((c) => c.result === "WARN" || c.result === "FAIL").slice(0, 3);
  return (
    <div className="space-y-5">
      <Card>
        <dl className="grid grid-cols-2 gap-5 md:grid-cols-3 xl:grid-cols-6">
          <Metric label="Net P&L" value={money(full.net_pnl, cur)} sub={`${pct(full.return_pct)} of starting cash`} hint={WHY.net} testId="ql-metric-net" />
          <Metric label="OOS net" value={money(oos.net_pnl, cur)} sub={`${oos.trades_closed} closed OOS trades`} hint={WHY.oos} />
          <Metric label="Max drawdown" value={pct(full.max_drawdown_pct, 2, false)} sub={money(-full.max_drawdown_abs, cur)} hint={WHY.dd} />
          <Metric label="Trades" value={String(full.trades_closed)} sub={`${full.trades_open} open · ${full.orders_rejected} rejected`} hint={WHY.trades} />
          <Metric label="Exposure" value={pct(full.exposure, 0, false)} sub={`fees ${money(full.fees, cur, false)}`} hint={WHY.exposure} />
          <Metric label="Buy & hold" value={pct(full.benchmark_price_return)} sub="price only, no costs" hint={WHY.benchmark} />
        </dl>
      </Card>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_280px]">
        <Card title="Equity net of costs" aside={<Provisional />}>
          {equity ? (
            <EquityChart
              points={equity.points}
              initialCash={full.initial_cash}
              currency={cur}
              oosStart={equity.oos_start}
              totalPoints={equity.total_points}
            />
          ) : (
            <p className="text-[13px] text-fg-faint">Loading the curve…</p>
          )}
        </Card>
        <Card title="Train vs out-of-sample">
          <div className="space-y-5">
            <SegmentColumn title={`Train · ${utc(train.start_utc)}`} m={train} currency={cur} />
            <SegmentColumn title={`OOS · ${utc(oos.start_utc)}`} m={oos} currency={cur} />
            {full.trades_open > 0 && (
              <p className="text-2xs text-fg-faint">
                Open at the end: {money(full.unrealized_gross_pnl, cur)} unrealised at {num(full.mark_price)} — marked, not sold.
              </p>
            )}
          </div>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Assessment experiment={experiment} />
        <Card title="Top warnings">
          {warnings.length === 0 ? (
            <p className="text-[13px] text-fg-muted">No warnings from the gates that ran.</p>
          ) : (
            <ul className="space-y-3">
              {warnings.map((c) => (
                <li key={c.id} className="text-[13px]">
                  <div className="flex items-center gap-2">
                    <CheckBadge result={c.result} />
                    <span className="text-fg">{c.title}</span>
                  </div>
                  <p className="mt-1 text-fg-muted">{c.observations}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

export function Assessment({ experiment }: { experiment: QlExperiment }) {
  const s = experiment.summary;
  if (!s) return null;
  const a = s.assessment;
  const block = (title: string, items: string[], tone = "text-fg-muted") => (
    <div>
      <p className="mb-1.5 label">{title}</p>
      <ul className={cx("space-y-1 text-[13px] leading-relaxed", tone)}>
        {items.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </div>
  );
  return (
    <Card title="JARVIS assessment" aside={<VerdictBadge verdict={s.verdict} />} testId="ql-assessment">
      <div className="space-y-4">
        <p className="text-[15px] text-fg">{a.headline}</p>
        {block("Observed", a.observed)}
        {block("Limitations", a.limitations)}
        {block("Can't conclude", a.cannot_conclude)}
        <div className="rounded-xl border border-ql/30 bg-ql/5 p-3">
          <p className="mb-1 label">Next pre-registered test</p>
          <p className="text-[13px] text-fg">{a.next_test}</p>
        </div>
      </div>
    </Card>
  );
}

function Trades({ experiment }: { experiment: QlExperiment }) {
  const [ledger, setLedger] = useState<QlLedger | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const cur = experiment.summary?.currency ?? "USD";
  useEffect(() => {
    api.qlLedger(experiment.id).then(setLedger).catch(() => setLedger(null));
  }, [experiment.id]);
  if (!ledger) return <p className="text-[13px] text-fg-faint">Loading the ledger…</p>;
  const trade = ledger.trades.find((t) => t.id === selected) ?? null;
  return (
    <div className="space-y-5" data-testid="ql-trades">
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Card title={`Trade explorer · ${ledger.trades.length}`}>
          {ledger.trades.length === 0 ? (
            <p className="text-[13px] text-fg-muted">No trades: the signal never produced a filled entry.</p>
          ) : (
            <div className="max-h-[420px] overflow-auto">
              <table className="w-full text-left text-[12px]">
                <thead className="sticky top-0 bg-ql-surface text-2xs text-fg-faint">
                  <tr>
                    {["", "Entry (UTC)", "Exit (UTC)", "Entry", "Exit", "Qty", "Gross", "Fees", "Net", "Bars"].map((h) => (
                      <th key={h} className="px-2 py-1.5 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="font-mono tabular">
                  {ledger.trades.map((t) => (
                    <tr
                      key={t.id}
                      tabIndex={0}
                      onClick={() => setSelected(t.id)}
                      onKeyDown={(e) => e.key === "Enter" && setSelected(t.id)}
                      className={cx(
                        "cursor-pointer border-t border-ql-border/60 text-fg-muted hover:bg-white/[0.03]",
                        selected === t.id && "bg-white/[0.05] text-fg",
                      )}
                      data-testid="ql-trade-row"
                    >
                      <td className="px-2 py-1.5">{t.id}</td>
                      <td className="px-2 py-1.5">{utc(t.entry_time_utc)}</td>
                      <td className="px-2 py-1.5">{t.status === "OPEN" ? <span className="text-ql-warning">open · marked</span> : utc(t.exit_time_utc)}</td>
                      <td className="px-2 py-1.5">{num(t.entry_price)}</td>
                      <td className="px-2 py-1.5">{num(t.exit_price)}{t.exit_price_is_mark ? "*" : ""}</td>
                      <td className="px-2 py-1.5">{t.quantity}</td>
                      <td className="px-2 py-1.5">{num(t.gross_pnl, 2)}</td>
                      <td className="px-2 py-1.5">{num(t.fees, 2)}</td>
                      <td className="px-2 py-1.5 text-fg">{num(t.net_pnl, 2)}</td>
                      <td className="px-2 py-1.5">{t.bars_held}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-2xs text-fg-faint">* exit price is the last close (mark), not a fill.</p>
            </div>
          )}
        </Card>
        <TradeDetail trade={trade} ledger={ledger} currency={cur} />
      </div>
      <Card title={`Orders · ${ledger.orders.length}`}>
        <OrdersTable orders={ledger.orders} />
      </Card>
      <Card title={`Signals · ${ledger.signals.length}`}>
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-left text-[12px]">
            <thead className="text-2xs text-fg-faint">
              <tr>
                {["Bar", "Bar start (UTC)", "Known at", "Side", "SMA fast", "SMA slow", "What happened"].map((h) => (
                  <th key={h} className="px-2 py-1 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="font-mono text-fg-muted tabular">
              {ledger.signals.map((sig) => (
                <tr key={sig.index} className="border-t border-ql-border/60">
                  <td className="px-2 py-1">{sig.index}</td>
                  <td className="px-2 py-1">{utc(sig.bar_start_utc)}</td>
                  <td className="px-2 py-1">{utc(sig.available_at_utc)}</td>
                  <td className="px-2 py-1">{sig.side}</td>
                  <td className="px-2 py-1">{num(sig.sma_fast)}</td>
                  <td className="px-2 py-1">{num(sig.sma_slow)}</td>
                  <td className="px-2 py-1">{sig.disposition.replace(/_/g, " ").toLowerCase()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function TradeDetail({ trade, ledger, currency }: { trade: QlTrade | null; ledger: QlLedger; currency: string }) {
  if (!trade) {
    return (
      <Card title="Trade detail">
        <p className="text-[13px] text-fg-faint">Select a trade to see its signal bar, fill bar, prices and fees.</p>
      </Card>
    );
  }
  const entry = ledger.orders.find((o) => o.id === trade.entry_order);
  const exit = ledger.orders.find((o) => o.id === trade.exit_order);
  const signal = entry ? ledger.signals.find((s) => s.index === entry.signal_index) : undefined;
  const rows: [string, ReactNode][] = [
    ["Signal bar", entry ? `#${entry.signal_index} · ${utc(entry.signal_bar_start_utc)}` : "—"],
    ["Signal known at", entry ? utc(entry.signal_available_at_utc) : "—"],
    ["SMA fast / slow", signal ? `${num(signal.sma_fast)} / ${num(signal.sma_slow)}` : "—"],
    ["Entry fill", entry ? `#${entry.fill_index} · ${utc(entry.fill_time_utc)} @ ${num(entry.fill_price)}` : "—"],
    ["Entry open · slippage", entry ? `${num(entry.reference_open)} · ${num(entry.slippage_cost, 4)}` : "—"],
    ["Cash after entry", entry ? money(entry.cash_after, currency, false) : "—"],
    [
      "Exit",
      trade.status === "OPEN"
        ? `marked at last close ${num(trade.exit_price)} — no exit assumed`
        : exit
          ? `#${exit.fill_index} · ${utc(exit.fill_time_utc)} @ ${num(exit.fill_price)}`
          : "—",
    ],
    ["Fees", money(trade.fees, currency, false)],
    ["Gross → net", `${money(trade.gross_pnl, currency)} → ${money(trade.net_pnl, currency)}`],
    ["Return on entry cost", pct(trade.return_pct)],
  ];
  return (
    <Card title={`Trade ${trade.id}`} aside={<Badge tone={trade.status === "OPEN" ? "warning" : "muted"}>{trade.status}</Badge>} testId="ql-trade-detail">
      <dl className="space-y-1.5 text-[13px]">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[130px_minmax(0,1fr)] gap-2">
            <dt className="text-fg-faint">{k}</dt>
            <dd className="selectable font-mono text-[12px] text-fg">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-2xs text-fg-faint">
        Ambiguities: none possible in R1 — market orders only, no stop or target can be touched inside a bar.
      </p>
    </Card>
  );
}

function OrdersTable({ orders }: { orders: QlOrder[] }) {
  return (
    <div className="max-h-64 overflow-auto">
      <table className="w-full text-left text-[12px]">
        <thead className="text-2xs text-fg-faint">
          <tr>
            {["Order", "Side", "Status", "Signal bar", "Fill bar", "Open", "Fill", "Fee", "Cash after", "Position"].map((h) => (
              <th key={h} className="px-2 py-1 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="font-mono text-fg-muted tabular">
          {orders.map((o) => (
            <tr key={o.id} className="border-t border-ql-border/60" title={o.reason ?? undefined}>
              <td className="px-2 py-1">{o.id}</td>
              <td className="px-2 py-1">{o.side}</td>
              <td className={cx("px-2 py-1", o.status !== "FILLED" && "text-ql-warning")}>{o.status.replace("_", " ").toLowerCase()}</td>
              <td className="px-2 py-1">{o.signal_index}</td>
              <td className="px-2 py-1">{o.fill_index ?? "—"}</td>
              <td className="px-2 py-1">{num(o.reference_open)}</td>
              <td className="px-2 py-1">{num(o.fill_price)}</td>
              <td className="px-2 py-1">{num(o.fee, 4)}</td>
              <td className="px-2 py-1">{num(o.cash_after, 2)}</td>
              <td className="px-2 py-1">{o.position_after ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Assumptions({ experiment }: { experiment: QlExperiment }) {
  const sim = (experiment.manifest.simulation ?? {}) as Record<string, unknown>;
  const validation = (experiment.manifest.validation ?? {}) as Record<string, unknown>;
  const passport = experiment.dataset?.passport;
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title="Execution model">
        <dl className="space-y-1.5 text-[13px]">
          {Object.entries(sim).map(([k, v]) => (
            <div key={k} className="grid grid-cols-[170px_minmax(0,1fr)] gap-2">
              <dt className="text-fg-faint">{k.replace(/_/g, " ")}</dt>
              <dd className="font-mono text-[12px] text-fg">{String(v)}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-2xs text-fg-faint">
          Zero-latency next-open fills are an optimistic research approximation; real fills are usually worse.
        </p>
      </Card>
      <Card title="Split & policy">
        <dl className="space-y-1.5 text-[13px]">
          {Object.entries(validation)
            .filter(([k]) => k !== "verdict_policy")
            .map(([k, v]) => (
              <div key={k} className="grid grid-cols-[200px_minmax(0,1fr)] gap-2">
                <dt className="text-fg-faint">{k.replace(/_/g, " ")}</dt>
                <dd className="font-mono text-[12px] text-fg">{String(v)}</dd>
              </div>
            ))}
        </dl>
      </Card>
      {passport && (
        <Card title="Data limitations" className="lg:col-span-2">
          <ul className="space-y-1.5 text-[13px] text-fg-muted">
            {passport.limitations.map((l) => (
              <li key={l}>— {l}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

const GATES: Record<QlCheck["gate"], string> = {
  A: "Gate A · Valid data & execution",
  B: "Gate B · Baseline plausibility",
  C: "Gate C · Generalisation",
  D: "Gate D · Costs, regimes & uncertainty",
  E: "Gate E · Forward validation",
};

function Validation({ experiment }: { experiment: QlExperiment }) {
  const s = experiment.summary;
  if (!s) return null;
  return (
    <div className="space-y-5" data-testid="ql-validation">
      <Card title="Verdict">
        <div className="flex items-center gap-3">
          <VerdictBadge verdict={s.verdict} />
          <p className="text-[13px] text-fg-muted">{s.meaning}</p>
        </div>
        <p className="mt-3 text-[13px] text-fg">{s.reason}</p>
        <p className="mt-3 text-2xs text-fg-faint">
          Policy fixed before the run: INVALID if any Gate A check fails · FAILED if the OOS segment has ≥ 30 closed trades
          and loses after costs · otherwise INCONCLUSIVE. R1 never says “validated”.
        </p>
      </Card>
      {(Object.keys(GATES) as QlCheck["gate"][]).map((gate) => {
        const checks = s.checks.filter((c) => c.gate === gate);
        return (
          <Card key={gate} title={GATES[gate]}>
            <ul className="divide-y divide-ql-border/60">
              {checks.map((c) => (
                <li key={c.id} className="grid gap-2 py-2.5 md:grid-cols-[220px_90px_minmax(0,1fr)]">
                  <span className="text-[13px] text-fg">
                    {c.title}
                    <span className="ml-2 font-mono text-2xs text-fg-faint">{c.id.split(".")[0]}</span>
                  </span>
                  <span>
                    <CheckBadge result={c.result} />
                  </span>
                  <span className="text-[13px] text-fg-muted">
                    {c.observations}
                    {c.assumptions.length > 0 && (
                      <span className="mt-1 block text-2xs text-fg-faint">Assumes: {c.assumptions.join(" ")}</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        );
      })}
    </div>
  );
}

function Audit({ experiment }: { experiment: QlExperiment }) {
  const s = experiment.summary;
  const [repro, setRepro] = useState<QlReproduction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="space-y-5">
      <Card
        title="Reproducibility"
        aside={
          <Button
            disabled={busy}
            data-testid="ql-reproduce"
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                setRepro(await api.qlReproduce(experiment.id));
              } catch (err) {
                setError(err instanceof ApiError ? err.message : "Failed.");
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Re-running…" : "Re-run & compare"}
          </Button>
        }
      >
        <dl className="space-y-1.5 text-[13px]">
          <HashRow label="Manifest SHA-256" value={experiment.manifest_sha256} />
          <HashRow label="Strategy spec SHA-256" value={experiment.manifest.strategy_spec_sha256} />
          <HashRow label="Data snapshot SHA-256" value={String(experiment.manifest.dataset_snapshot_sha256 ?? "")} />
          <HashRow label="Results SHA-256" value={experiment.results_sha256} />
        </dl>
        {repro && (
          <p className={cx("mt-3 text-[13px]", repro.identical ? "text-ql-positive" : "text-ql-danger")} data-testid="ql-repro-result">
            {repro.identical
              ? "✓ Identical: the re-run from the frozen snapshot produced the same results hash."
              : "✕ Different results hash — investigate before trusting this run."}
            {!repro.same_code_revision && " (Code revision changed since the run.)"}
          </p>
        )}
        {error && <p className="mt-3 text-[13px] text-ql-danger">{error}</p>}
      </Card>
      {s && (
        <Card title="Invariant audit">
          <ul className="space-y-2">
            {s.audit.map((c) => (
              <li key={c.id} className="flex items-start gap-3 text-[13px]">
                <CheckBadge result={c.passed ? "PASS" : "FAIL"} />
                <span className="min-w-0">
                  <span className="font-mono text-2xs text-fg-faint">{c.id}</span>
                  <span className="block text-fg-muted">{c.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card title={`Artifacts · ${experiment.artifacts.length}`}>
        <table className="w-full text-left text-[12px]">
          <thead className="text-2xs text-fg-faint">
            <tr>
              <th className="py-1 font-medium">File</th>
              <th className="py-1 font-medium">SHA-256</th>
              <th className="py-1 text-right font-medium">Size</th>
            </tr>
          </thead>
          <tbody className="font-mono text-fg-muted">
            {experiment.artifacts.map((a) => (
              <tr key={a.kind} className="border-t border-ql-border/60">
                <td className="py-1">{a.relative_path}</td>
                <td className="py-1">
                  <Hash value={a.sha256} length={20} />
                </td>
                <td className="py-1 text-right">{(a.bytes / 1024).toFixed(1)} KB</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Manifest">
        <pre className="selectable max-h-80 overflow-auto rounded-lg bg-ql-raised p-3 font-mono text-[11px] leading-relaxed text-fg-muted">
          {JSON.stringify(experiment.manifest, null, 2)}
        </pre>
      </Card>
    </div>
  );
}

function HashRow({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="grid grid-cols-[180px_minmax(0,1fr)] gap-2">
      <dt className="text-fg-faint">{label}</dt>
      <dd className="selectable truncate font-mono text-[12px] text-fg-muted">{value ?? "—"}</dd>
    </div>
  );
}
