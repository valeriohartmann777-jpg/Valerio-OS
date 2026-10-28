import { beforeEach, describe, expect, it } from "vitest";

import { TABS, type View, fromHash, normalize, tabOf, toHash, withMemory } from "./routes";

describe("JARVIS 2.0 routes", () => {
  it("has exactly five top-level tabs with an icon, a label and a colour", () => {
    expect(TABS.map((t) => t.label)).toEqual(["Home", "Training Hub", "QuantLab", "Team ULTRON", "Settings"]);
    for (const tab of TABS) {
      expect(tab.icon.length).toBeGreaterThan(0);
      expect(tab.color).toMatch(/^var\(--color-/);
    }
  });

  it("round-trips every view through the address", () => {
    const views: View[] = [
      { name: "home" },
      { name: "mission", id: "m-42" },
      { name: "training", section: "model" },
      { name: "quantlab", section: "data-vault" },
      { name: "ultron", room: "quantlab" },
      { name: "ultron", room: "workshop", mission: "ul-7" },
      { name: "settings", section: "ai" },
    ];
    for (const view of views) expect(fromHash(toHash(view))).toEqual(view);
  });

  it("keeps old links working", () => {
    expect(fromHash("#/learning")).toEqual({ name: "training" });
    expect(fromHash("#/bots")).toEqual({ name: "quantlab", section: "bots" });
    expect(fromHash("#/apex")).toEqual({ name: "quantlab", section: "apex" });
    expect(fromHash("#/mission/abc")).toEqual({ name: "mission", id: "abc" });
    expect(normalize({ name: "learning" })).toEqual({ name: "training" });
    expect(normalize({ name: "bots" })).toEqual({ name: "quantlab", section: "bots" });
  });

  it("lands on Home for an empty or unknown address and rejects odd segments", () => {
    expect(fromHash("")).toEqual({ name: "home" });
    expect(fromHash("#/somewhere-else")).toEqual({ name: "home" });
    expect(fromHash("#/quantlab/<script>")).toEqual({ name: "quantlab" });
  });

  it("puts a mission under the Home tab", () => {
    expect(tabOf({ name: "mission", id: "x" })).toBe("home");
    expect(tabOf({ name: "bots" })).toBe("quantlab");
  });

  describe("remembered sub-section", () => {
    beforeEach(() => {
      const store = new Map<string, string>();
      globalThis.localStorage = {
        getItem: (k: string) => store.get(k) ?? null,
        setItem: (k: string, v: string) => void store.set(k, v),
        removeItem: (k: string) => void store.delete(k),
        clear: () => store.clear(),
        key: () => null,
        length: 0,
      } as Storage;
    });

    it("reopens the section used last when the tab itself is clicked", () => {
      expect(withMemory({ name: "quantlab" })).toEqual({ name: "quantlab" });
      withMemory({ name: "quantlab", section: "data-vault" });
      expect(withMemory({ name: "quantlab" })).toEqual({ name: "quantlab", section: "data-vault" });
      withMemory({ name: "ultron", room: "academy" });
      expect(withMemory({ name: "ultron" })).toEqual({ name: "ultron", room: "academy" });
      expect(withMemory({ name: "home" })).toEqual({ name: "home" }); // Home has no memory
    });
  });
});
