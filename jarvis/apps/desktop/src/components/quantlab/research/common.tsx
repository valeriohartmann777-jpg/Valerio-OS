import type { QrFitness, QrRunRow, QrRunStatus, QrTestStatus, QrVerdict } from "@jarvis/protocol";
import type { ReactNode } from "react";

import { useJarvis } from "../../../store/store";
import { cx } from "../../ui/primitives";
import { Badge, compactInputClass } from "../ui";

/** Shared QuantLab research pieces: status badges (icon + label, never colour alone) and formatting. */

export type Mode = "Simple" | "Research" | "Institutional";

/** The newest quantlab.* event id: a change means "refetch". */
export function useQuantLabTick(): string | null {
  return useJarvis((s) => {
    for (let i = s.activity.length - 1; i >= 0; i--) {
      const event = s.activity[i];
      if (event?.type.startsWith("quantlab.")) return event.id;
    }
    return null;
  });
}

const VERDICT: Record<QrVerdict, { tone: "danger" | "warning" | "positive" | "accent" | "muted"; icon: string; label: string }> = {
  INVALID_DATA_OR_METHOD: { tone: "danger", icon: "⊘", label: "Invalid data or method" },
  INSUFFICIENT_EVIDENCE: { tone: "warning", icon: "◐", label: "Insufficient evidence" },
  REJECTED_HYPOTHESIS: { tone: "danger", icon: "✕", label: "Rejected hypothesis" },
  PROMISING_RESEARCH_CANDIDATE: { tone: "positive", icon: "◆", label: "Promising research candidate" },
  FORWARD_VALIDATION_REQUIRED: { tone: "accent", icon: "▲", label: "Forward validation required" },
  ROBUST_UNDER_TESTED_ASSUMPTIONS: { tone: "positive", icon: "✓", label: "Robust under tested assumptions" },
};

export function VerdictTag({ verdict, testId = "qr-verdict" }: { verdict: QrVerdict | null; testId?: string }) {
  if (!verdict) return <Badge tone="muted">No verdict · backtest</Badge>;
  const v = VERDICT[verdict];
  return (
    <Badge tone={v.tone} icon={v.icon} testId={testId}>
      {v.label}
    </Badge>
  );
}

export function verdictLabel(verdict: QrVerdict): string {
  return VERDICT[verdict].label;
}

const TEST: Record<QrTestStatus, { tone: "danger" | "warning" | "positive" | "muted"; icon: string; label: string }> = {
  PASSED: { tone: "positive", icon: "✓", label: "Passed" },
  WARNING: { tone: "warning", icon: "!", label: "Warning" },
  FAILED: { tone: "danger", icon: "✕", label: "Failed" },
  INCONCLUSIVE: { tone: "warning", icon: "◐", label: "Inconclusive" },
  NOT_APPLICABLE: { tone: "muted", icon: "–", label: "Not applicable" },
  NOT_RUN: { tone: "muted", icon: "○", label: "Not run" },
};

export function TestBadge({ status }: { status: QrTestStatus }) {
  const t = TEST[status];
  return (
    <Badge tone={t.tone} icon={t.icon}>
      {t.label}
    </Badge>
  );
}

const FITNESS: Record<QrFitness, { tone: "danger" | "warning" | "positive"; icon: string; label: string }> = {
  FIT: { tone: "positive", icon: "✓", label: "Fit" },
  FIT_WITH_LIMITATIONS: { tone: "warning", icon: "!", label: "Fit with limitations" },
  INSUFFICIENT: { tone: "warning", icon: "◐", label: "Insufficient" },
  INVALID: { tone: "danger", icon: "⊘", label: "Invalid" },
};

export function FitnessBadge({ status }: { status: QrFitness }) {
  const f = FITNESS[status];
  return (
    <Badge tone={f.tone} icon={f.icon} testId="qr-fitness">
      {f.label}
    </Badge>
  );
}

const RUN: Record<QrRunStatus, { tone: "accent" | "muted" | "danger" | "warning"; label: string }> = {
  QUEUED: { tone: "muted", label: "Queued" },
  RUNNING: { tone: "accent", label: "Running" },
  COMPLETED: { tone: "muted", label: "Complete" },
  FAILED: { tone: "danger", label: "Failed" },
  CANCELED: { tone: "muted", label: "Cancelled" },
  INTERRUPTED: { tone: "warning", label: "Interrupted" },
};

export function RunBadge({ status }: { status: QrRunStatus }) {
  return <Badge tone={RUN[status].tone}>{RUN[status].label}</Badge>;
}

export function FixtureBadge({ label = "Fixture · synthetic" }: { label?: string }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-ql-warning/60 bg-ql-warning/10 px-2 py-0.5 font-mono text-2xs font-semibold tracking-[0.12em] text-ql-warning uppercase"
      title="Offline fixture provider: synthetic prices in Databento's format. Engineering checks only — no market evidence."
      data-testid="qr-fixture"
    >
      {label}
    </span>
  );
}

// Formatting ---------------------------------------------------------------------------------

export function usd(value: number | null | undefined, signed = false, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  const text = Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const sign = value < 0 ? "−" : signed && value > 0 ? "+" : "";
  return `${sign}$${text}`;
}

export function fmt(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  const text = Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: digits });
  return value < 0 ? `−${text}` : text;
}

