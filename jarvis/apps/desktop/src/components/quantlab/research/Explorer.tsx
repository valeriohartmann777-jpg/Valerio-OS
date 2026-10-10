import type { QrCompare, QrRunRow, QrStrategy, QrStrategyRow, QrTrade, QrTradeDetail, QrTrades } from "@jarvis/protocol";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../../../lib/api";
import { Button, cx } from "../../ui/primitives";
import { Badge, Card, compactInputClass, inputClass } from "../ui";
import { CandleChart, type PriceLine } from "./charts";
import { Empty, FixtureBadge, Kv, RunBadge, RunPicker, VerdictTag, fmt, hhmm, price, tone, usd, type Mode } from "./common";

// Trade Explorer -------------------------------------------------------------------------------

const PAGE = 100;

export function TradeExplorer({
  runId,
  runs,
  focus,
  onPick,
}: {
  runId: string | null;
  runs: QrRunRow[];
  focus: number | null;
  onPick: (id: string) => void;
}) {
  const [segment, setSegment] = useState<string>("");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<QrTrades | null>(null);
  const [selected, setSelected] = useState<QrTrade | null>(null);
  const [onlyAmbiguous, setOnlyAmbiguous] = useState(false);
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (!runId) return;
    let live = true;
    api
      .qrTrades(runId, page * PAGE, PAGE, segment || undefined)
      .then((d) => {
        if (!live) return;
        setData(d);
        if (focus !== null) setSelected(d.rows.find((t) => t.number === focus) ?? null);
      })
      .catch(() => live && setData(null));
    return () => {
      live = false;
    };
  }, [runId, page, segment, focus]);

  const rows = useMemo(
    () => (data?.rows ?? []).filter((t) => (!onlyAmbiguous || t.ambiguous) && (!reason || t.exit_reason === reason)),
    [data, onlyAmbiguous, reason],
  );
  if (!runId) return <Empty title="No run selected"><RunPicker runs={runs} value={null} onChange={onPick} /></Empty>;
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE));
  return (
    <div className="space-y-5" data-testid="qr-trades">
      <div className="flex flex-wrap items-center gap-3">
        <RunPicker runs={runs} value={runId} onChange={onPick} />
        <select className={compactInputClass} value={segment} onChange={(e) => { setSegment(e.target.value); setPage(0); }} aria-label="Segment">
          <option value="">All segments</option>
          <option value="IS">In-sample</option>
          <option value="OOS">Out-of-sample</option>
          <option value="HOLDOUT">Holdout</option>
        </select>
        <select className={compactInputClass} value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Exit reason">
          <option value="">Any exit</option>
          {["TARGET", "STOP", "TIME_EXIT", "LAST_BAR_CLOSE", "SIGNAL_EXIT"].map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-[13px] text-fg-muted">
          <input type="checkbox" checked={onlyAmbiguous} onChange={(e) => setOnlyAmbiguous(e.target.checked)} />
          Ambiguous only
        </label>
        <span className="ml-auto text-2xs text-fg-faint">{data?.total ?? 0} trades · times UTC</span>
      </div>
      <div className="grid gap-5 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card>
          <div className="max-h-[560px] overflow-y-auto">
            <table className="w-full text-left text-[12px]">
              <thead className="sticky top-0 bg-ql-surface text-2xs text-fg-faint">
                <tr>
                  {["#", "Session", "Seg", "Side", "Entry", "Exit", "Exit reason", "R", "MAE/MFE", "Net"].map((h) => (
                    <th key={h} className="py-1 pr-2 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="tabular text-fg-muted">
                {rows.map((t) => (
                  <tr
                    key={`${t.segment}-${t.number}`}
                    className={cx("cursor-pointer border-t border-ql-border/60 hover:bg-white/[0.03]", selected?.number === t.number && selected.segment === t.segment && "bg-ql/[0.06]")}
                    onClick={() => setSelected(t)}
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && setSelected(t)}
                    data-testid="qr-trade-row"
                  >
                    <td className="py-1 font-mono text-2xs text-fg-faint">{t.number}</td>
                    <td>{t.session}</td>
                    <td className="font-mono text-2xs">{t.segment}</td>
                    <td>{t.direction === "LONG" ? "▲ Long" : "▼ Short"}</td>
                    <td className="font-mono text-2xs">{hhmm(t.entry_time)} {price(t.entry_price)}</td>
                    <td className="font-mono text-2xs">{hhmm(t.exit_time)} {price(t.exit_price)}</td>
                    <td>{t.exit_reason}{t.ambiguous && <span className="ml-1 text-ql-warning" title="ambiguous 1-minute bar">◐</span>}</td>
                    <td className="font-mono text-2xs">{fmt(t.r_multiple)}</td>
                    <td className="font-mono text-2xs">{t.mae_ticks}/{t.mfe_ticks}</td>
                    <td className={cx("text-right", tone(t.net))}>{usd(t.net, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {pages > 1 && (
            <div className="mt-3 flex items-center gap-2 text-2xs text-fg-faint">
              <Button className="h-7" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</Button>
              <span>page {page + 1} / {pages}</span>
              <Button className="h-7" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}>Next</Button>
            </div>
          )}
        </Card>
        {selected ? <TradeDetail runId={runId} trade={selected} /> : <Empty title="Pick a trade">See its bars at the time information was available, the fills, the costs and why it exited.</Empty>}
      </div>
    </div>
  );
}

function TradeDetail({ runId, trade }: { runId: string; trade: QrTrade }) {
  const [d, setD] = useState<QrTradeDetail | null>(null);
  useEffect(() => {
    let live = true;
    setD(null);
    api.qrTrade(runId, trade.number, trade.segment).then((x) => live && setD(x));
    return () => {
      live = false;
    };
  }, [runId, trade.number, trade.segment]);
  const lines = useMemo(() => {
    const out: PriceLine[] = [];
    if (!d) return out;
    if (d.range) {
      out.push({ value: d.range.high, label: "range high", tone: "muted", dash: "2 3" });
      out.push({ value: d.range.low, label: "range low", tone: "muted", dash: "2 3" });
    }
    if (trade.stop_price !== null) out.push({ value: trade.stop_price, label: "stop", tone: "danger", dash: "5 3" });
    if (trade.target_price !== null) out.push({ value: trade.target_price, label: "target", tone: "positive", dash: "5 3" });
    out.push({ value: trade.entry_price, label: "entry", tone: "accent" });
    return out;
  }, [d, trade]);
  const candles = useMemo(() => (d?.bars ?? []).map((b) => ({ label: hhmm(b.t), o: b.o, h: b.h, l: b.l, c: b.c })), [d]);
  const markers = useMemo(() => {
    if (!d) return [];
    const at = (iso: string) => d.bars.findIndex((b) => b.t === iso);
    const dir = trade.direction === "LONG" ? 1 : -1;
    return [
      { index: at(trade.entry_time), price: trade.entry_price, kind: "entry" as const, direction: dir as 1 | -1, label: `entry ${price(trade.entry_price)}` },
      { index: at(trade.exit_time), price: trade.exit_price, kind: "exit" as const, direction: dir as 1 | -1, win: trade.net > 0, label: `exit ${price(trade.exit_price)}` },
    ].filter((m) => m.index >= 0);
  }, [d, trade]);
  return (
    <Card title={`Trade #${trade.number} · ${trade.session} · ${trade.raw_symbol}`} aside={<span className={cx("text-[15px] font-medium tabular", tone(trade.net))}>{usd(trade.net, true)}</span>} testId="qr-trade-detail">
      {!d ? (
        <p className="text-[13px] text-fg-faint">Loading bars…</p>
      ) : (
        <>
          <CandleChart candles={candles} lines={lines} markers={markers} height={280} ariaLabel={`1-minute bars around trade ${trade.number}`} testId="qr-trade-chart" />
          <ol className="mt-3 space-y-1 text-[13px] text-fg-muted" data-testid="qr-trade-story">
            {d.story.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ol>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div>
              <p className="label mb-1">Fills</p>
              <table className="w-full text-left text-2xs">
                <tbody className="font-mono text-fg-muted">
                  {d.fills.map((f) => (
                    <tr key={f.order_id} className="border-t border-ql-border/60">
                      <td className="py-0.5">{f.side}</td>
                      <td>{price(f.price)}</td>
                      <td title="bar the fill happened in">{hhmm(f.bar_time)}</td>
                      <td title="when the deciding information existed">known {hhmm(f.known_at)}</td>
                      <td>{f.reason}{f.ambiguous ? " ◐" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <p className="label mb-1">Costs</p>
              <dl>
                <Kv label="Commission">{usd(d.costs.commission)}</Kv>
                <Kv label="Exchange fees">{usd(d.costs.exchange_fees)}</Kv>
                <Kv label="Slippage (modelled)">{usd(d.costs.slippage)}</Kv>
                <Kv label="Total">{usd(d.costs.total)}</Kv>
                <Kv label="MAE / MFE">{usd(trade.mae_usd)} / {usd(trade.mfe_usd)}</Kv>
              </dl>
              <p className="mt-1 text-2xs text-fg-faint">MAE/MFE use whole-minute extremes — upper bounds within the entry and exit minutes.</p>
            </div>
          </div>
          <p className="mt-3 text-2xs text-fg-faint">{d.timezone}</p>
        </>
      )}
    </Card>
  );
}

// Experiment library ---------------------------------------------------------------------------

export function Experiments({ mode, tick, onOpenRun, onEquityLab }: { mode: Mode; tick: string | null; onOpenRun: (id: string) => void; onEquityLab: () => void }) {
  const [strategies, setStrategies] = useState<QrStrategyRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<QrStrategy | null>(null);
  const [compare, setCompare] = useState<string[]>([]);
  const [result, setResult] = useState<QrCompare | null>(null);
  const load = useCallback(async () => {
    const s = await api.qrStrategies();
    setStrategies(s);
    setSelected((cur) => cur ?? s[0]?.id ?? null);
  }, []);
  useEffect(() => {
    void load();
  }, [load, tick]);
  useEffect(() => {
    if (!selected) return;
    void api.qrStrategy(selected).then(setDetail);
  }, [selected, tick]);
  return (
    <div className="grid gap-5 xl:grid-cols-[300px_minmax(0,1fr)]" data-testid="qr-experiments">
      <div className="space-y-5">
        <Card title="Strategy families">
          {strategies.length === 0 ? (
            <p className="text-[13px] text-fg-faint">No strategy yet.</p>
          ) : (
            <ul className="-mx-2 space-y-0.5">
              {strategies.map((s) => (
                <li key={s.id}>
                  <button type="button" onClick={() => setSelected(s.id)} className={cx("w-full rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-white/[0.03]", selected === s.id && "bg-white/[0.04]")}>
                    <span className="text-fg">{s.name}</span>
                    <span className="block text-2xs text-fg-faint">{s.product} · {s.versions} version(s){s.latest_run?.verdict ? ` · ${s.latest_run.verdict.replace(/_/g, " ").toLowerCase()}` : ""}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Cash-equity reference (R1)">
          <p className="text-[13px] text-fg-muted">The earlier SMA reference experiments live in their own lab.</p>
          <Button className="mt-2" onClick={onEquityLab}>Open the R1 lab</Button>
        </Card>
      </div>
      {!detail ? (
        <Empty title="Pick a strategy family" />
      ) : (
        <div className="space-y-5">
          <Card title={detail.name} aside={<Badge tone="muted">{detail.product}</Badge>}>
            <p className="text-[13px] text-fg-muted">{detail.hypothesis}</p>
            <div className="mt-3 grid gap-4 sm:grid-cols-3">
              <div>
                <p className="text-xs text-ql-muted">Variants tried</p>
                <p className="text-lg font-medium text-fg tabular" data-testid="qr-variants">{detail.trials.variants}</p>
                <p className="text-2xs text-fg-faint">versions × grid points — the selection-bias correction counts them all</p>
              </div>
              <div>
                <p className="text-xs text-ql-muted">Runs</p>
                <p className="text-lg font-medium text-fg tabular">{detail.runs.length}</p>
              </div>
              <div>
                <p className="text-xs text-ql-muted">Holdout looks</p>
                <p className={cx("text-lg font-medium tabular", detail.holdouts.length > 1 ? "text-ql-warning" : "text-fg")}>{detail.holdouts.length}</p>
                <p className="text-2xs text-fg-faint">{detail.holdouts.length > 1 ? "no longer independent" : "each look is recorded"}</p>
              </div>
            </div>
          </Card>
          <Card title="Version tree & runs">
            <ol className="space-y-3">
              {detail.versions.map((v) => {
                const runs = detail.runs.filter((r) => r.version_id === v.id);
                return (
                  <li key={v.id} className="rounded-xl border border-ql-border p-3">
                    <div className="flex flex-wrap items-center gap-2 text-[13px]">
                      <span className="font-medium text-fg">v{v.number}</span>
                      {v.parent_id && <span className="text-2xs text-fg-faint">from v{detail.versions.find((x) => x.id === v.parent_id)?.number}</span>}
                      <Badge tone={v.origin === "ai" ? "accent" : "muted"}>{v.origin === "ai" ? "JARVIS draft" : "You"}</Badge>
                      <Badge tone={v.state === "READY" ? "positive" : "warning"}>{v.state}</Badge>
                      <span className="font-mono text-[10px] text-fg-faint">{v.spec_sha256.slice(0, 12)}</span>
                      {v.note && <span className="text-2xs text-fg-muted">— {v.note}</span>}
                    </div>
                    {runs.length > 0 && (
                      <table className="mt-2 w-full text-left text-[12px]">
                        <tbody className="text-fg-muted">
                          {runs.map((r) => (
                            <tr key={r.id} className="border-t border-ql-border/60">
                              <td className="py-1 pr-2">
                                <input type="checkbox" aria-label="compare" checked={compare.includes(r.id)} disabled={r.status !== "COMPLETED"} onChange={(e) => setCompare((c) => (e.target.checked ? [...c, r.id] : c.filter((x) => x !== r.id)))} />
                              </td>
                              <td><RunBadge status={r.status} /></td>
                              <td>{r.kind}{r.include_holdout ? " + holdout" : ""}</td>
                              <td className="font-mono text-2xs">{r.dataset_id}</td>
                              <td>{r.verdict ? <VerdictTag verdict={r.verdict} testId="qr-exp-verdict" /> : "—"}</td>
                              <td className={cx("text-right tabular", tone(r.oos_net))}>{r.oos_net === null ? "—" : `OOS ${usd(r.oos_net, true)}`}</td>
                              <td className="text-right">{r.fixture && <FixtureBadge label="Fixture" />}</td>
                              <td className="text-right">
                                <Button className="h-7" onClick={() => onOpenRun(r.id)} disabled={r.status !== "COMPLETED"}>Open</Button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </li>
                );
              })}
            </ol>
            <div className="mt-3 flex items-center gap-2">
              <Button disabled={compare.length < 2} onClick={async () => setResult(await api.qrCompare(compare))} data-testid="qr-compare">
                Compare {compare.length || ""} run(s)
              </Button>
            </div>
            {result && <Comparison result={result} />}
          </Card>
          {mode !== "Simple" && (
            <Card title="Trial registry (latest)">
              <div className="max-h-60 overflow-y-auto">
                <table className="w-full text-left text-[12px]">
                  <thead className="text-2xs text-fg-faint">
                    <tr><th className="py-1 font-medium">Segment</th><th className="font-medium">Parameters</th><th className="text-right font-medium">Trades</th><th className="text-right font-medium">Net</th><th className="text-right font-medium">Sharpe/day</th></tr>
                  </thead>
                  <tbody className="font-mono text-2xs text-fg-muted">
                    {detail.trials.recent.map((t) => (
                      <tr key={t.id} className="border-t border-ql-border/60">
                        <td className="py-0.5">{t.segment}</td>
                        <td>{Object.entries(t.params).map(([k, v]) => `${k}=${v}`).join(", ") || "as saved"}</td>
                        <td className="text-right">{t.trades}</td>
                        <td className={cx("text-right", tone(t.net))}>{usd(t.net, true)}</td>
                        <td className="text-right">{fmt(t.sharpe_daily, 4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
          <Notes strategy={detail} onSaved={setDetail} />
        </div>
      )}
    </div>
  );
}

function Comparison({ result }: { result: QrCompare }) {
  return (
    <div className="mt-4 space-y-3" data-testid="qr-comparison">
      <table className="w-full text-left text-[12px]">
        <thead className="text-2xs text-fg-faint">
          <tr>{["Run", "Kind", "Verdict", "Net", "OOS net", "OOS trades", "OOS Sharpe", "Max DD"].map((h) => <th key={h} className="py-1 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody className="tabular text-fg-muted">
          {result.runs.map((r) => (
            <tr key={r.id} className="border-t border-ql-border/60">
              <td className="py-1">{r.name} v{r.version}</td>
              <td>{r.kind}</td>
              <td>{r.verdict ? r.verdict.replace(/_/g, " ").toLowerCase() : "—"}</td>
              <td className={tone(r.net)}>{usd(r.net, true)}</td>
              <td className={tone(r.oos_net)}>{usd(r.oos_net, true)}</td>
              <td>{r.oos_trades}</td>
              <td>{fmt(r.oos_sharpe)}</td>
              <td>{usd(r.max_drawdown)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {result.spec_differences.length > 0 && (
        <div>
          <p className="label mb-1">Rule differences</p>
          <table className="w-full text-left text-2xs">
            <tbody className="font-mono text-fg-muted">
              {result.spec_differences.map((d) => (
                <tr key={d.field} className="border-t border-ql-border/60">
                  <td className="py-0.5 pr-3 text-fg">{d.field}</td>
                  {d.values.map((v, i) => <td key={i} className="pr-3">{JSON.stringify(v)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Notes({ strategy, onSaved }: { strategy: QrStrategy; onSaved: (s: QrStrategy) => void }) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState<"note" | "decision">("note");
  const [error, setError] = useState<string | null>(null);
  return (
    <Card title="Research journal" aside={<span className="text-2xs text-fg-faint">notes and decisions stay with the strategy</span>}>
      <form
        className="flex gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!text.trim()) return;
          try {
            onSaved(await api.qrNote(strategy.id, kind, text.trim()));
            setText("");
            setError(null);
          } catch (x) {
            setError(x instanceof ApiError ? x.message : "Couldn't save.");
          }
        }}
      >
        <select className={compactInputClass} value={kind} onChange={(e) => setKind(e.target.value as "note" | "decision")}>
          <option value="note">Note</option>
          <option value="decision">Decision</option>
        </select>
        <input className={inputClass} placeholder="e.g. Parked — wait for a year of licensed data." value={text} onChange={(e) => setText(e.target.value)} />
        <Button type="submit">Add</Button>
      </form>
      {error && <p className="mt-2 text-[13px] text-ql-danger">{error}</p>}
      <ul className="mt-3 space-y-1.5">
        {strategy.notes.map((n) => (
          <li key={n.id} className="text-[13px]">
            <Badge tone={n.kind === "decision" ? "accent" : "muted"}>{n.kind}</Badge>
            <span className="ml-2 text-fg-muted">{n.text}</span>
            <span className="ml-2 font-mono text-2xs text-fg-faint">{n.created_at.slice(0, 10)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
