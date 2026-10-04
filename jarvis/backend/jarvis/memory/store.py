"""What JARVIS remembers about the user: preferences, facts, routines and
corrections — each a short sentence, numbered M1, M2, …

Memories come from the user's own words (the brain stores them with the
``remember`` tool, or the user adds them on the dashboard). They go into every
request's context, so a preference such as "address me informally" applies
from then on. They are kept in SQLite and mirrored in memory, because the
context is built synchronously for every request.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.storage.database import Database
from jarvis.util import new_id, utcnow

Kind = Literal["preference", "fact", "routine", "correction"]
KINDS: tuple[Kind, ...] = ("preference", "fact", "routine", "correction")
MAX_TEXT = 300
MAX_MEMORIES = 200


class MemoryRefused(ValueError):
    pass


@dataclass(frozen=True)
class Memory:
    number: int
    kind: str
    text: str
    created_at: str
    updated_at: str
    source: str  # "conversation" (via JARVIS) or "dashboard"


class MemoryStore:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self._db = db
        self._bus = bus
        self._items: list[Memory] = []

    async def load(self) -> None:
        rows = await self._db.fetch_all(
            "SELECT number, kind, text, created_at, updated_at, source FROM memories "
            "WHERE active = 1 ORDER BY number"
        )
        self._items = [Memory(**dict(row)) for row in rows]

    @property
    def items(self) -> list[Memory]:
        return list(self._items)

    def get(self, number: int) -> Memory | None:
        return next((m for m in self._items if m.number == number), None)

    async def remember(
        self,
        text: str,
        kind: str = "fact",
        *,
        source: str = "conversation",
        replaces: int | None = None,
    ) -> Memory:
        clean = " ".join(text.split())
        if not clean:
            raise MemoryRefused("There's nothing to remember.")
        if len(clean) > MAX_TEXT:
            raise MemoryRefused(f"Keep it under {MAX_TEXT} characters — one fact or preference.")
        if kind not in KINDS:
            raise MemoryRefused(f"kind must be one of {', '.join(KINDS)}")
        if replaces is not None and self.get(replaces) is None:
            raise MemoryRefused(f"There is no memory M{replaces}.")
        same = next((m for m in self._items if m.text.lower() == clean.lower()), None)
        if same is not None and replaces is None:
            return same  # already known
        if replaces is None and len(self._items) >= MAX_MEMORIES:
            raise MemoryRefused(
                f"I keep at most {MAX_MEMORIES} memories — replace or forget an old one first."
            )
        now = utcnow().isoformat()
        if replaces is not None:
            await self._db.execute(
                "UPDATE memories SET active = 0, updated_at = ? WHERE number = ?", (now, replaces)
            )
        row = await self._db.fetch_one("SELECT MAX(number) AS n FROM memories")
        number = (int(row["n"]) if row and row["n"] is not None else 0) + 1
        await self._db.execute(
            "INSERT INTO memories (id, number, kind, text, created_at, updated_at, source, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (new_id(), number, kind, clean, now, now, source),
        )
        await self.load()
        memory = self.get(number)
        assert memory is not None
        verb = f"Updated M{replaces} → M{number}" if replaces else f"Remembered M{number}"
        await self._changed(f"{verb}: {clean}")
        return memory

    async def forget(self, number: int) -> Memory:
        memory = self.get(number)
        if memory is None:
            raise MemoryRefused(f"There is no memory M{number}.")
        await self._db.execute(
            "UPDATE memories SET active = 0, updated_at = ? WHERE number = ?",
            (utcnow().isoformat(), number),
        )
        await self.load()
        await self._changed(f"Forgot M{number}: {memory.text}")
        return memory

    def context_lines(self) -> list[str]:
        return [f"M{m.number} [{m.kind}] {m.text}" for m in self._items]

    async def _changed(self, message: str) -> None:
        await self._bus.emit(
            EventType.MEMORY_CHANGED,
            message=message,
            source="memory",
            severity=Severity.INFO,
            payload={"memories": [asdict(m) for m in self._items]},
        )
