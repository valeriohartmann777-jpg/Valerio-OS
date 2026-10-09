"""Write reports/DATA_QUALITY_REPORT.md for every prepared dataset.

Usage (from research/):
    python scripts/data_quality.py            # all datasets in the manifest
    python scripts/data_quality.py --id ES_1m

The report is descriptive. It must be read, and its findings handled in
journal/RESEARCH_JOURNAL.md, before any event study runs on the data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgelab.config import RESEARCH_ROOT, load_instrument, load_session_template  # noqa: E402
from edgelab.data.manifest import get_entry, load_manifest, load_processed  # noqa: E402
from edgelab.data.quality import check_bars, render_markdown  # noqa: E402

REPORT = RESEARCH_ROOT / "reports" / "DATA_QUALITY_REPORT.md"
JSON_DIR = RESEARCH_ROOT / "reports" / "data_quality"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", help="only this dataset id")
    ap.add_argument("--out", type=Path, default=REPORT)
    args = ap.parse_args()

    entries = [get_entry(args.id)] if args.id else load_manifest()
    if not entries:
        print("No datasets registered; DATA_QUALITY_REPORT.md stays in its BLOCKED state.")
        return 1
    reports = []
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    for e in entries:
        bars, meta = load_processed(e.id)
        inst = load_instrument(e.instrument)
        tpl = load_session_template(inst.session_template)
        rep = check_bars(
            bars,
            instrument=e.instrument,
            source=f"{e.source} (dataset {e.id}, adjustment: {e.adjustment})",
            template=tpl,
            bar_minutes=e.bar_minutes,
            tick_size=inst.tick_size,
            roll_cfg=inst.roll,
            meta=meta,
        )
        rep["evidence_class"] = "PROXY" if inst.is_proxy else "CME"
        (JSON_DIR / f"{e.id}.json").write_text(json.dumps(rep, indent=2, default=str))
        reports.append(rep)
        print(f"{e.id}: {rep['rows']} rows, {rep.get('trading_dates', 0)} trading dates, "
              f"{rep.get('first_ts')} .. {rep.get('last_ts')}")
    args.out.write_text(render_markdown(reports))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
