import type { QhDataset, QrAssumption, QrCheck, QrDraft, QrSpec, QrStrategy, QrStrategyRow, QrTemplate } from "@jarvis/protocol";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../../../lib/api";
import { Button, cx } from "../../ui/primitives";
import { Badge, Card, Field, compactInputClass, inputClass } from "../ui";
import { Empty, FixtureBadge, type Mode } from "./common";

/**
 * Strategy Studio: an idea in words (JARVIS drafts) or a template → editable rule
 * cards → live validation with the plain-language rules → a saved, versioned spec →
 * a backtest or validation run on a verified dataset. Nothing runs unreviewed.
 */

type Path = string;

function get(spec: QrSpec, path: Path): unknown {
  return path.split(".").reduce<unknown>((node, key) => (node && typeof node === "object" ? (node as Record<string, unknown>)[key] : undefined), spec);
}

function set(spec: QrSpec, path: Path, value: unknown): QrSpec {
  const keys = path.split(".");
  const copy = structuredClone(spec) as Record<string, unknown>;
  let node = copy;
  for (const key of keys.slice(0, -1)) {
    const next = node[key];
    node[key] = next && typeof next === "object" ? { ...(next as Record<string, unknown>) } : {};
    node = node[key] as Record<string, unknown>;
  }
  const last = keys[keys.length - 1] ?? "";
  if (value === undefined) delete node[last];
  else node[last] = value;
  return copy as QrSpec;
}

function assumptions(spec: QrSpec): QrAssumption[] {
  return (get(spec, "assumptions") as QrAssumption[] | undefined) ?? [];
}

function withAssumption(spec: QrSpec, field: string, state: QrAssumption["state"]): QrSpec {
  const list = assumptions(spec).map((a) => (a.field === field ? { ...a, state } : a));
  return set(spec, "assumptions", list);
}

type Kind = "number" | "text" | "time" | "select";
interface FieldDef {
  path: Path;
  label: string;
  kind: Kind;
  options?: [string, string][];
  hint?: string;
  optional?: boolean;
  step?: string;
}

const RULE_ORB: FieldDef[] = [
  { path: "rule.range_minutes", label: "Opening range (minutes)", kind: "number" },
  { path: "rule.entry", label: "Entry", kind: "select", options: [["stop_through_range", "Stop order beyond the range"], ["close_beyond_range", "Bar closes beyond → next open"]] },
  { path: "rule.direction", label: "Direction", kind: "select", options: [["both", "Long and short"], ["long", "Long only"], ["short", "Short only"]] },
  { path: "rule.buffer_ticks", label: "Buffer (ticks)", kind: "number" },
];
const RULE_MA: FieldDef[] = [
  { path: "rule.fast", label: "Fast SMA (bars)", kind: "number" },
  { path: "rule.slow", label: "Slow SMA (bars)", kind: "number" },
  { path: "rule.direction", label: "Direction", kind: "select", options: [["both", "Long and short"], ["long", "Long only"], ["short", "Short only"]] },
];

