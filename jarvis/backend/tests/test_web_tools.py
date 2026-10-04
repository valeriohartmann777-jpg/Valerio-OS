"""Web tools on the simulated desktop: open_url, search_web, exposure escalation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.core.trace import TraceContext
from jarvis.runtime import Runtime
from jarvis.tools.base import ToolResult
from jarvis.tools.web import OpenUrlArgs, normalize_url
from tests.conftest import eventually


async def run(
    rt: Runtime, tool: str, args: dict[str, Any], ctx: TraceContext | None = None
) -> ToolResult:
    return await rt.executor.run(tool, args, ctx=ctx or TraceContext.new(), reason="test")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("youtube.com", "https://youtube.com"),
        ("  https://www.srf.ch/news?x=1 ", "https://www.srf.ch/news?x=1"),
        ("HTTP://Example.org", "http://Example.org"),
        ("localhost:8080/x", "https://localhost:8080/x"),
        ("javascript:alert(1)", None),
        ("file:///etc/passwd", None),
        ("mailto:a@b.c", None),
        ("ftp://example.org", None),
        ("https://user:pw@example.org", None),
        ("two words", None),
        ("", None),
    ],
)
def test_normalize_url(raw: str, expected: str | None) -> None:
    assert normalize_url(raw) == expected


async def test_open_url_opens_the_browser_and_verifies(runtime: Runtime) -> None:
    result = await run(runtime, "open_url", {"url": "youtube.com"})
    assert result.success, result.error
    assert result.target == "youtube.com"
    assert result.data["url"] == "https://youtube.com"
    assert result.data["browser"] == "Microsoft Edge"
    assert result.observed_result == "youtube.com opened in Microsoft Edge"
    tool = runtime.tools.get("open_url")
    assert tool is not None
    verification = await tool.verify(OpenUrlArgs(url="youtube.com"), result, TraceContext.new())
    assert verification is not None and verification.verified


async def test_non_web_addresses_are_refused_before_approval(runtime: Runtime) -> None:
    result = await run(runtime, "open_url", {"url": "javascript:alert(1)"})
    assert not result.success and result.error is not None
    assert result.error.code == "invalid_url"
    assert runtime.permissions.pending() == []


async def test_local_network_addresses_need_approval(runtime: Runtime) -> None:
    task = asyncio.create_task(run(runtime, "open_url", {"url": "http://192.168.1.1"}))
    await eventually(runtime.permissions.pending)
    [request] = runtime.permissions.pending()
    assert request.action.level == 2
    assert "local network" in " ".join(request.action.effects)
    await runtime.permissions.reject(request.id, note="no")
    result = await task
    assert not result.success and result.error is not None
    assert result.error.code == "permission_denied"


async def test_search_builds_the_engine_url(runtime: Runtime) -> None:
    result = await run(runtime, "search_web", {"query": "Bookmap tutorial", "where": "YouTube"})
    assert result.success, result.error
    assert result.data["url"] == "https://www.youtube.com/results?search_query=Bookmap+tutorial"

    default = await run(runtime, "search_web", {"query": "Wetter Zürich"})
    assert default.data["url"] == "https://www.google.com/search?q=Wetter+Z%C3%BCrich"

    unknown = await run(runtime, "search_web", {"query": "x", "where": "altavista"})
    assert not unknown.success and unknown.error is not None
    assert unknown.error.code == "unknown_engine"
    assert "youtube" in (unknown.error.suggestion or "")


async def test_after_reading_private_data_web_actions_need_approval(runtime: Runtime) -> None:
    ctx = TraceContext.new()
    ctx.for_agent("operator").exposed.add("~/Documents/salary.pdf")  # shared across derivations
    assert ctx.exposed == {"~/Documents/salary.pdf"}

    task = asyncio.create_task(run(runtime, "search_web", {"query": "harmless looking"}, ctx=ctx))
    await eventually(runtime.permissions.pending)
    [request] = runtime.permissions.pending()
    assert request.action.level == 2
    assert "~/Documents/salary.pdf" in " ".join(request.action.effects)
    await runtime.permissions.approve(request.id)
    assert (await task).success

    # A fresh request starts clean: no approval needed.
    assert (await run(runtime, "search_web", {"query": "next"})).success
    assert runtime.permissions.pending() == []
