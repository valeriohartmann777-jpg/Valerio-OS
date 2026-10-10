import type { UlArtifact, UlEvent, UlMission, UlTask } from "@jarvis/protocol";
import { type ReactNode, useEffect, useState } from "react";

import { ApiError, api } from "../../lib/api";
import { dispatch } from "../../store/store";
import { Badge, Hash, ago, compactInputClass, inputClass } from "../quantlab/ui";
import { Button, cx } from "../ui/primitives";
import { MissionGraph } from "./MissionGraph";
import {
  AgentName,
  ArtifactViewer,
  Fraction,
  MissionState,
  Section,
  TaskState,
  time,
  usd,
} from "./parts";

/** One mission: charter, task graph, evidence, controls — all live from the backend. */

export function MissionView({ mission, tick }: { mission: UlMission; tick: string | null }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [viewing, setViewing] = useState<UlArtifact | null>(null);
  const [events, setEvents] = useState<UlEvent[]>([]);
  const task = mission.tasks.find((t) => t.key === selected) ?? null;

  useEffect(() => {
    let live = true;
    api.ulActivity(mission.id, 400).then((e) => live && setEvents(e), () => undefined);
    return () => {
      live = false;
    };
  }, [mission.id, tick]);

  return (
    <div className="space-y-5" data-testid="ul-mission" data-state={mission.state}>
      <Header mission={mission} />
      {mission.blocker && mission.state !== "COMPLETE" && <Blocker mission={mission} />}
      <Approvals mission={mission} />
      {mission.report && <Report mission={mission} />}

      <Section
        title={`Task graph · ${mission.tasks.length} task(s) · same column = may run in parallel`}
        aside={<span className="text-2xs text-fg-faint">click a task for its contract and evidence</span>}
      >
        <MissionGraph tasks={mission.tasks} selected={selected} onSelect={setSelected} />
      </Section>

      {task && <TaskPanel task={task} mission={mission} onArtifact={setViewing} />}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Charter mission={mission} />
        <Section title={`Evidence · ${mission.artifacts.length} artifact(s)`}>
          <ArtifactList artifacts={mission.artifacts} onOpen={setViewing} />
        </Section>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Section title={`Agent runs · ${mission.runs.length}`}>
          <RunsTable mission={mission} />
        </Section>
        <Section title="Mission activity · from the event log">
          <Timeline events={events} />
        </Section>
      </div>
      {viewing && <ArtifactViewer artifact={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}

function Header({ mission }: { mission: UlMission }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };
  const live = ["PLANNING", "RUNNING"].includes(mission.state);
  const done = ["COMPLETE", "FAILED", "CANCELLED"].includes(mission.state);
  return (
    <section className="rounded-2xl border border-ql-border bg-ql-surface p-5">
      <div className="flex flex-wrap items-start gap-4">
        <div className="mr-auto min-w-0">
          <p className="font-mono text-2xs tracking-[0.16em] text-fg-faint">
            MISSION {mission.id.toUpperCase()} · {mission.project_kind === "jarvis" ? "JARVIS REPOSITORY" : "SANDBOX PROJECT"} · REV {mission.revision}
          </p>
          <h2 className="mt-1 text-xl font-light text-fg" data-testid="ul-mission-title">
            {mission.title}
          </h2>
          <p className="mt-1 max-w-[860px] text-[13px] text-fg-muted">{mission.goal}</p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <MissionState state={mission.state} />
          <span className="font-mono text-2xs text-fg-faint">{mission.model_label}</span>
        </div>
      </div>
      <div className="mt-4 grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
        <Fraction done={mission.progress.complete} total={mission.progress.total} label="Tasks complete" />
        <div title={`${usd(mission.spent_usd, 4)} of ${usd(mission.budget_usd)}`}>
          <div className="flex justify-between text-2xs text-fg-faint">
            <span>Spend (hard cap)</span>
            <span className="font-mono tabular">
              {usd(mission.spent_usd)} / {usd(mission.budget_usd)}
            </span>
          </div>
          <div className="mt-1 h-[3px] overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className={cx("h-full rounded-full", mission.spent_usd / mission.budget_usd > 0.8 ? "bg-ql-warning" : "bg-ql-muted")}
              style={{ width: `${Math.min(100, (mission.spent_usd / mission.budget_usd) * 100)}%` }}
            />
          </div>
        </div>
        <div className="flex gap-2">
          {live && (
            <Button disabled={busy} onClick={() => act(() => api.ulControl(mission.id, "pause"))} data-testid="ul-pause">
              Pause
            </Button>
          )}
          {mission.state === "PAUSED" && (
            <Button variant="primary" disabled={busy} onClick={() => act(() => api.ulControl(mission.id, "resume"))} data-testid="ul-resume">
              Resume
            </Button>
          )}
          {!done && (
            <Button
              variant="danger"
              disabled={busy}
              onClick={() => {
                if (window.confirm("Stop this mission? Running agents are interrupted.")) {
                  void act(() => api.ulControl(mission.id, "cancel"));
                }
              }}
              data-testid="ul-cancel"
            >
              Stop
            </Button>
          )}
        </div>
      </div>
      {error && <p className="mt-3 text-[13px] text-ql-danger">{error}</p>}
    </section>
  );
}

function Blocker({ mission }: { mission: UlMission }) {
  const b = mission.blocker;
  const [answer, setAnswer] = useState("");
  const [budget, setBudget] = useState(String(Math.ceil((mission.budget_usd + 2) * 100) / 100));
  const [error, setError] = useState<string | null>(null);
  if (!b) return null;
  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    }
  };
  const tone = b.kind === "cancelled" ? "border-ql-border" : "border-ql-danger/40 bg-ql-danger/5";
  return (
    <section className={cx("rounded-2xl border p-5", tone)} data-testid="ul-blocker">
      <p className="label">
        {b.kind === "questions"
          ? "JARVIS needs your answer"
          : b.kind === "cancelled"
            ? "Stopped"
            : b.kind === "ai_route"
              ? "Paused · no AI route"
              : `Blocked · ${b.kind}`}
      </p>
      {b.message && <p className="mt-2 text-[13px] text-fg">{b.message}</p>}
      {b.questions && (
        <ul className="mt-2 space-y-1 text-[13px] text-fg">
          {b.questions.map((q) => (
            <li key={q}>? {q}</li>
          ))}
        </ul>
      )}
      {["questions", "plan", "model"].includes(b.kind) && mission.state === "BLOCKED" && (
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void run(() => api.ulAnswer(mission.id, answer));
          }}
        >
          <input className={inputClass} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Your answer or a clarification — JARVIS re-plans with it" />
          <Button type="submit" variant="primary" disabled={!answer.trim()}>
            Re-plan
          </Button>
        </form>
      )}
      {b.kind === "budget" && (
        <div className="mt-3 flex items-center gap-2">
          <input className={cx(compactInputClass, "w-28")} value={budget} onChange={(e) => setBudget(e.target.value)} inputMode="decimal" aria-label="New budget in USD" />
          <Button variant="primary" onClick={() => run(() => api.ulBudget(mission.id, Number(budget)))}>
            Raise budget & continue
          </Button>
        </div>
      )}
      {["retries", "review_failed", "model"].includes(b.kind) && mission.state === "BLOCKED" && (
        <Button className="mt-3" onClick={() => run(() => api.ulControl(mission.id, "resume"))}>
          {b.kind === "model" ? "Try again" : "Grant another round of attempts"}
        </Button>
      )}
      {b.kind === "ai_route" && mission.state === "BLOCKED" && (
        <div className="mt-3 flex flex-wrap items-center gap-3" data-testid="ul-ai-paused">
          <span className="text-[12px] text-fg-muted">
            Checkpoint kept — continues by itself when a route is back; no tool runs twice.
          </span>
          <Button onClick={() => run(() => api.ulControl(mission.id, "resume"))}>Try now</Button>
          <Button onClick={() => dispatch({ type: "navigate", view: { name: "settings" } })}>AI & Billing</Button>
        </div>
      )}
      {error && <p className="mt-2 text-[13px] text-ql-danger">{error}</p>}
    </section>
  );
}

