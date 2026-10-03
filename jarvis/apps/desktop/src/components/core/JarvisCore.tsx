/**
 * The JARVIS Core: a calm, state-driven visual.
 *
 * Only transform and opacity are animated (GPU-composited). Colour changes
 * transition through `color`, which every layer inherits via currentColor.
 */

import type { JarvisState } from "@jarvis/protocol";
import { motion } from "motion/react";
import type { CSSProperties } from "react";

type Tone = "accent" | "warning" | "danger" | "success" | "muted";
type Pulse = "breathe" | "listen" | "attention" | "none";

interface Visual {
  tone: Tone;
  glow: number;
  ticks: number; // seconds per revolution, 0 = still
  arcs: number; // seconds per revolution, 0 = hidden
  scan: boolean;
  ripple: boolean;
  pulse: Pulse;
}

const VISUALS: Record<JarvisState, Visual> = {
  DORMANT: { tone: "accent", glow: 0.5, ticks: 0, arcs: 0, scan: false, ripple: false, pulse: "breathe" },
  LISTENING: { tone: "accent", glow: 0.8, ticks: 0, arcs: 0, scan: false, ripple: false, pulse: "listen" },
  UNDERSTANDING: { tone: "accent", glow: 0.75, ticks: 90, arcs: 10, scan: false, ripple: false, pulse: "none" },
  PLANNING: { tone: "accent", glow: 0.75, ticks: 90, arcs: 7, scan: false, ripple: false, pulse: "none" },
  THINKING: { tone: "accent", glow: 0.8, ticks: 120, arcs: 12, scan: true, ripple: false, pulse: "none" },
  DELEGATING: { tone: "accent", glow: 0.85, ticks: 60, arcs: 5, scan: false, ripple: true, pulse: "none" },
  EXECUTING: { tone: "accent", glow: 0.95, ticks: 45, arcs: 4, scan: false, ripple: true, pulse: "none" },
  VERIFYING: { tone: "accent", glow: 0.8, ticks: 60, arcs: 0, scan: true, ripple: false, pulse: "none" },
  WAITING_FOR_APPROVAL: { tone: "warning", glow: 0.7, ticks: 0, arcs: 0, scan: false, ripple: false, pulse: "attention" },
  SPEAKING: { tone: "accent", glow: 0.85, ticks: 0, arcs: 0, scan: false, ripple: false, pulse: "listen" },
  COMPLETE: { tone: "success", glow: 0.7, ticks: 0, arcs: 0, scan: false, ripple: false, pulse: "breathe" },
  FAILED: { tone: "danger", glow: 0.6, ticks: 0, arcs: 0, scan: false, ripple: false, pulse: "none" },
  PAUSED: { tone: "muted", glow: 0.2, ticks: 0, arcs: 0, scan: false, ripple: false, pulse: "none" },
};

const TONES: Record<Tone, string> = {
  accent: "var(--color-accent)",
  warning: "var(--color-warning)",
  danger: "var(--color-danger)",
  success: "var(--color-success)",
  muted: "var(--color-fg-faint)",
};

const PULSES: Record<Pulse, { scale: number[]; opacity: number[]; duration: number }> = {
  breathe: { scale: [1, 1.03, 1], opacity: [0.92, 1, 0.92], duration: 6 },
  listen: { scale: [1, 1.08, 1], opacity: [0.9, 1, 0.9], duration: 1.2 },
  attention: { scale: [1, 1.02, 1], opacity: [1, 0.6, 1], duration: 2.2 },
  none: { scale: [1], opacity: [1], duration: 0.6 },
};

const SIZE = 300;
const C = SIZE / 2;
const TICKS = Array.from({ length: 90 }, (_, i) => i * 4);

function spin(seconds: number, direction: "cw" | "ccw" = "cw"): CSSProperties {
  return seconds
    ? { animation: `spin-${direction} ${seconds}s linear infinite` }
    : { animationPlayState: "paused" };
}

