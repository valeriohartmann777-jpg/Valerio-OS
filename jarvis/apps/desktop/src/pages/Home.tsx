import type { BlOptions, UlOverview } from "@jarvis/protocol";
import { AnimatePresence } from "motion/react";
import { useEffect, useState } from "react";

import { ActivityStream } from "../components/activity/ActivityStream";
import { ApprovalCard } from "../components/approval/ApprovalCard";
import { Conversation } from "../components/conversation/Conversation";
import { CoreStrip } from "../components/core/CoreStage";
import { ActiveMission } from "../components/mission/ActiveMission";
import { ContextPanel } from "../components/panels/ContextPanel";
import { MissionsPanel } from "../components/panels/MissionsPanel";
import { Icon } from "../components/ui/icons";
import { PageHeader, Panel, StatusPill, Tile } from "../components/ui/shell";
import { api } from "../lib/api";
import { learningLabel } from "../lib/format";
import { go } from "../lib/history";
import { featuredMission } from "../store/reducer";
import { useJarvis, useNow } from "../store/store";

/**
 * Home — talking to JARVIS is the main thing (brief §4). Left: the conversation with JARVIS's
 * real state; the composer is the command bar below. Right: what needs you now (approval or
 * the active mission with its controls), the three areas with their real status, activity.
 */
export function Home() {
  const now = useNow(1000);
  const approval = useJarvis((s) => s.approvals[0] ?? null);
  const mission = useJarvis((s) => featuredMission(s, now));
  const brain = useJarvis((s) => s.brain);

  return (
    <div className="flex h-full min-h-0 flex-col px-7" style={{ "--area": "var(--color-home)" } as React.CSSProperties}>
      <div className="mx-auto flex h-full min-h-0 w-full max-w-[1500px] flex-col">
        <PageHeader
          eyebrow="JARVIS / Command center"
          title="Welcome back."
          subtitle="Ask JARVIS, give it a task, or approve what it proposes. It tells you what it understood, did and verified."
          aside={
            brain?.available ? (
              <StatusPill tone="success" testId="home-brain">Reasoning ready</StatusPill>
            ) : (
              <button type="button" onClick={() => go({ name: "settings", section: "ai" })} title={brain?.reason ?? undefined}>
                <StatusPill tone="warning" testId="home-brain">No AI route — set up</StatusPill>
              </button>
            )
          }
        />
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-5 pb-5 lg:grid-cols-[minmax(0,1.65fr)_minmax(320px,1fr)]">
          <Panel className="flex min-h-[380px] flex-col" testId="home-chat">
            <CoreStrip />
            <div className="mt-4 flex min-h-0 flex-1 border-t border-hairline pt-3">
              <Conversation />
            </div>
          </Panel>

          <div className="flex min-h-0 flex-col gap-5 overflow-y-auto pr-1">
            <Panel title="Needs you now" testId="home-focus">
              <AnimatePresence mode="wait">
                {approval ? (
                  <ApprovalCard key={approval.id} request={approval} />
                ) : mission ? (
                  <ActiveMission key={mission.id} mission={mission} />
                ) : (
                  <p key="none" className="flex items-center gap-2 text-[13px] text-fg-muted">
                    <Icon name="check" size={16} className="text-success" />
                    Nothing is waiting for you. Missions and approvals appear here.
                  </p>
                )}
              </AnimatePresence>
            </Panel>

            <Panel title="Your areas">
              <AreaTiles />
            </Panel>

            <Panel title="Your tasks" padded={false} className="pb-2">
              <MissionsPanel />
            </Panel>

            <Panel padded={false} className="h-64 shrink-0 overflow-hidden">
              <ActivityStream />
            </Panel>

            <Panel padded={false}>
              <ContextPanel />
            </Panel>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Quick launch with one true sentence per area — nothing invented, "—" while loading. */
function AreaTiles() {
  const learning = useJarvis((s) => s.learning);
  const training = useJarvis((s) => s.training);
  const connection = useJarvis((s) => s.connection);
  const [options, setOptions] = useState<BlOptions | null>(null);
  const [ultron, setUltron] = useState<UlOverview | null>(null);

  useEffect(() => {
    if (connection !== "online") return;
    api.blOptions().then(setOptions, () => setOptions(null));
    api.ulOverview().then(setUltron, () => setUltron(null));
  }, [connection]);

  const local = options?.markets.filter((m) => m.local.length > 0).map((m) => m.symbol) ?? [];
  const busy = ultron?.workers.busy ?? 0;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
      <Tile
        label="Training Hub"
        icon="training"
        color="var(--color-training)"
        testId="home-tile-training"
        onClick={() => go({ name: "training" })}
        detail={
          learning
            ? `Learning: ${learningLabel(learning.state)}${training?.model ? ` · model run ${training.model.run}` : ""}`
            : "—"
        }
      />
      <Tile
        label="QuantLab"
        icon="quantlab"
        color="var(--color-quant)"
        testId="home-tile-quantlab"
        onClick={() => go({ name: "quantlab" })}
        detail={
          options
            ? `${options.strategies.length} strateg${options.strategies.length === 1 ? "y" : "ies"} · local data: ${local.join(", ") || "none yet"}`
            : "—"
        }
      />
      <Tile
        label="Team ULTRON"
        icon="ultron"
        color="var(--color-ultron)"
        testId="home-tile-ultron"
        onClick={() => go({ name: "ultron" })}
        detail={
          ultron
            ? busy > 0
              ? `${busy} agent task${busy === 1 ? "" : "s"} running · ${ultron.counts.active} mission(s)`
              : `All robots docked · ${ultron.counts.active} active mission(s)`
            : "—"
        }
      />
    </div>
  );
}
