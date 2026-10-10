import type { QlCheckResult, QlDataStatus, QlRunStatus, QlVerdict } from "@jarvis/protocol";
import type { ReactNode } from "react";

import { cx } from "../ui/primitives";

/** QuantLab building blocks: cards, badges and number formatting (handoff 02_UX). */

export function Card({
  title,
  aside,
  children,
  className,
  testId,
}: {
  title?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section
      className={cx("rounded-2xl border border-ql-border bg-ql-surface p-5", className)}
      data-testid={testId}
    >
      {(title || aside) && (
        <header className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="label">{title}</h2>}
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

type QlTone = "accent" | "positive" | "warning" | "danger" | "muted";

const TONE_TEXT: Record<QlTone, string> = {
  accent: "text-ql",
  positive: "text-ql-positive",
  warning: "text-ql-warning",
  danger: "text-ql-danger",
  muted: "text-ql-muted",
};

export function Badge({ tone, icon, children, title, testId }: {
  tone: QlTone;
  icon?: string;
  children: ReactNode;
  title?: string;
  testId?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-current/25 px-2 py-0.5 font-mono text-2xs font-medium tracking-[0.08em] uppercase",
        TONE_TEXT[tone],
      )}
      title={title}
      data-testid={testId}
    >
      {icon && <span aria-hidden>{icon}</span>}
      {children}
    </span>
  );
}

const VERDICTS: Record<QlVerdict, { tone: QlTone; icon: string; label: string }> = {
  INVALID: { tone: "danger", icon: "⊘", label: "Invalid" },
  FAILED: { tone: "danger", icon: "✕", label: "Failed" },
  INCONCLUSIVE: { tone: "warning", icon: "◐", label: "Inconclusive" },
  PROMISING_RESEARCH_CANDIDATE: { tone: "positive", icon: "◆", label: "Promising candidate" },
};

export function VerdictBadge({ verdict, title }: { verdict: QlVerdict; title?: string }) {
  const v = VERDICTS[verdict];
  return (
    <Badge tone={v.tone} icon={v.icon} title={title} testId="ql-verdict">
      {v.label}
    </Badge>
  );
}

const DATA: Record<QlDataStatus, { tone: QlTone; icon: string }> = {
  ACCEPTED: { tone: "positive", icon: "✓" },
  WARNING: { tone: "warning", icon: "!" },
  REJECTED: { tone: "danger", icon: "✕" },
  UNSUPPORTED: { tone: "danger", icon: "⊘" },
};

export function DataStatus({ status }: { status: QlDataStatus }) {
  return (
    <Badge tone={DATA[status].tone} icon={DATA[status].icon} testId="ql-data-status">
      {status}
    </Badge>
  );
}

const RUN: Record<QlRunStatus, { tone: QlTone; label: string }> = {
  queued: { tone: "muted", label: "Queued" },
  running: { tone: "accent", label: "Running" },
  completed: { tone: "muted", label: "Complete" },
  failed: { tone: "danger", label: "Failed to run" },
  cancelled: { tone: "muted", label: "Cancelled" },
};

export function RunStatus({ status }: { status: QlRunStatus }) {
  return <Badge tone={RUN[status].tone}>{RUN[status].label}</Badge>;
}

const CHECKS: Record<QlCheckResult, { tone: QlTone; icon: string; label: string }> = {
  PASS: { tone: "positive", icon: "✓", label: "Pass" },
  WARN: { tone: "warning", icon: "!", label: "Warn" },
  FAIL: { tone: "danger", icon: "✕", label: "Fail" },
  NOT_RUN: { tone: "muted", icon: "○", label: "Not run" },
  N_A: { tone: "muted", icon: "–", label: "n/a" },
};

export function CheckBadge({ result }: { result: QlCheckResult }) {
  const c = CHECKS[result];
  return (
    <Badge tone={c.tone} icon={c.icon}>
      {c.label}
    </Badge>
  );
}

export function SyntheticBadge() {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-lg border border-ql-warning/60 bg-ql-warning/10 px-2 py-0.5 font-mono text-2xs font-semibold tracking-[0.12em] text-ql-warning uppercase"
      data-testid="ql-synthetic"
      title="Fabricated engineering data. It proves the arithmetic, nothing about a market."
    >
      Synthetic / test only
    </span>
  );
}

