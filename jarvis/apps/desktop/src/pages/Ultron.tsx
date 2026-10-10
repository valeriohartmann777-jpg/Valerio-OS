import type { UlAgent, UlArtifact, UlEvent, UlKnowledge, UlMission, UlMissionRow, UlOverview, UlProjectKind } from "@jarvis/protocol";
import { useCallback, useEffect, useState } from "react";

import { Badge, Hash, compactInputClass, inputClass } from "../components/quantlab/ui";
import { Button, cx } from "../components/ui/primitives";
import { MissionCard, MissionView, Timeline } from "../components/ultron/MissionView";
import { AgentName, ArtifactViewer, MissionState, Section, time, usd } from "../components/ultron/parts";
import { ApiError, api } from "../lib/api";
import { dispatch, useJarvis } from "../store/store";

/**
 * ULTRON Mission Control. JARVIS / ULTRON / [Overview | Mission Control | Agent Matrix |
 * Projects | Knowledge | Activity | Permissions]. Everything shown is read from the backend;
 * there are no simulated agents or progress bars without a real denominator.
 */

const VIEWS = ["Overview", "Mission Control", "Agent Matrix", "Projects", "Knowledge", "Activity", "Permissions"] as const;
type ViewName = (typeof VIEWS)[number];

function useUltronTick(): string | null {
  return useJarvis((s) => {
    for (let i = s.activity.length - 1; i >= 0; i--) {
      const event = s.activity[i];
      if (event?.type.startsWith("ultron.")) return event.id;
    }
    return null;
  });
}

export function Ultron() {
  const [view, setView] = useState<ViewName>("Overview");
  const [overview, setOverview] = useState<UlOverview | null>(null);
  const [missions, setMissions] = useState<UlMissionRow[]>([]);
  const [missionId, setMissionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tick = useUltronTick();
  const connection = useJarvis((s) => s.connection);

  const refresh = useCallback(async () => {
    try {
      const [o, m] = await Promise.all([api.ulOverview(), api.ulMissions()]);
      setOverview(o);
      setMissions(m);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "ULTRON isn't reachable.");
    }
  }, []);

  useEffect(() => {
    const id = window.setTimeout(() => void refresh(), 120); // coalesce bursts of events
    return () => window.clearTimeout(id);
  }, [refresh, tick, connection]);

  const open = (id: string) => {
    setMissionId(id);
    setView("Mission Control");
  };

  return (
    <div className="h-full overflow-y-auto" data-testid="ultron-page">
      <div className="mx-auto max-w-[1360px] px-8 py-8">
        <header className="mb-5 flex flex-wrap items-end gap-4">
          <div className="mr-auto">
            <p className="font-mono text-2xs tracking-[0.2em] text-fg-faint">JARVIS / ULTRON / {view.toUpperCase()}</p>
            <h1 className="mt-1 text-2xl font-light tracking-[-0.01em] text-fg">ULTRON</h1>
            <p className="mt-1 text-[13px] text-fg-muted" data-testid="ul-status-line">
              {overview ? (
                <>
                  <span className={overview.workers.busy ? "text-ql" : undefined}>
                    {overview.workers.busy} of {overview.workers.limit} worker slot(s) busy
                  </span>{" "}
                  · {overview.counts.active} active mission(s) · {overview.pending_approvals.length} approval(s) waiting
                </>
              ) : (
                "Connecting…"
              )}
            </p>
          </div>
          {overview && <IsolationBadge overview={overview} />}
          <EmergencyControls />
        </header>

        {overview?.scripted && (
          <p className="mb-4 rounded-xl border border-ql-warning/50 bg-ql-warning/10 px-4 py-2 font-mono text-2xs tracking-[0.1em] text-ql-warning" data-testid="ul-scripted">
            SCRIPTED TEST MODEL — agent replies come from a test script. Files, git, sandboxed test runs, policy, approvals and persistence are real.
          </p>
        )}
        {overview && !overview.model_ready && !overview.scripted && (
          <div className="mb-4 flex items-center gap-3 rounded-xl border border-ql-border bg-ql-surface px-4 py-3 text-[13px] text-fg-muted">
            ULTRON's agents run on Claude. Add your Anthropic API key to start missions.
            <Button className="ml-auto" onClick={() => dispatch({ type: "navigate", view: { name: "settings" } })}>
              Open Settings
            </Button>
          </div>
        )}

        <nav className="mb-6 flex gap-1 overflow-x-auto rounded-xl border border-ql-border bg-ql-surface p-1" aria-label="ULTRON">
          {VIEWS.map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={cx("flex-1 whitespace-nowrap rounded-lg px-3 py-1.5 text-[13px] transition-colors", view === v ? "bg-ql-raised text-fg" : "text-fg-faint hover:text-fg-muted")}
              data-testid={`ul-view-${v.toLowerCase().replace(/\s+/g, "-")}`}
            >
              {v}
            </button>
          ))}
        </nav>
        {error && <p className="mb-4 text-[13px] text-ql-danger">{error}</p>}

        {view === "Overview" && overview && <OverviewView overview={overview} missions={missions} onOpen={open} onCreated={(m) => open(m.id)} />}
        {view === "Mission Control" && <MissionControl missions={missions} selected={missionId} onSelect={setMissionId} tick={tick} />}
        {view === "Agent Matrix" && overview && <AgentMatrix agents={overview.agents} onOpen={open} />}
        {view === "Projects" && <Projects missions={missions} onOpen={open} tick={tick} />}
        {view === "Knowledge" && <Knowledge tick={tick} />}
        {view === "Activity" && <Activity missions={missions} tick={tick} />}
        {view === "Permissions" && overview && <Permissions overview={overview} onSaved={refresh} />}
      </div>
    </div>
  );
}

