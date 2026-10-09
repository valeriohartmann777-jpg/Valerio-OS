import type { QlSpecCheck, QlStrategy, QlStrategySpec } from "@jarvis/protocol";
import { type InputHTMLAttributes, type ReactNode, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../../lib/api";
import { Button, cx } from "../ui/primitives";
import { Card, Field, FieldState, Hash, inputClass } from "./ui";

/**
 * Strategy Architect: the SMA-crossover StrategySpec as a form. Every field shows
 * whether it is Unknown (empty), Assumed (a preset value nobody looked at) or
 * Confirmed (set by the user). Nothing is saved until the spec validates, nothing
 * is unknown, and the user has read the plain-language summary.
 */

type Key =
  | "name"
  | "hypothesis"
  | "symbol"
  | "exchange"
  | "currency"
  | "timezone"
  | "adjusted"
  | "interval"
  | "fast"
  | "slow"
  | "slippage"
  | "size"
  | "cash"
  | "fee"
  | "feeBps"
  | "oos";

type Values = Record<Key, string>;
type State = "unknown" | "assumed" | "confirmed";

const FIELD_PATHS: Record<string, Key> = {
  name: "name",
  hypothesis: "hypothesis",
  "instrument.symbol": "symbol",
  "instrument.exchange": "exchange",
  "instrument.currency": "currency",
  "instrument.timezone": "timezone",
  "instrument.corporate_action_policy": "adjusted",
  "timeframe.bar_interval": "interval",
  "signal.fast_window": "fast",
  "signal.slow_window": "slow",
  "execution.slippage_bps": "slippage",
  "position.size_units": "size",
  "position.initial_cash": "cash",
  "costs.fee_fixed_per_order": "fee",
  "costs.fee_variable_bps": "feeBps",
  "costs.currency": "currency",
  "analysis.chronological_oos_fraction": "oos",
};

const QUESTIONS: Partial<Record<Key, string>> = {
  name: "What should this strategy be called?",
  hypothesis: "What do you expect to happen, and why? (Written down before the run.)",
  symbol: "Which instrument does your dataset contain? It must match the data's symbol.",
  exchange: "On which exchange is it listed?",
  currency: "Which currency are prices quoted in?",
  timezone: "Which timezone is the instrument's home market in?",
};

const LABELS: Record<Key, string> = {
  name: "Name",
  hypothesis: "Hypothesis",
  symbol: "Symbol",
  exchange: "Exchange",
  currency: "Currency",
  timezone: "Market timezone",
  adjusted: "Corporate actions",
  interval: "Bar interval",
  fast: "Fast SMA",
  slow: "Slow SMA",
  slippage: "Slippage (bps)",
  size: "Shares per trade",
  cash: "Starting cash",
  fee: "Fee per order",
  feeBps: "Fee (bps of notional)",
  oos: "Out-of-sample share",
};

export function fromSpec(spec: QlStrategySpec): Values {
  return {
    name: spec.name,
    hypothesis: spec.hypothesis,
    symbol: spec.instrument.symbol,
    exchange: spec.instrument.exchange,
    currency: spec.instrument.currency,
    timezone: spec.instrument.timezone,
    adjusted: spec.instrument.corporate_action_policy ?? "",
    interval: spec.timeframe.bar_interval,
    fast: String(spec.signal.fast_window),
    slow: String(spec.signal.slow_window),
    slippage: String(spec.execution.slippage_bps),
    size: String(spec.position.size_units),
    cash: String(spec.position.initial_cash),
    fee: String(spec.costs.fee_fixed_per_order),
    feeBps: String(spec.costs.fee_variable_bps),
    oos: String(spec.analysis.chronological_oos_fraction),
  };
}

const EQUITY_PRESET: Values = {
  name: "",
  hypothesis: "",
  symbol: "",
  exchange: "",
  currency: "USD",
  timezone: "America/New_York",
  adjusted: "data_adjusted",
  interval: "1d",
  fast: "20",
  slow: "50",
  slippage: "5",
  size: "10",
  cash: "10000",
  fee: "1",
  feeBps: "0",
  oos: "0.25",
};

const number = (v: string) => (v.trim() === "" ? Number.NaN : Number(v));

function toSpec(v: Values): QlStrategySpec {
  return {
    schema_version: "0.1",
    name: v.name.trim(),
    hypothesis: v.hypothesis.trim(),
    instrument: {
      asset_class: "cash_equity",
      symbol: v.symbol.trim(),
      exchange: v.exchange.trim(),
      currency: v.currency.trim().toUpperCase(),
      timezone: v.timezone.trim(),
      ...(v.adjusted ? { corporate_action_policy: v.adjusted as "data_adjusted" } : {}),
    },
    timeframe: {
      bar_interval: v.interval as QlStrategySpec["timeframe"]["bar_interval"],
      timestamps_are_bar: "open",
      signal_timestamp: "bar_close",
    },
    signal: {
      type: "sma_crossover",
      fast_window: number(v.fast),
      slow_window: number(v.slow),
      price_column: "close",
      cross: "strict_cross",
    },
    execution: {
      entry_order: "market",
      exit_order: "market",
      fill_timing: "next_bar_open",
      slippage_bps: number(v.slippage),
      end_of_data: "mark_open_position_no_forced_sale",
    },
    position: { direction: "long_only", size_units: number(v.size), initial_cash: number(v.cash), leverage: 1 },
    costs: {
      fee_fixed_per_order: number(v.fee),
      fee_variable_bps: number(v.feeBps),
      currency: v.currency.trim().toUpperCase(),
    },
    analysis: { chronological_oos_fraction: number(v.oos), allow_parameter_search_on_oos: false },
  };
}

export function Architect({
  template,
  editing,
  onSaved,
  onCancel,
}: {
  template: QlStrategySpec | null;
  editing: QlStrategy | null;
  onSaved: (strategy: QlStrategy) => void;
  onCancel: () => void;
}) {
  const latest = editing?.versions[editing.versions.length - 1]?.spec ?? null;
  const [values, setValues] = useState<Values>(() => (latest ? fromSpec(latest) : EQUITY_PRESET));
  const [confirmed, setConfirmed] = useState<Set<Key>>(
    () => new Set(latest ? (Object.keys(EQUITY_PRESET) as Key[]) : []),
  );
  const [check, setCheck] = useState<QlSpecCheck | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const spec = useMemo(() => toSpec(values), [values]);

  useEffect(() => {
    setReviewed(false);
    const id = window.setTimeout(() => {
      api
        .qlValidate(spec)
        .then(setCheck)
        .catch(() => setCheck(null));
    }, 250);
    return () => window.clearTimeout(id);
  }, [spec]);

  const stateOf = (key: Key): State =>
    values[key].trim() === "" && key !== "adjusted" ? "unknown" : confirmed.has(key) ? "confirmed" : "assumed";
  const keys = Object.keys(LABELS) as Key[];
  const unknown = keys.filter((k) => stateOf(k) === "unknown");
  const assumed = keys.filter((k) => stateOf(k) === "assumed");
  const fieldErrors = new Map<Key, string>();
  for (const err of check?.errors ?? []) {
    const key = FIELD_PATHS[err.field];
    if (key && !fieldErrors.has(key)) fieldErrors.set(key, err.problem);
  }

  const set = (key: Key) => (event: { target: { value: string } }) => {
    setValues((v) => ({ ...v, [key]: event.target.value }));
    setConfirmed((c) => new Set(c).add(key));
  };
  const confirmAll = () => setConfirmed(new Set(keys));
  const load = (preset: Values) => {
    setValues(preset);
    setConfirmed(new Set());
  };

  const canSave = Boolean(check?.valid) && unknown.length === 0 && reviewed && !saving;
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      onSaved(editing ? await api.qlAddVersion(editing.id, spec) : await api.qlCreateStrategy(spec));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Saving failed.");
    } finally {
      setSaving(false);
    }
  };

  const input = (key: Key, props: InputHTMLAttributes<HTMLInputElement> = {}) => (
    <Field label={LABELS[key]} state={stateOf(key)}>
      <input
        className={cx(inputClass, fieldErrors.has(key) && "border-ql-danger/70")}
        value={values[key]}
        onChange={set(key)}
        aria-invalid={fieldErrors.has(key)}
        data-field={key}
        {...props}
      />
      {fieldErrors.has(key) && <span className="mt-1 block text-2xs text-ql-danger">{fieldErrors.get(key)}</span>}
    </Field>
  );

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]" data-testid="ql-architect">
      <Card
        title={editing ? `New version of ${editing.name}` : "Strategy Architect · SMA crossover"}
        aside={
          !editing && (
            <div className="flex gap-2">
              <Button onClick={() => load(EQUITY_PRESET)}>Daily equity preset</Button>
              {template && (
                <Button onClick={() => load(fromSpec(template))} data-testid="ql-preset-fixture">
                  Synthetic fixture preset
                </Button>
              )}
            </div>
          )
        }
      >
        <div className="space-y-5">
          <div className="grid gap-3">
            {input("name", { placeholder: "e.g. SPY 20/50 trend" })}
            <Field label={LABELS.hypothesis} state={stateOf("hypothesis")}>
              <textarea
                className={cx(inputClass, "h-16 py-1.5", fieldErrors.has("hypothesis") && "border-ql-danger/70")}
                value={values.hypothesis}
                onChange={set("hypothesis")}
                data-field="hypothesis"
                placeholder="Trend persistence after a 20/50 crossover beats buy & hold after costs…"
              />
              {fieldErrors.has("hypothesis") && (
                <span className="mt-1 block text-2xs text-ql-danger">{fieldErrors.get("hypothesis")}</span>
              )}
            </Field>
          </div>

          <Group title="Instrument · cash equity, long only">
            {input("symbol", { placeholder: "SPY" })}
            {input("exchange", { placeholder: "NYSE Arca" })}
            {input("currency", { maxLength: 3 })}
            {input("timezone")}
            <Field label={LABELS.adjusted} state={stateOf("adjusted")}>
              <select className={inputClass} value={values.adjusted} onChange={set("adjusted")}>
                <option value="">not declared</option>
                <option value="data_adjusted">data is split/dividend adjusted</option>
                <option value="not_applicable_test_fixture">n/a (synthetic fixture)</option>
              </select>
            </Field>
            <Field label={LABELS.interval} state={stateOf("interval")}>
              <select className={inputClass} value={values.interval} onChange={set("interval")} data-field="interval">
                <option value="1d">1 day</option>
                <option value="1h">1 hour</option>
                <option value="5m">5 minutes</option>
                <option value="1m">1 minute</option>
              </select>
            </Field>
          </Group>

          <Group title="Signal · closed bars only">
            {input("fast", { inputMode: "numeric" })}
            {input("slow", { inputMode: "numeric" })}
          </Group>

          <Group title="Execution, position and costs">
            {input("slippage", { inputMode: "decimal" })}
            {input("size", { inputMode: "numeric" })}
            {input("cash", { inputMode: "decimal" })}
            {input("fee", { inputMode: "decimal" })}
            {input("feeBps", { inputMode: "decimal" })}
            <Field label={LABELS.oos} state={stateOf("oos")} hint="The last part of the data, set aside before the run and never used to choose parameters.">
              <select className={inputClass} value={values.oos} onChange={set("oos")}>
                {["0.2", "0.25", "0.3", "0.4"].map((v) => (
                  <option key={v} value={v}>
                    last {Math.round(Number(v) * 100)}% of bars
                  </option>
                ))}
              </select>
            </Field>
          </Group>
          <p className="text-2xs text-fg-faint">
            Fixed by R1: signal at the bar's close → market order at the next bar's open (zero latency), long only, no
            leverage, no stops or targets, open positions at the end are marked, not sold.
          </p>
        </div>
      </Card>

      <div className="space-y-4">
        <Card title="JARVIS · before you save">
          {unknown.length > 0 && (
            <div className="mb-4" data-testid="ql-unknowns">
              <p className="mb-2 text-xs font-medium text-ql-danger">Open questions · {unknown.length}</p>
              <ul className="space-y-1.5 text-[13px] text-fg-muted">
                {unknown.map((k) => (
                  <li key={k}>{QUESTIONS[k] ?? `${LABELS[k]}?`}</li>
                ))}
              </ul>
            </div>
          )}
          {assumed.length > 0 && (
            <div className="mb-4">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-medium text-ql-warning">Assumed, not confirmed · {assumed.length}</p>
                <button type="button" className="text-2xs text-fg-muted hover:text-fg" onClick={confirmAll}>
                  Confirm all
                </button>
              </div>
              <ul className="space-y-1 text-2xs text-fg-muted">
                {assumed.map((k) => (
                  <li key={k} className="flex justify-between gap-2">
                    <span>{LABELS[k]}</span>
                    <span className="truncate font-mono">{values[k] || "—"}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(check?.warnings.length ?? 0) > 0 && (
            <ul className="mb-4 space-y-1.5 text-[13px] text-ql-warning">
              {check?.warnings.map((w) => (
                <li key={w}>! {w}</li>
              ))}
            </ul>
          )}
          {check && !check.valid && (
            <p className="mb-4 text-[13px] text-ql-danger" data-testid="ql-spec-error">
              {check.code === "UNSUPPORTED_INSTRUMENT" ? "Unsupported instrument: " : ""}
              {check.message}
            </p>
          )}
          {check?.valid && check.summary && (
            <div className="mb-4 rounded-xl border border-ql-border bg-ql-raised p-3">
              <p className="mb-1 label">Pre-run summary</p>
              <p className="text-[13px] leading-relaxed text-fg" data-testid="ql-spec-summary">
                {check.summary}
              </p>
              <p className="mt-2 text-2xs text-fg-faint">
                Spec SHA-256 <Hash value={check.spec_sha256} />
              </p>
            </div>
          )}
          <label className="mb-3 flex items-start gap-2 text-[13px] text-fg-muted">
            <input
              type="checkbox"
              className="mt-0.5 accent-[var(--color-ql)]"
              checked={reviewed}
              onChange={(e) => setReviewed(e.target.checked)}
              disabled={!check?.valid || unknown.length > 0}
              data-testid="ql-reviewed"
            />
            I've read the summary; this version is frozen once saved.
          </label>
          {error && <p className="mb-3 text-[13px] text-ql-danger">{error}</p>}
          <div className="flex gap-2">
            <Button variant="primary" disabled={!canSave} onClick={save} data-testid="ql-save-strategy">
              {saving ? "Saving…" : editing ? "Save version" : "Save strategy"}
            </Button>
            <Button onClick={onCancel}>Cancel</Button>
          </div>
          <ul className="mt-4 space-y-1 text-2xs text-fg-faint">
            <li>
              <FieldState state="unknown" /> must be answered
            </li>
            <li>
              <FieldState state="assumed" /> a preset nobody confirmed yet
            </li>
            <li>
              <FieldState state="confirmed" /> set or confirmed by you
            </li>
          </ul>
        </Card>
      </div>
    </div>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset>
      <legend className="mb-2 label">{title}</legend>
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-3">{children}</div>
    </fieldset>
  );
}