function Approvals({ mission }: { mission: UlMission }) {
  const [error, setError] = useState<string | null>(null);
  if (mission.approvals.length === 0) return null;
  return (
    <Section title="Approvals · consequence boundary" testId="ul-approvals">
      <ul className="space-y-3">
        {mission.approvals.map((a) => (
          <li key={a.id} className="rounded-xl border border-ql-border bg-ql-raised p-4">
            <div className="flex flex-wrap items-center gap-3">
              <Badge tone={a.state === "pending" ? "warning" : a.state === "executed" ? "positive" : "muted"}>{a.state}</Badge>
              <span className="text-[13px] text-fg">{a.title}</span>
              <span className="font-mono text-2xs text-fg-faint">{a.category}</span>
              {a.state === "pending" && (
                <span className="ml-auto flex gap-2">
                  <Button
                    variant="primary"
                    onClick={() => api.ulDecide(a.id, "approve").catch((e) => setError(e instanceof ApiError ? e.message : "Failed"))}
                    data-testid="ul-approve"
                  >
                    Approve
                  </Button>
                  <Button onClick={() => api.ulDecide(a.id, "reject").catch(() => undefined)}>Decline</Button>
                </span>
              )}
            </div>
            <pre className="selectable mt-2 overflow-x-auto font-mono text-[11px] text-fg-muted">{JSON.stringify(a.effect, null, 2)}</pre>
            <p className="mt-1 font-mono text-[10px] text-fg-faint">
              effect signature <Hash value={a.signature} length={16} /> — exactly this runs, or nothing
            </p>
            {a.result && <p className="mt-2 text-[13px] text-fg-muted">{a.result}</p>}
          </li>
        ))}
      </ul>
      {error && <p className="mt-2 text-[13px] text-ql-danger">{error}</p>}
    </Section>
  );
}

