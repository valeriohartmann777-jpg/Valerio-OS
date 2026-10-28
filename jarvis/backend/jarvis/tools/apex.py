"""apex_analysis: JARVIS's view of a market — structure, zones, counted rates, candidates."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from jarvis.apex.service import ApexError, ApexService
from jarvis.core.trace import TraceContext
from jarvis.permissions.models import PermissionLevel
from jarvis.tools.base import Tool, ToolError, ToolResult
from jarvis.tools.registry import ToolRegistry


class ApexArgs(BaseModel):
    market: Literal["XAUUSD", "NQ", "MNQ"] = Field(description="XAUUSD (gold), NQ or MNQ")


class ApexAnalysisTool(Tool[ApexArgs]):
    name = "apex_analysis"
    description = (
        "JARVIS APEX right now for XAUUSD (gold), NQ or MNQ: market structure (trend on 15 min "
        "and 1 h, swings, breaks of structure), the nearest support and resistance zones with "
        "the counted share of earlier touches that held (with sample size and 95% interval), "
        "and rule-based trade candidates with entry, stop, targets, reward/risk and risk in $. "
        "Candidates are journaled and judged on later bars. Research only — APEX places no "
        "orders. Always say where the data comes from (and if it is synthetic test data) and "
        "that the numbers are counted rates, not predictions or advice."
    )
    permission_level = PermissionLevel.READ
    input_model = ApexArgs

    def __init__(self, service: Callable[[], ApexService]) -> None:
        self._service = service

    async def execute(self, args: ApexArgs, ctx: TraceContext) -> ToolResult:
        try:
            out = await self._service().analyze(args.market, refresh=True)
        except ApexError as exc:
            return ToolResult.failure(
                self.name,
                ToolError(
                    code=exc.code.lower(), message=exc.message, suggestion=exc.remedy or None
                ),
            )
        summary = brief(out)
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            summary=summary,
            observed_result=summary,
            data=compact(out),
        )


def _rate(rate: dict[str, Any] | None) -> str:
    if not rate:
        return "no rate"
    if rate.get("p") is None:
        return f"insufficient data ({rate.get('n', 0)} touches)"
    lo, hi = rate["ci"]
    return f"{rate['p']:.0%} held (95% {lo:.0%}-{hi:.0%}, n={rate['n']})"


def brief(out: dict[str, Any]) -> str:
    """One line for the chat; the full result goes to the model as data."""
    data = out["data"]
    source = "SYNTHETIC test data" if data["synthetic"] else data["label"]
    update = data.get("update") or {}
    failed = f" The data update failed: {update['error']}" if update.get("error") else ""
    if out.get("blocked"):
        blocks = [f for f in out["quality"]["findings"] if f["level"] == "BLOCK"]
        reason = f" {blocks[0]['code']}: {blocks[0]['message']}" if blocks else ""
        return (
            f"APEX {out['market']}: analysis blocked by the data check ({source}).{reason}"
            f"{failed} APEX → Data shows the cause and repairs it."
        )
    zones = {z["id"]: z for z in out["zones"]}
    parts = [f"APEX {out['market']} at {out.get('price')} ({source})"]
    trend = out.get("structure", {}).get("60", {}).get("trend")
    if trend:
        parts.append(f"1h trend {trend}")
    for zid in ("S1", "R1"):
        z = zones.get(zid)
        if z:
            parts.append(f"{zid} {z['low']}-{z['high']}: {_rate(z.get('rate'))}")
    parts.append(f"{len(out['candidates'])} candidate(s)")
    if out["quality"].get("stale"):
        parts.append("data stale: no candidates")
    return " · ".join(parts) + failed


def compact(out: dict[str, Any]) -> dict[str, Any]:
    """What the model needs — without the chart bars."""
    keep = {k: v for k, v in out.items() if k not in ("chart",)}
    keep["candidates"] = [
        {
            k: c.get(k)
            for k in (
                "direction",
                "zone",
                "entry",
                "stop",
                "targets",
                "risk_points",
                "risk_usd",
                "unit",
                "valid_until",
                "with_trend",
                "zone_rate",
                "track_record",
            )
        }
        for c in out["candidates"]
    ]
    keep["note"] = (
        "Research candidates; nothing is ordered. Rates are counted from data before the moment."
    )
    return keep


def register_apex_tools(registry: ToolRegistry, service: Callable[[], ApexService]) -> None:
    registry.register(ApexAnalysisTool(service))
