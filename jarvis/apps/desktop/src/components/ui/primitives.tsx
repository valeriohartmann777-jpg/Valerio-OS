import type { ButtonHTMLAttributes, ReactNode } from "react";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

export function Section({
  title,
  aside,
  children,
  className,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("px-5 py-4", className)}>
      <header className="mb-3 flex items-center justify-between">
        <h2 className="label">{title}</h2>
        {aside}
      </header>
      {children}
    </section>
  );
}

export type Tone = "accent" | "success" | "warning" | "danger" | "muted" | "faint";

const DOT_TONES: Record<Tone, string> = {
  accent: "bg-accent",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  muted: "bg-fg-muted",
  faint: "bg-fg-faint/60",
};

const TEXT_TONES: Record<Tone, string> = {
  accent: "text-accent",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  muted: "text-fg-muted",
  faint: "text-fg-faint",
};

export function textTone(tone: Tone): string {
  return TEXT_TONES[tone];
}

export function StatusDot({ tone, live = false }: { tone: Tone; live?: boolean }) {
  return (
    <span className="relative inline-flex size-1.5 shrink-0">
      {live && (
        <span
          className={cx("absolute inset-0 rounded-full", DOT_TONES[tone])}
          style={{ animation: "beacon 1.6s ease-in-out infinite" }}
        />
      )}
      <span className={cx("relative size-1.5 rounded-full", DOT_TONES[tone])} />
    </span>
  );
}

export function Meter({ value, tone = "muted" }: { value: number; tone?: Tone }) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className="h-[2px] w-full overflow-hidden rounded-full bg-hairline-strong">
      <div
        className={cx("h-full origin-left rounded-full transition-transform duration-700", DOT_TONES[tone])}
        style={{ transform: `scaleX(${clamped / 100})` }}
      />
    </div>
  );
}

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border border-current/20 px-2 py-0.5 text-2xs font-medium tracking-wide",
        TEXT_TONES[tone],
      )}
    >
      {children}
    </span>
  );
}

type ButtonVariant = "primary" | "ghost" | "danger" | "warning";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-fg text-canvas hover:bg-white border-transparent",
  ghost: "border-hairline-strong text-fg-muted hover:text-fg hover:border-white/20 hover:bg-white/[0.03]",
  danger: "border-danger/40 text-danger hover:bg-danger/10",
  warning: "bg-warning text-canvas hover:brightness-110 border-transparent",
};

export function Button({
  variant = "ghost",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      type="button"
      className={cx(
        "no-drag inline-flex h-8 items-center justify-center gap-2 rounded-lg border px-3 text-[13px] font-medium",
        "transition-colors duration-150 disabled:pointer-events-none disabled:opacity-40",
        BUTTON_VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[84px_minmax(0,1fr)] items-baseline gap-3 py-[5px]">
      <dt className="text-xs text-fg-faint">{label}</dt>
      <dd className="truncate text-[13px] text-fg">{children}</dd>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-[13px] text-fg-faint">{children}</p>;
}
