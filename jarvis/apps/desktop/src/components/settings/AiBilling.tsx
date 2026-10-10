import type { AiEvent, AiPaidSettings, AiRoute, AiStatus } from "@jarvis/protocol";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../../lib/api";
import { useJarvis } from "../../store/store";
import { Button, Meter, StatusDot, type Tone, cx, textTone } from "../ui/primitives";
import { Group, Item, KeyForm, Toggle } from "./parts";

/**
 * Settings → AI & Billing: which route serves AI work (Claude plan → Claude API → local /
 * pause), the paid API fallback and its hard caps, and what was used this month.
 *
 * Nothing here is invented: plan usage and API credit balances have no official interface,
 * so they read "not retrievable" instead of a number. Paid fallback starts OFF and needs an
 * explicit confirmation with limits; the backend records every approval.
 */

/** The newest ai.* event id: a change means "refetch". */
export function useAiTick(): string | null {
  return useJarvis((s) => {
    for (let i = s.activity.length - 1; i >= 0; i--) {
      const event = s.activity[i];
      if (event?.type.startsWith("ai.")) return event.id;
    }
    return null;
  });
}

/** The live AI status, refetched whenever the router reports a change or the backend (re)connects. */
export function useAiStatus(): [AiStatus | null, (next: AiStatus) => void, string | null] {
  const tick = useAiTick();
  const online = useJarvis((s) => s.connection === "online");
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    if (!online) return;
    let live = true;
    api.aiStatus().then(
      (next) => {
        if (!live) return;
        setStatus(next);
        setProblem(null);
      },
      (err: unknown) => live && setProblem(err instanceof Error ? err.message : "AI status unavailable."),
    );
    return () => {
      live = false;
    };
  }, [tick, online]);
  return [status, setStatus, problem];
}

export const ROUTE_VIEW: Record<AiRoute, { text: string; tone: Tone; icon: string }> = {
  plan: { text: "Plan", tone: "success", icon: "●" },
  api: { text: "API", tone: "warning", icon: "$" },
  local: { text: "Local", tone: "accent", icon: "◐" },
  paused: { text: "Paused", tone: "danger", icon: "‖" },
};

const STATE_VIEW: Record<string, { text: string; tone: Tone }> = {
  CONNECTED: { text: "Connected", tone: "success" },
  READY: { text: "Ready", tone: "success" },
  UNKNOWN: { text: "Not checked yet", tone: "faint" },
  DISABLED: { text: "Off", tone: "faint" },
  NO_KEY: { text: "No key", tone: "faint" },
  NOT_INSTALLED: { text: "Claude Code not installed", tone: "warning" },
  NOT_LOGGED_IN: { text: "Not signed in", tone: "warning" },
  NOT_SUPPORTED: { text: "Not supported for JARVIS", tone: "danger" },
  LIMIT_REACHED: { text: "Usage limit reached", tone: "warning" },
  INSUFFICIENT_CREDIT: { text: "Credit too low / spend limit", tone: "danger" },
  INVALID_KEY: { text: "Key rejected", tone: "danger" },
  BUDGET_REACHED: { text: "Your budget is used up", tone: "warning" },
  UNAVAILABLE: { text: "Unavailable for now", tone: "warning" },
};

const STRATEGIES: { value: AiStatus["strategy"]; label: string }[] = [
  { value: "subscription_first", label: "Plan first — then paid API, then local" },
  { value: "plan_only", label: "Claude plan only (never paid)" },
  { value: "api_only", label: "Claude API only (paid, within budget)" },
];

const PROFILES: { value: AiStatus["profile"]; label: string }[] = [
  { value: "economy", label: "Economy — Sonnet instead of Opus" },
  { value: "balanced", label: "Balanced — as configured" },
  { value: "deep", label: "Deep — Opus for research and code" },
];

const fieldClass =
  "no-drag h-7 rounded-md border border-hairline-strong bg-surface px-2 text-[13px] text-fg focus:border-accent/50 focus:outline-none";

function message(err: unknown): string {
  if (err instanceof ApiError) return err.suggestion ? `${err.message} ${err.suggestion}` : err.message;
  return err instanceof Error ? err.message : "That didn't work.";
}

