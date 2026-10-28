import type { ApexCandidate, ApexStructure, ApexZone } from "@jarvis/protocol";
import { type KeyboardEvent, type PointerEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "../../ui/icons";
import { barAt, type ChartView, clampView, initialView, niceTicks, panView, timeLabels, zoomView } from "./chartView";

/**
 * The APEX candlestick chart: the analysis' bars at the chosen timeframe with its zones as
 * bands, the confirmed swings (HH/HL/LH/LL), breaks of structure and one candidate's entry,
 * stop and targets. Scroll to zoom, drag to pan, arrows / + / − / 0 on the keyboard. Plain SVG
 * — every number here is also in the tables next to it.
 */

type Bar = [number, number, number, number, number, number];

const UP = "#42d7b6";
const DOWN = "#ff747d";
const INK = "#becfe7";
const FAINT = "rgb(190 207 231 / 0.10)";
const RIGHT = 78; // price axis
const TOP = 10;
const BOTTOM = 26; // time axis

export function ApexChart({
  bars,
  minutes,
  forming,
  zones,
  structure,
  candidate,
  price,
  decimals,
  height = 470,
}: {
  bars: Bar[];
  minutes: number;
  forming: boolean;
  zones: ApexZone[];
  structure?: ApexStructure;
  candidate: ApexCandidate | null;
  price: number | null;
  decimals: number;
  height?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(960);
  const [view, setView] = useState<ChartView>(() => initialView(bars.length));
  const [hover, setHover] = useState<{ x: number; y: number } | null>(null);
  const drag = useRef<{ x: number; end: number } | null>(null);
  const total = bars.length;

  // A new series (another analysis or timeframe) starts at its newest bars.
  const first = bars[0]?.[0] ?? 0;
  useEffect(() => setView(initialView(total)), [total, first, minutes]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(320, Math.round(entry.contentRect.width)));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const plotW = width - RIGHT;
  const plotH = height - TOP - BOTTOM;
  const start = Math.max(0, view.end - view.count);
  const shown = bars.slice(start, view.end);
  const step = plotW / Math.max(1, view.count);

  // Scroll zooms (a native listener: React's wheel events can't stop the page from scrolling).
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = el.getBoundingClientRect();
      const anchor = (event.clientX - rect.left) / Math.max(1, rect.width - RIGHT);
      setView((v) => zoomView(v, total, Math.exp(event.deltaY * 0.0015), anchor));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [total]);

  const levels = useMemo(
    () => (candidate ? [candidate.entry.price, candidate.stop, ...candidate.targets.map((t) => t.price)] : []),
    [candidate],
  );
  const [lo, hi] = useMemo(() => {
    if (shown.length === 0) return [0, 1];
    let low = Math.min(...shown.map((b) => b[3]));
    let high = Math.max(...shown.map((b) => b[2]));
    for (const level of levels) {
      low = Math.min(low, level);
      high = Math.max(high, level);
    }
    const pad = (high - low) * 0.06 || 1;
    return [low - pad, high + pad];
  }, [shown, levels]);

  const y = useCallback((p: number) => TOP + ((hi - p) / (hi - lo)) * plotH, [hi, lo, plotH]);
  const xOf = (i: number) => (i - start) * step + step / 2;
  const fmt = (p: number) => p.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

  const times = useMemo(() => bars.map((b) => b[0]), [bars]);
  const visibleZones = zones.filter((z) => z.high >= lo && z.low <= hi);
  const swings = (structure?.swings ?? [])
    .map((s) => ({ ...s, i: barAt(times, s.t, minutes) }))
    .filter((s) => s.i >= start && s.i < view.end);
  const events = (structure?.events ?? [])
    .map((e) => ({ ...e, i: barAt(times, e.t, minutes) }))
    .filter((e) => e.i >= start && e.i < view.end);

  const hoverIndex = hover ? Math.min(view.end - 1, Math.max(start, start + Math.floor(hover.x / step))) : null;
  const hovered = hoverIndex != null ? bars[hoverIndex] : undefined;
  const hoverPrice = hover ? hi - ((hover.y - TOP) / plotH) * (hi - lo) : null;

  const local = (event: PointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const scale = width / rect.width;
    return { x: (event.clientX - rect.left) * scale, y: (event.clientY - rect.top) * scale };
  };
  const onPointerDown = (event: PointerEvent<SVGSVGElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { x: local(event).x, end: view.end };
  };
  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    const p = local(event);
    if (drag.current) {
      const moved = (drag.current.x - p.x) / step;
      setView(clampView({ end: drag.current.end + moved, count: view.count }, total));
    }
    setHover(p.x <= plotW && p.y >= TOP && p.y <= TOP + plotH ? p : null);
  };
  const onPointerUp = () => {
    drag.current = null;
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const by = Math.max(1, Math.round(view.count * 0.1));
    const actions: Record<string, () => ChartView> = {
      ArrowLeft: () => panView(view, total, -by),
      ArrowRight: () => panView(view, total, by),
      "+": () => zoomView(view, total, 0.8),
      "=": () => zoomView(view, total, 0.8),
      "-": () => zoomView(view, total, 1.25),
      "0": () => initialView(total),
      Home: () => initialView(total),
    };
    const act = actions[event.key];
    if (act) {
      event.preventDefault();
      setView(act());
    }
  };

  if (total === 0) {
    return (
      <div className="grid h-[200px] place-items-center rounded-xl bg-canvas-2 text-[13px] text-fg-muted" data-testid="apex-chart" data-bars={0}>
        No bars to draw.
      </div>
    );
  }

  const priceY = price != null && price >= lo && price <= hi ? y(price) : null;
  // ticks under the price badge are left out so nothing overlaps
  const ticks = niceTicks(lo, hi, Math.max(3, Math.round(plotH / 70))).filter((t) => priceY == null || Math.abs(y(t) - priceY) > 14);
  const labels = timeLabels(times, start, view.end, Math.max(2, Math.floor(plotW / 120)));
  // Zone names sit inside the plot at its right edge (the axis keeps the prices).
  const zoneLabels = spread(
    visibleZones.map((z) => ({
      y: y((Math.min(z.high, hi) + Math.max(z.low, lo)) / 2),
      text: z.id,
      color: z.side === "support" ? UP : DOWN,
    })),
    TOP + 8,
    TOP + plotH - 8,
    16,
  );
  const candidateLines = candidate
    ? [
        { price: candidate.entry.price, color: "#ffe16b", text: `Entry ${fmt(candidate.entry.price)}` },
        { price: candidate.stop, color: DOWN, text: `Stop ${fmt(candidate.stop)}` },
        ...candidate.targets.map((t, k) => ({ price: t.price, color: "#4cdfac", text: `TP${k + 1} ${fmt(t.price)} · ${t.rr}R` })),
      ]
    : [];
  const span = `${new Date(first * 1000).toLocaleString()} – ${new Date((bars[total - 1]?.[0] ?? 0) * 1000).toLocaleString()}`;

  return (
    <div
      ref={box}
      className="relative select-none rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-cta"
      tabIndex={0}
      onKeyDown={onKeyDown}
      role="group"
      aria-label={`Candlestick chart, ${minutes}-minute bars, ${span}. Arrow keys pan, plus and minus zoom, 0 resets.`}
      data-testid="apex-chart"
      data-minutes={minutes}
      data-bars={shown.length}
      data-total={total}
    >
      <div className="absolute top-2 right-[86px] z-10 flex gap-1">
        <ChartButton label="Zoom in" testId="apex-chart-zoom-in" onClick={() => setView(zoomView(view, total, 0.7))}>
          <Icon name="plus" size={13} />
        </ChartButton>
        <ChartButton label="Zoom out" testId="apex-chart-zoom-out" onClick={() => setView(zoomView(view, total, 1.4))}>
          <span className="text-[14px] leading-none font-black">−</span>
        </ChartButton>
        <ChartButton label="Back to the newest bars" testId="apex-chart-reset" onClick={() => setView(initialView(total))}>
          <Icon name="back" size={13} />
        </ChartButton>
      </div>
      <svg
        ref={svg}
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        className="block cursor-crosshair touch-none"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => {
          drag.current = null;
          setHover(null);
        }}
        onDoubleClick={() => setView(initialView(total))}
        aria-hidden="true"
      >
        <rect x={0} y={0} width={width} height={height} rx={12} className="fill-canvas-2" />
        {ticks.map((t) => (
          <g key={`tick-${t}`}>
            <line x1={0} x2={plotW} y1={y(t)} y2={y(t)} stroke={FAINT} />
            <text x={plotW + 8} y={y(t) + 4} fill="rgb(190 207 231 / 0.55)" className="text-[11px]">
              {fmt(t)}
            </text>
          </g>
        ))}
        {visibleZones.map((z) => {
          const support = z.side === "support";
          const y1 = y(Math.min(z.high, hi));
          const y2 = y(Math.max(z.low, lo));
          return (
            <rect
              key={z.id}
              x={0}
              y={y1}
              width={plotW}
              height={Math.max(2, y2 - y1)}
              fill={support ? "rgb(66 215 182 / 0.13)" : "rgb(255 116 125 / 0.13)"}
              stroke={support ? "rgb(66 215 182 / 0.5)" : "rgb(255 116 125 / 0.5)"}
              strokeWidth={1}
              data-testid="apex-chart-zone"
            />
          );
        })}
        {shown.map(([t, o, h, l, c], k) => {
          const i = start + k;
          const up = c >= o;
          const x = xOf(i);
          const color = up ? UP : DOWN;
          const last = forming && i === total - 1;
          const bodyW = Math.max(1, step * 0.66);
          return (
            <g key={t} opacity={last ? 0.55 : 1}>
              <line x1={x} x2={x} y1={y(h)} y2={y(l)} stroke={color} strokeWidth={1} />
              <rect
                x={x - bodyW / 2}
                y={y(Math.max(o, c))}
                width={bodyW}
                height={Math.max(1, Math.abs(y(o) - y(c)))}
                fill={last ? "none" : color}
                stroke={color}
                strokeWidth={last ? 1 : 0}
              />
            </g>
          );
        })}
        {swings.map((s) => {
          const above = s.kind === "high";
          const x = xOf(s.i);
          const py = y(s.price) + (above ? -7 : 7);
          return (
            <g key={`sw-${s.kind}-${s.t}`} data-testid="apex-chart-swing">
              <path d={above ? `M${x - 4} ${py - 5} L${x + 4} ${py - 5} L${x} ${py}` : `M${x - 4} ${py + 5} L${x + 4} ${py + 5} L${x} ${py}`} fill={INK} />
              {s.label && (
                <text x={x} y={above ? py - 8 : py + 16} textAnchor="middle" fill={INK} className="text-[10px] font-bold">
                  {s.label}
                </text>
              )}
            </g>
          );
        })}
        {events.map((e) => {
          const x = xOf(e.i);
          return (
            <g key={`ev-${e.t}-${e.type}`}>
              <line x1={Math.max(0, x - step * 6)} x2={x} y1={y(e.level)} y2={y(e.level)} stroke="#ffe16b" strokeDasharray="3 3" />
              <text x={x + 4} y={y(e.level) + (e.direction === "up" ? -4 : 12)} fill="#ffe16b" className="text-[10px] font-bold">
                {e.type} {e.direction === "up" ? "↑" : "↓"}
              </text>
            </g>
          );
        })}
        {priceY != null && <line x1={0} x2={plotW} y1={priceY} y2={priceY} stroke={INK} strokeDasharray="4 4" strokeWidth={1} />}
        {candidate && (
          <g data-testid="apex-chart-candidate">
            {candidateLines.map((l) => (
              <line key={`l-${l.text}`} x1={0} x2={plotW} y1={y(l.price)} y2={y(l.price)} stroke={l.color} strokeWidth={1.6} />
            ))}
            {spread(
              candidateLines.map((l) => ({ ...l, y: y(l.price) })),
              TOP + 14,
              TOP + plotH,
            ).map((l) => (
              <text key={`t-${l.text}`} x={8} y={l.y - 4} fill={l.color} className="text-[12px] font-bold">
                {l.text}
              </text>
            ))}
          </g>
        )}
        {zoneLabels.map((l) => (
          <g key={`r-${l.text}`}>
            <rect x={plotW - 36} y={l.y - 8} width={30} height={16} rx={4} fill="rgb(10 21 48 / 0.85)" />
            <text x={plotW - 21} y={l.y + 4} textAnchor="middle" fill={l.color} className="text-[11px] font-bold">
              {l.text}
            </text>
          </g>
        ))}
        {priceY != null && price != null && (
          <g data-testid="apex-chart-price">
            <rect x={plotW + 2} y={priceY - 10} width={RIGHT - 4} height={20} rx={4} fill="#becfe7" />
            <text x={plotW + 8} y={priceY + 4} fill="#0a1530" className="text-[11px] font-bold">
              {fmt(price)}
            </text>
          </g>
        )}
        {labels.map((l) => (
          <text key={`x-${l.i}`} x={xOf(l.i)} y={height - 8} textAnchor="middle" fill="rgb(190 207 231 / 0.6)" className="text-[11px]">
            {l.text}
          </text>
        ))}
        {hover && hovered && hoverPrice != null && (
          <g data-testid="apex-chart-crosshair" pointerEvents="none">
            <line x1={xOf(hoverIndex ?? 0)} x2={xOf(hoverIndex ?? 0)} y1={TOP} y2={TOP + plotH} stroke="rgb(248 251 255 / 0.35)" />
            <line x1={0} x2={plotW} y1={hover.y} y2={hover.y} stroke="rgb(248 251 255 / 0.35)" />
            <rect x={plotW + 2} y={hover.y - 10} width={RIGHT - 4} height={20} rx={4} fill="#22314f" />
            <text x={plotW + 8} y={hover.y + 4} fill="#f8fbff" className="text-[11px] font-bold">
              {fmt(hoverPrice)}
            </text>
            <rect x={8} y={TOP + 4} width={Math.min(plotW - 16, 470)} height={22} rx={6} fill="rgb(10 21 48 / 0.85)" />
            <text x={16} y={TOP + 19} fill="#f8fbff" className="text-[12px]">
              {`${new Date(hovered[0] * 1000).toLocaleString()} · O ${fmt(hovered[1])} H ${fmt(hovered[2])} L ${fmt(hovered[3])} C ${fmt(hovered[4])}`}
              {forming && hoverIndex === total - 1 ? " · forming" : ""}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}

function ChartButton({ label, testId, onClick, children }: { label: string; testId: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="grid h-7 w-7 place-items-center rounded-md border border-hairline-strong bg-surface-2/90 text-fg-muted hover:text-fg"
      data-testid={testId}
    >
      {children}
    </button>
  );
}

/** Labels sorted by height and pushed apart so none overlaps (at least 14 px between). */
function spread<T extends { y: number }>(labels: T[], min: number, max: number, gap = 14): T[] {
  const out = [...labels].sort((a, b) => a.y - b.y).map((l) => ({ ...l }));
  for (let i = 0; i < out.length; i++) {
    const prev = out[i - 1];
    const cur = out[i]!;
    cur.y = Math.max(cur.y, min, prev ? prev.y + gap : -Infinity);
  }
  const overflow = out.length ? out[out.length - 1]!.y - max : 0;
  if (overflow > 0) for (const l of out) l.y -= overflow;
  return out;
}
