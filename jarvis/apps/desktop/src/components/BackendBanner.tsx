import { useEffect, useState } from "react";

import { checkReadiness, supervisor, type SupervisorStatus, useReadiness, useSupervisor } from "../lib/backend";
import { BACKEND_URL } from "../lib/config";
import { useJarvis, useNow } from "../store/store";
import { Icon } from "./ui/icons";
import { Button, cx } from "./ui/primitives";

/**
 * Says plainly when the backend isn't there or isn't well, why, and what fixes it:
 * Retry, Restart backend, Repair (reinstall its packages), and the diagnostics — what the
 * backend printed, its last exit, the log file. Shown only when something is wrong (a calm
 * "starting" note after a few seconds during a normal start).
 */
export function BackendBanner() {
  const connection = useJarvis((s) => s.connection);
  const ready = useReadiness();
  const sup = useSupervisor();
  const now = useNow(1000);
  const [since] = useState(() => Date.now());
  const [details, setDetails] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  // A normal start takes a few seconds: no alarm before that.
  const grace = now - since < 4000;
  const supBad = sup && ["crashed", "unavailable", "restarting", "installing"].includes(sup.state);
  const degraded = connection === "online" && ready.status === "degraded";
  const unreachable = connection === "online" && ready.status === "unreachable";
  const down = connection !== "online" && !grace;
  useEffect(() => {
    if (!down && !supBad) setDetails(false);
  }, [down, supBad]);
  if (!down && !supBad && !degraded && !unreachable) return null;

  const run = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label);
    try {
      await action();
      await checkReadiness();
    } finally {
      setBusy(null);
    }
  };

  const state = degraded ? "degraded" : (sup?.state ?? (connection === "offline" ? "offline" : "connecting"));
  const tone = degraded || sup?.state === "installing" || sup?.state === "starting" ? "warning" : "danger";
  const { title, text } = describe(sup, connection, degraded, ready.checks, now);
  const actions = sup?.diagnosis?.actions ?? [];

  return (
    <div
      role="alert"
      className={cx(
        "border-b px-6 py-3 text-[13px]",
        tone === "danger" ? "border-danger/40 bg-danger/10" : "border-warning/40 bg-warning/10",
      )}
      data-testid="backend-banner"
      data-state={state}
    >
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-start gap-3">
        <Icon name="alert" size={18} className={cx("mt-0.5 shrink-0", tone === "danger" ? "text-danger" : "text-warning")} />
        <div className="min-w-0 flex-1">
          <p className="font-bold text-fg" data-testid="backend-banner-title">
            {title}
          </p>
          <p className="mt-0.5 text-fg-muted">{text}</p>
          {sup?.diagnosis && (
            <p className="mt-1 text-fg-muted" data-testid="backend-diagnosis" data-code={sup.diagnosis.code}>
              <b className="text-fg">{sup.diagnosis.detail}</b> {sup.diagnosis.remedy}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => void run("retry", checkReadiness)} disabled={busy !== null} data-testid="backend-retry">
            {busy === "retry" ? "Checking…" : "Retry"}
          </Button>
          {supervisor && (
            <Button
              onClick={() => void run("restart", () => supervisor!.restart())}
              disabled={busy !== null || sup?.state === "installing"}
              reason={sup?.state === "installing" ? "Packages are being installed." : null}
              data-testid="backend-restart"
            >
              {busy === "restart" ? "Restarting…" : "Restart backend"}
            </Button>
          )}
          {supervisor && actions.includes("repair") && (
            <Button variant="primary" onClick={() => void run("repair", () => supervisor!.repair())} disabled={busy !== null} data-testid="backend-repair">
              {busy === "repair" ? "Repairing…" : "Repair"}
            </Button>
          )}
          <Button onClick={() => setDetails((d) => !d)} aria-expanded={details} data-testid="backend-diagnostics">
            {details ? "Hide diagnostics" : "View diagnostics"}
          </Button>
        </div>
      </div>
      {details && <Diagnostics sup={sup} checks={ready.checks} />}
    </div>
  );
}

