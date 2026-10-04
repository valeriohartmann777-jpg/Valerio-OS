import type { MemoryItem } from "@jarvis/protocol";
import { type FormEvent, useState } from "react";

import { ApiError, api } from "../../lib/api";
import { useJarvis } from "../../store/store";
import { Section, cx } from "../ui/primitives";

const KIND_LABEL: Record<MemoryItem["kind"], string> = {
  preference: "Preference",
  fact: "Fact",
  routine: "Routine",
  correction: "Correction",
};

/** What JARVIS keeps in mind about the user. It learns these from conversations
 * ("merk dir …", corrections); they can also be added or removed here. */
export function MemoryPanel() {
  const memories = useJarvis((s) => s.memories);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    setError(null);
    try {
      await api.addMemory(text.trim());
      setText("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    }
  };

  return (
    <Section
      title="Memory"
      aside={<span className="font-mono text-2xs text-fg-faint">{memories.length || ""}</span>}
    >
      <div data-testid="memory-panel">
        {memories.length === 0 ? (
          <p className="text-[13px] leading-relaxed text-fg-faint">
            Nothing yet. Tell JARVIS what to keep in mind — “Merk dir: …” — or correct it; it remembers.
          </p>
        ) : (
          <ul className="max-h-[260px] space-y-2 overflow-y-auto pr-1">
            {[...memories].reverse().map((memory) => (
              <li key={memory.number} className="group flex items-start gap-2 text-[13px]" data-testid="memory-item">
                <span className="mt-[3px] font-mono text-2xs text-fg-faint">M{memory.number}</span>
                <div className="min-w-0 flex-1">
                  <p className="selectable leading-snug text-fg-muted">{memory.text}</p>
                  <span className="text-2xs text-fg-faint">{KIND_LABEL[memory.kind] ?? memory.kind}</span>
                </div>
                <button
                  type="button"
                  aria-label={`Forget M${memory.number}`}
                  className="no-drag text-fg-faint opacity-0 transition-opacity group-hover:opacity-100 hover:text-danger"
                  onClick={() => void api.forgetMemory(memory.number)}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <form onSubmit={(e) => void add(e)} className="mt-3">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={300}
            placeholder="Add something to remember…"
            className={cx(
              "no-drag w-full rounded-lg border border-hairline bg-transparent px-3 py-1.5 text-[13px] text-fg",
              "placeholder:text-fg-faint focus:border-hairline-strong focus:outline-none",
            )}
            data-testid="memory-input"
          />
          {error && <p className="mt-1 text-xs text-danger">{error}</p>}
        </form>
      </div>
    </Section>
  );
}
