"""Build 1-minute bars and exact volume-at-price products from validated Databento trades,
register the bar file in configs/datasets.yaml, prepare it and write its quality report.

Usage (from research/):
    python scripts/build_market_data.py --pilot      # ES_DB_PILOT and NQ_DB_PILOT, purpose pipeline_check
    python scripts/build_market_data.py --products ES --start 2021-10-01 --end 2026-10-01 --id ES_1m_DB --purpose research

Refuses unless scripts/validate_market_data.py passed for the same raw files. Steps: build
(edgelab/data/databento_build.py) -> register the dataset if it is new -> prepare it (as
prepare_data.py) -> bar-level quality report (as data_quality.py). What is left is printed:
the DATA_QUALITY journal note and, for the ES pilot, scripts/pipeline_check.py. Nothing
here runs a study.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgelab.config import RESEARCH_ROOT, load_instrument, load_research_config, load_session_template  # noqa: E402
from edgelab.data.databento_build import build_product  # noqa: E402
from edgelab.data.databento_client import load_databento_config, resolve  # noqa: E402
from edgelab.data.manifest import PURPOSES, get_entry, load_manifest, load_processed, prepare, register  # noqa: E402
from edgelab.data.quality import check_bars, render_markdown  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_market_data import DQ_DIR, write_report  # noqa: E402

DQ_REPORT = RESEARCH_ROOT / "reports" / "DATA_QUALITY_REPORT.md"
PRIVATE_KEYS = ("spread_points", "atr_d14")  # price differences stay next to the data, out of git


def manifest_row(cfg: dict, product: str, start: str, end: str, dataset_id: str, purpose: str, bars_path: Path) -> dict:
    parent = cfg["products"][product]["parent"]
    return {
        "id": dataset_id, "instrument": product, "path": bars_path.relative_to(RESEARCH_ROOT).as_posix(),
        "source": (f"Databento {cfg['dataset']} trades, parent {parent}, {start} to {end} (end exclusive): outright "
                   "futures only, 1-minute OHLCV of the contract active under the frozen calendar roll of "
                   "configs/instruments.yaml, exchange timestamps, unadjusted; built by scripts/build_market_data.py"),
        "tz_in": "UTC", "label": "open", "bar_minutes": 1, "adjustment": "unadjusted", "on_conflict": "abort",
        "purpose": purpose,
    }


def public_build(summary: dict) -> dict:
    """build.json without price differences (kept beside the data, never committed)."""
    out = json.loads(json.dumps(summary, default=str))
    for g in out.get("session_checks", {}).get("roll_gaps", []):
        for k in PRIVATE_KEYS:
            g.pop(k, None)
    return out


def bar_quality(dataset_id: str) -> dict:
    e = get_entry(dataset_id)
    bars, meta = load_processed(e.id)
    inst = load_instrument(e.instrument)
    rep = check_bars(bars, instrument=e.instrument, source=f"{e.source} (dataset {e.id}, adjustment: {e.adjustment})",
                     template=load_session_template(inst.session_template), bar_minutes=e.bar_minutes,
                     tick_size=inst.tick_size, roll_cfg=inst.roll, meta=meta)
    rep["evidence_class"] = "PROXY" if inst.is_proxy else "CME"
    (DQ_DIR / f"{e.id}.json").write_text(json.dumps(rep, indent=2, default=str))
    reports = [json.loads((DQ_DIR / f"{x.id}.json").read_text()) for x in load_manifest() if (DQ_DIR / f"{x.id}.json").exists()]
    DQ_REPORT.write_text(render_markdown(reports))
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pilot", action="store_true", help="pilot period, ids and purpose from configs/databento.yaml")
    ap.add_argument("--start", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--end", help="YYYY-MM-DD (UTC, exclusive)")
    ap.add_argument("--products", nargs="+", help="default: all products of configs/databento.yaml")
    ap.add_argument("--id", help="dataset id (one product only)")
    ap.add_argument("--purpose", choices=PURPOSES, help="manifest purpose (pilot default: pipeline_check)")
    args = ap.parse_args()

    cfg = load_databento_config()
    pilot = cfg["pilot"]
    start = args.start or (pilot["start"] if args.pilot else None)
    end = args.end or (pilot["end"] if args.pilot else None)
    if not (start and end):
        ap.error("give --start and --end, or --pilot")
    products = args.products or list(cfg["products"])
    if args.id and len(products) != 1:
        ap.error("--id names one dataset: give exactly one product")
    if not args.pilot and not args.id:
        ap.error("give --id for a non-pilot build")
    purpose = args.purpose or (pilot.get("purpose", "pipeline_check") if args.pilot else "research")
    rcfg = load_research_config()
    embargoed = rcfg["markets"]["validation_market"]

    DQ_DIR.mkdir(parents=True, exist_ok=True)
    failed, built = False, []
    for product in products:
        dataset_id = args.id or pilot["dataset_ids"][product]
        inst = load_instrument(product)
        tpl = load_session_template(inst.session_template)
        try:
            s = build_product(cfg, product, start, end, dataset_id=dataset_id, instrument=inst, template=tpl)
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"{product}: REFUSED: {exc}")
            failed = True
            continue
        (DQ_DIR / f"build_{dataset_id}.json").write_text(json.dumps(public_build(s), indent=2))
        print(f"{dataset_id}: {'PASS' if s['passed'] else 'FAIL'}  {s['bars_active']:,} bars, "
              f"{s['trading_dates']} trading dates, "
              f"contracts {', '.join(s['contracts_used'])}, rolls {[r['trading_date'] for r in s['rolls']]}")
        for f in s["fatal"]:
            print(f"   FATAL  {f}")
        if not s["passed"]:
            failed = True
            continue
        bars_path = resolve(cfg["processed_dir"]) / dataset_id / "bars_1m.parquet"
        row = manifest_row(cfg, product, start, end, dataset_id, purpose, bars_path)
        print(f"   manifest: {'registered' if register(row) else 'already registered'} ({purpose})")
        _, meta = prepare(get_entry(dataset_id))
        print(f"   prepared: {meta['rows_processed']:,} rows, sha256 {meta['processed_sha256'][:16]}")
        rep = bar_quality(dataset_id)
        print(f"   bar quality: {rep.get('trading_dates')} trading dates, {rep.get('full_rth_dates')} with full RTH")
        built.append((dataset_id, product))
    write_report()
    print("wrote reports/TRADE_DATA_QUALITY_REPORT.md and reports/DATA_QUALITY_REPORT.md")
    if failed:
        print("STOP: a build failed; nothing downstream may run on it.")
        return 1
    for dataset_id, product in built:
        print(f"\nNext for {dataset_id}:")
        print("  read reports/TRADE_DATA_QUALITY_REPORT.md and reports/DATA_QUALITY_REPORT.md, then")
        print(f"  python scripts/journal_note.py --title 'DATA_QUALITY {dataset_id}' --text-file <how each finding was handled>")
        if purpose == "pipeline_check" and product != embargoed:
            print(f"  python scripts/pipeline_check.py --dataset {dataset_id}")
        elif product == embargoed:
            print(f"  ({product} is embargoed until rule freeze: data only, no study runs on it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
