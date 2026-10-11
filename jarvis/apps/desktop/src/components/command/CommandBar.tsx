import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";

import { api } from "../../lib/api";
import { dispatch, useJarvis } from "../../store/store";
import { Icon } from "../ui/icons";
import { cx } from "../ui/primitives";

const MAX_HISTORY = 50;

export function CommandBar() {
  const connection = useJarvis((s) => s.connection);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const online = connection === "online";

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      const isShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k";
      const isSlash = event.key === "/" && document.activeElement?.tagName !== "INPUT";
      if (isShortcut || isSlash) {
        event.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const command = text.trim();
    if (!command || !online) return;
    setText("");
    setCursor(null);
    setError(null);
    setHistory((h) => [command, ...h.filter((c) => c !== command)].slice(0, MAX_HISTORY));
    try {
      await api.chat(command);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The command could not be sent.");
      setText(command);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      setText("");
      setCursor(null);
      return;
    }
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    if (history.length === 0) return;
    event.preventDefault();
    const next =
      event.key === "ArrowUp"
        ? Math.min((cursor ?? -1) + 1, history.length - 1)
        : cursor === null
          ? null
          : cursor - 1 < 0
            ? null
            : cursor - 1;
    setCursor(next);
    setText(next === null ? "" : (history[next] ?? ""));
  };

  return (
    <div className="border-t border-hairline-strong bg-[#0a1530]/95 px-6 py-3">
      <form onSubmit={submit} className="mx-auto flex max-w-[900px] items-center gap-3">
        <div
          className={cx(
            "group flex h-12 flex-1 items-center gap-3 rounded-xl border bg-surface px-4 transition-colors duration-200",
            "border-control/70 focus-within:border-home",
          )}
        >
          <span
            className={cx(
              "size-1.5 rounded-full transition-colors",
              online ? "bg-accent" : "bg-fg-faint",
            )}
          />
          <input
            ref={input}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setError(null);
            }}
            onKeyDown={onKeyDown}
            placeholder={online ? "Ask JARVIS anything…" : "Waiting for the core…"}
            disabled={!online}
            className="h-full flex-1 bg-transparent text-[15px] text-fg placeholder:text-fg-faint focus:outline-none disabled:cursor-not-allowed"
            aria-label="Command"
            data-testid="command-input"
            autoFocus
            spellCheck={false}
          />
          {error && <span className="text-xs text-danger">{error}</span>}
        </div>
        <button
          type="submit"
          disabled={!online || !text.trim()}
          title={!online ? "Waiting for the core" : !text.trim() ? "Type a message first" : "Send (Enter)"}
          className="inline-flex h-12 items-center gap-2 rounded-xl bg-cta px-4 text-[13px] font-extrabold tracking-[0.06em] text-cta-ink uppercase shadow-[0_3px_0_#b89a2c] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-40"
          data-testid="command-send"
        >
          Send <Icon name="send" size={16} />
        </button>
        <MicButton />
      </form>
    </div>
  );
}

/** Push-to-talk: click to speak a request, click again to stop. */
function MicButton() {
  const voice = useJarvis((s) => s.voice);
  const online = useJarvis((s) => s.connection === "online");
  const [problem, setProblem] = useState<string | null>(null);
  const phase = voice?.state;
  const busy = phase === "listening" || phase === "speaking";
  const usable = online && (phase === "ready" || busy);

  const click = async () => {
    setProblem(null);
    if (!voice || phase === "off" || phase === "unavailable") {
      dispatch({ type: "navigate", view: { name: "settings", section: "voice" } });
      return;
    }
    try {
      await (busy ? api.voiceStop() : api.voiceListen());
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "The microphone isn't available.");
    }
  };

  const title =
    problem ??
    (phase === "listening"
      ? "Listening — click to stop"
      : phase === "speaking"
        ? "Speaking — click to stop"
        : phase === "transcribing"
          ? "Understanding…"
          : phase === "ready"
            ? voice?.wake_word_active
              ? "Click to speak — or say “Hey JARVIS”"
              : "Click to speak"
            : (voice?.reason ?? "Set up voice in Settings"));

  return (
    <button
      type="button"
      onClick={() => void click()}
      disabled={!online || phase === "transcribing"}
      title={title}
      aria-label="Microphone"
      data-testid="mic-button"
      data-state={phase ?? "none"}
      className={cx(
        "relative grid size-12 place-items-center rounded-xl border transition-colors duration-200",
        "disabled:cursor-not-allowed disabled:opacity-60",
        busy
          ? "border-accent/60 bg-accent/10 text-accent"
          : usable
            ? "border-hairline-strong text-fg-muted hover:border-accent/40 hover:text-accent"
            : "border-hairline-strong text-fg-faint",
        problem && "border-danger/40 text-danger",
      )}
    >
      {phase === "listening" && (
        <span
          className="absolute inset-0 rounded-xl border border-accent/50"
          style={{ animation: "beacon 1.4s ease-in-out infinite" }}
        />
      )}
      <MicIcon />
    </button>
  );
}

function MicIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.2">
      <rect x="5.5" y="1.5" width="5" height="8.5" rx="2.5" />
      <path d="M3 7.5a5 5 0 0 0 10 0M8 12.5v2" strokeLinecap="round" />
    </svg>
  );
}
