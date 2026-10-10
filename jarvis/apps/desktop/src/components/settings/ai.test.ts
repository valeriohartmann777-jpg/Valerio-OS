import type { AiStatus } from "@jarvis/protocol";
import { describe, expect, it } from "vitest";

import { ROUTE_VIEW, money } from "./AiBilling";

const withCurrency = (display: "USD" | "CHF", rate: number | null) =>
  ({ currency: { display, usd_to_chf: rate } }) as AiStatus;

describe("AI & Billing helpers", () => {
  it("shows USD unless CHF has a rate assumption", () => {
    expect(money(1.5, withCurrency("USD", null))).toBe("$1.50");
    expect(money(1.5, withCurrency("CHF", null))).toBe("$1.50"); // no rate → no invented conversion
    expect(money(10, withCurrency("CHF", 0.8))).toBe("CHF 8.00");
    expect(money(0.00123, withCurrency("USD", null), 4)).toBe("$0.0012");
  });

  it("never prints a number for an unknown amount", () => {
    expect(money(null, withCurrency("USD", null))).toBe("—");
    expect(money(undefined, null)).toBe("—");
  });

  it("labels every route with text and an icon, not colour alone", () => {
    for (const view of Object.values(ROUTE_VIEW)) {
      expect(view.text.length).toBeGreaterThan(0);
      expect(view.icon.length).toBeGreaterThan(0);
    }
    expect(ROUTE_VIEW.paused.text).toBe("Paused");
    expect(ROUTE_VIEW.api.tone).not.toBe(ROUTE_VIEW.plan.tone); // paid is visibly different
  });
});
