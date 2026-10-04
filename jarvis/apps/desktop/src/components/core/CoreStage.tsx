import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

import { BACKEND_URL } from "../../lib/config";
import { stateLabel } from "../../lib/format";
import { useJarvis } from "../../store/store";
import { cx } from "../ui/primitives";
import { JarvisCore } from "./JarvisCore";

interface Copy {
  label: string;
  labelTone: string;
  headline: string;
  sub?: string;
}

/**
 * The core steps back (shrinks) while a mission or approval needs the stage,
 * so the whole picture fits without scrolling.
 */
export function CoreStage({ compact }: { compact: boolean }) {
  const connection = useJarvis((s) => s.connection);
  const jarvis = useJarvis((s) => s.jarvis);
  const viewport = useViewportHeight();

  // Replies live in the conversation below; the stage says what JARVIS is doing.
  const copy = describe(connection, jarvis.state, jarvis.detail);
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
  switch (state) {
    case "DORMANT":
      return { label: "Online", labelTone: "text-accent", headline: "Everything is nominal." };
    case "COMPLETE":
      return { label: stateLabel(state), labelTone: "text-success", headline: "Done." };
    case "FAILED":
      return { label: stateLabel(state), labelTone: "text-danger", headline: "That didn't work — details below." };
    case "LISTENING":
      return { label: "Listening", labelTone: "text-accent", headline: "I'm listening." };
    case "SPEAKING":
      return { label: "Speaking", labelTone: "text-accent", headline: "Speaking…" };
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
