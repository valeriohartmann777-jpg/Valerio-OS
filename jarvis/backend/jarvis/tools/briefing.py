"""market_levels: today's key levels for NQ / gold, on request in the chat."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from jarvis.briefing.service import BriefingService
from jarvis.core.trace import TraceContext
from jarvis.permissions.models import PermissionLevel
from jarvis.tools.base import Tool, ToolError, ToolResult
from jarvis.tools.registry import ToolRegistry


class LevelsArgs(BaseModel):
    instrument: Literal["NQ", "XAUUSD"] | None = Field(
        None, description="One market, or leave empty for both"
    )


class MarketLevelsTool(Tool[LevelsArgs]):
    name = "market_levels"
    description = (
        "Today's key support and resistance levels for NQ and/or XAUUSD (gold) from current "
        "minute data: previous day, overnight/Asia range, previous week, volume profile, "
        "intact swing highs/lows, round numbers — each with what JARVIS's own level studies "
        "measured. The same content as the morning briefing. Statistics, not trade advice."
    )
    permission_level = PermissionLevel.READ
    input_model = LevelsArgs
    # Study names and notes come from the research model, which reads the web.
    returns_untrusted_text = True

    def __init__(self, service: Callable[[], BriefingService]) -> None:
        self._service = service

    async def execute(self, args: LevelsArgs, ctx: TraceContext) -> ToolResult:
        briefing = await self._service().build(args.instrument)
        if not briefing.maps:
            reasons = "; ".join(f"{k}: {v}" for k, v in briefing.errors.items()) or "no data"
            return ToolResult.failure(
                self.name,
                ToolError(
                    code="no_data",
                    message=f"I couldn't get current market data ({reasons}).",
                    suggestion="Try again in a few minutes.",
                ),
            )
        summary = f"Levels for {', '.join(briefing.maps)}"
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            summary=summary,
            observed_result=summary,
            data={
                "text": briefing.text,
                "levels": {name: m.as_dict() for name, m in briefing.maps.items()},
                "errors": briefing.errors,
            },
        )


def register_briefing_tools(registry: ToolRegistry, service: Callable[[], BriefingService]) -> None:
    registry.register(MarketLevelsTool(service))
