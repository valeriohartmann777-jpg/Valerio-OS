import type { BlOptions, HqSnapshot, TmGrant, TmProject, TmTask } from "@jarvis/protocol";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../../../lib/api";
import { clock } from "../../../lib/format";
import { go } from "../../../lib/history";
import { useJarvis } from "../../../store/store";
import { verdictLabel } from "../../quantlab/research/common";
import { BacktestLab } from "../../quantlab/research/Results";
import { BacktestLauncher } from "../../quantlab/vault/BacktestLauncher";
import { Icon } from "../../ui/icons";
import { Button, cx } from "../../ui/primitives";
import { EmptyState, ErrorState, Panel, StatusPill, WorkspaceModeBadge, type PillTone } from "../../ui/shell";
import { RobotFigure } from "./Robot";
import { STATE_PILL } from "./World";

/**
 * The ULTRON QuantLab (brief §7.6): the team research room on the *same* QuantLab engine and
 * Data Vault as Solo, with its own workspace. A strategy enters only through the owner's
 * "Share with ULTRON" (a copy); robots act only within the grants, checked by the server.
 */

const MEMBERS = ["cipher", "atlas", "sentinel", "archive", "vector"];
const GRANT_ORDER: TmGrant[] = ["read_spec", "run_backtests", "propose_versions"];
const TASK_TONE: Record<TmTask["state"], PillTone> = { QUEUED: "muted", RUNNING: "info", COMPLETE: "success", FAILED: "danger" };

function useTeamTick(): string | null {
  return useJarvis((s) => {
    for (let i = s.activity.length - 1; i >= 0; i--) {
      const e = s.activity[i];
      if (e && (e.type.startsWith("team.") || e.type.startsWith("quantlab.mission") || e.type.startsWith("quantlab.research"))) return e.id;
    }
    return null;
  });
}

const message = (e: unknown) => (e instanceof ApiError ? `${e.message}${e.suggestion ? ` — ${e.suggestion}` : ""}` : "That didn't work.");

