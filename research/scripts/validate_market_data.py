"""Validate raw Databento trades and stage their aggregates for the build.

Usage (from research/):
    python scripts/validate_market_data.py --pilot                 # ES and NQ, pilot period
    python scripts/validate_market_data.py --products ES --start 2021-10-01 --end 2026-10-01

Every trade of the raw files is checked (edgelab/data/trades.py): timestamps, duplicates,
prices and sizes, the tick grid, contract mapping through the instrument definitions,
expiries, trades outside CME session hours. Fatal findings stop the pipeline (exit code
1); findings to review stay in the data and are handled in the DATA_QUALITY journal note.
Writes reports/data_quality/trades_<product>_<start>_<end>.json and regenerates
reports/TRADE_DATA_QUALITY_REPORT.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgelab.config import RESEARCH_ROOT, load_instrument, load_session_template  # noqa: E402
from edgelab.data.databento_build import validate_raw  # noqa: E402
from edgelab.data.databento_client import load_databento_config  # noqa: E402
from edgelab.data.databento_report import render_report  # noqa: E402

DQ_DIR = RESEARCH_ROOT / "reports" / "data_quality"
REPORT = RESEARCH_ROOT / "reports" / "TRADE_DATA_QUALITY_REPORT.md"


def write_report(dq_dir: Path = DQ_DIR, out: Path = REPORT) -> None:
    vals = [json.loads(p.read_text()) for p in sorted(dq_dir.glob("trades_*.json"))]
    builds = [json.loads(p.read_text()) for p in sorted(dq_dir.glob("build_*.json"))]
    out.write_text(render_report(vals, builds))


def compact(val: dict) -> dict:
    """The validation without stage-file details (those stay next to the data)."""
    return {k: v for k, v in val.items() if k != "stage_files"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pilot", action="store_true", help="use the pilot period of configs/databento.yaml")
    ap.add_argument("--start", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--end", help="YYYY-MM-DD (UTC, exclusive)")
    ap.add_argument("--products", nargs="+", help="default: all products of configs/databento.yaml")
    args = ap.parse_args()

    cfg = load_databento_config()
    start = args.start or (cfg["pilot"]["start"] if args.pilot else None)
    end = args.end or (cfg["pilot"]["end"] if args.pilot else None)
    if not (start and end):
        ap.error("give --start and --end, or --pilot")
    DQ_DIR.mkdir(parents=True, exist_ok=True)
    failed = False
    for product in args.products or list(cfg["products"]):
        inst = load_instrument(product)
        tpl = load_session_template(inst.session_template)
        try:
            val = validate_raw(cfg, product, start, end, instrument=inst, template=tpl)
        except FileNotFoundError as exc:
            print(f"{product}: {exc}")
            failed = True
            continue
        (DQ_DIR / f"trades_{product}_{start}_{end}.json").write_text(json.dumps(compact(val), indent=2, default=str))
        c = val["trade_checks"]
        print(f"{product}: {'PASS' if val['passed'] else 'FAIL'}  {c.get('records', 0):,} records, "
              f"{c.get('records_outright', 0):,} outright, {val['trading_dates_with_outright_trades']} trading dates")
        for f in val["fatal"]:
            print(f"   FATAL  {f}")
        for r in val["review"]:
            print(f"   review {r}")
        failed |= not val["passed"]
    write_report()
    print(f"wrote {REPORT.relative_to(RESEARCH_ROOT)}")
    if failed:
        print("STOP: fix or explain the fatal findings before anything is built.")
        return 1
    print("Next: python scripts/build_market_data.py " + ("--pilot" if args.pilot else f"--start {start} --end {end} --id <id>"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
