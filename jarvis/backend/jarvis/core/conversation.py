"""Working memory: the recent exchanges of this session (not long-term memory).

Earlier turns are replayed as plain text — exactly the blocks the user sent
and JARVIS's final reply — never with tool calls or reasoning blocks. That
keeps every request append-only (prompt-cache friendly) and avoids replaying
reasoning blocks that some models bind to the exact conversation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from jarvis.llm.base import ChatModel


@dataclass(frozen=True)
class Exchange:
    user_blocks: tuple[str, ...]
    reply: str


class Conversation:
    def __init__(self, max_turns: int) -> None:
        self._turns: deque[Exchange] = deque(maxlen=max(1, max_turns))

    def add(self, user_blocks: list[str], reply: str) -> None:
        self._turns.append(Exchange(tuple(user_blocks), reply))

    def messages_for(self, model: ChatModel) -> list[Any]:
        messages: list[Any] = []
        for turn in self._turns:
            messages.append(model.user_message(list(turn.user_blocks)))
            messages.append(model.assistant_text(turn.reply))
        return messages

    def __len__(self) -> int:
        return len(self._turns)