const CRITERION = {
  verified: { cls: "text-ql-positive", label: "✓ verified by a check" },
  reviewed: { cls: "text-ql-positive", label: "✓ confirmed by SENTINEL" },
  failed: { cls: "text-ql-danger", label: "✕ failed" },
  not_checked: { cls: "text-ql-warning", label: "○ not checked" },
} as const;

function Report({ mission }: { mission: UlMission }) {
  const r = mission.report;
  if (!r) return null;
  return (
    <Section title="Final report · JARVIS" testId="ul-report">
      <ul className="space-y-2">
        {r.criteria.map((c) => (
          <li key={c.criterion} className="grid gap-2 text-[13px] md:grid-cols-[minmax(0,1fr)_200px]">
            <span className="text-fg">
              {c.criterion}
              <span className="block text-2xs text-fg-faint">{c.evidence}</span>
            </span>
            <span className={cx("font-mono text-2xs", CRITERION[c.status].cls)}>{CRITERION[c.status].label}</span>
          </li>
        ))}
      </ul>
      <div className="mt-4 grid gap-2 text-2xs text-fg-faint md:grid-cols-4">
        <span>Branch <span className="font-mono text-fg-muted">{r.branch}</span></span>
        <span>Commit <Hash value={r.head_commit} /></span>
        <span>{r.files.length} file(s) · isolation {r.isolation}</span>
        <span>Spent {usd(r.spent_usd, 4)}</span>
      </div>
      {r.delivery && <p className="mt-3 text-[13px] text-fg">{r.delivery}</p>}
    </Section>
  );
}

function Charter({ mission }: { mission: UlMission }) {
  const c = mission.charter;
  if (!c) return <Section title="Charter">{<p className="text-[13px] text-fg-faint">JARVIS is writing the charter…</p>}</Section>;
  const list = (title: string, items: string[] | undefined) =>
    items && items.length > 0 ? (
      <div>
        <p className="mb-1 label">{title}</p>
        <ul className="space-y-0.5 text-[13px] text-fg-muted">
          {items.map((i) => (
            <li key={i}>— {i}</li>
          ))}
        </ul>
      </div>
    ) : null;
  return (
    <Section title="Charter · validated by the runtime">
      <div className="space-y-3">
        <p className="text-[13px] text-fg">{c.objective}</p>
        <div>
          <p className="mb-1 label">Success criteria</p>
          <ul className="space-y-1 text-[13px]">
            {c.success_criteria.map((s) => (
              <li key={s.criterion} className="text-fg-muted">
                {s.criterion}
                {s.check && <span className="ml-2 font-mono text-2xs text-ql">$ {s.check.join(" ")}</span>}
              </li>
            ))}
          </ul>
        </div>
        {list("In scope", c.in_scope)}
        {list("Out of scope", c.out_of_scope)}
        {list("Assumptions", c.assumptions)}
        {list("Your answers", c.answers)}
        {list("Runtime notes", mission.plan_notes ?? undefined)}
      </div>
    </Section>
  );
}

