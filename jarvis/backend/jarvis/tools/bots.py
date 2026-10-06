"""bot_report: how the user's MetaTrader EAs did in JARVIS's Bot Lab."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel

from jarvis.bots.service import BotLabService
from jarvis.core.trace import TraceContext
from jarvis.permissions.models import PermissionLevel
from jarvis.tools.base import Tool, ToolResult
from jarvis.tools.registry import ToolRegistry

_HEADLINE = (
    "trades",
    "net",
    "profit_factor",
    "win_rate",
    "return_pct",
    "t_stat",
    "max_drawdown_pct",
    "max_daily_loss_pct",
    "median_month_pct",
    "worst_month_pct",
)


class NoArgs(BaseModel):
    pass


def _headline(stats: dict[str, Any] | None) -> dict[str, Any] | None:
    return {k: stats.get(k) for k in _HEADLINE} if stats else None


class BotReportTool(Tool[NoArgs]):
    name = "bot_report"
    description = (
        "The user's MetaTrader 5 Expert Advisors in JARVIS's Bot Lab: setup state, each EA's "
        "versions and backtests (in-sample, out-of-sample, holdout), which versions were "
        "validated, whether the holdout confirmed them, the prop-firm check, and what account "
        "size a $10k month would need. Read-only."
    )
    permission_level = PermissionLevel.READ
    input_model = NoArgs
    # Version titles and notes come from the research model.
    returns_untrusted_text = True

    def __init__(self, service: Callable[[], BotLabService]) -> None:
        self._service = service

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        lab = self._service()
        status = await asyncio.to_thread(lab.status)
        bots = []
        for expert in status.experts:
            detail = await lab.detail(expert["name"])
            if detail is None:
                bots.append({"name": expert["name"], "imported": False})
                continue
            bots.append(
                {
                    "name": detail["name"],
                    "settings": detail["settings"],
                    "versions": [
                        {k: v[k] for k in ("number", "parent", "title", "hypothesis", "compiled")}
                        for v in detail["versions"]
                    ],
                    "backtests": [
                        {
                            "test": t["number"],
                            "version": t["version"],
                            "status": t["status"],
                            "error": t["error"],
                            "in_sample": _headline(t["in_sample"]),
                            "out_of_sample": _headline(t["out_of_sample"]),
                            "holdout": _headline(t["holdout"]),
                            "validated": t["validated"],
                            "holdout_confirmed": t["holdout_confirmed"],
                        }
                        for t in detail["tests"][:10]
                    ],
                    "best_test": detail["best_test"],
                    "ten_k_projection": detail["projection"],
                    "notes": [n["text"] for n in detail["notes"]][-10:],
                }
            )
        summary = (
            f"Bot Lab: {len(status.experts)} EA(s) found, test terminal "
            + ("ready" if status.ready else "not set up")
            + (f", improving {status.improving}" if status.improving else "")
        )
        data = {
            "lab": {k: v for k, v in asdict(status).items() if k not in ("experts",)},
            "bots": bots,
        }
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            summary=summary,
            observed_result=summary,
            data=data,
        )


def register_bot_tools(registry: ToolRegistry, service: Callable[[], BotLabService]) -> None:
    registry.register(BotReportTool(service))