const CARDS: { title: string; fields: FieldDef[] | "rule"; modes?: Mode[] }[] = [
  {
    title: "Session",
    fields: [
      { path: "session.start", label: "Start (New York)", kind: "time" },
      { path: "session.end", label: "End", kind: "time" },
      { path: "session.flatten_at", label: "Flat by", kind: "time", hint: "Market exit at the next bar's open." },
      { path: "session.entry_cutoff", label: "No new entries after", kind: "time", optional: true },
    ],
  },
  { title: "Entry rule", fields: "rule" },
  {
    title: "Exits",
    fields: [
      { path: "exits.stop.type", label: "Stop", kind: "select", options: [["range_opposite", "Opposite side of the range"], ["ticks", "Fixed ticks"], ["none", "None"]] },
      { path: "exits.stop.ticks", label: "Stop ticks", kind: "number", optional: true },
      { path: "exits.target.type", label: "Target", kind: "select", options: [["r_multiple", "Multiple of risk (R)"], ["ticks", "Fixed ticks"], ["none", "None"]] },
      { path: "exits.target.value", label: "Target value", kind: "number", optional: true, step: "0.25" },
    ],
  },
  {
    title: "Execution & costs",
    fields: [
      { path: "execution.slippage_ticks", label: "Slippage (ticks / fill)", kind: "number" },
      { path: "execution.limit_fill", label: "Targets fill", kind: "select", options: [["trade_through", "Only if price trades through"], ["touch", "On touch (optimistic)"]] },
      { path: "execution.latency_bars", label: "Latency (bars)", kind: "number" },
      { path: "costs.commission_per_contract_side", label: "Commission $ / contract / side", kind: "number", step: "0.01" },
      { path: "costs.exchange_fees_per_contract_side", label: "Exchange fees $ / contract / side", kind: "number", step: "0.01" },
    ],
  },
  {
    title: "Size & account",
    fields: [
      { path: "sizing.contracts", label: "Contracts", kind: "number" },
      { path: "sizing.account_capital", label: "Account capital ($)", kind: "number" },
      { path: "sizing.initial_margin_per_contract", label: "Initial margin / contract ($)", kind: "number", optional: true },
    ],
  },
  {
    title: "Validation plan (fixed before the run)",
    modes: ["Research", "Institutional"],
    fields: [
      { path: "validation.oos_fraction", label: "Out-of-sample share", kind: "number", step: "0.05" },
      { path: "validation.holdout_fraction", label: "Sealed holdout share", kind: "number", step: "0.05" },
      { path: "validation.embargo_sessions", label: "Embargo (sessions)", kind: "number" },
      { path: "validation.min_trades_oos", label: "Min. OOS trades", kind: "number" },
      { path: "validation.walk_forward.train_sessions", label: "Walk-forward train", kind: "number" },
      { path: "validation.walk_forward.test_sessions", label: "Walk-forward test", kind: "number" },
      { path: "validation.walk_forward.mode", label: "Walk-forward mode", kind: "select", options: [["rolling", "Rolling"], ["anchored", "Anchored"]] },
      { path: "validation.bootstrap.seed", label: "Bootstrap seed", kind: "number" },
    ],
  },
];

