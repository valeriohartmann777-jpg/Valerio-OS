import type { UlTask } from "@jarvis/protocol";
import { useMemo } from "react";

import { cx } from "../ui/primitives";
import { AgentName, TASK } from "./parts";

/**
 * The mission's task graph: columns are dependency depth, so tasks in the same
 * column can run in parallel. Edges point from a dependency to its dependent.
 * Node state is text plus an icon — never colour alone.
 */

const W = 228;
const H = 78;
const GAP_X = 72;
const GAP_Y = 18;

const TEXT: Record<string, string> = {
  accent: "text-ql",
  positive: "text-ql-positive",
  warning: "text-ql-warning",
  danger: "text-ql-danger",
  muted: "text-fg-faint",
};

const BORDER: Record<string, string> = {
  accent: "border-ql/60",
  positive: "border-ql-positive/50",
  warning: "border-ql-warning/60",
  danger: "border-ql-danger/60",
  muted: "border-ql-border",
};

export function MissionGraph({
  tasks,
  selected,
  onSelect,
}: {
  tasks: UlTask[];
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const layout = useMemo(() => {
    const columns = new Map<number, UlTask[]>();
    for (const t of tasks) {
      const depth = t.contract.depth ?? 0;
      columns.set(depth, [...(columns.get(depth) ?? []), t]);
    }
    const pos = new Map<string, { x: number; y: number }>();
    let height = 0;
    for (const [depth, list] of columns) {
      list.forEach((t, row) => {
        pos.set(t.key, { x: depth * (W + GAP_X), y: row * (H + GAP_Y) });
      });
      height = Math.max(height, list.length * (H + GAP_Y) - GAP_Y);
    }
    const width = Math.max(...[...columns.keys()].map((d) => d * (W + GAP_X) + W), W);
    return { pos, width, height: Math.max(height, H) };
  }, [tasks]);

  if (tasks.length === 0) {
    return <p className="text-[13px] text-fg-faint">No tasks yet — JARVIS is planning.</p>;
  }
  return (
    <div className="overflow-x-auto pb-2" data-testid="ul-graph">
      <div className="relative" style={{ width: layout.width, height: layout.height }}>
        <svg className="pointer-events-none absolute inset-0" width={layout.width} height={layout.height} aria-hidden>
          {tasks.flatMap((t) =>
            t.depends_on.map((dep) => {
              const a = layout.pos.get(dep);
              const b = layout.pos.get(t.key);
              if (!a || !b) return null;
              const x1 = a.x + W;
              const y1 = a.y + H / 2;
              const x2 = b.x;
              const y2 = b.y + H / 2;
              const mid = (x1 + x2) / 2;
              return (
                <path
                  key={`${dep}-${t.key}`}
                  d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2 - 4},${y2}`}
                  fill="none"
                  stroke="var(--color-ql-muted)"
                  strokeOpacity={0.45}
                  strokeWidth={1.5}
                  markerEnd="url(#ul-arrow)"
                />
              );
            }),
          )}
          <defs>
            <marker id="ul-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" fill="var(--color-ql-muted)" fillOpacity={0.6} />
            </marker>
          </defs>
        </svg>
        {tasks.map((t) => {
          const p = layout.pos.get(t.key);
          if (!p) return null;
          const state = TASK[t.state];
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => onSelect(t.key)}
              className={cx(
                "absolute flex flex-col justify-between rounded-xl border bg-ql-raised px-3 py-2 text-left transition-colors",
                BORDER[state.tone],
                selected === t.key ? "ring-1 ring-ql/60" : "hover:bg-white/[0.04]",
              )}
              style={{ left: p.x, top: p.y, width: W, height: H }}
              data-testid="ul-node"
              data-state={t.state}
              aria-label={`${t.key}: ${t.title}, ${state.label}`}
            >
              <span className="flex items-center gap-2">
                <AgentName id={t.owner} />
                <span className="truncate text-[12.5px] text-fg">{t.title}</span>
              </span>
              <span className="flex items-center justify-between text-2xs">
                <span className={cx("flex items-center gap-1", TEXT[state.tone])}>
                  <span aria-hidden className={cx(t.running && "animate-pulse")}>
                    {state.icon}
                  </span>
                  {state.label}
                </span>
                <span className="font-mono text-fg-faint">
                  {t.key} · {t.attempts}/{t.max_attempts}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