function TaskPanel({ task, mission, onArtifact }: { task: UlTask; mission: UlMission; onArtifact: (a: UlArtifact) => void }) {
  const runs = mission.runs.filter((r) => r.task_id === task.id);
  const artifacts = mission.artifacts.filter((a) => a.task_id === task.id);
  const r = task.result;
  const row = (label: string, value: ReactNode) => (
    <div className="grid grid-cols-[130px_minmax(0,1fr)] gap-3 py-1 text-[13px]">
      <dt className="text-fg-faint">{label}</dt>
      <dd className="min-w-0 text-fg">{value}</dd>
    </div>
  );
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <AgentName id={task.owner} /> {task.key} · {task.title}
        </span>
      }
      aside={<TaskState state={task.state} />}
      testId="ul-task-panel"
    >
      <div className="grid gap-6 xl:grid-cols-2">
        <dl>
          {row("Objective", task.contract.objective)}
          {row("Done when", <ul>{task.contract.done_when.map((d) => <li key={d}>— {d}</li>)}</ul>)}
          {row("May write", task.contract.allowed_paths.length ? <span className="font-mono text-[12px]">{task.contract.allowed_paths.join("  ")}</span> : "nothing")}
          {row(
            "Checks",
            task.contract.checks.length ? (
              <span className="font-mono text-[12px]">{task.contract.checks.map((c) => `$ ${c.join(" ")}`).join("\n")}</span>
            ) : task.contract.reviews.length ? (
              `reviews ${task.contract.reviews.join(", ")}`
            ) : (
              "—"
            ),
          )}
          {row("Attempts", `${task.attempts} of ${task.max_attempts}`)}
          {row("Cost", usd(task.cost_usd, 4))}
        </dl>
        <div className="space-y-3">
          {r?.summary && <p className="text-[13px] text-fg">{r.summary}</p>}
          {r?.verdict && (
            <p className="text-[13px]">
              Verdict <span className={r.accepted ? "text-ql-positive" : "text-ql-danger"}>{r.accepted ? "accepted" : "rejected"}</span>
              <span className="text-fg-faint"> · SENTINEL said “{r.verdict}”, runtime checks {r.runtime_checks_passed ? "passed" : "failed"}</span>
            </p>
          )}
          {(r?.checks ?? r?.runtime_checks ?? []).map((c) => (
            <p key={c.command} className="font-mono text-[12px]">
              <span className={c.passed ? "text-ql-positive" : "text-ql-danger"}>{c.passed ? "✓" : "✕"}</span> {c.command}
              <span className="text-fg-faint"> · exit {c.exit_code ?? "timeout"} · {c.seconds}s</span>
            </p>
          ))}
          {r?.findings && r.findings.length > 0 && (
            <ul className="space-y-1 text-[13px] text-fg-muted">
              {r.findings.map((f) => (
                <li key={f.issue}>
                  <span className="font-mono text-2xs text-ql-warning">[{f.severity}]</span> {f.issue}
                </li>
              ))}
            </ul>
          )}
          {task.feedback && task.state !== "COMPLETE" && (
            <div className="rounded-lg border border-ql-warning/30 bg-ql-warning/5 p-3">
              <p className="mb-1 label">Feedback for the next attempt</p>
              <pre className="selectable max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-fg-muted">{task.feedback}</pre>
            </div>
          )}
          <ArtifactList artifacts={artifacts} onOpen={onArtifact} />
          {runs.length > 0 && (
            <p className="text-2xs text-fg-faint">
              {runs.length} run(s): {runs.map((x) => `#${x.attempt} ${x.state.toLowerCase()} (${x.tool_calls} tool calls)`).join(" · ")}
            </p>
          )}
        </div>
      </div>
    </Section>
  );
}

