"""Trace context that flows explicitly through every execution chain."""

from __future__ import annotations

from dataclasses import dataclass, replace

from jarvis.util import new_id


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    mission_id: str | None = None
    agent: str | None = None

    @classmethod
    def new(cls) -> TraceContext:
        return cls(trace_id=new_id()[:16])

    def for_mission(self, mission_id: str) -> TraceContext:
        return replace(self, mission_id=mission_id)

    def for_agent(self, agent: str) -> TraceContext:
        return replace(self, agent=agent)

    def log_fields(self) -> dict[str, str]:
        fields = {"trace_id": self.trace_id}
        if self.mission_id:
            fields["mission_id"] = self.mission_id
        if self.agent:
            fields["agent"] = self.agent
        return fields
