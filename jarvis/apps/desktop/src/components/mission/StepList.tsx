import type { MissionStep } from "@jarvis/protocol";
import { motion } from "motion/react";

import { stepStatusLabel } from "../../lib/format";
import { StatusDot, cx, textTone } from "../ui/primitives";
import { STEP_TONE } from "../ui/status";

export function StepList({ steps, detailed = false }: { steps: MissionStep[]; detailed?: boolean }) {
  return (
    <ol className="space-y-0.5">
      {steps.map((step) => (
        <StepRow key={step.id} step={step} detailed={detailed} />
      ))}
    </ol>
  );
}

function StepRow({ step, detailed }: { step: MissionStep; detailed: boolean }) {
  const tone = STEP_TONE[step.status];
  const pending = step.status === "waiting" || step.status === "skipped";
  return (
    <motion.li
      layout="position"
      className="grid grid-cols-[28px_minmax(0,1fr)_auto] items-start gap-3 rounded-md py-1.5"
      data-testid="mission-step"
      data-status={step.status}
    >
      <span className="pt-px font-mono text-xs text-fg-faint tabular">
        {String(step.index + 1).padStart(2, "0")}
      </span>
      <div className="min-w-0">
        <div className={cx("truncate text-[13px]", pending ? "text-fg-faint" : "text-fg")}>{step.title}</div>
        {step.summary && step.status !== "waiting" && (
          <div className="mt-0.5 truncate text-xs text-fg-faint">{step.summary}</div>
        )}
        {detailed && <StepDetail step={step} />}
      </div>
      <span className={cx("flex items-center gap-2 pt-px text-2xs font-medium tracking-wide uppercase", textTone(tone))}>
        <StatusDot tone={tone} live={step.status === "active" || step.status === "waiting_for_approval"} />
        {stepStatusLabel(step.status)}
      </span>
    </motion.li>
  );
}

function StepDetail({ step }: { step: MissionStep }) {
  const observations = step.result?.observations ?? [];
  const evidence = step.verification?.evidence ?? [];
  return (
    <div className="selectable mt-2 space-y-2 border-l border-hairline-strong pl-3 text-xs text-fg-muted">
      <div className="font-mono text-fg-faint">
        {step.agent}
        {step.tool && ` · ${step.tool}(${JSON.stringify(step.args)})`}
        {step.result && ` · ${step.result.duration_ms} ms`}
        {step.result?.simulated && " · simulated"}
      </div>
      {observations.length > 0 && (
        <ul className="space-y-0.5">
          {observations.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      {step.verification && (
        <div>
          <span className={textTone(step.verification.status === "verified" ? "success" : "warning")}>
            {step.verification.status}
          </span>
          {` · ${step.verification.method}`}
          {evidence.length > 0 && <span className="text-fg-faint"> · {evidence.join(" · ")}</span>}
        </div>
      )}
      {step.error && (
        <div className="text-danger">
          {step.error.message}
          {step.error.suggestion && <span className="text-fg-muted"> — {step.error.suggestion}</span>}
        </div>
      )}
    </div>
  );
}
