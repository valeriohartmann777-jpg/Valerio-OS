import type {
  QhAudit,
  QhCacheRow,
  QhCatalog,
  QhDataset,
  QhJob,
  QhQuote,
  QhResolution,
  QhStatus,
} from "@jarvis/protocol";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../../../lib/api";
import { Button, cx } from "../../ui/primitives";
import { DataLab } from "../DataLab";
import { LineChart } from "../research/charts";
import { Empty, FixtureBadge, Kv, Progress, bytes, fmt, usd, type Mode } from "../research/common";
import { Badge, Card, Field, compactInputClass, inputClass } from "../ui";

/**
 * Data Hub: Databento connection, catalog, cost-first acquisition, local cache,
 * verified datasets. No download happens without a quote the user approved here.
 */

const err = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export function DataHub({ mode, tick, onDatasets }: { mode: Mode; tick: string | null; onDatasets?: () => void }) {
  const [status, setStatus] = useState<QhStatus | null>(null);
  const [jobs, setJobs] = useState<QhJob[]>([]);
  const [cache, setCache] = useState<QhCacheRow[]>([]);
  const [datasets, setDatasets] = useState<QhDataset[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<"provider" | "files">("provider");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, j, c, d] = await Promise.all([api.qhStatus(), api.qhJobs(), api.qhCache(), api.qhDatasets()]);
      setStatus(s);
      setJobs(j);
      setCache(c);
      setDatasets(d);
      setError(null);
    } catch (e) {
      setError(err(e, "The Data Hub isn't reachable."));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, tick]);

  // Poll while a download runs (events also trigger refreshes).
  const busy = jobs.some((j) => j.status === "QUEUED" || j.status === "RUNNING");
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => void refresh(), 1500);
    return () => clearInterval(timer);
  }, [busy, refresh]);

  return (
    <div className="space-y-5" data-testid="qh-page">
      <div className="flex gap-1 rounded-xl border border-ql-border bg-ql-surface p-1 text-[13px]" role="tablist">
        {(
          [
            ["provider", "Market data (Databento)"],
            ["files", "File import (CSV / Parquet)"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={cx("flex-1 rounded-lg px-3 py-1.5", tab === key ? "bg-ql-raised text-fg" : "text-fg-faint hover:text-fg-muted")}
            data-testid={`qh-tab-${key}`}
          >
            {label}
          </button>
        ))}
      </div>
      {error && <p className="text-[13px] text-ql-danger">{error}</p>}
      {tab === "files" ? (
        <FilesTab />
      ) : (
        <>
          {status?.fixture && (
            <p className="rounded-xl border border-ql-warning/50 bg-ql-warning/5 px-4 py-2 font-mono text-2xs tracking-[0.06em] text-ql-warning" data-testid="qh-fixture-banner">
              {status.fixture_label} — every dataset from it is labelled FIXTURE and gives no market evidence.
            </p>
          )}
          <div className="grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
            <Connection status={status} onChanged={refresh} />
            <Spend status={status} onChanged={refresh} />
          </div>
          {status?.status === "CONNECTED" && <Acquire status={status} onChanged={refresh} />}
          <Jobs jobs={jobs} onChanged={refresh} />
          <Cache cache={cache} onBuilt={(d) => {
            setSelected(d.id);
            void refresh();
            onDatasets?.();
          }} />
          <Datasets datasets={datasets} selected={selected} onSelect={setSelected} mode={mode} />
          {mode === "Institutional" && <Audit tick={tick} />}
        </>
      )}
    </div>
  );
}

