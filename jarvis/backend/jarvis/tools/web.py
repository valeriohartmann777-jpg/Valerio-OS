"""Web tools: open a web address or a search in the default browser.

Only ``http``/``https`` addresses are opened. Addresses on this computer or the
local network need approval (a page there can trigger actions on devices or
services). Both tools can carry data off the computer, so after a request has
read private files they need approval too (see ``ToolExecutor``).

Verification observes the browser: a browser window in front counts as
verified. Page titles are only readable on macOS with the Screen Recording
permission, so the page itself is not checked.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import quote_plus, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from jarvis.core.trace import TraceContext
from jarvis.permissions.models import ActionDescriptor, PermissionLevel
from jarvis.settings import WebSettings
from jarvis.tools.base import Tool, ToolError, ToolResult, Verification
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.system.apps import AppCatalog
from jarvis.tools.system.backend import ActionError, SystemBackend
from jarvis.tools.system.handoff import BROWSERS, is_browser, observe_handoff

_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]*):(?!\d)", re.IGNORECASE)


def normalize_url(raw: str) -> str | None:
    """``youtube.com`` → ``https://youtube.com``; ``None`` for anything that isn't
    a plain http(s) address (``javascript:``, ``file:``, ``mailto:``, spaces…)."""
    text = raw.strip()
    if not text or any(c.isspace() for c in text):
        return None
    if "://" not in text:
        if (match := _SCHEME.match(text)) and match.group(1).lower() not in ("http", "https"):
            return None
        text = f"https://{text}"
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None  # credentials in a URL are a classic phishing trick
    return urlunsplit(parts._replace(scheme=parts.scheme.lower()))


def is_local(host: str) -> bool:
    """This computer or the local network."""
    host = host.lower().strip("[]")
    if host == "localhost" or host.endswith((".localhost", ".local", ".lan", ".home.arpa")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host  # single-label names resolve on the local network
    return address.is_private or address.is_loopback or address.is_link_local


def _host(url: str) -> str:
    host = urlsplit(url).hostname or url
    return host.removeprefix("www.")


class _BrowserTool:
    """Open a URL and observe the browser."""

    def __init__(self, backend: SystemBackend, catalog: AppCatalog, *, verify_timeout: float):
        self._backend = backend
        self._catalog = catalog
        self._timeout = verify_timeout

    async def _open(self, tool: str, url: str, target: str) -> ToolResult:
        before = await self._backend.snapshot()
        try:
            await self._backend.open_location(url)
        except ActionError as exc:
            return ToolResult.failure(
                tool,
                ToolError(
                    code=exc.code, message=exc.message, suggestion=exc.suggestion, detail=exc.detail
                ),
                target=target,
                simulated=self._backend.simulated,
            )
        observations = [f"Address handed to the default browser: {url}"]
        handoff = await observe_handoff(
            self._backend, before, wait_seconds=self._timeout, accept=is_browser
        )
        data = {"url": url, "browser": None}
        if handoff.window is None:
            observations.append("No browser window came to the front")
            observed = "Handed to the default browser, but no browser window came to the front"
        else:
            browser = self._catalog.window_label(handoff.window)
            data["browser"] = browser
            observations.append(
                f"{browser} is in front"
                + ("" if handoff.changed else " (it already was; the new page can't be seen)")
            )
            observed = f"{target} opened in {browser}"
        return ToolResult(
            success=True,
            tool=tool,
            action=tool,
            target=target,
            summary=observed,
            observed_result=observed,
            observations=observations,
            data=data,
            simulated=self._backend.simulated,
        )

    async def _verify_browser(self) -> Verification:
        snapshot = await self._backend.snapshot()
        front = snapshot.foreground
        browsers = [w for w in snapshot.windows if w.process_name in BROWSERS]
        if front is not None and is_browser(front):
            return Verification(
                status="verified",
                method="window_enumeration",
                summary=f"{self._catalog.window_label(front)} is in front",
                evidence=[f"{len(browsers)} browser window(s)"],
            )
        if browsers:
            return Verification(
                status="unverifiable",
                method="window_enumeration",
                summary="A browser is open, but it isn't in front.",
                evidence=[f"{len(browsers)} browser window(s)"],
            )
        return Verification(
            status="failed", method="window_enumeration", summary="No browser window found."
        )


class OpenUrlArgs(BaseModel):
    url: str = Field(
        min_length=1,
        max_length=2000,
        description="Web address, e.g. 'youtube.com' or 'https://www.srf.ch/news'.",
    )


class OpenUrlTool(_BrowserTool, Tool[OpenUrlArgs]):
    name = "open_url"
    description = "Open a web page (http/https address) in the user's default browser."
    permission_level = PermissionLevel.SAFE_ACTION
    input_model = OpenUrlArgs
    side_effects = True
    sends_data_out = True

    def describe_action(self, args: OpenUrlArgs) -> ActionDescriptor:
        url = normalize_url(args.url) or args.url
        host = _host(url)
        local = is_local(urlsplit(url).hostname or "")
        return ActionDescriptor(
            title="Open web page",
            target=host,
            summary=f"Open {host} in your browser.",
            effects=[
                f"Your default browser loads {url[:200]}",
                *(["The address is on this computer or your local network"] if local else []),
            ],
            level=PermissionLevel.MODIFICATION if local else self.permission_level,
            category="web",
        )

    def step_titles(self, args: OpenUrlArgs) -> tuple[str, str]:
        host = self.describe_action(args).target
        return f"Open {host}", "Verify the browser is in front"

    async def precheck(self, args: OpenUrlArgs, ctx: TraceContext) -> ToolError | None:
        if normalize_url(args.url) is None:
            return ToolError(
                code="invalid_url",
                message="I only open web addresses (http or https).",
                suggestion="For files on this computer, use the file tools.",
            )
        return None

    async def execute(self, args: OpenUrlArgs, ctx: TraceContext) -> ToolResult:
        url = normalize_url(args.url)
        assert url is not None  # precheck
        return await self._open(self.name, url, _host(url))

    async def verify(
        self, args: OpenUrlArgs, result: ToolResult, ctx: TraceContext
    ) -> Verification:
        if not result.success:
            return Verification(status="failed", method="result", summary=result.summary)
        return await self._verify_browser()


class SearchWebArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="What to search for.")
    where: str = Field(
        default="web",
        description=(
            "Search engine: 'web' (default), 'images', 'news', 'youtube', 'maps' or 'wikipedia'."
        ),
    )


class SearchWebTool(_BrowserTool, Tool[SearchWebArgs]):
    name = "search_web"
    description = (
        "Open a search (Google, YouTube, Maps, Wikipedia…) in the user's default browser. "
        "Shows results to the user; it does not return them to you."
    )
    permission_level = PermissionLevel.SAFE_ACTION
    input_model = SearchWebArgs
    side_effects = True
    sends_data_out = True

    def __init__(
        self,
        backend: SystemBackend,
        catalog: AppCatalog,
        settings: WebSettings,
        *,
        verify_timeout: float,
    ) -> None:
        super().__init__(backend, catalog, verify_timeout=verify_timeout)
        self._engines = {k.lower(): v for k, v in settings.search_engines.items()}
        self._default = settings.default_search.lower()

    def _engine(self, where: str) -> str:
        key = where.strip().lower() or self._default
        return key if key in self._engines else ""

    def _url(self, args: SearchWebArgs) -> str:
        return self._engines[self._engine(args.where)].replace(
            "{query}", quote_plus(args.query.strip())
        )

    def describe_action(self, args: SearchWebArgs) -> ActionDescriptor:
        engine = self._engine(args.where) or args.where
        label = "the web" if engine == "web" else engine.capitalize()
        return ActionDescriptor(
            title="Search",
            target=engine,
            summary=f"Search {label} for “{args.query.strip()}”.",
            effects=["Your default browser opens the search results"],
            level=self.permission_level,
            category="web",
        )

    def step_titles(self, args: SearchWebArgs) -> tuple[str, str]:
        return f"Search for “{args.query.strip()[:40]}”", "Verify the browser is in front"

    async def precheck(self, args: SearchWebArgs, ctx: TraceContext) -> ToolError | None:
        if not self._engine(args.where):
            return ToolError(
                code="unknown_engine",
                message=f"I don't know the search “{args.where}”.",
                suggestion=f"Available: {', '.join(sorted(self._engines))} (config/web.yaml).",
            )
        return None

    async def execute(self, args: SearchWebArgs, ctx: TraceContext) -> ToolResult:
        engine = self._engine(args.where)
        return await self._open(self.name, self._url(args), f"{engine} search")

    async def verify(
        self, args: SearchWebArgs, result: ToolResult, ctx: TraceContext
    ) -> Verification:
        if not result.success:
            return Verification(status="failed", method="result", summary=result.summary)
        return await self._verify_browser()


def register_web_tools(
    registry: ToolRegistry,
    backend: SystemBackend,
    catalog: AppCatalog,
    settings: WebSettings,
    *,
    verify_timeout: float,
) -> None:
    registry.register(OpenUrlTool(backend, catalog, verify_timeout=verify_timeout))
    registry.register(SearchWebTool(backend, catalog, settings, verify_timeout=verify_timeout))
