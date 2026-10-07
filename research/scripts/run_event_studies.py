"""Run the pre-registered event studies on the DEV period of a registered dataset.

Usage (from research/):
    python scripts/run_event_studies.py --dataset ES_1m
    python scripts/run_event_studies.py --dataset ES_1m --ids A001 B002
    python scripts/run_event_studies.py --dataset ES_1m --ids C009 --rerun-reason "why it must run again"

Refuses to run unless the dataset is in the manifest and prepared, the quality report in
reports/data_quality/<id>.json belongs to the prepared file, the journal holds a note
"DATA_QUALITY <id>" (python scripts/journal_note.py) and every study is pre-registered
(python scripts/preregister_studies.py). Only the development market (ES) is accepted;
NQ stays untouched until rule freeze; crypto runs are written as PROXY.

The DEV/VAL/TEST split is frozen in results/split_ledger.json on the first run. No bar
after the last DEV trading date is loaded. Outputs: event_studies/<id>/ (PROXY_<id>/ for
crypto), results/test_registry.csv, journal results, REJECTED_IDEAS.md rows.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from edgelab import journal  # noqa: E402
from edgelab.config import RESEARCH_ROOT, load_instrument, load_research_config, load_session_template  # noqa: E402
from edgelab.data.manifest import get_entry, load_processed  # noqa: E402
from edgelab.sessions import label_sessions  # noqa: E402
from edgelab.studies.context import StudyContext  # noqa: E402
from edgelab.studies.runner import OUT_ROOT, context_params, load_config, roll_exclusions, run_all  # noqa: E402
from edgelab.validation.splits import SplitLedger, chronological_split  # noqa: E402

DQ_DIR = RESEARCH_ROOT / "reports" / "data_quality"


def refuse(msg: str) -> int:
    print(f"REFUSED: {msg}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, help="dataset id from configs/datasets.yaml")
    ap.add_argument("--ids", nargs="*", help="study ids (default: all)")
    ap.add_argument("--rerun-reason", help="required to run a study again on the same dataset")
    args = ap.parse_args()

    cfg = load_config()
    rcfg = load_research_config()
    entry = get_entry(args.dataset)
    inst = load_instrument(entry.instrument)
    dev_market, val_market = rcfg["markets"]["development"], rcfg["markets"]["validation_market"]
    if entry.instrument in (val_market, f"M{val_market}"):
        return refuse(f"{val_market} is embargoed until rule freeze (RESEARCH_PROTOCOL.md 2.5)")
    if entry.instrument != dev_market and not inst.is_proxy:
        return refuse(f"event studies run on the development market {dev_market} or on a PROXY market only")
    if any(w in entry.source.lower() for w in ("synthetic", "simulated", "random walk", "generated")):
        return refuse("the manifest source describes synthetic data; synthetic data is never research evidence")

    bars, meta = load_processed(entry.id)
    dq_path = DQ_DIR / f"{entry.id}.json"
    if not dq_path.exists():
        return refuse(f"{dq_path} missing: run scripts/data_quality.py and read reports/DATA_QUALITY_REPORT.md")
    dq = json.loads(dq_path.read_text())
    if dq.get("meta", {}).get("processed_sha256") != meta["processed_sha256"]:
        return refuse("the quality report belongs to another version of the prepared file: rerun scripts/data_quality.py")
    if dq.get("fatal"):
        return refuse(f"quality report is fatal: {dq['fatal']}")
    if not journal.has_note(f"DATA_QUALITY {entry.id}"):
        return refuse(f"no journal note 'DATA_QUALITY {entry.id}': record how the quality findings were handled "
                      "(python scripts/journal_note.py --title 'DATA_QUALITY <id>' --text-file ...)")

    tpl = load_session_template(inst.session_template)
    lab = label_sessions(bars.index, tpl)
    rth_dates = pd.DatetimeIndex(sorted(pd.unique(lab.loc[lab["is_rth"].to_numpy(), "trading_date"])))
    sp = rcfg["splits"]
    ledger = SplitLedger()
    split = ledger.first_for_market(dev_market) if inst.is_proxy else None
    if split is None:
        split = ledger.register(inst.symbol, meta["processed_sha256"],
                                chronological_split(rth_dates, sp["fractions"], int(sp["embargo_trading_days"])))
    dev_a, dev_b = (pd.Timestamp(x) for x in split["DEV"])
    keep = (lab["trading_date"] <= dev_b).to_numpy()
    bars_dev, lab_dev = bars[keep], lab[keep]
    dev_dates = rth_dates[(rth_dates >= dev_a) & (rth_dates <= dev_b)]
    excl = roll_exclusions(bars_dev, lab_dev, entry.adjustment, inst.roll, rth_dates[rth_dates <= dev_b])
    print(f"{entry.id}: DEV {split['DEV'][0]} .. {split['DEV'][1]} ({len(dev_dates)} trading dates), "
          f"{len(excl)} roll-excluded dates, adjustment {entry.adjustment}")

    ctx = StudyContext(
        bars_dev[["open", "high", "low", "close", "volume"]], inst, tpl,
        provenance=f"manifest:{entry.id}:{meta['processed_sha256'][:16]}",
        period_dates=dev_dates, excluded_dates=frozenset(excl), params=context_params(cfg), seed=int(cfg["common"]["seed"]),
    )
    out_root = OUT_ROOT / (f"PROXY_{entry.id}" if inst.is_proxy else entry.id)
    try:
        outs = run_all(ctx, cfg, ids=args.ids or None, out_root=out_root, rerun_reason=args.rerun_reason,
                       period_label=f"DEV {split['DEV'][0]}..{split['DEV'][1]}")
    except PermissionError as exc:
        return refuse(str(exc))
    for o in outs:
        pr = o.primary
        eff = f"{pr['point']:+.4f} [{pr['lo']:+.4f}, {pr['hi']:+.4f}]" if pr else o.status
        print(f"{o.sid:10s} {o.type} {o.n_events:6d} events  {eff:32s} {o.decision}")
    print(f"outputs: {out_root}")
    return 2 if any(o.status == "ERROR" for o in outs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