function FilesTab() {
  const [datasets, setDatasets] = useState<Awaited<ReturnType<typeof api.qlDatasets>>>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [fixtures, setFixtures] = useState(false);
  const load = useCallback(async () => {
    const [d, o] = await Promise.all([api.qlDatasets(), api.qlOverview()]);
    setDatasets(d);
    setFixtures(o.fixtures_available);
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  return (
    <div className="space-y-3">
      <p className="text-[13px] text-fg-muted">
        Imported files feed the cash-equity reference lab (R1). Futures research uses verified datasets from the
        provider tab.
      </p>
      <DataLab
        datasets={datasets}
        fixturesAvailable={fixtures}
        selected={selected}
        onSelect={setSelected}
        onImported={(d) => {
          setSelected(d.id);
          void load();
        }}
      />
    </div>
  );
}

// Connection ---------------------------------------------------------------------------------

function Connection({ status, onChanged }: { status: QhStatus | null; onChanged: () => void }) {
  const [key, setKey] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string; remedy?: string | null } | null>(null);
  const [replacing, setReplacing] = useState(false);
  const connected = status?.status === "CONNECTED";

  const run = async (what: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(what);
    setMessage(null);
    try {
      await fn();
      setMessage({ tone: "ok", text: ok });
      setKey("");
      setReplacing(false);
      onChanged();
    } catch (e) {
      setMessage({ tone: "error", text: err(e, "That didn't work."), remedy: e instanceof ApiError ? e.suggestion : null });
    } finally {
      setBusy(null);
    }
  };

  const keystore = status?.keystore;
  return (
    <Card
      title="Databento connection"
      aside={
        <div className="flex items-center gap-2">
          {status?.fixture && <FixtureBadge label="Fixture provider" />}
          <Badge
            tone={connected ? "positive" : status?.status === "REJECTED" ? "danger" : "muted"}
            icon={connected ? "●" : "○"}
            testId="qh-connection-status"
          >
            {connected ? "Connected" : status?.status === "REJECTED" ? "Key rejected" : "Not connected"}
          </Badge>
        </div>
      }
      testId="qh-connection"
    >
      {connected && !replacing ? (
        <div>
          <dl>
            <Kv label="Key">stored in {keystore?.backend} · ending {status?.key_hint}</Kv>
            <Kv label="Last verified">{status?.verified_at?.replace("T", " ").slice(0, 19)} UTC</Kv>
            <Kv label="Visible datasets">{status?.datasets.length ?? 0}</Kv>
            <Kv label="SDK">databento {status?.sdk_version ?? "?"}</Kv>
          </dl>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button onClick={() => run("test", () => api.qhTest(), "Verified with an authenticated metadata request.")} disabled={!!busy} data-testid="qh-test">
              {busy === "test" ? "Checking…" : "Test connection"}
            </Button>
            <Button onClick={() => setReplacing(true)}>Replace key</Button>
            <Button
              variant="danger"
              onClick={() => run("disconnect", () => api.qhDisconnect(), "Key removed. Cached data stays on this computer.")}
              disabled={!!busy}
              data-testid="qh-disconnect"
            >
              Disconnect
            </Button>
          </div>
        </div>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (key.trim()) void run("connect", () => api.qhConnect(key), "Connected — verified with Databento metadata, key stored in the keystore.");
          }}
        >
          <p className="text-[13px] text-fg-muted">
            Paste your Databento API key. JARVIS checks it with a free metadata request, then keeps it in{" "}
            {keystore?.available ? keystore.backend : "the operating system's keystore"} — never in files, logs or chats.
          </p>
          {keystore && !keystore.available && (
            <p className="mt-2 text-[13px] text-ql-danger" data-testid="qh-no-keystore">{keystore.reason}</p>
          )}
          {keystore?.test_only && <p className="mt-2 text-2xs text-ql-warning">Test keystore (memory only) — for automated tests.</p>}
          <div className="mt-3 flex gap-2">
            <input
              className={inputClass}
              type={show ? "text" : "password"}
              autoComplete="off"
              spellCheck={false}
              placeholder="db-…"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              aria-label="Databento API key"
              data-testid="qh-key"
            />
            <Button onClick={() => setShow((s) => !s)} aria-pressed={show} title={show ? "Hide key" : "Show key"}>
              {show ? "Hide" : "Show"}
            </Button>
            <Button variant="primary" type="submit" disabled={!key.trim() || !!busy || keystore?.available === false} data-testid="qh-connect">
              {busy === "connect" ? "Verifying…" : "Connect & Verify"}
            </Button>
          </div>
          <p className="mt-2 text-2xs text-fg-faint">
            Tip: use a key dedicated to JARVIS and set usage limits in the Databento portal. A valid key doesn't mean every
            dataset is licensed — the catalog shows what this account can use.
          </p>
          {replacing && (
            <button type="button" className="mt-2 text-2xs text-fg-muted underline" onClick={() => setReplacing(false)}>
              Keep the current key
            </button>
          )}
        </form>
      )}
      {message && (
        <div className={cx("mt-3 text-[13px]", message.tone === "ok" ? "text-ql-positive" : "text-ql-danger")} role="status" data-testid="qh-message">
          {message.text}
          {message.remedy && <p className="mt-1 text-fg-muted">{message.remedy}</p>}
        </div>
      )}
      {status?.error && !message && <p className="mt-3 text-[13px] text-ql-danger">{status.error}</p>}
    </Card>
  );
}

