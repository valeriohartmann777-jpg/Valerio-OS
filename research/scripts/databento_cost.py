"""Databento cost and size estimates from the metadata API. Never downloads, never bills.

Usage (from research/, with DATABENTO_API_KEY set in the shell):
    python scripts/databento_cost.py --start 2024-01-01 --end 2024-04-01 --symbols NQ.FUT ES.FUT
    python scripts/databento_cost.py --table      # full-history options -> reports/DATABENTO_COST_ESTIMATES.md

``--end`` is exclusive. The trade files need the instrument definitions of the same
period to map instrument ids to contracts and to drop spreads; their (small) cost is
shown separately. Sizes are Databento's billable sizes (uncompressed DBN); the zstd
files on disk are several times smaller.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from edgelab.config import RESEARCH_ROOT  # noqa: E402
from edgelab.data.databento_client import (  # noqa: E402
    MissingApiKey, Request, dataset_range, estimate, load_databento_config, make_client, parse_parent_symbol, request_error,
)

REPORT = RESEARCH_ROOT / "reports" / "DATABENTO_COST_ESTIMATES.md"
GB = 1e9


def fmt_usd(x: float) -> str:
    return f"${x:,.2f}"


def fmt_gb(b: float) -> str:
    return f"{b / GB:,.2f} GB"


def symbol_costs(client, cfg: dict, symbols: list[str], schema: str, start: str, end: str) -> dict[str, dict]:
    return {s: estimate(client, Request(cfg["dataset"], schema, (s,), cfg["stype_in"], start, end)) for s in symbols}


def single_period(client, cfg: dict, symbols: list[str], schema: str, start: str, end: str) -> None:
    trades = symbol_costs(client, cfg, symbols, schema, start, end)
    defs = symbol_costs(client, cfg, symbols, cfg["definition_schema"], start, end)
    limit = float(cfg["safety"]["max_auto_download_cost_usd"])
    print("Databento cost estimate (metadata API; nothing downloaded, nothing billed)")
    print(f"dataset {cfg['dataset']}   schema {schema}   stype_in {cfg['stype_in']}   period {start} -> {end} (end exclusive)")
    print()
    print(f"{'symbol':10s} {'cost':>12s} {'billable size':>16s}")
    for s, e in trades.items():
        print(f"{s:10s} {fmt_usd(e['cost_usd']):>12s} {fmt_gb(e['billable_bytes']):>16s}")
    tot = sum(e["cost_usd"] for e in trades.values())
    size = sum(e["billable_bytes"] for e in trades.values())
    print(f"{'total':10s} {fmt_usd(tot):>12s} {fmt_gb(size):>16s}")
    dtot = sum(e["cost_usd"] for e in defs.values())
    print()
    print(f"schema {cfg['definition_schema']} for the same symbols and period (contract mapping): {fmt_usd(dtot)}")
    grand = tot + dtot
    verdict = "within" if grand <= limit else "ABOVE"
    print(f"download total {fmt_usd(grand)}: {verdict} the automatic-download limit of {fmt_usd(limit)} "
          "(configs/databento.yaml safety.max_auto_download_cost_usd)")


def periods(first: pd.Timestamp, last: pd.Timestamp) -> list[tuple[str, str, str]]:
    end = last.tz_convert("UTC").normalize()
    out = []
    for label, years in (("1 year", 1), ("3 years", 3), ("5 years", 5)):
        out.append((label, (end - pd.DateOffset(years=years)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
    out.append(("since 2020", "2020-01-01", end.strftime("%Y-%m-%d")))
    out.append(("max (dataset start)", first.tz_convert("UTC").normalize().strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
    return out


def cost_table(client, cfg: dict) -> str:
    first, last = dataset_range(client, cfg["dataset"])
    prods = list(cfg["products"])  # ES, NQ
    order = [p for p in ("NQ", "ES") if p in prods] + [p for p in prods if p not in ("NQ", "ES")]
    parents = {p: cfg["products"][p]["parent"] for p in order}
    lines = [
        "# DATABENTO_COST_ESTIMATES",
        "",
        f"Generated {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `scripts/databento_cost.py --table` "
        "from Databento's metadata API (list prices for this account; nothing was downloaded or billed).",
        f"Dataset `{cfg['dataset']}`, `stype_in={cfg['stype_in']}`, symbols {', '.join(parents.values())}. "
        f"Available range: {first.strftime('%Y-%m-%d')} to {last.strftime('%Y-%m-%d %H:%M UTC')}. "
        "Periods end on the last available date (exclusive). Sizes are billable (uncompressed DBN); "
        "the zstd files on disk are several times smaller.",
        "",
        "No download happens until a period is chosen explicitly (`scripts/databento_download.py --start ... --end ... "
        "--confirm-cost <amount>`).",
        "",
    ]
    rows = periods(first, last)
    schemas = [(cfg["schema"], "needed for exact volume at price (POC/VAH/VAL from trades)")]
    schemas += [(s, "enough for the frozen 52 studies, which use 1-minute OHLCV")
                for s in cfg.get("cost_table_schemas", []) if s != cfg["schema"]]
    for schema, note in schemas:
        lines += [f"## `{schema}` + `{cfg['definition_schema']}` ({note})", "",
                  "| Period | Start | End | " + " | ".join(f"{p} Cost" for p in order) + " | Total | Estimated Size |",
                  "|---|---|---|" + "---:|" * (len(order) + 2)]
        for label, a, b in rows:
            costs, size = {}, 0.0
            for p in order:
                main = estimate(client, Request(cfg["dataset"], schema, (parents[p],), cfg["stype_in"], a, b))
                dfn = estimate(client, Request(cfg["dataset"], cfg["definition_schema"], (parents[p],), cfg["stype_in"], a, b))
                costs[p] = main["cost_usd"] + dfn["cost_usd"]
                size += main["billable_bytes"] + dfn["billable_bytes"]
            lines.append(f"| {label} | {a} | {b} | " + " | ".join(fmt_usd(costs[p]) for p in order)
                         + f" | {fmt_usd(sum(costs.values()))} | {fmt_gb(size)} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--end", help="YYYY-MM-DD (UTC, exclusive)")
    ap.add_argument("--symbols", nargs="+", help="parent symbols, e.g. NQ.FUT ES.FUT (default: configs/databento.yaml)")
    ap.add_argument("--schema", help="default: configs/databento.yaml schema (trades)")
    ap.add_argument("--table", action="store_true", help="estimate full-history options and write the report")
    ap.add_argument("--out", type=Path, default=REPORT)
    args = ap.parse_args()

    cfg = load_databento_config()
    if not args.table and not (args.start and args.end):
        ap.error("give --start and --end, or --table")
    symbols = args.symbols or [p["parent"] for p in cfg["products"].values()]
    for s in symbols:
        parse_parent_symbol(s)
    try:
        client = make_client()
    except MissingApiKey as exc:
        print(exc)
        return 2
    try:
        if args.table:
            text = cost_table(client, cfg)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text + "\n")
            print(text)
            print(f"\nwrote {args.out}")
        else:
            Request(cfg["dataset"], args.schema or cfg["schema"], tuple(symbols), cfg["stype_in"], args.start, args.end)
            single_period(client, cfg, symbols, args.schema or cfg["schema"], args.start, args.end)
    except Exception as exc:  # network, authentication, validation errors from the API
        print(f"Databento request failed: {request_error(exc)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
