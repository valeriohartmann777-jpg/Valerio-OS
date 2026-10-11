import { Component, type ErrorInfo, type ReactNode } from "react";

import { Icon, type IconName } from "./icons";
import { Button, cx } from "./primitives";

/**
 * JARVIS 2.0 shell primitives (docs/JARVIS_2_DESIGN_SYSTEM.md): page header, panels, state
 * views and badges. Pages compose these instead of inventing their own colours.
 */

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  aside,
}: {
  eyebrow: string;
  title: string;
  subtitle?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 pt-7 pb-5">
      <div className="min-w-0">
        <p className="text-[12px] font-extrabold tracking-[0.22em] text-[var(--area)] uppercase">{eyebrow}</p>
        <h1 className="mt-1.5 text-[clamp(28px,3.2vw,44px)] leading-none font-black tracking-[-0.02em] text-fg uppercase">
          {title}
        </h1>
        {subtitle && <p className="mt-2 max-w-[760px] text-[14px] leading-relaxed text-fg-muted">{subtitle}</p>}
      </div>
      {aside && <div className="flex flex-wrap items-center gap-2">{aside}</div>}
    </header>
  );
}

/** A card region with a heavy uppercase title. */
export function Panel({
  title,
  aside,
  children,
  className,
  testId,
  padded = true,
}: {
  title?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
  padded?: boolean;
}) {
  return (
    <section
      className={cx(
        "rounded-2xl border border-hairline-strong bg-surface/90 shadow-[0_10px_30px_rgb(0_0_0/0.25)]",
        padded && "p-5",
        className,
      )}
      data-testid={testId}
    >
      {(title || aside) && (
        <header className={cx("mb-3 flex items-center justify-between gap-3", !padded && "px-5 pt-4")}>
          {title && <h2 className="text-[15px] font-black tracking-[0.04em] text-fg uppercase">{title}</h2>}
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

export type PillTone = "success" | "warning" | "danger" | "info" | "muted";

const PILL: Record<PillTone, { cls: string; icon: IconName }> = {
  success: { cls: "text-success border-success/40 bg-success/10", icon: "check" },
  warning: { cls: "text-warning border-warning/40 bg-warning/10", icon: "alert" },
  danger: { cls: "text-danger border-danger/40 bg-danger/10", icon: "x" },
  info: { cls: "text-accent border-accent/40 bg-accent/10", icon: "info" },
  muted: { cls: "text-fg-muted border-hairline-strong bg-surface-2/60", icon: "clock" },
};

/** A status with icon + text — never colour alone. */
export function StatusPill({ tone, children, testId, title }: { tone: PillTone; children: ReactNode; testId?: string; title?: string }) {
  return (
    <span
      className={cx("inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[12px] font-bold tracking-[0.04em] uppercase", PILL[tone].cls)}
      data-testid={testId}
      title={title}
    >
      <Icon name={PILL[tone].icon} size={13} />
      {children}
    </span>
  );
}

/** Solo or team context, shown wherever QuantLab work can happen. */
export function WorkspaceModeBadge({ mode }: { mode: "solo" | "team" }) {
  return mode === "solo" ? (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-quant/40 bg-quant/10 px-2 py-0.5 text-[12px] font-bold tracking-[0.06em] text-quant uppercase" data-testid="workspace-solo">
      <Icon name="lock" size={13} /> Solo workspace
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-ultron/40 bg-ultron/10 px-2 py-0.5 text-[12px] font-bold tracking-[0.06em] text-ultron uppercase" data-testid="workspace-team">
      <Icon name="users" size={13} /> Team workspace
    </span>
  );
}

export function EmptyState({ icon = "info", title, children, action }: { icon?: IconName; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-xl border border-dashed border-hairline-strong bg-canvas-2/40 p-5">
      <span className="flex items-center gap-2 text-[14px] font-bold text-fg">
        <Icon name={icon} size={16} className="text-fg-muted" />
        {title}
      </span>
      {children && <div className="text-[13px] leading-relaxed text-fg-muted">{children}</div>}
      {action}
    </div>
  );
}

export function ErrorState({ title, children, onRetry }: { title: string; children?: ReactNode; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-xl border border-danger/40 bg-danger/10 p-4" role="alert">
      <span className="flex items-center gap-2 text-[14px] font-bold text-danger">
        <Icon name="alert" size={16} /> {title}
      </span>
      {children && <div className="text-[13px] leading-relaxed text-fg-muted">{children}</div>}
      {onRetry && <Button onClick={onRetry}>Try again</Button>}
    </div>
  );
}

export function LoadingState({ children = "Loading…" }: { children?: ReactNode }) {
  return (
    <p className="flex items-center gap-2 text-[13px] text-fg-muted" role="status">
      <span className="size-3 animate-spin rounded-full border-2 border-fg-faint border-t-transparent" aria-hidden />
      {children}
    </p>
  );
}

/** A large clickable tile that opens another area (Home quick launch, room cards). */
export function Tile({
  label,
  icon,
  color,
  detail,
  onClick,
  testId,
}: {
  label: string;
  icon: IconName;
  color: string;
  detail: ReactNode;
  onClick: () => void;
  testId?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      style={{ "--tile": color } as React.CSSProperties}
      className="group flex min-h-[92px] flex-col items-start justify-between gap-2 rounded-xl border border-hairline-strong border-b-4 border-b-[color-mix(in_srgb,var(--tile)_55%,transparent)] bg-surface-2/60 p-3.5 text-left transition hover:-translate-y-0.5 hover:border-[var(--tile)]"
    >
      <span className="flex w-full items-center justify-between">
        <span className="text-[12px] font-extrabold tracking-[0.12em] text-fg uppercase">{label}</span>
        <Icon name={icon} size={18} className="text-[var(--tile)]" />
      </span>
      <span className="text-[12px] leading-snug text-fg-muted">{detail}</span>
    </button>
  );
}

/** Keeps one broken panel from blanking the whole app; shows what failed and a retry. */
export class SafeErrorBoundary extends Component<{ children: ReactNode; label: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[${this.props.label}]`, error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="p-6">
          <ErrorState title={`${this.props.label} couldn't be shown`} onRetry={() => this.setState({ error: null })}>
            {this.state.error.message || "An unexpected error occurred in this view."} The rest of JARVIS keeps working.
          </ErrorState>
        </div>
      );
    }
    return this.props.children;
  }
}

/** Sub-navigation inside one tab (never a second copy of the five main tabs). */
export function SubNav<T extends string>({
  label,
  items,
  current,
  onSelect,
}: {
  label: string;
  items: readonly { id: T; label: string; icon?: IconName; badge?: ReactNode }[];
  current: T;
  onSelect: (id: T) => void;
}) {
  return (
    <nav className="mb-5 flex gap-1 overflow-x-auto rounded-xl border border-hairline-strong bg-surface/80 p-1" aria-label={label}>
      {items.map((item) => {
        const active = item.id === current;
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onSelect(item.id)}
            aria-current={active ? "page" : undefined}
            data-testid={`subnav-${item.id}`}
            className={cx(
              "flex min-h-10 shrink-0 items-center gap-2 rounded-lg px-3.5 text-[13px] font-semibold whitespace-nowrap transition-colors",
              active ? "bg-surface-2 text-fg shadow-[inset_0_-3px_0_var(--area)]" : "text-fg-muted hover:bg-white/5 hover:text-fg",
            )}
          >
            {item.icon && <Icon name={item.icon} size={16} className={active ? "text-[var(--area)]" : undefined} />}
            {item.label}
            {item.badge}
          </button>
        );
      })}
    </nav>
  );
}
