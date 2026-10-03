import type { SettingsView } from "@jarvis/protocol";
import { useEffect, useState } from "react";

import { Empty, cx } from "../components/ui/primitives";
import { api } from "../lib/api";
import { BACKEND_URL } from "../lib/config";

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
          Read-only in this phase. Edit <code className="font-mono text-xs">config/*.yaml</code> and restart
          JARVIS to change behaviour.
        </p>

        {error && <p className="mt-8 text-[13px] text-danger">{error}</p>}
        {!settings && !error && <Empty>Loading…</Empty>}

        {settings && (
          <div className="mt-10 space-y-10">
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

            <Group title="Models">
              {Object.entries(settings.models).map(([role, value]) => (
                <Item key={role} label={role}>
                  <span className="text-fg-muted">{value === "none" ? "Not configured (Phase 3)" : value}</span>
                </Item>
              ))}
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
