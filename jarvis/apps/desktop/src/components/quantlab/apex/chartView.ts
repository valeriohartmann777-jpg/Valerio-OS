/**
 * The pure maths of the APEX chart: which bars are visible (zoom / pan), price ticks, time
 * labels and finding the bar that contains a moment. Kept apart from the SVG so it is tested.
 */

/** ``end`` = index after the last visible bar; ``count`` = number of visible bars. */
export interface ChartView {
  end: number;
  count: number;
}

export const MIN_BARS = 20;

export function initialView(total: number, preferred = 140): ChartView {
  return clampView({ end: total, count: Math.min(total, preferred) }, total);
}

export function clampView(view: ChartView, total: number): ChartView {
  if (total <= 0) return { end: 0, count: 0 };
  const count = Math.round(Math.min(total, Math.max(Math.min(MIN_BARS, total), view.count)));
  const end = Math.round(Math.min(total, Math.max(count, view.end)));
  return { end, count };
}

/** Zoom by ``factor`` (< 1 = in) keeping the bar under ``anchor`` (0 = left … 1 = right) fixed. */
export function zoomView(view: ChartView, total: number, factor: number, anchor = 1): ChartView {
  const a = Math.min(1, Math.max(0, anchor));
  const at = view.end - view.count + a * view.count;
  const count = view.count * factor;
  return clampView({ end: at + (1 - a) * count, count }, total);
}

/** Move by ``bars`` (positive = later). */
export function panView(view: ChartView, total: number, bars: number): ChartView {
  return clampView({ end: view.end + bars, count: view.count }, total);
}

/** Round price steps (1, 2, 2.5, 5 × 10^k) for about ``target`` ticks in [lo, hi]. */
export function niceTicks(lo: number, hi: number, target = 6): number[] {
  if (!(hi > lo) || !Number.isFinite(lo) || !Number.isFinite(hi)) return [];
  const raw = (hi - lo) / Math.max(1, target);
  const power = 10 ** Math.floor(Math.log10(raw));
  // the round step whose tick count comes closest to the target
  const step = [1, 2, 2.5, 5, 10]
    .map((m) => m * power)
    .reduce((best, s) => (Math.abs((hi - lo) / s - target) < Math.abs((hi - lo) / best - target) ? s : best));
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** Index of the bar whose span [t, t + minutes) contains ``moment``; -1 when none does. */
export function barAt(times: number[], moment: number, minutes: number): number {
  let lo = 0;
  let hi = times.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if ((times[mid] ?? Infinity) <= moment) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  if (found < 0) return -1;
  return moment < (times[found] ?? 0) + minutes * 60 ? found : -1;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** About ``max`` evenly spaced time labels for bars [start, end); dates where the day changes. */
export function timeLabels(times: number[], start: number, end: number, max = 6): { i: number; text: string }[] {
  const n = end - start;
  if (n <= 0) return [];
  const every = Math.max(1, Math.ceil(n / max));
  const out: { i: number; text: string }[] = [];
  let lastDay = "";
  for (let i = start; i < end; i += every) {
    const d = new Date((times[i] ?? 0) * 1000);
    const day = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const clock = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    out.push({ i, text: day !== lastDay ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}. ${clock}` : clock });
    lastDay = day;
  }
  return out;
}

export const TIMEFRAMES = [
  { minutes: 1, label: "1m", name: "1 minute" },
  { minutes: 2, label: "2m", name: "2 minutes" },
  { minutes: 3, label: "3m", name: "3 minutes" },
  { minutes: 5, label: "5m", name: "5 minutes" },
  { minutes: 10, label: "10m", name: "10 minutes" },
  { minutes: 15, label: "15m", name: "15 minutes" },
  { minutes: 60, label: "1h", name: "1 hour" },
  { minutes: 240, label: "4h", name: "4 hours" },
  { minutes: 1440, label: "D1", name: "New York trading days" },
] as const;

/** The structure APEX computed that fits a chart timeframe best (15-minute or 1-hour swings). */
export function structureKey(minutes: number): "15" | "60" {
  return minutes >= 60 ? "60" : "15";
}