export function share(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  return `${(value * 100).toFixed(digits)}%`;
}

export function price(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function hhmm(iso: string | null | undefined): string {
  return iso ? iso.slice(11, 16) : "—";
}

export function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return "text-fg";
  return value > 0 ? "text-ql-positive" : "text-ql-danger";
}

export function bytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} KB`;
  return `${n} B`;
}

// Pieces -------------------------------------------------------------------------------------

export function RunPicker({
  runs,
  value,
  onChange,
  only,
}: {
  runs: QrRunRow[];
  value: string | null;
  onChange: (id: string) => void;
  only?: "validation";
}) {
  const choices = runs.filter((r) => r.status === "COMPLETED" && (!only || r.kind === only));
  if (!choices.length) return null;
  return (
    <select
      className={cx(compactInputClass, "max-w-[520px]")}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      aria-label="Run"
      data-testid="qr-run-picker"
    >
      {!value && <option value="">Choose a run…</option>}
      {choices.map((r) => (
        <option key={r.id} value={r.id}>
          {r.name} v{r.version_number} · {r.kind} · {r.symbol}
          {r.include_holdout ? " · +holdout" : ""}
          {r.fixture ? " · FIXTURE" : ""} · {r.finished_at?.slice(0, 16).replace("T", " ")}
        </option>
      ))}
    </select>
  );
}

export function Empty({ title, children, testId }: { title: string; children?: ReactNode; testId?: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-ql-border px-6 py-10 text-center" data-testid={testId}>
      <p className="text-[15px] text-fg">{title}</p>
      {children && <div className="mx-auto mt-2 max-w-[560px] text-[13px] text-fg-muted">{children}</div>}
    </div>
  );
}

export function Kv({ label, children, mono = false }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[150px_minmax(0,1fr)] gap-3 py-1 text-[13px]">
      <dt className="text-fg-faint">{label}</dt>
      <dd className={cx("min-w-0 break-words text-fg", mono && "selectable font-mono text-2xs text-fg-muted")}>{children}</dd>
    </div>
  );
}

export function Progress({ value, label }: { value: number; label?: string }) {
  const pct = Math.max(0, Math.min(1, value));
  return (
    <div className="w-full" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct * 100)} aria-label={label}>
      <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
        <div className="h-full rounded-full bg-ql transition-[width] duration-300" style={{ width: `${pct * 100}%` }} />
      </div>
      {label && <p className="mt-1 text-2xs text-fg-faint">{label}</p>}
    </div>
  );
}

/** A tiny, safe markdown renderer for JARVIS's own reports (headings, lists, tables, bold, code). */
export function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  const inline = (s: string, key: string) => {
    const parts = s.split(/(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_)/g);
    return (
      <span key={key}>
        {parts.map((p, k) =>
          p.startsWith("**") ? (
            <strong key={k} className="font-semibold text-fg">{p.slice(2, -2)}</strong>
          ) : p.startsWith("`") ? (
            <code key={k} className="selectable font-mono text-2xs text-fg-muted">{p.slice(1, -1)}</code>
          ) : p.startsWith("_") && p.endsWith("_") && p.length > 2 ? (
            <em key={k}>{p.slice(1, -1)}</em>
          ) : (
            p
          ),
        )}
      </span>
    );
  };
  while (i < lines.length) {
    const line = lines[i] ?? "";
    if (line.startsWith("# ")) out.push(<h2 key={i} className="mt-2 text-lg font-light text-fg">{line.slice(2)}</h2>);
    else if (line.startsWith("## ")) out.push(<h3 key={i} className="label mt-5">{line.slice(3)}</h3>);
    else if (line.startsWith("> ")) out.push(<p key={i} className="mt-2 rounded-lg border border-ql-warning/40 bg-ql-warning/5 px-3 py-2 text-[13px] text-ql-warning">{inline(line.slice(2), `q${i}`)}</p>);
    else if (line.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && (lines[i] ?? "").startsWith("|")) {
        const cells = (lines[i] ?? "").split("|").slice(1, -1).map((c) => c.trim());
        if (!cells.every((c) => /^-+$/.test(c))) rows.push(cells);
        i++;
      }
      out.push(
        <table key={`t${i}`} className="mt-2 w-full text-left text-[12px]">
          <tbody>
            {rows.map((r, k) => (
              <tr key={k} className={cx("border-t border-ql-border/60", k === 0 && "text-fg-faint")}>
                {r.map((c, j) => (
                  <td key={j} className="px-2 py-1 align-top">{inline(c, `c${k}${j}`)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      );
      continue;
    } else if (line.startsWith("- ") || line.startsWith("  - ")) {
      const nested = line.startsWith("  - ");
      out.push(
        <p key={i} className={cx("mt-1 text-[13px] text-fg-muted", nested ? "pl-8" : "pl-4")}>
          <span className="mr-2 text-fg-faint">•</span>
          {inline(line.replace(/^\s*- /, ""), `l${i}`)}
        </p>,
      );
    } else if (line.trim()) out.push(<p key={i} className="mt-2 text-[13px] text-fg-muted">{inline(line, `p${i}`)}</p>);
    i++;
  }
  return <div className="selectable">{out}</div>;
}

export function download(name: string, text: string, type = "text/markdown"): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
