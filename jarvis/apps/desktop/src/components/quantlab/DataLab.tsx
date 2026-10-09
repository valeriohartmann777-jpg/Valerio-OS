import type { QlBarInterval, QlDataset, QlFinding } from "@jarvis/protocol";
import { type ReactNode, useState } from "react";

import { ApiError, api } from "../../lib/api";
import { Button, cx } from "../ui/primitives";
import { Card, DataStatus, Field, Hash, SyntheticBadge, inputClass, utc } from "./ui";

/**
 * Data Lab: import CSV/Parquet, see every quality finding, and the Data Passport.
 * Fail closed — REJECTED and UNSUPPORTED datasets can be inspected, never run.
 */

const FIXTURES = [
  { name: "ma_crossover_case.csv", label: "SMA crossover (12 bars)" },
  { name: "golden_execution_case.csv", label: "Golden execution (8 bars)" },
  { name: "invalid_ohlc_duplicate.csv", label: "Broken: bad OHLC + duplicate" },
];

const TIMEZONES = ["", "Etc/UTC", "America/New_York", "America/Chicago", "Europe/London", "Europe/Berlin", "Europe/Zurich", "Asia/Tokyo"];

async function base64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

export function DataLab({
  datasets,
  fixturesAvailable,
  selected,
  onSelect,
  onImported,
}: {
  datasets: QlDataset[];
  fixturesAvailable: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
  onImported: (dataset: QlDataset) => void;
}) {
  const current = datasets.find((d) => d.id === selected) ?? datasets[0] ?? null;
  return (
    <div className="grid gap-5 lg:grid-cols-[360px_minmax(0,1fr)]" data-testid="ql-datalab">
      <div className="space-y-5">
        <ImportCard onImported={onImported} />
        <Card title="Synthetic fixtures · engineering only">
          <p className="mb-3 text-[13px] text-fg-muted">
            Fabricated bars from the QuantLab handoff. They test the arithmetic and the importer — never a market.
          </p>
          <div className="flex flex-col gap-2">
            {FIXTURES.map((f) => (
              <FixtureButton key={f.name} name={f.name} label={f.label} disabled={!fixturesAvailable} onImported={onImported} />
            ))}
          </div>
        </Card>
        <Card title={`Datasets · ${datasets.length}`}>
          {datasets.length === 0 ? (
            <p className="text-[13px] text-fg-faint">Nothing imported yet.</p>
          ) : (
            <ul className="-mx-2 space-y-0.5">
              {datasets.map((d) => (
                <li key={d.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(d.id)}
                    className={cx(
                      "w-full rounded-lg px-2 py-2 text-left transition-colors hover:bg-white/[0.03]",
                      current?.id === d.id && "bg-white/[0.04]",
                    )}
                    data-testid="ql-dataset-row"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-[13px] text-fg">
                        {d.symbol} <span className="text-fg-faint">· {d.frequency ?? "?"}</span>
                      </span>
                      <DataStatus status={d.status} />
                    </div>
                    <div className="mt-0.5 flex items-center justify-between gap-2 text-2xs text-fg-faint">
                      <span className="truncate">{d.filename}</span>
                      <span>{d.rows.toLocaleString("en-US")} bars</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      {current ? <Passport dataset={current} /> : <Pipeline status={null} />}
    </div>
  );
}

function FixtureButton({
  name,
  label,
  disabled,
  onImported,
}: {
  name: string;
  label: string;
  disabled: boolean;
  onImported: (d: QlDataset) => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      className="justify-between"
      disabled={disabled || busy}
      data-testid={`ql-fixture-${name}`}
      onClick={async () => {
        setBusy(true);
        try {
          onImported(await api.qlFixture(name));
        } finally {
          setBusy(false);
        }
      }}
    >
      <span>{label}</span>
      <span className="font-mono text-2xs text-ql-warning">SYNTHETIC</span>
    </Button>
  );
}

function ImportCard({ onImported }: { onImported: (d: QlDataset) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [symbol, setSymbol] = useState("");
  const [exchange, setExchange] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [timezone, setTimezone] = useState("");
  const [frequency, setFrequency] = useState<"" | QlBarInterval>("");
  const [provider, setProvider] = useState("user_supplied");
  const [license, setLicense] = useState("");
  const [adjustment, setAdjustment] = useState<"unknown" | "adjusted" | "unadjusted">("unknown");
  const [columns, setColumns] = useState({ time: "", open: "", high: "", low: "", close: "", volume: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const dataset = await api.qlImport({
        filename: file.name,
        content_base64: await base64(file),
        symbol: symbol.trim(),
        exchange: exchange.trim(),
        currency: currency.trim().toUpperCase(),
        timezone: timezone || null,
        frequency: frequency || null,
        provider: provider.trim(),
        license: license.trim() || "unverified (user's responsibility)",
        adjustment,
        columns,
      });
      onImported(dataset);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Import failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Import historical bars">
      <div className="space-y-3">
        <label
          className="flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-ql-border bg-ql-raised/50 px-3 py-4 text-center text-[13px] text-fg-muted hover:border-ql/50"
        >
          <input
            type="file"
            accept=".csv,.txt,.parquet"
            className="sr-only"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            data-testid="ql-file"
          />
          {file ? (
            <span className="text-fg">
              {file.name} <span className="text-fg-faint">· {(file.size / 1024).toFixed(0)} KB</span>
            </span>
          ) : (
            <span>Choose a CSV or Parquet file (OHLCV, one row per bar)</span>
          )}
        </label>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Symbol">
            <input className={inputClass} value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="SPY" />
          </Field>
          <Field label="Exchange">
            <input className={inputClass} value={exchange} onChange={(e) => setExchange(e.target.value)} placeholder="NYSE Arca" />
          </Field>
          <Field label="Currency">
            <input className={inputClass} value={currency} maxLength={3} onChange={(e) => setCurrency(e.target.value)} />
          </Field>
          <Field label="Bar interval">
            <select className={inputClass} value={frequency} onChange={(e) => setFrequency(e.target.value as "" | QlBarInterval)}>
              <option value="">detect from data</option>
              <option value="1d">1 day</option>
              <option value="1h">1 hour</option>
              <option value="5m">5 minutes</option>
              <option value="1m">1 minute</option>
            </select>
          </Field>
          <Field label="Timestamps without offset are in" hint="Only used for times without a UTC offset. Dates alone (daily bars) need none.">
            <select className={inputClass} value={timezone} onChange={(e) => setTimezone(e.target.value)}>
              {TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>
                  {tz || "— (data has offsets)"}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Splits & dividends">
            <select className={inputClass} value={adjustment} onChange={(e) => setAdjustment(e.target.value as typeof adjustment)}>
              <option value="unknown">unknown</option>
              <option value="adjusted">adjusted by provider</option>
              <option value="unadjusted">unadjusted</option>
            </select>
          </Field>
          <Field label="Provider">
            <input className={inputClass} value={provider} onChange={(e) => setProvider(e.target.value)} />
          </Field>
          <Field label="Licence">
            <input className={inputClass} value={license} onChange={(e) => setLicense(e.target.value)} placeholder="unverified" />
          </Field>
        </div>
        <details className="text-[13px] text-fg-muted">
          <summary className="cursor-pointer text-xs">Column mapping (only if detection fails)</summary>
          <div className="mt-2 grid grid-cols-3 gap-2">
            {(Object.keys(columns) as (keyof typeof columns)[]).map((role) => (
              <Field key={role} label={role}>
                <input
                  className={inputClass}
                  value={columns[role]}
                  onChange={(e) => setColumns((c) => ({ ...c, [role]: e.target.value }))}
                  placeholder="auto"
                />
              </Field>
            ))}
          </div>
        </details>
        {error && <p className="text-[13px] text-ql-danger">{error}</p>}
        <Button variant="primary" disabled={!file || !symbol.trim() || !exchange.trim() || busy} onClick={submit}>
          {busy ? "Checking…" : "Import & check"}
        </Button>
        <p className="text-2xs text-fg-faint">
          Nothing is repaired silently: duplicates, broken OHLC, bad prices and unknown timezones reject the file.
        </p>
      </div>
    </Card>
  );
}

const STAGES = ["Source", "Normalize", "Audit", "Resolve", "Freeze"] as const;

function Pipeline({ status }: { status: QlDataset["status"] | null }) {
  const reached = status === null ? 0 : status === "ACCEPTED" || status === "WARNING" ? 5 : 3;
  const stoppedAt = status === "REJECTED" || status === "UNSUPPORTED" ? 2 : -1;
  return (
    <ol className="flex items-center gap-2" aria-label="Data pipeline">
      {STAGES.map((stage, i) => (
        <li key={stage} className="flex flex-1 items-center gap-2">
          <span
            className={cx(
              "flex-1 rounded-lg border px-2 py-1.5 text-center font-mono text-[10px] tracking-[0.14em] uppercase",
              i === stoppedAt
                ? "border-ql-danger/50 text-ql-danger"
                : i < reached
                  ? "border-ql/40 text-ql"
                  : "border-ql-border text-fg-faint",
            )}
          >
            {i === stoppedAt ? "✕ " : i < reached ? "✓ " : ""}
            {stage}
          </span>
          {i < STAGES.length - 1 && <span className="text-fg-faint">→</span>}
        </li>
      ))}
    </ol>
  );
}

const SEVERITY = {
  block: { icon: "✕", cls: "text-ql-danger", label: "Blocks" },
  warn: { icon: "!", cls: "text-ql-warning", label: "Warning" },
  info: { icon: "i", cls: "text-fg-muted", label: "Note" },
};

function FindingRow({ finding }: { finding: QlFinding }) {
  const s = SEVERITY[finding.severity];
  return (
    <li className="py-2">
      <div className="flex items-start gap-2 text-[13px]">
        <span className={cx("w-4 shrink-0 font-mono", s.cls)} aria-label={s.label}>
          {s.icon}
        </span>
        <div className="min-w-0">
          <p className="text-fg">
            <span className="mr-2 font-mono text-2xs text-fg-muted">{finding.code}</span>
            {finding.message}
            {finding.count > 0 && <span className="text-fg-faint"> · {finding.count.toLocaleString("en-US")}</span>}
          </p>
          {finding.examples.length > 0 && (
            <p className="selectable mt-0.5 truncate font-mono text-2xs text-fg-faint">{finding.examples.join("  ·  ")}</p>
          )}
        </div>
      </div>
    </li>
  );
}

function Passport({ dataset }: { dataset: QlDataset }) {
  const p = dataset.passport;
  const row = (label: string, value: ReactNode) => (
    <div className="grid grid-cols-[150px_minmax(0,1fr)] gap-3 py-1 text-[13px]">
      <dt className="text-fg-faint">{label}</dt>
      <dd className="truncate text-fg">{value}</dd>
    </div>
  );
  return (
    <div className="space-y-5" data-testid="ql-passport">
      <Card>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <h2 className="mr-auto text-lg font-medium text-fg">
            Data Passport · {p.instrument.symbol}
          </h2>
          {dataset.synthetic && <SyntheticBadge />}
          <DataStatus status={dataset.status} />
        </div>
        <Pipeline status={dataset.status} />
        {!dataset.usable && (
          <p className="mt-4 rounded-xl border border-ql-danger/40 bg-ql-danger/5 px-3 py-2 text-[13px] text-ql-danger" data-testid="ql-data-blocked">
            Fail closed: this dataset can't be used for a run. Fix the blocking findings below and import again.
          </p>
        )}
        <dl className="mt-4 grid gap-x-8 md:grid-cols-2">
          <div>
            {row("Provider", p.provider)}
            {row("Licence", p.license)}
            {row("File", p.original_filename)}
            {row("Instrument", `${p.instrument.symbol} · ${p.venue} · ${p.currency}`)}
            {row("Asset class", p.asset_class)}
            {row("Frequency", `${p.frequency ?? "—"}${p.frequency_source === "inferred" ? " (inferred)" : ""}`)}
            {row("Timestamps", `${p.timezone_original} → ${p.normalized_timezone}, ${p.timestamp_semantics}`)}
            {row("Corporate actions", p.corporate_actions_or_roll_policy)}
          </div>
          <div>
            {row("Coverage", `${utc(p.coverage_start_utc)} → ${utc(p.coverage_end_utc)}`)}
            {row("Bars", `${p.row_count.toLocaleString("en-US")} of ${p.rows_in_file.toLocaleString("en-US")} rows`)}
            {row("Gaps", `${p.missing_periods.gaps} (≈${p.missing_periods.estimated_missing_bars} bars)`)}
            {row("Duplicates · sort fixes", `${p.duplicates} · ${p.sort_fixes}`)}
            {row("Invalid OHLC · outliers", `${p.invalid_ohlc} · ${p.outliers}`)}
            {row("Original SHA-256", <Hash value={p.original_sha256} length={16} />)}
            {row("Normalized SHA-256", <Hash value={p.normalized_sha256} length={16} />)}
            {row("Columns", Object.entries(p.column_mapping).map(([k, v]) => `${k}=${v}`).join(", "))}
          </div>
        </dl>
      </Card>

      <Card title={`Quality findings · ${p.quality_findings.length}`}>
        {p.quality_findings.length === 0 ? (
          <p className="text-[13px] text-fg-muted">No findings.</p>
        ) : (
          <ul className="divide-y divide-ql-border/60">
            {p.quality_findings.map((f) => (
              <FindingRow key={f.code} finding={f} />
            ))}
          </ul>
        )}
        {p.normalizations.length > 0 && (
          <p className="mt-3 text-2xs text-fg-faint">Normalisation: {p.normalizations.join(" · ")}</p>
        )}
      </Card>

      <Card title="Limitations">
        <ul className="space-y-1.5 text-[13px] text-fg-muted">
          {p.limitations.map((l) => (
            <li key={l}>— {l}</li>
          ))}
        </ul>
      </Card>

      {dataset.preview.normalized_rows.length > 0 && (
        <Card title="Normalized preview (UTC bar start)">
          <div className="overflow-x-auto">
            <table className="w-full text-left font-mono text-2xs text-fg-muted tabular">
              <thead className="text-fg-faint">
                <tr>
                  {["ts", "open", "high", "low", "close", "volume"].map((h) => (
                    <th key={h} className="px-2 py-1 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {dataset.preview.normalized_rows.map((r) => (
                  <tr key={r.ts} className="border-t border-ql-border/60">
                    <td className="px-2 py-1">{r.ts}</td>
                    <td className="px-2 py-1">{r.open}</td>
                    <td className="px-2 py-1">{r.high}</td>
                    <td className="px-2 py-1">{r.low}</td>
                    <td className="px-2 py-1">{r.close}</td>
                    <td className="px-2 py-1">{r.volume ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
