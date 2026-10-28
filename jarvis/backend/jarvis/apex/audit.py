"""SENTINEL's independent re-check of a stored APEX analysis.

1. **Same data**: the bars before the analysis moment hash to the same value as then (a
   difference means the source revised its history — reported, then compared anyway).
2. **No look-ahead**: the analysis is recomputed from the data on disk now, which by now may
   include bars after the moment; zones, structure, rates and candidates must be identical.
3. **Outcomes**: every journaled candidate of the analysis is re-judged on the latest bars;
   a final outcome must come out the same as recorded.
"""

from __future__ import annotations

from typing import Any

COMPARED = (
    "quality", "price", "atr15", "structure", "zones", "scenarios", "rejected", "chart",
    "execution",
)  # fmt: skip
CANDIDATE_FIELDS = ("id", "direction", "entry", "stop", "targets", "valid_until", "risk_points")
# What each strategy decided (its plans are compared through CANDIDATE_FIELDS; the track record
# and journal fields attached to them later are not part of the decision).
DECISION_FIELDS = ("version", "status", "direction", "setups", "trigger", "reasons")


def core(analysis: dict[str, Any]) -> dict[str, Any]:
    out = {k: analysis.get(k) for k in COMPARED}
    out["candidates"] = [
        {k: c.get(k) for k in CANDIDATE_FIELDS} for c in analysis.get("candidates", [])
    ]
    out["strategies"] = {
        name: {k: d.get(k) for k in DECISION_FIELDS} | {"plans": [c["id"] for c in d["candidates"]]}
        for name, d in (analysis.get("strategies") or {}).items()
    }
    return out


def compare(stored: dict[str, Any], recomputed: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []
    if stored.get("engine") != recomputed.get("engine"):
        checks.append(
            {
                "code": "ENGINE",
                "passed": False,
                "detail": f"Made with {stored.get('engine')}, recomputed with "
                f"{recomputed.get('engine')}: a difference below can come from the engine "
                "change, not from look-ahead. Analyse again to check this version.",
            }
        )
    same_data = stored["data"]["sha256"] == recomputed["data"]["sha256"]
    checks.append(
        {
            "code": "SAME_DATA",
            "passed": same_data,
            "detail": "The bars before the moment are byte-identical to those the analysis used."
            if same_data
            else "The source changed bars before the moment since the analysis (revised history).",
        }
    )
    a, b = core(stored), core(recomputed)
    differing = [k for k in a if a[k] != b[k]]
    checks.append(
        {
            "code": "NO_LOOKAHEAD",
            "passed": not differing,
            "detail": "Recomputed with the data available now: identical zones, structure, rates "
            "and candidates."
            if not differing
            else "Differs in: " + ", ".join(differing),
        }
    )
    return checks


def outcome_check(
    recorded: list[dict[str, Any]], rejudged: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Final outcomes must be reproduced exactly; open ones may have moved on."""
    mismatches = []
    finals = 0
    for c in recorded:
        old = c.get("outcome") or {}
        new = rejudged.get(c["id"])
        if not old.get("final") or new is None:
            continue
        finals += 1
        if (old.get("state"), old.get("r")) != (new.get("state"), new.get("r")):
            mismatches.append(c["id"])
    return {
        "code": "OUTCOMES",
        "passed": not mismatches,
        "detail": f"{finals} final outcome(s) re-judged identically."
        if not mismatches
        else f"Different on re-judging: {', '.join(mismatches)}",
    }
