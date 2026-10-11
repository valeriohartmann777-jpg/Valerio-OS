import type { QvAcquisition, QvImport, QvOverview, QvSeries } from "@jarvis/protocol";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../../../lib/api";
import { Button, cx } from "../../ui/primitives";
import { FixtureBadge, Kv, bytes, usd } from "../research/common";
import { Badge, Card, inputClass } from "../ui";

/**
 * QuantLab → Data Vault: the permanent local archive. Everything shown comes from the real
 * catalog (no invented coverage, no invented prices). Imports and backtests are local; the one
 * paid path — a Databento batch purchase — needs a free quote, your explicit approval and
 * offline mode off.
 */

const errorText = (e: unknown, fallback: string) =>
  e instanceof ApiError ? `${e.message}${e.suggestion ? ` — ${e.suggestion}` : ""}` : fallback;

const GRADE_TONE: Record<string, "positive" | "warning" | "danger" | "muted"> = { A: "positive", B: "warning", C: "warning", F: "danger" };

export function DataVault({ tick, focusSymbol }: { tick: string | null; focusSymbol?: string | null }) {
  const [overview, setOverview] = useState<QvOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const reload = useCallback(() => {
    api.qvOverview().then(
      (o) => {
        setOverview(o);
        setError(null);
      },
      (e: unknown) => setError(errorText(e, "The Data Vault isn't reachable.")),
    );
  }, []);
  useEffect(() => reload(), [reload, tick]);

  const run = async (action: () => Promise<unknown>, done?: (r: unknown) => string) => {
    setError(null);
    setNote(null);
    try {
      const r = await action();
      if (done) setNote(done(r));
      reload();
    } catch (e) {
      setError(errorText(e, "That didn't work."));
    }
  };

  if (!overview) return <p className="text-[13px] text-fg-faint">{error ?? "Loading the vault…"}</p>;
  const parts = overview.storage.parts;
  return (
    <div className="space-y-5" data-testid="qv-vault">
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Card
          title="Local archive"
          aside={
            <span className="flex items-center gap-2">
              <Badge tone={overview.offline ? "accent" : "muted"} icon="⏻" testId="qv-offline-state">
                {overview.offline ? "Offline mode on" : "Online"}
              </Badge>
              <Button
                onClick={() => void run(() => api.qvOffline(!overview.offline))}
                data-testid="qv-offline-toggle"
                title="Hard switch: blocks every market-data request (quotes, purchases)"
              >
                {overview.offline ? "Go online" : "Offline mode"}
              </Button>
            </span>
          }
        >
          <SeriesTable series={overview.series} focus={focusSymbol ?? null} />
          <p className="mt-3 text-2xs leading-relaxed text-fg-faint">
            Coverage is shown per trading day against the NYSE calendar; gaps are listed, never assumed filled. Backtests read only
            from here — a Databento key is never needed to re-run them. {overview.license}
          </p>
        </Card>
        <Card title="Storage & safety">
          <dl>
            <Kv label="Raw originals">{bytes(parts.raw?.bytes ?? 0)} · {parts.raw?.files ?? 0} files</Kv>
            <Kv label="Day files (Parquet)">{bytes(parts.canonical?.bytes ?? 0)}</Kv>
            <Kv label="Snapshots">{bytes(parts.snapshots?.bytes ?? 0)}</Kv>
            <Kv label="Derived timeframes">{bytes(parts.derived?.bytes ?? 0)} · rebuildable</Kv>
            <Kv label="Disk free">{bytes(overview.storage.disk_free_bytes)}</Kv>
          </dl>
          {overview.backup_warning && (
            <p className="mt-3 text-[12px] text-ql-warning" data-testid="qv-backup-warning">
              {overview.backup_warning}
            </p>
          )}
          {overview.issues.length > 0 && (
            <ul className="mt-3 space-y-1 text-[12px] text-ql-danger" data-testid="qv-issues">
              {overview.issues.map((i) => (
                <li key={i.id}>
                  {i.kind}: {String((i.detail as { message?: string }).message ?? i.subject)}
                </li>
              ))}
            </ul>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              onClick={() =>
                void run(() => api.qvAudit(true), (r) => {
                  const a = r as { checked: number; problems: unknown[]; repaired: unknown[] };
                  return `Audit: ${a.checked} files checked, ${a.problems.length} problem(s), ${a.repaired.length} repaired.`;
                })
              }
              data-testid="qv-audit"
            >
              Audit / Repair
            </Button>
            <Button
              onClick={() =>
                void run(api.qvRebuild, (r) => {
                  const b = r as { rebuilt: { identical: boolean }[] };
                  return `Rebuilt ${b.rebuilt.length} derived timeframe(s) locally; ${b.rebuilt.filter((x) => x.identical).length} bit-identical.`;
                })
              }
              data-testid="qv-rebuild"
            >
              Rebuild derived bars
            </Button>
          </div>
          {overview.last_audit && (
            <p className="mt-2 text-2xs text-fg-faint">
              Last audit {overview.last_audit.at.slice(0, 16).replace("T", " ")}: {overview.last_audit.ok ? "all files intact" : `${overview.last_audit.problems.length} problem(s)`}.
            </p>
          )}
          <Backup overview={overview} run={run} />
        </Card>
      </div>
      {(note || error) && (
        <p className={cx("text-[13px]", error ? "text-ql-danger" : "text-fg-muted")} role="status" data-testid={error ? "qv-error" : "qv-note"}>
          {error ?? note}
        </p>
      )}
      <div className="grid gap-5 lg:grid-cols-2">
        <ImportFiles onDone={reload} />
        <Acquire overview={overview} onDone={reload} />
      </div>
      <Alternatives overview={overview} />
    </div>
  );
}

function SeriesTable({ series, focus }: { series: QvSeries[]; focus: string | null }) {
  if (!series.length) {
    return (
      <p className="text-[13px] text-fg-muted" data-testid="qv-empty">
        The vault is empty. Import a file you already have, or acquire history once from Databento (after a free quote and your
        approval).
      </p>
    );
  }
  return (
    <table className="w-full text-[12px]" data-testid="qv-series">
      <thead className="text-2xs text-fg-faint">
        <tr>
          <th className="py-1 text-left font-normal">Series</th>
          <th className="py-1 text-left font-normal">Source</th>
          <th className="py-1 text-left font-normal">Coverage [start, end)</th>
          <th className="py-1 text-right font-normal">Sessions</th>
          <th className="py-1 pl-4 text-left font-normal">Bars</th>
          <th className="py-1 text-left font-normal">Quality</th>
        </tr>
      </thead>
      <tbody className="text-fg-muted">
        {series.map((s) => (
          <tr key={s.key} className={cx("border-t border-ql-border", focus && s.root === focus && "bg-ql-raised")} data-testid="qv-series-row">
            <td className="py-1.5 font-mono text-fg">{s.symbol}</td>
            <td className="py-1.5">{s.fixture ? <FixtureBadge label="Fixture · synthetic" /> : s.source}</td>
            <td className="py-1.5 font-mono">
              {s.start} → {s.end}
              {s.holes.length > 0 && <span className="ml-1 text-ql-warning" title={s.holes.map((h) => `${h.start} → ${h.end}`).join(", ")}>· {s.holes.length} gap(s)</span>}
            </td>
            <td className="py-1.5 text-right">
              {s.sessions_with_bars}/{s.sessions}
            </td>
            <td className="py-1.5 pl-4">
              {s.source_minutes}m{s.derived.length ? ` → ${s.derived.join(", ")}` : ""}
            </td>
            <td className="py-1.5">
              <Badge tone={GRADE_TONE[s.grade] ?? "muted"} title={s.mapped ? "Contract mapping present" : "No contract per bar"}>
                {s.grade}
                {!s.mapped ? " · unmapped" : ""}
                {s.corrupt.length ? " · damaged" : ""}
              </Badge>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ImportFiles({ onDone }: { onDone: () => void }) {
  const [staged, setStaged] = useState<QvImport | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QvImport | null>(null);

  const stage = async (action: () => Promise<QvImport>) => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const s = await action();
      if (s.duplicate) {
        setResult(s);
        setStaged(null);
      } else {
        setStaged(s);
        const guess: Record<string, string> = { contract_mode: s.preview.mapping.contract ? "column" : "none", timestamp_marks: "start" };
        for (const [k, v] of Object.entries(s.preview.mapping)) if (v) guess[k] = v;
        if (s.preview.contract_roots.length === 1) guess.root = s.preview.contract_roots[0]!;
        setMapping(guess);
      }
    } catch (e) {
      setError(errorText(e, "The file couldn't be read."));
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    if (!staged) return;
    setBusy(true);
    setError(null);
    try {
      const done = await api.qvCommit(staged.id, mapping);
      setResult(done);
      setStaged(null);
      onDone();
    } catch (e) {
      setError(errorText(e, "The import was refused."));
    } finally {
      setBusy(false);
    }
  };

  const columns = staged?.preview.columns ?? [];
  const colSelect = (key: string, label: string, optional = false) => (
    <label className="space-y-1 text-2xs text-fg-faint" key={key}>
      <span>{label}</span>
      <select className={inputClass} value={mapping[key] ?? ""} onChange={(e) => setMapping((m) => ({ ...m, [key]: e.target.value }))} data-testid={`qv-map-${key}`}>
        <option value="">{optional ? "— none —" : "— choose —"}</option>
        {columns.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </label>
  );
  const isDbn = staged?.format === "dbn";
  return (
    <Card title="Import files" testId="qv-import">
      <p className="mb-3 text-[12px] leading-relaxed text-fg-muted">
        DBN / DBN.ZST (from Databento), Parquet or CSV. The original is kept unchanged; bars are checked (timestamps, OHLC, tick grid,
        duplicates) and never labelled exchange-verified unless they are Databento's own files.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <label className="no-drag cursor-pointer rounded-lg border border-ql-border px-3 py-1.5 text-[12px] text-fg-muted hover:text-fg">
          Choose file…
          <input
            type="file"
            className="hidden"
            accept=".csv,.txt,.parquet,.dbn,.zst"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void stage(() => api.qvUpload(file));
              e.target.value = "";
            }}
            data-testid="qv-file"
          />
        </label>
        <span className="text-2xs text-fg-faint">or a path on this Mac:</span>
        <input className={cx(inputClass, "w-64")} placeholder="/Users/you/Downloads/nq-1m.dbn.zst" value={path} onChange={(e) => setPath(e.target.value)} />
        <Button disabled={!path.trim() || busy} onClick={() => void stage(() => api.qvImportPath(path.trim()))}>
          Read
        </Button>
      </div>
      {busy && <p className="mt-2 text-[12px] text-fg-faint">Working…</p>}
      {staged && (
        <div className="mt-4 space-y-3" data-testid="qv-preview">
          <p className="text-[12px] text-fg">
            {staged.filename} · {staged.format.toUpperCase()} · {bytes(staged.bytes)}
            {staged.preview.dbn && ` · ${staged.preview.dbn.dataset} ${staged.preview.dbn.schema} ${staged.preview.dbn.symbols.join(", ")}`}
          </p>
          {!isDbn && staged.preview.sample.length > 0 && (
            <div className="overflow-x-auto">
              <table className="text-2xs text-fg-muted">
                <thead>
                  <tr>
                    {columns.map((c) => (
                      <th key={c} className="px-2 py-1 text-left font-normal text-fg-faint">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {staged.preview.sample.slice(0, 4).map((row, i) => (
                    <tr key={i}>
                      {columns.map((c) => (
                        <td key={c} className="px-2 py-0.5 font-mono">
                          {String(row[c] ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="grid gap-2 sm:grid-cols-3">
            <label className="space-y-1 text-2xs text-fg-faint">
              <span>Product</span>
              <select className={inputClass} value={mapping.root ?? ""} onChange={(e) => setMapping((m) => ({ ...m, root: e.target.value }))} data-testid="qv-map-root">
                <option value="">— choose —</option>
                {["NQ", "MNQ", "ES", "MES"].map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
            {!isDbn && (
              <>
                {colSelect("timestamp", "Timestamp (bar time)")}
                <label className="space-y-1 text-2xs text-fg-faint">
                  <span>Timestamps mark</span>
                  <select className={inputClass} value={mapping.timestamp_marks ?? "start"} onChange={(e) => setMapping((m) => ({ ...m, timestamp_marks: e.target.value }))}>
                    <option value="start">the bar's start</option>
                    <option value="end">the bar's end</option>
                  </select>
                </label>
                {staged.preview.needs.includes("timezone") && (
                  <label className="space-y-1 text-2xs text-fg-faint">
                    <span>Timezone of the timestamps</span>
                    <select className={inputClass} value={mapping.timezone ?? ""} onChange={(e) => setMapping((m) => ({ ...m, timezone: e.target.value }))} data-testid="qv-map-timezone">
                      <option value="">— choose —</option>
                      {["UTC", "America/New_York", "America/Chicago", "Europe/Zurich", "Europe/London"].map((z) => (
                        <option key={z} value={z}>
                          {z}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {colSelect("open", "Open")}
                {colSelect("high", "High")}
                {colSelect("low", "Low")}
                {colSelect("close", "Close")}
                {colSelect("volume", "Volume", true)}
                <label className="space-y-1 text-2xs text-fg-faint">
                  <span>Contract month per bar</span>
                  <select className={inputClass} value={mapping.contract_mode ?? "none"} onChange={(e) => setMapping((m) => ({ ...m, contract_mode: e.target.value }))} data-testid="qv-map-contract-mode">
                    <option value="column">From a column (e.g. NQZ5)</option>
                    <option value="single">The whole file is one contract</option>
                    <option value="none">Not stated (can't backtest across rolls)</option>
                  </select>
                </label>
                {mapping.contract_mode === "column" && colSelect("contract", "Contract column")}
                {mapping.contract_mode === "single" && (
                  <label className="space-y-1 text-2xs text-fg-faint">
                    <span>Contract</span>
                    <input className={inputClass} placeholder="NQZ5" value={mapping.single_contract ?? ""} onChange={(e) => setMapping((m) => ({ ...m, single_contract: e.target.value }))} />
                  </label>
                )}
              </>
            )}
          </div>
          <p className="text-2xs text-fg-faint">{staged.preview.note}</p>
          <Button variant="primary" disabled={busy || !mapping.root} onClick={() => void commit()} data-testid="qv-commit">
            Import into the vault
          </Button>
        </div>
      )}
      {result && (
        <div className="mt-3 space-y-1 text-[12px]" data-testid="qv-import-result">
          {result.duplicate ? (
            <p className="text-fg-muted">This exact file was imported before ({result.filename}) — nothing to do.</p>
          ) : (
            <p className="text-fg-muted">
              {result.status === "COMMITTED" ? "Imported" : result.status}: {result.rows_ok.toLocaleString("en-US")} bars of {result.symbol} ({result.source_minutes}m),{" "}
              {result.days_added} new day(s){result.rows_rejected ? `, ${result.rows_rejected} row(s) rejected` : ""}.
            </p>
          )}
          {result.findings.map((f) => (
            <p key={f.code} className={f.level === "BLOCK" ? "text-ql-danger" : f.level === "WARN" ? "text-ql-warning" : "text-fg-faint"}>
              {f.level}: {f.message}
            </p>
          ))}
        </div>
      )}
      {error && (
        <p className="mt-3 text-[12px] text-ql-danger" data-testid="qv-import-error">
          {error}
        </p>
      )}
    </Card>
  );
}

const ACQ_TONE: Record<string, "positive" | "warning" | "danger" | "muted" | "accent"> = {
  QUOTED: "accent",
  APPROVED: "warning",
  SUBMITTING: "warning",
  SUBMITTED: "warning",
  DOWNLOADING: "warning",
  INGESTING: "warning",
  COMPLETED: "positive",
  NOTHING_TO_BUY: "positive",
  ATTENTION: "danger",
  FAILED: "danger",
  EXPIRED: "muted",
  CANCELED: "muted",
};

function Acquire({ overview, onDone }: { overview: QvOverview; onDone: () => void }) {
  const [symbol, setSymbol] = useState("NQ");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [definitions, setDefinitions] = useState(true);
  const [acq, setAcq] = useState<QvAcquisition | null>(null);
  const [maxUsd, setMaxUsd] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const status = overview.provider.databento.status;

  useEffect(() => {
    if (!acq || !["SUBMITTING", "SUBMITTED", "DOWNLOADING", "INGESTING", "APPROVED"].includes(acq.status)) return;
    const timer = setInterval(() => {
      api.qvAcquisitions().then((all) => {
        const now = all.find((a) => a.id === acq.id);
        if (now) setAcq(now);
        if (now?.status === "COMPLETED") onDone();
      }, () => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [acq, onDone]);

  const act = async (fn: () => Promise<QvAcquisition>) => {
    setBusy(true);
    setError(null);
    try {
      const a = await fn();
      setAcq(a);
      if (a.quote && !maxUsd) setMaxUsd(String(Math.ceil((a.quote.cost_usd + 0.005) * 100) / 100));
    } catch (e) {
      setError(errorText(e, "That didn't work."));
    } finally {
      setBusy(false);
    }
  };

  const updateMissing = (s: QvSeries) => {
    setSymbol(s.root ?? "NQ");
    setStart(s.start);
    setEnd(new Date(Date.now() - 86_400_000).toISOString().slice(0, 10));
  };
  const databentoSeries = overview.series.filter((s) => s.provider === "databento");
  return (
    <Card
      title="Acquire once with Databento"
      aside={
        overview.provider.databento.fixture_label ? (
          <FixtureBadge label="Fixture provider" />
        ) : (
          <Badge tone={status === "CONNECTED" ? "positive" : "muted"}>{status === "CONNECTED" ? "Key connected" : "No key"}</Badge>
        )
      }
      testId="qv-acquire"
    >
      <p className="mb-3 text-[12px] leading-relaxed text-fg-muted">
        A one-time batch purchase of exactly the days you don't have (continuous {symbol}.v.0, ohlcv-1m{definitions ? " + contract definitions" : ""}).
        The quote is free and real (Databento metadata.get_cost); nothing is bought until you approve the amount. Data purchases are
        separate from your AI budget.
      </p>
      {databentoSeries.length > 0 && (
        <p className="mb-2 text-2xs text-fg-faint">
          Update only missing periods:{" "}
          {databentoSeries.map((s) => (
            <button key={s.key} type="button" className="no-drag mr-2 text-ql hover:underline" onClick={() => updateMissing(s)}>
              {s.symbol}: gaps + up to yesterday
            </button>
          ))}
        </p>
      )}
      <div className="grid gap-2 sm:grid-cols-4">
        <label className="space-y-1 text-2xs text-fg-faint">
          <span>Market</span>
          <select className={inputClass} value={symbol} onChange={(e) => setSymbol(e.target.value)} data-testid="qv-acq-symbol">
            {["NQ", "MNQ", "ES", "MES"].map((r) => (
              <option key={r} value={r}>
                {r}.v.0
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-2xs text-fg-faint">
          <span>From (UTC day)</span>
          <input type="date" className={inputClass} value={start} onChange={(e) => setStart(e.target.value)} data-testid="qv-acq-start" />
        </label>
        <label className="space-y-1 text-2xs text-fg-faint">
          <span>To (exclusive)</span>
          <input type="date" className={inputClass} value={end} onChange={(e) => setEnd(e.target.value)} data-testid="qv-acq-end" />
        </label>
        <label className="flex items-end gap-2 pb-1 text-2xs text-fg-faint">
          <input type="checkbox" checked={definitions} onChange={(e) => setDefinitions(e.target.checked)} /> definitions
        </label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          disabled={busy || !start || !end || overview.offline}
          onClick={() => void act(() => api.qvPrepare({ symbols: [`${symbol}.v.0`], start, end, schema: "ohlcv-1m", stype_in: "continuous", include_definitions: definitions }))}
          data-testid="qv-acq-quote"
        >
          Get free quote
        </Button>
        {overview.offline && <span className="text-2xs text-fg-faint">Offline mode is on — no request leaves this computer.</span>}
      </div>
      {acq && (
        <div className="mt-4 space-y-2 text-[12px]" data-testid="qv-acq" data-status={acq.status}>
          <p className="flex items-center gap-2">
            <Badge tone={ACQ_TONE[acq.status] ?? "muted"} testId="qv-acq-status">
              {acq.status.replace(/_/g, " ")}
            </Badge>
            {acq.quote && acq.status !== "NOTHING_TO_BUY" && (
              <span className="text-fg" data-testid="qv-acq-cost">
                Quote {usd(acq.quote.cost_usd)} {acq.quote.currency} · {acq.quote.jobs} batch job(s) · {acq.quote.held_days_deducted ?? 0} day(s) already held
              </span>
            )}
          </p>
          {acq.quote?.message && <p className="text-fg-muted">{acq.quote.message}</p>}
          {acq.quote?.caveat && acq.status === "QUOTED" && <p className="text-2xs leading-relaxed text-fg-faint">{acq.quote.caveat}</p>}
          {acq.quote?.fixture_note && <p className="text-2xs text-ql-warning">{acq.quote.fixture_note}</p>}
          {acq.status === "QUOTED" && (
            <div className="space-y-2 rounded-lg border border-ql-border p-3">
              <label className="flex items-center gap-2 text-2xs text-fg-faint">
                Maximum I approve (USD)
                <input className={cx(inputClass, "w-28")} value={maxUsd} onChange={(e) => setMaxUsd(e.target.value)} data-testid="qv-acq-max" />
              </label>
              <label className="flex items-start gap-2 text-[12px] text-fg-muted">
                <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} data-testid="qv-acq-confirm" />
                I approve buying exactly this data from Databento for at most this amount. Expires {acq.expires_at?.slice(11, 16)} UTC.
              </label>
              <Button
                variant="warning"
                disabled={!confirm || busy || !(Number(maxUsd) >= (acq.quote?.cost_usd ?? 0))}
                onClick={() => void act(() => api.qvApprove(acq.id, acq.request_hash, Number(maxUsd)))}
                data-testid="qv-acq-approve"
              >
                Approve
              </Button>
            </div>
          )}
          {acq.status === "APPROVED" && (
            <Button variant="warning" disabled={busy} onClick={() => void act(() => api.qvStart(acq.id))} data-testid="qv-acq-execute">
              Start the purchase (up to {usd(acq.max_usd ?? 0)})
            </Button>
          )}
          {acq.job.jobs?.map((j) => (
            <p key={`${j.symbol}-${j.schema}-${j.start}`} className="text-2xs text-fg-faint">
              {j.symbol} {j.schema} {j.start} → {j.end}: {j.state.toLowerCase()}
              {j.provider_job ? ` · job ${j.provider_job}` : ""} · {j.files.length} file(s){j.files.length ? ` (${j.files.every((f) => f.verified) ? "SHA-256 verified" : "unverified"})` : ""}
            </p>
          ))}
          {acq.error && <p className="text-ql-danger">{acq.error}</p>}
          {acq.status === "ATTENTION" && (
            <div className="flex gap-2">
              <Button onClick={() => void act(() => api.qvResume(acq.id))}>Check again</Button>
              <Button onClick={() => void act(() => api.qvAbandon(acq.id))}>Close</Button>
            </div>
          )}
        </div>
      )}
      {error && (
        <p className="mt-3 text-[12px] text-ql-danger" data-testid="qv-acq-error">
          {error}
        </p>
      )}
    </Card>
  );
}

function Backup({ overview, run }: { overview: QvOverview; run: (a: () => Promise<unknown>, done?: (r: unknown) => string) => Promise<void> }) {
  const [location, setLocation] = useState("");
  const [confirm, setConfirm] = useState(false);
  const latest = overview.backups[0];
  return (
    <div className="mt-4 border-t border-ql-border pt-3">
      <p className="label mb-2">Backup / restore</p>
      <input className={inputClass} placeholder="/Volumes/External/Backups" value={location} onChange={(e) => setLocation(e.target.value)} data-testid="qv-backup-location" />
      <label className="mt-2 flex items-start gap-2 text-2xs text-fg-faint">
        <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
        Copy my market data there (private; licensed data stays under its provider's terms).
      </label>
      <div className="mt-2 flex gap-2">
        <Button
          disabled={!location.trim() || !confirm}
          onClick={() => void run(() => api.qvBackup(location.trim()), (r) => {
            const b = r as { files: number; bytes: number; location: string };
            return `Backup verified: ${b.files} files, ${bytes(b.bytes)} → ${b.location}`;
          })}
          data-testid="qv-backup"
        >
          Back up
        </Button>
        <Button
          disabled={!location.trim() || !confirm}
          onClick={() => void run(() => api.qvRestore(location.trim()), (r) => {
            const x = r as { restored: number; intact: number };
            return `Restore: ${x.restored} file(s) restored, ${x.intact} already intact.`;
          })}
          title="Point to a JARVIS-QuantLab-Backup/<timestamp> folder"
        >
          Restore from here
        </Button>
      </div>
      {latest && (
        <p className="mt-2 text-2xs text-fg-faint">
          Last backup {latest.created_at.slice(0, 16).replace("T", " ")} · {latest.files} files · {latest.status.toLowerCase()}
        </p>
      )}
    </div>
  );
}

function Alternatives({ overview }: { overview: QvOverview }) {
  const a = overview.alternatives;
  return (
    <Card title="Alternative provider evaluation">
      <table className="w-full text-[12px]">
        <thead className="text-2xs text-fg-faint">
          <tr>
            {["Provider", "Status", "Contract identity", "Granularity", "Cost", "License"].map((h) => (
              <th key={h} className="py-1 text-left font-normal">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="text-fg-muted">
          {a.providers.map((p) => (
            <tr key={p.name} className="border-t border-ql-border align-top">
              <td className="py-1.5 text-fg">{p.name}</td>
              <td className="py-1.5">{p.status}</td>
              <td className="py-1.5">{p.identity}</td>
              <td className="py-1.5">{p.granularity}</td>
              <td className="py-1.5">{p.cost}</td>
              <td className="py-1.5">{p.license}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-2xs text-fg-faint">
        Criteria: {a.criteria.join(" · ")}. {a.never}
      </p>
    </Card>
  );
}
