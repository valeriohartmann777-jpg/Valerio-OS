import type { UlArtifact, UlArtifactContent, UlMissionState, UlTaskState } from "@jarvis/protocol";
import { type ReactNode, useEffect, useState } from "react";

import { api } from "../../lib/api";
import { Badge } from "../quantlab/ui";
import { cx } from "../ui/primitives";

/** ULTRON building blocks. Every state shown here comes from the backend. */

type Tone = "accent" | "positive" | "warning" | "danger" | "muted";

const MISSION: Record<UlMissionState, { tone: Tone; icon: string; label: string }> = {
  PLANNING: { tone: "accent", icon: "◌", label: "Planning" },
  RUNNING: { tone: "accent", icon: "●", label: "Running" },
  PAUSED: { tone: "warning", icon: "❚❚", label: "Paused" },
  WAITING_APPROVAL: { tone: "warning", icon: "!", label: "Needs approval" },
  BLOCKED: { tone: "danger", icon: "■", label: "Blocked" },
  COMPLETE: { tone: "positive", icon: "✓", label: "Complete" },
  FAILED: { tone: "danger", icon: "✕", label: "Failed" },
  CANCELLED: { tone: "muted", icon: "○", label: "Cancelled" },
};

export function MissionState({ state }: { state: UlMissionState }) {
  const s = MISSION[state];
  return (
    <Badge tone={s.tone} icon={s.icon} testId="ul-mission-state">
      {s.label}
    </Badge>
  );
}

export const TASK: Record<UlTaskState, { tone: Tone; icon: string; label: string }> = {
  DRAFT: { tone: "muted", icon: "○", label: "Waiting" },
  READY: { tone: "muted", icon: "◎", label: "Ready" },
  RUNNING: { tone: "accent", icon: "●", label: "Working" },
  VERIFYING: { tone: "accent", icon: "◐", label: "Verifying" },
  RETRYING: { tone: "warning", icon: "↻", label: "Retrying" },
  WAITING_APPROVAL: { tone: "warning", icon: "!", label: "Approval" },
  COMPLETE: { tone: "positive", icon: "✓", label: "Complete" },
  BLOCKED: { tone: "danger", icon: "■", label: "Blocked" },
  FAILED: { tone: "danger", icon: "✕", label: "Failed" },
  CANCELLED: { tone: "muted", icon: "–", label: "Cancelled" },
};

export function TaskState({ state }: { state: UlTaskState }) {
  const s = TASK[state];
  return (
    <Badge tone={s.tone} icon={s.icon}>
      {s.label}
    </Badge>
  );
}

export const AGENT_COLOR: Record<string, string> = {
  jarvis: "text-fg",
  axiom: "text-[#9db7ff]",
  forge: "text-ql",
  sentinel: "text-[#d6b4ff]",
};

export function AgentName({ id, className }: { id: string | null | undefined; className?: string }) {
  if (!id) return <span className="text-fg-faint">—</span>;
  return (
    <span className={cx("font-mono text-[11px] font-semibold tracking-[0.12em]", AGENT_COLOR[id] ?? "text-fg-muted", className)}>
      {id.toUpperCase()}
    </span>
  );
}

export function usd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `$${value.toFixed(value !== 0 && value < 0.01 ? 4 : digits)}`;
}

export function time(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-GB", { hour12: false });
}

/** A real fraction (both numbers from the backend), never an estimate. */
export function Fraction({ done, total, label }: { done: number; total: number; label: string }) {
  const share = total > 0 ? done / total : 0;
  return (
    <div className="min-w-0" title={`${done} of ${total} ${label}`}>
      <div className="flex justify-between text-2xs text-fg-faint">
        <span>{label}</span>
        <span className="font-mono tabular">
          {done}/{total}
        </span>
      </div>
      <div className="mt-1 h-[3px] overflow-hidden rounded-full bg-white/[0.06]">
        <div className="h-full rounded-full bg-ql transition-[width] duration-500" style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

export function Section({ title, aside, children, testId }: { title: ReactNode; aside?: ReactNode; children: ReactNode; testId?: string }) {
  return (
    <section className="rounded-2xl border border-ql-border bg-ql-surface p-5" data-testid={testId}>
      <header className="mb-3 flex items-center justify-between gap-3">
        <h2 className="label">{title}</h2>
        {aside}
      </header>
      {children}
    </section>
  );
}

/** Shows an artifact's stored content; the backend re-checks its SHA-256 on every read. */
export function ArtifactViewer({ artifact, onClose }: { artifact: UlArtifact; onClose: () => void }) {
  const [data, setData] = useState<UlArtifactContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.ulArtifact(artifact.id).then(setData, () => setError("Couldn't load the artifact."));
  }, [artifact.id]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-8" onClick={onClose} role="presentation">
      <div
        className="flex max-h-full w-full max-w-[980px] flex-col rounded-2xl border border-ql-border bg-ql-surface"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={artifact.name}
        data-testid="ul-artifact-viewer"
      >
        <header className="flex items-center gap-3 border-b border-ql-border px-5 py-3">
          <AgentName id={artifact.agent} />
          <span className="truncate text-[13px] text-fg">{artifact.name}</span>
          <span className="font-mono text-2xs text-fg-faint">{artifact.kind}</span>
          {data && (
            <span className={cx("font-mono text-2xs", data.intact ? "text-ql-positive" : "text-ql-danger")} title={artifact.sha256}>
              {data.intact ? "✓ sha256 intact" : "✕ checksum mismatch"}
            </span>
          )}
          <button type="button" onClick={onClose} className="ml-auto text-[13px] text-fg-muted hover:text-fg">
            Close
          </button>
        </header>
        <div className="min-h-0 overflow-auto p-5">
          {error && <p className="text-[13px] text-ql-danger">{error}</p>}
          {data && <Content kind={artifact.kind} name={artifact.name} text={data.content} />}
        </div>
      </div>
    </div>
  );
}

function Content({ kind, name, text }: { kind: string; name: string; text: string }) {
  if (kind === "patch" || name.endsWith(".patch")) {
    return (
      <pre className="selectable font-mono text-[11.5px] leading-relaxed">
        {text.split("\n").map((line, i) => (
          <div
            key={i}
            className={cx(
              line.startsWith("+") && !line.startsWith("+++") && "text-ql-positive",
              line.startsWith("-") && !line.startsWith("---") && "text-ql-danger",
              line.startsWith("@@") && "text-ql",
              line.startsWith("diff ") && "mt-2 text-fg",
              !/^[-+@]|^diff /.test(line) && "text-fg-muted",
            )}
          >
            {line || " "}
          </div>
        ))}
      </pre>
    );
  }
  let shown = text;
  if (name.endsWith(".json")) {
    try {
      shown = JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      /* show as is */
    }
  }
  return <pre className="selectable whitespace-pre-wrap font-mono text-[11.5px] leading-relaxed text-fg-muted">{shown}</pre>;
}