export function JarvisCore({
  state,
  size,
  offline = false,
}: {
  state: JarvisState;
  size: number;
  offline?: boolean;
}) {
  const visual = offline ? VISUALS.PAUSED : VISUALS[state];
  const pulse = PULSES[visual.pulse];

  return (
    <motion.div
      className="relative shrink-0 transition-[color] duration-700"
      style={{ color: TONES[visual.tone] }}
      initial={false}
      animate={{ width: size, height: size }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      data-state={state}
      aria-label={`JARVIS core — ${state}`}
      role="img"
    >
      {/* ambient glow */}
      <motion.div
        className="pointer-events-none absolute -inset-16 rounded-full"
        style={{ background: "radial-gradient(circle, currentColor 0%, transparent 58%)" }}
        animate={{ opacity: visual.glow * 0.16 }}
        transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
      />

      {/* outer hairline */}
      <Layer>
        <circle cx={C} cy={C} r={146} fill="none" stroke="var(--color-hairline-strong)" strokeWidth={1} />
      </Layer>

      {/* tick ring */}
      <Layer style={spin(visual.ticks)}>
        <g stroke="currentColor" strokeOpacity={0.3} strokeWidth={1}>
          {TICKS.map((deg) => {
            const major = deg % 30 === 0;
            const rad = (deg * Math.PI) / 180;
            const r1 = major ? 124 : 129;
            const r2 = 134;
            return (
              <line
                key={deg}
                x1={C + r1 * Math.cos(rad)}
                y1={C + r1 * Math.sin(rad)}
                x2={C + r2 * Math.cos(rad)}
                y2={C + r2 * Math.sin(rad)}
                strokeOpacity={major ? 0.55 : 0.3}
              />
            );
          })}
        </g>
      </Layer>

      {/* structured arcs — thinking / planning / executing */}
      <motion.div
        className="absolute inset-0"
        animate={{ opacity: visual.arcs ? 0.9 : 0 }}
        transition={{ duration: 0.6 }}
      >
        <Layer style={spin(visual.arcs || 8)}>
          <circle
            cx={C}
            cy={C}
            r={112}
            fill="none"
            stroke="currentColor"
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeDasharray="150 84.6"
          />
        </Layer>
      </motion.div>

      {/* inner ring + verification sweep */}
      <Layer>
        <circle cx={C} cy={C} r={88} fill="none" stroke="currentColor" strokeOpacity={0.18} strokeWidth={1} />
      </Layer>
      <motion.div
        className="absolute inset-0"
        animate={{ opacity: visual.scan ? 1 : 0 }}
        transition={{ duration: 0.5 }}
      >
        <Layer style={spin(visual.scan ? 2.4 : 6, "ccw")}>
          <circle
            cx={C}
            cy={C}
            r={88}
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            strokeDasharray="56 497"
          />
        </Layer>
      </motion.div>

      {/* outward data movement — executing */}
      {visual.ripple && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          {[0, 1.1].map((delay) => (
            <span
              key={delay}
              className="absolute size-[40%] rounded-full border border-current"
              style={{ animation: `ripple 2.2s ${delay}s cubic-bezier(0.22,1,0.36,1) infinite`, opacity: 0 }}
            />
          ))}
        </div>
      )}

      {/* core */}
      <div className="absolute inset-0 grid place-items-center">
        <motion.div
          key={state === "COMPLETE" ? "complete" : "steady"}
          className="size-[41.3%]"
          initial={state === "COMPLETE" ? { scale: 1.08 } : false}
          animate={{ scale: pulse.scale, opacity: pulse.opacity }}
          transition={{
            duration: pulse.duration,
            repeat: visual.pulse === "none" ? 0 : Infinity,
            ease: "easeInOut",
          }}
        >
          <svg viewBox="0 0 124 124" className="size-full overflow-visible">
            <defs>
              <radialGradient id="core-fill" cx="50%" cy="46%" r="50%">
                <stop offset="0%" stopColor="#ffffff" stopOpacity={0.9} />
                <stop offset="12%" stopColor="currentColor" stopOpacity={0.85} />
                <stop offset="45%" stopColor="currentColor" stopOpacity={0.32} />
                <stop offset="100%" stopColor="currentColor" stopOpacity={0.04} />
              </radialGradient>
            </defs>
            <circle cx={62} cy={62} r={58} fill="url(#core-fill)" />
            <circle cx={62} cy={62} r={58} fill="none" stroke="currentColor" strokeOpacity={0.55} />
          </svg>
        </motion.div>
      </div>
    </motion.div>
  );
}

function Layer({ children, style }: { children: React.ReactNode; style?: CSSProperties }) {
  return (
    <div className="absolute inset-0 will-change-transform" style={style}>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="size-full">
        {children}
      </svg>
    </div>
  );
}