export function StrategyStudio({
  mode,
  tick,
  datasets,
  onRun,
}: {
  mode: Mode;
  tick: string | null;
  datasets: QhDataset[];
  onRun: (runId: string) => void;
}) {
  const [templates, setTemplates] = useState<QrTemplate[]>([]);
  const [strategies, setStrategies] = useState<QrStrategyRow[]>([]);
  const [spec, setSpec] = useState<QrSpec | null>(null);
  const [origin, setOrigin] = useState<"user" | "ai">("user");
  const [draft, setDraft] = useState<QrDraft | null>(null);
  const [strategy, setStrategy] = useState<QrStrategy | null>(null);
  const [versionId, setVersionId] = useState<string | null>(null);
  const [check, setCheck] = useState<QrCheck | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string; remedy?: string | null } | null>(null);

  const load = useCallback(async () => {
    const [t, s] = await Promise.all([api.qrTemplates(), api.qrStrategies()]);
    setTemplates(t);
    setStrategies(s);
  }, []);
  useEffect(() => {
    void load();
  }, [load, tick]);

  // Live validation of whatever is in the editor.
  useEffect(() => {
    if (!spec) return;
    let live = true;
    const timer = setTimeout(() => {
      api
        .qrCheck(spec)
        .then((c) => live && setCheck(c))
        .catch(() => live && setCheck(null));
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [spec]);

  const edit = (next: QrSpec) => {
    setSpec(next);
    setReviewed(false);
    setVersionId(null);
  };

  const openStrategy = async (id: string, version?: string) => {
    const s = await api.qrStrategy(id);
    setStrategy(s);
    const v = s.versions.find((x) => x.id === version) ?? s.versions[s.versions.length - 1];
    if (v) {
      setSpec(v.spec);
      setVersionId(v.id);
      setOrigin("user");
      setDraft(null);
      setReviewed(true);
    }
  };

  const saved = versionId !== null;
  const save = async () => {
    if (!spec) return;
    setMessage(null);
    try {
      const parent = strategy?.versions[strategy.versions.length - 1]?.id;
      let result: QrStrategy;
      if (origin === "ai") {
        result = await api.qrCreateFromAi(spec, note || `Drafted by JARVIS${draft ? `: ${draft.summary}` : ""}`, strategy ? parent : undefined);
      } else if (strategy) {
        result = await api.qrAddVersion(strategy.id, spec, note || undefined, parent);
      } else {
        result = await api.qrCreate(spec, note || undefined);
      }
      const latest = result.versions.find((v) => v.spec_sha256 === check?.sha256) ?? result.versions[result.versions.length - 1];
      setStrategy(result);
      setVersionId(latest?.id ?? null);
      setOrigin("user");
      setNote("");
      setMessage({ tone: "ok", text: `Saved as version ${latest?.number ?? "?"} — frozen by its hash.` });
      void load();
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : "Couldn't save." });
    }
  };

  return (
    <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]" data-testid="qr-studio">
      <div className="space-y-5">
        <Composer
          current={saved ? spec : null}
          onDraft={(d) => {
            setDraft(d);
            setSpec(d.spec);
            setOrigin("ai");
            setVersionId(null);
            setReviewed(false);
            if (!saved) setStrategy(null);
          }}
        />
        <Card title="Start from a template">
          <div className="flex flex-col gap-2">
            {templates.map((t) => (
              <Button
                key={t.kind}
                className="justify-start"
                onClick={() => {
                  setSpec(t.spec);
                  setOrigin("user");
                  setDraft(null);
                  setStrategy(null);
                  setVersionId(null);
                  setReviewed(false);
                }}
                data-testid={`qr-template-${t.kind}`}
              >
                {t.title}
              </Button>
            ))}
          </div>
          <p className="mt-2 text-2xs text-fg-faint">Templates are starting points, not trading signals — every value is marked as an assumption.</p>
        </Card>
        <Card title="Saved strategies">
          {strategies.length === 0 ? (
            <p className="text-[13px] text-fg-faint">None yet.</p>
          ) : (
            <ul className="-mx-2 space-y-0.5">
              {strategies.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => void openStrategy(s.id)}
                    className={cx("w-full rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-white/[0.03]", strategy?.id === s.id && "bg-white/[0.04]")}
                    data-testid="qr-strategy-row"
                  >
                    <span className="text-fg">{s.name}</span>
                    <span className="ml-2 text-2xs text-fg-faint">{s.product} · {s.versions} version(s)</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {strategy && (
          <Card title="Versions">
            <ol className="space-y-1.5">
              {strategy.versions.map((v) => (
                <li key={v.id}>
                  <button
                    type="button"
                    onClick={() => void openStrategy(strategy.id, v.id)}
                    className={cx("w-full rounded-lg border px-2 py-1.5 text-left text-[13px]", v.id === versionId ? "border-ql/50 bg-ql/5" : "border-ql-border hover:bg-white/[0.03]")}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-fg">v{v.number}</span>
                      <div className="flex gap-1">
                        <Badge tone={v.origin === "ai" ? "accent" : "muted"}>{v.origin === "ai" ? "JARVIS draft" : "You"}</Badge>
                        <Badge tone={v.state === "READY" ? "positive" : "warning"}>{v.state}</Badge>
                      </div>
                    </div>
                    {v.note && <p className="mt-0.5 truncate text-2xs text-fg-faint">{v.note}</p>}
                    <p className="font-mono text-[10px] text-fg-faint">{v.spec_sha256.slice(0, 12)}</p>
                  </button>
                </li>
              ))}
            </ol>
          </Card>
        )}
      </div>

      <div className="space-y-5">
        {!spec ? (
          <Empty title="Describe an idea, pick a template or open a saved strategy" testId="qr-studio-empty">
            JARVIS turns your words into exact, editable rules. You'll see every assumption and the plain-language rules
            before anything runs.
          </Empty>
        ) : (
          <>
            {draft && <DraftNotes draft={draft} />}
            <Card title="Hypothesis">
              <div className="grid gap-3">
                <Field label="Name">
                  <input className={inputClass} value={String(get(spec, "name") ?? "")} onChange={(e) => edit(set(spec, "name", e.target.value))} data-testid="qr-name" />
                </Field>
                <Field label="What you expect and why">
                  <textarea
                    className={cx(inputClass, "h-20 py-2")}
                    value={String(get(spec, "hypothesis") ?? "")}
                    onChange={(e) => edit(set(spec, "hypothesis", e.target.value))}
                  />
                </Field>
                <InstrumentCard spec={spec} onChange={edit} />
              </div>
            </Card>
            <div className="grid gap-5 lg:grid-cols-2">
              {CARDS.filter((c) => !c.modes || c.modes.includes(mode)).map((card) => (
                <RuleCard key={card.title} title={card.title} spec={spec} fields={card.fields === "rule" ? (get(spec, "rule.type") === "ma_crossover" ? RULE_MA : RULE_ORB) : card.fields} onChange={edit} />
              ))}
              {mode !== "Simple" && <GridCard spec={spec} onChange={edit} />}
            </div>
            <AssumptionsCard spec={spec} onChange={edit} />
            <Card
              title="What will run"
              aside={check?.valid ? <Badge tone={check.state === "READY" ? "positive" : "warning"} testId="qr-state">{check.state}</Badge> : <Badge tone="danger">Invalid</Badge>}
              testId="qr-rules"
            >
              {check && !check.valid && (
                <ul className="space-y-1 text-[13px] text-ql-danger" data-testid="qr-errors">
                  {check.errors.map((e) => (
                    <li key={`${e.field}-${e.problem}`}>
                      <span className="font-mono text-2xs">{e.field}</span>: {e.problem}
                    </li>
                  ))}
                </ul>
              )}
              {check?.valid && (
                <>
                  <ol className="list-decimal space-y-1.5 pl-5 text-[13px] text-fg-muted">
                    {check.description?.map((d) => <li key={d}>{d}</li>)}
                  </ol>
                  {check.warnings && check.warnings.length > 0 && (
                    <ul className="mt-3 space-y-1">
                      {check.warnings.map((w) => (
                        <li key={w} className="text-[13px] text-ql-warning">! {w}</li>
                      ))}
                    </ul>
                  )}
                  {check.unresolved && check.unresolved.length > 0 && (
                    <p className="mt-3 text-[13px] text-ql-warning">
                      Unknown: {check.unresolved.map((u) => u.field).join(", ")} — resolve before running.
                    </p>
                  )}
                </>
              )}
              {mode === "Institutional" && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-2xs text-fg-muted">Machine-readable spec · SHA-256 {check?.sha256?.slice(0, 16)}</summary>
                  <pre className="selectable mt-2 max-h-72 overflow-auto rounded-lg bg-black/30 p-3 font-mono text-[11px] text-fg-muted">{JSON.stringify(spec, null, 2)}</pre>
                </details>
              )}
              <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-ql-border pt-3">
                {!saved && (
                  <>
                    <label className="flex items-center gap-2 text-[13px] text-fg">
                      <input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} data-testid="qr-reviewed" />
                      I reviewed these rules and assumptions
                    </label>
                    <input className={cx(compactInputClass, "w-64")} placeholder="Change note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
                    <Button variant="primary" disabled={!reviewed || !check?.valid} onClick={() => void save()} data-testid="qr-save">
                      {strategy ? `Save as version ${(strategy.versions.at(-1)?.number ?? 0) + 1}` : "Save strategy"}
                    </Button>
                  </>
                )}
                {saved && <span className="text-[13px] text-ql-positive">✓ Saved version — edit any field to create the next version.</span>}
              </div>
              {message && (
                <p className={cx("mt-2 text-[13px]", message.tone === "ok" ? "text-ql-positive" : "text-ql-danger")} data-testid="qr-save-message">
                  {message.text}
                </p>
              )}
            </Card>
            {saved && versionId && (
              <RunLauncher versionId={versionId} spec={spec} datasets={datasets} ready={check?.state === "READY"} onRun={onRun} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Composer({ current, onDraft }: { current: QrSpec | null; onDraft: (d: QrDraft) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; remedy?: string | null } | null>(null);
  return (
    <Card title="Describe your idea" testId="qr-composer">
      <textarea
        className={cx(inputClass, "h-28 py-2")}
        placeholder="e.g. Trade the breakout of NQ's first 15 minutes after the open, both directions, stop at the other side of the range, target twice the risk, flat before the close."
        value={text}
        onChange={(e) => setText(e.target.value)}
        data-testid="qr-idea"
      />
      <div className="mt-2 flex items-center gap-2">
        <Button
          variant="primary"
          disabled={busy || text.trim().length < 8}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              onDraft(await api.qrInterpret(text.trim(), current ?? undefined));
            } catch (e) {
              setError({ text: e instanceof ApiError ? e.message : "JARVIS couldn't draft it.", remedy: e instanceof ApiError ? e.suggestion : null });
            } finally {
              setBusy(false);
            }
          }}
          data-testid="qr-draft"
        >
          {busy ? "JARVIS is drafting…" : current ? "Revise with JARVIS" : "Draft rules with JARVIS"}
        </Button>
      </div>
      <p className="mt-2 text-2xs text-fg-faint">JARVIS proposes rules only — it never calculates results. You review and save; every AI revision becomes a new, counted variant.</p>
      {error && (
        <p className="mt-2 text-[13px] text-ql-danger" role="alert">
          {error.text}
          {error.remedy && <span className="block text-fg-muted">{error.remedy}</span>}
        </p>
      )}
    </Card>
  );
}

function DraftNotes({ draft }: { draft: QrDraft }) {
  return (
    <Card title="JARVIS draft" aside={<Badge tone="accent">{draft.model}</Badge>} testId="qr-draft-notes">
      <p className="text-[15px] text-fg">{draft.summary}</p>
      {draft.questions.length > 0 && (
        <ul className="mt-3 space-y-1">
          {draft.questions.map((q) => (
            <li key={q.question} className="text-[13px] text-ql-warning">
              ? {q.question} {q.field && <span className="font-mono text-2xs text-fg-faint">({q.field})</span>}
            </li>
          ))}
        </ul>
      )}
      {draft.unsupported.length > 0 && (
        <p className="mt-2 text-[13px] text-fg-muted">Not expressible yet: {draft.unsupported.join("; ")}.</p>
      )}
      <p className="mt-2 text-2xs text-fg-faint">{draft.note}</p>
    </Card>
  );
}

function InstrumentCard({ spec, onChange }: { spec: QrSpec; onChange: (s: QrSpec) => void }) {
  const product = String(get(spec, "instrument.product") ?? "NQ");
  const symbol = String(get(spec, "instrument.symbol") ?? "NQ.v.0");
  const roll = symbol.split(".")[1] ?? "v";
  const update = (p: string, r: string) => {
    let next = set(spec, "instrument.product", p);
    next = set(next, "instrument.symbol", `${p}.${r}.0`);
    next = set(next, "instrument.stype_in", "continuous");
    onChange(next);
  };
  return (
    <div className="grid grid-cols-2 gap-3">
      <Field label="Instrument" hint="NQ $5/tick (×20), MNQ $0.50/tick (×2), ES $12.50/tick (×50), MES $1.25/tick (×5).">
        <select className={inputClass} value={product} onChange={(e) => update(e.target.value, roll)} data-testid="qr-product">
          {["NQ", "MNQ", "ES", "MES"].map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
      </Field>
      <Field label="Contract series" hint="Continuous symbol; positions never cross a roll.">
        <select className={inputClass} value={roll} onChange={(e) => update(product, e.target.value)}>
          <option value="v">{product}.v.0 — by volume</option>
          <option value="c">{product}.c.0 — by expiry</option>
          <option value="n">{product}.n.0 — by open interest</option>
        </select>
      </Field>
    </div>
  );
}

function RuleCard({ title, spec, fields, onChange }: { title: string; spec: QrSpec; fields: FieldDef[]; onChange: (s: QrSpec) => void }) {
  const list = assumptions(spec);
  return (
    <Card title={title}>
      <div className="grid grid-cols-2 gap-3">
        {fields.map((f) => {
          const value = get(spec, f.path);
          const assumption = list.find((a) => a.field === f.path);
          const update = (raw: string) => {
            let parsed: unknown = raw;
            if (f.kind === "number") parsed = raw === "" ? (f.optional ? undefined : 0) : Number(raw);
            if (f.kind === "time" && raw === "" && f.optional) parsed = null;
            let next = set(spec, f.path, parsed);
            if (assumption && assumption.state !== "confirmed") next = withAssumption(next, f.path, "confirmed");
            onChange(next);
          };
          return (
            <Field key={f.path} label={f.label} hint={f.hint} state={assumption?.state}>
              {f.kind === "select" ? (
                <select className={inputClass} value={String(value ?? "")} onChange={(e) => update(e.target.value)} data-testid={`qr-field-${f.path}`}>
                  {f.options?.map(([v, label]) => (
                    <option key={v} value={v}>{label}</option>
                  ))}
                </select>
              ) : (
                <input
                  className={inputClass}
                  type={f.kind === "time" ? "time" : f.kind === "number" ? "number" : "text"}
                  step={f.step ?? (f.kind === "number" ? "1" : undefined)}
                  value={value === null || value === undefined ? "" : String(value)}
                  onChange={(e) => update(e.target.value)}
                  data-testid={`qr-field-${f.path}`}
                />
              )}
            </Field>
          );
        })}
      </div>
    </Card>
  );
}

function GridCard({ spec, onChange }: { spec: QrSpec; onChange: (s: QrSpec) => void }) {
  const grid = (get(spec, "validation.parameter_grid") as Record<string, number[]> | undefined) ?? {};
  const stress = (get(spec, "validation.cost_stress") as number[] | undefined) ?? [];
  const [text, setText] = useState(Object.entries(grid).map(([k, v]) => `${k}: ${v.join(", ")}`).join("\n"));
  const [stressText, setStressText] = useState(stress.join(", "));
  useEffect(() => {
    setText(Object.entries(grid).map(([k, v]) => `${k}: ${v.join(", ")}`).join("\n"));
    setStressText(stress.join(", "));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(grid), JSON.stringify(stress)]);
  const combos = Object.values(grid).reduce((n, v) => n * Math.max(1, v.length), 1);
  return (
    <Card title="Pre-registered parameter grid & stress">
      <Field label="Grid (one parameter per line)" hint="Evaluated on in-sample data only, and inside each walk-forward training window.">
        <textarea
          className={cx(inputClass, "h-20 py-2 font-mono text-[12px]")}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            const out: Record<string, number[]> = {};
            for (const line of text.split("\n")) {
              const [k, v] = line.split(":");
              if (k?.trim() && v) out[k.trim()] = v.split(",").map((x) => Number(x.trim())).filter((x) => Number.isFinite(x));
            }
            onChange(set(spec, "validation.parameter_grid", out));
          }}
        />
      </Field>
      <p className="mt-1 text-2xs text-fg-faint">{combos} combination(s) — each one counts as a trial for the selection-bias correction.</p>
      <Field label="Cost stress multipliers">
        <input
          className={inputClass}
          value={stressText}
          onChange={(e) => setStressText(e.target.value)}
          onBlur={() => onChange(set(spec, "validation.cost_stress", stressText.split(",").map((x) => Number(x.trim())).filter((x) => Number.isFinite(x) && x > 0)))}
        />
      </Field>
    </Card>
  );
}

function AssumptionsCard({ spec, onChange }: { spec: QrSpec; onChange: (s: QrSpec) => void }) {
  const list = assumptions(spec);
  if (!list.length) return null;
  return (
    <Card title="Assumptions" aside={<span className="text-2xs text-fg-faint">every value you didn't state</span>} testId="qr-assumptions">
      <ul className="space-y-2">
        {list.map((a) => (
          <li key={a.field} className="flex flex-wrap items-center gap-3 text-[13px]">
            <Badge tone={a.state === "confirmed" ? "positive" : a.state === "unknown" ? "danger" : "warning"}>{a.state}</Badge>
            <span className="font-mono text-2xs text-fg">{a.field}</span>
            <span className="text-fg-muted">{a.note}</span>
            {a.state !== "confirmed" && (
              <Button className="ml-auto h-7" onClick={() => onChange(withAssumption(spec, a.field, "confirmed"))} data-testid={`qr-confirm-${a.field}`}>
                {a.state === "unknown" ? "I've set it — confirm" : "Confirm"}
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function RunLauncher({ versionId, spec, datasets, ready, onRun }: { versionId: string; spec: QrSpec; datasets: QhDataset[]; ready: boolean; onRun: (id: string) => void }) {
  const product = String(get(spec, "instrument.product") ?? "");
  const usable = useMemo(() => datasets.filter((d) => d.quality.status !== "BLOCK"), [datasets]);
  const [datasetId, setDatasetId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; remedy?: string | null } | null>(null);
  useEffect(() => {
    const match = usable.find((d) => d.symbol.startsWith(`${product}.`)) ?? usable[0];
    setDatasetId(match?.id ?? "");
  }, [usable, product]);
  const chosen = usable.find((d) => d.id === datasetId);
  const start = async (kind: "backtest" | "validation") => {
    setBusy(true);
    setError(null);
    try {
      const run = await api.qrStart({ version_id: versionId, dataset_id: datasetId, kind });
      onRun(run.id);
    } catch (e) {
      setError({ text: e instanceof ApiError ? e.message : "Couldn't start.", remedy: e instanceof ApiError ? e.suggestion : null });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Run it" testId="qr-launcher">
      {usable.length === 0 ? (
        <p className="text-[13px] text-fg-muted">No verified dataset yet — get data in the Data Hub first.</p>
      ) : (
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Verified dataset">
            <select className={cx(compactInputClass, "min-w-[320px]")} value={datasetId} onChange={(e) => setDatasetId(e.target.value)} data-testid="qr-run-dataset">
              {usable.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.symbol} · {d.start} → {d.end} · {d.records.toLocaleString("en-US")} bars{d.fixture ? " · FIXTURE" : ""}
                </option>
              ))}
            </select>
          </Field>
          <Button variant="primary" disabled={!ready || busy || !datasetId} onClick={() => void start("backtest")} data-testid="qr-run-backtest">
            Run backtest
          </Button>
          <Button disabled={!ready || busy || !datasetId} onClick={() => void start("validation")} data-testid="qr-run-validation">
            Run full validation
          </Button>
          {chosen?.fixture && <FixtureBadge />}
        </div>
      )}
      {!ready && <p className="mt-2 text-[13px] text-ql-warning">Resolve the unknown assumptions first — a draft never runs.</p>}
      {chosen && !chosen.symbol.startsWith(`${product}.`) && (
        <p className="mt-2 text-[13px] text-ql-danger">This dataset isn't {product}: the run will be marked INVALID (multipliers differ).</p>
      )}
      {error && (
        <p className="mt-2 text-[13px] text-ql-danger">
          {error.text} {error.remedy && <span className="text-fg-muted">{error.remedy}</span>}
        </p>
      )}
      <p className="mt-2 text-2xs text-fg-faint">The final holdout stays sealed. You can evaluate it once, later, in Validation.</p>
    </Card>
  );
}
