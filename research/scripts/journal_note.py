"""Append a dated note to journal/RESEARCH_JOURNAL.md.

Usage (from research/):
    python scripts/journal_note.py --title "DATA_QUALITY ES_1m" --text-file notes/dq_es.md
    python scripts/journal_note.py --title "DATA_QUALITY ES_1m" --text "Read the report: ..."

The event-study runner requires a note titled "DATA_QUALITY <dataset id>" that records
how the findings of reports/DATA_QUALITY_REPORT.md were handled (RESEARCH_PROTOCOL.md 2.1).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgelab import journal  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--text-file", type=Path)
    args = ap.parse_args()
    text = args.text if args.text is not None else args.text_file.read_text(encoding="utf-8")
    if len(text.strip()) < 40:
        print("refusing a near-empty note: write what was checked and what was decided")
        return 1
    journal.note(args.title, text)
    print(f"note '{args.title}' appended to {journal.JOURNAL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
