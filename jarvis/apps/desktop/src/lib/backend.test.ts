import { describe, expect, it } from "vitest";

import { headerState, type Readiness, type SupervisorStatus } from "./backend";

const ready = (status: Readiness["status"]): Readiness => ({ status, checks: [], checkedAt: 0 });
const sup = (state: SupervisorStatus["state"]) => ({ state }) as SupervisorStatus;

describe("the header's backend state", () => {
  it("says online only with the event stream up AND /health/ready passed", () => {
    expect(headerState("online", ready("ready"), null).text).toBe("Backend online");
    expect(headerState("online", ready("unknown"), null).text).toBe("Checking backend");
    expect(headerState("online", ready("degraded"), null)).toEqual({ text: "Backend degraded", tone: "warning" });
    expect(headerState("online", ready("unreachable"), null).tone).toBe("danger");
  });

  it("uses what the app's supervisor knows while there is no connection", () => {
    expect(headerState("connecting", ready("unknown"), sup("restarting")).text).toBe("Backend offline");
    expect(headerState("connecting", ready("unknown"), sup("crashed")).text).toBe("Backend offline");
    expect(headerState("connecting", ready("unknown"), sup("installing")).text).toBe("Installing backend");
    expect(headerState("connecting", ready("unknown"), sup("starting")).text).toBe("Connecting");
    expect(headerState("offline", ready("unknown"), null).text).toBe("Backend offline");
  });
});
