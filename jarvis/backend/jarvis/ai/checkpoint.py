"""Job checkpoints: an agent run continues where it stopped, without repeating a tool call.

``run_agent`` saves the conversation after every model reply and after every round of tool
results. Each executed tool call is written to the tool-action ledger under its call id.
When a paused or interrupted run resumes, it reloads the conversation; tool calls that the
last reply asked for are answered from the ledger when they already ran, so nothing is
executed twice — no file written twice, no command re-run, no purchase repeated.
"""

from __future__ import annotations

from typing import Any

from jarvis.ai.messages import jsonable
from jarvis.ai.store import AiStore


class RunCheckpoint:
    def __init__(self, store: AiStore, key: str) -> None:
        self._store = store
        self.key = key

    async def load(self) -> tuple[list[Any], int] | None:
        return await self._store.checkpoint(self.key)

    async def save(self, messages: list[Any], rounds: int) -> None:
        await self._store.save_checkpoint(self.key, jsonable(messages), rounds)

    async def recorded(self, call_id: str) -> tuple[str, bool] | None:
        return await self._store.ledger(self.key, call_id)

    async def record(
        self, call_id: str, name: str, payload: dict[str, Any], content: str, is_error: bool
    ) -> None:
        await self._store.record_tool(self.key, call_id, name, payload, content, is_error)

    async def clear(self) -> None:
        await self._store.clear_checkpoint(self.key)

    async def exists(self) -> bool:
        return await self._store.checkpoint(self.key) is not None
