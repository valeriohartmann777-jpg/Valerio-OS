import { useEffect, useMemo, useRef, useState } from "react";

import { cx } from "../../ui/primitives";
import { niceTicks } from "../EquityChart";

/**
 * QuantLab research charts, SVG only. One y-scale per panel; thin marks; hover
 * tooltips; every chart has a data view. Candles stay neutral graphite (context);
 * colour is reserved for what the strategy did (entries, exits, stop/target).
 */

function useWidth(min = 320): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => entry && setWidth(Math.max(min, entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, [min]);
  return [ref, width];
}

function short(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${(value / 1000).toFixed(1)}K`;
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

const TONE: Record<string, string> = {
  positive: "var(--color-ql-positive)",
  danger: "var(--color-ql-danger)",
  accent: "var(--color-ql)",
  muted: "var(--color-ql-muted)",
  warning: "var(--color-ql-warning)",
};

export interface Candle {
  label: string;
  o: number;
  h: number;
  l: number;
  c: number;
}

export interface PriceLine {
  value: number;
  label: string;
  tone: "positive" | "danger" | "accent" | "muted" | "warning";
  dash?: string;
}

export interface Marker {
  index: number;
  price: number;
  kind: "entry" | "exit";
  direction: 1 | -1;
  win?: boolean;
  label: string;
}

const PAD = { top: 16, right: 92, bottom: 26, left: 8 };

export function CandleChart({
  candles,
  lines = [],
  markers = [],
  height = 260,
  ariaLabel,
  testId,
  detail,
}: {
  candles: Candle[];
  lines?: PriceLine[];
  markers?: Marker[];
  height?: number;
  ariaLabel: string;
  testId?: string;
  detail?: (index: number) => string[];
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const geo = useMemo(() => {
    const n = candles.length;
    const plotW = width - PAD.left - PAD.right;
    const slot = n ? plotW / n : plotW;
    const x = (i: number) => PAD.left + slot * (i + 0.5);
    const values = candles.flatMap((c) => [c.h, c.l]).concat(lines.map((l) => l.value));
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    if (!(hi > lo)) {
      lo -= 1;
      hi += 1;
    }
    const pad = (hi - lo) * 0.06;
    lo -= pad;
    hi += pad;
    const y = (v: number) => PAD.top + (1 - (v - lo) / (hi - lo)) * (height - PAD.top - PAD.bottom);
    return { n, slot, x, y, ticks: niceTicks(lo, hi, 5), right: width - PAD.right };
  }, [candles, lines, width, height]);

  if (!candles.length) return <p className="text-[13px] text-fg-faint">No bars to draw.</p>;
  const body = Math.max(1, Math.min(9, geo.slot * 0.6));
  const active = hover !== null ? candles[hover] : null;
  const labelTicks = [0, Math.floor((geo.n - 1) / 2), geo.n - 1];

  return (
    <div ref={ref} className="relative" data-testid={testId}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={ariaLabel}
        tabIndex={0}
        className="block touch-none outline-none focus-visible:ring-1 focus-visible:ring-ql/50"
        onPointerMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const i = Math.floor((e.clientX - rect.left - PAD.left) / geo.slot);
          setHover(i >= 0 && i < geo.n ? i : null);
        }}
        onPointerLeave={() => setHover(null)}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") setHover((h) => Math.min(geo.n - 1, (h ?? -1) + 1));
          else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? geo.n) - 1));
          else if (e.key === "Escape") setHover(null);
          else return;
          e.preventDefault();
        }}
      >
        {geo.ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={geo.right} y1={geo.y(t)} y2={geo.y(t)} stroke="white" strokeOpacity={0.05} />
            <text x={geo.right + 8} y={geo.y(t) + 3} className="fill-fg-faint font-mono text-[10px] tabular">
              {short(t)}
            </text>
          </g>
        ))}
        {candles.map((c, i) => {
          const up = c.c >= c.o;
          const top = geo.y(Math.max(c.o, c.c));
          const bottom = geo.y(Math.min(c.o, c.c));
          return (
            <g key={`${c.label}-${i}`} opacity={hover === null || hover === i ? 1 : 0.55}>
              <line x1={geo.x(i)} x2={geo.x(i)} y1={geo.y(c.h)} y2={geo.y(c.l)} stroke="var(--color-ql-muted)" strokeWidth={1} />
              <rect
                x={geo.x(i) - body / 2}
                y={top}
                width={body}
                height={Math.max(1, bottom - top)}
                rx={1}
                fill={up ? "var(--color-ql-surface)" : "var(--color-ql-muted)"}
                stroke="var(--color-ql-muted)"
                strokeWidth={1}
              />
            </g>
          );
        })}
        {lines.map((l) => (
          <g key={`${l.label}-${l.value}`}>
            <line
              x1={PAD.left}
              x2={geo.right}
              y1={geo.y(l.value)}
              y2={geo.y(l.value)}
              stroke={TONE[l.tone]}
              strokeWidth={1.25}
              strokeDasharray={l.dash}
              opacity={0.9}
            />
            <text x={geo.right + 8} y={geo.y(l.value) - 3} className="fill-fg-muted text-[10px]">
              {l.label}
            </text>
          </g>
        ))}
        {markers.map((m, k) => {
          const cx_ = geo.x(m.index);
          const cy = geo.y(m.price);
          if (m.kind === "entry") {
            const d = m.direction > 0 ? `M${cx_},${cy - 7} L${cx_ - 6},${cy + 4} L${cx_ + 6},${cy + 4} Z` : `M${cx_},${cy + 7} L${cx_ - 6},${cy - 4} L${cx_ + 6},${cy - 4} Z`;
            return <path key={k} d={d} fill="var(--color-ql)" stroke="var(--color-ql-surface)" strokeWidth={1.5}><title>{m.label}</title></path>;
          }
          return (
            <circle key={k} cx={cx_} cy={cy} r={4.5} fill={m.win ? "var(--color-ql-positive)" : "var(--color-ql-danger)"} stroke="var(--color-ql-surface)" strokeWidth={1.5}>
              <title>{m.label}</title>
            </circle>
          );
        })}
        {labelTicks.map((i, k) => (
          <text
            key={`x${i}-${k}`}
            x={geo.x(i)}
            y={height - 8}
            textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"}
            className="fill-fg-faint font-mono text-[10px]"
          >
            {candles[i]?.label}
          </text>
        ))}
        {hover !== null && (
          <line x1={geo.x(hover)} x2={geo.x(hover)} y1={PAD.top} y2={height - PAD.bottom} stroke="white" strokeOpacity={0.3} pointerEvents="none" />
        )}
      </svg>
      {active && hover !== null && (
        <div
          className="pointer-events-none absolute top-2 z-10 min-w-[180px] rounded-lg border border-ql-border bg-ql-raised/95 px-3 py-2 shadow-xl"
          style={{ left: Math.min(Math.max(0, geo.x(hover) + 12), width - 220) }}
          role="status"
        >
          <p className="font-mono text-2xs text-fg-faint">{active.label}</p>
          <p className="mt-0.5 font-mono text-2xs text-fg tabular">
            O {short(active.o)} · H {short(active.h)} · L {short(active.l)} · C {short(active.c)}
          </p>
          {detail?.(hover).map((line) => (
            <p key={line} className="mt-0.5 text-2xs text-fg-muted">
              {line}
            </p>
          ))}
        </div>
      )}
      <div className="mt-1 flex items-center justify-between text-2xs text-fg-faint">
        <span>
          {markers.length > 0 && "▲▼ entry (direction) · ● exit (green win, red loss) · "}
          hollow = up, filled = down
        </span>
        <button type="button" className="text-fg-muted underline-offset-2 hover:underline" onClick={() => setTable((t) => !t)}>
          {table ? "Hide data" : "Show data"}
        </button>
      </div>
      {table && (
        <div className="mt-2 max-h-56 overflow-y-auto rounded-lg border border-ql-border">
          <table className="w-full text-left text-2xs">
            <thead className="sticky top-0 bg-ql-raised text-fg-faint">
              <tr>
                {["Bar", "Open", "High", "Low", "Close"].map((h) => (
                  <th key={h} className="px-2 py-1 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="font-mono text-fg-muted tabular">
              {candles.map((c, i) => (
                <tr key={`${c.label}-${i}`} className="border-t border-ql-border/60">
                  <td className="px-2 py-0.5">{c.label}</td>
                  <td className="px-2 py-0.5">{c.o}</td>
                  <td className="px-2 py-0.5">{c.h}</td>
                  <td className="px-2 py-0.5">{c.l}</td>
                  <td className="px-2 py-0.5">{c.c}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Signed values as bars around zero (P&L per window, month, scenario). */
export function SignedBars({
  items,
  height = 160,
  unit = "$",
  ariaLabel,
  testId,
}: {
  items: { label: string; value: number; note?: string }[];
  height?: number;
  unit?: string;
  ariaLabel: string;
  testId?: string;
}) {
  const [ref, width] = useWidth(240);
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const pad = { top: 12, right: 64, bottom: 22, left: 8 };
  const values = items.map((i) => i.value);
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  const span = hi - lo || 1;
  const plotH = height - pad.top - pad.bottom;
  const y = (v: number) => pad.top + ((hi - v) / span) * plotH;
  const slot = (width - pad.left - pad.right) / Math.max(1, items.length);
  const bar = Math.max(2, Math.min(28, slot - 2));
  const fmtv = (v: number) => (unit === "$" ? `${v < 0 ? "−" : ""}$${short(Math.abs(v))}` : `${short(v)}${unit}`);
  if (!items.length) return <p className="text-[13px] text-fg-faint">Nothing to show.</p>;
  const active = hover !== null ? items[hover] : null;
  return (
    <div ref={ref} className="relative" data-testid={testId}>
      <svg width={width} height={height} role="img" aria-label={ariaLabel} onPointerLeave={() => setHover(null)}>
        <line x1={pad.left} x2={width - pad.right} y1={y(0)} y2={y(0)} stroke="var(--color-ql-muted)" strokeOpacity={0.6} />
        <text x={width - pad.right + 8} y={y(0) + 3} className="fill-fg-faint font-mono text-[10px]">0</text>
        <text x={width - pad.right + 8} y={y(hi) + 8} className="fill-fg-faint font-mono text-[10px]">{fmtv(hi)}</text>
        {lo < 0 && <text x={width - pad.right + 8} y={y(lo)} className="fill-fg-faint font-mono text-[10px]">{fmtv(lo)}</text>}
        {items.map((item, i) => {
          const x = pad.left + slot * i + (slot - bar) / 2;
          const top = y(Math.max(0, item.value));
          const h = Math.max(1, Math.abs(y(item.value) - y(0)));
          return (
            <g key={`${item.label}-${i}`} onPointerEnter={() => setHover(i)}>
              <rect x={pad.left + slot * i} y={pad.top} width={slot} height={plotH} fill="transparent" />
              <rect
                x={x}
                y={top}
                width={bar}
                height={h}
                rx={Math.min(4, bar / 2)}
                fill={item.value >= 0 ? "var(--color-ql)" : "var(--color-ql-danger)"}
                opacity={hover === null || hover === i ? 0.9 : 0.45}
              />
            </g>
          );
        })}
        {[0, items.length - 1].map((i, k) => (
          <text key={`l${i}-${k}`} x={pad.left + slot * (i + 0.5)} y={height - 6} textAnchor={k === 0 ? "start" : "end"} className="fill-fg-faint font-mono text-[10px]">
            {items[i]?.label}
          </text>
        ))}
      </svg>
      {active && hover !== null && (
        <div
          className="pointer-events-none absolute top-1 z-10 rounded-lg border border-ql-border bg-ql-raised/95 px-3 py-1.5 shadow-xl"
          style={{ left: Math.min(Math.max(0, pad.left + slot * hover + slot / 2 + 8), width - 200) }}
          role="status"
        >
          <p className="font-mono text-2xs text-fg-faint">{active.label}</p>
          <p className={cx("text-[13px] font-medium tabular", active.value >= 0 ? "text-fg" : "text-ql-danger")}>
            {active.value >= 0 ? "+" : ""}
            {fmtv(active.value)}
          </p>
          {active.note && <p className="text-2xs text-fg-muted">{active.note}</p>}
        </div>
      )}
      <div className="mt-1 flex justify-end text-2xs">
        <button type="button" className="text-fg-muted underline-offset-2 hover:underline" onClick={() => setTable((t) => !t)}>
          {table ? "Hide data" : "Show data"}
        </button>
      </div>
      {table && (
        <table className="mt-1 w-full text-left text-2xs">
          <tbody className="font-mono text-fg-muted tabular">
            {items.map((i) => (
              <tr key={i.label} className="border-t border-ql-border/60">
                <td className="px-2 py-0.5">{i.label}</td>
                <td className="px-2 py-0.5 text-right">{fmtv(i.value)}</td>
                <td className="px-2 py-0.5">{i.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** One series over time with vertical event markers (e.g. contract rolls). */
export function LineChart({
  points,
  events = [],
  height = 180,
  ariaLabel,
  testId,
}: {
  points: { label: string; value: number }[];
  events?: { index: number; label: string }[];
  height?: number;
  ariaLabel: string;
  testId?: string;
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { top: 14, right: 76, bottom: 22, left: 8 };
  const n = points.length;
  const geo = useMemo(() => {
    const vals = points.map((p) => p.value);
    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    if (!(hi > lo)) {
      lo -= 1;
      hi += 1;
    }
    const x = (i: number) => pad.left + (n <= 1 ? 0 : (i / (n - 1)) * (width - pad.left - pad.right));
    const y = (v: number) => pad.top + (1 - (v - lo) / (hi - lo)) * (height - pad.top - pad.bottom);
    const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
    return { x, y, path, ticks: niceTicks(lo, hi, 4) };
  }, [points, width, height, n, pad.left, pad.right, pad.top, pad.bottom]);
  if (n < 2) return <p className="text-[13px] text-fg-faint">Not enough points.</p>;
  const active = hover !== null ? points[hover] : null;
  return (
    <div ref={ref} className="relative" data-testid={testId}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={ariaLabel}
        onPointerMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const rel = (e.clientX - rect.left - pad.left) / (rect.width - pad.left - pad.right);
          setHover(Math.max(0, Math.min(n - 1, Math.round(rel * (n - 1)))));
        }}
        onPointerLeave={() => setHover(null)}
      >
        {geo.ticks.map((t) => (
          <g key={t}>
            <line x1={pad.left} x2={width - pad.right} y1={geo.y(t)} y2={geo.y(t)} stroke="white" strokeOpacity={0.05} />
            <text x={width - pad.right + 8} y={geo.y(t) + 3} className="fill-fg-faint font-mono text-[10px]">{short(t)}</text>
          </g>
        ))}
        {events.map((e) => (
          <g key={`${e.index}-${e.label}`}>
            <line x1={geo.x(e.index)} x2={geo.x(e.index)} y1={pad.top} y2={height - pad.bottom} stroke="var(--color-ql-warning)" strokeDasharray="3 3" strokeOpacity={0.7} />
            <text x={geo.x(e.index) + 4} y={pad.top + 8} className="fill-ql-warning text-[10px]">{e.label}</text>
          </g>
        ))}
        <path d={geo.path} fill="none" stroke="var(--color-ql)" strokeWidth={2} strokeLinejoin="round" />
        {[0, n - 1].map((i, k) => (
          <text key={`d${k}`} x={geo.x(i)} y={height - 6} textAnchor={k === 0 ? "start" : "end"} className="fill-fg-faint font-mono text-[10px]">
            {points[i]?.label}
          </text>
        ))}
        {hover !== null && active && (
          <g pointerEvents="none">
            <line x1={geo.x(hover)} x2={geo.x(hover)} y1={pad.top} y2={height - pad.bottom} stroke="white" strokeOpacity={0.3} />
            <circle cx={geo.x(hover)} cy={geo.y(active.value)} r={4} fill="var(--color-ql)" stroke="var(--color-ql-surface)" strokeWidth={2} />
          </g>
        )}
      </svg>
      {active && hover !== null && (
        <div
          className="pointer-events-none absolute top-1 z-10 rounded-lg border border-ql-border bg-ql-raised/95 px-3 py-1.5 shadow-xl"
          style={{ left: Math.min(Math.max(0, geo.x(hover) + 10), width - 180) }}
          role="status"
        >
          <p className="font-mono text-2xs text-fg-faint">{active.label}</p>
          <p className="text-[13px] font-medium text-fg tabular">{short(active.value)}</p>
        </div>
      )}
    </div>
  );
}

/** A confidence interval against zero. */
export function Interval({ low, high, unit = "$" }: { low: number; high: number; unit?: string }) {
  const lo = Math.min(low, 0);
  const hi = Math.max(high, 0);
  const span = hi - lo || 1;
  const at = (v: number) => `${((v - lo) / span) * 100}%`;
  const fmtv = (v: number) => `${v < 0 ? "−" : ""}${unit}${short(Math.abs(v))}`;
  return (
    <div className="py-2" aria-label={`Interval ${fmtv(low)} to ${fmtv(high)}`}>
      <div className="relative h-6">
        <div className="absolute top-1/2 h-px w-full bg-white/[0.08]" />
        <div className="absolute top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-ql" style={{ left: at(low), width: `${((high - low) / span) * 100}%` }} />
        <div className="absolute top-0 h-6 w-px bg-ql-muted" style={{ left: at(0) }} title="zero" />
      </div>
      <div className="flex justify-between font-mono text-2xs text-fg-faint tabular">
        <span>{fmtv(low)}</span>
        <span>0</span>
        <span>{fmtv(high)}</span>
      </div>
    </div>
  );
}
