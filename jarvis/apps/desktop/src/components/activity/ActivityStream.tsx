import type { JarvisEvent } from "@jarvis/protocol";
import { useEffect, useLayoutEffect, useRef } from "react";
import { useShallow } from "zustand/react/shallow";

import { clock } from "../../lib/format";
import { visibleActivity } from "../../store/reducer";
import { dispatch, useJarvis } from "../../store/store";
import { cx } from "../ui/primitives";
import { SEVERITY_TEXT } from "../ui/status";

const MARKERS: Partial<Record<JarvisEvent["severity"], string>> = {
  important: "bg-accent",
  warning: "bg-warning",
  error: "bg-danger",
};

export function ActivityStream() {
  const events = useJarvis(useShallow(visibleActivity));
  const showDebug = useJarvis((s) => s.showDebug);
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const onScroll = () => {
      pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
    };
    element.addEventListener("scroll", onScroll, { passive: true });
    return () => element.removeEventListener("scroll", onScroll);
  }, []);

  useLayoutEffect(() => {
    const element = scroller.current;
    if (element && pinned.current) element.scrollTop = element.scrollHeight;
  }, [events]);

  return (
    <section className="flex h-full min-h-0 flex-col" data-testid="activity-stream">
      <header className="flex items-center justify-between px-5 pt-3 pb-2">
        <h2 className="label">Activity</h2>
        <label className="flex cursor-pointer items-center gap-2 text-2xs text-fg-faint select-none">
          <input
            type="checkbox"
            className="size-3 accent-[var(--color-accent)]"
            checked={showDebug}
            onChange={() => dispatch({ type: "toggleDebug" })}
          />
          Debug
        </label>
      </header>
      <div ref={scroller} className="selectable min-h-0 flex-1 overflow-y-auto px-5 pb-3">
        {events.length === 0 ? (
          <p className="text-[13px] text-fg-faint">Nothing has happened yet.</p>
        ) : (
          <ol className="space-y-px">
            {events.map((event) => (
              <ActivityRow key={event.id} event={event} />
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}

function ActivityRow({ event }: { event: JarvisEvent }) {
  const marker = MARKERS[event.severity];
  return (
    <li className="grid grid-cols-[64px_80px_minmax(0,1fr)] items-baseline gap-3 py-[3px] text-[13px] leading-5">
      <time className="font-mono text-xs text-fg-faint tabular">{clock(event.timestamp)}</time>
      <span className="truncate font-mono text-2xs tracking-wider text-fg-faint uppercase">{event.source}</span>
      <span className={cx("flex min-w-0 items-baseline gap-2", SEVERITY_TEXT[event.severity])}>
        <span className={cx("size-1 shrink-0 translate-y-[-2px] rounded-full", marker ?? "bg-transparent")} />
        <span className="truncate">{event.message || event.type}</span>
      </span>
    </li>
  );
}
