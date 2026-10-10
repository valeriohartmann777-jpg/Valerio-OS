"""quantlab_report: the latest QuantLab experiments, read critically."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from jarvis.core.trace import TraceContext
from jarvis.permissions.models import PermissionLevel
from jarvis.quantlab.futures.service import ResearchService
from jarvis.quantlab.service import QuantLabService
from jarvis.tools.base import Tool, ToolResult
from jarvis.tools.registry import ToolRegistry


class NoArgs(BaseModel):
    pass


class QuantLabReportTool(Tool[NoArgs]):
    name = "quantlab_report"
    description = (
        "QuantLab research results: the latest futures research runs (NQ/MNQ/ES/MES strategy "
        "versions on verified Databento datasets: verdict, data fitness, out-of-sample trades "
        "and net after costs) and the cash-equity reference experiments (verdict "
        "INVALID/FAILED/INCONCLUSIVE). All numbers come from the deterministic engine; "
        "synthetic test data is labelled. Read-only — it can't buy data or trade."
    )
    permission_level = PermissionLevel.READ
    input_model = NoArgs
    # Strategy names and hypotheses are the user's own text, but treat them as data.
    returns_untrusted_text = True

    def __init__(
        self,
        service: Callable[[], QuantLabService],
        research: Callable[[], ResearchService] | None = None,
    ) -> None:
        self._service = service
        self._research = research

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        lab = self._service()
        text = await lab.report_text()
        if self._research is not None:
            text = await self._research().report_text() + "\n\n" + text
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


def register_quantlab_tools(
    registry: ToolRegistry,
    service: Callable[[], QuantLabService],
    research: Callable[[], ResearchService] | None = None,
) -> None:
    registry.register(QuantLabReportTool(service, research))
