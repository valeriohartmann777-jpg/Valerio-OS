"""Pre-register every event study of configs/event_studies.yaml in the research journal.

Usage (from research/):
    python scripts/preregister_studies.py

Writes, once, the note "PRE-DATA CLARIFICATIONS" (RESEARCH_PROTOCOL.md section 13) and one
entry per study: hypothesis, detector definition, parameters, primary test, expected
outcome. Studies that are already registered are skipped, never rewritten. Run it BEFORE
any market data is loaded; git history then proves the expectations came first.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgelab import journal  # noqa: E402
from edgelab.config import RESEARCH_ROOT  # noqa: E402
from edgelab.studies.runner import load_config, preregister_all  # noqa: E402

CLARIFICATIONS = "PRE-DATA CLARIFICATIONS"


def protocol_section(title_prefix: str = "## 13.") -> str:
    text = (RESEARCH_ROOT / "RESEARCH_PROTOCOL.md").read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(title_prefix)}.*?(?=^## |\Z)", text, flags=re.M | re.S)
    if not m:
        raise SystemExit("RESEARCH_PROTOCOL.md has no section 13 (pre-data clarifications)")
    head, _, body = m.group(0).strip().partition("\n")
    return f"**{head.lstrip('# ').strip()}**\n{body}"  # no markdown heading inside a journal note


def main() -> int:
    cfg = load_config()
    if not journal.has_note(CLARIFICATIONS):
        journal.note(f"{CLARIFICATIONS} (RESEARCH_PROTOCOL.md section 13)", protocol_section())
        print(f"wrote note {CLARIFICATIONS}")
    done = preregister_all(cfg)
    print(f"pre-registered {len(done)} studies, {len(cfg['studies']) - len(done)} were already registered")
    print(f"journal: {journal.JOURNAL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
