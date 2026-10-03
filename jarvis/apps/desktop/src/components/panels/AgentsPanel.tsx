import type { AgentState } from "@jarvis/protocol";

import { useJarvis } from "../../store/store";
import { Section, StatusDot, cx, textTone } from "../ui/primitives";
import { AGENT_TONE } from "../ui/status";

export function AgentsPanel() {
  const agents = useJarvis((s) => s.agents);
  const online = agents.filter((a) => a.available).length;
  return (
    <Section
      title="Agents"
      aside={
        <span className="font-mono text-2xs text-fg-faint tabular">
          {online}/{agents.length} online
        </span>
      }
    >
      <ul className="-mx-2 space-y-0.5" data-testid="agents-panel">
        {agents.map((agent) => (
          <AgentRow key={agent.id} agent={agent} />
        ))}
      </ul>
    </Section>
  );
}

function AgentRow({ agent }: { agent: AgentState }) {
  const tone = AGENT_TONE[agent.status];
  const busy = agent.status === "active" || agent.status === "waiting";
  const statusText =
    agent.status === "standby"
      ? `Phase ${agent.available_in_phase ?? "—"}`
      : agent.status === "waiting"
        ? "Waiting"
        : agent.status === "active"
          ? "Active"
          : "Idle";
  return (
    <li
      className={cx(
        "rounded-lg px-2 py-2 transition-colors duration-300",
        busy && "bg-white/[0.025]",
        !agent.available && "opacity-45",
      )}
      data-testid={`agent-${agent.id}`}
      data-status={agent.status}
    >
      <div className="flex items-center gap-2.5">
        <StatusDot tone={tone} live={busy} />
        <span className={cx("text-[13px] font-medium", busy ? "text-fg" : "text-fg-muted")}>{agent.name}</span>
        <span className="truncate text-xs text-fg-faint">{agent.role}</span>
        <span className={cx("ml-auto text-2xs font-medium tracking-wide uppercase", textTone(tone))}>
          {statusText}
        </span>
      </div>
      {busy && agent.current_task && (
        <p className="mt-1 truncate pl-4 text-xs text-fg-muted">{agent.detail ?? agent.current_task}</p>
      )}
    </li>
  );
}
