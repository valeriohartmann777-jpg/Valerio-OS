"""Blind pipeline check: run the 52 pre-registered studies once over a pilot dataset in a
sandbox, to show that the software works on real data. No result is looked at.

Usage (from research/):
    python scripts/pipeline_check.py --dataset ES_DB_PILOT

Steps
  1. Run the full test suite. Any failure: STOP, nothing runs.
  2. Accept only a manifest dataset with purpose ``pipeline_check`` (never research data,
     never the market embargoed until rule freeze) that is prepared, has a non-fatal
     quality report belonging to the prepared file and a ``DATA_QUALITY <id>`` journal note.
  3. Run every study through the unchanged runner (``edgelab/studies/runner.py: run_all``)
     with a non-manifest provenance, so the runner itself forces every output (results,
     test registry, journal copy, rejected ideas) into a temporary directory outside the
     repository. No split ledger is created or read.
  4. Causality on the real bars: for the studies in ``tests/test_studies.py``
     ``CAUSAL_STUDIES``, events before a session boundary must not change when every bar
     after it is removed.
  5. Write ``reports/PIPELINE_CHECK_<id>.md`` with structure only (status, event counts,
     output completeness, NaNs, errors, causality), add a journal note and delete the
     sandbox. Effect sizes, p-values, gates and decisions are never printed, written or
     kept: the pilot period will most likely fall into the DEV period of the real run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from edgelab import journal  # noqa: E402
from edgelab.config import RESEARCH_ROOT, load_instrument, load_research_config, load_session_template  # noqa: E402
from edgelab.data.manifest import DatasetEntry, get_entry, load_processed  # noqa: E402
from edgelab.sessions import label_sessions  # noqa: E402
from edgelab.studies.catalog import STUDIES  # noqa: E402
from edgelab.studies.context import StudyContext  # noqa: E402
from edgelab.studies.runner import REJECTED, context_params, load_config, roll_exclusions, run_all, study_spec  # noqa: E402

DQ_DIR = RESEARCH_ROOT / "reports" / "data_quality"
REPORTS = RESEARCH_ROOT / "reports"
# identical to tests/test_studies.py CAUSAL_STUDIES (a test keeps the two in sync)
CAUSAL_STUDIES = ("A001", "A002", "A003", "A005", "A006", "A009", "A010", "A011", "A012", "B002", "B003", "B004", "B005",
                  "B006", "B008", "B010", "B011", "B013", "B014", "C001", "C004", "C005", "C006", "C007", "C008", "C009",
                  "C010", "C012", "C013", "C014", "C015", "C018")
EVENT_KEY_COLUMNS = ("pos", "direction", "date", "level", "t0", "day")


def refuse(msg: str) -> int:
    print(f"REFUSED: {msg}")
    return 1


def admission(entry: DatasetEntry, rcfg: dict) -> str | None:
    """Why this dataset may not be used for a pipeline check, or ``None``."""
    if entry.purpose != "pipeline_check":
        return (f"{entry.id} has purpose {entry.purpose!r}: only pipeline_check datasets run here; "
                "research data goes through scripts/run_event_studies.py")
    embargoed = rcfg["markets"]["validation_market"]
    if entry.instrument in (embargoed, f"M{embargoed}"):
        return f"{embargoed} is embargoed until rule freeze (RESEARCH_PROTOCOL.md 2.5): its data is validated, never run"
    if load_instrument(entry.instrument).is_proxy:
        return "the pipeline check is for CME data"
    if any(w in entry.source.lower() for w in ("synthetic", "simulated", "random walk", "generated")):
        return "the manifest source describes synthetic data"
    return None


def run_tests() -> tuple[bool, str]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=RESEARCH_ROOT,
                          capture_output=True, text=True)
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    return proc.returncode == 0, lines[-1] if lines else proc.stderr.strip()[-300:]


def event_nans(events: pd.DataFrame) -> int:
    cols = [c for c in EVENT_KEY_COLUMNS if c in events.columns]
    if not cols or events.empty:
        return 0
    return int(events[cols].isna().any(axis=1).sum())


def structural_rows(outs: list, out_root: Path) -> list[dict]:
    """Per study: what ran and what was written; nothing about effects or decisions."""
    rows = []
    for o in outs:
        d = out_root / o.sid
        ev_path = d / "events.parquet"
        events = pd.read_parquet(ev_path) if ev_path.exists() else pd.DataFrame()
        finite = None
        if o.primary is not None:
            finite = bool(np.isfinite(o.primary.get("point", np.nan)) and np.isfinite(o.primary.get("se", np.nan)))
        err = ""
        if o.status == "ERROR":
            tb = [ln for ln in "\n".join(o.notes).splitlines() if ln.strip()]
            err = tb[-1][:160] if tb else "error"
        rows.append({
            "study": o.sid, "status": o.status, "events": int(o.n_events), "event_dates": int(o.n_event_dates),
            "result_json": (d / "result.json").exists(), "events_file": ev_path.exists() or o.n_events == 0,
            "event_rows_with_nan": event_nans(events), "primary_estimate_finite": finite, "error": err,
        })
    return rows


def known_events(events: pd.DataFrame, ctx: StudyContext, cut: pd.Timestamp) -> set:
    """Events whose decision bar and entry bar both close before ``cut``."""
    if not len(events):
        return set()
    t = ctx.b5.index[events["pos"].to_numpy(np.int64)]
    m = np.asarray(t + pd.Timedelta(minutes=2 * ctx.tf) <= cut)
    lvl = events["level"].to_numpy(float) if "level" in events.columns else np.zeros(len(events))
    return set(zip(t[m], events["direction"].to_numpy()[m], np.round(lvl[m], 6)))


def causality_on_real_bars(bars: pd.DataFrame, make_ctx, cfg: dict, cut_dates: list[pd.Timestamp],
                           template) -> list[dict]:
    """Events before each cut (start of a session) with all bars vs. with the bars before the cut only."""
    full = make_ctx(bars)
    rows = []
    for d in cut_dates:
        cut = (pd.Timestamp(d) - pd.Timedelta(days=1) + pd.Timedelta(hours=18)).tz_localize(template.timezone)
        trunc = make_ctx(bars[bars.index < cut])
        for sid in CAUSAL_STUDIES:
            try:
                a = known_events(STUDIES[sid](full, study_spec(cfg, sid)).events, full, cut)
                b = known_events(STUDIES[sid](trunc, study_spec(cfg, sid)).events, trunc, cut)
                rows.append({"cut": str(cut), "study": sid, "events_compared": len(a | b), "identical": a == b, "error": ""})
            except Exception as exc:  # reported, never hidden
                rows.append({"cut": str(cut), "study": sid, "events_compared": 0, "identical": False,
                             "error": f"{type(exc).__name__}: {exc}"[:160]})
    return rows


def md_table(rows: list[dict]) -> str:
    if not rows:
        return "_none_\n"
    cols = list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, help="a pipeline_check dataset id from configs/datasets.yaml")
    ap.add_argument("--keep-sandbox", action="store_true", help=argparse.SUPPRESS)  # debugging the software only
    args = ap.parse_args()

    entry = get_entry(args.dataset)
    rcfg = load_research_config()
    why = admission(entry, rcfg)
    if why:
        return refuse(why)
    print("running the test suite ...")
    ok, summary = run_tests()
    print(f"  {summary}")
    if not ok:
        return refuse("the test suite fails: STOP, nothing runs until every test passes")

    bars, meta = load_processed(entry.id)
    dq_path = DQ_DIR / f"{entry.id}.json"
    if not dq_path.exists():
        return refuse(f"{dq_path} missing: run scripts/build_market_data.py (or data_quality.py)")
    dq = json.loads(dq_path.read_text())
    if dq.get("meta", {}).get("processed_sha256") != meta["processed_sha256"]:
        return refuse("the quality report belongs to another version of the prepared file")
    if dq.get("fatal"):
        return refuse(f"quality report is fatal: {dq['fatal']}")
    if not journal.has_note(f"DATA_QUALITY {entry.id}"):
        return refuse(f"no journal note 'DATA_QUALITY {entry.id}' (scripts/journal_note.py)")

    cfg = load_config()
    inst = load_instrument(entry.instrument)
    tpl = load_session_template(inst.session_template)
    prov = f"pipeline_check:{entry.id}:{meta['processed_sha256'][:16]}"

    def make_ctx(b: pd.DataFrame) -> StudyContext:
        lab = label_sessions(b.index, tpl)
        rth = pd.DatetimeIndex(sorted(pd.unique(lab.loc[lab["is_rth"].to_numpy(), "trading_date"])))
        excl = roll_exclusions(b, lab, entry.adjustment, inst.roll, rth)
        return StudyContext(b[["open", "high", "low", "close", "volume"]], inst, tpl, provenance=prov, period_dates=rth,
                            excluded_dates=frozenset(excl), params=context_params(cfg), seed=int(cfg["common"]["seed"]))

    ctx = make_ctx(bars)
    n_ok = int(len(ctx.ok_days()))
    print(f"{entry.id}: {ctx.n_days} RTH dates, {n_ok} event days, {len(ctx.excluded_dates)} roll-excluded dates")

    sandbox = Path(tempfile.mkdtemp(prefix="edgelab_pipeline_check_"))
    started = dt.datetime.now(dt.timezone.utc)
    try:
        shutil.copy(journal.JOURNAL, sandbox / "RESEARCH_JOURNAL.md")
        if REJECTED.exists():
            shutil.copy(REJECTED, sandbox / "REJECTED_IDEAS.md")
        out_root = sandbox / "event_studies"
        run_error = ""
        try:
            outs = run_all(ctx, cfg, out_root=out_root, registry=sandbox / "test_registry.csv",
                           journal_path=sandbox / "RESEARCH_JOURNAL.md", rejected=sandbox / "REJECTED_IDEAS.md",
                           period_label="PIPELINE CHECK (blind)")
            rows = structural_rows(outs, out_root)
        except Exception:
            run_error = traceback.format_exc(limit=8)
            rows = []
        days = ctx.days
        cuts = [days[len(days) // 3], days[2 * len(days) // 3]] if len(days) >= 6 else []
        causal = causality_on_real_bars(bars, make_ctx, cfg, cuts, tpl)
    finally:
        if args.keep_sandbox:
            print(f"sandbox kept at {sandbox} (contains results: delete it without reading them)")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)

    n_err = sum(r["status"] == "ERROR" for r in rows)
    bad_files = [r["study"] for r in rows if not (r["result_json"] and r["events_file"])]
    nan_rows = [r["study"] for r in rows if r["event_rows_with_nan"]]
    nonfinite = [r["study"] for r in rows if r["primary_estimate_finite"] is False]
    causal_fail = sorted({r["study"] for r in causal if not r["identical"]})
    passed = (not run_error and len(rows) == len(cfg["studies"]) and n_err == 0 and not bad_files and not nan_rows
              and not nonfinite and not causal_fail and bool(causal))
    counts = pd.Series([r["status"] for r in rows]).value_counts().to_dict() if rows else {}
    text = "\n".join([
        f"# PIPELINE_CHECK {entry.id}", "",
        f"Run {started.strftime('%Y-%m-%d %H:%M UTC')} by `scripts/pipeline_check.py`. Dataset `{entry.id}` "
        f"(sha256 {meta['processed_sha256'][:16]}), {ctx.n_days} RTH dates, {n_ok} event days, "
        f"{len(ctx.excluded_dates)} roll-excluded dates.", "",
        f"**Result: {'PASS' if passed else 'FAIL'}.** This checks the software on real data only. The studies ran "
        "in a sandbox outside the repository that was deleted afterwards; effect sizes, p-values, gates and decisions "
        "were not printed, written or kept, no split ledger was created and the repository's test registry, journal "
        "and rejected-ideas file were not touched. Nothing here is evidence for or against any hypothesis.", "",
        f"Test suite before the run: {summary}.", "",
        f"Studies run: {len(rows)} of {len(cfg['studies'])}; status counts {counts}. Errors: {n_err}. "
        f"Missing outputs: {bad_files or 'none'}. Event rows with NaN keys: {nan_rows or 'none'}. "
        f"Non-finite primary estimate (value not shown): {nonfinite or 'none'}.", "",
        *(["Runner error:", "", "```", run_error, "```", ""] if run_error else []),
        "## Per study (structure only)", "", md_table(rows),
        "## Causality on the real bars", "",
        "Events whose decision and entry bars close before a cut (start of a session) must be identical with all "
        "bars and with only the bars before the cut.", "",
        f"Studies with differences: {causal_fail or 'none'}; comparisons: {len(causal)}, events compared: "
        f"{sum(r['events_compared'] for r in causal)}.", "", md_table(causal),
    ])
    out = REPORTS / f"PIPELINE_CHECK_{entry.id}.md"
    out.write_text(text + "\n")
    journal.note(f"PIPELINE_CHECK {entry.id}",
                 f"Blind pipeline check of the 52 pre-registered studies on {entry.id} (purpose pipeline_check): "
                 f"{'PASS' if passed else 'FAIL'}. Structure only: {len(rows)} studies ran, {n_err} errors, causality "
                 f"differences {causal_fail or 'none'}. Ran in a deleted sandbox; no effect, p-value, gate or decision "
                 f"was looked at or kept; no split ledger; nothing registered. Report: reports/PIPELINE_CHECK_{entry.id}.md.")
    print(f"{'PASS' if passed else 'FAIL'}: wrote {out.relative_to(RESEARCH_ROOT)} and a journal note")
    for r in rows:
        if r["status"] == "ERROR":
            print(f"  ERROR {r['study']}: {r['error']}")
    if causal_fail:
        print(f"  causality differences: {causal_fail}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
