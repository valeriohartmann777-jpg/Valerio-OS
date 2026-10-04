"""Lets JARVIS answer "what have you learned?" from its learning journal.

Read-only. The full record — including results on the holdout period, which
the research loop itself never sees — is for the user.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel

from jarvis.core.trace import TraceContext
from jarvis.learning.journal import LearningJournal
from jarvis.learning.service import LearningStatus
from jarvis.permissions.models import PermissionLevel
from jarvis.tools.base import Tool, ToolResult
from jarvis.tools.registry import ToolRegistry


class NoArgs(BaseModel):
    pass


_STATS = ("trades", "win_rate", "avg_r", "t_stat", "profit_factor", "max_drawdown_r")


def _brief(stats: dict[str, Any] | None) -> dict[str, Any] | None:
    if not stats:
        return None
    return {k: round(v, 3) if isinstance(v, float) else v for k, v in stats.items() if k in _STATS}


class LearningReportTool(Tool[NoArgs]):
    name = "learning_report"
    description = (
        "What JARVIS has learned on its own about scalping and day trading NQ and XAUUSD: "
        "learning status and budget, knowledge notes, support/resistance level studies "
        "(how often levels held vs. random prices, in-sample), validated findings (with "
        "their out-of-sample and holdout results) and recent rounds and tests."
    )
    permission_level = PermissionLevel.READ
    input_model = NoArgs
    # Notes and study names come from the research model, which reads the web.
    returns_untrusted_text = True

    def __init__(self, journal: LearningJournal, status: Callable[[], LearningStatus]) -> None:
        self._journal = journal
        self._status = status

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        status = self._status()
        counts = status.counts
        validated = await self._journal.tests(limit=20, status="validated")
        recent = await self._journal.tests(limit=10)
        rounds = await self._journal.rounds(limit=5)
        studies = await self._journal.studies(limit=15)
        summary = (
            f"Learning is {status.state}; {counts.get('rounds', 0)} rounds, "
            f"{counts.get('tests', 0)} backtests, {counts.get('validated', 0)} validated "
            f"({counts.get('confirmed', 0)} confirmed on the holdout), "
            f"{counts.get('notes', 0)} notes."
        )
        data: dict[str, Any] = {
            "status": {
                k: v
                for k, v in asdict(status).items()
                if k not in ("data", "counts", "progress", "focus")
            },
            "counts": counts,
            "notes": [
                {"note": f"N{n['number']}", "topic": n["topic"], "text": n["text"]}
                for n in await self._journal.notes(limit=60)
            ],
            "validated": [
                {
                    "test": f"T{t['number']}",
                    "name": t["name"],
                    "market": f"{t['instrument']} {t['style']} {t['timeframe']}",
                    "hypothesis": (t["spec"] or {}).get("hypothesis"),
                    "in_sample": _brief(t["in_sample"]),
                    "out_of_sample": _brief(t["out_of_sample"]),
                    "holdout": _brief(t["holdout"]),
                    "confirmed_on_holdout": t["holdout_confirmed"],
                }
                for t in validated
            ],
            "recent_tests": [
                {
                    "test": f"T{t['number']}",
                    "name": t["name"],
                    "result": t["status"],
                    "why": t["reason"],
                }
                for t in recent
            ],
            "level_studies": [
                {
                    "study": f"S{st['number']}",
                    "name": st["name"],
                    "setup": {
                        k: (st["spec"] or {}).get(k)
                        for k in ("instrument", "timeframe", "side", "level", "when")
                    },
                    "result": {
                        k: (st["result"] or {}).get(k)
                        for k in (
                            "touches",
                            "held_rate",
                            "expected_rate",
                            "edge_z",
                            "by_touch",
                            "avg_favourable_points",
                            "avg_adverse_points",
                        )
                    },
                }
                for st in studies
                if st["ok"]
            ],
            "research_focus": status.focus,
            "recent_rounds": [
                {"round": r["number"], "status": r["status"], "summary": r["summary"]}
                for r in rounds
            ],
        }
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            summary=summary,
            observed_result=summary,
            data=data,
        )


def register_learning_tools(
    registry: ToolRegistry, journal: LearningJournal, status: Callable[[], LearningStatus]
) -> None:
    registry.register(LearningReportTool(journal, status))