function IsolationBadge({ overview }: { overview: UlOverview }) {
  const iso = overview.isolation;
  const label = iso.kind === "seatbelt" ? "Sandbox · macOS Seatbelt" : iso.kind === "netns" ? "Sandbox · no network" : "No OS sandbox";
  return (
    <Badge tone={iso.kind === "none" ? "warning" : "accent"} title={iso.detail}>
      {label}
    </Badge>
  );
}

function EmergencyControls() {
  const [note, setNote] = useState<string | null>(null);
  return (
    <div className="flex items-center gap-2">
      {note && <span className="text-2xs text-fg-faint">{note}</span>}
      <Button onClick={async () => setNote(`${(await api.ulPauseAll()).paused} paused`)} data-testid="ul-pause-all">
        Pause all
      </Button>
      <Button
        variant="danger"
        onClick={async () => {
          if (window.confirm("Emergency stop: cancel every active mission and interrupt all agents?")) {
            setNote(`${(await api.ulStopAll()).cancelled} stopped`);
          }
        }}
        data-testid="ul-stop-all"
      >
        Stop all
      </Button>
    </div>
  );
}

// Overview ---------------------------------------------------------------------------------

function OverviewView({ overview, missions, onOpen, onCreated }: { overview: UlOverview; missions: UlMissionRow[]; onOpen: (id: string) => void; onCreated: (m: UlMission) => void }) {
  const [events, setEvents] = useState<UlEvent[]>([]);
  useEffect(() => {
    api.ulActivity(undefined, 12).then(setEvents, () => undefined);
  }, [overview]);
  const active = overview.agents.filter((a) => a.active);
  return (
    <div className="space-y-5" data-testid="ul-overview">
      <NewMission overview={overview} onCreated={onCreated} />
      <dl className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Active missions" value={String(overview.counts.active)} sub={`${overview.counts.complete} complete · ${overview.counts.blocked} blocked`} />
        <Stat label="Worker slots" value={`${overview.workers.busy} / ${overview.workers.limit}`} sub="running agent tasks" />
        <Stat label="Approvals waiting" value={String(overview.pending_approvals.length)} sub="consequence boundary" />
        <Stat label="Spend today" value={usd(overview.spend.today_usd)} sub={`${usd(overview.spend.week_usd)} this week`} />
      </dl>
      {overview.pending_approvals.length > 0 && (
        <Section title="Waiting for you">
          <ul className="space-y-2">
            {overview.pending_approvals.map((a) => (
              <li key={a.id} className="flex items-center gap-3 text-[13px]">
                <Badge tone="warning">approval</Badge>
                <span className="min-w-0 flex-1 truncate text-fg">{a.title}</span>
                <Button onClick={() => onOpen(a.mission_id)}>Review</Button>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Section title={`Missions · ${missions.length}`}>
          {missions.length === 0 ? (
            <p className="text-[13px] text-fg-faint">No mission yet. Describe a goal above — or tell JARVIS in the command bar.</p>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {missions.slice(0, 8).map((m) => (
                <MissionCard key={m.id} m={m} onOpen={() => onOpen(m.id)} />
              ))}
            </div>
          )}
        </Section>
        <div className="space-y-5">
          <Section title="Team status">
            <ul className="space-y-2">
              {active.map((a) => (
                <li key={a.id} className="flex items-center gap-3 text-[13px]">
                  <AgentName id={a.id} className="w-20" />
                  <span className={cx("flex-1 truncate", a.status === "working" ? "text-ql" : "text-fg-faint")}>
                    {a.status === "working" ? `● ${a.current.map((c) => c.key).join(", ")}` : "idle"}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-2xs text-fg-faint">ATLAS, PRISM, CIPHER, ARCHIVE, VECTOR, OPERATOR: planned (R2/R3), not running.</p>
          </Section>
          <Section title="Latest activity">
            <Timeline events={events} showDebug={false} />
          </Section>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-2xl border border-ql-border bg-ql-surface p-4">
      <dt className="text-xs text-ql-muted">{label}</dt>
      <dd className="mt-1 text-2xl font-medium text-fg">{value}</dd>
      <dd className="text-2xs text-fg-faint">{sub}</dd>
    </div>
  );
}

function NewMission({ overview, onCreated }: { overview: UlOverview; onCreated: (m: UlMission) => void }) {
  const [goal, setGoal] = useState("");
  const [project, setProject] = useState<UlProjectKind>("sandbox");
  const [budget, setBudget] = useState(String(overview.config.budget_usd_per_mission));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = overview.model_ready || overview.scripted;
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      onCreated(await api.ulCreate(goal.trim(), project, Number(budget)));
      setGoal("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't start the mission.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="rounded-2xl border border-ql/30 bg-ql-surface p-5">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void start();
        }}
      >
        <label className="label" htmlFor="ul-goal">
          JARVIS › new mission
        </label>
        <textarea
          id="ul-goal"
          className={cx(inputClass, "mt-2 h-20 py-2")}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="e.g. Build a small CLI that converts CSV to JSON, with tests."
          data-testid="ul-goal"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <select className={cx(compactInputClass, "w-auto")} value={project} onChange={(e) => setProject(e.target.value as UlProjectKind)} aria-label="Project">
            <option value="sandbox">New isolated project</option>
            <option value="jarvis" disabled={!overview.repo_available}>
              JARVIS itself (reviewed branch)
            </option>
          </select>
          <label className="flex items-center gap-2 text-[13px] text-fg-muted">
            Budget $
            <input className={cx(compactInputClass, "w-20")} value={budget} onChange={(e) => setBudget(e.target.value)} inputMode="decimal" />
          </label>
          <span className="text-2xs text-fg-faint">Hard cap — every model call is checked against it first.</span>
          <Button type="submit" variant="primary" className="ml-auto" disabled={!ready || busy || goal.trim().length < 8} data-testid="ul-start">
            {busy ? "Starting…" : "Start mission"}
          </Button>
        </div>
        {error && <p className="mt-2 text-[13px] text-ql-danger">{error}</p>}
      </form>
    </section>
  );
}

// Mission Control --------------------------------------------------------------------------

function MissionControl({ missions, selected, onSelect, tick }: { missions: UlMissionRow[]; selected: string | null; onSelect: (id: string) => void; tick: string | null }) {
  const current = selected ?? missions[0]?.id ?? null;
  const [mission, setMission] = useState<UlMission | null>(null);
  useEffect(() => {
    if (!current) return;
    let live = true;
    api.ulMission(current).then((m) => live && setMission(m), () => undefined);
    return () => {
      live = false;
    };
  }, [current, tick]);
  // Poll while something runs, in case an event is missed.
  useEffect(() => {
    if (!current || !mission || !["PLANNING", "RUNNING"].includes(mission.state)) return;
    const id = window.setInterval(() => api.ulMission(current).then(setMission, () => undefined), 2000);
    return () => window.clearInterval(id);
  }, [current, mission]);
  if (missions.length === 0) return <p className="text-[13px] text-fg-faint">No missions yet — start one from the Overview.</p>;
  return (
    <div className="grid gap-5 xl:grid-cols-[260px_minmax(0,1fr)]">
      <nav className="space-y-1" aria-label="Missions">
        {missions.map((m) => (
          <button
            key={m.id}
            type="button"
            onClick={() => onSelect(m.id)}
            className={cx("w-full rounded-lg px-3 py-2 text-left transition-colors", current === m.id ? "bg-ql-raised" : "hover:bg-white/[0.03]")}
          >
            <span className="block truncate text-[13px] text-fg">{m.title}</span>
            <span className="mt-1 flex items-center justify-between">
              <span className="font-mono text-2xs text-fg-faint">{m.id}</span>
              <MissionState state={m.state} />
            </span>
          </button>
        ))}
      </nav>
      <div>{mission && mission.id === current ? <MissionView mission={mission} tick={tick} /> : <p className="text-[13px] text-fg-faint">Loading…</p>}</div>
    </div>
  );
}

// Agent Matrix -------------------------------------------------------------------------------

function AgentMatrix({ agents, onOpen }: { agents: UlAgent[]; onOpen: (id: string) => void }) {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="ul-agents">
      {agents.map((a) => (
        <section key={a.id} className={cx("rounded-2xl border p-5", a.active ? "border-ql-border bg-ql-surface" : "border-dashed border-ql-border/70 bg-transparent")} data-testid="ul-agent" data-active={a.active}>
          <header className="flex items-center gap-3">
            <AgentName id={a.id} className="text-[13px]" />
            <span className="text-[12px] text-fg-muted">{a.role}</span>
            <span className="ml-auto">
              {a.status === "working" ? <Badge tone="accent" icon="●">working</Badge> : a.status === "idle" ? <Badge tone="muted">idle</Badge> : <Badge tone="muted">not active · {a.release}</Badge>}
            </span>
          </header>
          <p className="mt-3 text-[13px] text-fg-muted">{a.deliverables}</p>
          {a.current.length > 0 && (
            <ul className="mt-3 space-y-1">
              {a.current.map((c) => (
                <li key={`${c.mission_id}-${c.key}`}>
                  <button type="button" className="text-[12.5px] text-ql hover:underline" onClick={() => onOpen(c.mission_id)}>
                    {c.mission_id} · {c.key} ({c.state.toLowerCase()})
                  </button>
                </li>
              ))}
            </ul>
          )}
          <dl className="mt-4 space-y-1.5 text-[12px]">
            <div className="flex gap-2">
              <dt className="w-16 shrink-0 text-fg-faint">Tools</dt>
              <dd className="font-mono text-[11px] text-fg-muted">{a.tools.join(" · ") || "—"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-16 shrink-0 text-fg-faint">Writes</dt>
              <dd className="text-fg-muted">{a.writes}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-16 shrink-0 text-fg-faint">Never</dt>
              <dd className="text-fg-muted">{a.must_not}</dd>
            </div>
            {a.active && (
              <div className="flex gap-2">
                <dt className="w-16 shrink-0 text-fg-faint">Record</dt>
                <dd className="font-mono text-[11px] text-fg-muted">
                  {Object.entries(a.runs).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ") || "no runs yet"} · {usd(a.spent_usd)}
                  {a.model && ` · ${a.model}`}
                </dd>
              </div>
            )}
          </dl>
        </section>
      ))}
    </div>
  );
}

// Projects -------------------------------------------------------------------------------------

function Projects({ missions, onOpen, tick }: { missions: UlMissionRow[]; onOpen: (id: string) => void; tick: string | null }) {
  const [details, setDetails] = useState<UlMission[]>([]);
  useEffect(() => {
    let live = true;
    Promise.all(missions.slice(0, 20).map((m) => api.ulMission(m.id))).then((d) => live && setDetails(d), () => undefined);
    return () => {
      live = false;
    };
  }, [missions, tick]);
  const group = (kind: UlProjectKind) => details.filter((d) => d.project_kind === kind);
  const card = (m: UlMission) => (
    <li key={m.id} className="rounded-xl border border-ql-border bg-ql-raised p-4">
      <div className="flex items-center gap-3">
        <button type="button" className="min-w-0 flex-1 truncate text-left text-[14px] text-fg hover:underline" onClick={() => onOpen(m.id)}>
          {m.title}
        </button>
        <MissionState state={m.state} />
      </div>
      <dl className="mt-2 space-y-1 text-[12px]">
        <div className="flex gap-2">
          <dt className="w-20 shrink-0 text-fg-faint">Workspace</dt>
          <dd className="selectable truncate font-mono text-[11px] text-fg-muted">{m.workspace}/repo</dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-20 shrink-0 text-fg-faint">Branch</dt>
          <dd className="font-mono text-[11px] text-fg-muted">{m.report?.branch ?? (m.project_kind === "jarvis" ? `ultron/${m.id}` : "main")} · <Hash value={m.head_commit} /></dd>
        </div>
        {m.report && (
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-fg-faint">Files</dt>
            <dd className="font-mono text-[11px] text-fg-muted">{m.report.files.join("  ")}</dd>
          </div>
        )}
        {m.report?.delivery && (
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-fg-faint">Delivery</dt>
            <dd className="text-fg-muted">{m.report.delivery}</dd>
          </div>
        )}
      </dl>
    </li>
  );
  return (
    <div className="grid gap-5 xl:grid-cols-2" data-testid="ul-projects">
      <Section title={`Isolated projects · ${group("sandbox").length}`}>
        <ul className="space-y-3">{group("sandbox").map(card)}</ul>
        {group("sandbox").length === 0 && <p className="text-[13px] text-fg-faint">None yet.</p>}
      </Section>
      <Section title={`JARVIS repository · ${group("jarvis").length}`}>
        <ul className="space-y-3">{group("jarvis").map(card)}</ul>
        <p className="mt-3 text-2xs text-fg-faint">Each JARVIS mission works on its own branch in a separate worktree. Merging into your running checkout is locked in this release.</p>
      </Section>
    </div>
  );
}

// Knowledge ---------------------------------------------------------------------------------

function Knowledge({ tick }: { tick: string | null }) {
  const [data, setData] = useState<UlKnowledge | null>(null);
  const [viewing, setViewing] = useState<UlArtifact | null>(null);
  const memories = useJarvis((s) => s.memories);
  useEffect(() => {
    api.ulKnowledge().then(setData, () => undefined);
  }, [tick]);
  const list = (items: UlArtifact[] | undefined, empty: string) =>
    !items || items.length === 0 ? (
      <p className="text-[13px] text-fg-faint">{empty}</p>
    ) : (
      <ul className="-mx-2">
        {items.map((a) => (
          <li key={a.id}>
            <button type="button" onClick={() => setViewing(a)} className="flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left hover:bg-white/[0.04]">
              <span className="font-mono text-2xs text-fg-faint">{a.mission_id}</span>
              <span className="min-w-0 flex-1 truncate text-[13px] text-fg-muted">{a.name}</span>
              <span className="text-2xs text-fg-faint">{time(a.created_at)}</span>
            </button>
          </li>
        ))}
      </ul>
    );
  return (
    <div className="space-y-5" data-testid="ul-knowledge">
      <p className="text-[13px] text-fg-muted">
        Decisions and evidence the team produced, kept with checksums. ARCHIVE (R2) will curate this into project memory; until then nothing here is rewritten.
      </p>
      <div className="grid gap-5 xl:grid-cols-3">
        <Section title="Specifications · AXIOM">{list(data?.specs, "No specs yet.")}</Section>
        <Section title="Reviews · SENTINEL">{list(data?.reviews, "No reviews yet.")}</Section>
        <Section title="Mission reports · JARVIS">{list(data?.reports, "No reports yet.")}</Section>
      </div>
      <Section title={`What JARVIS remembers about you · ${memories.length}`}>
        {memories.length === 0 ? (
          <p className="text-[13px] text-fg-faint">Nothing stored. Manage memory on Home.</p>
        ) : (
          <ul className="space-y-1 text-[13px] text-fg-muted">
            {memories.map((m) => (
              <li key={m.number}>
                <span className="font-mono text-2xs text-fg-faint">M{m.number}</span> {m.text}
              </li>
            ))}
          </ul>
        )}
      </Section>
      {viewing && <ArtifactViewer artifact={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}

// Activity -----------------------------------------------------------------------------------

function Activity({ missions, tick }: { missions: UlMissionRow[]; tick: string | null }) {
  const [mission, setMission] = useState("");
  const [agent, setAgent] = useState("");
  const [debug, setDebug] = useState(false);
  const [events, setEvents] = useState<UlEvent[]>([]);
  useEffect(() => {
    api.ulActivity(mission || undefined, 500).then(setEvents, () => undefined);
  }, [mission, tick]);
  const shown = events.filter((e) => (!agent || e.agent === agent) && (debug || e.severity !== "debug"));
  return (
    <div className="space-y-4" data-testid="ul-activity">
      <div className="flex flex-wrap items-center gap-3">
        <select className={cx(compactInputClass, "w-auto")} value={mission} onChange={(e) => setMission(e.target.value)} aria-label="Mission filter">
          <option value="">All missions</option>
          {missions.map((m) => (
            <option key={m.id} value={m.id}>
              {m.id} · {m.title}
            </option>
          ))}
        </select>
        <select className={cx(compactInputClass, "w-auto")} value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent filter">
          <option value="">All agents</option>
          {["jarvis", "axiom", "forge", "sentinel"].map((a) => (
            <option key={a} value={a}>
              {a.toUpperCase()}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-[13px] text-fg-muted">
          <input type="checkbox" checked={debug} onChange={(e) => setDebug(e.target.checked)} className="accent-[var(--color-ql)]" />
          include tool-level detail
        </label>
        <span className="ml-auto text-2xs text-fg-faint">{shown.length} event(s) · source: ULTRON event log</span>
      </div>
      <section className="rounded-2xl border border-ql-border bg-ql-surface p-4">
        {shown.length === 0 ? (
          <p className="text-[13px] text-fg-faint">No events.</p>
        ) : (
          <ol className="space-y-1">
            {shown.map((e) => (
              <li key={e.id} className="grid grid-cols-[72px_88px_120px_minmax(0,1fr)] gap-2 text-[12px]">
                <span className="font-mono text-fg-faint tabular">{time(e.at)}</span>
                <AgentName id={e.agent ?? "jarvis"} />
                <span className="truncate font-mono text-2xs text-fg-faint">{e.kind}</span>
                <span className={cx("min-w-0", e.severity === "warning" ? "text-ql-warning" : e.severity === "important" ? "text-ql" : "text-fg-muted")}>{e.message}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

// Permissions & Settings ------------------------------------------------------------------

function Permissions({ overview, onSaved }: { overview: UlOverview; onSaved: () => void }) {
  const c = overview.config;
  const [budget, setBudget] = useState(String(c.budget_usd_per_mission));
  const [workers, setWorkers] = useState(String(c.max_parallel_workers));
  const [attempts, setAttempts] = useState(String(c.max_attempts));
  const [note, setNote] = useState<string | null>(null);
  const save = async () => {
    try {
      await api.ulConfig({ budget_usd_per_mission: Number(budget), max_parallel_workers: Number(workers), max_attempts: Number(attempts) });
      setNote("Saved.");
      onSaved();
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : "Couldn't save.");
    }
  };
  const tone = { auto: "positive", approval: "warning", locked: "danger" } as const;
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]" data-testid="ul-permissions">
      <Section title="Policy · enforced in code before every effect">
        <table className="w-full text-left text-[13px]">
          <tbody>
            {overview.policy.map((p) => (
              <tr key={p.category} className="border-t border-ql-border/60">
                <td className="py-2 font-mono text-[11.5px] text-fg-muted">{p.category}</td>
                <td className="py-2 text-fg-muted">{p.description}</td>
                <td className="py-2 text-right">
                  <Badge tone={tone[p.decision]}>{p.decision === "auto" ? "autonomous" : p.decision === "approval" ? "needs approval" : "locked"}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-2xs text-fg-faint">
          Agents can't grant themselves permissions, can't spawn agents (delegation depth {c.max_delegation_depth}), and SENTINEL can't waive an approval.
        </p>
      </Section>
      <div className="space-y-5">
        <Section title="Limits">
          <div className="grid grid-cols-3 gap-3">
            <label className="text-xs text-ql-muted">
              Budget / mission ($)
              <input className={cx(inputClass, "mt-1")} value={budget} onChange={(e) => setBudget(e.target.value)} inputMode="decimal" />
            </label>
            <label className="text-xs text-ql-muted">
              Parallel workers
              <input className={cx(inputClass, "mt-1")} value={workers} onChange={(e) => setWorkers(e.target.value)} inputMode="numeric" />
            </label>
            <label className="text-xs text-ql-muted">
              Attempts / task
              <input className={cx(inputClass, "mt-1")} value={attempts} onChange={(e) => setAttempts(e.target.value)} inputMode="numeric" />
            </label>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Button variant="primary" onClick={save}>
              Save limits
            </Button>
            {note && <span className="text-[13px] text-fg-muted">{note}</span>}
          </div>
          <p className="mt-3 text-2xs text-fg-faint">
            {c.max_rounds_per_run} model↔tool rounds per run · {c.command_timeout_seconds}s per command · exports go to {c.export_dir}
          </p>
        </Section>
        <Section title="Isolation">
          <p className="text-[13px] text-fg">{overview.isolation.detail}</p>
          <ul className="mt-2 space-y-1 text-[12px] text-fg-muted">
            <li>{overview.isolation.network_blocked ? "✓" : "✕"} network blocked for agent commands</li>
            <li>{overview.isolation.writes_confined ? "✓" : "○"} file writes confined by the OS (always confined by the tool broker)</li>
            <li>✓ no shell · allowlisted commands · scrubbed environment · timeouts</li>
            <li>✓ one git worktree per task · reviews in a throwaway checkout</li>
          </ul>
        </Section>
        <Section title="Models">
          <ul className="space-y-1 text-[12px]">
            {Object.entries(c.agents).map(([agent, m]) => (
              <li key={agent} className="flex gap-3">
                <AgentName id={agent} className="w-20" />
                <span className="font-mono text-[11px] text-fg-muted">
                  {overview.scripted ? "scripted test model" : `${m.model} · effort ${m.effort ?? "default"}`}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-2xs text-fg-faint">From config/ultron.yaml; the key is the brain's (Settings → Brain).</p>
        </Section>
      </div>
    </div>
  );
}