export function ResearchOnly() {
  return (
    <Badge tone="accent" title="QuantLab simulates history. It never places orders and has no broker connection.">
      Research only
    </Badge>
  );
}

export function Provisional() {
  return (
    <Badge tone="warning" title="R1 can't validate an edge: walk-forward, cost stress and forward tests are missing.">
      Provisional · not validated
    </Badge>
  );
}

/** A labelled number. `hint` explains why it matters (shown on hover and to screen readers). */
export function Metric({
  label,
  value,
  sub,
  hint,
  testId,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  hint?: string;
  testId?: string;
}) {
  return (
    <div className="min-w-0" data-testid={testId}>
      <dt className="flex items-center gap-1 text-xs text-ql-muted">
        {label}
        {hint && (
          <span className="cursor-help text-fg-faint" title={hint} aria-label={hint}>
            ⓘ
          </span>
        )}
      </dt>
      <dd className="mt-1 truncate text-lg font-medium text-fg">{value}</dd>
      {sub && <dd className="mt-0.5 truncate text-2xs text-fg-faint">{sub}</dd>}
    </div>
  );
}

export function Hash({ value, length = 12 }: { value: string | null | undefined; length?: number }) {
  if (!value) return <span className="text-fg-faint">—</span>;
  return (
    <span className="selectable font-mono text-2xs text-fg-muted" title={value}>
      {value.slice(0, length)}
    </span>
  );
}

// Formatting --------------------------------------------------------------------------

export function money(value: number | null | undefined, currency = "USD", signedValue = true): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  const text = Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = value < 0 ? "−" : signedValue && value > 0 ? "+" : "";
  return `${sign}${text} ${currency}`;
}

export function pct(value: number | null | undefined, digits = 2, signedValue = true): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  const text = `${Math.abs(value * 100).toFixed(digits)}%`;
  const sign = value < 0 ? "−" : signedValue && value > 0 ? "+" : "";
  return `${sign}${text}`;
}

export function num(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

/** UTC timestamps are shown as UTC (the data's truth), compactly. */
export function utc(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso.replace("T", " ").replace(/:00Z$/, "Z").replace(/Z$/, " UTC");
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const minutes = Math.round((Date.now() - Date.parse(iso)) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return new Date(iso).toLocaleDateString("en-GB");
}

export function Field({
  label,
  state,
  children,
  hint,
}: {
  label: string;
  state?: "unknown" | "assumed" | "confirmed";
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="block min-w-0">
      <span className="mb-1 flex items-center justify-between gap-2 text-xs text-ql-muted">
        <span title={hint}>
          {label}
          {hint && <span className="ml-1 cursor-help text-fg-faint">ⓘ</span>}
        </span>
        {state && <FieldState state={state} />}
      </span>
      {children}
    </label>
  );
}

const FIELD_STATES = {
  unknown: { text: "Unknown", cls: "text-ql-danger" },
  assumed: { text: "Assumed", cls: "text-ql-warning" },
  confirmed: { text: "Confirmed", cls: "text-ql-positive" },
};

export function FieldState({ state }: { state: "unknown" | "assumed" | "confirmed" }) {
  const s = FIELD_STATES[state];
  return (
    <span className={cx("font-mono text-[10px] tracking-[0.12em] uppercase", s.cls)} data-state={state}>
      {state === "confirmed" ? "✓ " : state === "unknown" ? "? " : "~ "}
      {s.text}
    </span>
  );
}

export const inputClass =
  "h-8 w-full rounded-lg border border-ql-border bg-ql-raised px-2.5 text-[13px] text-fg outline-none placeholder:text-fg-faint focus:border-ql/60";

/** The same control without the full width (for inline selects and small inputs). */
export const compactInputClass = inputClass.replace("w-full ", "");
