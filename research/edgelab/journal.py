"""Append-only research journal with pre-registration.

``preregister`` writes the hypothesis, rules, parameters, dataset and the EXPECTED
outcome before an experiment runs. ``record_result`` refuses to write a result for
an experiment that was never pre-registered, and inserts the result under the
existing entry without touching the pre-registered text, so git history proves the
expectation came first.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .config import RESEARCH_ROOT

JOURNAL = RESEARCH_ROOT / "journal" / "RESEARCH_JOURNAL.md"
DECISIONS = ("REJECT", "KEEP", "MODIFY", "VALIDATE")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _header(exp_id: str) -> str:
    return f"## {exp_id} "


def is_preregistered(exp_id: str, path: Path = JOURNAL) -> bool:
    return path.exists() and any(line.startswith(_header(exp_id)) for line in path.read_text(encoding="utf-8").splitlines())


def preregister(
    exp_id: str,
    title: str,
    *,
    hypothesis: str,
    reason: str,
    rules: str,
    parameters: str,
    dataset: str,
    expected: str,
    path: Path = JOURNAL,
) -> None:
    """Append a pre-registration block; refuses duplicates."""
    if is_preregistered(exp_id, path):
        raise ValueError(f"{exp_id} already pre-registered")
    block = (
        f"\n{_header(exp_id)}— {title}\n\n"
        f"- **Pre-registered (UTC):** {_now()}\n"
        f"- **Hypothesis:** {hypothesis}\n"
        f"- **Reason for test:** {reason}\n"
        f"- **Rules:** {rules}\n"
        f"- **Parameters:** {parameters}\n"
        f"- **Dataset:** {dataset}\n"
        f"- **Expected outcome BEFORE running:** {expected}\n"
        f"\n<!-- results:{exp_id} -->\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(block)


def record_result(exp_id: str, *, actual: str, interpretation: str, decision: str, path: Path = JOURNAL) -> None:
    """Insert a dated result under a pre-registered entry."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if not is_preregistered(exp_id, path):
        raise PermissionError(f"{exp_id} was not pre-registered; write the expectation first")
    text = path.read_text(encoding="utf-8")
    marker = f"<!-- results:{exp_id} -->"
    block = (
        f"- **Result recorded (UTC):** {_now()}\n"
        f"- **Actual result:** {actual}\n"
        f"- **Interpretation:** {interpretation}\n"
        f"- **Decision:** {decision}\n"
    )
    if marker not in text:
        raise RuntimeError(f"results marker for {exp_id} missing; journal was edited by hand")
    text = text.replace(marker, block + "\n" + marker, 1)
    path.write_text(text, encoding="utf-8")


def note(title: str, text: str, path: Path = JOURNAL) -> None:
    """Append a dated free-text note (protocol clarification, data finding, re-run reason).

    Notes never count as pre-registrations and never carry results."""
    if not title.strip() or "\n" in title:
        raise ValueError("note title must be one non-empty line")
    block = f"\n### NOTE {_now()} — {title.strip()}\n\n{text.strip()}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(block)


def has_note(title_prefix: str, path: Path = JOURNAL) -> bool:
    """True if a note whose title starts with ``title_prefix`` exists."""
    if not path.exists():
        return False
    pat = re.compile(r"^### NOTE \S+ — " + re.escape(title_prefix), flags=re.M)
    return bool(pat.search(path.read_text(encoding="utf-8")))


def has_result(exp_id: str, path: Path = JOURNAL) -> bool:
    """True if at least one result was recorded under the entry ``exp_id``."""
    if not is_preregistered(exp_id, path):
        return False
    text = path.read_text(encoding="utf-8")
    start = re.search(rf"^{re.escape(_header(exp_id))}", text, flags=re.M).start()
    end = text.index(f"<!-- results:{exp_id} -->", start)
    return "**Result recorded (UTC):**" in text[start:end]


def entries(path: Path = JOURNAL) -> list[str]:
    """IDs of all journal entries in order."""
    if not path.exists():
        return []
    return re.findall(r"^## ([A-Z]\d{3}[A-Za-z0-9_.-]*) ", path.read_text(encoding="utf-8"), flags=re.M)
