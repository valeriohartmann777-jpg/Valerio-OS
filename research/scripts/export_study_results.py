"""Export the results of one event-study run as flat files.

Usage (from research/):
    python scripts/export_study_results.py --dataset NQ_KAGGLE_1M --prefix nq

Reads what the runner wrote: event_studies/<dataset>/<ID>/result.json, the test registry
results/test_registry.csv and the split ledger. Writes into outputs/:
    <prefix>_study_results.csv   one row per study: effect, 95% CI, p, BH-adjusted p, halves,
                                 economic effect vs cost hurdle, twin, decision
    <prefix>_gate_results.csv    one row per study: G1..G6 (true / false / empty = not applicable)
    <prefix>_fdr_results.csv     every test in the registry with its global and family BH p
                                 (G2 is judged over ALL registered tests, not only this run)
    <prefix>_full_results.json   all of the above plus dataset, split and per-study notes
Nothing is re-estimated. The BH-adjusted p in the study table is the one the gates used at
run time; the FDR table shows the registry as it is now.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from edgelab.config import RESULTS_DIR, RESEARCH_ROOT  # noqa: E402
from edgelab.stats.multiple_testing import TestRegistry  # noqa: E402
from edgelab.studies.runner import GATES, OUT_ROOT, TYPE_GATES, load_config  # noqa: E402


def study_rows(results: list[dict]) -> tuple[list[dict], list[dict]]:
    studies, gates = [], []
    for r in results:
        pr, tw = r.get("primary") or {}, r.get("twin") or {}
        halves = r.get("halves") or [None, None]
        studies.append({
            "study": r["sid"], "family": r["family"], "type": r["type"], "hypothesis": r["hypothesis"], "title": r["title"],
            "status": r["status"], "events": r["n_events"], "event_dates": r["n_event_dates"],
            "effect_label": pr.get("label"), "effect": pr.get("point"), "ci_lo": pr.get("lo"), "ci_hi": pr.get("hi"),
            "se": pr.get("se"), "p_value": pr.get("p_value"), "p_bh_at_run": r.get("p_bh"), "n_dates": pr.get("n_dates"),
            "n_by_group": json.dumps(pr.get("n_by_group")) if pr else None,
            "half1": halves[0], "half2": halves[1], "econ_effect": r.get("econ_effect"), "cost_hurdle": r.get("hurdle"),
            "twin_mode": r.get("twin_mode"), "twin_effect": tw.get("point"), "twin_lo": tw.get("lo"), "twin_hi": tw.get("hi"),
            "ranking_score": r.get("score"), "decision": r.get("decision"), "failed_gates": " ".join(r.get("failed_gates") or []),
            "evidence_class": r.get("evidence_class"), "provenance": r.get("provenance"), "git_commit": r.get("git_commit"),
        })
        g = r.get("gates") or {}
        gates.append({"study": r["sid"], "type": r["type"],
                      **{k: (g.get(k) if k in TYPE_GATES[r["type"]] else None) for k in GATES},
                      "applicable": " ".join(TYPE_GATES[r["type"]]), "failed": " ".join(r.get("failed_gates") or []),
                      "decision": r.get("decision")})
    return studies, gates


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, help="dataset id whose run is exported (event_studies/<id>/)")
    ap.add_argument("--prefix", required=True, help="file name prefix, e.g. nq")
    ap.add_argument("--root", type=Path, default=OUT_ROOT, help=argparse.SUPPRESS)       # software tests
    ap.add_argument("--registry", type=Path, default=RESULTS_DIR / "test_registry.csv", help=argparse.SUPPRESS)
    ap.add_argument("--out", type=Path, default=RESEARCH_ROOT / "outputs")
    args = ap.parse_args()

    run_dir = args.root / args.dataset
    paths = sorted(run_dir.glob("*/result.json"))
    if not paths:
        print(f"no results in {run_dir}: run scripts/run_event_studies.py --dataset {args.dataset} first")
        return 1
    cfg = load_config()
    order = {sid: i for i, sid in enumerate(cfg["studies"])}
    results = sorted((json.loads(p.read_text()) for p in paths), key=lambda r: order.get(r["sid"], 10**6))
    studies, gates = study_rows(results)
    q = float(cfg["common"]["fdr_q"])
    reg = TestRegistry(args.registry).adjusted(q)
    provs = {r.get("provenance") for r in results}
    if len(reg):
        reg["this_run"] = reg["notes"].astype(str).apply(lambda n: any(f"prov={p}" in n for p in provs))
        reg["role"] = reg["notes"].astype(str).str.extract(r"role=([a-z]+)", expand=False)
    args.out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(studies).to_csv(args.out / f"{args.prefix}_study_results.csv", index=False)
    pd.DataFrame(gates).to_csv(args.out / f"{args.prefix}_gate_results.csv", index=False)
    reg.to_csv(args.out / f"{args.prefix}_fdr_results.csv", index=False)
    ledger = RESULTS_DIR / "split_ledger.json"
    summary = {
        "dataset": args.dataset, "exported_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "studies_in_config": len(cfg["studies"]), "studies_with_results": len(results),
        "status_counts": pd.Series([r["status"] for r in results]).value_counts().to_dict(),
        "decision_counts": pd.Series([r.get("decision") for r in results]).value_counts().to_dict(),
        "fdr_q": q, "tests_in_registry": int(len(reg)), "tests_of_this_run": int(reg["this_run"].sum()) if len(reg) else 0,
        "split_ledger": json.loads(ledger.read_text()) if ledger.exists() else None,
        "summary_md": str((run_dir / "SUMMARY.md").relative_to(RESEARCH_ROOT)) if (run_dir / "SUMMARY.md").exists()
        and run_dir.is_relative_to(RESEARCH_ROOT) else None,
        "studies": results,
    }
    (args.out / f"{args.prefix}_full_results.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"{len(results)} studies exported to {args.out}/{args.prefix}_*.csv / .json "
          f"(status {summary['status_counts']}, decisions {summary['decision_counts']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
