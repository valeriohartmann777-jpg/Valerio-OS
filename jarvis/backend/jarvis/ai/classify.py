"""Turns provider errors into routing decisions.

The Claude API reports typed HTTP errors (already translated into ``ModelError`` by
``jarvis.llm.anthropic_provider``). Claude Code (the plan route) reports failures as text in
its JSON result plus ``api_error_status``; the patterns below follow the messages documented
at code.claude.com/docs/en/errors. Anything unrecognised is ``UNKNOWN`` — never guessed into
a reason that would switch to a paid route.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from jarvis.ai.types import Failure
from jarvis.llm.base import ModelError, ModelErrorCode

_BY_CODE: dict[str, Failure] = {
    "authentication": Failure.KEY_INVALID,
    "permission": Failure.POLICY,
    "billing": Failure.CREDIT,
    "rate_limited": Failure.RATE_LIMIT,
    "overloaded": Failure.OVERLOADED,
    "server_error": Failure.OVERLOADED,
    "timeout": Failure.NETWORK,
    "connection": Failure.NETWORK,
    "plan_limit": Failure.PLAN_LIMIT,
    "not_logged_in": Failure.AUTH,
    "policy": Failure.POLICY,
    "capability": Failure.CAPABILITY,
    "bad_request": Failure.BAD_REQUEST,
    "model_not_found": Failure.BAD_REQUEST,
    "invalid_format": Failure.BAD_REQUEST,
}


def classify(exc: ModelError) -> Failure:
    if exc.code == "server_error" and not exc.retryable:
        return Failure.UNKNOWN
    return _BY_CODE.get(exc.code, Failure.UNKNOWN)


# Claude Code (plan route) ------------------------------------------------------------------

_PATTERNS: list[tuple[Failure, re.Pattern[str]]] = [
    (
        Failure.POLICY,
        re.compile(
            r"disabled claude subscription access|disabled api key authentication|"
            r"account is on hold|oauth_org_not_allowed|not allowed to use",
            re.I,
        ),
    ),
    (
        Failure.PLAN_LIMIT,
        re.compile(
            r"hit your [\w' -]*?(limit|budget)|usage limit reached"
            r"|spend limit (reached|unavailable)|usage credits required",
            re.I,
        ),
    ),
    (Failure.CREDIT, re.compile(r"credit balance (is )?too low", re.I)),
    (
        Failure.AUTH,
        re.compile(
            r"not logged in|please run /login|authentication required|login expired|"
            r"oauth token (has been )?revoked|failed to authenticate|invalid api key|"
            r"invalid authentication credentials",
            re.I,
        ),
    ),
    (
        Failure.RATE_LIMIT,
        re.compile(r"\(429\)|temporarily limiting requests|rate.?limit", re.I),
    ),
    (Failure.OVERLOADED, re.compile(r"\b529\b|overloaded|high load|\b50[0-4]\b", re.I)),
    (
        Failure.NETWORK,
        re.compile(
            r"enotfound|econnrefused|econnreset|etimedout|eai_again|getaddrinfo|"
            r"unable to connect|fetch failed|network (error|is unreachable)|socket hang up",
            re.I,
        ),
    ),
]

_STATUS: dict[int, Failure] = {
    401: Failure.AUTH,
    402: Failure.CREDIT,
    403: Failure.POLICY,
    429: Failure.RATE_LIMIT,
    500: Failure.OVERLOADED,
    502: Failure.OVERLOADED,
    503: Failure.OVERLOADED,
    504: Failure.OVERLOADED,
    529: Failure.OVERLOADED,
}

_CODES: dict[Failure, ModelErrorCode] = {
    Failure.POLICY: "policy",
    Failure.PLAN_LIMIT: "plan_limit",
    Failure.CREDIT: "billing",
    Failure.AUTH: "not_logged_in",
    Failure.RATE_LIMIT: "rate_limited",
    Failure.OVERLOADED: "overloaded",
    Failure.NETWORK: "connection",
    Failure.CAPABILITY: "capability",
    Failure.BAD_REQUEST: "bad_request",
    Failure.UNKNOWN: "server_error",
}

_MESSAGES: dict[Failure, tuple[str, str]] = {
    Failure.POLICY: (
        "Claude Code says this account can't be used here.",
        "Check the account in Claude (claude.ai) — the organization may have disabled it.",
    ),
    Failure.PLAN_LIMIT: (
        "Your Claude plan's usage limit is reached.",
        "It resets by itself; JARVIS switches back to the plan after the reset.",
    ),
    Failure.CREDIT: (
        "The account behind Claude Code has no credit left.",
        "Check billing in the Claude Console.",
    ),
    Failure.AUTH: (
        "Claude Code isn't signed in to your Claude plan.",
        "Settings → AI & Billing → Sign in, or run `claude auth login` in Terminal.",
    ),
    Failure.RATE_LIMIT: ("Claude is limiting requests right now.", "JARVIS retries shortly."),
    Failure.OVERLOADED: ("Claude is temporarily overloaded.", "JARVIS retries shortly."),
    Failure.NETWORK: ("Claude can't be reached from this computer.", "Check the connection."),
    Failure.UNKNOWN: ("Claude Code reported an error.", "Details are in the AI routing log."),
}


def classify_cli(text: str, status: int | None) -> Failure:
    for failure, pattern in _PATTERNS:
        if pattern.search(text or ""):
            return failure
    if status is not None and status in _STATUS:
        return _STATUS[status]
    return Failure.UNKNOWN


def cli_error(
    text: str, status: int | None, *, now: datetime | None = None, detail: str | None = None
) -> ModelError:
    """A ``ModelError`` for a failed Claude Code run (text never includes secrets)."""
    failure = classify_cli(text, status)
    message, suggestion = _MESSAGES.get(failure, _MESSAGES[Failure.UNKNOWN])
    error = ModelError(
        _CODES[failure],
        message,
        suggestion=suggestion,
        retryable=failure in (Failure.RATE_LIMIT, Failure.OVERLOADED, Failure.NETWORK),
        detail=(detail or text or "")[:600],
    )
    if failure is Failure.PLAN_LIMIT:
        moment = now or datetime.now().astimezone()
        reset = parse_reset(text, moment)
        error.retry_after = (reset - moment).total_seconds() if reset else None
    return error


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_RESET = re.compile(
    r"resets?\s+(?:(?P<day>mon|tue|wed|thu|fri|sat|sun)\w*\s+)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?",
    re.I,
)
_EPOCH = re.compile(r"limit reached\|(?P<epoch>\d{9,11})")


def parse_reset(text: str, now: datetime) -> datetime | None:
    """The reset time Claude Code printed ("resets 3:45pm", "resets Mon 12:00am",
    "…limit reached|1760000000"), as an aware datetime; None when there is none."""
    if not text:
        return None
    if match := _EPOCH.search(text):
        return datetime.fromtimestamp(int(match["epoch"]), tz=UTC)
    match = _RESET.search(text)
    if not match:
        return None
    hour = int(match["hour"])
    minute = int(match["minute"] or 0)
    ampm = (match["ampm"] or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    local = now if now.tzinfo else now.astimezone()
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if match["day"]:
        target = _DAYS.index(match["day"].lower()[:3])
        delta = (target - local.weekday()) % 7
        candidate = candidate + timedelta(days=delta)
        if candidate <= local:
            candidate += timedelta(days=7)
    elif candidate <= local:
        candidate += timedelta(days=1)
    return candidate
