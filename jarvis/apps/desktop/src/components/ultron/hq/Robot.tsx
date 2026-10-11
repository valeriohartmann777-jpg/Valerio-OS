import type { HqState } from "@jarvis/protocol";

import { cx } from "../../ui/primitives";

/**
 * The ULTRON robots — original small SVG figures drawn for JARVIS (no game or film assets).
 * Each has its own antenna tip and chest emblem; the face shows the *real* state:
 * closed eyes when docked, open eyes while working, a badge for approval / blocked / failed /
 * done, and a dimmed body when offline. Animation runs only while a robot really works.
 */

type Emblem = "triangle" | "bolt" | "orbit" | "gem" | "bars" | "shield" | "cursor" | "book" | "gear";

const LOOK: Record<string, { tip: "ball" | "triangle" | "dish" | "diamond" | "square" | "ring"; emblem: Emblem }> = {
  axiom: { tip: "triangle", emblem: "triangle" },
  forge: { tip: "square", emblem: "bolt" },
  atlas: { tip: "dish", emblem: "orbit" },
  prism: { tip: "diamond", emblem: "gem" },
  cipher: { tip: "ball", emblem: "bars" },
  sentinel: { tip: "ring", emblem: "shield" },
  operator: { tip: "ball", emblem: "cursor" },
  archive: { tip: "square", emblem: "book" },
  vector: { tip: "triangle", emblem: "gear" },
};

const BADGE: Partial<Record<HqState, { text: string; fill: string }>> = {
  waiting_approval: { text: "!", fill: "#ffc46b" },
  blocked: { text: "!", fill: "#ff9d4d" },
  failed: { text: "×", fill: "#ff747d" },
  complete: { text: "✓", fill: "#4cdfac" },
  reserved: { text: "…", fill: "#9db1cd" },
};

export function RobotFigure({ id, color, state, size = 56 }: { id: string; color: string; state: HqState; size?: number }) {
  const look = LOOK[id] ?? { tip: "ball", emblem: "bars" };
  const asleep = state === "idle" || state === "reserved" || state === "complete" || state === "offline";
  const badge = BADGE[state];
  const gid = `bot-metal-${id}`;
  return (
    <svg
      width={size}
      height={size * 1.15}
      viewBox="0 0 64 74"
      aria-hidden
      className={cx(state === "working" && "hq-bob", state === "offline" && "opacity-45 grayscale")}
      data-robot-state={state}
    >
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#eef3f9" />
          <stop offset="1" stopColor="#8fa4c4" />
        </linearGradient>
      </defs>
      {/* antenna */}
      <line x1="32" y1="6" x2="32" y2="15" stroke="#273c5f" strokeWidth="2.5" />
      <Tip kind={look.tip} color={color} />
      {/* head */}
      <rect x="11" y="14" width="42" height="30" rx="10" fill={`url(#${gid})`} stroke="#22385a" strokeWidth="2.5" />
      <rect x="16.5" y="20" width="31" height="17" rx="6" fill="#0d1a30" />
      {asleep ? (
        <>
          <path d="M22 29h7M35 29h7" stroke={color} strokeWidth="2.4" strokeLinecap="round" opacity={state === "offline" ? 0.5 : 0.9} />
        </>
      ) : (
        <>
          <circle cx="25.5" cy="28.5" r="3.4" fill={color} />
          <circle cx="38.5" cy="28.5" r="3.4" fill={color} />
          <circle cx="25.5" cy="28.5" r="6" fill={color} opacity="0.18" />
          <circle cx="38.5" cy="28.5" r="6" fill={color} opacity="0.18" />
        </>
      )}
      {/* ears */}
      <rect x="6.5" y="24" width="5" height="10" rx="2" fill="#5d7397" />
      <rect x="52.5" y="24" width="5" height="10" rx="2" fill="#5d7397" />
      {/* body */}
      <rect x="18" y="46" width="28" height="18" rx="6" fill={`url(#${gid})`} stroke="#22385a" strokeWidth="2.2" />
      <EmblemShape kind={look.emblem} color={color} />
      {/* feet */}
      <rect x="21" y="64" width="8" height="6" rx="2" fill="#3d5478" />
      <rect x="35" y="64" width="8" height="6" rx="2" fill="#3d5478" />
      {state === "idle" && (
        <text x="52" y="13" fontSize="10" fontWeight="800" fill="#becfe7" className="hq-z">
          z
        </text>
      )}
      {badge && (
        <g>
          <circle cx="54" cy="12" r="8" fill={badge.fill} stroke="#0b1730" strokeWidth="2" />
          <text x="54" y="16" textAnchor="middle" fontSize="11" fontWeight="900" fill="#0b1730">
            {badge.text}
          </text>
        </g>
      )}
    </svg>
  );
}

function Tip({ kind, color }: { kind: string; color: string }) {
  switch (kind) {
    case "triangle":
      return <path d="M32 0 37 8H27z" fill={color} />;
    case "dish":
      return <path d="M25 5a7 7 0 0 0 14 0z" fill={color} />;
    case "diamond":
      return <path d="M32 0 36.5 4.5 32 9 27.5 4.5z" fill={color} />;
    case "square":
      return <rect x="28" y="1" width="8" height="7" rx="1.5" fill={color} />;
    case "ring":
      return <circle cx="32" cy="5" r="4" fill="none" stroke={color} strokeWidth="2.6" />;
    default:
      return <circle cx="32" cy="5" r="4.2" fill={color} />;
  }
}

function EmblemShape({ kind, color }: { kind: Emblem; color: string }) {
  switch (kind) {
    case "triangle":
      return <path d="M32 49.5 37 58h-10z" fill={color} />;
    case "bolt":
      return <path d="M33.5 49 27.5 56.5h5l-2 6 6.5-8h-5z" fill={color} />;
    case "orbit":
      return (
        <>
          <ellipse cx="32" cy="55" rx="7" ry="3.2" fill="none" stroke={color} strokeWidth="1.8" />
          <circle cx="32" cy="55" r="2.2" fill={color} />
        </>
      );
    case "gem":
      return <path d="M27 53 32 49l5 4-5 7z" fill={color} />;
    case "bars":
      return (
        <>
          <rect x="25.5" y="55" width="3" height="5" rx="0.8" fill={color} />
          <rect x="30.5" y="51.5" width="3" height="8.5" rx="0.8" fill={color} />
          <rect x="35.5" y="53.5" width="3" height="6.5" rx="0.8" fill={color} />
        </>
      );
    case "shield":
      return <path d="M32 49.5 38 52v4c0 3-3 4.5-6 5.5-3-1-6-2.5-6-5.5v-4z" fill={color} />;
    case "cursor":
      return <path d="M28.5 49.5v11l3-3 2.5 4.5 2-1-2.5-4.5h4z" fill={color} />;
    case "book":
      return <path d="M26 51h6v10h-6zM32.5 51h5.5v10h-5.5z" fill={color} />;
    default:
      return (
        <>
          <circle cx="32" cy="55" r="5" fill="none" stroke={color} strokeWidth="2.4" strokeDasharray="2.4 1.6" />
          <circle cx="32" cy="55" r="1.8" fill={color} />
        </>
      );
  }
}
