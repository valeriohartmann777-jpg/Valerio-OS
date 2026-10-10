import type { QmAgent, QmMission, QmMissionRow, QmTask, QmWaiting } from "@jarvis/protocol";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../../../lib/api";
import { Button, cx } from "../../ui/primitives";
import { Badge, Card, Hash, ago, inputClass } from "../ui";
import { Empty, FixtureBadge, Kv, usd, type Mode } from "../research/common";
import { BlueprintView } from "./BlueprintView";
import { DossierView } from "./DossierView";
import { EvolutionView } from "./EvolutionView";
import { MissionState, MissionVerdict, OPEN_STATES, STAGE_LABEL } from "./common";

/**
 * Research Room: every mission's stages, what each agent actually did (real task records),
 * the questions and approvals waiting for you, the verdict and the dossier.
 */

export function ResearchRoom({
  tick,
  mode,
  missionId,
  onPick,
  onOpenSource,
  onDataHub,
}: {
  tick: string | null;
  mode: Mode;
  missionId: string | null;
  onPick: (id: string) => void;
  onOpenSource: (id: string) => void;
  onDataHub: () => void;
}) {
  const [missions, setMissions] = useState<QmMissionRow[]>([]);
  const [agents, setAgents] = useState<QmAgent[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, a] = await Promise.all([api.qmMissions(), api.qmAgents()]);
      setMissions(m);
      setAgents(a);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Research missions aren't reachable.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, tick]);

  useEffect(() => {
    if (!missionId && missions[0]) onPick(missions[0].id);
  }, [missionId, missions, onPick]);

  return (
    <div className="grid gap-5 xl:grid-cols-[300px_minmax(0,1fr)]" data-testid="research-room">
      <div className="grid content-start gap-4">
        <Card title={`Missions · ${missions.length}`}>
          {!missions.length ? (
            <p className="text-[13px] text-fg-muted">No missions yet. Add an idea in the Idea Inbox — a mission starts when the source is read.</p>
          ) : (
            <ul className="grid gap-1" data-testid="qm-list">
              {missions.map((m) => (
                <li key={m.id}>
                  <button
                    type="button"
                    onClick={() => onPick(m.id)}
                    className={cx(
                      "w-full rounded-lg px-3 py-2 text-left",
                      missionId === m.id ? "bg-ql-raised" : "hover:bg-white/[0.03]",
                    )}
                    data-testid={`qm-pick-${m.id}`}
                  >
                    <span className="block truncate text-[13px] text-fg">{m.title}</span>
                    <span className="mt-1 flex flex-wrap items-center gap-1.5">
                      {m.kind === "evolution" && <Badge tone="accent">Evolution</Badge>}
                      {m.verdict ? <MissionVerdict verdict={m.verdict} /> : <MissionState state={m.state} />}
                    </span>
                    <span className="mt-1 block text-2xs text-fg-faint">
                      {m.stage ? STAGE_LABEL[m.stage] ?? m.stage : "—"} · {ago(m.updated_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <AgentRoster agents={agents} />
      </div>
      <div className="min-w-0">
        {error && <p className="mb-3 text-[13px] text-ql-danger">{error}</p>}
        {missionId ? (
          <MissionDetail key={missionId} id={missionId} tick={tick} mode={mode} onPick={onPick} onOpenSource={onOpenSource} onDataHub={onDataHub} onChanged={load} />
        ) : (
          <Empty title="No mission selected" />
        )}
      </div>
    </div>
  );
}

function AgentRoster({ agents }: { agents: QmAgent[] }) {
  return (
    <Card title="ULTRON research team">
      <ul className="grid gap-2" data-testid="qm-agents">
        {agents.map((a) => (
          <li key={a.id} className={cx("text-[12px]", !a.active && "opacity-50")} data-testid={`qm-agent-${a.id}`}>
            <div className="flex items-center gap-2">
              <span className="font-mono text-[11px] tracking-[0.1em] text-fg">{a.name}</span>
              <span className="text-2xs text-fg-faint">
                {a.status === "working" ? "◌ working" : a.status === "idle" ? "○ idle" : "not used"}
              </span>
              {a.spent_usd > 0 && <span className="ml-auto font-mono text-2xs text-fg-faint">{usd(a.spent_usd, false, 3)}</span>}
            </div>
            <p className="text-2xs text-fg-muted">{a.role}</p>
            <p className="text-[10px] text-fg-faint">{a.engine}</p>
            {a.current.map((c) => (
              <p key={c.task_id} className="text-2xs text-ql">→ {c.objective}</p>
            ))}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function MissionDetail({
  id,
  tick,
  mode,
  onPick,
  onOpenSource,
  onDataHub,
  onChanged,
}: {
  id: string;
  tick: string | null;
  mode: Mode;
  onPick: (id: string) => void;
  onOpenSource: (id: string) => void;
  onDataHub: () => void;
  onChanged: () => void;
}) {
  const [m, setM] = useState<QmMission | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setM(await api.qmMission(id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "This mission isn't reachable.");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load, tick]);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      setError(null);
      await fn();
      await load();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That didn't work.");
    }
  };

  if (!m) return error ? <p className="text-[13px] text-ql-danger">{error}</p> : <p className="text-[13px] text-fg-muted">Loading…</p>;

  const open = OPEN_STATES.includes(m.state);
  const stageIndex = m.stage ? m.stages.indexOf(m.stage) : -1;
  const verdict = m.refs.verdict;
  const cap = m.budget.max_model_usd ?? 0;

  return (
    <div className="grid gap-5" data-testid="qm-detail" data-state={m.state}>
      <Card>
        <div className="flex flex-wrap items-start gap-3">
          <div className="mr-auto min-w-0">
            <p className="font-mono text-2xs tracking-[0.16em] text-fg-faint uppercase">
              {m.kind === "evolution" ? "Strategy evolution" : "Idea-to-Edge research"} · {m.model_label}
            </p>
            <h2 className="mt-1 truncate text-xl font-light text-fg">{m.title}</h2>
            <p className="mt-1 text-[12px] text-fg-muted">
              Model spend {usd(m.spent_usd, false, 3)} of {usd(cap)} cap · paid data needs your approval (cap {usd(m.budget.max_paid_data_usd ?? 0)})
              {m.source_id && (
                <>
                  {" · "}
                  <button type="button" className="underline" onClick={() => onOpenSource(m.source_id as string)}>
                    source
                  </button>
                </>
              )}
              {m.parent_id && (
                <>
                  {" · "}
                  <button type="button" className="underline" onClick={() => onPick(m.parent_id as string)}>
                    parent mission
                  </button>
                </>
              )}
            </p>
          </div>
          {m.verdict ? <MissionVerdict verdict={m.verdict} /> : <MissionState state={m.state} />}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {m.state === "RUNNING" && (
            <Button onClick={() => void act(() => api.qmControl(m.id, "pause"))} data-testid="qm-pause">
              Pause
            </Button>
          )}
          {(m.state === "PAUSED" || m.state === "BLOCKED") && (
            <Button variant="primary" onClick={() => void act(() => api.qmControl(m.id, "resume"))} data-testid="qm-resume">
              Resume
            </Button>
          )}
          {open && (
            <Button variant="danger" onClick={() => void act(() => api.qmControl(m.id, "cancel"))} data-testid="qm-cancel">
              Cancel mission
            </Button>
          )}
          {m.kind === "idea" && m.state === "COMPLETE" && m.refs.run_id && (
            <Button
              variant="primary"
              onClick={() =>
                void act(async () => {
                  const child = await api.qmEvolve(m.id);
                  onPick(child.id);
                })
              }
              title="CIPHER proposes reasoned variants within the budget; each is tested with the holdout sealed and audited by SENTINEL"
              data-testid="qm-evolve"
            >
              Evolve strategy
            </Button>
          )}
        </div>
        {error && <p className="mt-3 text-[13px] text-ql-danger" data-testid="qm-error">{error}</p>}
        {m.error && m.state !== "COMPLETE" && <p className="mt-3 text-[13px] text-ql-danger">{m.error}</p>}

        <ol className="mt-5 flex flex-wrap gap-1" aria-label="Stages" data-testid="qm-stages">
          {m.stages.map((s, i) => {
            const done = m.state === "COMPLETE" || i < stageIndex;
            const current = i === stageIndex && m.state !== "COMPLETE";
            return (
              <li
                key={s}
                className={cx(
                  "rounded-md border px-2 py-1 text-2xs",
                  done ? "border-ql-positive/40 text-ql-positive" : current ? "border-ql text-ql" : "border-ql-border text-fg-faint",
                )}
                aria-current={current ? "step" : undefined}
              >
                {done ? "✓ " : current ? "◌ " : ""}
                {STAGE_LABEL[s] ?? s}
              </li>
            );
          })}
        </ol>
      </Card>

      {m.waiting && open && (
        <Waiting mission={m} waiting={m.waiting} onAct={act} onDataHub={onDataHub} />
      )}

      {verdict && (
        <Card title="Verdict" testId="qm-verdict-card">
          <div className="flex flex-wrap items-center gap-3">
            <MissionVerdict verdict={verdict.label} />
            {m.refs.audit && (
              <Badge tone={m.refs.audit.passed ? "positive" : "danger"} icon={m.refs.audit.passed ? "✓" : "✕"}>
                SENTINEL audit {m.refs.audit.passed ? "passed" : "failed"}
              </Badge>
            )}
            {verdict.fixture && <FixtureBadge />}
          </div>
          <ul className="mt-3 grid gap-1 text-[13px] text-fg-muted">
            {verdict.reasons.map((r) => (
              <li key={r}>· {r}</li>
            ))}
          </ul>
          {verdict.note && (
            <div className="mt-3 rounded-lg border border-ql-border px-3 py-2 text-[13px]">
              <p className="text-fg">{verdict.note.text}</p>
              <p className="mt-1 text-fg-muted">Next step: {verdict.note.next_step}</p>
              <p className="mt-1 text-2xs text-fg-faint">
                {verdict.note.checked
                  ? "Written by JARVIS; every number in it was checked against the evidence."
                  : "The model's note was not used (no model, or it failed the evidence check); this line comes straight from the evidence."}
              </p>
            </div>
          )}
          <p className="mt-3 text-2xs text-fg-faint">
            Research result under the tested assumptions only. Not a trading recommendation; past simulated results do not
            predict future returns.
          </p>
        </Card>
      )}

      {(m.trials.length > 0 || m.kind === "evolution") && <EvolutionView mission={m} onAct={act} />}

      {m.blueprint && mode !== "Simple" && <BlueprintView blueprint={m.blueprint} mode={mode} />}

      {mode !== "Simple" && (m.refs.boundary || m.refs.audit) && (
        <Card title="Checks">
          {[m.refs.boundary, m.refs.audit].filter(Boolean).map((report) =>
            report ? (
              <div key={report.kind} className="mb-4">
                <p className="label mb-1">
                  {report.kind} · <Hash value={report.sha256} />
                </p>
                <ul className="grid gap-1 text-[12px]">
                  {report.checks.map((c) => (
                    <li key={c.id} className="grid grid-cols-[56px_minmax(0,1fr)] gap-2">
                      <span className={cx("font-mono text-2xs", c.result === "PASS" ? "text-ql-positive" : c.result === "WARN" ? "text-ql-warning" : "text-ql-danger")}>
                        {c.result === "PASS" ? "✓" : c.result === "WARN" ? "◐" : "✕"} {c.result}
                      </span>
                      <span className="text-fg-muted">
                        <span className="text-fg">{c.title}</span> — {c.detail}
                      </span>
                    </li>
                  ))}
                </ul>
                {report.limitation && <p className="mt-1 text-2xs text-fg-faint">{report.limitation}</p>}
              </div>
            ) : null,
          )}
        </Card>
      )}

      <Card title="What the agents did" aside={<span className="text-2xs text-fg-faint">{m.tasks.length} tasks · real records</span>}>
        <ol className="grid gap-2" data-testid="qm-tasks">
          {m.tasks.map((t) => (
            <TaskRow key={t.id} task={t} mode={mode} />
          ))}
        </ol>
      </Card>

      {mode === "Institutional" && (
        <Card title="Mission record">
          <dl>
            <Kv label="Mission" mono>{m.id}</Kv>
            <Kv label="Budget hash" mono>{m.budget_sha256}</Kv>
            {m.refs.spec_sha256 && <Kv label="Spec hash" mono>{m.refs.spec_sha256}</Kv>}
            {m.refs.protocol && <Kv label="Protocol hash" mono>{m.refs.protocol.sha256}</Kv>}
            {m.refs.dataset_id && <Kv label="Dataset" mono>{m.refs.dataset_id}</Kv>}
            {m.refs.run_id && <Kv label="Run" mono>{m.refs.run_id}</Kv>}
            <Kv label="Budget" mono>{JSON.stringify(m.budget)}</Kv>
          </dl>
        </Card>
      )}

      {m.state === "COMPLETE" && <DossierView missionId={m.id} />}
    </div>
  );
}

function TaskRow({ task, mode }: { task: QmTask; mode: Mode }) {
  const [openRow, setOpenRow] = useState(false);
  const tone =
    task.state === "COMPLETE"
      ? "text-ql-positive"
      : task.state === "RUNNING"
        ? "text-ql"
        : task.state === "FAILED" || task.state === "VETOED" || task.state === "BLOCKED"
          ? "text-ql-danger"
          : task.state === "WAITING"
            ? "text-ql-warning"
            : "text-fg-faint";
  return (
    <li className="rounded-lg border border-ql-border px-3 py-2" data-testid="qm-task" data-agent={task.agent}>
      <button type="button" className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setOpenRow(!openRow)}>
        <span className="w-16 font-mono text-[11px] tracking-[0.1em] text-fg uppercase">{task.agent}</span>
        <span className="mr-auto min-w-0 text-[13px] text-fg">{task.objective}</span>
        <span className={cx("font-mono text-2xs", tone)}>{task.state}</span>
        {task.cost_usd > 0 && <span className="font-mono text-2xs text-fg-faint">{usd(task.cost_usd, false, 3)}</span>}
        <span className="text-fg-faint" aria-hidden>{openRow ? "▾" : "▸"}</span>
      </button>
      {openRow && (
        <div className="mt-2 grid gap-1 text-[12px]">
          {task.actions.map((a, i) => (
            <p key={i} className="text-fg-muted">
              <span className="font-mono text-2xs text-fg-faint">{a.at.slice(11, 19)}</span> {a.text}
            </p>
          ))}
          {task.limitations.map((l) => (
            <p key={l} className="text-ql-warning">◐ {l}</p>
          ))}
          {task.error && <p className="text-ql-danger">✕ {task.error}</p>}
          {mode !== "Simple" && (
            <p className="font-mono text-2xs text-fg-faint">
              {task.kind} · {task.model ?? "no model"} · tools: {task.allowed_tools.join(", ") || "none"} · inputs <Hash value={task.inputs_hash} />
            </p>
          )}
        </div>
      )}
    </li>
  );
}

function Waiting({
  mission,
  waiting,
  onAct,
  onDataHub,
}: {
  mission: QmMission;
  waiting: QmWaiting;
  onAct: (fn: () => Promise<unknown>) => Promise<void>;
  onDataHub: () => void;
}) {
  if (waiting.kind === "questions") return <QuestionForm mission={mission} waiting={waiting} onAct={onAct} />;
  if (waiting.kind === "connect_data") {
    return (
      <Card title="Needs you · data connection" testId="qm-wait-connect">
        <p className="text-[13px] text-fg-muted">
          VECTOR needs Databento to check which historical data exists and what it costs. Connect your key once in the
          Data Hub (it goes straight into the macOS Keychain — never into chat, logs or prompts). The mission continues by
          itself.
        </p>
        <Button className="mt-3" variant="primary" onClick={onDataHub} data-testid="qm-go-datahub">
          Open Data Hub
        </Button>
      </Card>
    );
  }
  if (waiting.kind === "data_purchase") return <DataApproval mission={mission} waiting={waiting} onAct={onAct} />;
  return null; // lock_candidate is handled in the evolution view
}

function QuestionForm({
  mission,
  waiting,
  onAct,
}: {
  mission: QmMission;
  waiting: QmWaiting;
  onAct: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const questions = waiting.questions ?? [];
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [values, setValues] = useState<Record<string, string>>({});
  const terms = questions.filter((q) => q.kind === "term");
  const fields = questions.filter((q) => q.kind === "field");
  const notes = questions.filter((q) => q.kind === "note");
  const send = (acceptDefaults: boolean) =>
    onAct(() =>
      api.qmAnswer(mission.id, {
        accept_defaults: acceptDefaults,
        choices,
        values: Object.fromEntries(
          Object.entries(values)
            .filter(([, v]) => v.trim() !== "")
            .map(([k, v]) => {
              const q = fields.find((f) => f.field === k);
              return [k, q?.input === "number" ? Number(v) : v];
            }),
        ),
      }),
    );
  const anyDefault = terms.some((t) => t.default);

  return (
    <Card title="Needs you · define the rules" testId="qm-wait-questions">
      <p className="mb-4 text-[13px] text-fg-muted">
        These parts are undefined in the source and change what gets tested. Pick a definition — or accept the research
        defaults, which are then labelled as defaults (not as the source's words) in every result.
      </p>
      <div className="grid gap-4">
        {terms.map((q) => (
          <fieldset key={q.term} className="rounded-lg border border-ql-border px-3 py-2" data-testid={`qm-q-${q.term}`}>
            <legend className="px-1 text-[13px] text-fg">
              {q.question} {q.material && <span className="text-2xs text-ql-warning">· changes results</span>}
            </legend>
            <div className="mt-1 grid gap-1">
              {q.options?.map((o) => (
                <label key={o.id} className="flex cursor-pointer items-start gap-2 text-[12px]">
                  <input
                    type="radio"
                    name={q.term}
                    value={o.id}
                    checked={choices[q.term as string] === o.id}
                    onChange={() => setChoices({ ...choices, [q.term as string]: o.id })}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="text-fg">{o.label}</span>
                    {q.default === o.id && <span className="ml-1 text-2xs text-fg-faint">(research default)</span>}
                    {o.definition && <span className="block text-fg-muted">{o.definition}</span>}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        ))}
        {fields.map((q) => (
          <label key={q.field} className="block" data-testid={`qm-f-${q.field}`}>
            <span className="mb-1 block text-[12px] text-fg">{q.question}</span>
            {q.options ? (
              <select className={inputClass} value={values[q.field as string] ?? ""} onChange={(e) => setValues({ ...values, [q.field as string]: e.target.value })}>
                <option value="">Choose…</option>
                {q.options.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className={inputClass}
                inputMode="decimal"
                value={values[q.field as string] ?? ""}
                onChange={(e) => setValues({ ...values, [q.field as string]: e.target.value })}
                placeholder="number"
              />
            )}
          </label>
        ))}
        {notes.map((q, i) => (
          <p key={i} className="text-[12px] text-fg-muted">
            ✎ {q.question} <span className="text-fg-faint">— answer with a note on the source.</span>
          </p>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          variant="primary"
          disabled={!Object.keys(choices).length && !Object.values(values).some((v) => v.trim())}
          onClick={() => void send(false)}
          data-testid="qm-answer"
        >
          Use my answers
        </Button>
        {anyDefault && (
          <Button onClick={() => void send(true)} data-testid="qm-accept-defaults">
            Accept research defaults for the rest
          </Button>
        )}
      </div>
    </Card>
  );
}

function DataApproval({
  mission,
  waiting,
  onAct,
}: {
  mission: QmMission;
  waiting: QmWaiting;
  onAct: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const cost = waiting.cost_usd ?? 0;
  const [max, setMax] = useState(() => (Math.ceil(cost * 1.1 * 100) / 100).toFixed(2));
  const [confirmed, setConfirmed] = useState(false);
  const request = waiting.request ?? {};
  const maxNumber = Number(max);
  const valid = Number.isFinite(maxNumber) && maxNumber >= cost && maxNumber > 0;
  return (
    <Card title="Needs your approval · paid data" testId="qm-wait-purchase">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-2xl font-light text-fg" data-testid="qm-quote-cost">
          {usd(cost)}
        </p>
        <span className="text-[12px] text-fg-muted">estimated by Databento for this request (quote {waiting.quote_id})</span>
        {waiting.fixture && <FixtureBadge label="Fixture provider · no real charge" />}
      </div>
      <dl className="mt-3">
        <Kv label="Data">
          {String(request.symbols ?? "")} · {String(request.schema ?? "")} + definitions · {String(request.dataset ?? "")}
        </Kv>
        <Kv label="Period">
          {String(request.start ?? "")} → {String(request.end ?? "")} (UTC, end exclusive)
        </Kv>
        <Kv label="Mission data cap">
          {usd(waiting.mission_cap_usd ?? 0)} {waiting.within_mission_budget ? "· within cap" : "· ABOVE the mission cap — approval will be refused"}
        </Kv>
      </dl>
      <p className="mt-3 text-[12px] text-fg-muted">
        JARVIS never buys data on its own. Approving re-prices the request; if Databento's price is above your maximum the
        purchase is refused. Days already cached are reused, and a resumed mission never buys the same days twice.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="block w-40">
          <span className="mb-1 block text-xs text-ql-muted">Your maximum (USD)</span>
          <input className={inputClass} inputMode="decimal" value={max} onChange={(e) => setMax(e.target.value)} data-testid="qm-max-usd" />
        </label>
        <label className="flex items-center gap-2 text-[12px] text-fg">
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} data-testid="qm-confirm" />
          I approve this charge to my Databento account
        </label>
        <Button
          variant="warning"
          disabled={!confirmed || !valid || !waiting.within_mission_budget}
          onClick={() => void onAct(() => api.qmApproveData(mission.id, maxNumber))}
          data-testid="qm-approve"
        >
          Approve purchase
        </Button>
        <Button onClick={() => void onAct(() => api.qmDeclineData(mission.id))} data-testid="qm-decline">
          Decline
        </Button>
      </div>
    </Card>
  );
}