function describe(
  sup: SupervisorStatus | null,
  connection: string,
  degraded: boolean,
  checks: { name: string; ok: boolean; detail: string }[],
  now: number,
): { title: string; text: string } {
  if (degraded) {
    const failed = checks.filter((c) => !c.ok);
    return {
      title: "The backend answers, but not everything works",
      text: failed.map((c) => `${c.name}: ${c.detail}`).join(" · ") || "A readiness check failed.",
    };
  }
  if (connection === "online") {
    return { title: "The backend didn't answer its readiness check", text: `Asked ${BACKEND_URL}/health/ready — no answer.` };
  }
  switch (sup?.state) {
    case "installing":
      return { title: "Installing the backend's Python packages…", text: "After an update, new packages are installed once. This can take a minute." };
    case "starting":
      return { title: "Starting the backend…", text: `Waiting for it to answer on ${sup.url}.` };
    case "restarting": {
      const wait = sup.nextRetryAt ? Math.max(0, Math.ceil((sup.nextRetryAt - now) / 1000)) : 0;
      return {
        title: `The backend stopped — starting it again in ${wait} s (attempt ${sup.attempts + 1})`,
        text: sup.diagnosis?.title ?? "It exited unexpectedly.",
      };
    }
    case "crashed":
      return {
        title: sup.diagnosis?.title ?? "The backend keeps stopping",
        text: `It stopped ${sup.attempts} times in a row right after starting, so JARVIS stopped restarting it.`,
      };
    case "unavailable":
      return { title: sup.diagnosis?.title ?? "The backend can't be started", text: "" };
    case "external":
      return {
        title: "The backend isn't answering",
        text: `JARVIS uses a backend it didn't start (${sup.url}); it doesn't answer right now.`,
      };
    default:
      return {
        title: "The backend isn't reachable",
        text: supervisor
          ? `No answer from ${BACKEND_URL} yet.`
          : `No answer from ${BACKEND_URL}. Start JARVIS with the app or \`npm run dev\` — the backend starts with it.`,
      };
  }
}

function Diagnostics({ sup, checks }: { sup: SupervisorStatus | null; checks: { name: string; ok: boolean; detail: string }[] }) {
  return (
    <div className="mx-auto mt-3 max-w-[1400px] rounded-xl border border-hairline-strong bg-surface/90 p-3 text-[12px]" data-testid="backend-details">
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-[160px_minmax(0,1fr)]">
        <dt className="text-fg-faint">Backend URL</dt>
        <dd className="font-mono text-fg">{sup?.url ?? BACKEND_URL}</dd>
        {sup && (
          <>
            <dt className="text-fg-faint">Supervisor</dt>
            <dd className="font-mono text-fg">
              {sup.state}
              {sup.pid ? ` · pid ${sup.pid}` : ""} · build {sup.build.slice(0, 7)}
            </dd>
            <dt className="text-fg-faint">Python</dt>
            <dd className="font-mono break-all text-fg">{sup.python ?? "not found"}</dd>
            {sup.lastExit && (
              <>
                <dt className="text-fg-faint">Last exit</dt>
                <dd className="font-mono text-fg">
                  code {sup.lastExit.code ?? sup.lastExit.signal ?? "?"} after {(sup.lastExit.uptimeMs / 1000).toFixed(1)} s ·{" "}
                  {new Date(sup.lastExit.at).toLocaleTimeString()}
                </dd>
              </>
            )}
            {sup.logFile && (
              <>
                <dt className="text-fg-faint">Log file</dt>
                <dd className="font-mono break-all text-fg">{sup.logFile}</dd>
              </>
            )}
          </>
        )}
        {checks.length > 0 && (
          <>
            <dt className="text-fg-faint">Readiness</dt>
            <dd className="text-fg">
              {checks.map((c) => (
                <span key={c.name} className={cx("mr-3", c.ok ? "text-success" : "text-danger")}>
                  {c.ok ? "✓" : "✗"} {c.name}
                </span>
              ))}
            </dd>
          </>
        )}
      </dl>
      {sup && sup.log.length > 0 && (
        <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-canvas-2 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-fg-muted" data-testid="backend-log">
          {sup.log.slice(-40).join("\n")}
        </pre>
      )}
      {!sup && (
        <p className="mt-2 text-fg-muted">
          This window runs outside the JARVIS app, so it can't see the backend process. Start JARVIS with the app or{" "}
          <code>npm run dev</code>.
        </p>
      )}
    </div>
  );
}
