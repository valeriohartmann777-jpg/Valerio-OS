"""Convert registered raw files into canonical parquet bars.

Usage (from research/):
    python scripts/prepare_data.py --inspect data/raw/ES_1m.csv   # look before registering
    python scripts/prepare_data.py                                # every dataset in the manifest
    python scripts/prepare_data.py --id ES_1m                     # one dataset

``--inspect`` prints the raw head, the detected columns and the modal bar spacing so
the manifest entry (timezone, open/close label) can be written from evidence.
Preparation never fills gaps, never removes outliers and never adjusts rolls.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgelab.data.loader import infer_bar_minutes, normalize_columns, parse_timestamps, read_table  # noqa: E402
from edgelab.data.manifest import get_entry, load_manifest, prepare  # noqa: E402


def inspect(path: Path, tz_in: str) -> None:
    with open(path, "rb") as fh:
        head = fh.read(800).decode("utf-8", errors="replace")
    print("--- raw head ---")
    print(head)
    raw = read_table(path)
    print("--- columns ---")
    print(list(raw.columns))
    df = normalize_columns(raw)
    print("--- normalised ---")
    print(list(df.columns))
    print(f"rows: {len(df)}")
    try:
        idx = parse_timestamps(df, tz_in=tz_in)
        print(f"first ts: {idx.min()}  last ts: {idx.max()}  (naive values read as {tz_in})")
        print(f"modal spacing: {infer_bar_minutes(idx.sort_values())} min")
        print("first timestamps per day (tells you the session start in the file's clock):")
        s = idx.sort_values()
        firsts = s.to_series().groupby(s.normalize()).min().head(5)
        for t in firsts:
            print("  ", t)
    except Exception as exc:  # inspection must never crash on odd files
        print(f"timestamp parsing failed: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inspect", type=Path, help="print what a raw file looks like and exit")
    ap.add_argument("--tz-in", default="UTC", help="timezone assumed for naive timestamps during --inspect")
    ap.add_argument("--id", help="prepare only this dataset id")
    args = ap.parse_args()

    if args.inspect:
        inspect(args.inspect, args.tz_in)
        return 0
    entries = [get_entry(args.id)] if args.id else load_manifest()
    if not entries:
        print("No datasets registered in configs/datasets.yaml. Nothing to prepare.")
        return 1
    for e in entries:
        pq, meta = prepare(e)
        print(f"{e.id}: {meta['rows_read']} rows read -> {meta['rows_processed']} rows -> {pq}")
        print(f"   identical duplicates dropped: {meta['identical_duplicates_dropped']}, "
              f"conflicting duplicate timestamps: {meta['conflicting_duplicate_timestamps']}")
        if "warning_bar_minutes" in meta:
            print(f"   WARNING: {meta['warning_bar_minutes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
