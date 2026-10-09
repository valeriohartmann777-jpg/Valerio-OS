"""Download raw Databento files (trades + instrument definitions), one file per month.

Usage (from research/, with DATABENTO_API_KEY set in the shell):
    python scripts/databento_download.py --pilot --dry-run          # costs of the pilot, nothing fetched
    python scripts/databento_download.py --pilot                    # ES and NQ, 2024-01-01 -> 2024-04-01
    python scripts/databento_download.py --symbols NQ.FUT ES.FUT --start 2024-01-01 --end 2024-04-01
    python scripts/databento_download.py --start 2021-10-01 --end 2026-10-01 --products ES --confirm-cost 412.50

Before anything is fetched, the cost of every file still missing is estimated with the
metadata API. Above ``safety.max_auto_download_cost_usd`` (configs/databento.yaml,
$20.00) the run aborts unless ``--confirm-cost`` is at least the estimate. Files already
on disk are verified against their SHA-256 and never fetched twice or overwritten.
Each run that fetches data appends an entry to reports/DATA_ACQUISITION_LOG.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgelab.config import RESEARCH_ROOT  # noqa: E402
from edgelab.data.databento_client import (  # noqa: E402
    CostLimitExceeded, MissingApiKey, check_cost, download_chunk, library_versions, load_databento_config, make_client,
    plan_downloads, product_for_parent, request_error,
)

LOG = RESEARCH_ROOT / "reports" / "DATA_ACQUISITION_LOG.md"


def _rel(p: Path) -> str:
    try:
        return p.relative_to(RESEARCH_ROOT).as_posix()
    except ValueError:
        return str(p)


def log_entry(cfg: dict, products: list[str], schemas: list[str], start: str, end: str, metas: list[dict],
              estimate: float, limit: float, confirmed: float | None, failure: str | None) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parents = ", ".join(cfg["products"][p]["parent"] for p in products)
    lines = [
        "", f"## {now} — Databento download ({', '.join(products)}, {start} -> {end})", "",
        f"Request: `{cfg['dataset']}`, schemas {', '.join(schemas)}, parents {parents}, `stype_in={cfg['stype_in']}`, "
        f"{start} -> {end} (end exclusive), one file per calendar month. Script: `scripts/databento_download.py`.",
        f"Estimated cost of the files fetched in this run: ${estimate:,.2f} (safety limit ${limit:,.2f}; "
        + (f"explicitly confirmed up to ${confirmed:,.2f})." if confirmed is not None else "no confirmation needed)."),
        f"Library: {', '.join(f'{k} {v}' for k, v in library_versions().items())}. The API key came from the "
        "environment variable and was not written anywhere.", "",
        "| file | records | size MB | sha256 (first 16) |", "|---|---:|---:|---|",
    ]
    for m in metas:
        lines.append(f"| {m['_path']} | {m['records']:,} | {m['file_bytes'] / 1e6:,.1f} | {m['sha256'][:16]} |")
    if failure:
        lines += ["", f"**The run stopped with an error:** {failure}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pilot", action="store_true", help="use the pilot period of configs/databento.yaml")
    ap.add_argument("--start", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--end", help="YYYY-MM-DD (UTC, exclusive)")
    ap.add_argument("--symbols", nargs="+", help="parent symbols, e.g. NQ.FUT ES.FUT (instead of --products)")
    ap.add_argument("--products", nargs="+", help="product keys of configs/databento.yaml (default: all)")
    ap.add_argument("--schemas", nargs="+", help="default: definition and trades")
    ap.add_argument("--confirm-cost", type=float, help="accept an estimate above the safety limit, up to this amount (USD)")
    ap.add_argument("--dry-run", action="store_true", help="estimate and stop")
    args = ap.parse_args()

    cfg = load_databento_config()
    start = args.start or (cfg["pilot"]["start"] if args.pilot else None)
    end = args.end or (cfg["pilot"]["end"] if args.pilot else None)
    if not (start and end):
        ap.error("give --start and --end, or --pilot")
    if args.symbols and args.products:
        ap.error("give --symbols or --products, not both")
    try:
        from_symbols = [product_for_parent(cfg, s) for s in args.symbols] if args.symbols else None
    except KeyError as exc:
        ap.error(str(exc))
    products = from_symbols or args.products or list(cfg["products"])
    unknown = [p for p in products if p not in cfg["products"]]
    if unknown:
        ap.error(f"unknown products {unknown}; configured: {list(cfg['products'])}")
    schemas = args.schemas or [cfg["definition_schema"], cfg["schema"]]
    limit = float(cfg["safety"]["max_auto_download_cost_usd"])

    try:
        client = make_client()
    except MissingApiKey as exc:
        print(exc)
        return 2
    try:
        tasks = plan_downloads(client, cfg, products, schemas, start, end)
    except Exception as exc:  # network, authentication, request validation
        print(f"Cost estimate failed, nothing downloaded: {request_error(exc)}")
        return 1

    print(f"{'product':8s} {'schema':11s} {'period':25s} {'status':9s} {'est. cost':>10s} {'billable MB':>12s}")
    for t in tasks:
        r = t.request
        status = "on disk" if t.exists else "missing"
        print(f"{t.product:8s} {r.schema:11s} {r.start + ' -> ' + r.end:25s} {status:9s} "
              f"{'' if t.exists else f'${t.cost_usd:,.2f}':>10s} {'' if t.exists else f'{t.billable_bytes / 1e6:,.1f}':>12s}")
    missing = [t for t in tasks if not t.exists]
    total = sum(t.cost_usd for t in missing)
    print(f"\nfiles missing: {len(missing)} of {len(tasks)}; estimated cost of the missing files: ${total:,.2f} "
          f"(safety limit ${limit:,.2f})")
    if not missing:
        print("All files are on disk and match their SHA-256. Nothing to download.")
        return 0
    try:
        check_cost(total, limit, args.confirm_cost)
    except CostLimitExceeded as exc:
        print(exc)
        print(f"To proceed: re-run with --confirm-cost {total:.2f} (or more), or change "
              "safety.max_auto_download_cost_usd in configs/databento.yaml.")
        return 3
    if args.dry_run:
        print("--dry-run: nothing downloaded.")
        return 0

    metas, failure = [], None
    for t in missing:
        try:
            meta = download_chunk(client, t)
        except Exception as exc:
            failure = f"{_rel(t.path)}: {request_error(exc)}"
            print(f"Download failed: {failure}")
            break
        meta["_path"] = _rel(t.path)
        metas.append(meta)
        print(f"  {meta['_path']}: {meta['records']:,} records, {meta['file_bytes'] / 1e6:,.1f} MB")
    if metas or failure:
        fetched = sum(t.cost_usd for t in missing[: len(metas)])
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(log_entry(cfg, products, schemas, start, end, metas, fetched, limit,
                               args.confirm_cost if total > limit else None, failure))
        print(f"logged in {_rel(LOG)}")
    if failure:
        return 1
    print("Next: python scripts/validate_market_data.py " + ("--pilot" if args.pilot else f"--start {start} --end {end}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
