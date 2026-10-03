import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

import { BACKEND_URL } from "../../lib/config";
import { stateLabel } from "../../lib/format";
import type { LastMessage } from "../../store/reducer";
import { useJarvis, useNow } from "../../store/store";
import { cx } from "../ui/primitives";
import { JarvisCore } from "./JarvisCore";

/** How long JARVIS's last reply stays on stage after it returned to idle. */
const MESSAGE_LINGER_MS = 20_000;

interface Copy {
  label: string;
  labelTone: string;
  headline: string;
  sub?: string;
  message?: LastMessage;
}

/**
 * The core steps back (shrinks) while a mission or approval needs the stage,
 * so the whole picture fits without scrolling.
 */
export function CoreStage({ compact }: { compact: boolean }) {
  const connection = useJarvis((s) => s.connection);
  const jarvis = useJarvis((s) => s.jarvis);
  const lastMessage = useJarvis((s) => s.lastMessage);
  const now = useNow(1000);
  const viewport = useViewportHeight();

  const copy = describe(connection, jarvis.state, jarvis.detail, lastMessage, now);
  const size = compact
    ? Math.round(Math.min(240, Math.max(132, viewport * 0.16)))
    : Math.round(Math.min(300, Math.max(200, viewport * 0.32)));

  return (
    <div className="flex flex-col items-center">
      <JarvisCore state={jarvis.state} size={size} offline={connection !== "online"} />
      <div
        className={cx(
          "flex w-full max-w-[560px] flex-col items-center text-center transition-[margin] duration-500",
          compact ? "mt-5" : "mt-10 min-h-[112px]",
        )}
      >
        <div className={cx("label tracking-[0.32em] transition-colors duration-500", copy.labelTone)}>
          {copy.label}
        </div>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={copy.headline}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
            className="mt-3"
          >
            <p
              className="selectable text-[22px] leading-snug font-light tracking-[-0.01em] text-fg"
              data-testid="jarvis-headline"
            >
              {copy.headline}
            </p>
            {copy.sub && !(compact && jarvis.state === "WAITING_FOR_APPROVAL") && (
              <p className="mt-2 text-[13px] text-fg-muted">{copy.sub}</p>
            )}
            {copy.message?.error && (
              <dl className="mx-auto mt-4 grid max-w-[460px] grid-cols-[84px_minmax(0,1fr)] gap-x-3 gap-y-1 text-left text-[13px]">
                <dt className="text-fg-faint">Reason</dt>
                <dd className="text-fg-muted">{copy.message.error.message}</dd>
                {copy.message.error.suggestion && (
                  <>
                    <dt className="text-fg-faint">Suggested</dt>
                    <dd className="text-fg-muted">{copy.message.error.suggestion}</dd>
                  </>
                )}
              </dl>
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}

function describe(
  connection: string,
  state: ReturnType<typeof useJarvis.getState>["jarvis"]["state"],
  detail: string,
  message: LastMessage | null,
  now: number,
): Copy {
  if (connection === "connecting") {
    return {
      label: "Connecting",
      labelTone: "text-fg-faint",
      headline: "Establishing link to the core…",
      sub: BACKEND_URL.replace(/^https?:\/\//, ""),
    };
  }
  if (connection === "offline") {
    return {
      label: "Offline",
      labelTone: "text-danger",
      headline: "The connection to the core was lost.",
      sub: "Reconnecting automatically.",
    };
  }
  const freshMessage = message && now - message.at < MESSAGE_LINGER_MS ? message : null;
  switch (state) {
    case "DORMANT":
      return freshMessage
        ? { label: "Online", labelTone: "text-accent", headline: freshMessage.text, message: freshMessage }
        : { label: "Online", labelTone: "text-accent", headline: "Everything is nominal." };
    case "COMPLETE":
    case "FAILED":
      return {
        label: stateLabel(state),
        labelTone: state === "COMPLETE" ? "text-success" : "text-danger",
        headline: message?.text ?? detail,
        message: message ?? undefined,
      };
    case "WAITING_FOR_APPROVAL":
      return {
        label: stateLabel(state),
        labelTone: "text-warning",
        headline: "Your approval is required.",
        sub: detail,
      };
    case "THINKING":
      return {
        label: "Thinking",
        labelTone: "text-accent",
        headline: "Thinking it through…",
        sub: detail || undefined,
      };
    case "PAUSED":
      return { label: "Paused", labelTone: "text-fg-muted", headline: detail || "Mission paused." };
    default:
      return { label: stateLabel(state), labelTone: "text-accent", headline: detail || "…" };
  }
}

function useViewportHeight(): number {
  const [height, setHeight] = useState(() => window.innerHeight);
  useEffect(() => {
    const onResize = () => setHeight(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return height;
}
