import { Section } from "../ui/primitives";

/** Memory is a Phase 7 capability. The panel says so instead of faking content. */
export function MemoryPanel() {
  return (
    <Section title="Memory" aside={<span className="font-mono text-2xs text-fg-faint">Phase 7</span>}>
      <p className="text-[13px] leading-relaxed text-fg-faint">
        Long-term memory is not active yet. Projects, people and decisions will appear here.
      </p>
    </Section>
  );
}
