"""level_odds: what JARVIS's own trained model says about the levels near the price."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from jarvis.core.trace import TraceContext
from jarvis.learning.market import DataError
from jarvis.permissions.models import PermissionLevel
from jarvis.tools.base import Tool, ToolError, ToolResult
from jarvis.tools.registry import ToolRegistry
from jarvis.training.service import TrainingService


class OddsArgs(BaseModel):
    instrument: Literal["NQ", "XAUUSD"] = Field(description="NQ or XAUUSD (gold)")


class LevelOddsTool(Tool[OddsArgs]):
    name = "level_odds"
    description = (
        "Right now, for the nearest support and resistance levels of NQ or XAUUSD (gold): "
        "the probability that each level holds when a 5-minute candle touches it, from the "
        "model JARVIS trained itself on years of level touches. Gives the model's number only "
        "where it has proven better than a simple baseline on months it never saw; otherwise "
        "only the baseline (how often such levels held in the past). Works during the session "
        "window the model was trained on (NQ 09:30-16:00, gold 03:00-13:30 New York time). "
        "Statistics, not trade advice."
    )
    permission_level = PermissionLevel.READ
    input_model = OddsArgs

    def __init__(self, service: Callable[[], TrainingService]) -> None:
        self._service = service

    async def execute(self, args: OddsArgs, ctx: TraceContext) -> ToolResult:
        try:
            odds = await self._service().odds(args.instrument)
        except (DataError, ValueError) as exc:
            return ToolResult.failure(
                self.name,
                ToolError(
                    code="no_data",
                    message=f"I couldn't work out the level odds ({exc}).",
                    suggestion="Try again in a few minutes.",
                ),
            )
        source = "the trained model" if odds["model_used"] else "the baseline only"
        summary = f"{args.instrument} level odds ({odds['session']} session, {source})"
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            summary=summary,
            observed_result=summary,
            data=odds,
        )


def register_training_tools(registry: ToolRegistry, service: Callable[[], TrainingService]) -> None:
    registry.register(LevelOddsTool(service))