function ArtifactList({ artifacts, onOpen }: { artifacts: UlArtifact[]; onOpen: (a: UlArtifact) => void }) {
  if (artifacts.length === 0) return <p className="text-[13px] text-fg-faint">No artifacts yet.</p>;
  return (
    <ul className="-mx-2 max-h-72 overflow-y-auto">
      {artifacts.map((a) => (
        <li key={a.id}>
          <button
            type="button"
            onClick={() => onOpen(a)}
            className="flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left hover:bg-white/[0.04]"
            data-testid="ul-artifact"
          >
            <AgentName id={a.agent} className="w-20 shrink-0" />
            <span className="w-16 shrink-0 font-mono text-2xs text-fg-faint">{a.kind}</span>
            <span className="min-w-0 flex-1 truncate text-[12.5px] text-fg-muted">{a.name}</span>
            <span className="font-mono text-2xs text-fg-faint">{(a.bytes / 1024).toFixed(1)} KB</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function RunsTable({ mission }: { mission: UlMission }) {
  if (mission.runs.length === 0) return <p className="text-[13px] text-fg-faint">No agent has run yet.</p>;
  return (
    <div className="max-h-72 overflow-auto">
      <table className="w-full text-left text-[12px]">
        <thead className="text-2xs text-fg-faint">
          <tr>
            {["Agent", "Task", "#", "State", "Tools", "Tokens in/out", "Cost", "Started"].map((h) => (
              <th key={h} className="px-1.5 py-1 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="font-mono text-fg-muted tabular">
          {mission.runs.map((r) => (
            <tr key={r.id} className="border-t border-ql-border/60" title={r.error ?? r.summary ?? undefined}>
              <td className="px-1.5 py-1">
                <AgentName id={r.agent} />
              </td>
              <td className="px-1.5 py-1">{r.task_id?.split(":")[1] ?? "plan"}</td>
              <td className="px-1.5 py-1">{r.attempt}</td>
              <td className={cx("px-1.5 py-1", r.state === "FAILED" && "text-ql-danger", r.state === "INTERRUPTED" && "text-ql-warning")}>
                {r.state.toLowerCase()}
              </td>
              <td className="px-1.5 py-1">{r.tool_calls}</td>
              <td className="px-1.5 py-1">
                {r.input_tokens}/{r.output_tokens}
              </td>
              <td className="px-1.5 py-1">{usd(r.cost_usd, 4)}</td>
              <td className="px-1.5 py-1">{time(r.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const SEVERITY: Record<string, string> = {
  warning: "text-ql-warning",
  error: "text-ql-danger",
  important: "text-ql",
};

export function Timeline({ events, showDebug = true }: { events: UlEvent[]; showDebug?: boolean }) {
  const shown = showDebug ? events : events.filter((e) => e.severity !== "debug");
  if (shown.length === 0) return <p className="text-[13px] text-fg-faint">No events yet.</p>;
  return (
    <ol className="max-h-80 space-y-1 overflow-y-auto" data-testid="ul-timeline">
      {shown.map((e) => (
        <li key={e.id} className="grid grid-cols-[64px_84px_minmax(0,1fr)] gap-2 text-[12px]">
          <span className="font-mono text-fg-faint tabular" title={e.at}>
            {time(e.at)}
          </span>
          <AgentName id={e.agent ?? "jarvis"} />
          <span className={cx("min-w-0 truncate", SEVERITY[e.severity] ?? (e.severity === "debug" ? "text-fg-faint" : "text-fg-muted"))} title={e.message}>
            {e.message}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function MissionCard({ m, onOpen }: { m: { id: string; title: string; state: UlMission["state"]; progress: { complete: number; total: number }; spent_usd: number; budget_usd: number; blocker: UlMission["blocker"]; created_at: string; project_kind: string }; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} className="w-full rounded-xl border border-ql-border bg-ql-raised p-4 text-left transition-colors hover:border-ql/40" data-testid="ul-mission-card">
      <div className="flex items-start gap-3">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[14px] text-fg">{m.title}</span>
          <span className="font-mono text-2xs text-fg-faint">
            {m.id} · {m.project_kind} · {ago(m.created_at)}
          </span>
        </span>
        <MissionState state={m.state} />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-4">
        <Fraction done={m.progress.complete} total={m.progress.total} label="Tasks" />
        <div className="text-2xs text-fg-faint">
          Spend
          <span className="block font-mono text-[12px] text-fg-muted">
            {usd(m.spent_usd)} / {usd(m.budget_usd)}
          </span>
        </div>
      </div>
      {m.blocker && m.state === "BLOCKED" && (
        <p className="mt-2 line-clamp-2 text-[12px] text-ql-danger">{m.blocker.message ?? m.blocker.questions?.join(" ")}</p>
      )}
    </button>
  );
}
