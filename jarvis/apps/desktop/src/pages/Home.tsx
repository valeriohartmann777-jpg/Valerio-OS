import { AnimatePresence } from "motion/react";

import { ActivityStream } from "../components/activity/ActivityStream";
import { ApprovalCard } from "../components/approval/ApprovalCard";
import { Conversation } from "../components/conversation/Conversation";
import { CoreStage } from "../components/core/CoreStage";
import { ActiveMission } from "../components/mission/ActiveMission";
import { AgentsPanel } from "../components/panels/AgentsPanel";
import { ContextPanel } from "../components/panels/ContextPanel";
import { MemoryPanel } from "../components/panels/MemoryPanel";
import { MissionsPanel } from "../components/panels/MissionsPanel";
import { featuredMission } from "../store/reducer";
import { useJarvis, useNow } from "../store/store";

/**
 * Hierarchy: 1 core → 2 active mission / approval → 3 conversation + command
 * bar (shell) → 4 context → 5 agents → 6 activity → 7 secondary metrics.
 */
export function Home() {
  const now = useNow(1000);
  const approval = useJarvis((s) => s.approvals[0] ?? null);
  const mission = useJarvis((s) => featuredMission(s, now));
  const chatting = useJarvis((s) => s.conversation.length > 0);

  return (
    <div className="grid h-full min-h-0 grid-cols-[272px_minmax(0,1fr)_304px] grid-rows-[minmax(0,1fr)_188px]">
      <aside className="min-h-0 overflow-y-auto border-r border-hairline">
        <ContextPanel />
        <div className="mx-5 border-t border-hairline" />
        <MemoryPanel />
      </aside>

      <main className="relative flex min-h-0 flex-col items-center px-8 pt-[3vh]">
        <div className="flex w-full shrink-0 flex-col items-center">
          <CoreStage compact={Boolean(approval || mission || chatting)} />
          <div className="mt-4 flex w-full flex-col items-center">
            <AnimatePresence mode="wait">
              {approval ? (
                <ApprovalCard key={approval.id} request={approval} />
              ) : mission ? (
                <ActiveMission key={mission.id} mission={mission} />
              ) : null}
            </AnimatePresence>
          </div>
        </div>
        <div className="mt-3 flex min-h-0 w-full flex-1 border-t border-hairline/60 pt-3">
          <Conversation />
        </div>
      </main>

      <aside className="min-h-0 overflow-y-auto border-l border-hairline">
        <AgentsPanel />
        <div className="mx-5 border-t border-hairline" />
        <MissionsPanel />
      </aside>

      <div className="col-span-3 min-h-0 border-t border-hairline bg-canvas-2/40">
        <ActivityStream />
      </div>
    </div>
  );
}
