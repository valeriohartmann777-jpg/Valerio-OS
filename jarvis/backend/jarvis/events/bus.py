"""In-process publish/subscribe event bus.

- Handlers subscribe by exact type, prefix wildcard (``mission.*``) or ``*``.
- Handlers run in subscription order; one failing handler never affects the
  emitter or other handlers.
- Streams (bounded queues) feed WebSocket clients. A slow client drops its
  oldest events instead of blocking JARVIS.
- A bounded history lets late subscribers catch up.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import Any

from jarvis.core.trace import TraceContext
from jarvis.events.types import Event, EventType, Severity

log = logging.getLogger("jarvis.events")

Handler = Callable[[Event], Awaitable[None]]


def matches(pattern: str, event_type: str) -> bool:
    if pattern in ("*", event_type):
        return True
    return pattern.endswith(".*") and event_type.startswith(pattern[:-1])


class EventStream:
    """A bounded queue of events for one consumer (e.g. a WebSocket)."""

    def __init__(self, bus: EventBus, maxsize: int) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def offer(self, event: Event) -> None:
        if self._queue.full():
            self._queue.get_nowait()
            self.dropped += 1
        self._queue.put_nowait(event)

    async def get(self) -> Event:
        return await self._queue.get()

    def close(self) -> None:
        self._bus._streams.discard(self)

    async def __aiter__(self) -> AsyncIterator[Event]:
        while True:
            yield await self.get()


class EventBus:
    def __init__(self, history_size: int = 500) -> None:
        self._handlers: list[tuple[str, Handler]] = []
        self._streams: set[EventStream] = set()
        self._history: deque[Event] = deque(maxlen=history_size)

    def subscribe(self, pattern: str, handler: Handler) -> Callable[[], None]:
        entry = (pattern, handler)
        self._handlers.append(entry)

        def unsubscribe() -> None:
            if entry in self._handlers:
                self._handlers.remove(entry)

        return unsubscribe

    def open_stream(self, maxsize: int = 1000) -> EventStream:
        stream = EventStream(self, maxsize)
        self._streams.add(stream)
        return stream

    @property
    def stream_count(self) -> int:
        return len(self._streams)

    async def publish(self, event: Event) -> Event:
        self._history.append(event)
        log.debug(
            event.message or event.type,
            extra={
                "event": event.type,
                "trace_id": event.trace_id,
                "mission_id": event.mission_id,
                "source": event.source,
            },
        )
        for pattern, handler in list(self._handlers):
            if not matches(pattern, event.type):
                continue
            try:
                await handler(event)
            except Exception:
                log.exception(
                    "event handler failed",
                    extra={"event": event.type, "trace_id": event.trace_id},
                )
        for stream in list(self._streams):
            stream.offer(event)
        return event

    async def emit(
        self,
        event_type: EventType,
        *,
        message: str = "",
        source: str = "jarvis",
        severity: Severity = Severity.INFO,
        ctx: TraceContext | None = None,
        mission_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        event = Event(
            type=event_type,
            message=message,
            source=source,
            severity=severity,
            trace_id=ctx.trace_id if ctx else None,
            mission_id=mission_id or (ctx.mission_id if ctx else None),
            payload=payload or {},
        )
        return await self.publish(event)

    def history(
        self,
        *,
        limit: int = 100,
        min_severity: Severity = Severity.DEBUG,
        exclude: Iterable[EventType] = (),
    ) -> list[Event]:
        excluded = set(exclude)
        selected = [e for e in self._history if e.at_least(min_severity) and e.type not in excluded]
        return selected[-limit:]
