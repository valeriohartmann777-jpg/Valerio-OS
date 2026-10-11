import type { BlAdvanced, BlCostProfile, BlOptions, BlReadiness, BlReadinessStatus, BlStrategy } from "@jarvis/protocol";
import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../../../lib/api";
import { Icon } from "../../ui/icons";
import { Button, cx } from "../../ui/primitives";
import { FixtureBadge, verdictLabel } from "../research/common";
import { Badge, Card, inputClass } from "../ui";

/**
 * Backtest Lab launcher: Strategy × Market × Timeframe → Backtest. Runs only on local data —
 * the readiness badge comes from the Data Vault's real catalog, never from an API key.
 */

const READY_TONE: Record<BlReadinessStatus, "positive" | "warning" | "danger" | "muted"> = {
  READY: "positive",
  PARTIAL: "warning",
  WRONG_GRANULARITY: "danger",
  MISSING_MAPPINGS: "danger",
  INVALID: "danger",
  NOT_AVAILABLE: "muted",
  INCOMPATIBLE: "muted",
};
const READY_ICON: Record<BlReadinessStatus, string> = {
  READY: "●",
  PARTIAL: "◐",
  WRONG_GRANULARITY: "≠",
  MISSING_MAPPINGS: "?",
  INVALID: "✕",
  NOT_AVAILABLE: "○",
  INCOMPATIBLE: "⊘",
};

export function ReadinessBadge({ r, testId }: { r: BlReadiness; testId?: string }) {
  return (
    <Badge tone={READY_TONE[r.status]} icon={READY_ICON[r.status]} title={r.message} testId={testId}>
      {r.label}
    </Badge>
  );
}