export function TeamQuantLab({ hq }: { hq: HqSnapshot | null }) {
  const [projects, setProjects] = useState<TmProject[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tick = useTeamTick();

  const refresh = useCallback(async () => {
    try {
      const list = await api.tmProjects();
      setProjects(list);
      setSelected((current) => current ?? list.find((p) => p.state === "ACTIVE")?.id ?? null);
      setError(null);
    } catch (e) {
      setError(message(e));
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh, tick]);

  const project = projects?.find((p) => p.id === selected) ?? null;
  const members = hq?.robots.filter((r) => MEMBERS.includes(r.id)) ?? [];
  return (
    <div className="space-y-5" data-testid="team-quantlab" style={{ "--area": "var(--color-quant)" } as React.CSSProperties}>
      <Panel title="ULTRON QuantLab" aside={<WorkspaceModeBadge mode="team" />}>
        <p className="max-w-[900px] text-[14px] leading-relaxed text-fg-muted">
          The team research lab. It uses <b className="text-fg">the same QuantLab engine and Data Vault</b> as your Solo QuantLab,
          in its own workspace: the team only sees a strategy you share, works on its own copy, and acts only within the access you
          grant. Every proposed improvement becomes a new team version with a registered trial; the holdout stays sealed until you
          open it.
        </p>
        <ul className="mt-4 flex flex-wrap gap-3" aria-label="Team members">
          {members.map((m) => (
            <li key={m.id} className="flex items-center gap-2 rounded-xl border border-hairline-strong bg-surface-2/50 py-1 pr-3 pl-1" data-testid={`tm-member-${m.id}`}>
              <RobotFigure id={m.id} color={m.color} state={m.state} size={30} />
              <span>
                <span className="block text-[12px] font-black text-fg">{m.name}</span>
                <StatusPill tone={STATE_PILL[m.state]}>{m.state_label}</StatusPill>
              </span>
            </li>
          ))}
        </ul>
      </Panel>

      {error && <ErrorState title="The team workspace isn't reachable" onRetry={() => void refresh()}>{error}</ErrorState>}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(280px,360px)_minmax(0,1fr)]">
        <div className="space-y-5">
          <Panel title="Team projects">
            {projects === null ? (
              <p className="text-[13px] text-fg-muted">Loading…</p>
            ) : projects.length === 0 ? (
              <p className="text-[13px] text-fg-muted">Nothing shared yet. Share a Solo strategy below to start.</p>
            ) : (
              <ul className="space-y-2">
                {projects.map((p) => (
                  <li key={p.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(p.id)}
                      aria-pressed={selected === p.id}
                      className={cx("w-full rounded-xl border p-3 text-left", selected === p.id ? "border-quant bg-surface-2" : "border-hairline-strong hover:border-control")}
                      data-testid="tm-project"
                    >
                      <span className="block text-[14px] font-bold text-fg">{p.name}</span>
                      <span className="block text-[12px] text-fg-muted">
                        {p.state === "CLOSED" ? "Closed · " : ""}
                        {p.team_strategy.versions.length} team version(s) · {p.runs.length} run(s) · {p.tasks.length} robot task(s)
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <ShareForm onShared={(p) => { setSelected(p.id); void refresh(); }} />
        </div>

        {project ? (
          <ProjectView key={project.id} project={project} hq={hq} tick={tick} onChange={() => void refresh()} />
        ) : (
          <EmptyState icon="share" title="No team project selected">
            Your Solo strategies stay private. Share one with ULTRON to let CIPHER, SENTINEL and the others work on a copy.
          </EmptyState>
        )}
      </div>
    </div>
  );
}

function ShareForm({ onShared }: { onShared: (p: TmProject) => void }) {
  const [options, setOptions] = useState<BlOptions | null>(null);
  const [versionId, setVersionId] = useState("");
  const [grants, setGrants] = useState<Record<TmGrant, boolean>>({ read_spec: true, run_backtests: true, propose_versions: false });
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.blOptions().then((o) => {
      setOptions(o);
      setVersionId((v) => v || o.strategies.find((s) => s.state === "READY")?.version_id || "");
    }, () => undefined);
  }, []);
  const ready = (options?.strategies ?? []).filter((s) => s.state === "READY");
  const share = async () => {
    setBusy(true);
    setError(null);
    try {
      const p = await api.tmShare({ version_id: versionId, grants, confirm });
      setConfirm(false);
      onShared(p);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel title="Share with ULTRON" testId="tm-share">
      <label className="block text-[13px] text-fg-muted">
        Solo strategy
        <select className="mt-1 h-10 w-full rounded-lg border border-control/70 bg-surface-2 px-3 text-[14px] text-fg" value={versionId} onChange={(e) => setVersionId(e.target.value)} data-testid="tm-share-version">
          {ready.length === 0 && <option value="">No saved strategy ready yet</option>}
          {ready.map((s) => (
            <option key={s.version_id} value={s.version_id}>
              {s.name} · v{s.version_number}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="mt-3 space-y-2">
        <legend className="text-[13px] text-fg-muted">What the team may do</legend>
        {GRANT_ORDER.map((g) => (
          <label key={g} className="flex items-start gap-2 text-[13px] text-fg">
            <input type="checkbox" className="mt-0.5" checked={grants[g]} onChange={(e) => setGrants((x) => ({ ...x, [g]: e.target.checked }))} data-testid={`tm-share-grant-${g}`} />
            {GRANT_TEXT[g]}
          </label>
        ))}
      </fieldset>
      <label className="mt-3 flex items-start gap-2 text-[13px] text-fg-muted">
        <input type="checkbox" className="mt-0.5" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} data-testid="tm-share-confirm" />
        The team gets a copy of this version. My Solo strategy stays private and unchanged.
      </label>
      {error && <p className="mt-2 text-[13px] text-danger">{error}</p>}
      <Button
        className="mt-3"
        variant="primary"
        disabled={busy || !versionId || !confirm}
        reason={!versionId ? "Save a strategy in your Solo QuantLab first." : !confirm ? "Tick the confirmation first." : null}
        onClick={() => void share()}
        data-testid="tm-share-submit"
      >
        <Icon name="share" size={15} /> Share with ULTRON
      </Button>
    </Panel>
  );
}

const GRANT_TEXT: Record<TmGrant, string> = {
  read_spec: "Read the shared copy and its results",
  run_backtests: "Run backtests on the shared Data Vault (local data, no purchases)",
  propose_versions: "Propose improved versions (each a new team version + registered trial)",
};

function ProjectView({ project, hq, tick, onChange }: { project: TmProject; hq: HqSnapshot | null; tick: string | null; onChange: () => void }) {
  const [runId, setRunId] = useState<string | null>(project.runs.find((r) => r.status === "COMPLETED")?.id ?? null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const active = project.state === "ACTIVE";
  const latest = project.runs.find((r) => r.status === "COMPLETED") ?? null;
  const g = project.grants;
  const act = async (key: string, call: () => Promise<unknown>, done?: string) => {
    setBusy(key);
    setError(null);
    try {
      await call();
      if (done) setNote(done);
      onChange();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(null);
    }
  };
  const missing = (needs: TmGrant[]) => needs.filter((n) => !g[n]).map((n) => GRANT_TEXT[n].toLowerCase());
  const sentinelWhy = !active ? "The project is closed." : missing(["read_spec"]).length ? `Grant first: ${missing(["read_spec"]).join("; ")}.` : !latest ? "Run a team backtest first." : null;
  const cipherWhy = !active
    ? "The project is closed."
    : missing(["read_spec", "run_backtests", "propose_versions"]).length
      ? `Grant first: ${missing(["read_spec", "run_backtests", "propose_versions"]).join("; ")}.`
      : !latest
        ? "Run a team backtest first."
        : hq && !hq.ai.ready
          ? hq.ai.reason
          : latest.kind !== "validation"
            ? "CIPHER needs a validation run (the team backtest default)."
            : null;

  return (
    <div className="space-y-5" data-testid="tm-project-view">
      <Panel
        title={project.name}
        aside={
          <span className="flex items-center gap-2">
            <WorkspaceModeBadge mode="team" />
            {active && (
              <Button onClick={() => void act("close", () => api.tmClose(project.id))} disabled={busy !== null}>
                Close project
              </Button>
            )}
          </span>
        }
      >
        <p className="text-[13px] text-fg-muted">
          Team copy of your Solo strategy (Solo version <code className="font-mono text-xs">{project.solo_version_id}</code>). Your Solo
          strategy isn&apos;t visible to the robots and can&apos;t be changed from here.
        </p>
        <Access project={project} disabled={!active || busy !== null} onSave={(grants) => act("grants", () => api.tmGrants(project.id, grants, true), "Access updated — logged in the audit below.")} />
        {note && <p className="mt-2 text-[13px] text-success" role="status">{note}</p>}
        {error && <p className="mt-2 text-[13px] text-danger" data-testid="tm-error">{error}</p>}
      </Panel>

      {active && (
        <BacktestLauncher tick={tick} teamProject={project.id} onRun={(id) => { setRunId(id); onChange(); }} onVault={() => go({ name: "quantlab", section: "data-vault" })} />
      )}

      <Panel title="Ask the robots" testId="tm-ask">
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy !== null || sentinelWhy !== null} reason={sentinelWhy} onClick={() => void act("sentinel", () => api.tmAsk(project.id, "sentinel", "challenge", latest?.id))} data-testid="tm-ask-sentinel">
            Ask SENTINEL to challenge the latest run
          </Button>
          <Button variant="primary" disabled={busy !== null || cipherWhy !== null} reason={cipherWhy} onClick={() => void act("cipher", () => api.tmAsk(project.id, "cipher", "improve", latest?.id))} data-testid="tm-ask-cipher">
            Ask CIPHER to propose improvements
          </Button>
        </div>
        {(sentinelWhy || cipherWhy) && <p className="mt-2 text-[12px] text-fg-muted">{cipherWhy ?? sentinelWhy}</p>}
        {project.tasks.length > 0 && (
          <ul className="mt-4 divide-y divide-hairline" data-testid="tm-tasks">
            {project.tasks.map((t) => (
              <TaskRow key={t.id} task={t} />
            ))}
          </ul>
        )}
        {project.missions.map((m) => (
          <Trials key={m.id} mission={m} />
        ))}
      </Panel>

      <Panel title="Team versions" testId="tm-versions">
        <ul className="divide-y divide-hairline">
          {project.team_strategy.versions.map((v) => (
            <li key={v.id} className="flex flex-wrap items-center justify-between gap-3 py-2 text-[13px]" data-testid="tm-version">
              <span>
                <b className="text-fg">v{v.number}</b> <span className="text-fg-muted">· {v.origin === "team-share" ? "shared from Solo" : v.origin === "ai" ? "proposed by CIPHER" : v.origin} · {clock(v.created_at)}</span>
                {v.note && <span className="block text-[12px] text-fg-muted">{v.note}</span>}
              </span>
              {v.origin !== "team-share" && active && (
                <Button
                  onClick={() => {
                    if (window.confirm("Copy this team version into your Solo strategy as a new version? Nothing is overwritten.")) {
                      void act("return", () => api.tmReturn(project.id, v.id, true), "Copied to your Solo strategy as a new version.");
                    }
                  }}
                  disabled={busy !== null}
                  data-testid="tm-return"
                >
                  Copy to my Solo strategy
                </Button>
              )}
            </li>
          ))}
        </ul>
      </Panel>

      {project.runs.length > 0 && (
        <BacktestLab runId={runId} runs={project.runs} tick={tick} mode="Research" onPick={setRunId} onTrade={() => undefined} />
      )}

      <Panel title="Audit" testId="tm-audit">
        <ul className="space-y-1 text-[12px] text-fg-muted">
          {project.audit.map((a) => (
            <li key={a.id}>
              <span className="font-mono">{clock(a.at)}</span> · {a.actor} · {a.action}
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}

function Access({ project, disabled, onSave }: { project: TmProject; disabled: boolean; onSave: (g: Record<TmGrant, boolean>) => void }) {
  const [draft, setDraft] = useState(project.grants);
  const [confirm, setConfirm] = useState(false);
  const changed = GRANT_ORDER.some((k) => draft[k] !== project.grants[k]);
  return (
    <div className="mt-3 rounded-xl border border-hairline bg-canvas-2/40 p-3" data-testid="tm-access">
      <p className="text-[12px] font-bold tracking-[0.08em] text-fg-muted uppercase">Access for the team</p>
      <div className="mt-2 space-y-1.5">
        {GRANT_ORDER.map((k) => (
          <label key={k} className="flex items-center gap-2 text-[13px] text-fg">
            <input type="checkbox" checked={draft[k]} disabled={disabled} onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.checked }))} data-testid={`tm-grant-${k}`} />
            {GRANT_TEXT[k]}
          </label>
        ))}
      </div>
      {changed && (
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-[13px] text-fg-muted">
            <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} data-testid="tm-grant-confirm" />I confirm this change of access
          </label>
          <Button variant="primary" disabled={!confirm || disabled} reason={!confirm ? "Tick the confirmation first." : null} onClick={() => { onSave(draft); setConfirm(false); }} data-testid="tm-grant-save">
            Save access
          </Button>
        </div>
      )}
    </div>
  );
}

function TaskRow({ task }: { task: TmTask }) {
  const r = (task.result ?? {}) as { passed?: boolean; failed?: unknown[]; warnings?: { name?: string; detail?: string }[]; checked?: number; trials?: number; mission_state?: string; second_engine?: { agree?: number; compared?: number; skipped?: boolean } };
  return (
    <li className="py-2 text-[13px]" data-testid="tm-task" data-agent={task.agent} data-state={task.state}>
      <div className="flex items-center justify-between gap-3">
        <b className="text-fg">{task.agent.toUpperCase()} · {task.kind === "challenge" ? "challenge" : "improvement round"}</b>
        <StatusPill tone={TASK_TONE[task.state]}>{task.state.toLowerCase()}</StatusPill>
      </div>
      <p className="mt-0.5 text-[12px] text-fg-muted">
        {task.state === "FAILED"
          ? task.error
          : task.agent === "sentinel" && task.result
            ? `${r.passed ? "All blocking checks passed" : `${r.failed?.length ?? 0} check(s) failed`} · ${r.warnings?.length ?? 0} warning(s) · ${r.checked ?? 0} checks · ${secondEngine(r.second_engine)}`
            : task.agent === "cipher" && task.result
              ? `${missionState(r.mission_state)} · ${r.trials ?? 0} trial(s) registered`
              : `Run ${task.request.run_id}`}
      </p>
    </li>
  );
}

function Trials({ mission }: { mission: TmProject["missions"][number] }) {
  if (mission.trials.length === 0) return null;
  return (
    <div className="mt-4" data-testid="tm-trials">
      <p className="text-[12px] font-bold tracking-[0.08em] text-fg-muted uppercase">
        {mission.title} · {mission.state.toLowerCase()} · trial ledger (append-only)
      </p>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-[12px]">
          <thead className="text-fg-muted">
            <tr>
              <th className="py-1 pr-3 font-semibold">#</th>
              <th className="py-1 pr-3 font-semibold">Variant</th>
              <th className="py-1 pr-3 font-semibold">OOS net</th>
              <th className="py-1 pr-3 font-semibold">OOS trades</th>
              <th className="py-1 pr-3 font-semibold">Audit</th>
              <th className="py-1 font-semibold">Verdict</th>
            </tr>
          </thead>
          <tbody className="text-fg">
            {mission.trials.map((t) => (
              <tr key={t.id} className="border-t border-hairline" data-testid="tm-trial">
                <td className="py-1 pr-3 font-mono">{t.iteration}</td>
                <td className="py-1 pr-3">
                  {t.title}
                  {t.mechanism && <span className="block text-fg-muted">{t.mechanism}</span>}
                </td>
                <td className="py-1 pr-3 font-mono">{t.metrics?.oos_net ?? "—"}</td>
                <td className="py-1 pr-3 font-mono">{t.metrics?.oos_trades ?? "—"}</td>
                <td className="py-1 pr-3">{t.audit?.passed === true ? "passed" : t.audit?.passed === false ? "failed" : "—"}</td>
                <td className="py-1">{t.metrics?.verdict ? verdictLabel(t.metrics.verdict as never) : t.outcome.toLowerCase()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-[12px] text-fg-muted">Each variant is a new team version tested with the holdout sealed. Many variants raise the risk of data-mining — compare them on the Pareto view, not on one number.</p>
    </div>
  );
}

/** Never a pass that didn't happen: the second engine only covers 1-minute bars. */
function secondEngine(cmp: { agree?: number; compared?: number; skipped?: boolean } | undefined): string {
  if (!cmp || cmp.skipped || !cmp.compared) return "second engine not run (1-minute bars only)";
  return `second engine: ${cmp.agree ?? 0} of ${cmp.compared} trades agree`;
}

function missionState(state: string | undefined): string {
  if (state === "WAITING_USER") return "Round finished — waiting for your decision (holdout sealed)";
  if (state === "COMPLETE") return "Round finished";
  return `Mission ${(state ?? "unknown").toLowerCase().replace(/_/g, " ")}`;
}
