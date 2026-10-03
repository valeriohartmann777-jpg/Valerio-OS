import type { SettingsView } from "@jarvis/protocol";
import { type FormEvent, useEffect, useState } from "react";

import { Button, Empty, StatusDot, cx } from "../components/ui/primitives";
import { ApiError, api } from "../lib/api";
import { BACKEND_URL } from "../lib/config";
import { modelLabel } from "../lib/format";
import { useJarvis } from "../store/store";

const POLICY_TEXT: Record<string, string> = {
  auto: "Automatic",
  confirm: "Confirmation",
  strong_confirm: "Strong confirmation",
  deny: "Disabled",
};

export function Settings() {
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.settings().then(setSettings, (err: unknown) =>
      setError(err instanceof Error ? err.message : "Settings unavailable."),
    );
  }, []);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[820px] px-10 py-10">
        <h1 className="text-2xl font-light tracking-[-0.01em] text-fg">Settings</h1>
        <p className="mt-2 text-[13px] text-fg-muted">
          Connect Claude below. Everything else is read-only in this phase: edit{" "}
          <code className="font-mono text-xs">config/*.yaml</code> and restart JARVIS to change it.
        </p>

        {error && <p className="mt-8 text-[13px] text-danger">{error}</p>}
        {!settings && !error && <Empty>Loading…</Empty>}

        {settings && (
          <div className="mt-10 space-y-10">
            <BrainSettings models={settings.models} />

            <Group title="System">
              <Item label="Version">{settings.version}</Item>
              <Item label="Backend">{BACKEND_URL}</Item>
              <Item label="Environment">
                <span className={cx(settings.simulated && "text-warning")}>
                  {settings.system_backend}
                  {settings.simulated && " — simulated desktop, no real applications are launched"}
                </span>
              </Item>
              <Item label="Config">
                <span className="font-mono text-xs">{settings.config_dir}</span>
              </Item>
              <Item label="Database">
                <span className="font-mono text-xs">{settings.database_path}</span>
              </Item>
            </Group>

            <Group title="Permissions">
              {settings.permission_levels.map((level) => (
                <Item key={level.level} label={`Level ${level.level}`}>
                  <span className="inline-block w-40 text-fg">{level.label}</span>
                  <span className={cx(level.policy === "auto" ? "text-fg-muted" : "text-warning")}>
                    {POLICY_TEXT[level.policy] ?? level.policy}
                  </span>
                </Item>
              ))}
              <Item label="Disabled">{settings.disabled_categories.join(", ") || "—"}</Item>
              <Item label="Expiry">{Math.round(settings.approval_timeout_seconds / 60)} min</Item>
            </Group>

            <Group title="Personality">
              <Item label="Name">{settings.personality_name}</Item>
              <Item label="Traits">{settings.personality_traits.join(" · ")}</Item>
            </Group>

            <Group title="Known applications">
              <p className="text-[13px] leading-relaxed text-fg-muted">{settings.known_apps.join(" · ")}</p>
            </Group>
          </div>
        )}
      </div>
    </div>
  );
}

function BrainSettings({ models }: { models: Record<string, string> }) {
  const brain = useJarvis((s) => s.brain);
  const [replacing, setReplacing] = useState(false);
  const online = Boolean(brain?.available && brain.fast_model);
  const showForm = !online || replacing;

  return (
    <Group title="Brain">
      <Item label="Status">
        {online && brain?.fast_model ? (
          <span className="inline-flex items-center gap-2" data-testid="brain-status">
            <StatusDot tone="success" />
            {modelLabel(brain.fast_model)}
            {brain.reasoning_model && (
              <span className="text-fg-muted">· think: {modelLabel(brain.reasoning_model)}</span>
            )}
          </span>
        ) : (
          <span className="inline-flex items-center gap-2 text-warning" data-testid="brain-status">
            <StatusDot tone="warning" />
            Offline
          </span>
        )}
      </Item>
      {/* Without any key the form below says it all; a rejected key needs its reason. */}
      {!online && brain?.reason && brain.key_hint && (
        <Item label="Reason">
          <span className="whitespace-normal text-fg-muted">{brain.reason}</span>
        </Item>
      )}
      <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-4 py-3 text-[13px]">
        <span className="text-fg-faint">API key</span>
        {showForm ? (
          <KeyForm
            onDone={() => setReplacing(false)}
            onCancel={replacing ? () => setReplacing(false) : undefined}
          />
        ) : (
          <span className="flex items-center gap-3">
            <span className="font-mono text-xs text-fg-muted">sk-ant-…{brain?.key_hint ?? ""}</span>
            <button
              type="button"
              className="no-drag text-xs text-fg-faint hover:text-fg"
              onClick={() => setReplacing(true)}
            >
              Replace
            </button>
          </span>
        )}
      </div>
      {Object.entries(models).map(([role, value]) => (
        <Item key={role} label={role}>
          <span className="text-fg-muted">{value === "none" ? "Not configured" : value}</span>
        </Item>
      ))}
    </Group>
  );
}

function KeyForm({ onDone, onCancel }: { onDone: () => void; onCancel?: () => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; suggestion: string | null } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.connectBrain(key);
      setKey("");
      onDone();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? { message: err.message, suggestion: err.suggestion }
          : { message: "Something went wrong.", suggestion: null },
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="min-w-0 space-y-2.5" data-testid="key-form">
      <div className="flex gap-2">
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="sk-ant-…"
          autoComplete="off"
          spellCheck={false}
          aria-label="Anthropic API key"
          data-testid="key-input"
          className={cx(
            "no-drag h-8 min-w-0 flex-1 rounded-lg border bg-surface px-3 font-mono text-xs text-fg",
            "placeholder:text-fg-faint focus:border-accent/50 focus:outline-none",
            error ? "border-danger/40" : "border-hairline-strong",
          )}
        />
        <Button type="submit" variant="primary" disabled={busy || !key.trim()} data-testid="key-connect">
          {busy ? "Checking…" : "Connect"}
        </Button>
        {onCancel && (
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        )}
      </div>
      {error ? (
        <p className="text-xs leading-relaxed text-danger" data-testid="key-error">
          {error.message}
          {error.suggestion && <span className="text-fg-muted"> {error.suggestion}</span>}
        </p>
      ) : (
        <p className="text-xs leading-relaxed text-fg-faint">
          Create one at{" "}
          <a
            href="https://console.anthropic.com/settings/keys"
            target="_blank"
            rel="noreferrer"
            className="no-drag text-fg-muted underline decoration-hairline-strong underline-offset-2 hover:text-fg"
          >
            console.anthropic.com
          </a>
          . JARVIS checks it with Anthropic, then keeps it only in <code className="font-mono">jarvis/.env</code>{" "}
          on this computer. No restart needed.
        </p>
      )}
    </form>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="label mb-3">{title}</h2>
      <dl className="divide-y divide-hairline border-y border-hairline">{children}</dl>
    </section>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-4 py-2.5 text-[13px]">
      <dt className="text-fg-faint capitalize">{label}</dt>
      <dd className="selectable truncate text-fg">{children}</dd>
    </div>
  );
}
