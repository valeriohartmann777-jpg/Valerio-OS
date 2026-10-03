import { MissionControls } from "../components/mission/MissionControls";
import { StepList } from "../components/mission/StepList";
import { Empty, Pill, StatusDot, cx, textTone } from "../components/ui/primitives";
import { MISSION_TONE, SEVERITY_TEXT } from "../components/ui/status";
import { clock, duration, missionNumber, missionStatusLabel } from "../lib/format";
import { isTerminal } from "../store/reducer";
import { dispatch, useJarvis, useNow } from "../store/store";

export function MissionDetail({ id }: { id: string }) {
  const mission = useJarvis((s) => s.missions.find((m) => m.id === id) ?? null);
  const activity = useJarvis((s) => s.activity);
  const now = useNow(1000);

  if (!mission) {
    return (
      <div className="p-10">
        <Empty>This mission is no longer in the recent list.</Empty>
      </div>
    );
  }

  const tone = MISSION_TONE[mission.status];
  const trace = activity.filter((e) => e.mission_id === mission.id || (mission.trace_id && e.trace_id === mission.trace_id));

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[920px] px-10 py-10">
        <button
          type="button"
          className="mb-8 text-xs text-fg-faint transition-colors hover:text-fg-muted"
          onClick={() => dispatch({ type: "navigate", view: { name: "home" } })}
        >
          ← Home
        </button>

        <header className="flex items-start justify-between gap-8">
          <div>
            <div className="flex items-center gap-3">
              <span className="label">Mission {missionNumber(mission.number)}</span>
              <Pill tone={tone}>
                <StatusDot tone={tone} live={!isTerminal(mission)} />
                {missionStatusLabel(mission.status)}
              </Pill>
            </div>
            <h1 className="mt-3 text-2xl font-light tracking-[-0.01em] text-fg">{mission.title}</h1>
            <p className="mt-2 text-[13px] text-fg-muted">Goal: “{mission.goal}”</p>
          </div>
          <MissionControls mission={mission} />
        </header>

        <dl className="mt-8 grid grid-cols-4 gap-6 border-y border-hairline py-5 text-[13px]">
          <Meta label="Started">{clock(mission.created_at)}</Meta>
          <Meta label="Duration">{duration(mission.created_at, mission.finished_at, now)}</Meta>
          <Meta label="Agents">{mission.agents.join(", ") || "—"}</Meta>
          <Meta label="Trace">
            <span className="font-mono text-xs">{mission.trace_id ?? "—"}</span>
          </Meta>
        </dl>

        {mission.result && (
          <section className="mt-8">
            <h2 className="label mb-3">Result</h2>
            <p className="text-[17px] font-light text-fg">{mission.result}</p>
            {mission.verification && (
              <p className={cx("mt-2 text-xs", textTone(mission.verification.status === "verified" ? "success" : "warning"))}>
                {mission.verification.summary}
              </p>
            )}
          </section>
        )}

        <section className="mt-10">
          <h2 className="label mb-3">Steps</h2>
          <StepList steps={mission.steps} detailed />
        </section>

        {mission.errors.length > 0 && (
          <section className="mt-10">
            <h2 className="label mb-3">Errors</h2>
            <ul className="space-y-2 text-[13px]">
              {mission.errors.map((error, i) => (
                <li key={`${error.code}-${i}`}>
                  <span className="text-danger">{error.message}</span>
                  {error.suggestion && <span className="text-fg-muted"> — {error.suggestion}</span>}
                  <span className="ml-2 font-mono text-2xs text-fg-faint">{error.code}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="mt-10">
          <h2 className="label mb-3">Trace</h2>
          {trace.length === 0 ? (
            <Empty>No events recorded in this session.</Empty>
          ) : (
            <ol className="selectable space-y-px">
              {trace.map((event) => (
                <li key={event.id} className="grid grid-cols-[64px_80px_minmax(0,1fr)] gap-3 py-[3px] text-[13px]">
                  <time className="font-mono text-xs text-fg-faint tabular">{clock(event.timestamp)}</time>
                  <span className="font-mono text-2xs tracking-wider text-fg-faint uppercase">{event.source}</span>
                  <span className={cx("truncate", SEVERITY_TEXT[event.severity])}>{event.message}</span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </div>
  );
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="label mb-1.5">{label}</dt>
      <dd className="truncate text-fg-muted">{children}</dd>
    </div>
  );
}
