import { HAS_TITLEBAR_OVERLAY } from "../lib/config";
import { clock } from "../lib/format";
import type { View } from "../store/reducer";
import { dispatch, useJarvis, useNow } from "../store/store";
import { StatusDot, cx } from "./ui/primitives";

const CONNECTION = {
  online: { text: "System online", tone: "success" as const },
  connecting: { text: "Connecting", tone: "warning" as const },
  offline: { text: "Offline", tone: "danger" as const },
};

export function TopBar() {
  const connection = useJarvis((s) => s.connection);
  const simulated = useJarvis((s) => s.simulated);
  const version = useJarvis((s) => s.version);
  const view = useJarvis((s) => s.view);
  const now = useNow(1000);
  const status = CONNECTION[connection];

  return (
    <header
      className={cx(
        "drag flex h-11 shrink-0 items-center gap-8 border-b border-hairline px-5",
        HAS_TITLEBAR_OVERLAY && "pr-[150px]",
      )}
    >
      <div className="flex items-baseline gap-2.5">
        <span className="text-[13px] font-semibold tracking-[0.34em] text-fg">JARVIS</span>
        {version && <span className="font-mono text-2xs text-fg-faint">v{version}</span>}
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
        <NavItem active={view.name === "settings"} view={{ name: "settings" }}>
          Settings
        </NavItem>
      </nav>

      <div className="ml-auto flex items-center gap-5">
        {simulated && (
          <span
            className="rounded-md border border-warning/30 px-2 py-0.5 font-mono text-2xs tracking-wider text-warning uppercase"
            title="The desktop is simulated (non-Windows host). No real applications are launched."
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
