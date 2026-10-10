import type { BriefingStatus, SettingsView } from "@jarvis/protocol";
import { useEffect, useState } from "react";

import { AiBillingSettings, useAiStatus } from "../components/settings/AiBilling";
import { Group, Item, KeyForm, Toggle } from "../components/settings/parts";
import { UpdateSummary } from "../components/UpdateControls";
import { Empty, StatusDot, cx } from "../components/ui/primitives";
import { api } from "../lib/api";
import { APP_BRIDGE, BACKEND_URL, type BackgroundInfo } from "../lib/config";
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
          Choose how JARVIS reaches Claude — your plan first, paid API only within your approved budget — and set up the voice. Everything else is read-only in this phase: edit{" "}
          <code className="font-mono text-xs">config/*.yaml</code> and restart JARVIS to change it.
        </p>

        {error && <p className="mt-8 text-[13px] text-danger">{error}</p>}
        {!settings && !error && <Empty>Loading…</Empty>}

        {settings && (
          <div className="mt-10 space-y-10">
            <AiBillingSettings />

            <BrainSettings models={settings.models} />

            <VoiceSettings />

            <BriefingSettings />

            <BackgroundSettings />

            <Group title="System">
              <Item label="Version">{settings.version}</Item>
              <Item label="Updates">
                <UpdateSummary />
              </Item>
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

            <Group title="Files">
              <Item label="Visible folders">{settings.file_roots.join(" · ") || "—"}</Item>
              <Item label="Never read">
                <span className="text-fg-muted">Hidden files, keys, .env and password files</span>
              </Item>
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
  const [ai] = useAiStatus();
  const online = Boolean(brain?.available && brain.fast_model);

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
            {ai && <span className="text-fg-muted">· via {ai.current.label}</span>}
          </span>
        ) : (
          <span className="inline-flex items-center gap-2 text-warning" data-testid="brain-status">
            <StatusDot tone="warning" />
            Offline
          </span>
        )}
      </Item>
      {!online && brain?.reason && (
        <Item label="Reason">
          <span className="whitespace-normal text-fg-muted">{brain.reason}</span>
        </Item>
      )}
      {Object.entries(models).map(([role, value]) => (
        <Item key={role} label={role}>
          <span className="text-fg-muted">{value === "none" ? "Not configured" : value}</span>
        </Item>
      ))}
    </Group>
  );
}

const VOICE_PHASE: Record<string, string> = {
  off: "Off",
  unavailable: "Unavailable",
  ready: "Ready",
  listening: "Listening…",
  transcribing: "Understanding…",
  speaking: "Speaking…",
};

function VoiceSettings() {
  const voice = useJarvis((s) => s.voice);
  const [replacing, setReplacing] = useState(false);
  const [testing, setTesting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  if (!voice) return null;
  const working = voice.state !== "off" && voice.state !== "unavailable";

  const run = async (action: () => Promise<unknown>) => {
    setProblem(null);
    try {
      await action();
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "That didn't work.");
    }
  };

  return (
    <Group title="Voice">
      <Item label="Status">
        <span
          className={cx("inline-flex items-center gap-2", !working && (voice.configured ? "text-warning" : "text-fg-muted"))}
          data-testid="voice-status"
        >
          <StatusDot tone={working ? "success" : voice.configured ? "warning" : "faint"} />
          {VOICE_PHASE[voice.state] ?? voice.state}
          {working && <span className="text-fg-muted">· {voice.voice_name} (ElevenLabs)</span>}
        </span>
      </Item>
      {voice.reason && voice.configured && (
        <Item label="Note">
          <span className="whitespace-normal text-fg-muted">{voice.reason}</span>
        </Item>
      )}
      <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-4 py-3 text-[13px]">
        <span className="text-fg-faint">API key</span>
        {!voice.configured || replacing ? (
          <KeyForm
            connect={api.connectVoice}
            label="ElevenLabs API key"
            placeholder="sk_…"
            provider={{ name: "ElevenLabs", url: "https://elevenlabs.io/app/settings/api-keys", host: "elevenlabs.io" }}
            storage={
              <>
                in <code className="font-mono">jarvis/.env</code> on this computer
              </>
            }
            testid="voice-key"
            onDone={() => setReplacing(false)}
            onCancel={replacing ? () => setReplacing(false) : undefined}
          />
        ) : (
          <span className="flex items-center gap-3">
            <span className="font-mono text-xs text-fg-muted">…{voice.key_hint ?? ""}</span>
            <button type="button" className="no-drag text-xs text-fg-faint hover:text-fg" onClick={() => setReplacing(true)}>
              Replace
            </button>
          </span>
        )}
      </div>
      {voice.configured && (
        <>
          <Item label="Hey JARVIS">
            <Toggle
              checked={voice.wake_word}
              onChange={(value) => void run(() => api.voicePreferences({ wake_word: value }))}
              testid="wake-word-toggle"
            >
              {voice.wake_word_active
                ? "Listening — the wake word is recognised on this computer"
                : voice.wake_word
                  ? "Starting…"
                  : "Off — use the microphone button"}
            </Toggle>
          </Item>
          <Item label="Spoken replies">
            <Toggle
              checked={voice.speak_replies}
              onChange={(value) => void run(() => api.voicePreferences({ speak_replies: value }))}
              testid="speak-replies-toggle"
            >
              {voice.speak_replies ? "Also for typed commands" : "Only when you spoke"}
            </Toggle>
          </Item>
          <Item label="Try it">
            <span className="flex items-center gap-3">
              <button
                type="button"
                className="no-drag text-xs text-fg-muted hover:text-fg disabled:opacity-50"
                disabled={!working || testing}
                onClick={() => {
                  setTesting(true);
                  void run(api.voiceTest).finally(() => setTesting(false));
                }}
              >
                {testing ? "Speaking…" : "Play a sample"}
              </button>
              {problem && <span className="truncate text-xs text-danger">{problem}</span>}
            </span>
          </Item>
        </>
      )}
    </Group>
  );
}

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function BriefingSettings() {
  const [status, setStatus] = useState<BriefingStatus | null>(null);
  const [time, setTime] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  useEffect(() => {
    api.briefing().then(
      (s) => {
        setStatus(s);
        setTime(s.time);
      },
      () => setProblem("Briefing settings unavailable."),
    );
  }, []);
  if (!status) return problem ? <p className="text-[13px] text-danger">{problem}</p> : null;

  const update = async (prefs: { enabled?: boolean; time?: string }) => {
    setProblem(null);
    try {
      const next = await api.briefingPreferences(prefs);
      setStatus(next);
      setTime(next.time);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "That didn't work.");
    }
  };

  const sendNow = async () => {
    setBusy(true);
    setProblem(null);
    try {
      await api.sendBriefing();
      setSent(true);
      setStatus(await api.briefing());
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  const days = status.weekdays.map((d) => DAY_NAMES[d]).join(", ");
  return (
    <Group title="Morning briefing">
      <Item label="Briefing">
        <Toggle
          checked={status.enabled}
          onChange={(value) => void update({ enabled: value })}
          testid="briefing-toggle"
        >
          {status.enabled
            ? `NQ and gold levels in the chat, ${days}`
            : "Off — ask for it any time: “Gib mir das Briefing”"}
        </Toggle>
      </Item>
      <Item label="Time">
        <span className="flex items-center gap-3">
          <input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
            onBlur={() => time && time !== status.time && void update({ time })}
            className="no-drag rounded-md border border-hairline bg-transparent px-2 py-0.5 font-mono text-[13px] text-fg focus:border-hairline-strong focus:outline-none"
            data-testid="briefing-time"
          />
          <span className="text-xs text-fg-faint">
            local time · comes until {status.catch_up_until} if JARVIS starts later
          </span>
        </span>
      </Item>
      <Item label="Next">
        <span className="text-fg-muted" data-testid="briefing-next">
          {status.next_at ? new Date(status.next_at).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "—"}
          {status.last_sent && <span className="text-fg-faint"> · last {status.last_sent}</span>}
        </span>
      </Item>
      <Item label="Now">
        <span className="flex items-center gap-3">
          <button
            type="button"
            className="no-drag text-xs text-fg-muted hover:text-fg disabled:opacity-50"
            disabled={busy}
            onClick={() => void sendNow()}
            data-testid="briefing-send"
          >
            {busy ? "Preparing…" : sent ? "Sent — see the chat on Home" : "Send the briefing now"}
          </button>
          {problem && <span className="truncate text-xs text-danger">{problem}</span>}
          {status.last_error && !problem && (
            <span className="truncate text-xs text-warning" title={status.last_error}>
              Last time: {status.last_error}
            </span>
          )}
        </span>
      </Item>
    </Group>
  );
}

function BackgroundSettings() {
  const [info, setInfo] = useState<BackgroundInfo | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    APP_BRIDGE?.background().then(setInfo, () => setProblem("Unavailable."));
  }, []);
  const bridge = APP_BRIDGE;
  if (!bridge || (!info && !problem)) return null;

  const setLogin = async (enabled: boolean) => {
    setProblem(null);
    try {
      const login = await bridge.setStartAtLogin(enabled);
      setInfo((current) => (current ? { ...current, login } : current));
      if (login.enabled !== enabled) setProblem("macOS didn't accept the change.");
    } catch {
      setProblem("That didn't work.");
    }
  };

  const quitKey = navigator.platform.startsWith("Mac") ? "⌘Q" : "the tray menu";
  return (
    <Group title="Always on">
      <Item label="Window closed">
        <span className="text-fg-muted" data-testid="background-mode">
          {info?.keepsRunning
            ? `Keeps running in the menu bar — learning and training go on. Quit: ${quitKey}`
            : "Closing the window quits JARVIS."}
        </span>
      </Item>
      {info?.login.supported && (
        <Item label="Start at login">
          <Toggle checked={info.login.enabled} onChange={(value) => void setLogin(value)} testid="login-toggle">
            {info.login.needsApproval
              ? "Allow JARVIS in System Settings → General → Login Items"
              : info.login.enabled
                ? "Starts when you log in, in the menu bar"
                : "Off — start JARVIS yourself"}
          </Toggle>
        </Item>
      )}
      {problem && (
        <Item label="Problem">
          <span className="text-danger">{problem}</span>
        </Item>
      )}
    </Group>
  );
}
