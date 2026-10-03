"""The TypeScript protocol must list exactly the backend's event types and states."""

from __future__ import annotations

import re
from pathlib import Path

from jarvis.core.state import JarvisState
from jarvis.events.types import EventType, Severity
from jarvis.settings import PROJECT_ROOT

EVENTS_TS = PROJECT_ROOT / "packages" / "protocol" / "src" / "events.ts"


def ts_array(name: str, source: str) -> list[str]:
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", source, re.S)
    assert match, f"{name} not found in {EVENTS_TS}"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_event_types_match() -> None:
    source = Path(EVENTS_TS).read_text(encoding="utf-8")
    assert ts_array("EVENT_TYPES", source) == [e.value for e in EventType]


def test_states_and_severities_match() -> None:
    source = Path(EVENTS_TS).read_text(encoding="utf-8")
    assert ts_array("JARVIS_STATES", source) == [s.value for s in JarvisState]
    assert ts_array("SEVERITIES", source) == [s.value for s in Severity]
