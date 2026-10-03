import type { Mission } from "@jarvis/protocol";
import { useState } from "react";

import { api } from "../../lib/api";
import { isTerminal } from "../../store/reducer";
import { Button } from "../ui/primitives";

export function MissionControls({ mission }: { mission: Mission }) {
  const [busy, setBusy] = useState(false);
  if (isTerminal(mission)) return null;

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      console.warn("mission control failed", error);
    } finally {
      setBusy(false);
    }
  };

  const paused = mission.status === "paused";
  return (
    <div className="flex items-center gap-2">
      <Button
        disabled={busy}
        onClick={() => run(() => (paused ? api.resumeMission(mission.id) : api.pauseMission(mission.id)))}
      >
        {paused ? "Resume" : "Pause"}
      </Button>
      <Button variant="danger" disabled={busy} onClick={() => run(() => api.stopMission(mission.id))}>
        Stop
      </Button>
    </div>
  );
}
