import type { ApexCause, ApexDataStatus, ApexMarketName, ApexSourceStatus, ApexSpread, ApexUpdateReport } from "@jarvis/protocol";

import { go } from "../../../lib/history";
import { Icon } from "../../ui/icons";
import { Button, cx } from "../../ui/primitives";
import { LoadingState, Panel, StatusPill, type PillTone } from "../../ui/shell";

/**
 * APEX → Data: where each source stands, the concrete cause of a blocked or doubtful analysis
 * and the action that fixes it. Repair re-checks every trading day without candles and
 * downloads what is missing — free Dukascopy data only, never a Data Vault purchase.
 */

export const CAUSE_TONE: Record<ApexCause["level"], PillTone> = { OK: "success", INFO: "info", WARN: "warning", BLOCK: "danger" };
const SOURCE_TONE: Record<ApexSourceStatus["state"], PillTone> = { ok: "success", warn: "warning", error: "danger", none: "muted" };
const SOURCE_TEXT: Record<ApexSourceStatus["state"], string> = { ok: "complete", warn: "check", error: "problem", none: "no data" };

const when = (epoch: number) => new Date(epoch * 1000).toLocaleString();

export function CauseList({
  causes,
  onRepair,
  repairing,
  compact,
}: {
  causes: ApexCause[];
  onRepair: () => void;
  repairing: boolean;
  compact?: boolean;
}) {
  const shown = compact ? causes.filter((c) => c.level !== "INFO" && c.level !== "OK") : causes;
  if (shown.length === 0) return null;
  // One repair fixes every cause that offers it: the button sits at the first of them.
  const repairAt = shown.findIndex((c) => c.actions.includes("repair"));
  const vaultAt = shown.findIndex((c) => c.actions.includes("vault"));
  return (
    <ul className="flex flex-col gap-2">
      {shown.map((c, k) => (
        <li
          key={c.code}
          className="flex flex-wrap items-start gap-x-3 gap-y-2 rounded-xl border border-hairline-strong bg-surface-2/40 px-3 py-2 text-[13px]"
          data-testid="apex-cause"
          data-code={c.code}
          data-level={c.level}
        >
          <StatusPill tone={CAUSE_TONE[c.level]}>{c.code.replace(/_/g, " ")}</StatusPill>
          <span className="min-w-0 flex-1 text-fg">{c.text}</span>
          <span className="flex gap-2">
            {k === repairAt && (
              <Button onClick={onRepair} disabled={repairing} reason={repairing ? "The repair is running." : null} data-testid="apex-repair">
                <Icon name="database" size={14} /> {repairing ? "Repairing…" : "Repair data"}
              </Button>
            )}
            {k === vaultAt && (
              <Button onClick={() => go({ name: "quantlab", section: "data-vault" })} data-testid="apex-open-vault">
                Open Data Vault
              </Button>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ApexData({
  market,
  status,
  onRepair,
  onAnalyse,
}: {
  market: ApexMarketName;
  status: ApexDataStatus | null;
  onRepair: () => void;
  onAnalyse: () => void;
}) {
  if (!status) return <LoadingState>Checking the {market} data…</LoadingState>;
  const repair = status.repair;
  const repairing = repair?.state === "RUNNING";
  return (
    <div className="flex flex-col gap-5" data-testid="apex-data-status">
      <Panel
        title={`Data status · ${market}`}
        aside={<span className="text-[12px] text-fg-muted">checked {when(status.checked_at)}</span>}
      >
        <CauseList causes={status.causes} onRepair={onRepair} repairing={repairing} />
        {repair && (
          <div className="mt-4 rounded-xl border border-hairline-strong bg-surface-2/40 p-3 text-[13px]" data-testid="apex-repair-state" data-state={repair.state}>
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill tone={repair.state === "COMPLETED" ? "success" : repair.state === "FAILED" ? "danger" : "info"}>
                {`Repair ${repair.state.toLowerCase()}`}
              </StatusPill>
              {repair.message && <span className="text-fg">{repair.message}</span>}
              {repair.state === "COMPLETED" && !repair.fixture && (
                <Button variant="primary" onClick={onAnalyse} data-testid="apex-reanalyse">
                  <Icon name="target" size={14} /> Analyse again
                </Button>
              )}
            </div>
            {repair.steps.length > 0 && (
              <ul className="mt-2 flex flex-col gap-1 text-[12px] text-fg-muted">
                {repair.steps.map((s, k) => (
                  <li key={k}>
                    <b className="text-fg">
                      {s.source}
                      {s.side ? ` · ${s.side.toLowerCase()}` : ""}
                    </b>{" "}
                    {s.skipped ?? (s.ok ? `ok${s.report ? ` — ${reportLine(s.report)}` : ""}` : `failed — ${s.error}`)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <p className="mt-3 text-[12px] leading-relaxed text-fg-faint">
          Repair asks Dukascopy again for every trading day in {status.span[0]} → {status.span[1]} that has no candles (also days
          stored as empty by older versions) and fetches the ask candles for the spread. It never buys data: futures bars come
          from the Data Vault. Quality checks stay on — a repaired analysis still shows every warning that applies.
        </p>
      </Panel>
      {status.sources.map((s) => (
        <SourceCard key={s.id} source={s} active={status.active === s.id} />
      ))}
    </div>
  );
}

function SourceCard({ source: s, active }: { source: ApexSourceStatus; active: boolean }) {
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          {s.label ?? s.id}
          {active && <span className="text-[11px] font-semibold tracking-normal text-quant normal-case">used by the analysis</span>}
        </span>
      }
      aside={<StatusPill tone={SOURCE_TONE[s.state]} testId="apex-source-state">{SOURCE_TEXT[s.state]}</StatusPill>}
      testId="apex-source"
    >
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-[180px_minmax(0,1fr)]" data-source={s.id} data-state={s.state}>
        <dt className="text-fg-faint">Source</dt>
        <dd className="font-mono text-fg">
          {s.id}
          {s.prices ? ` · ${s.prices} prices` : ""}
        </dd>
        {s.message && (
          <>
            <dt className="text-fg-faint">Status</dt>
            <dd className="text-fg">
              {s.message}
              {s.action ? ` ${s.action}` : ""}
            </dd>
          </>
        )}
        {s.coverage && (
          <>
            <dt className="text-fg-faint">Cached</dt>
            <dd className="text-fg">
              {s.coverage.first ?? "—"} → {s.coverage.last ?? "—"}
              {s.coverage.bars != null ? ` · ${s.coverage.bars.toLocaleString()} minute bars` : ""}
            </dd>
          </>
        )}
        {s.newest_bar != null && (
          <>
            <dt className="text-fg-faint">Newest cached bar</dt>
            <dd className="text-fg">
              {when(s.newest_bar)}
              {s.kind === "dukascopy" && (
                <>
                  {s.behind_minutes ? ` · ${s.behind_minutes.toLocaleString()} trading minutes before today are missing` : " · complete through yesterday"}
                  <span className="text-fg-faint"> (today's bars are fetched with each analysis)</span>
                </>
              )}
              {s.kind === "fixture" && <span className="text-fg-faint"> · generated up to now</span>}
            </dd>
          </>
        )}
        {s.days && (
          <>
            <dt className="text-fg-faint">Days in the span</dt>
            <dd className="text-fg">
              {Object.entries(s.days)
                .map(([state, n]) => `${n} ${state.replace("_", " ")}`)
                .join(" · ")}
            </dd>
          </>
        )}
        {s.suspicious && s.suspicious.length > 0 && (
          <>
            <dt className="text-fg-faint">Without candles</dt>
            <dd className="text-warning" data-testid="apex-missing-days">
              {s.suspicious
                .slice(0, 6)
                .map((r) => `${r.from === r.to ? r.from : `${r.from} → ${r.to}`} (${r.days} d, ${Object.keys(r.states).join("/").replace("_", " ")})`)
                .join(" · ")}
              {s.suspicious.length > 6 ? " …" : ""}
            </dd>
          </>
        )}
        {s.spread !== undefined && (
          <>
            <dt className="text-fg-faint">Bid / ask spread</dt>
            <dd className="text-fg">
              <SpreadText spread={s.spread} />
            </dd>
          </>
        )}
        {s.cache && (
          <>
            <dt className="text-fg-faint">Cache folder</dt>
            <dd className="font-mono text-[12px] break-all text-fg-muted">{s.cache}</dd>
          </>
        )}
      </dl>
      {s.updates && s.updates.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-fg-faint">
                <th className="pb-1.5 font-semibold">Update</th>
                <th className="pb-1.5 font-semibold">Side</th>
                <th className="pb-1.5 font-semibold">Result</th>
                <th className="pb-1.5 font-semibold">Answers</th>
              </tr>
            </thead>
            <tbody>
              {[...s.updates].reverse().map((r, k) => (
                <tr key={`${r.at}-${k}`} className="border-t border-hairline-strong/60 align-top" data-testid="apex-update-row" data-error={r.error ? "yes" : "no"}>
                  <td className="py-1 pr-3 whitespace-nowrap text-fg-muted">
                    {when(r.at)}
                    {r.recheck ? " · repair" : ""}
                  </td>
                  <td className="py-1 pr-3 text-fg-muted">{r.kind === "today" ? "today" : r.side.toLowerCase()}</td>
                  <td className={cx("py-1 pr-3", r.error ? "text-danger" : "text-fg")}>{r.error ?? reportLine(r)}</td>
                  <td className="py-1 font-mono text-fg-faint">
                    {r.answers
                      ? Object.entries(r.answers)
                          .map(([code, n]) => `${code}×${n}`)
                          .join(" ")
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export function SpreadText({ spread }: { spread: ApexSpread | null | undefined }) {
  if (!spread) return <span className="text-fg-faint">not measured</span>;
  if (!spread.measured) return <span className="text-fg-faint">{spread.reason ?? "not measured"}</span>;
  return (
    <span className={spread.exceeds_costs ? "text-warning" : undefined} data-testid="apex-spread">
      median {spread.session_median} in the session (90 %: {spread.session_p90}) · overall {spread.median} · costs assume{" "}
      {spread.cost_points}
      {spread.exceeds_costs ? " — above the costs" : ""}
    </span>
  );
}

function reportLine(r: ApexUpdateReport): string {
  if (r.kind === "today") return "today's candles";
  if (!r.asked) return "nothing to ask — the cache had every day";
  const parts = [`${r.asked} day(s) asked`, `${r.with_data ?? 0} with candles`];
  if (r.empty) parts.push(`${r.empty} empty`);
  if (r.missing) parts.push(`${r.missing} missing (404)`);
  if (r.pending) parts.push(`${r.pending} not published yet`);
  if (r.failed) parts.push(`${r.failed} failed`);
  return parts.join(", ");
}
