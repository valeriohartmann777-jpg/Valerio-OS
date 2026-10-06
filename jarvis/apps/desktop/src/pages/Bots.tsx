import type { BotDetail, BotLabStatus, BotScaling, BotSettings, BotStats, BotTest, BotVersion } from "@jarvis/protocol";
import { useEffect, useState } from "react";

import { Button, Empty, Pill, StatusDot, type Tone, cx, textTone } from "../components/ui/primitives";
import { ApiError, api } from "../lib/api";
import { clock, signed, usd } from "../lib/format";
import { useJarvis } from "../store/store";

const STATE: Record<BotLabStatus["state"], { label: string; tone: Tone }> = {
  idle: { label: "Ready", tone: "muted" },
  working: { label: "Working", tone: "accent" },
  improving: { label: "Improving", tone: "accent" },
  waiting: { label: "Between rounds", tone: "muted" },
  budget: { label: "Budget used up", tone: "muted" },
  stalled: { label: "Stopped itself", tone: "warning" },
  needs_brain: { label: "Needs Claude", tone: "warning" },
  error: { label: "Problem", tone: "danger" },
};

const MODELS: Record<string, string> = {
  real_ticks: "Every tick based on real ticks",
  every_tick: "Every tick (generated)",
  ohlc_1m: "1-minute OHLC (fast)",
  open_prices: "Open prices only",
};

const PERIODS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];

function message(err: unknown): string {
  return err instanceof ApiError ? err.message : "That didn't work.";
}

/** The user's MetaTrader 5 EAs: backtests, improvement by Claude, honest results. */
export function Bots() {
  const lab = useJarvis((s) => s.bots);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const experts = lab?.experts ?? [];
  const current = selected ?? experts[0]?.name ?? null;

  const act = async (call: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await call();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto" data-testid="bots-page">
      <div className="mx-auto max-w-[1040px] px-10 py-10">
        <header>
          <h1 className="text-2xl font-light tracking-[-0.01em] text-fg">Bots</h1>
          <p className="mt-2 max-w-[680px] text-[13px] leading-relaxed text-fg-muted">
            JARVIS backtests your MetaTrader 5 Expert Advisors in its own copy of MetaTrader and improves them with
            Claude. A version counts only if it also holds on months it wasn&apos;t tuned on. Your EA files and your
            running MetaTrader are never touched — improved versions reach MetaTrader only when you copy them.
          </p>
        </header>
        {error && <p className="mt-4 text-[13px] text-danger">{error}</p>}

        {lab && <Setup lab={lab} busy={busy} act={act} />}

        {lab && experts.length > 0 && (
          <section className="mt-10">
            <h2 className="label mb-3">Your EAs · {experts.length}</h2>
            <div className="flex flex-wrap gap-2" data-testid="bot-list">
              {experts.map((expert) => (
                <button
                  key={expert.name}
                  type="button"
                  onClick={() => setSelected(expert.name)}
                  title={expert.path}
                  className={cx(
                    "no-drag rounded-lg border px-3 py-1.5 text-[13px] transition-colors",
                    expert.name === current
                      ? "border-accent/50 text-fg"
                      : "border-hairline-strong text-fg-muted hover:text-fg",
                  )}
                >
                  {expert.file}
                  {lab.improving === expert.name && <span className="ml-2 text-2xs text-accent">improving</span>}
                </button>
              ))}
            </div>
          </section>
        )}

        {lab && current && <BotPanel key={current} name={current} lab={lab} />}
      </div>
    </div>
  );
}

