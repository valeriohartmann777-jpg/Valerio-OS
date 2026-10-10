import { HAS_TITLEBAR_OVERLAY, HAS_TRAFFIC_LIGHTS } from "../lib/config";
import { clock } from "../lib/format";
import type { View } from "../store/reducer";
import { dispatch, useJarvis, useNow } from "../store/store";
import { ROUTE_VIEW, useAiStatus } from "./settings/AiBilling";
import { UpdateButton } from "./UpdateControls";
import { StatusDot, cx, textTone } from "./ui/primitives";

const CONNECTION = {
  online: { text: "System online", tone: "success" as const },
  connecting: { text: "Connecting", tone: "warning" as const },
  offline: { text: "Offline", tone: "danger" as const },
};

export function TopBar() {
  const connection = useJarvis((s) => s.connection);
  const simulated = useJarvis((s) => s.simulated);
  const version = useJarvis((s) => s.version);
  const build = useJarvis((s) => s.build);
  const view = useJarvis((s) => s.view);
  const now = useNow(1000);
  const status = CONNECTION[connection];

  return (
    <header
      className={cx(
        "drag flex h-11 shrink-0 items-center gap-8 border-b border-hairline px-5",
        HAS_TITLEBAR_OVERLAY && "pr-[150px]",
        HAS_TRAFFIC_LIGHTS && "pl-[88px]",
      )}
    >
      <div className="flex items-baseline gap-2.5">
        <span className="text-[13px] font-semibold tracking-[0.34em] text-fg">JARVIS</span>
        {version && (
          <span className="font-mono text-2xs text-fg-faint" data-testid="build" title={build ?? undefined}>
            v{version}
            {build && build !== "unknown" && ` · ${build.slice(0, 7)}`}
          </span>
        )}
      </div>

      <nav className="no-drag flex items-center gap-1" aria-label="Main">
        <NavItem active={view.name === "home"} view={{ name: "home" }}>
          Home
        </NavItem>
        {view.name === "mission" && (
          <NavItem active view={view}>
            Mission
          </NavItem>
        )}
        <NavItem active={view.name === "learning"} view={{ name: "learning" }}>
          Learning
        </NavItem>
        <NavItem active={view.name === "bots"} view={{ name: "bots" }}>
          Bots
        </NavItem>
        <NavItem active={view.name === "quantlab"} view={{ name: "quantlab" }}>
          QuantLab
        </NavItem>
        <NavItem active={view.name === "ultron"} view={{ name: "ultron" }}>
          ULTRON
        </NavItem>
        <NavItem active={view.name === "settings"} view={{ name: "settings" }}>
          Settings
        </NavItem>
      </nav>

      <div className="ml-auto flex items-center gap-5">
        <UpdateButton />
        <AiRouteChip />
        {simulated && (
          <span
            className="rounded-md border border-warning/30 px-2 py-0.5 font-mono text-2xs tracking-wider text-warning uppercase"
            title="No real system control on this machine — nothing is actually launched. On macOS or Windows: re-run the setup script and restart JARVIS."
            data-testid="simulated-badge"
          >
            Simulated
          </span>
        )}
        <span className="flex items-center gap-2 text-2xs font-medium tracking-[0.16em] text-fg-muted uppercase">
          <StatusDot tone={status.tone} live={connection !== "online"} />
          <span data-testid="connection-status">{status.text}</span>
        </span>
        <span className="font-mono text-xs text-fg-faint tabular">{clock(now)}</span>
      </div>
    </header>
  );
}

function NavItem({ active, view, children }: { active: boolean; view: View; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={() => dispatch({ type: "navigate", view })}
      className={cx(
        "rounded-md px-2.5 py-1 text-[13px] transition-colors",
        active ? "text-fg" : "text-fg-faint hover:text-fg-muted",
      )}
    >
      {children}
    </button>
  );
}

/** Which route AI work takes right now (Claude plan / API / local / paused) — opens AI & Billing. */
function AiRouteChip() {
  const [status] = useAiStatus();
  if (!status) return null;
  const view = ROUTE_VIEW[status.current.route];
  return (
    <button
      type="button"
      onClick={() => dispatch({ type: "navigate", view: { name: "settings" } })}
      title={`${status.current.label}: ${status.current.reason}`}
      data-testid="ai-route-chip"
      data-route={status.current.route}
      className={cx(
        "no-drag inline-flex items-center gap-1.5 rounded-md border border-current/25 px-2 py-0.5 font-mono text-2xs tracking-wider uppercase",
        textTone(view.tone),
      )}
    >
      <span aria-hidden>{view.icon}</span>
      AI · {view.text}
    </button>
  );
}
