import type {
  LearningNote,
  LearningRound,
  LearningStats,
  LearningStatus,
  LearningStudy,
  LearningTest,
} from "@jarvis/protocol";
import { useEffect, useState } from "react";

import { Button, Empty, Meter, Pill, StatusDot, type Tone, cx } from "../components/ui/primitives";
import { ApiError, api } from "../lib/api";
import { clock, learningLabel, modelLabel, signed, usd } from "../lib/format";
import { dispatch, useJarvis } from "../store/store";

const STATE_TONE: Record<LearningStatus["state"], Tone> = {
  off: "faint",
  preparing: "accent",
  running: "accent",
  waiting: "muted",
  budget: "muted",
  stalled: "warning",
  needs_brain: "warning",
  error: "danger",
};

const TEST_STATUS: Record<LearningTest["status"], { label: string; tone: Tone }> = {
  validated: { label: "Validated", tone: "success" },
  oos_failed: { label: "Failed out-of-sample", tone: "muted" },
  rejected: { label: "Rejected", tone: "faint" },
  invalid: { label: "Invalid", tone: "danger" },
};

/** JARVIS's own trading research: status, budget, findings, notes and the log. */
export function Learning() {
  const learning = useJarvis((s) => s.learning);
  const [findings, setFindings] = useState<LearningTest[]>([]);
  const [tests, setTests] = useState<LearningTest[]>([]);
  const [notes, setNotes] = useState<LearningNote[]>([]);
  const [rounds, setRounds] = useState<LearningRound[]>([]);
  const [studies, setStudies] = useState<LearningStudy[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reload the lists whenever something was recorded.
  const counts = learning?.counts ?? {};
  const changed = [counts.tests, counts.notes, counts.rounds, counts.studies, learning?.round, learning?.state].join("-");
  useEffect(() => {
    let live = true;
    void Promise.all([
      api.learningFindings(),
      api.learningTests(40),
      api.learningNotes(),
      api.learningRounds(12),
      api.learningStudies(30),
    ]).then(
      ([f, t, n, r, st]) => {
        if (!live) return;
        setFindings(f);
        setTests(t);
        setNotes(n);
        setRounds(r);
        setStudies(st);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [changed]);

  const toggle = async () => {
    setBusy(true);
    setError(null);
    try {
      await (learning?.enabled ? api.stopLearning() : api.startLearning());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto" data-testid="learning-page">
      <div className="mx-auto max-w-[1040px] px-10 py-10">
        <header className="flex items-start justify-between gap-8">
          <div>
            <h1 className="text-2xl font-light tracking-[-0.01em] text-fg">Learning</h1>
            <p className="mt-2 max-w-[620px] text-[13px] leading-relaxed text-fg-muted">
              JARVIS researches scalping and day trading on NQ and XAUUSD on its own: it forms hypotheses, backtests
              them on minute data and keeps what holds up on data it never tuned on. It only learns — no broker, no
              orders.
            </p>
          </div>
          <Button
            variant={learning?.enabled ? "ghost" : "primary"}
            onClick={() => void toggle()}
            disabled={busy || !learning}
            data-testid="learning-toggle"
          >
            {learning?.enabled ? "Stop learning" : "Start learning"}
          </Button>
        </header>
        {error && <p className="mt-4 text-[13px] text-danger">{error}</p>}

        {learning && <StatusStrip learning={learning} />}

        {learning && <FocusEditor focus={learning.focus} />}

        <section className="mt-12">
          <h2 className="label mb-3">Validated findings</h2>
          {findings.length === 0 ? (
            <Empty>
              None yet. A finding counts only when it passes in-sample and then holds up out-of-sample — expect this
              to be rare; most ideas don't survive costs.
            </Empty>
          ) : (
            <div className="space-y-3" data-testid="learning-findings">
              {findings.map((test) => (
                <Finding key={test.number} test={test} />
              ))}
            </div>
          )}
        </section>

        <div className="mt-12 grid grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)] gap-10">
          <section>
            <h2 className="label mb-3">Knowledge · {notes.length}</h2>
            {notes.length === 0 ? (
              <Empty>Lessons appear here as JARVIS writes them down.</Empty>
            ) : (
              <ul className="space-y-4" data-testid="learning-notes">
                {notes.map((note) => (
                  <Note key={note.number} note={note} />
                ))}
              </ul>
            )}
          </section>
          <section>
            <h2 className="label mb-3">Research log</h2>
            {tests.length === 0 ? (
              <Empty>No backtests yet.</Empty>
            ) : (
              <ul className="divide-y divide-hairline border-y border-hairline" data-testid="learning-tests">
                {tests.map((test) => (
                  <TestRow key={test.number} test={test} />
                ))}
              </ul>
            )}
          </section>
        </div>

        <section className="mt-12">
          <h2 className="label mb-3">Support &amp; resistance studies · {studies.filter((x) => x.ok).length}</h2>
          {studies.length === 0 ? (
            <Empty>
              Level studies measure how often a level type held compared with arbitrary prices touched the same way —
              on the in-sample years only.
            </Empty>
          ) : (
            <ul className="divide-y divide-hairline border-y border-hairline" data-testid="learning-studies">
              {studies.map((study) => (
                <StudyRow key={study.number} study={study} />
              ))}
            </ul>
          )}
        </section>

        <section className="mt-12">
          <h2 className="label mb-3">Rounds</h2>
          {rounds.length === 0 ? (
            <Empty>No rounds yet.</Empty>
          ) : (
            <ul className="divide-y divide-hairline border-y border-hairline" data-testid="learning-rounds">
              {rounds.map((round) => (
                <RoundRow key={round.number} round={round} />
              ))}
            </ul>
          )}
        </section>

        {learning && <DataNote learning={learning} />}
      </div>
    </div>
  );
}

function StatusStrip({ learning }: { learning: LearningStatus }) {
  const tone = STATE_TONE[learning.state];
  const spent = learning.spent_today_usd;
  const stallShare = learning.stall_limit ? (learning.stall_rounds / learning.stall_limit) * 100 : 0;
  const counts = learning.counts;
  return (
    <div className="mt-10 grid grid-cols-4 gap-px overflow-hidden rounded-xl border border-hairline bg-hairline">
      <Cell label="Status">
        <div className="flex items-center gap-2 text-[15px] text-fg" data-testid="learning-state">
          <StatusDot tone={tone} live={learning.state === "running" || learning.state === "preparing"} />
          {learningLabel(learning.state)}
          {learning.state === "running" && learning.round && <span className="text-fg-faint">· round {learning.round}</span>}
        </div>
        {learning.state === "preparing" && learning.progress !== null && (
          <div className="mt-3">
            <Meter value={learning.progress * 100} tone="accent" />
          </div>
        )}
        <p className={cx("mt-2 text-xs leading-relaxed", tone === "danger" ? "text-danger" : "text-fg-muted")}>
          {learning.state === "waiting" && learning.next_round_at
            ? `Next round at ${clock(learning.next_round_at)}.`
            : learning.state === "off"
              ? "Start it and JARVIS researches on its own."
              : learning.detail}
          {learning.state === "needs_brain" && (
            <button
              type="button"
              className="ml-1 text-accent hover:underline"
              onClick={() => dispatch({ type: "navigate", view: { name: "settings" } })}
            >
              Open Settings
            </button>
          )}
        </p>
      </Cell>
      <Cell label="Budget today">
        <div className="font-mono text-[15px] text-fg tabular" data-testid="learning-budget">
          {usd(spent)} <span className="text-fg-faint">/ {usd(learning.budget_usd)}</span>
        </div>
        <div className="mt-3">
          <Meter value={(spent / learning.budget_usd) * 100} tone={spent >= learning.budget_usd * 0.9 ? "warning" : "muted"} />
        </div>
        <p className="mt-2 text-xs text-fg-muted">Hard limit per day, from the API's own usage.</p>
      </Cell>
      <Cell label="Without new finding">
        <div className="font-mono text-[15px] text-fg tabular" data-testid="learning-stall">
          {learning.stall_rounds} <span className="text-fg-faint">/ {learning.stall_limit} rounds</span>
        </div>
        <div className="mt-3">
          <Meter value={stallShare} tone={stallShare >= 75 ? "warning" : "muted"} />
        </div>
        <p className="mt-2 text-xs text-fg-muted">At {learning.stall_limit} it stops itself. Starting again grants new rounds.</p>
      </Cell>
      <Cell label="So far">
        <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 text-xs whitespace-nowrap">
          <Count label="Rounds" value={counts.rounds} />
          <Count label="Backtests" value={counts.tests} />
          <Count label="Passed in-sample" value={counts.in_sample_passed} />
          <Count label="Validated" value={counts.validated} strong />
          <Count label="Confirmed" value={counts.confirmed} />
          <Count label="Level studies" value={counts.studies} />
          <Count label="Notes" value={counts.notes} />
        </dl>
      </Cell>
    </div>
  );
}

function FocusEditor({ focus }: { focus: string }) {
  const [text, setText] = useState(focus);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => setText(focus), [focus]);
  const dirty = text.trim() !== focus.trim();

  const save = async (value: string) => {
    setSaving(true);
    try {
      await api.setLearningFocus(value);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="mt-8">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="label">Research focus</h2>
        <span className="text-2xs text-fg-faint">{saved ? "Saved — used from the next round on" : "What JARVIS studies"}</span>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        maxLength={1500}
        className="selectable w-full resize-y rounded-xl border border-hairline bg-canvas-2 px-4 py-3 text-[13px] leading-relaxed text-fg outline-none focus:border-hairline-strong"
        data-testid="learning-focus"
      />
      <div className="mt-2 flex justify-end gap-2">
        <Button onClick={() => void save("")} disabled={saving} title="Back to the default from config/learning.yaml">
          Default
        </Button>
        <Button variant="primary" onClick={() => void save(text)} disabled={saving || !dirty} data-testid="learning-focus-save">
          Save focus
        </Button>
      </div>
    </section>
  );
}

function StudyRow({ study }: { study: LearningStudy }) {
  const r = study.result;
  const z = r.edge_z;
  const tone: Tone = !study.ok ? "danger" : z === null ? "faint" : z >= 2 ? "success" : z <= -2 ? "warning" : "muted";
  const verdict = !study.ok
    ? "Invalid"
    : z === null
      ? "Too few touches"
      : z >= 2
        ? "Holds more than chance"
        : z <= -2
          ? "Breaks more than chance"
          : "No edge";
  return (
    <li className="py-2.5">
      <div className="flex items-baseline justify-between gap-4">
        <div className="min-w-0 truncate text-[13px] text-fg">
          <span className="mr-2 font-mono text-2xs text-fg-faint">S{study.number}</span>
          {study.name}
        </div>
        <Pill tone={tone}>{verdict}</Pill>
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-4 text-xs">
        <span className="min-w-0 truncate text-fg-faint" title={study.spec.question}>
          {[study.instrument, study.spec.timeframe, study.spec.side].filter(Boolean).join(" ")} ·{" "}
          <span className="font-mono">{study.spec.level}</span>
          {!study.ok && r.error && ` · ${r.error}`}
        </span>
        {study.ok && r.held_rate !== null && r.expected_rate !== null && (
          <span className="shrink-0 font-mono text-fg-muted tabular" title="held vs. by chance (control group)">
            {r.touches} touches · held {Math.round(r.held_rate * 100)}% vs {Math.round(r.expected_rate * 100)}% · z{" "}
            {z?.toFixed(1)}
          </span>
        )}
      </div>
    </li>
  );
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="bg-canvas-2 px-5 py-4">
      <div className="label mb-3">{label}</div>
      {children}
    </div>
  );
}

function Count({ label, value, strong }: { label: string; value: number | undefined; strong?: boolean }) {
  return (
    <>
      <dt className="text-fg-faint">{label}</dt>
      <dd className={cx("text-right font-mono tabular", strong ? "text-success" : "text-fg-muted")}>{value ?? 0}</dd>
    </>
  );
}

function Finding({ test }: { test: LearningTest }) {
  const confirmed = test.holdout_confirmed;
  return (
    <article className="rounded-xl border border-hairline bg-canvas-2 px-5 py-4">
      <header className="flex items-start justify-between gap-6">
        <div>
          <div className="flex items-center gap-2 text-2xs text-fg-faint">
            <span className="font-mono">T{test.number}</span>
            <span>·</span>
            <span>{market(test)}</span>
          </div>
          <h3 className="mt-1 text-[15px] text-fg">{test.name}</h3>
          {test.spec.hypothesis && <p className="mt-1 text-[13px] leading-relaxed text-fg-muted">{test.spec.hypothesis}</p>}
        </div>
        {confirmed !== null && (
          <Pill tone={confirmed ? "success" : "warning"}>
            {confirmed ? "Confirmed on unseen data" : "Not confirmed on unseen data"}
          </Pill>
        )}
      </header>
      <table className="mt-4 w-full text-xs">
        <thead className="text-fg-faint">
          <tr className="text-left">
            <th className="py-1 font-normal">Period</th>
            <th className="py-1 text-right font-normal">Trades</th>
            <th className="py-1 text-right font-normal">Win rate</th>
            <th className="py-1 text-right font-normal">Avg R</th>
            <th className="py-1 text-right font-normal">Profit factor</th>
            <th className="py-1 text-right font-normal">t</th>
          </tr>
        </thead>
        <tbody className="font-mono text-fg-muted tabular">
          <StatsRow label="In-sample" stats={test.in_sample} />
          <StatsRow label="Out-of-sample" stats={test.out_of_sample} />
          <StatsRow label="Holdout (unseen)" stats={test.holdout} />
        </tbody>
      </table>
    </article>
  );
}

function StatsRow({ label, stats }: { label: string; stats: LearningStats | null }) {
  if (!stats) return null;
  return (
    <tr className="border-t border-hairline">
      <td className="py-1.5 font-sans text-fg-faint">{label}</td>
      <td className="py-1.5 text-right">{stats.trades}</td>
      <td className="py-1.5 text-right">{Math.round(stats.win_rate * 100)}%</td>
      <td className={cx("py-1.5 text-right", stats.avg_r > 0 ? "text-fg" : "text-danger")}>{signed(stats.avg_r)}</td>
      <td className="py-1.5 text-right">{stats.profit_factor.toFixed(2)}</td>
      <td className="py-1.5 text-right">{stats.t_stat.toFixed(1)}</td>
    </tr>
  );
}

function Note({ note }: { note: LearningNote }) {
  return (
    <li>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-2xs text-fg-faint">N{note.number}</span>
        <span className="text-[13px] text-fg">{note.topic}</span>
      </div>
      <p className="selectable mt-1 text-[13px] leading-relaxed whitespace-pre-wrap text-fg-muted">{note.text}</p>
      {note.sources.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-x-3 text-2xs">
          {note.sources
            .filter((url) => url.startsWith("https://"))
            .map((url) => (
              <a key={url} href={url} target="_blank" rel="noreferrer" className="text-accent/80 hover:text-accent">
                {hostname(url)}
              </a>
            ))}
        </div>
      )}
    </li>
  );
}

function TestRow({ test }: { test: LearningTest }) {
  const status = TEST_STATUS[test.status];
  const stats = test.in_sample;
  return (
    <li className="py-2.5">
      <div className="flex items-baseline justify-between gap-4">
        <div className="min-w-0 truncate text-[13px] text-fg">
          <span className="mr-2 font-mono text-2xs text-fg-faint">T{test.number}</span>
          {test.name}
        </div>
        <Pill tone={status.tone}>{status.label}</Pill>
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-4 text-xs">
        <span className="min-w-0 truncate text-fg-faint" title={test.reason}>
          {market(test)} · {test.reason}
        </span>
        {stats && stats.trades > 0 && (
          <span className="shrink-0 font-mono text-fg-muted tabular">
            {stats.trades} tr · {signed(stats.avg_r)} R
          </span>
        )}
      </div>
    </li>
  );
}

function RoundRow({ round }: { round: LearningRound }) {
  const failed = round.status === "failed" || round.status === "interrupted";
  return (
    <li className="grid grid-cols-[72px_minmax(0,1fr)_auto] items-baseline gap-4 py-2.5 text-[13px]">
      <span className="font-mono text-2xs text-fg-faint">Round {round.number}</span>
      <span className={cx("min-w-0", failed ? "text-warning" : "text-fg-muted")}>
        {round.summary || round.error || (round.status === "running" ? "In progress…" : round.status)}
      </span>
      <span className="font-mono text-2xs text-fg-faint tabular">
        {clock(round.started_at)} · {usd(round.cost_usd)}
        {round.searches > 0 && ` · ${round.searches} search${round.searches === 1 ? "" : "es"}`}
      </span>
    </li>
  );
}

function DataNote({ learning }: { learning: LearningStatus }) {
  const entries = Object.entries(learning.data);
  return (
    <footer className="mt-12 border-t border-hairline pt-5 text-xs leading-relaxed text-fg-faint" data-testid="learning-data">
      <p>
        {entries.length
          ? entries
              .map(([name, d]) => `${name}: ${d.first} → ${d.last}, ${(d.bars / 1_000_000).toFixed(1)}M minute bars`)
              .join(" · ")
          : "Market data downloads on the first start (Dukascopy, free, about 5 years of minute bars)."}
      </p>
      <p className="mt-1">
        NQ is studied on Dukascopy's Nasdaq-100 CFD (moves like the future, prices differ by the basis). Model:{" "}
        {modelLabel(learning.model)}
        {learning.web_search ? ", with web search" : ""}. Holdout results are never shown to the research model.
      </p>
    </footer>
  );
}

function market(test: LearningTest): string {
  return [test.instrument, test.style, test.timeframe].filter(Boolean).join(" ");
}

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
