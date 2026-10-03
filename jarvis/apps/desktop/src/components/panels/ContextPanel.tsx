import { modelLabel, percent, uptime } from "../../lib/format";
import { busyAgent, featuredMission } from "../../store/reducer";
import { useJarvis, useNow } from "../../store/store";
import { Meter, Row, Section } from "../ui/primitives";

export function ContextPanel() {
  const context = useJarvis((s) => s.context);
  const mission = useJarvis((s) => featuredMission(s, Date.now()));
  const agent = useJarvis(busyAgent);
  const brain = useJarvis((s) => s.brain);
  useNow(2000);

  return (
    <Section title="Context">
      <dl data-testid="context-panel">
        <Row label="Active app">{context?.active_app ?? "—"}</Row>
        <Row label="Window">
          <span title={context?.active_window ?? undefined}>{context?.active_window ?? "—"}</span>
        </Row>
        <Row label="Mission">{mission ? mission.title : <Faint>None</Faint>}</Row>
        <Row label="Agent">{agent ? agent.name : <Faint>Idle</Faint>}</Row>
        <Row label="Brain">
          {brain?.available && brain.fast_model ? (
            <span title={brain.reasoning_model ? `think: ${modelLabel(brain.reasoning_model)}` : undefined}>
              {modelLabel(brain.fast_model)}
            </span>
          ) : (
            <span className="text-warning" title={brain?.reason ?? undefined} data-testid="brain-offline">
              Offline — no API key
            </span>
          )}
        </Row>
        <Row label="Voice">
          <Faint>Not configured</Faint>
        </Row>
      </dl>

      <div className="mt-4 space-y-3">
        <Metric label="CPU" value={context?.cpu_percent ?? 0} />
        <Metric
          label="Memory"
          value={context?.memory_percent ?? 0}
          detail={
            context
              ? `${context.memory_used_gb.toFixed(1)} / ${context.memory_total_gb.toFixed(1)} GB`
              : undefined
          }
        />
      </div>

      <dl className="mt-4">
        <Row label="Host">{context?.hostname ?? "—"}</Row>
        <Row label="System">{context?.platform ?? "—"}</Row>
        <Row label="Uptime">{context ? uptime(context.uptime_seconds) : "—"}</Row>
      </dl>
    </Section>
  );
}

function Metric({ label, value, detail }: { label: string; value: number; detail?: string }) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between text-xs">
        <span className="text-fg-faint">{label}</span>
        <span className="font-mono text-fg-muted tabular">
          {detail && <span className="mr-2 text-fg-faint">{detail}</span>}
          {percent(value)}
        </span>
      </div>
      <Meter value={value} tone={value > 85 ? "warning" : "muted"} />
    </div>
  );
}

function Faint({ children }: { children: React.ReactNode }) {
  return <span className="text-fg-faint">{children}</span>;
}