function Spend({ status, onChanged }: { status: QhStatus | null; onChanged: () => void }) {
  const [edit, setEdit] = useState(false);
  const [perRequest, setPerRequest] = useState("");
  const [perMonth, setPerMonth] = useState("");
  const [error, setError] = useState<string | null>(null);
  const caps = status?.caps;
  const month = status?.approved_this_month_usd ?? 0;
  return (
    <Card title="Spend control" aside={<Badge tone="accent">Approval required</Badge>}>
      <dl className="grid grid-cols-3 gap-4">
        <div>
          <dt className="text-xs text-ql-muted">Approved this month</dt>
          <dd className="mt-1 text-lg font-medium text-fg tabular" data-testid="qh-month">{usd(month)}</dd>
          <dd className="text-2xs text-fg-faint">estimates you approved, not a bill</dd>
        </div>
        <div>
          <dt className="text-xs text-ql-muted">Per request cap</dt>
          <dd className="mt-1 text-lg font-medium text-fg tabular">{usd(caps?.per_request_usd)}</dd>
        </div>
        <div>
          <dt className="text-xs text-ql-muted">Monthly cap</dt>
          <dd className="mt-1 text-lg font-medium text-fg tabular">{usd(caps?.per_month_usd)}</dd>
        </div>
      </dl>
      {caps && <Progress value={caps.per_month_usd ? month / caps.per_month_usd : 0} label={`${usd(month)} of ${usd(caps.per_month_usd)} monthly cap`} />}
      <p className="mt-3 text-2xs text-fg-faint">
        Every paid download shows an estimate first and needs your explicit approval — also when JARVIS or ULTRON work
        autonomously. Caps never rise on their own.
      </p>
      {edit ? (
        <form
          className="mt-3 flex flex-wrap items-end gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await api.qhSetCaps({ per_request_usd: Number(perRequest), per_month_usd: Number(perMonth) });
              setEdit(false);
              setError(null);
              onChanged();
            } catch (x) {
              setError(err(x, "Couldn't save the caps."));
            }
          }}
        >
          <Field label="Per request ($)">
            <input className={compactInputClass + " w-28"} inputMode="decimal" value={perRequest} onChange={(e) => setPerRequest(e.target.value)} />
          </Field>
          <Field label="Per month ($)">
            <input className={compactInputClass + " w-28"} inputMode="decimal" value={perMonth} onChange={(e) => setPerMonth(e.target.value)} />
          </Field>
          <Button type="submit" variant="primary">Save caps</Button>
          <Button onClick={() => setEdit(false)}>Cancel</Button>
          {error && <p className="w-full text-[13px] text-ql-danger">{error}</p>}
        </form>
      ) : (
        <Button
          className="mt-3"
          onClick={() => {
            setPerRequest(String(caps?.per_request_usd ?? 25));
            setPerMonth(String(caps?.per_month_usd ?? 100));
            setEdit(true);
          }}
        >
          Change caps
        </Button>
      )}
    </Card>
  );
}

// Catalog → quote → approval -------------------------------------------------------------------

const ROLL = [
  ["v", "volume (v) — most traded, by previous day's volume"],
  ["c", "calendar (c) — nearest expiry"],
  ["n", "open interest (n)"],
] as const;

