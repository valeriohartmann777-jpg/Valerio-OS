import type { ApexCandidate, ApexZone } from "@jarvis/protocol";

/**
 * 15-minute candles with the APEX zones as bands and one candidate's entry, stop and targets
 * as lines. Plain SVG (no chart library); the same numbers are in the tables next to it.
 */
export function ApexChart({
  bars,
  zones,
  candidate,
  price,
  decimals,
}: {
  bars: [number, number, number, number, number][];
  zones: ApexZone[];
  candidate: ApexCandidate | null;
  price: number | null;
  decimals: number;
}) {
  const W = 960;
  const H = 340;
  const right = 78; // room for labels
  const top = 10;
  const bottom = 22;
  if (bars.length === 0) return null;
  const lows = bars.map((b) => b[3]);
  const highs = bars.map((b) => b[2]);
  let lo = Math.min(...lows);
  let hi = Math.max(...highs);
  const pad = (hi - lo) * 0.08 || 1;
  lo -= pad;
  hi += pad;
  const levels = candidate ? [candidate.entry.price, candidate.stop, ...candidate.targets.map((t) => t.price)] : [];
  for (const level of levels) {
    lo = Math.min(lo, level - pad / 2);
    hi = Math.max(hi, level + pad / 2);
  }
  const visible = zones.filter((z) => z.high >= lo && z.low <= hi);
  const plotW = W - right;
  const step = plotW / bars.length;
  const y = (p: number) => top + ((hi - p) / (hi - lo)) * (H - top - bottom);
  const fmt = (p: number) => p.toFixed(decimals);
  const first = new Date((bars[0]?.[0] ?? 0) * 1000);
  const last = new Date((bars[bars.length - 1]?.[0] ?? 0) * 1000);
  const label = `15-minute chart from ${first.toLocaleString()} to ${last.toLocaleString()}, ${visible.length} zones shown`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={label} data-testid="apex-chart">
      <rect x={0} y={0} width={W} height={H} rx={12} className="fill-canvas-2" />
      {visible.map((z) => {
        const support = z.side === "support";
        const y1 = y(Math.min(z.high, hi));
        const y2 = y(Math.max(z.low, lo));
        return (
          <g key={z.id} data-testid="apex-chart-zone">
            <rect
              x={0}
              y={y1}
              width={plotW}
              height={Math.max(2, y2 - y1)}
              fill={support ? "rgb(66 215 182 / 0.16)" : "rgb(255 116 125 / 0.16)"}
              stroke={support ? "rgb(66 215 182 / 0.55)" : "rgb(255 116 125 / 0.55)"}
              strokeWidth={1}
            />
          </g>
        );
      })}
      {bars.map(([t, o, h, l, c], i) => {
        const up = c >= o;
        const x = i * step + step / 2;
        const color = up ? "#42d7b6" : "#ff747d";
        return (
          <g key={t}>
            <line x1={x} x2={x} y1={y(h)} y2={y(l)} stroke={color} strokeWidth={1} />
            <rect
              x={x - Math.max(1, step * 0.32)}
              y={y(Math.max(o, c))}
              width={Math.max(2, step * 0.64)}
              height={Math.max(1, Math.abs(y(o) - y(c)))}
              fill={color}
            />
          </g>
        );
      })}
      {price != null && <line x1={0} x2={plotW} y1={y(price)} y2={y(price)} stroke="#becfe7" strokeDasharray="4 4" strokeWidth={1} />}
      {spread(
        [
          ...visible.map((z) => ({ y: y((Math.min(z.high, hi) + Math.max(z.low, lo)) / 2), text: z.id, color: "#becfe7" })),
          ...(price != null ? [{ y: y(price), text: fmt(price), color: "#f8fbff" }] : []),
        ],
        top + 6,
        H - bottom,
      ).map((l) => (
        <text key={`r-${l.text}`} x={plotW + 6} y={l.y + 4} fill={l.color} className="text-[12px] font-bold">
          {l.text}
        </text>
      ))}
      {candidate && (
        <g data-testid="apex-chart-candidate">
          {[
            { price: candidate.entry.price, color: "#ffe16b", text: `Entry ${fmt(candidate.entry.price)}` },
            { price: candidate.stop, color: "#ff747d", text: `Stop ${fmt(candidate.stop)}` },
            ...candidate.targets.map((t, k) => ({ price: t.price, color: "#4cdfac", text: `TP${k + 1} ${fmt(t.price)}` })),
          ].map((l) => (
            <line key={`l-${l.text}`} x1={0} x2={plotW} y1={y(l.price)} y2={y(l.price)} stroke={l.color} strokeWidth={1.6} />
          ))}
          {spread(
            [
              { y: y(candidate.entry.price), color: "#ffe16b", text: `Entry ${fmt(candidate.entry.price)}` },
              { y: y(candidate.stop), color: "#ff747d", text: `Stop ${fmt(candidate.stop)}` },
              ...candidate.targets.map((t, k) => ({ y: y(t.price), color: "#4cdfac", text: `TP${k + 1} ${fmt(t.price)}` })),
            ],
            top + 12,
            H - bottom,
          ).map((l) => (
            <text key={`t-${l.text}`} x={8} y={l.y - 4} fill={l.color} className="text-[12px] font-bold">
              {l.text}
            </text>
          ))}
        </g>
      )}
    </svg>
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
