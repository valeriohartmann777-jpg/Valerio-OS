import type { QbBlueprint, QsSegment } from "@jarvis/protocol";

import { Badge, Card, Hash } from "../ui";
import type { Mode } from "../research/common";
import { BlueprintStatus, ClassBadge, tc } from "./common";

/**
 * The Strategy Blueprint: what the strategy does in plain words, where every field comes from
 * (source quote, you, a declared research default, or still missing), and what can't be tested.
 */

export function valueAt(spec: Record<string, unknown>, path: string): unknown {
  let cur: unknown = spec;
  for (const part of path.split(".")) {
    if (cur && typeof cur === "object" && part in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return cur;
}

function show(value: unknown): string {
  if (value === undefined) return "—";
  if (value === null) return "none";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function BlueprintView({
  blueprint,
  segments,
  mode,
  onSeek,
}: {
  blueprint: QbBlueprint;
  segments?: Map<string, QsSegment>;
  mode: Mode;
  onSeek?: (s: QsSegment) => void;
}) {
  const rows = Object.entries(blueprint.provenance).sort(([a], [b]) => a.localeCompare(b));
  const blocking = rows.filter(([, p]) => p.class === "MISSING_BLOCKING");
  const defaults = rows.filter(([, p]) => p.class === "DEFAULT_RESEARCH_ASSUMPTION");
  const visible = mode === "Simple" ? rows.filter(([, p]) => p.class !== "INFERRED_NONCRITICAL") : rows;

  return (
    <Card
      title={`Strategy blueprint · v${blueprint.number}`}
      aside={
        <span className="flex items-center gap-2">
          {blueprint.origin === "owner" && <Badge tone="accent">Your definitions applied</Badge>}
          <BlueprintStatus status={blueprint.status} />
        </span>
      }
      testId="blueprint-view"
    >
      {blueprint.summary && <p className="mb-3 text-[14px] text-fg">{blueprint.summary}</p>}
      <div className="grid gap-5 lg:grid-cols-2">
        <div>
          <p className="label mb-2">What it does</p>
          {blueprint.what_it_does?.length ? (
            <ol className="grid list-decimal gap-1 pl-5 text-[13px] text-fg" data-testid="blueprint-rules">
              {blueprint.what_it_does.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ol>
          ) : (
            <p className="text-[13px] text-fg-muted">No testable rule yet.</p>
          )}
          <p className="mt-3 text-2xs text-fg-faint">
            {blocking.length
              ? `${blocking.length} field(s) must be defined before anything is tested — asked as ${blueprint.questions.length} grouped question(s) in the Research Room.`
              : defaults.length
                ? `${defaults.length} declared research default(s) — the source did not say these; they are labelled in the report.`
                : "Every critical field comes from the source or from you."}
          </p>
        </div>
        {!!blueprint.illustration?.length && (
          <div className="rounded-xl border border-dashed border-ql-border px-4 py-3" data-testid="blueprint-illustration">
            <p className="font-mono text-2xs tracking-[0.12em] text-ql-warning uppercase">{blueprint.illustration[0]}</p>
            <ul className="mt-2 grid gap-1 text-[12px] text-fg-muted">
              {blueprint.illustration.slice(1).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {blueprint.ambiguities.length > 0 && (
        <div className="mt-5">
          <p className="label mb-2">Terms with more than one meaning</p>
          <ul className="grid gap-2" data-testid="blueprint-ambiguities">
            {blueprint.ambiguities.map((a) => {
              const chosen = a.alternatives.find((alt) => alt.id === a.chosen);
              return (
                <li key={a.id} className="rounded-lg border border-ql-border px-3 py-2 text-[13px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-fg">{a.name}</span>
                    {a.material && <Badge tone="warning">changes results</Badge>}
                    <span className="ml-auto text-2xs text-fg-faint">
                      {a.basis === "open" ? "open — you decide" : a.basis === "default" ? "research default" : a.basis === "user" ? "you chose" : "the source says"}
                    </span>
                  </div>
                  <p className="mt-1 text-[12px] text-fg-muted">{a.why}</p>
                  {chosen ? (
                    <p className="mt-1 text-[12px] text-fg">→ {chosen.label}: {chosen.definition}</p>
                  ) : (
                    <p className="mt-1 text-[12px] text-ql-warning">
                      Options: {a.alternatives.filter((x) => x.supported).map((x) => x.label).join(" · ")}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {blueprint.unsupported.length > 0 && (
        <div className="mt-5" data-testid="blueprint-unsupported">
          <p className="label mb-2">Not testable here</p>
          <ul className="grid gap-1 text-[12px] text-fg-muted">
            {blueprint.unsupported.map((u) => (
              <li key={u.feature}>
                ⊘ <span className="text-fg">{u.feature}</span> — {u.reason}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-2xs text-fg-faint">These parts are reported as untested; JARVIS never swaps in a look-alike rule silently.</p>
        </div>
      )}

      <div className="mt-5 overflow-x-auto">
        <p className="label mb-2">Where each field comes from</p>
        <table className="w-full text-left text-[12px]" data-testid="blueprint-provenance">
          <thead className="text-2xs text-fg-faint">
            <tr>
              <th className="py-1 pr-3 font-normal">Field</th>
              <th className="py-1 pr-3 font-normal">Value</th>
              <th className="py-1 pr-3 font-normal">Basis</th>
              <th className="py-1 font-normal">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {visible.map(([path, p]) => (
              <tr key={path} className="border-t border-ql-border/60 align-top" data-class={p.class}>
                <td className="py-1.5 pr-3 font-mono text-2xs text-fg-muted">{path}</td>
                <td className="selectable max-w-[260px] truncate py-1.5 pr-3 font-mono text-2xs text-fg" title={show(valueAt(blueprint.spec, path))}>
                  {p.class === "MISSING_BLOCKING" ? <span className="text-ql-danger">undefined</span> : show(valueAt(blueprint.spec, path))}
                </td>
                <td className="py-1.5 pr-3">
                  <ClassBadge cls={p.class} />
                </td>
                <td className="py-1.5 text-fg-muted">
                  {p.segment_ids.map((id) => {
                    const seg = segments?.get(id);
                    return seg && onSeek ? (
                      <button key={id} type="button" className="mr-1 font-mono text-[10px] text-ql hover:underline" onClick={() => onSeek(seg)}>
                        {seg.start_ms !== null ? tc(seg.start_ms) : id}
                      </button>
                    ) : null;
                  })}
                  {p.note}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {mode !== "Simple" && blueprint.spec_sha256 && (
        <p className="mt-3 text-2xs text-fg-faint">
          Spec hash <Hash value={blueprint.spec_sha256} /> {blueprint.version_id && <>· frozen as version <Hash value={blueprint.version_id} /></>}
        </p>
      )}
    </Card>
  );
}