function Setup({
  lab,
  busy,
  act,
}: {
  lab: BotLabStatus;
  busy: boolean;
  act: (call: () => Promise<unknown>) => Promise<void>;
}) {
  const state = STATE[lab.state];
  return (
    <section className="mt-10 rounded-xl border border-hairline bg-canvas-2 px-5 py-4" data-testid="bot-setup">
      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[13px] text-fg" data-testid="bot-lab-state">
            <StatusDot tone={state.tone} live={lab.state === "working" || lab.state === "improving"} />
            {state.label}
            {lab.detail && <span className="truncate text-xs text-fg-faint">· {lab.detail}</span>}
          </div>
          <ul className="mt-3 space-y-1 text-xs">
            {lab.checks.map((check) => (
              <li key={check.key} className="flex gap-2" data-testid={`bot-check-${check.key}`}>
                <span className={check.ok ? "text-success" : "text-warning"}>{check.ok ? "✓" : "✗"}</span>
                <span className="shrink-0 whitespace-nowrap text-fg-muted">{check.label}</span>
                <span className="min-w-0 truncate font-mono text-fg-faint" title={check.detail}>
                  {check.detail}
                </span>
              </li>
            ))}
          </ul>
          {lab.user_terminal_running && (
            <p className="mt-2 text-xs text-fg-faint">
              Your MetaTrader is open — JARVIS works in its own copy and doesn&apos;t touch it.
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          {!lab.ready && (
            <Button
              variant="primary"
              disabled={busy || !lab.checks.find((c) => c.key === "data")?.ok}
              onClick={() => void act(() => api.setUpBots())}
              data-testid="bot-setup-button"
            >
              Set up test terminal
            </Button>
          )}
          {lab.ready && (
            <Button
              disabled={busy}
              onClick={() => void act(() => api.openTestTerminal())}
              title="Log in to your demo account once, so the tester can download gold's history"
            >
              Open test terminal
            </Button>
          )}
          <button
            type="button"
            className="no-drag text-xs text-fg-faint hover:text-fg"
            onClick={() => void act(() => api.refreshBots())}
          >
            Search again
          </button>
        </div>
      </div>
    </section>
  );
}

function BotPanel({ name, lab }: { name: string; lab: BotLabStatus }) {
  const [detail, setDetail] = useState<BotDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // Reload when the lab reports progress for any run or round.
  const changed = [lab.state, lab.detail, lab.running.length, lab.improving].join("|");
  useEffect(() => {
    let live = true;
    api.bot(name).then(
      (d) => {
        if (!live) return;
        setDetail(d);
        setMissing(false);
      },
      (err) => {
        if (live && err instanceof ApiError && err.status === 404) setMissing(true);
      },
    );
    return () => {
      live = false;
    };
  }, [name, changed]);

  const act = async (call: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await call();
      if (done) setNote(done);
      setDetail(await api.bot(name));
      setMissing(false);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  if (missing) {
    return (
      <section className="mt-6 rounded-xl border border-hairline bg-canvas-2 px-5 py-4">
        <p className="text-[13px] text-fg-muted">
          JARVIS hasn&apos;t looked at {name} yet. Importing reads its source and inputs; your file stays as it is.
        </p>
        <Button className="mt-3" variant="primary" disabled={busy} onClick={() => void act(() => api.importBot(name))}>
          Import {name}
        </Button>
        {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      </section>
    );
  }
  if (!detail) return null;

  const improving = lab.improving === name;
  const running = lab.running.filter((r) => r.bot === name);
  const best = detail.tests.find((t) => t.number === detail.best_test) ?? null;
  const latest = detail.tests.find((t) => t.status === "done") ?? null;
  const shown = best ?? latest;

  return (
    <div className="mt-6 space-y-10" data-testid="bot-panel">
      <section className="rounded-xl border border-hairline bg-canvas-2 px-5 py-4">
        <div className="flex items-start justify-between gap-6">
          <TesterSettings detail={detail} busy={busy} act={act} />
          <div className="flex shrink-0 flex-col items-end gap-2">
            <Button
              disabled={busy || !lab.ready || running.length > 0}
              onClick={() => void act(() => api.botBacktest(name, 0), "Backtest started — this takes minutes.")}
              data-testid="bot-backtest"
            >
              {running.length > 0 ? "Backtest running…" : "Backtest the original"}
            </Button>
            <Button
              variant={improving ? "ghost" : "primary"}
              disabled={busy || !lab.ready}
              onClick={() => void act(() => (improving ? api.stopImproving() : api.improveBot(name)))}
              data-testid="bot-improve"
            >
              {improving ? "Stop improving" : "Improve with Claude"}
            </Button>
            <span className="text-2xs text-fg-faint tabular">
              Claude today {usd(lab.spent_today_usd)} / {usd(lab.budget_usd)}
              {lab.next_round_at && improving ? ` · next round ${clock(lab.next_round_at)}` : ""}
            </span>
          </div>
        </div>
        {error && <p className="mt-3 text-xs text-danger">{error}</p>}
        {note && <p className="mt-3 text-xs text-fg-muted">{note}</p>}
      </section>

      <section>
        <h2 className="label mb-1">{best ? `Best validated: T${best.number} (v${best.version})` : "Latest backtest"}</h2>
        <p className="mb-3 text-xs text-fg-faint">
          In-sample {detail.periods.in_sample} · out-of-sample {detail.periods.out_of_sample} · holdout{" "}
          {detail.periods.holdout}. Claude only ever sees in-sample numbers.
        </p>
        {shown ? <Results test={shown} /> : <Empty>No backtest yet.</Empty>}
      </section>

      {detail.projection && <Projection projection={detail.projection} />}

      <section>
        <h2 className="label mb-3">Versions · {detail.versions.length}</h2>
        <ul className="divide-y divide-hairline border-y border-hairline" data-testid="bot-versions">
          {[...detail.versions].reverse().map((version) => (
            <VersionRow
              key={version.number}
              name={name}
              version={version}
              tests={detail.tests.filter((t) => t.version === version.number)}
              busy={busy || !lab.ready}
              act={act}
            />
          ))}
        </ul>
      </section>

      <section>
        <h2 className="label mb-3">Backtests · {detail.tests.length}</h2>
        {detail.tests.length === 0 ? (
          <Empty>None yet.</Empty>
        ) : (
          <ul className="divide-y divide-hairline border-y border-hairline" data-testid="bot-tests">
            {detail.tests.map((test) => (
              <TestRow key={test.number} test={test} />
            ))}
          </ul>
        )}
      </section>

      {(detail.notes.length > 0 || detail.rounds.length > 0) && (
        <section className="grid grid-cols-2 gap-10">
          <div>
            <h2 className="label mb-3">What Claude learned</h2>
            <ul className="space-y-2 text-[13px] text-fg-muted">
              {detail.notes.map((n) => (
                <li key={n.number}>{n.text}</li>
              ))}
            </ul>
          </div>
          <div>
            <h2 className="label mb-3">Rounds</h2>
            <ul className="space-y-1.5 text-xs">
              {detail.rounds.map((r) => (
                <li key={r.number} className="flex justify-between gap-4">
                  <span className="min-w-0 truncate text-fg-muted">
                    Round {r.number} · {r.summary || r.error || r.status}
                  </span>
                  <span className="shrink-0 font-mono text-fg-faint tabular">{usd(r.cost_usd)}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}
    </div>
  );
}

function TesterSettings({
  detail,
  busy,
  act,
}: {
  detail: BotDetail;
  busy: boolean;
  act: (call: () => Promise<unknown>, done?: string) => Promise<void>;
}) {
  const [form, setForm] = useState<BotSettings>(detail.settings);
  const changed = JSON.stringify(form) !== JSON.stringify(detail.settings);
  const field = "no-drag rounded-md border border-hairline-strong bg-canvas px-2 py-1 text-[13px] text-fg";
  return (
    <div className="min-w-0 space-y-2 text-xs" data-testid="bot-settings">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1.5 text-fg-faint">
          Symbol
          <input
            className={cx(field, "w-28")}
            value={form.symbol}
            onChange={(e) => setForm({ ...form, symbol: e.target.value })}
            title="Your broker's name for gold (XAUUSD, XAUUSD.m, GOLD …)"
          />
        </label>
        <label className="flex items-center gap-1.5 text-fg-faint">
          Timeframe
          <select className={field} value={form.period} onChange={(e) => setForm({ ...form, period: e.target.value })}>
            {[...new Set([...PERIODS, form.period])].map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-fg-faint">
          Deposit
          <input
            className={cx(field, "w-24")}
            type="number"
            value={form.deposit}
            onChange={(e) => setForm({ ...form, deposit: Number(e.target.value) })}
          />
        </label>
        <label className="flex items-center gap-1.5 text-fg-faint">
          Leverage 1:
          <input
            className={cx(field, "w-16")}
            type="number"
            value={form.leverage}
            onChange={(e) => setForm({ ...form, leverage: Number(e.target.value) })}
          />
        </label>
      </div>
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-1.5 text-fg-faint">
          Model
          <select className={field} value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })}>
            {Object.entries(MODELS).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {changed && (
          <Button disabled={busy} onClick={() => void act(() => api.botSettings(detail.name, form), "Saved.")}>
            Save
          </Button>
        )}
      </div>
    </div>
  );
}

function Results({ test }: { test: BotTest }) {
  const rows: [string, BotStats | null][] = [
    ["In-sample", test.in_sample],
    ["Out-of-sample", test.out_of_sample],
    ["Holdout (unseen)", test.holdout],
  ];
  const months = test.unseen?.monthly ?? [];
  return (
    <div className="rounded-xl border border-hairline bg-canvas-2 px-5 py-4" data-testid="bot-results">
      <div className="flex items-center justify-between gap-4 text-xs">
        <span className="text-fg-faint">
          T{test.number} · v{test.version} · {test.settings.symbol} {test.settings.period} ·{" "}
          {MODELS[test.settings.model] ?? test.settings.model} · deposit {test.settings.deposit.toLocaleString("en-US")}
        </span>
        <Verdict test={test} />
      </div>
      <table className="mt-3 w-full text-xs">
        <thead className="text-fg-faint">
          <tr className="text-left">
            <th className="py-1 font-normal">Period</th>
            {["Trades", "Net", "PF", "Win", "Return", "Max DD", "Worst day", "Median month", "t"].map((h) => (
              <th key={h} className="py-1 text-right font-normal">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="font-mono text-fg-muted tabular">
          {rows.map(([label, stats]) => (
            <StatsRow key={label} label={label} stats={stats} />
          ))}
        </tbody>
      </table>
      {months.length > 0 && (
        <div className="mt-4">
          <div className="label mb-2">Months it wasn&apos;t tuned on</div>
          <div className="grid grid-cols-6 gap-1 text-2xs" data-testid="bot-months">
            {months.map((m) => (
              <div
                key={m.month}
                className={cx(
                  "rounded px-1.5 py-1 font-mono tabular",
                  (m.pct ?? 0) >= 0 ? "bg-success/10 text-success" : "bg-danger/10 text-danger",
                )}
                title={`${m.trades} trades, ${m.pnl.toLocaleString("en-US")} ${test.settings.currency ?? "USD"}`}
              >
                {m.month} {m.pct === null ? "–" : `${signed(m.pct, 1)}%`}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Verdict({ test }: { test: BotTest }) {
  if (test.validated === null) return <span className="text-fg-faint">not validated</span>;
  if (!test.validated) return <span className="text-fg-faint" title={test.validation_reason ?? ""}>failed: {test.validation_reason}</span>;
  return (
    <span className={cx("flex items-center gap-1.5", textTone(test.holdout_confirmed ? "success" : "warning"))}>
      <StatusDot tone={test.holdout_confirmed ? "success" : "warning"} />
      {test.holdout_confirmed ? "Validated · holdout confirms it" : "Validated · holdout not significant"}
    </span>
  );
}

function StatsRow({ label, stats }: { label: string; stats: BotStats | null }) {
  if (!stats || stats.trades === 0) return null;
  const prop = stats.prop;
  const propOk = prop ? prop.daily_loss_ok && prop.max_loss_ok : true;
  return (
    <tr className="border-t border-hairline">
      <td className="py-1.5 font-sans text-fg-faint">{label}</td>
      <td className="py-1.5 text-right">{stats.trades}</td>
      <td className={cx("py-1.5 text-right", stats.net >= 0 ? "text-fg" : "text-danger")}>
        {Math.round(stats.net).toLocaleString("en-US")}
      </td>
      <td className="py-1.5 text-right">{stats.profit_factor?.toFixed(2) ?? "–"}</td>
      <td className="py-1.5 text-right">{stats.win_rate === null ? "–" : `${Math.round(stats.win_rate * 100)}%`}</td>
      <td className="py-1.5 text-right">{signed(stats.return_pct, 1)}%</td>
      <td className={cx("py-1.5 text-right", !propOk && "text-warning")} title={propOk ? "" : "breaks the prop-firm limits"}>
        {stats.max_drawdown_pct.toFixed(1)}%
      </td>
      <td className="py-1.5 text-right">{stats.max_daily_loss_pct.toFixed(1)}%</td>
      <td className="py-1.5 text-right">
        {stats.median_month_pct === null ? "–" : `${signed(stats.median_month_pct, 1)}%`}
      </td>
      <td className="py-1.5 text-right">{stats.t_stat.toFixed(1)}</td>
    </tr>
  );
}

function Projection({ projection }: { projection: NonNullable<BotDetail["projection"]> }) {
  return (
    <section data-testid="bot-projection">
      <h2 className="label mb-1">What $10k a month would take</h2>
      <p className="mb-3 max-w-[720px] text-xs leading-relaxed text-fg-faint">
        From T{projection.test}&apos;s {projection.basis} months. The lot size is scaled so that the backtest&apos;s worst
        drawdown × 1.5 stays inside the limit. Linear scaling ignores slippage at size — treat it as the best case,
        and only for a version that holds up on the holdout.
      </p>
      <div className="grid grid-cols-2 gap-4">
        <ScalingCard title="Prop firm" plan={projection.prop_firm} />
        <ScalingCard title="Own account" plan={projection.own_account} />
      </div>
    </section>
  );
}

function ScalingCard({ title, plan }: { title: string; plan: BotScaling | null }) {
  return (
    <div className="rounded-xl border border-hairline bg-canvas-2 px-5 py-4 text-xs">
      <div className="label mb-2">{title}</div>
      {!plan ? (
        <Empty>Not enough months to project.</Empty>
      ) : (
        <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1">
          <dt className="text-fg-faint">Account · drawdown limit</dt>
          <dd className="text-right font-mono text-fg-muted tabular">
            {plan.account.toLocaleString("en-US")} · {plan.drawdown_limit_pct}%
          </dd>
          <dt className="text-fg-faint">Risk scale vs. the backtest</dt>
          <dd className="text-right font-mono text-fg-muted tabular">×{plan.risk_scale}</dd>
          <dt className="text-fg-faint">Typical month</dt>
          <dd className={cx("text-right font-mono tabular", plan.monthly_usd > 0 ? "text-fg" : "text-danger")}>
            {signed(plan.monthly_pct, 1)}% · {Math.round(plan.monthly_usd).toLocaleString("en-US")} $
          </dd>
          <dt className="text-fg-faint">Account for {plan.target.toLocaleString("en-US")} $ / month</dt>
          <dd className="text-right font-mono text-fg tabular" data-testid="bot-account-needed">
            {plan.account_for_target === null ? "not reachable" : `${plan.account_for_target.toLocaleString("en-US")} $`}
          </dd>
        </dl>
      )}
    </div>
  );
}

function VersionRow({
  name,
  version,
  tests,
  busy,
  act,
}: {
  name: string;
  version: BotVersion;
  tests: BotTest[];
  busy: boolean;
  act: (call: () => Promise<unknown>, done?: string) => Promise<void>;
}) {
  const [diff, setDiff] = useState<string | null>(null);
  const validated = tests.some((t) => t.validated);
  const confirmed = tests.some((t) => t.holdout_confirmed);
  const tone: Tone = confirmed ? "success" : validated ? "warning" : version.compiled || version.number === 0 ? "muted" : "danger";
  const label = confirmed
    ? "Holdout confirms"
    : validated
      ? "Validated"
      : !version.compiled && version.errors.length > 0
        ? "Doesn't compile"
        : tests.length
          ? "Tested"
          : "New";
  return (
    <li className="py-2.5">
      <div className="flex items-baseline justify-between gap-4">
        <div className="min-w-0 text-[13px] text-fg">
          <span className="mr-2 font-mono text-2xs text-fg-faint">v{version.number}</span>
          {version.title}
          {version.diff && (
            <span className="ml-2 font-mono text-2xs text-fg-faint">
              +{version.diff.added} −{version.diff.removed}
            </span>
          )}
        </div>
        <Pill tone={tone}>{label}</Pill>
      </div>
      {version.hypothesis && <p className="mt-1 text-xs text-fg-muted">{version.hypothesis}</p>}
      {!version.compiled && version.errors.length > 0 && (
        <p className="mt-1 truncate font-mono text-2xs text-danger" title={version.errors.join("\n")}>
          {version.errors[0]}
        </p>
      )}
      <div className="mt-1.5 flex gap-4 text-xs">
        <button
          type="button"
          className="no-drag text-fg-faint hover:text-fg disabled:opacity-40"
          disabled={busy}
          onClick={() => void act(() => api.botBacktest(name, version.number), "Backtest started.")}
        >
          Backtest
        </button>
        {version.parent !== null && (
          <button
            type="button"
            className="no-drag text-fg-faint hover:text-fg"
            onClick={() =>
              diff === null ? void api.botSource(name, version.number).then((s) => setDiff(s.diff)) : setDiff(null)
            }
          >
            {diff === null ? "Show changes" : "Hide changes"}
          </button>
        )}
        <button
          type="button"
          className="no-drag text-fg-faint hover:text-fg disabled:opacity-40"
          disabled={busy || (!version.compiled && version.number !== 0)}
          onClick={() =>
            void act(
              () => api.installBot(name, version.number),
              `Copied to MetaTrader as Experts/JARVIS/${name}/${name}_v${version.number}.mq5 — refresh the Navigator, compile in MetaEditor, test on demo first.`,
            )
          }
          data-testid={`bot-install-${version.number}`}
        >
          Copy to MetaTrader
        </button>
      </div>
      {diff !== null && (
        <pre className="selectable mt-2 max-h-80 overflow-auto rounded-lg border border-hairline bg-canvas p-3 font-mono text-2xs leading-relaxed">
          {diff.split("\n").map((line, i) => (
            <div
              key={i}
              className={cx(
                line.startsWith("+") && !line.startsWith("+++") && "text-success",
                line.startsWith("-") && !line.startsWith("---") && "text-danger",
                !line.startsWith("+") && !line.startsWith("-") && "text-fg-faint",
              )}
            >
              {line || " "}
            </div>
          ))}
        </pre>
      )}
    </li>
  );
}

function TestRow({ test }: { test: BotTest }) {
  const ins = test.in_sample;
  return (
    <li className="grid grid-cols-[52px_minmax(0,1fr)_auto] items-baseline gap-4 py-2 text-xs">
      <span className="font-mono text-2xs text-fg-faint">T{test.number}</span>
      <span className={cx("min-w-0 truncate", test.status === "failed" ? "text-warning" : "text-fg-muted")}>
        v{test.version} · {test.origin === "research" ? "Claude" : "you"} ·{" "}
        {test.status === "running"
          ? "running…"
          : test.status === "failed"
            ? test.error
            : test.validated === null
              ? "not validated"
              : test.validated
                ? test.holdout_confirmed
                  ? "validated, holdout confirms"
                  : "validated, holdout not significant"
                : `failed ${test.validation_reason ?? ""}`}
        {Object.keys(test.inputs).length > 0 &&
          ` · ${Object.entries(test.inputs)
            .map(([k, v]) => `${k}=${v}`)
            .join(", ")}`}
      </span>
      <span className="font-mono text-2xs text-fg-faint tabular">
        {ins ? `IS ${ins.trades} tr · PF ${ins.profit_factor?.toFixed(2) ?? "–"} · DD ${ins.max_drawdown_pct.toFixed(1)}%` : ""}
        {test.seconds ? ` · ${Math.round(test.seconds)} s` : ""}
      </span>
    </li>
  );
}
