"""Intent routing.

Phase 1 uses deterministic rules: they are instant, free and predictable for
the small command set JARVIS supports today. Phase 3 adds a model-backed
router behind the same ``Router`` protocol; trivial commands will keep taking
the fast path.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Complexity = Literal["instant", "simple", "complex", "mission"]


class IntentKind(StrEnum):
    QUERY = "query"  # read-only, answered directly, no mission
    ACTION = "action"  # side effects → mission (act + verify)
    CONVERSATION = "conversation"  # no tools
    UNSUPPORTED = "unsupported"


class Intent(BaseModel):
    kind: IntentKind
    text: str
    summary: str
    complexity: Complexity = "instant"
    confidence: float = 1.0
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    targets: list[str] = Field(default_factory=list)
    topic: str | None = None  # conversation topic, e.g. "greeting"


class Router(Protocol):
    def route(self, text: str) -> Intent: ...


_PREFIX = re.compile(r"^(?:(?:hey|ok|okay)\s+)?jarvis[\s,:;.!-]*", re.I)
_POLITE = re.compile(r"^(?:please|could you|can you|would you|kannst du|bitte)\s+", re.I)
_SUFFIX = re.compile(r"[\s.!?]+$|\s+(?:please|for me|bitte)$", re.I)
_OPEN = re.compile(
    r"^(?:open|launch|start|run|bring up|fire up|öffne|starte|öffnen|starten)\s+(?P<targets>.+)$",
    re.I,
)
_SPLIT = re.compile(r"\s*(?:,|&|\band then\b|\band\b|\bund\b|\bplus\b)\s*", re.I)
_FILLER = re.compile(
    r"^(?:the|a|an|my|up|den|die|das)\s+|\s+(?:app|application|program|programm)$", re.I
)

_QUERIES: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(
            r"(active|current|focused|front)\s+(window|app)|what am i (looking at|working on|in)"
            r"|which (window|app)|welches fenster",
            re.I,
        ),
        "get_active_window",
        "Report the active window",
    ),
    (
        re.compile(
            r"(list|show)\s+(the\s+)?(running\s+|open\s+)?(apps|applications|programs|windows)"
            r"|what('s| is)\s+(running|open)|which apps",
            re.I,
        ),
        "list_running_apps",
        "List running applications",
    ),
    (
        re.compile(
            r"system\s+(info|information|status|report)|status report|how('s| is) (the )?system"
            r"|cpu|memory usage|ram usage|systemstatus",
            re.I,
        ),
        "get_system_info",
        "Report system status",
    ),
]

_THINK = re.compile(
    r"^(?:/think\b|think(?:\s+(?:about|through|hard))?\s*:|denk(?:e)?\s+(?:nach|darüber\s+nach)\s*:"
    r"|überleg(?:e)?(?:\s+dir)?\s*:)\s*",
    re.I,
)
_GREETING = re.compile(
    r"^(hi|hello|hey|good (morning|afternoon|evening)|hallo|servus|moin)\b", re.I
)
_HELP = re.compile(r"^(help|what can you do|capabilities|was kannst du)", re.I)


def split_think(text: str) -> tuple[bool, str]:
    """``think: …`` / ``denk nach: …`` selects THINK mode (the reasoning model)."""
    stripped = _PREFIX.sub("", " ".join(text.strip().split()))
    match = _THINK.match(stripped)
    if match and stripped[match.end() :].strip():
        return True, stripped[match.end() :].strip()
    return False, text


def clean(text: str) -> str:
    text = " ".join(text.strip().split())
    text = _PREFIX.sub("", text)
    text = _POLITE.sub("", text)
    previous = None
    while previous != text:
        previous = text
        text = _SUFFIX.sub("", text)
    return text.strip()


def _targets(raw: str) -> list[str]:
    targets: list[str] = []
    for part in _SPLIT.split(raw):
        part = part.strip()
        previous = None
        while previous != part:
            previous = part
            part = _FILLER.sub("", part).strip()
        if part and part.lower() not in {t.lower() for t in targets}:
            targets.append(part)
    return targets


class RuleBasedRouter:
    def route(self, text: str) -> Intent:
        command = clean(text)
        if not command:
            return Intent(
                kind=IntentKind.CONVERSATION, text=text, summary="Greeting", topic="greeting"
            )

        if match := _OPEN.match(command):
            targets = _targets(match.group("targets"))
            if targets:
                names = ", ".join(targets)
                return Intent(
                    kind=IntentKind.ACTION,
                    text=text,
                    summary=f"Open application: {names}",
                    complexity="instant" if len(targets) == 1 else "mission",
                    tool="open_application",
                    targets=targets,
                )

        for pattern, tool, summary in _QUERIES:
            if pattern.search(command):
                return Intent(kind=IntentKind.QUERY, text=text, summary=summary, tool=tool)

        if _GREETING.match(command):
            return Intent(
                kind=IntentKind.CONVERSATION, text=text, summary="Greeting", topic="greeting"
            )
        if _HELP.match(command):
            return Intent(
                kind=IntentKind.CONVERSATION,
                text=text,
                summary="Capabilities",
                topic="capabilities",
            )
        return Intent(
            kind=IntentKind.UNSUPPORTED,
            text=text,
            summary="Open-ended request",
            complexity="complex",
            confidence=0.0,
        )
