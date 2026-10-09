"""quantlab_report: the latest QuantLab experiments, read critically."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from jarvis.core.trace import TraceContext
from jarvis.permissions.models import PermissionLevel
from jarvis.quantlab.service import QuantLabService
from jarvis.tools.base import Tool, ToolResult
from jarvis.tools.registry import ToolRegistry


class NoArgs(BaseModel):
    pass


class QuantLabReportTool(Tool[NoArgs]):
    name = "quantlab_report"
    description = (
        "QuantLab research results: the latest completed experiments (strategy, data, "
        "verdict INVALID/FAILED/INCONCLUSIVE), what was observed in train vs out-of-sample "
        "after costs, what can't be concluded, and the next pre-registered test. Synthetic "
        "test data is labelled. Read-only; QuantLab never trades."
    )
    permission_level = PermissionLevel.READ
    input_model = NoArgs
    # Strategy names and hypotheses are the user's own text, but treat them as data.
    returns_untrusted_text = True

    def __init__(self, service: Callable[[], QuantLabService]) -> None:
        self._service = service

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        lab = self._service()
        text = await lab.report_text()
        overview = await lab.overview()
        summary = (
            f"QuantLab: {overview['counts']['experiments']} experiment(s), "
            f"{overview['counts']['datasets']} dataset(s)"
        )
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            summary=summary,
            observed_result=summary,
            data={"report": text, "counts": overview["counts"], "scope": overview["scope"]},
        )


def register_quantlab_tools(registry: ToolRegistry, service: Callable[[], QuantLabService]) -> None:
    registry.register(QuantLabReportTool(service))
