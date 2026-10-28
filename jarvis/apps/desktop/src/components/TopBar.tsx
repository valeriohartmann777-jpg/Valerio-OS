import { useRef } from "react";

import { HAS_TITLEBAR_OVERLAY, HAS_TRAFFIC_LIGHTS } from "../lib/config";
import { clock } from "../lib/format";
import { go } from "../lib/history";
import { TABS, type Tab, tabOf } from "../lib/routes";
import { headerState, useReadiness, useSupervisor } from "../lib/backend";
import { useJarvis, useNow } from "../store/store";
import { routeChip, useAiStatus } from "./settings/AiBilling";
import { Icon } from "./ui/icons";
import { StatusDot, cx, textTone } from "./ui/primitives";
import { UpdateButton } from "./UpdateControls";


/**
 * The app header: brand, exactly five tabs (icon + label + active bar in the area colour) and
 * the real system state — backend connection, AI route, simulated desktop, updates.
 */
export function TopBar() {
  const connection = useJarvis((s) => s.connection);
  const simulated = useJarvis((s) => s.simulated);
  const version = useJarvis((s) => s.version);
  const build = useJarvis((s) => s.build);
  const active = useJarvis((s) => tabOf(s.view));
  const now = useNow(1000);
  // "Backend online" only when the event stream is up AND /health/ready passed (lib/backend.ts).
  const status = headerState(connection, useReadiness(), useSupervisor());
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);

  // Arrow keys move between tabs (Home/End jump), Enter/Space activates — no hidden gestures.
  const onKey = (event: React.KeyboardEvent, index: number) => {
    const last = TABS.length - 1;
    const target =
      event.key === "ArrowRight" ? (index === last ? 0 : index + 1)
      : event.key === "ArrowLeft" ? (index === 0 ? last : index - 1)
      : event.key === "Home" ? 0
      : event.key === "End" ? last
      : null;
    if (target === null) return;
    event.preventDefault();
    tabs.current[target]?.focus();
  };

  return (
    <header
      className={cx(
        "drag relative z-20 flex h-16 shrink-0 items-stretch gap-6 border-b border-hairline-strong bg-[#0a1530]/95 px-5 backdrop-blur",
        HAS_TITLEBAR_OVERLAY && "pr-[150px]",
        HAS_TRAFFIC_LIGHTS && "pl-[88px]",
      )}
    >
      <div className="flex items-center gap-2.5">
        <BrandMark />
        <div className="flex flex-col leading-none">
          <span className="text-[15px] font-black tracking-[0.18em] text-fg">JARVIS</span>
          {version && (
            <span className="mt-1 font-mono text-[11px] whitespace-nowrap text-fg-faint" data-testid="build" title={build ?? undefined}>
              v{version}
              {build && build !== "unknown" && <span className="sr-only xl:not-sr-only">{` · ${build.slice(0, 7)}`}</span>}
            </span>
          )}
        </div>
      </div>

      <nav className="no-drag flex min-w-0 items-stretch gap-1 overflow-x-auto" aria-label="Main">
        {TABS.map((tab, i) => (
          <TabButton
            key={tab.id}
            tab={tab.id}
            active={active === tab.id}
            onKey={(e) => onKey(e, i)}
            ref={(el) => {
              tabs.current[i] = el;
            }}
          />
        ))}
      </nav>

      <div className="no-drag ml-auto flex items-center gap-3 whitespace-nowrap xl:gap-4">
        <UpdateButton />
        <AiRouteChip />
        {simulated && (
          <span
            className="rounded-md border border-warning/40 px-2 py-0.5 font-mono text-2xs tracking-wider text-warning uppercase"
            title="No real system control on this machine — nothing is actually launched. On macOS or Windows: re-run the setup script and restart JARVIS."
            data-testid="simulated-badge"
          >
            Simulated
          </span>
        )}
        <span className="flex items-center gap-2 text-2xs font-semibold tracking-[0.12em] text-fg-muted uppercase" role="status" title={status.text}>
          <StatusDot tone={status.tone} live={status.tone !== "success"} />
          {/* below 1280 px only the dot shows; the text stays for screen readers and the tooltip */}
          <span data-testid="connection-status" className="sr-only xl:not-sr-only">
            {status.text}
          </span>
        </span>
        <span className="hidden font-mono text-xs text-fg-faint tabular xl:inline">{clock(now)}</span>
      </div>
    </header>
  );
}

function TabButton({
  tab,
  active,
  onKey,
  ref,
}: {
  tab: Tab;
  active: boolean;
  onKey: (event: React.KeyboardEvent) => void;
  ref: (el: HTMLButtonElement | null) => void;
}) {
  const def = TABS.find((t) => t.id === tab)!;
  return (
    <button
      ref={ref}
      type="button"
      onClick={() => go({ name: tab } as Parameters<typeof go>[0])}
      onKeyDown={onKey}
      aria-current={active ? "page" : undefined}
      title={def.hint}
      data-testid={`tab-${tab}`}
      style={{ "--tab": def.color } as React.CSSProperties}
      className={cx(
        "relative flex min-w-[96px] flex-col items-center justify-center gap-1 rounded-t-lg px-3 text-[12px] font-extrabold tracking-[0.06em] uppercase transition-colors",
        active ? "bg-gradient-to-b from-surface-2 to-surface text-fg" : "text-fg-muted hover:bg-white/5 hover:text-fg",
      )}
    >
      <Icon name={def.icon} size={20} className="text-[var(--tab)]" />
      <span className="whitespace-nowrap">{def.label}</span>
      {active && (
        <span aria-hidden className="absolute inset-x-3 bottom-0 h-1 rounded-t-md bg-[var(--tab)] shadow-[0_0_14px_var(--tab)]" />
      )}
    </button>
  );
}

/** The JARVIS mark: an original hexagon with a "J" — no third-party logo. */
export function BrandMark({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 34 34" aria-hidden>
      <defs>
        <linearGradient id="jv-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#81e7ff" />
          <stop offset="1" stopColor="#5575fd" />
        </linearGradient>
      </defs>
      <path d="M8.5 1h17L34 17l-8.5 16h-17L0 17z" fill="url(#jv-mark)" />
      <path d="M19.5 9v11.5a4.5 4.5 0 0 1-9 0" fill="none" stroke="#0b1b30" strokeWidth={3.2} strokeLinecap="round" />
    </svg>
  );
}

/** Which route AI work takes right now (Claude plan / API / local / paused) — opens AI & Billing. */
function AiRouteChip() {
  const [status] = useAiStatus();
  if (!status) return null;
  const view = routeChip(status);
  return (
    <button
      type="button"
      onClick={() => go({ name: "settings", section: "ai" })}
      title={`${status.current.label}: ${status.current.reason}`}
      data-testid="ai-route-chip"
      data-route={status.current.route}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-md border border-current/30 px-2 py-0.5 font-mono text-2xs tracking-wider uppercase",
        textTone(view.tone),
      )}
    >
      <span aria-hidden>{view.icon}</span>
      AI · {view.text}
    </button>
  );
}