function Acquire({ status, onChanged }: { status: QhStatus; onChanged: () => void }) {
  const [dataset, setDataset] = useState(status.datasets.includes("GLBX.MDP3") ? "GLBX.MDP3" : (status.datasets[0] ?? ""));
  const [catalog, setCatalog] = useState<QhCatalog | null>(null);
  const [product, setProduct] = useState("NQ");
  const [roll, setRoll] = useState<string>("v");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [definitions, setDefinitions] = useState(true);
  const [quote, setQuote] = useState<QhQuote | null>(null);
  const [resolution, setResolution] = useState<QhResolution | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ text: string; remedy?: string | null } | null>(null);
  const symbol = `${product}.${roll}.0`;

  useEffect(() => {
    if (!dataset) return;
    let live = true;
    api
      .qhCatalog(dataset)
      .then((c) => {
        if (!live) return;
        setCatalog(c);
        const info = c.info;
        if (info) {
          const last = new Date(`${info.available_end}T00:00:00Z`);
          const first = new Date(last.getTime() - 92 * 86_400_000);
          setEnd((e) => e || info.available_end);
          setStart((s) => s || first.toISOString().slice(0, 10));
        }
      })
      .catch((e) => live && setError({ text: err(e, "Couldn't load the catalog."), remedy: e instanceof ApiError ? e.suggestion : null }));
    return () => {
      live = false;
    };
  }, [dataset]);

  const info = catalog?.info;
  const act = async (what: string, fn: () => Promise<void>) => {
    setBusy(what);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError({ text: err(e, "That didn't work."), remedy: e instanceof ApiError ? e.suggestion : null });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="Get market data" aside={info && <span className="font-mono text-2xs text-fg-faint">{info.dataset} · {info.available_start} → {info.available_end} (UTC, exclusive)</span>} testId="qh-acquire">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Dataset">
              <select className={inputClass} value={dataset} onChange={(e) => setDataset(e.target.value)} data-testid="qh-dataset">
                {status.datasets.map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            </Field>
            <Field label="Schema" hint="1-minute OHLCV bars; ts_event is the bar's START.">
              <select className={inputClass} value="ohlcv-1m" disabled aria-label="Schema">
                <option>ohlcv-1m</option>
              </select>
            </Field>
          </div>
          <div>
            <p className="mb-1 text-xs text-ql-muted">Instrument</p>
            <div className="flex flex-wrap gap-1.5">
              {(catalog?.products ?? []).map((p) => (
                <button
                  key={p.root}
                  type="button"
                  disabled={!p.engine}
                  title={p.engine ? p.name : `${p.name} — in the catalog, not supported by the engine yet`}
                  onClick={() => setProduct(p.root)}
                  className={cx(
                    "rounded-lg border px-2.5 py-1 font-mono text-2xs",
                    product === p.root ? "border-ql/60 bg-ql/10 text-fg" : "border-ql-border text-fg-muted",
                    !p.engine && "opacity-40",
                  )}
                  data-testid={`qh-product-${p.root}`}
                >
                  {p.root}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Continuous contract" hint="Unadjusted prices; the contract behind the symbol changes at rolls.">
              <select className={inputClass} value={roll} onChange={(e) => setRoll(e.target.value)}>
                {ROLL.map(([k, label]) => (
                  <option key={k} value={k}>{label}</option>
                ))}
              </select>
            </Field>
            <Field label="Symbol">
              <input className={inputClass} value={symbol} readOnly aria-label="Symbol" data-testid="qh-symbol" />
            </Field>
            <Field label="From (UTC day)">
              <input className={inputClass} type="date" value={start} min={info?.available_start} max={info?.available_end} onChange={(e) => setStart(e.target.value)} data-testid="qh-start" />
            </Field>
            <Field label="Until (exclusive)">
              <input className={inputClass} type="date" value={end} min={info?.available_start} max={info?.available_end} onChange={(e) => setEnd(e.target.value)} data-testid="qh-end" />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-[13px] text-fg-muted">
            <input type="checkbox" checked={definitions} onChange={(e) => setDefinitions(e.target.checked)} />
            Include contract definitions (tick size, multiplier, expiry — point-in-time)
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => act("resolve", async () => setResolution(await api.qhResolve(dataset, [symbol], "continuous", start, end)))}
              disabled={!start || !end || !!busy}
              data-testid="qh-resolve"
            >
              {busy === "resolve" ? "Resolving…" : "Show contracts"}
            </Button>
            <Button
              variant="primary"
              onClick={() =>
                act("quote", async () =>
                  setQuote(
                    await api.qhQuote({ dataset, schema: "ohlcv-1m", stype_in: "continuous", symbols: [symbol], start, end, include_definitions: definitions }),
                  ),
                )
              }
              disabled={!start || !end || !!busy}
              data-testid="qh-get-quote"
            >
              {busy === "quote" ? "Asking Databento…" : "Get cost estimate"}
            </Button>
          </div>
          {error && (
            <div className="text-[13px] text-ql-danger" role="alert">
              {error.text}
              {error.remedy && <p className="mt-0.5 text-fg-muted">{error.remedy}</p>}
            </div>
          )}
          {info && Object.keys(info.recent_conditions).length > 0 && (
            <p className="text-2xs text-fg-faint">
              Last 30 days condition:{" "}
              {Object.entries(info.recent_conditions).map(([k, v]) => `${v} ${k}`).join(" · ")}
            </p>
          )}
          {resolution && <Resolution resolution={resolution} />}
        </div>
        <div>{quote ? <QuoteCard quote={quote} onDecided={(q) => {
          setQuote(q);
          onChanged();
        }} /> : <Empty title="No estimate yet">Choose an instrument and dates, then get a cost estimate. Nothing is bought until you approve it.</Empty>}</div>
      </div>
    </Card>
  );
}

function Resolution({ resolution }: { resolution: QhResolution }) {
  const rows = Object.entries(resolution.mappings).flatMap(([sym, list]) => list.map((m) => ({ sym, ...m })));
  return (
    <div className="rounded-xl border border-ql-border bg-ql-raised p-3" data-testid="qh-resolution">
      <p className="label mb-2">Contracts behind the symbol (point-in-time)</p>
      <table className="w-full text-left text-2xs">
        <tbody className="font-mono text-fg-muted">
          {rows.map((r) => (
            <tr key={`${r.sym}-${r.start}`} className="border-t border-ql-border/60">
              <td className="py-0.5">{r.sym}</td>
              <td>{r.start} → {r.end}</td>
              <td className="text-right">instrument {r.id}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {resolution.not_found.length > 0 && <p className="mt-2 text-2xs text-ql-danger">Not found: {resolution.not_found.join(", ")}</p>}
    </div>
  );
}

function QuoteCard({ quote, onDecided }: { quote: QhQuote; onDecided: (q: QhQuote) => void }) {
  const suggested = Math.ceil(quote.cost_usd * 1.1 * 100) / 100;
  const [budget, setBudget] = useState(String(suggested));
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; remedy?: string | null } | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const left = Math.max(0, Math.round((Date.parse(quote.expires_at) - now) / 1000));
  const open = quote.status === "OPEN" && left > 0;
  const free = quote.cost_usd === 0;
  return (
    <div className="rounded-xl border border-ql-border bg-ql-raised p-4" data-testid="qh-quote" data-status={quote.status}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="label">Estimate {quote.id}</p>
          <p className="mt-1 text-3xl font-light text-fg tabular" data-testid="qh-quote-cost">{usd(quote.cost_usd)}</p>
          <p className="text-2xs text-fg-faint">
            {fmt(quote.records, 0)} records · {bytes(quote.billable_bytes)} billable · {quote.cached_days} cached day(s) free
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          {quote.fixture && <FixtureBadge label="Fixture price" />}
          <Badge tone={open ? "accent" : "muted"}>{open ? `Valid ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : quote.status}</Badge>
        </div>
      </div>
      <table className="mt-3 w-full text-left text-2xs">
        <thead className="text-fg-faint">
          <tr>
            <th className="py-1 font-medium">Item</th>
            <th className="font-medium">Missing days</th>
            <th className="text-right font-medium">Estimate</th>
          </tr>
        </thead>
        <tbody className="font-mono text-fg-muted">
          {quote.items.map((item) => (
            <tr key={`${item.schema}-${item.symbol}`} className="border-t border-ql-border/60">
              <td className="py-1">{item.symbol} · {item.schema}</td>
              <td>{item.ranges.map((r) => `${r.start}→${r.end}`).join(", ") || "all cached"}</td>
              <td className="text-right">{usd(item.ranges.reduce((s, r) => s + r.cost_usd, 0), false, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Object.keys(quote.conditions).length > 0 && (
        <p className="mt-2 text-2xs text-ql-warning">
          Provider conditions: {Object.entries(quote.conditions).map(([d, c]) => `${d} ${c}`).join(", ")}
        </p>
      )}
      {quote.warnings.length > 0 && <p className="mt-1 text-2xs text-ql-warning">{quote.warnings.join(" · ")}</p>}
      <p className="mt-2 text-2xs text-fg-faint">{quote.caveat}</p>
      {quote.within_caps === false && (
        <p className="mt-2 text-[13px] text-ql-danger">Above your spend caps — narrow the request or raise the caps first.</p>
      )}
      {open && (
        <div className="mt-4 space-y-2 border-t border-ql-border pt-3">
          {!free && (
            <>
              <Field label="Maximum you approve ($)" hint="The download is refused if Databento's price rises above this.">
                <input className={compactInputClass + " w-32"} inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} data-testid="qh-budget" />
              </Field>
              <label className="flex items-start gap-2 text-[13px] text-fg">
                <input type="checkbox" className="mt-1" checked={agree} onChange={(e) => setAgree(e.target.checked)} data-testid="qh-agree" />
                <span>
                  I approve a charge of up to <strong>{usd(Number(budget) || 0)}</strong> on my Databento account for exactly this
                  request. Cancelling later can't undo charges for data already delivered.
                </span>
              </label>
            </>
          )}
          <div className="flex gap-2">
            <Button
              variant="primary"
              disabled={busy || (!free && (!agree || !(Number(budget) >= quote.cost_usd)))}
              onClick={async () => {
                setBusy(true);
                setError(null);
                try {
                  await api.qhApprove(quote.id, free ? 0 : Number(budget));
                  onDecided(await api.qhQuotes().then((qs) => qs.find((q) => q.id === quote.id) ?? quote));
                } catch (e) {
                  setError({ text: err(e, "Not approved."), remedy: e instanceof ApiError ? e.suggestion : null });
                } finally {
                  setBusy(false);
                }
              }}
              data-testid="qh-approve"
            >
              {free ? "Use cached data" : `Approve & download`}
            </Button>
            <Button
              onClick={async () => {
                try {
                  onDecided(await api.qhRejectQuote(quote.id));
                } catch (e) {
                  setError({ text: err(e, "Couldn't decline.") });
                }
              }}
            >
              Decline
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p className="mt-2 text-[13px] text-ql-danger" role="alert">
          {error.text}
          {error.remedy && <span className="block text-fg-muted">{error.remedy}</span>}
        </p>
      )}
    </div>
  );
}

// Jobs, cache, datasets -------------------------------------------------------------------------

function Jobs({ jobs, onChanged }: { jobs: QhJob[]; onChanged: () => void }) {
  if (!jobs.length) return null;
  return (
    <Card title="Downloads">
      <ul className="space-y-3">
        {jobs.slice(0, 6).map((j) => (
          <li key={j.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 text-[13px]" data-testid="qh-job" data-status={j.status}>
            <div>
              <div className="flex items-center gap-2">
                <Badge tone={j.status === "COMPLETED" ? "positive" : j.status === "FAILED" ? "danger" : j.status === "RUNNING" ? "accent" : "muted"}>
                  {j.status}
                </Badge>
                <span className="font-mono text-2xs text-fg-faint">{j.id}</span>
                {j.fixture && <FixtureBadge label="Fixture" />}
                <span className="text-fg-muted">
                  {fmt(j.records, 0)} records · {bytes(j.bytes)} · estimate {usd(j.estimated_cost_usd)}
                </span>
              </div>
              <div className="mt-1.5">
                <Progress value={j.chunks_total ? j.chunks_done / j.chunks_total : 0} label={`${j.chunks_done} / ${j.chunks_total} provider requests`} />
              </div>
              {j.error && <p className="mt-1 text-2xs text-ql-danger">{j.error}</p>}
            </div>
            {(j.status === "QUEUED" || j.status === "RUNNING") && (
              <Button variant="danger" onClick={async () => {
                await api.qhCancelJob(j.id);
                onChanged();
              }}>
                Cancel
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Cache({ cache, onBuilt }: { cache: QhCacheRow[]; onBuilt: (d: QhDataset) => void }) {
  const bars = cache.filter((c) => c.schema === "ohlcv-1m");
  const [key, setKey] = useState<string | null>(null);
  const row = bars.find((c) => c.cache_key === key) ?? bars[0];
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; remedy?: string | null } | null>(null);
  useEffect(() => {
    if (!row) return;
    setStart(row.first_day);
    const last = new Date(`${row.last_day}T00:00:00Z`);
    setEnd(new Date(last.getTime() + 86_400_000).toISOString().slice(0, 10));
  }, [row?.cache_key, row?.first_day, row?.last_day]);
  if (!cache.length) return null;
  return (
    <Card title="Local cache" aside={<span className="text-2xs text-fg-faint">bought once, reused — raw DBN + canonical Parquet</span>} testId="qh-cache">
      <table className="w-full text-left text-[13px]">
        <thead className="text-2xs text-fg-faint">
          <tr>
            <th className="py-1 font-medium">Symbol</th>
            <th className="font-medium">Schema</th>
            <th className="font-medium">Days</th>
            <th className="font-medium">Records</th>
            <th className="font-medium">Coverage (UTC)</th>
            <th className="font-medium">Flagged</th>
          </tr>
        </thead>
        <tbody className="text-fg-muted">
          {cache.map((c) => (
            <tr key={c.cache_key} className="border-t border-ql-border/60">
              <td className="py-1.5 font-mono text-2xs text-fg">{c.symbol} {c.fixture && <FixtureBadge label="Fixture" />}</td>
              <td className="font-mono text-2xs">{c.schema}</td>
              <td className="tabular">{c.days}</td>
              <td className="tabular">{fmt(c.records, 0)}</td>
              <td className="font-mono text-2xs">{c.first_day} → {c.last_day}</td>
              <td className={cx("tabular", c.flagged_days > 0 && "text-ql-warning")}>{c.flagged_days}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {row && (
        <form
          className="mt-4 flex flex-wrap items-end gap-2 border-t border-ql-border pt-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError(null);
            try {
              onBuilt(await api.qhBuild({ dataset: row.dataset, schema: "ohlcv-1m", stype_in: row.stype_in, symbol: row.symbol, start, end }));
            } catch (x) {
              setError({ text: err(x, "Couldn't build the dataset."), remedy: x instanceof ApiError ? x.suggestion : null });
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="Bars">
            <select className={compactInputClass} value={row.cache_key} onChange={(e) => setKey(e.target.value)}>
              {bars.map((b) => (
                <option key={b.cache_key} value={b.cache_key}>{b.symbol}</option>
              ))}
            </select>
          </Field>
          <Field label="From">
            <input type="date" className={compactInputClass} value={start} onChange={(e) => setStart(e.target.value)} />
          </Field>
          <Field label="Until (exclusive)">
            <input type="date" className={compactInputClass} value={end} onChange={(e) => setEnd(e.target.value)} />
          </Field>
          <Button type="submit" variant="primary" disabled={busy} data-testid="qh-build">
            {busy ? "Verifying…" : "Build verified dataset"}
          </Button>
          {error && (
            <p className="w-full text-[13px] text-ql-danger">
              {error.text} {error.remedy && <span className="text-fg-muted">{error.remedy}</span>}
            </p>
          )}
        </form>
      )}
    </Card>
  );
}

function Datasets({ datasets, selected, onSelect, mode }: { datasets: QhDataset[]; selected: string | null; onSelect: (id: string) => void; mode: Mode }) {
  const current = datasets.find((d) => d.id === selected) ?? datasets[0];
  if (!datasets.length) {
    return (
      <Card title="Verified datasets">
        <Empty title="No dataset yet">Download data (or reuse the cache) and build a verified dataset — it freezes the bars with a checksum, a manifest and a quality report.</Empty>
      </Card>
    );
  }
  return (
    <div className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
      <Card title="Verified datasets">
        <ul className="-mx-2 space-y-0.5">
          {datasets.map((d) => (
            <li key={d.id}>
              <button
                type="button"
                onClick={() => onSelect(d.id)}
                className={cx("w-full rounded-lg px-2 py-2 text-left text-[13px] hover:bg-white/[0.03]", current?.id === d.id && "bg-white/[0.04]")}
                data-testid="qh-dataset-row"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-2xs text-fg">{d.symbol}</span>
                  <QualityBadge status={d.quality.status} />
                </div>
                <p className="mt-0.5 text-2xs text-fg-faint">
                  {d.start} → {d.end} · {fmt(d.records, 0)} bars {d.fixture ? "· FIXTURE" : ""}
                </p>
              </button>
            </li>
          ))}
        </ul>
      </Card>
      {current && <DatasetDetail id={current.id} mode={mode} />}
    </div>
  );
}

function QualityBadge({ status }: { status: "OK" | "WARN" | "BLOCK" }) {
  return (
    <Badge tone={status === "OK" ? "positive" : status === "WARN" ? "warning" : "danger"} icon={status === "OK" ? "✓" : status === "WARN" ? "!" : "⊘"}>
      {status === "OK" ? "Quality OK" : status === "WARN" ? "Usable · warnings" : "Blocked"}
    </Badge>
  );
}

function DatasetDetail({ id, mode }: { id: string; mode: Mode }) {
  const [d, setD] = useState<QhDataset | null>(null);
  const [preview, setPreview] = useState<{ ts: number; close: number; instrument_id: number }[]>([]);
  useEffect(() => {
    let live = true;
    void Promise.all([api.qhDataset(id), api.qhPreview(id)]).then(([dataset, p]) => {
      if (!live) return;
      setD(dataset);
      setPreview(p.points);
    });
    return () => {
      live = false;
    };
  }, [id]);
  const points = useMemo(
    () => preview.map((p) => ({ label: new Date(p.ts / 1e6).toISOString().slice(0, 16).replace("T", " "), value: p.close })),
    [preview],
  );
  const events = useMemo(() => {
    const out: { index: number; label: string }[] = [];
    for (let i = 1; i < preview.length; i++) {
      if (preview[i]?.instrument_id !== preview[i - 1]?.instrument_id) {
        const sym = d?.manifest.instruments.find((x) => x.instrument_id === preview[i]?.instrument_id)?.raw_symbol;
        out.push({ index: i, label: `roll → ${sym ?? preview[i]?.instrument_id}` });
      }
    }
    return out;
  }, [preview, d]);
  if (!d) return <Card title="Dataset"><p className="text-[13px] text-fg-faint">Loading…</p></Card>;
  const m = d.manifest;
  return (
    <Card
      title={`Dataset ${d.id}`}
      aside={
        <div className="flex gap-2">
          {d.fixture && <FixtureBadge />}
          <QualityBadge status={d.quality.status} />
        </div>
      }
      testId="qh-dataset-detail"
    >
      <LineChart points={points} events={events} ariaLabel={`Close prices of ${d.symbol}, unadjusted, with contract rolls marked`} testId="qh-preview" />
      <p className="mt-1 text-2xs text-fg-faint">Close, unadjusted. A roll is a switch to another contract — the jump is never profit or loss.</p>
      <div className="mt-4 grid gap-5 lg:grid-cols-2">
        <div>
          <p className="label mb-2">Provenance</p>
          <dl>
            <Kv label="Source">{d.provider} · {d.dataset} · {d.schema} · {d.stype_in} {d.symbol}</Kv>
            <Kv label="Window (UTC)">{d.start} → {d.end} (exclusive)</Kv>
            <Kv label="Bars">{fmt(d.records, 0)}</Kv>
            <Kv label="Time convention">{m.time_convention}</Kv>
            <Kv label="Prices">{m.prices}</Kv>
            <Kv label="Contracts">
              {m.instruments.map((i) => `${i.raw_symbol ?? "?"} (${i.instrument_id}, ${fmt(i.bars, 0)} bars)`).join(" · ")}
            </Kv>
            <Kv label="Definitions">{m.definitions.records} record(s) over {m.definitions.days_cached} day(s)</Kv>
            <Kv label="Snapshot" mono>{d.snapshot_sha256}</Kv>
            {mode !== "Simple" && <Kv label="Calendar">{m.calendar.library} {m.calendar.version}</Kv>}
            {mode !== "Simple" && <Kv label="Transform">{m.transform_version} · SDK {m.sdk_version}</Kv>}
            <Kv label="Acquisition">
              {m.acquisition.length ? m.acquisition.map((a) => `${a.job_id} (${usd(a.estimated_cost_usd)} est.)`).join(", ") : "—"}
            </Kv>
            <Kv label="License">{m.license}</Kv>
          </dl>
        </div>
        <div>
          <p className="label mb-2">Data quality</p>
          <ul className="space-y-2" data-testid="qh-findings">
            {d.quality.findings.map((f) => (
              <li key={f.code} className="text-[13px]">
                <div className="flex items-center gap-2">
                  <Badge tone={f.level === "BLOCK" ? "danger" : f.level === "WARN" ? "warning" : "muted"}>{f.level}</Badge>
                  <span className="font-mono text-2xs text-fg-faint">{f.code}</span>
                  {f.count > 0 && <span className="text-2xs text-fg-faint">× {f.count}</span>}
                </div>
                <p className="mt-0.5 text-fg-muted">{f.message}</p>
                {mode !== "Simple" && f.examples.length > 0 && <p className="font-mono text-2xs text-fg-faint">{f.examples.join(" · ")}</p>}
              </li>
            ))}
            {!d.quality.findings.length && <li className="text-[13px] text-fg-muted">No findings.</li>}
          </ul>
          {d.quality.capabilities && (
            <>
              <p className="label mb-2 mt-5">What this data can support</p>
              <table className="w-full text-left text-[12px]" data-testid="qh-capabilities">
                <tbody>
                  {d.quality.capabilities.map((c) => (
                    <tr key={c.use} className="border-t border-ql-border/60 align-top">
                      <td className="py-1.5 pr-2 text-fg">{c.use}</td>
                      <td className="py-1.5 pr-2">
                        <Badge tone={c.status === "FIT" ? "positive" : c.status === "LIMITED" ? "warning" : "muted"}>{c.status.replace("_", " ")}</Badge>
                      </td>
                      <td className="py-1.5 text-fg-muted">{c.why}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </Card>
  );
}

function Audit({ tick }: { tick: string | null }) {
  const [rows, setRows] = useState<QhAudit[]>([]);
  useEffect(() => {
    void api.qhAudit().then(setRows);
  }, [tick]);
  return (
    <Card title="Audit trail" aside={<span className="text-2xs text-fg-faint">every connect, quote, approval and download — never the key</span>}>
      <div className="max-h-64 overflow-y-auto">
        <table className="w-full text-left text-2xs">
          <tbody className="font-mono text-fg-muted">
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-ql-border/60">
                <td className="py-0.5 pr-2 text-fg-faint">{r.at.slice(0, 19).replace("T", " ")}</td>
                <td className="pr-2 text-fg">{r.action}</td>
                <td className="truncate">{JSON.stringify(r.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