function when(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** Money in the owner's display currency; CHF is a conversion at their own rate assumption. */
export function money(usd: number | null | undefined, status: AiStatus | null, digits = 2): string {
  if (usd === null || usd === undefined) return "—";
  const rate = status?.currency.usd_to_chf;
  if (status?.currency.display === "CHF" && rate) return `CHF ${(usd * rate).toFixed(digits)}`;
  return `$${usd.toFixed(digits)}`;
}

function StateBadge({ state, testid }: { state: string; testid: string }) {
  const view = STATE_VIEW[state] ?? { text: state, tone: "faint" as Tone };
  return (
    <span className={cx("inline-flex items-center gap-2", textTone(view.tone))} data-testid={testid} data-state={state}>
      <StatusDot tone={view.tone} />
      {view.text}
    </span>
  );
}

function Sub({ children }: { children: React.ReactNode }) {
  return <div className="pt-5 pb-1 text-2xs font-medium tracking-[0.16em] text-fg-faint uppercase">{children}</div>;
}

function Wide({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-4 py-2.5 text-[13px]">
      <span className="text-fg-faint">{label}</span>
      <div className="min-w-0 space-y-2 whitespace-normal text-fg">{children}</div>
    </div>
  );
}

function Link({ onClick, disabled, testid, children }: { onClick: () => void; disabled?: boolean; testid?: string; children: React.ReactNode }) {
  return (
    <button
      type="button"
      className="no-drag text-xs text-fg-muted hover:text-fg disabled:opacity-50"
      disabled={disabled}
      onClick={onClick}
      data-testid={testid}
    >
      {children}
    </button>
  );
}

export function AiBillingSettings() {
  const [status, setStatus, loadProblem] = useAiStatus();
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const run = useCallback(
    async (name: string, action: () => Promise<AiStatus | void>) => {
      setBusy(name);
      setProblem(null);
      setNote(null);
      try {
        const next = await action();
        if (next) setStatus(next);
      } catch (err) {
        setProblem(message(err));
      } finally {
        setBusy(null);
      }
    },
    [setStatus],
  );

  if (!status) {
    return (
      <Group title="AI & Billing">
        <Item label="Status">
          <span className="text-fg-muted">{loadProblem ?? "Loading…"}</span>
        </Item>
      </Group>
    );
  }

  const route = ROUTE_VIEW[status.current.route];
  return (
    <section data-testid="ai-billing">
      <h2 className="label mb-3">AI & Billing</h2>
      <div className="divide-y divide-hairline border-y border-hairline">
        <Wide label="Route now">
          <span className={cx("inline-flex items-center gap-2 font-medium", textTone(route.tone))} data-testid="ai-current" data-route={status.current.route}>
            <span aria-hidden>{route.icon}</span>
            {status.current.label}
          </span>
          <p className="text-xs leading-relaxed text-fg-muted" data-testid="ai-current-reason">
            {status.current.reason}
          </p>
        </Wide>
        <Wide label="Strategy">
          <span className="flex flex-wrap items-center gap-3">
            <select
              className={fieldClass}
              value={status.strategy}
              onChange={(e) => void run("strategy", () => api.aiStrategy(e.target.value as AiStatus["strategy"]))}
              data-testid="ai-strategy"
              aria-label="Strategy"
            >
              {STRATEGIES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
            <select
              className={fieldClass}
              value={status.profile}
              onChange={(e) =>
                void run("profile", () => api.aiStrategy(status.strategy, e.target.value as AiStatus["profile"]))
              }
              data-testid="ai-profile"
              aria-label="Work profile"
            >
              {PROFILES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </span>
          <p className="text-xs text-fg-faint" data-testid="ai-order">
            Fallback order: {status.order.map((name, i) => `${i + 1}. ${name}`).join("  →  ")}. Temporary errors
            (overload, rate limit, network) are retried, never a reason to pay. Missions pause instead of degrading.
          </p>
        </Wide>
        {(problem || note) && (
          <Wide label={problem ? "Problem" : "Done"}>
            <p className={cx("text-xs", problem ? "text-danger" : "text-fg-muted")} data-testid={problem ? "ai-problem" : "ai-note"}>
              {problem ?? note}
            </p>
          </Wide>
        )}

        <PlanSection status={status} busy={busy} run={run} setNote={setNote} />
        <ApiSection status={status} busy={busy} run={run} />
        <LocalSection status={status} busy={busy} run={run} />
        <UsageSection status={status} busy={busy} run={run} />
      </div>
    </section>
  );
}

interface SectionProps {
  status: AiStatus;
  busy: string | null;
  run: (name: string, action: () => Promise<AiStatus | void>) => Promise<void>;
}

function PlanSection({ status, busy, run, setNote }: SectionProps & { setNote: (text: string | null) => void }) {
  const plan = status.providers.plan;
  const [personal, setPersonal] = useState(false);
  const missing = plan.state === "NOT_INSTALLED";
  const signedOut = plan.state === "NOT_LOGGED_IN";

  const terminal = (action: "login" | "install") =>
    run(`terminal-${action}`, async () => {
      const { command } = await api.aiTerminal(action);
      setNote(`Terminal opened with: ${command} — finish there, then press “Check”.`);
    });

  return (
    <>
      <Sub>1 · Claude plan (Pro / Max)</Sub>
      <Wide label="Status">
        <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <StateBadge state={plan.enabled ? plan.state : "DISABLED"} testid="ai-plan-status" />
          {plan.until && <span className="text-xs text-fg-faint">until {when(plan.until)}</span>}
        </span>
        {plan.detail && <p className="text-xs leading-relaxed text-fg-muted">{plan.detail}</p>}
        <span className="flex flex-wrap items-center gap-4">
          <Link onClick={() => void run("plan-check", api.aiPlanCheck)} disabled={busy !== null} testid="ai-plan-check">
            {busy === "plan-check" ? "Checking…" : "Check"}
          </Link>
          {missing && (
            <Link onClick={() => void terminal("install")} disabled={busy !== null} testid="ai-plan-install">
              Install Claude Code…
            </Link>
          )}
          {(signedOut || missing) && (
            <Link onClick={() => void terminal("login")} disabled={busy !== null || missing} testid="ai-plan-login">
              Sign in with Claude…
            </Link>
          )}
          {plan.enabled && !missing && !signedOut && plan.state !== "NOT_SUPPORTED" && (
            <Link
              onClick={() =>
                void run("plan-test", async () => {
                  const { result, status: next } = await api.aiPlanTest();
                  setNote(result.ok ? `Test answered on your plan (${result.latency_ms ?? "?"} ms).` : `Test failed: ${result.message}`);
                  return next;
                })
              }
              disabled={busy !== null}
              testid="ai-plan-test"
            >
              {busy === "plan-test" ? "Testing…" : "Test (uses a little plan usage)"}
            </Link>
          )}
        </span>
      </Wide>
      <Wide label="Use for JARVIS">
        {plan.enabled ? (
          <span className="flex flex-wrap items-center gap-4">
            <span className="text-fg-muted" data-testid="ai-plan-enabled">
              On since {when(plan.confirmed_at)} — personal use confirmed
            </span>
            <Link onClick={() => void run("plan-off", () => api.aiPlan(false))} disabled={busy !== null} testid="ai-plan-disable">
              Turn off
            </Link>
          </span>
        ) : (
          <>
            <label className="flex items-start gap-2 text-xs leading-relaxed text-fg-muted">
              <input
                type="checkbox"
                className="no-drag mt-0.5"
                checked={personal}
                onChange={(e) => setPersonal(e.target.checked)}
                data-testid="ai-plan-personal"
              />
              <span>
                Only I use this JARVIS, with my own Claude account. JARVIS runs the official Claude Code (
                <code className="font-mono">claude -p</code>) under my sign-in — no tokens copied, no one else's
                requests on my plan.
              </span>
            </label>
            <Button
              variant="primary"
              disabled={!personal || busy !== null}
              onClick={() => void run("plan-on", () => api.aiPlan(true, true))}
              data-testid="ai-plan-enable"
            >
              Use my Claude plan
            </Button>
          </>
        )}
      </Wide>
      <Item label="Plan usage">
        <span className="text-fg-faint" title={plan.usage}>
          {plan.usage}
        </span>
      </Item>
      {plan.version && (
        <Item label="Claude Code">
          <span className="text-fg-muted">
            {plan.version}
            {plan.login_method && ` · signed in via ${plan.login_method}`}
          </span>
        </Item>
      )}
      {plan.custom_binary && (
        <Item label="Binary">
          <span className="font-mono text-xs text-warning" title={plan.custom_binary} data-testid="ai-plan-binary">
            Custom (config / JARVIS_AI_CLI): {plan.custom_binary}
          </span>
        </Item>
      )}
    </>
  );
}

function ApiSection({ status, busy, run }: SectionProps) {
  const apiStatus = status.providers.api;
  const paid = apiStatus.paid;
  const [replacing, setReplacing] = useState(false);
  const [editing, setEditing] = useState(false);
  const hasKey = Boolean(apiStatus.key_hint);
  const spentPct = paid.enabled && paid.monthly_budget_usd > 0 ? (apiStatus.month_spent_usd / paid.monthly_budget_usd) * 100 : 0;

  return (
    <>
      <Sub>2 · Claude API (paid)</Sub>
      <Wide label="Status">
        <StateBadge state={hasKey ? apiStatus.state : "NO_KEY"} testid="ai-api-status" />
        {apiStatus.detail && <p className="text-xs leading-relaxed text-fg-muted">{apiStatus.detail}</p>}
        {hasKey && (
          <Link onClick={() => void run("api-check", api.aiApiCheck)} disabled={busy !== null} testid="ai-api-check">
            {busy === "api-check" ? "Checking…" : "Check key (free)"}
          </Link>
        )}
      </Wide>
      <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-4 py-3 text-[13px]">
        <span className="text-fg-faint">API key</span>
        {!hasKey || replacing ? (
          <KeyForm
            connect={async (key) => {
              const next = await api.aiKey(key); // throws for the form to show
              await run("key", async () => next);
            }}
            label="Anthropic API key"
            placeholder="sk-ant-…"
            provider={{ name: "Anthropic", url: "https://console.anthropic.com/settings/keys", host: "console.anthropic.com" }}
            storage={
              apiStatus.keystore.test_only ? (
                <span className="text-warning">in a test keystore in memory (not persisted)</span>
              ) : (
                <>in your {apiStatus.keystore.backend || "OS keystore"}</>
              )
            }
            testid="key"
            onDone={() => setReplacing(false)}
            onCancel={replacing ? () => setReplacing(false) : undefined}
          />
        ) : (
          <span className="flex flex-wrap items-center gap-3">
            <span className="font-mono text-xs text-fg-muted" data-testid="ai-key-hint">
              sk-ant-…{apiStatus.key_hint}
            </span>
            <span className="text-xs text-fg-faint">
              {apiStatus.key_source === "keystore"
                ? apiStatus.keystore.backend
                : apiStatus.key_source === "env_file"
                  ? "jarvis/.env (moves to the keystore on the next start)"
                  : "shell environment"}
            </span>
            <Link onClick={() => setReplacing(true)}>Replace</Link>
            <Link onClick={() => void run("key-remove", api.aiKeyDelete)} disabled={busy !== null} testid="ai-key-remove">
              Remove
            </Link>
          </span>
        )}
      </div>
      {!apiStatus.keystore.available && (
        <Item label="Keystore">
          <span className="whitespace-normal text-danger">{apiStatus.keystore.reason}</span>
        </Item>
      )}
      <Wide label="Paid fallback">
        <span className={cx("inline-flex items-center gap-2 font-medium", paid.enabled ? "text-warning" : "text-fg-muted")} data-testid="ai-paid-state">
          <StatusDot tone={paid.enabled ? "warning" : "faint"} />
          {paid.enabled ? `On — approved ${when(paid.approved_at)}` : "Off — JARVIS never pays without your approval"}
        </span>
        {paid.enabled && (
          <div className="max-w-[420px] space-y-1.5" data-testid="ai-paid-meter">
            <Meter value={spentPct} tone={spentPct >= 80 ? "danger" : spentPct >= 50 ? "warning" : "muted"} />
            <p className="text-xs text-fg-muted">
              {money(apiStatus.month_spent_usd, status)} of {money(paid.monthly_budget_usd, status)} used in {apiStatus.month}
              {" · "}per mission ≤ {money(paid.per_mission_cap_usd, status)} · ≤ {paid.max_concurrent} paid job
              {paid.max_concurrent === 1 ? "" : "s"} at once · warnings at {paid.warn_at.join(" / ")} %
              {paid.stop_at_budget ? " · stops at the budget" : " · paid calls wait at the budget"}
            </p>
          </div>
        )}
        {!paid.enabled && apiStatus.month_spent_usd > 0 && (
          <p className="text-xs text-fg-faint">Already billed in {apiStatus.month}: {money(apiStatus.month_spent_usd, status)}</p>
        )}
        {editing || !paid.enabled ? (
          <PaidForm
            current={paid}
            busy={busy}
            hasKey={hasKey}
            onApprove={(terms) =>
              void run("paid", async () => {
                const next = await api.aiPaid(terms);
                setEditing(false);
                return next;
              })
            }
            onCancel={editing ? () => setEditing(false) : undefined}
          />
        ) : (
          <span className="flex items-center gap-4">
            <Link onClick={() => setEditing(true)} testid="ai-paid-edit">
              Change limits…
            </Link>
            <Link onClick={() => void run("paid-off", api.aiPaidDisable)} disabled={busy !== null} testid="ai-paid-disable">
              Turn off
            </Link>
          </span>
        )}
      </Wide>
      <Wide label="API credits">
        <p className="text-xs leading-relaxed text-fg-faint" data-testid="ai-credits-note">
          {status.credits_note}
        </p>
      </Wide>
    </>
  );
}

function PaidForm({
  current,
  busy,
  hasKey,
  onApprove,
  onCancel,
}: {
  current: AiPaidSettings;
  busy: string | null;
  hasKey: boolean;
  onApprove: (terms: Omit<AiPaidSettings, "enabled" | "approved_at">) => void;
  onCancel?: () => void;
}) {
  const [budget, setBudget] = useState(current.monthly_budget_usd ? String(current.monthly_budget_usd) : "");
  const [cap, setCap] = useState(current.per_mission_cap_usd ? String(current.per_mission_cap_usd) : "");
  const [warn, setWarn] = useState<number[]>(current.warn_at.length ? current.warn_at : [50, 80, 100]);
  const [concurrent, setConcurrent] = useState(current.max_concurrent || 1);
  const [stop, setStop] = useState(current.stop_at_budget);
  const [confirm, setConfirm] = useState(false);

  const budgetValue = Number(budget);
  const capValue = Number(cap);
  const invalid =
    !(budgetValue > 0) ? "Set a monthly budget." : !(capValue > 0) ? "Set a per-mission cap." : capValue > budgetValue ? "The per-mission cap can't exceed the monthly budget." : null;

  return (
    <div className="max-w-[520px] space-y-3 rounded-lg border border-hairline p-3" data-testid="ai-paid-form">
      <div className="grid grid-cols-2 gap-3">
        <label className="space-y-1 text-xs text-fg-faint">
          <span>Monthly budget (USD)</span>
          <input
            type="number"
            min="0"
            step="1"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            className={cx(fieldClass, "block w-full font-mono")}
            data-testid="ai-paid-budget"
          />
        </label>
        <label className="space-y-1 text-xs text-fg-faint">
          <span>Per mission, at most (USD)</span>
          <input
            type="number"
            min="0"
            step="0.5"
            value={cap}
            onChange={(e) => setCap(e.target.value)}
            className={cx(fieldClass, "block w-full font-mono")}
            data-testid="ai-paid-cap"
          />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-fg-muted">
        <span className="text-fg-faint">Warn at</span>
        {[50, 80, 100].map((pct) => (
          <label key={pct} className="flex items-center gap-1.5">
            <input
              type="checkbox"
              className="no-drag"
              checked={warn.includes(pct)}
              onChange={(e) => setWarn((w) => (e.target.checked ? [...w, pct].sort((a, b) => a - b) : w.filter((v) => v !== pct)))}
            />
            {pct} %
          </label>
        ))}
        <label className="flex items-center gap-1.5">
          <span className="text-fg-faint">Paid jobs at once</span>
          <input
            type="number"
            min="1"
            max="8"
            value={concurrent}
            onChange={(e) => setConcurrent(Math.max(1, Math.min(8, Number(e.target.value) || 1)))}
            className={cx(fieldClass, "w-14 font-mono")}
            data-testid="ai-paid-concurrent"
          />
        </label>
      </div>
      <Toggle checked={stop} onChange={setStop} testid="ai-paid-stop">
        {stop ? "At the budget: turn paid fallback off until I approve again" : "At the budget: no new paid calls this month"}
      </Toggle>
      <label className="flex items-start gap-2 text-xs leading-relaxed text-fg-muted">
        <input
          type="checkbox"
          className="no-drag mt-0.5"
          checked={confirm}
          onChange={(e) => setConfirm(e.target.checked)}
          data-testid="ai-paid-confirm"
        />
        <span>
          I approve paid Claude API use within these limits when my plan isn't available. JARVIS never raises them
          itself and never tops up credit. Data purchases (Databento) are separate and never paid from this.
        </span>
      </label>
      {!hasKey && <p className="text-xs text-warning">Connect an API key above — until then nothing can be billed.</p>}
      <span className="flex items-center gap-3">
        <Button
          variant="warning"
          disabled={!confirm || invalid !== null || busy !== null}
          onClick={() =>
            onApprove({
              monthly_budget_usd: budgetValue,
              per_mission_cap_usd: capValue,
              warn_at: warn,
              max_concurrent: concurrent,
              stop_at_budget: stop,
            })
          }
          data-testid="ai-paid-approve"
        >
          {busy === "paid" ? "Saving…" : "Approve paid fallback"}
        </Button>
        {onCancel && <Button onClick={onCancel}>Cancel</Button>}
        {invalid && (budget || cap) && <span className="text-xs text-fg-faint">{invalid}</span>}
      </span>
    </div>
  );
}

function LocalSection({ status, busy, run }: SectionProps) {
  const local = status.providers.local;
  const [url, setUrl] = useState(local.base_url);
  const [model, setModel] = useState(local.model);
  useEffect(() => {
    setUrl(local.base_url);
    setModel(local.model);
  }, [local.base_url, local.model]);
  const save = (enabled: boolean) => run("local", () => api.aiLocal(enabled, url, model));

  return (
    <>
      <Sub>3 · Local AI (Ollama, optional)</Sub>
      <Wide label="Local model">
        <Toggle checked={local.enabled} onChange={(value) => void save(value)} testid="ai-local-toggle">
          {local.enabled ? "On — for conversation when Claude isn't reachable" : "Off"}
        </Toggle>
        <span className="flex flex-wrap items-center gap-2">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className={cx(fieldClass, "w-56 font-mono text-xs")}
            aria-label="Local server"
            spellCheck={false}
          />
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="model, e.g. llama3.2"
            className={cx(fieldClass, "w-44 font-mono text-xs")}
            aria-label="Local model"
            spellCheck={false}
          />
          <Link onClick={() => void save(local.enabled)} disabled={busy !== null}>
            Save
          </Link>
          {local.enabled && (
            <Link onClick={() => void run("local-check", api.aiLocalCheck)} disabled={busy !== null}>
              Check
            </Link>
          )}
        </span>
        {local.enabled && <StateBadge state={local.state} testid="ai-local-status" />}
        <p className="text-xs leading-relaxed text-fg-faint">
          Only this computer (loopback). Used for conversation only — planning, code, research and review missions
          pause and resume later instead of silently running on a weaker model.
        </p>
      </Wide>
    </>
  );
}

const PROVIDER_LABEL: Record<string, string> = { plan: "Claude plan", api: "Claude API", local: "Local AI" };

function UsageSection({ status, busy, run }: SectionProps) {
  const tick = useAiTick();
  const [events, setEvents] = useState<AiEvent[]>([]);
  const [rate, setRate] = useState(status.currency.usd_to_chf ? String(status.currency.usd_to_chf) : "");
  useEffect(() => {
    api.aiUsage().then((u) => setEvents(u.events.slice(0, 8)), () => setEvents([]));
  }, [tick]);

  const rows = status.usage;
  const setCurrency = (display: "USD" | "CHF") =>
    run("currency", () => api.aiCurrency(display, rate ? Number(rate) : null));

  return (
    <>
      <Sub>This month</Sub>
      <Wide label="Usage">
        {rows.length === 0 ? (
          <span className="text-fg-faint" data-testid="ai-usage">
            No AI calls yet this month.
          </span>
        ) : (
          <table className="w-full max-w-[560px] text-xs tabular" data-testid="ai-usage">
            <thead className="text-fg-faint">
              <tr>
                <th className="py-1 text-left font-normal">Route</th>
                <th className="py-1 text-right font-normal">Calls</th>
                <th className="py-1 text-right font-normal">Tokens in / out</th>
                <th className="py-1 text-right font-normal">Billed</th>
                <th className="py-1 text-right font-normal" title="What Claude Code reports the calls would cost at API prices — not billed on a plan">
                  List value
                </th>
              </tr>
            </thead>
            <tbody className="text-fg-muted">
              {rows.map((r) => (
                <tr key={r.provider} className="border-t border-hairline">
                  <td className="py-1 text-fg">{PROVIDER_LABEL[r.provider] ?? r.provider}</td>
                  <td className="py-1 text-right">
                    {r.calls}
                    {r.failures > 0 && <span className="text-warning"> ({r.failures} failed)</span>}
                  </td>
                  <td className="py-1 text-right">
                    {(r.input_tokens ?? 0).toLocaleString("en-US")} / {(r.output_tokens ?? 0).toLocaleString("en-US")}
                  </td>
                  <td className={cx("py-1 text-right", r.provider === "api" && "text-fg")}>
                    {r.provider === "api" ? money(r.cost_usd, status, 4) : "—"}
                  </td>
                  <td className="py-1 text-right text-fg-faint">{r.list_cost_usd ? money(r.list_cost_usd, status, 4) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {status.by_consumer.length > 0 && (
          <p className="text-xs text-fg-faint">
            By area:{" "}
            {status.by_consumer
              .map((c) => `${c.consumer} · ${PROVIDER_LABEL[c.provider] ?? c.provider} ${c.calls}×${c.provider === "api" ? ` ${money(c.cost_usd, status)}` : ""}`)
              .join("  ·  ")}
          </p>
        )}
      </Wide>
      <Wide label="Currency">
        <span className="flex flex-wrap items-center gap-3">
          <select
            className={fieldClass}
            value={status.currency.display}
            onChange={(e) => void setCurrency(e.target.value as "USD" | "CHF")}
            data-testid="ai-currency"
            aria-label="Display currency"
          >
            <option value="USD">USD (billing currency)</option>
            <option value="CHF">CHF (converted)</option>
          </select>
          <label className="flex items-center gap-2 text-xs text-fg-faint">
            1 USD =
            <input
              type="number"
              min="0"
              step="0.01"
              value={rate}
              placeholder="0.80"
              onChange={(e) => setRate(e.target.value)}
              onBlur={() => rate && Number(rate) !== status.currency.usd_to_chf && void setCurrency(status.currency.display)}
              className={cx(fieldClass, "w-20 font-mono")}
              data-testid="ai-rate"
            />
            CHF — your assumption, not a live rate
          </label>
        </span>
        {status.currency.display === "CHF" && !status.currency.usd_to_chf && (
          <p className="text-xs text-warning">Set a rate to show CHF — amounts stay in USD until then.</p>
        )}
        <p className="text-xs text-fg-faint">Budgets and limits are always kept in USD, the currency Anthropic bills in.</p>
      </Wide>
      <Wide label="Recent">
        {events.length === 0 ? (
          <span className="text-fg-faint">No routing events yet.</span>
        ) : (
          <ul className="space-y-1 text-xs" data-testid="ai-events">
            {events.map((e) => (
              <li key={e.id} className="flex gap-3">
                <span className="w-28 shrink-0 font-mono text-fg-faint">{when(e.at)}</span>
                <span className="text-fg-muted">{e.message}</span>
              </li>
            ))}
          </ul>
        )}
        {busy === "currency" && <span className="text-xs text-fg-faint">Saving…</span>}
      </Wide>
      {status.keystore_test_only && (
        <Item label="Test mode">
          <span className="text-warning" data-testid="ai-test-keystore">
            Test keystore (memory) — nothing is stored in the OS keystore in this run.
          </span>
        </Item>
      )}
    </>
  );
}
