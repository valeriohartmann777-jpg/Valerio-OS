import { describe, expect, it } from "vitest";

import { barAt, clampView, initialView, niceTicks, panView, structureKey, timeLabels, zoomView } from "./chartView";

describe("APEX chart view", () => {
  it("starts at the newest bars and never shows past the ends", () => {
    expect(initialView(500)).toEqual({ end: 500, count: 140 });
    expect(initialView(50)).toEqual({ end: 50, count: 50 });
    expect(clampView({ end: 900, count: 5 }, 500)).toEqual({ end: 500, count: 20 });
    expect(clampView({ end: -10, count: 100 }, 500)).toEqual({ end: 100, count: 100 });
    expect(clampView({ end: 3, count: 3 }, 0)).toEqual({ end: 0, count: 0 });
  });

  it("zooms around the bar under the pointer", () => {
    const view = { end: 400, count: 100 }; // bars 300 … 399
    const inAtMiddle = zoomView(view, 500, 0.5, 0.5); // bar 350 stays in the middle
    expect(inAtMiddle).toEqual({ end: 375, count: 50 });
    expect(zoomView(view, 500, 0.5, 1)).toEqual({ end: 400, count: 50 }); // right edge fixed
    expect(zoomView(view, 500, 100, 0.5)).toEqual({ end: 500, count: 500 }); // all bars at most
  });

  it("pans and stops at both ends", () => {
    const view = { end: 400, count: 100 };
    expect(panView(view, 500, -50)).toEqual({ end: 350, count: 100 });
    expect(panView(view, 500, 1000)).toEqual({ end: 500, count: 100 });
    expect(panView(view, 500, -1000)).toEqual({ end: 100, count: 100 });
  });

  it("puts price ticks on round numbers", () => {
    expect(niceTicks(2271.3, 2290.9, 6)).toEqual([2272.5, 2275, 2277.5, 2280, 2282.5, 2285, 2287.5, 2290]);
    expect(niceTicks(20012, 20140, 5)).toEqual([20025, 20050, 20075, 20100, 20125]);
    expect(niceTicks(5, 5)).toEqual([]);
  });

  it("finds the bar that contains a moment", () => {
    const times = [0, 900, 1800, 3600]; // a gap after 1800
    expect(barAt(times, 950, 15)).toBe(1);
    expect(barAt(times, 1800, 15)).toBe(2);
    expect(barAt(times, 2800, 15)).toBe(-1); // inside the gap
    expect(barAt(times, -5, 15)).toBe(-1);
    expect(barAt(times, 4000, 15)).toBe(3);
  });

  it("labels time, with the date where the day changes", () => {
    const start = new Date(2026, 9, 27, 22, 0).getTime() / 1000;
    const times = Array.from({ length: 12 }, (_, k) => start + k * 3600);
    const labels = timeLabels(times, 0, 12, 4);
    expect(labels.map((l) => l.i)).toEqual([0, 3, 6, 9]);
    expect(labels[0]?.text).toBe("27.10. 22:00");
    expect(labels[1]?.text).toBe("28.10. 01:00");
    expect(labels[2]?.text).toBe("04:00");
  });

  it("uses 1-hour structure on hourly charts", () => {
    expect([1, 5, 15, 60, 240].map(structureKey)).toEqual(["15", "15", "15", "60", "60"]);
  });
});
