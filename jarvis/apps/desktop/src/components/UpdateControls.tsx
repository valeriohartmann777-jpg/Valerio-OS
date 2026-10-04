import { useState } from "react";

import { clock } from "../lib/format";
import { type UpdateStatus, updates } from "../lib/updates";
import { dispatch, useJarvis } from "../store/store";
import { Button, StatusDot, cx } from "./ui/primitives";

async function run(action: "check" | "apply"): Promise<void> {
  if (!updates) return;
  const status = await (action === "check" ? updates.check() : updates.apply());
  dispatch({ type: "updates", status });
}

const pill =
  "no-drag inline-flex items-center gap-2 rounded-md border px-2 py-0.5 font-mono text-2xs tracking-wider uppercase";

/** Top bar: appears only when there is something to do or to watch. */
export function UpdateButton() {
  const status = useJarvis((s) => s.updates);
  const [open, setOpen] = useState(false);
  if (!status) return null;

  if (status.state === "updating" || status.state === "restarting") {
    return (
      <span className={cx(pill, "border-accent/30 text-accent")} data-testid="update-progress">
        <StatusDot tone="accent" live />
        {status.state === "updating" ? `Updating · ${status.step}` : "Restarting"}
      </span>
    );
  }
  if (status.state !== "available" && status.state !== "error") return null;
  const failed = status.state === "error";

  return (
    <div className="relative">
      <button
        type="button"
        className={cx(pill, failed ? "border-warning/30 text-warning" : "border-accent/30 text-accent")}
        onClick={() => setOpen((value) => !value)}
        data-testid="update-button"
      >
        <StatusDot tone={failed ? "warning" : "accent"} />
        {failed ? "Update problem" : "Update"}
      </button>
      {open && (
        <>
          <div className="no-drag fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className="no-drag absolute top-8 right-0 z-50 w-80 rounded-xl border border-hairline-strong bg-surface-2 p-4 shadow-2xl"
            data-testid="update-panel"
          >
            <UpdateDetails status={status} onDone={() => setOpen(false)} />
          </div>
        </>
      )}
    </div>
  );
}

function UpdateDetails({ status, onDone }: { status: UpdateStatus; onDone: () => void }) {
  if (status.state === "available") {
    return (
      <div className="space-y-3">
        <div>
          <p className="text-[13px] text-fg">A new version of JARVIS is ready</p>
          <p className="mt-1 line-clamp-2 text-[13px] text-fg-muted">{status.latest}</p>
        </div>
        <p className="text-xs text-fg-faint">
          {status.behind} change{status.behind === 1 ? "" : "s"} · JARVIS restarts when it's done (about a minute).
        </p>
        <div className="flex gap-2">
          <Button
            variant="primary"
            onClick={() => {
              onDone();
              void run("apply");
            }}
            data-testid="update-now"
          >
            Update now
          </Button>
          <Button onClick={onDone}>Later</Button>
        </div>
      </div>
    );
  }
  if (status.state === "error") {
    return (
      <div className="space-y-3">
        <p className="text-[13px] text-warning">{status.message}</p>
        {status.detail && (
          <pre className="selectable max-h-32 overflow-auto rounded-md bg-canvas p-2 font-mono text-2xs whitespace-pre-wrap text-fg-faint">
            {status.detail}
          </pre>
        )}
        <div className="flex gap-2">
          {status.canRetry && (
            <Button
              variant="primary"
              onClick={() => {
                onDone();
                void run("check");
              }}
            >
              Try again
            </Button>
          )}
          <Button onClick={onDone}>Close</Button>
        </div>
      </div>
    );
  }
  return null;
}

/** Settings → System → Updates. */
export function UpdateSummary() {
  const status = useJarvis((s) => s.updates);
  if (!status) return <span className="text-fg-muted">Only in the JARVIS app</span>;

  switch (status.state) {
    case "off":
      return <span className="text-fg-muted">{status.reason}</span>;
    case "checking":
      return <span className="text-fg-muted">Checking…</span>;
    case "current":
      return (
        <span className="flex items-center gap-3" data-testid="update-summary">
          <span className="text-fg-muted">Up to date · checked {clock(status.checkedAt)}</span>
          <InlineAction onClick={() => void run("check")}>Check now</InlineAction>
        </span>
      );
    case "available":
      return (
        <span className="flex items-center gap-3" data-testid="update-summary">
          <span className="truncate text-accent">New version: {status.latest}</span>
          <InlineAction onClick={() => void run("apply")}>Update now</InlineAction>
        </span>
      );
    case "updating":
      return <span className="text-accent">Updating · {status.step}…</span>;
    case "restarting":
      return <span className="text-accent">Restarting…</span>;
    case "error":
      return (
        <span className="flex items-center gap-3">
          <span className="truncate text-warning" title={status.detail}>
            {status.message}
          </span>
          {status.canRetry && <InlineAction onClick={() => void run("check")}>Try again</InlineAction>}
        </span>
      );
  }
}

function InlineAction({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" className="no-drag shrink-0 text-xs text-fg-faint hover:text-fg" onClick={onClick}>
      {children}
    </button>
  );
}
