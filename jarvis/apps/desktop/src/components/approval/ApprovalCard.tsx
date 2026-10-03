/**
 * Explicit approval request. Never a generic "Allow?": the user sees exactly
 * what will happen, why, and how risky it is.
 */

import type { PermissionRequest } from "@jarvis/protocol";
import { motion } from "motion/react";
import { useEffect, useState } from "react";

import { api } from "../../lib/api";
import { LEVEL_LABELS, capitalize } from "../../lib/format";
import { useNow } from "../../store/store";
import { Button, cx } from "../ui/primitives";

const CONFIRM_WINDOW_MS = 6000;

export function ApprovalCard({ request }: { request: PermissionRequest }) {
  const [inspect, setInspect] = useState(false);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const now = useNow(1000);
  const strong = request.policy === "strong_confirm";
  const { action } = request;

  useEffect(() => {
    if (!armed) return;
    const timer = window.setTimeout(() => setArmed(false), CONFIRM_WINDOW_MS);
    return () => window.clearTimeout(timer);
  }, [armed]);

  const decide = async (approve: boolean) => {
    if (approve && strong && !armed) {
      setArmed(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await (approve ? api.approve(request.id, strong) : api.reject(request.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The decision could not be sent.");
      setBusy(false);
    }
  };

  const expiresIn = request.expires_at ? Math.max(0, Date.parse(request.expires_at) - now) : null;

  return (
    <motion.section
      initial={{ opacity: 0, y: 10, scale: 0.99 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 6 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className={cx(
        "w-full max-w-[560px] rounded-xl border bg-surface px-5 py-3.5",
        strong ? "border-danger/35" : "border-warning/30",
      )}
      style={{ boxShadow: "0 24px 80px -32px rgb(241 182 92 / 0.25)" }}
      data-testid="approval-card"
      role="alertdialog"
      aria-label={`Approval required: ${action.title}`}
    >
      <header className="flex items-center justify-between">
        <span className={cx("label tracking-[0.24em]", strong ? "text-danger" : "text-warning")}>
          JARVIS request
        </span>
        <span className="font-mono text-2xs text-fg-faint uppercase">
          Level {action.level} · {LEVEL_LABELS[action.level]}
        </span>
      </header>

      <dl className="selectable mt-3.5 grid grid-cols-[92px_minmax(0,1fr)] gap-x-4 gap-y-2 text-[13px]">
        <dt className="text-fg-faint">Action</dt>
        <dd className="text-fg">{action.title}</dd>
        {action.target && (
          <>
            <dt className="text-fg-faint">Target</dt>
            <dd className="font-medium text-fg">{action.target}</dd>
          </>
        )}
        <dt className="text-fg-faint">Summary</dt>
        <dd className="text-fg-muted">{action.summary}</dd>
        {action.effects.length > 0 && (
          <>
            <dt className="text-fg-faint">Effects</dt>
            <dd>
              <ul className="space-y-0.5 text-fg-muted">
                {action.effects.map((effect) => (
                  <li key={effect} className="flex gap-2">
                    <span className="text-fg-faint">–</span>
                    {effect}
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
        <dt className="text-fg-faint">Reason</dt>
        <dd className="text-fg-muted">You asked: “{request.reason}”</dd>
      </dl>

      {inspect && (
        <pre className="selectable mt-4 overflow-x-auto rounded-lg border border-hairline bg-canvas-2 p-3 font-mono text-2xs leading-relaxed text-fg-muted">
          {[
            `request   ${request.id}`,
            `tool      ${request.tool}`,
            `policy    ${request.policy}`,
            `agent     ${request.agent ?? "—"}`,
            `trace     ${request.trace_id ?? "—"}`,
            `mission   ${request.mission_id ?? "—"}`,
            `reversible ${action.reversible ? "yes" : "no"}`,
          ].join("\n")}
        </pre>
      )}

      {error && <p className="mt-3 text-xs text-danger">{error}</p>}

      <footer className="mt-4 flex items-center gap-2">
        <Button
          variant={armed ? "warning" : "primary"}
          disabled={busy}
          onClick={() => decide(true)}
          data-testid="approve"
        >
          {armed ? "Confirm high-risk action" : "Approve"}
        </Button>
        <Button disabled={busy} onClick={() => decide(false)} data-testid="reject">
          Reject
        </Button>
        <Button onClick={() => setInspect((v) => !v)}>{inspect ? "Hide" : "Inspect"}</Button>
        <span className="ml-auto text-right font-mono text-2xs leading-4 text-fg-faint tabular">
          {request.agent && `${capitalize(request.agent)} · `}
          {strong && !armed ? "two-step confirmation" : "confirmation"}
          {expiresIn !== null && (
            <>
              <br />
              expires {formatCountdown(expiresIn)}
            </>
          )}
        </span>
      </footer>
    </motion.section>
  );
}

function formatCountdown(ms: number): string {
  const total = Math.ceil(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
