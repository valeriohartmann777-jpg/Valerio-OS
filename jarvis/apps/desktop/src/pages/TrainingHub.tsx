import { TrainedModel } from "../components/learning/TrainedModel";
import { Evaluations } from "../components/training/Evaluations";
import { FeedbackSection } from "../components/training/Feedback";
import { MemoryPanel } from "../components/panels/MemoryPanel";
import { Icon, type IconName } from "../components/ui/icons";
import { EmptyState, PageHeader, Panel, StatusPill, SubNav, type PillTone } from "../components/ui/shell";
import { learningLabel, trainingVerdict } from "../lib/format";
import { go } from "../lib/history";
import { useJarvis } from "../store/store";
import { Learning } from "./Learning";

/**
 * Training Hub — the former Learning tab (brief §5). It separates what JARVIS really does:
 * a research loop (Claude forms and backtests hypotheses), a small local model it trains and
 * validates, the knowledge it keeps, and the evaluations that decide what counts. It never
 * claims to retrain Claude's weights.
 */

type Section = "overview" | "research" | "model" | "knowledge" | "feedback" | "evaluations";

const VERDICT_PILL: Record<string, PillTone> = { success: "success", warning: "warning", danger: "danger", muted: "muted", faint: "muted", accent: "info" };

const SECTIONS: readonly { id: Section; label: string; icon: IconName }[] = [
  { id: "overview", label: "Overview", icon: "info" },
  { id: "research", label: "Research loop", icon: "flask" },
  { id: "model", label: "Trained model", icon: "target" },
  { id: "knowledge", label: "Knowledge", icon: "book" },
  { id: "feedback", label: "Feedback", icon: "thumbUp" },
  { id: "evaluations", label: "Evaluations", icon: "check" },
];

export function TrainingHub() {
  const view = useJarvis((s) => s.view);
  const requested = view.name === "training" ? view.section : undefined;
  const section: Section = SECTIONS.some((s) => s.id === requested) ? (requested as Section) : "overview";

  return (
    <div className="h-full overflow-y-auto px-7" style={{ "--area": "var(--color-training)" } as React.CSSProperties} data-testid="training-hub">
      <div className="mx-auto max-w-[1240px] pb-10">
        <PageHeader
          eyebrow="JARVIS / Learning division"
          title="Training Hub."
          subtitle="What JARVIS learns, how it is checked, and what it keeps. Improvements count only with evidence."
        />
        <SubNav label="Training Hub sections" items={SECTIONS} current={section} onSelect={(id) => go({ name: "training", section: id })} />
        {section === "overview" && <Overview />}
        {section === "research" && (
          <Panel>
            <Learning embedded />
          </Panel>
        )}
        {section === "model" && <ModelSection />}
        {section === "knowledge" && <Knowledge />}
        {section === "feedback" && <FeedbackSection />}
        {section === "evaluations" && <Evaluations />}
      </div>
    </div>
  );
}

function Overview() {
  const learning = useJarvis((s) => s.learning);
  const training = useJarvis((s) => s.training);
  const memories = useJarvis((s) => s.memories);
  const findings = learning?.counts.validated ?? 0;
  const learningTone: PillTone =
    !learning ? "muted" : learning.state === "error" ? "danger" : ["needs_brain", "stalled"].includes(learning.state) ? "warning" : learning.enabled ? "info" : "muted";
  const model = training?.model;
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <Panel title="What “learning” means here" testId="training-truth">
        <ul className="space-y-3 text-[14px] leading-relaxed text-fg-muted">
          <Truth icon="flask" title="Research loop">
            Claude (through your AI route) forms trading hypotheses and JARVIS backtests them on minute data. A finding
            counts only if it also holds on data it never tuned on.
          </Truth>
          <Truth icon="target" title="Trained model">
            A small local model of when support and resistance hold, trained on your computer and judged walk-forward
            on months it never saw. This is the only part that trains model weights — and only its own.
          </Truth>
          <Truth icon="book" title="Knowledge">
            What JARVIS keeps in mind about you (memory) and the lessons the research loop wrote down. You can add and
            remove entries.
          </Truth>
          <Truth icon="thumbUp" title="Feedback">
            Your 👍 / 👎 on replies, with what you asked and which route answered. It is evidence: a correction becomes
            part of JARVIS only when you keep it as a memory.
          </Truth>
          <Truth icon="alert" title="What it doesn't do">
            JARVIS doesn&apos;t change Claude&apos;s model weights and doesn&apos;t switch on improvements by itself.
          </Truth>
        </ul>
      </Panel>
      <div className="flex flex-col gap-5">
        <Panel title="Research loop" aside={<StatusPill tone={learningTone} testId="training-learning-state">{learning ? learningLabel(learning.state) : "—"}</StatusPill>}>
          <p className="text-[13px] text-fg-muted">
            {learning
              ? `${learning.counts.rounds ?? 0} rounds · ${learning.counts.tests ?? 0} backtests · ${findings} validated finding${findings === 1 ? "" : "s"}`
              : "Not reported yet."}
            {learning?.detail ? ` — ${learning.detail}` : ""}
          </p>
          <SectionLink onClick={() => go({ name: "training", section: "research" })}>Open research loop</SectionLink>
        </Panel>
        <Panel
          title="Trained model"
          aside={
            <StatusPill tone={model ? VERDICT_PILL[trainingVerdict(model.status).tone] ?? "muted" : "muted"} testId="training-model-state">
              {model ? trainingVerdict(model.status).label : "Not trained yet"}
            </StatusPill>
          }
        >
          <p className="text-[13px] text-fg-muted">
            {model ? `Run ${model.run} · data until ${model.data_until}` : "Train it once from its section — it uses free market data."}
          </p>
          <SectionLink onClick={() => go({ name: "training", section: "model" })}>Open trained model</SectionLink>
        </Panel>
        <Panel title="Knowledge" aside={<StatusPill tone="muted">{`${memories.length} memor${memories.length === 1 ? "y" : "ies"}`}</StatusPill>}>
          <p className="text-[13px] text-fg-muted">Preferences, facts and corrections JARVIS keeps in mind.</p>
          <SectionLink onClick={() => go({ name: "training", section: "knowledge" })}>Open knowledge</SectionLink>
        </Panel>
      </div>
    </div>
  );
}

function Truth({ icon, title, children }: { icon: IconName; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <Icon name={icon} size={18} className="mt-0.5 shrink-0 text-[var(--area)]" />
      <span>
        <b className="text-fg">{title}.</b> {children}
      </span>
    </li>
  );
}

function SectionLink({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="mt-3 inline-flex items-center gap-1.5 text-[13px] font-semibold text-[var(--area)] hover:underline">
      {children} <Icon name="arrow" size={14} />
    </button>
  );
}

function ModelSection() {
  const training = useJarvis((s) => s.training);
  return (
    <Panel>
      {training ? (
        <TrainedModel training={training} />
      ) : (
        <EmptyState title="Training status isn't reported yet">The backend reports it once it has started.</EmptyState>
      )}
    </Panel>
  );
}

function Knowledge() {
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <Panel padded={false}>
        <MemoryPanel />
      </Panel>
      <Panel title="Research notes">
        <p className="text-[13px] leading-relaxed text-fg-muted">
          Lessons the research loop wrote down are listed in the research loop, next to the backtests they came from.
        </p>
        <SectionLink onClick={() => go({ name: "training", section: "research" })}>Open research loop</SectionLink>
      </Panel>
    </div>
  );
}
