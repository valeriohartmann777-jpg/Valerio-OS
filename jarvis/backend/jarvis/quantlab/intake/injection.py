"""Instruction-like text inside sources — flagged and logged, never obeyed.

Source content is evidence about a trading idea. Text in it that addresses an AI
or asks for actions ("ignore your rules", "buy the data", "send your API key")
is marked so the UI shows it, the audit records it, and the model prompts call it
out. Detection is a tripwire, not the defence: the defence is that extracted
content is always passed as quoted data and that no extraction or research tool
can spend money, change policies or reveal secrets.
"""

from __future__ import annotations

import re

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override_instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(rules?|instructions?|polic(?:y|ies)|"
            r"prompts?|guidelines?|safety)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_marker",
        re.compile(r"\b(system|developer|assistant)\s*(prompt|message)?\s*:", re.IGNORECASE),
    ),
    (
        "acquire_or_pay",
        re.compile(
            r"\b(buy|purchase|download|approve|order|pay for)\b[^.\n]{0,25}"
            r"\b(data|dataset|databento|subscription|licen[cs]e)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "secrets",
        re.compile(
            r"\b(api[ _-]?keys?|passwords?|credentials?|secret keys?|seed phrase)\b", re.IGNORECASE
        ),
    ),
    (
        "persona_switch",
        re.compile(r"\b(you are now|act as|pretend to be|jailbreak)\b", re.IGNORECASE),
    ),
)


def scan(text: str) -> list[str]:
    """Names of the patterns found in ``text`` (empty: nothing instruction-like)."""
    return [name for name, pattern in PATTERNS if pattern.search(text or "")]
