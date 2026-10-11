import type { SVGProps } from "react";

/**
 * The one icon map of the app (JARVIS 2.0 design system). Simple 24×24 stroke drawings made
 * for JARVIS — no icon font, no third-party set, no emoji. Use `<Icon name="…" />`.
 */
const PATHS = {
  home: "M3 10.5 12 3l9 7.5V21h-6v-6.5H9V21H3z",
  training: "M2 9.5 12 4l10 5.5-10 5.5zM6 12v5c3.2 2.2 8.8 2.2 12 0v-5M22 9.5V16",
  quantlab: "M3 20.5h18M7 4.5v13M5 7.5h4v6H5zM17 3v14M15 6.5h4v7h-4z",
  ultron: "M5 7.5h14a2 2 0 0 1 2 2V17a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V9.5a2 2 0 0 1 2-2zM12 3.5v4M9.5 3.5h5M8.5 13h.01M15.5 13h.01M9.5 16.5h5",
  settings:
    "M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4zM10.3 2.5h3.4l.5 2.4 2 .9 2.1-1.3 2.4 2.4-1.3 2.1.9 2 2.4.5v3.4l-2.4.5-.9 2 1.3 2.1-2.4 2.4-2.1-1.3-2 .9-.5 2.4h-3.4l-.5-2.4-2-.9-2.1 1.3-2.4-2.4 1.3-2.1-.9-2-2.4-.5v-3.4l2.4-.5.9-2-1.3-2.1 2.4-2.4 2.1 1.3 2-.9z",
  send: "M4 12 20 4l-5 16-3.5-6.5zM11.5 13.5 20 4",
  mic: "M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3",
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.5 15.5 21 21",
  play: "M7 4.5v15l12-7.5z",
  pause: "M8 5v14M16 5v14",
  stop: "M6 6h12v12H6z",
  check: "M4.5 12.5 9.5 17.5 19.5 6.5",
  alert: "M12 3.5 22 20.5H2zM12 10v4.5M12 17.5h.01",
  x: "M6 6l12 12M18 6 6 18",
  lock: "M6 11h12v10H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3",
  share: "M16 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM16 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM10.6 13.5l2.8 1.8M13.4 8.7l-2.8 1.8",
  arrow: "M5 12h14M13 6l6 6-6 6",
  back: "M19 12H5M11 6l-6 6 6 6",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  moon: "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z",
  cog: "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1",
  flask: "M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3M7 15h10",
  book: "M4 4.5A1.5 1.5 0 0 1 5.5 3H20v15H5.5A1.5 1.5 0 0 0 4 19.5zM4 19.5A1.5 1.5 0 0 0 5.5 21H20v-3",
  target: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zM12 12h.01",
  database: "M12 8c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3zM4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  chat: "M4 5h16v11H9l-5 4z",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  bot: "M6 8h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2zM12 4v4M9 13h.01M15 13h.01",
  plus: "M12 5v14M5 12h14",
  chevron: "M9 6l6 6-6 6",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5M12 8h.01",
  wifi: "M2.5 9a14 14 0 0 1 19 0M6 12.5a9 9 0 0 1 12 0M9.5 16a4 4 0 0 1 5 0M12 19.5h.01",
  candles: "M7 3v4M7 17v4M5 7h4v10H5zM17 5v3M17 15v4M15 8h4v7h-4z",
  users: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 6.5M18 14a6 6 0 0 1 3.5 6",
} as const;

export type IconName = keyof typeof PATHS;
export const ICON_NAMES = Object.keys(PATHS) as IconName[];

export function Icon({
  name,
  size = 18,
  label,
  ...props
}: { name: IconName; size?: number; label?: string } & Omit<SVGProps<SVGSVGElement>, "name">) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
      {...props}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
