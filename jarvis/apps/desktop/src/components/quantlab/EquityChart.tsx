import type { QlEquityPoint } from "@jarvis/protocol";
import { useEffect, useMemo, useRef, useState } from "react";

import { cx } from "../ui/primitives";
import { money, pct, utc } from "./ui";

/**
 * Equity net of costs with its drawdown underneath — only ever drawn from a real
 * run's points. Two panels, one x (bars in order; gaps between sessions are
 * compressed), never two y-scales on one panel. The out-of-sample segment is
 * marked by a divider and a faint wash. Hover or arrow keys move a crosshair.
 */

const EQUITY_H = 210;
const DD_H = 84;
const GAP = 18;
const PAD = { top: 22, right: 76, bottom: 22, left: 8 };

export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!(max > min)) return [min];
  const raw = (max - min) / count;
  const step = 10 ** Math.floor(Math.log10(raw));
  const nice = [1, 2, 2.5, 5, 10].map((m) => m * step).find((s) => (max - min) / s <= count) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(min / nice) * nice; v <= max + 1e-9; v += nice) out.push(Number(v.toPrecision(12)));
  return out;
}

function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
  if (abs >= 10_000) return `${(value / 1000).toFixed(0)}K`;
  if (abs >= 1000) return `${(value / 1000).toFixed(1)}K`;
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function EquityChart({
  points,
  initialCash,
  currency,
  oosStart,
  totalPoints,
}: {
  points: QlEquityPoint[];
  initialCash: number;
  currency: string;
  oosStart: string | null;
  totalPoints: number;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => entry && setWidth(Math.max(320, entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const geo = useMemo(() => {
    const n = points.length;
    const plotW = width - PAD.left - PAD.right;
    const x = (i: number) => PAD.left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const values = points.map((p) => p.equity);
    let lo = Math.min(initialCash, ...values);
    let hi = Math.max(initialCash, ...values);
    if (hi - lo < 1e-9) {
      lo -= 1;
      hi += 1;
    }
    const pad = (hi - lo) * 0.08;
    lo -= pad;
    hi += pad;
    const eqTop = PAD.top;
    const y = (v: number) => eqTop + (1 - (v - lo) / (hi - lo)) * EQUITY_H;
    const ddTop = eqTop + EQUITY_H + GAP;
    const ddMin = Math.min(-0.0001, ...points.map((p) => p.drawdown ?? 0));
    const yd = (v: number) => ddTop + (v / ddMin) * DD_H;
    const oos = oosStart ? points.findIndex((p) => p.ts >= oosStart) : -1;
    const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join("");
    const ddLine = points
      .map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${yd(p.drawdown ?? 0).toFixed(1)}`)
      .join("");
    const dd = `${ddLine}L${x(n - 1).toFixed(1)},${ddTop}L${x(0).toFixed(1)},${ddTop}Z`;
    return {
      n,
      x,
      y,
      yd,
      ddTop,
      ddMin,
      line,
      dd,
      ddLine,
      oos,
      ticks: niceTicks(lo, hi),
      ddTicks: niceTicks(ddMin, 0, 2),
      height: ddTop + DD_H + PAD.bottom,
      plotRight: width - PAD.right,
    };
  }, [points, width, initialCash, oosStart]);

  const last = points[points.length - 1];
  if (points.length < 2 || !last) {
    return <p className="text-[13px] text-fg-faint">Not enough points to draw a curve.</p>;
  }

  const pick = (clientX: number, rect: DOMRect) => {
    const rel = (clientX - rect.left - PAD.left) / (rect.width - PAD.left - PAD.right);
    setHover(Math.max(0, Math.min(geo.n - 1, Math.round(rel * (geo.n - 1)))));
  };
  const active = hover !== null ? (points[hover] ?? null) : null;
  const dateTicks = [0, Math.floor((geo.n - 1) / 2), geo.n - 1];

  return (
    <div ref={wrap} className="relative" data-testid="ql-equity-chart">
      <svg
        width={width}
        height={geo.height}
        role="img"
        aria-label={`Equity net of costs, ${points.length} points shown of ${totalPoints}, ending at ${money(last.equity, currency, false)}`}
        tabIndex={0}
        className="block touch-none outline-none focus-visible:ring-1 focus-visible:ring-ql/50"
        onPointerMove={(e) => pick(e.clientX, e.currentTarget.getBoundingClientRect())}
        onPointerLeave={() => setHover(null)}
        onKeyDown={(e) => {
          const step = e.shiftKey ? 10 : 1;
          if (e.key === "ArrowRight") setHover((h) => Math.min(geo.n - 1, (h ?? -1) + step));
          else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? geo.n) - step));
          else if (e.key === "Home") setHover(0);
          else if (e.key === "End") setHover(geo.n - 1);
          else if (e.key === "Escape") setHover(null);
          else return;
          e.preventDefault();
        }}
        onBlur={() => setHover(null)}
      >
        {/* OOS wash + divider */}
        {geo.oos > 0 && (
          <g>
            <rect
              x={geo.x(geo.oos)}
              y={PAD.top - 14}
              width={geo.plotRight - geo.x(geo.oos)}
              height={geo.ddTop + DD_H - PAD.top + 14}
              fill="white"
              opacity={0.025}
            />
            <line
              x1={geo.x(geo.oos)}
              x2={geo.x(geo.oos)}
              y1={PAD.top - 14}
              y2={geo.ddTop + DD_H}
              stroke="var(--color-ql-muted)"
              strokeWidth={1}
              opacity={0.55}
            />
            <text x={geo.x(geo.oos) - 6} y={PAD.top - 4} textAnchor="end" className="fill-fg-faint text-[10px] tracking-[0.14em]">
              TRAIN
            </text>
            <text x={geo.x(geo.oos) + 6} y={PAD.top - 4} className="fill-fg-muted text-[10px] tracking-[0.14em]">
              OUT-OF-SAMPLE
            </text>
          </g>
        )}

        {/* equity grid + axis */}
        {geo.ticks.map((t) => (
          <g key={`t${t}`}>
            <line x1={PAD.left} x2={geo.plotRight} y1={geo.y(t)} y2={geo.y(t)} stroke="white" strokeOpacity={0.05} />
            {/* the end value is labelled directly; a tick right next to it would collide */}
            {Math.abs(geo.y(t) - geo.y(last.equity)) > 14 && (
              <text x={geo.plotRight + 8} y={geo.y(t) + 3} className="fill-fg-faint font-mono text-[10px] tabular">
                {compact(t)}
              </text>
            )}
          </g>
        ))}
        <line
          x1={PAD.left}
          x2={geo.plotRight}
          y1={geo.y(initialCash)}
          y2={geo.y(initialCash)}
          stroke="var(--color-ql-muted)"
          strokeOpacity={0.45}
        />
        <text x={PAD.left + 2} y={geo.y(initialCash) - 5} className="fill-fg-faint text-[10px]">
          start {compact(initialCash)}
        </text>
        <path d={geo.line} fill="none" stroke="var(--color-ql)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={geo.x(geo.n - 1)} cy={geo.y(last.equity)} r={4} fill="var(--color-ql)" stroke="var(--color-ql-surface)" strokeWidth={2} />
        <text x={geo.plotRight + 8} y={geo.y(last.equity) + 4} className="fill-fg text-[11px] font-medium">
          {compact(last.equity)}
        </text>

        {/* drawdown */}
        <text x={PAD.left} y={geo.ddTop - 5} className="fill-fg-faint text-[10px] tracking-[0.14em]">
          DRAWDOWN
        </text>
        {geo.ddTicks.map((t) => (
          <g key={`d${t}`}>
            <line x1={PAD.left} x2={geo.plotRight} y1={geo.yd(t)} y2={geo.yd(t)} stroke="white" strokeOpacity={0.05} />
            <text x={geo.plotRight + 8} y={geo.yd(t) + 3} className="fill-fg-faint font-mono text-[10px] tabular">
              {t === 0 ? "0%" : pct(t, 0)}
            </text>
          </g>
        ))}
        <path d={geo.dd} fill="var(--color-ql-muted)" fillOpacity={0.1} stroke="none" />
        <path
          d={geo.ddLine}
          fill="none"
          stroke="var(--color-ql-muted)"
          strokeWidth={1.5}
          strokeLinejoin="round"
        />

        {/* dates */}
        {dateTicks.map((i, k) => (
          <text
            key={`x${i}`}
            x={geo.x(i)}
            y={geo.height - 6}
            textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"}
            className="fill-fg-faint font-mono text-[10px]"
          >
            {utc(points[i]?.ts).replace(" UTC", "")}
          </text>
        ))}

        {/* crosshair */}
        {active && hover !== null && (
          <g pointerEvents="none">
            <line
              x1={geo.x(hover)}
              x2={geo.x(hover)}
              y1={PAD.top - 14}
              y2={geo.ddTop + DD_H}
              stroke="white"
              strokeOpacity={0.35}
            />
            <circle cx={geo.x(hover)} cy={geo.y(active.equity)} r={4} fill="var(--color-ql)" stroke="var(--color-ql-surface)" strokeWidth={2} />
            <circle cx={geo.x(hover)} cy={geo.yd(active.drawdown ?? 0)} r={4} fill="var(--color-ql-muted)" stroke="var(--color-ql-surface)" strokeWidth={2} />
          </g>
        )}
      </svg>

      {active && hover !== null && (
        <div
          className="pointer-events-none absolute top-2 z-10 min-w-[170px] rounded-lg border border-ql-border bg-ql-raised/95 px-3 py-2 shadow-xl"
          style={{
            left: Math.min(Math.max(0, geo.x(hover) + 12), width - 190),
          }}
          role="status"
        >
          <p className="text-[13px] font-semibold text-fg tabular">{money(active.equity, currency, false)}</p>
          <p className="mt-0.5 flex items-center gap-2 text-2xs text-fg-muted">
            <span className="inline-block h-[2px] w-3 rounded-full bg-ql-muted" />
            drawdown {active.drawdown === null ? "n/a" : pct(active.drawdown, 2, false)}
          </p>
          <p className="mt-1 font-mono text-2xs text-fg-faint">
            {utc(active.ts)} · {active.segment === "oos" ? "out-of-sample" : "train"}
          </p>
        </div>
      )}

      <div className="mt-2 flex items-center justify-between text-2xs text-fg-faint">
        <span>
          Equity = cash + shares × close, after fees and slippage · {points.length < totalPoints
            ? `${points.length} of ${totalPoints} bars drawn (peaks and troughs kept)`
            : `${totalPoints} bars`}
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
                <th className="px-2 py-1 font-medium">Bar (UTC)</th>
                <th className="px-2 py-1 text-right font-medium">Equity</th>
                <th className="px-2 py-1 text-right font-medium">Drawdown</th>
                <th className="px-2 py-1 font-medium">Segment</th>
              </tr>
            </thead>
            <tbody className="font-mono text-fg-muted tabular">
              {points.map((p) => (
                <tr key={p.ts} className={cx("border-t border-ql-border/60")}>
                  <td className="px-2 py-0.5">{utc(p.ts)}</td>
                  <td className="px-2 py-0.5 text-right">{p.equity.toFixed(2)}</td>
                  <td className="px-2 py-0.5 text-right">{p.drawdown === null ? "—" : pct(p.drawdown, 2, false)}</td>
                  <td className="px-2 py-0.5">{p.segment}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
