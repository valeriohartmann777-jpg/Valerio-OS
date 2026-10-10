import type { QrOverview, QrRunRow } from "@jarvis/protocol";

import { Button, cx } from "../../ui/primitives";
import { AskJarvis } from "../EquityLab";
import { Badge, Card } from "../ui";
import { FixtureBadge, Progress, RunBadge, VerdictTag, tone, usd } from "./common";

export type Section =
  | "Overview"
  | "Strategy Studio"
  | "Data Hub"
  | "Backtest Lab"
  | "Validation"
  | "Trade Explorer"
  | "Experiments"
  | "Risk & Execution"
  | "Reports"
  | "Equity lab (R1)";

/** QuantLab overview: research state, data provider, saved strategies, recent and active runs. */
export function Overview({
  overview,
  runs,
  go,
  open,
}: {
  overview: QrOverview | null;
  runs: QrRunRow[];
  go: (s: Section) => void;
  open: (id: string, s: Section) => void;
}) {
  if (!overview) return <p className="text-[13px] text-fg-faint">Loading…</p>;
  const hub = overview.hub;
  const connected = hub.status === "CONNECTED";
  const active = runs.filter((r) => r.status === "QUEUED" || r.status === "RUNNING");
  const done = runs.filter((r) => r.status === "COMPLETED");
  const steps = [
    { title: "Connect Databento", text: "Paste your API key once — it's checked with a free metadata call and kept in the Keychain.", done: connected, go: "Data Hub" as Section },
    { title: "Get verified data", text: "Cost estimate first, your approval, then download, cache and a quality report.", done: hub.datasets > 0, go: "Data Hub" as Section },
    { title: "Describe a strategy", text: "In your words — JARVIS drafts exact rules; you review every assumption.", done: overview.strategies > 0, go: "Strategy Studio" as Section },
    { title: "Backtest honestly", text: "Bar-start timestamps, next-bar fills, real multipliers and costs, ambiguous minutes counted.", done: done.length > 0, go: "Strategy Studio" as Section },
    { title: "Validate", text: "Out-of-sample, walk-forward, cost stress, bootstrap, trial-adjusted Sharpe — then a verdict.", done: done.some((r) => r.kind === "validation"), go: "Validation" as Section },
  ];
  return (
    <div className="space-y-5" data-testid="qr-overview">
      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Market data" aside={hub.fixture ? <FixtureBadge label="Fixture provider" /> : <Badge tone={connected ? "positive" : "muted"} icon={connected ? "●" : "○"}>{connected ? "Databento connected" : "Not connected"}</Badge>} testId="qr-overview-data">
          <p className="text-2xl font-light text-fg tabular">{hub.datasets}</p>
          <p className="text-2xs text-fg-faint">verified dataset(s) · {hub.cache.length} cached series · {usd(hub.approved_this_month_usd)} approved this month</p>
          {hub.jobs_running > 0 && <p className="mt-2 text-[13px] text-ql">{hub.jobs_running} download(s) running</p>}
          <Button className="mt-3" onClick={() => go("Data Hub")}>Open Data Hub</Button>
        </Card>
        <Card title="Research">
          <p className="text-2xl font-light text-fg tabular">{overview.strategies}</p>
          <p className="text-2xs text-fg-faint">saved strateg{overview.strategies === 1 ? "y" : "ies"} · {done.length} completed run(s)</p>
          <Button className="mt-3" onClick={() => go("Strategy Studio")} data-testid="qr-new-strategy">New strategy</Button>
        </Card>
        <Card title="Running now">
          {active.length === 0 ? (
            <p className="text-[13px] text-fg-faint">Nothing running.</p>
          ) : (
            <ul className="space-y-3">
              {active.map((r) => (
                <li key={r.id}>
                  <p className="text-[13px] text-fg">{r.name} v{r.version_number} · {r.kind}</p>
                  <Progress value={r.progress} label={r.stage ?? r.status.toLowerCase()} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      {done.length === 0 && (
        <Card title="Getting started" testId="qr-getting-started">
          <ol className="grid gap-3 md:grid-cols-5">
            {steps.map((s, i) => (
              <li key={s.title} className="rounded-xl border border-ql-border bg-ql-raised p-3">
                <p className={cx("font-mono text-2xs", s.done ? "text-ql-positive" : "text-fg-faint")}>{s.done ? "✓ DONE" : `STEP ${i + 1}`}</p>
                <p className="mt-1 text-[14px] text-fg">{s.title}</p>
                <p className="mt-1 text-[12px] text-fg-muted">{s.text}</p>
                <Button className="mt-2 h-7" onClick={() => go(s.go)}>Go</Button>
              </li>
            ))}
          </ol>
          <p className="mt-3 text-2xs text-fg-faint">Nothing on this page is a prediction. No curve or number appears before a real run produced it.</p>
        </Card>
      )}
      {done.length > 0 && (
        <Card title="Recent runs" testId="qr-recent">
          <table className="w-full text-left text-[13px]">
            <tbody>
              {runs.slice(0, 8).map((r) => (
                <tr key={r.id} className="border-t border-ql-border/60">
                  <td className="py-2 text-fg">{r.name} <span className="text-fg-faint">v{r.version_number}</span></td>
                  <td className="text-fg-muted">{r.kind}{r.include_holdout ? " + holdout" : ""}</td>
                  <td className="font-mono text-2xs text-fg-faint">{r.symbol}</td>
                  <td>{r.status === "COMPLETED" ? <VerdictTag verdict={r.verdict} testId="qr-recent-verdict" /> : <RunBadge status={r.status} />}</td>
                  <td className={cx("text-right tabular", tone(r.oos_net))}>{r.oos_net === null ? "" : `OOS ${usd(r.oos_net, true)}`}</td>
                  <td className="text-right">{r.fixture && <FixtureBadge label="Fixture" />}</td>
                  <td className="text-right">
                    {r.status === "COMPLETED" && (
                      <Button className="h-7" onClick={() => open(r.id, r.kind === "validation" ? "Validation" : "Backtest Lab")}>Open</Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <AskJarvis />
    </div>
  );
}