export function BacktestLauncher({
  tick,
  onRun,
  onVault,
  onIdeas,
  teamProject,
}: {
  tick: string | null;
  onRun: (runId: string) => void;
  onVault: () => void;
  onIdeas?: () => void;
  /**
   * The ULTRON QuantLab uses this same form: with a team project it lists only that
   * project's strategy and starts runs through the team endpoint (same engine, same vault).
   */
  teamProject?: string;
}) {
  const [options, setOptions] = useState<BlOptions | null>(null);
  const [query, setQuery] = useState("");
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [symbol, setSymbol] = useState("NQ");
  const [timeframe, setTimeframe] = useState("5m");
  const [rows, setRows] = useState<BlReadiness[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [advanced, setAdvanced] = useState<BlAdvanced>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [launched, setLaunched] = useState<string | null>(null);

  useEffect(() => {
    api.blOptions(teamProject ? `team:${teamProject}` : undefined).then(
      (o) => {
        setOptions(o);
        setStrategyId((current) => current ?? o.strategies.find((s) => s.state === "READY")?.id ?? o.strategies[0]?.id ?? null);
      },
      (e: unknown) => setError(e instanceof ApiError ? e.message : "Backtest Lab isn't reachable."),
    );
  }, [tick, teamProject]);

  const strategy = options?.strategies.find((s) => s.id === strategyId) ?? null;
  useEffect(() => {
    let live = true;
    api.blReadiness(symbol, strategy?.version_id, advanced.start, advanced.end).then(
      (r) => live && setRows(r.timeframes),
      () => live && setRows([]),
    );
    return () => {
      live = false;
    };
  }, [symbol, strategy?.version_id, advanced.start, advanced.end, tick]);

  const current = rows.find((r) => r.timeframe === timeframe) ?? null;
  const market = options?.markets.find((m) => m.symbol === symbol) ?? null;
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (options?.strategies ?? []).filter((s) => !q || `${s.name} ${s.product} ${s.rule}`.toLowerCase().includes(q));
  }, [options, query]);

  const launch = async () => {
    if (!strategy) return;
    setBusy(true);
    setError(null);
    try {
      const body = { version_id: strategy.version_id, symbol, timeframe, advanced };
      const out = teamProject ? await api.tmBacktest(teamProject, body) : await api.blStart(body);
      setLaunched(out.run.id);
      onRun(out.run.id);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.message}${e.suggestion ? ` — ${e.suggestion}` : ""}` : "The backtest couldn't start.");
    } finally {
      setBusy(false);
    }
  };

  const set = <K extends keyof BlAdvanced>(key: K, value: BlAdvanced[K]) =>
    setAdvanced((a) => ({ ...a, [key]: value === "" || value === undefined ? null : value }));

  return (
    <Card
      title="Backtest"
      aside={
        <span className="flex items-center gap-2">
          {options?.offline && (
            <Badge tone="accent" icon="⏻" testId="bl-offline">
              Offline mode
            </Badge>
          )}
          {current?.series?.fixture && <FixtureBadge label="Fixture data · synthetic" />}
        </span>
      }
      testId="bl-launcher"
    >
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1.2fr)]">
        <div>
          <p className="label mb-2">1 · Strategy</p>
          <input
            className={cx(inputClass, "mb-2")}
            placeholder="Search saved strategies…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            data-testid="bl-strategy-search"
          />
          <div className="max-h-56 space-y-1 overflow-y-auto pr-1" role="listbox" aria-label="Strategy" data-testid="bl-strategies">
            {filtered.map((s) => (
              <button
                key={s.id}
                type="button"
                role="option"
                aria-selected={s.id === strategyId}
                onClick={() => setStrategyId(s.id)}
                className={cx(
                  "no-drag block w-full rounded-lg border px-3 py-2 text-left",
                  s.id === strategyId ? "border-ql/50 bg-ql-raised" : "border-ql-border hover:border-ql-border-strong",
                )}
                data-testid="bl-strategy"
              >
                <span className="flex items-center justify-between gap-2 text-[13px] text-fg">
                  <span className="truncate">{s.name}</span>
                  <span className="shrink-0 font-mono text-2xs text-fg-faint">v{s.version_number}</span>
                </span>
                <span className="mt-0.5 block truncate text-2xs text-fg-faint">
                  {s.product} · {s.rule.replace(/_/g, " ")} · {s.state === "DRAFT" ? "draft (open questions)" : s.last_verdict ? verdictLabel(s.last_verdict as never) : "not yet validated"}
                </span>
              </button>
            ))}
            {options && filtered.length === 0 && (
              <p className="text-[12px] text-fg-faint">No saved strategy{query ? " matches" : " yet"}.</p>
            )}
          </div>
          {onIdeas && (
            <button type="button" className="no-drag mt-2 text-xs text-ql hover:underline" onClick={onIdeas} data-testid="bl-create">
              + Create with JARVIS (Idea Inbox)
            </button>
          )}
        </div>

        <div>
          <p className="label mb-2">2 · Market</p>
          <div className="grid grid-cols-2 gap-1.5" role="radiogroup" aria-label="Market">
            {(options?.markets ?? []).map((m) => (
              <button
                key={m.symbol}
                type="button"
                role="radio"
                aria-checked={symbol === m.symbol}
                onClick={() => setSymbol(m.symbol)}
                className={cx(
                  "no-drag rounded-lg border px-3 py-2 text-left",
                  symbol === m.symbol ? "border-ql/50 bg-ql-raised" : "border-ql-border hover:border-ql-border-strong",
                )}
                data-testid={`bl-market-${m.symbol}`}
              >
                <span className="block font-mono text-[13px] text-fg">{m.symbol}</span>
                <span className="block text-2xs text-fg-faint">${m.tick_value.toFixed(2)} / tick · ×{m.multiplier}</span>
              </button>
            ))}
          </div>
          {market && (
            <p className="mt-2 text-2xs leading-relaxed text-fg-faint">
              {market.name}, {market.venue}. {market.not}{" "}
              {market.local.length ? `Local: ${market.local.map((l) => `${l.symbol} ${l.start} → ${l.end}`).join(", ")}.` : "No local data yet."}
            </p>
          )}
        </div>

        <div>
          <p className="label mb-2">3 · Timeframe</p>
          <div className="grid grid-cols-4 gap-1.5" role="radiogroup" aria-label="Timeframe">
            {(options?.timeframes ?? []).map((t) => {
              const r = rows.find((x) => x.timeframe === t.label);
              const ok = r?.runnable ?? false;
              return (
                <button
                  key={t.label}
                  type="button"
                  role="radio"
                  aria-checked={timeframe === t.label}
                  onClick={() => setTimeframe(t.label)}
                  title={r ? `${r.label}: ${r.message}` : undefined}
                  className={cx(
                    "no-drag rounded-lg border py-1.5 font-mono text-[13px]",
                    timeframe === t.label ? "border-ql/50 bg-ql-raised text-fg" : "border-ql-border",
                    ok ? "text-fg-muted hover:text-fg" : "text-fg-faint line-through decoration-fg-faint/40",
                  )}
                  data-testid={`bl-tf-${t.label}`}
                  data-ready={ok ? "yes" : "no"}
                >
                  {t.label}
                </button>
              );
            })}
          </div>
          {current && (
            <div className="mt-3 space-y-1.5" data-testid="bl-readiness" data-status={current.status}>
              <ReadinessBadge r={current} testId="bl-readiness-badge" />
              <p className="text-[12px] leading-relaxed text-fg-muted">{current.message}</p>
              {current.series && (
                <p className="text-2xs text-fg-faint">
                  Source: {current.series.source} · {current.series.symbol}
                  {current.range ? ` · ${current.range.start} → ${current.range.end}` : ""} · built from {current.series.source_minutes}m bars
                </p>
              )}
              {current.compatibility?.notes.length ? (
                <ul className="list-disc space-y-0.5 pl-4 text-2xs leading-relaxed text-fg-faint" data-testid="bl-notes">
                  {current.compatibility.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              ) : null}
              {!current.runnable && (current.status === "NOT_AVAILABLE" || current.status === "PARTIAL" || current.status === "WRONG_GRANULARITY" || current.status === "INVALID" || current.status === "MISSING_MAPPINGS") && (
                <button type="button" className="no-drag text-xs text-ql hover:underline" onClick={onVault} data-testid="bl-open-vault">
                  Open Data Vault{current.missing?.length ? ` — missing: ${current.missing.map((m) => `${m.symbol} ${m.schema}${m.start ? ` ${m.start} → ${m.end}` : ""}`).join("; ")}` : ""}
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="mt-5 border-t border-ql-border pt-4">
        <button
          type="button"
          className="no-drag text-xs text-fg-muted hover:text-fg"
          onClick={() => setAdvancedOpen((v) => !v)}
          aria-expanded={advancedOpen}
          data-testid="bl-advanced-toggle"
        >
          {advancedOpen ? "▾" : "▸"} Advanced settings
        </button>
        {advancedOpen && (
          <div className="mt-3 grid gap-3 text-[12px] sm:grid-cols-2 lg:grid-cols-4" data-testid="bl-advanced">
            <label className="space-y-1 text-fg-faint">
              <span>Start (UTC day)</span>
              <input type="date" className={inputClass} value={advanced.start ?? ""} onChange={(e) => set("start", e.target.value)} />
            </label>
            <label className="space-y-1 text-fg-faint">
              <span>End (exclusive)</span>
              <input type="date" className={inputClass} value={advanced.end ?? ""} onChange={(e) => set("end", e.target.value)} />
            </label>
            <label className="space-y-1 text-fg-faint">
              <span>Cost profile</span>
              <select className={inputClass} value={advanced.cost_profile ?? ""} onChange={(e) => set("cost_profile", (e.target.value || null) as BlAdvanced["cost_profile"])}>
                <option value="">Strategy's own costs{strategy && strategy.product !== symbol ? ` (other market → Conservative)` : ""}</option>
                {options?.cost_profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-fg-faint">
              <span>Run</span>
              <select className={inputClass} value={advanced.kind ?? "backtest"} onChange={(e) => set("kind", e.target.value as BlAdvanced["kind"])}>
                <option value="backtest">Backtest</option>
                <option value="validation">Backtest + validation suite</option>
              </select>
            </label>
            <label className="space-y-1 text-fg-faint">
              <span>Contracts</span>
              <input type="number" min={1} className={inputClass} value={advanced.contracts ?? ""} onChange={(e) => set("contracts", e.target.value ? Number(e.target.value) : null)} />
            </label>
            <label className="space-y-1 text-fg-faint">
              <span>Capital (USD)</span>
              <input type="number" min={1} className={inputClass} value={advanced.account_capital ?? ""} onChange={(e) => set("account_capital", e.target.value ? Number(e.target.value) : null)} />
            </label>
            <label className="space-y-1 text-fg-faint">
              <span>Direction</span>
              <select className={inputClass} value={advanced.direction ?? ""} onChange={(e) => set("direction", (e.target.value || null) as BlAdvanced["direction"])}>
                <option value="">As in the strategy</option>
                <option value="long">Long only</option>
                <option value="short">Short only</option>
                <option value="both">Both</option>
              </select>
            </label>
            <label className="space-y-1 text-fg-faint">
              <span>Slippage (ticks per side)</span>
              <input type="number" min={0} className={inputClass} value={advanced.slippage_ticks ?? ""} onChange={(e) => set("slippage_ticks", e.target.value ? Number(e.target.value) : null)} />
            </label>
            <label className="space-y-1 text-fg-faint">
              <span>Commission / contract / side</span>
              <input type="number" min={0} step={0.01} className={inputClass} value={advanced.commission_per_contract_side ?? ""} onChange={(e) => set("commission_per_contract_side", e.target.value ? Number(e.target.value) : null)} />
            </label>
            <label className="space-y-1 text-fg-faint">
              <span>Exchange fees / contract / side</span>
              <input type="number" min={0} step={0.01} className={inputClass} value={advanced.exchange_fees_per_contract_side ?? ""} onChange={(e) => set("exchange_fees_per_contract_side", e.target.value ? Number(e.target.value) : null)} />
            </label>
            <label className="space-y-1 text-fg-faint">
              <span>Limit fills</span>
              <select className={inputClass} value={advanced.limit_fill ?? ""} onChange={(e) => set("limit_fill", (e.target.value || null) as BlAdvanced["limit_fill"])}>
                <option value="">As in the strategy</option>
                <option value="trade_through">Price must trade through (conservative)</option>
                <option value="touch">Touch is enough</option>
              </select>
            </label>
            <p className="self-end text-2xs leading-relaxed text-fg-faint">
              Session, fills and ambiguity rules come from the strategy version. Every value here becomes part of the run's
              identity and its report.
            </p>
          </div>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-ql-border pt-4">
        <Button
          variant="primary"
          size="lg"
          disabled={busy || !strategy || strategy.state !== "READY" || !current?.runnable}
          reason={
            !strategy
              ? "Pick a saved strategy first."
              : strategy.state !== "READY"
                ? "This strategy still has open questions — finish it in the Strategy Studio."
                : current && !current.runnable
                  ? current.message
                  : null
          }
          onClick={() => void launch()}
          data-testid="bl-run"
        >
          <Icon name="play" size={15} /> {busy ? "Starting…" : "Run backtest"}
        </Button>
        {strategy && current?.runnable && (
          <span className="text-[12px] text-fg-muted" data-testid="bl-assumptions">
            {assumptions(strategy, current, advanced, options?.cost_profiles ?? []).join(" · ")}
          </span>
        )}
        <span className="basis-full text-2xs text-fg-faint">
          {options?.network ?? "Local data only."} Changing the timeframe never buys data.
        </span>
        {strategy?.state === "DRAFT" && <span className="text-2xs text-ql-warning">Answer the strategy's open questions first.</span>}
        {launched && !error && <span className="text-2xs text-fg-muted" data-testid="bl-launched">Run {launched.slice(0, 12)} queued — results below.</span>}
        {error && (
          <span className="text-[12px] text-ql-danger" data-testid="bl-error">
            {error}
          </span>
        )}
      </div>
    </Card>
  );
}

/** The material assumptions of the run about to start, in one line next to Run (brief §6.1). */
function assumptions(strategy: BlStrategy, ready: BlReadiness, advanced: BlAdvanced, profiles: BlCostProfile[]): string[] {
  const range = advanced.start || advanced.end ? `${advanced.start ?? "start"} → ${advanced.end ?? "end"}` : ready.range ? `${ready.range.start} → ${ready.range.end}` : "all local data";
  const profile = profiles.find((p) => p.id === advanced.cost_profile);
  return [
    range,
    `session ${strategy.session}`,
    advanced.kind === "validation" ? "validation (in-sample → out-of-sample, holdout sealed)" : "backtest",
    profile ? `costs: ${profile.label}` : "costs from the strategy",
    advanced.contracts ? `${advanced.contracts} contract${advanced.contracts === 1 ? "" : "s"}` : "size from the strategy",
    advanced.direction && advanced.direction !== "both" ? `${advanced.direction} only` : "",
  ].filter(Boolean);
}
