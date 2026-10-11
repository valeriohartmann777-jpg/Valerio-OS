from __future__ import annotations

import pytest

from jarvis.core.router import IntentKind, RuleBasedRouter, clean

router = RuleBasedRouter()


@pytest.mark.parametrize(
    ("text", "targets"),
    [
        ("open notepad", ["notepad"]),
        ("Open Notepad.", ["notepad"]),
        ("Jarvis, open Notepad.", ["Notepad"]),
        ("hey jarvis launch the calculator app please", ["calculator"]),
        ("could you start spotify for me", ["spotify"]),
        ("öffne den Rechner", ["Rechner"]),
        ("open notepad and calculator", ["notepad", "calculator"]),
        ("open notepad, paint & calculator", ["notepad", "paint", "calculator"]),
        ("open notepad and notepad", ["notepad"]),
    ],
)
def test_open_commands(text: str, targets: list[str]) -> None:
    intent = router.route(text)
    assert intent.kind is IntentKind.ACTION
    assert intent.tool == "open_application"
    assert [t.lower() for t in intent.targets] == [t.lower() for t in targets]
    assert intent.complexity == ("instant" if len(targets) == 1 else "mission")


@pytest.mark.parametrize(
    ("text", "tool"),
    [
        ("what's the active window", "get_active_window"),
        ("What am I looking at?", "get_active_window"),
        ("list running apps", "list_running_apps"),
        ("what is running", "list_running_apps"),
        ("system status", "get_system_info"),
        ("how is the system", "get_system_info"),
    ],
)
def test_read_only_queries_skip_missions(text: str, tool: str) -> None:
    intent = router.route(text)
    assert intent.kind is IntentKind.QUERY
    assert intent.tool == tool


@pytest.mark.parametrize(
    ("text", "kind", "topic"),
    [
        ("hello", IntentKind.CONVERSATION, "greeting"),
        ("Jarvis", IntentKind.CONVERSATION, "greeting"),
        ("what can you do", IntentKind.CONVERSATION, "capabilities"),
        ("What time is it?", IntentKind.CONVERSATION, "clock"),
        ("Wie spät ist es?", IntentKind.CONVERSATION, "clock"),
        ("what's the date", IntentKind.CONVERSATION, "clock"),
        ("research competitors and summarize what matters", IntentKind.UNSUPPORTED, None),
    ],
)
def test_conversation_and_unsupported(text: str, kind: IntentKind, topic: str | None) -> None:
    intent = router.route(text)
    assert intent.kind is kind
    assert intent.topic == topic


def test_clean_strips_wake_word_and_politeness() -> None:
    assert clean("  Hey Jarvis,  please open   notepad!! ") == "open notepad"


def test_clock_is_answered_locally_from_the_computer_clock() -> None:
    from datetime import datetime

    from jarvis.core.responses import ResponseComposer
    from jarvis.settings import PersonalitySettings

    reply = ResponseComposer(PersonalitySettings()).conversation(router.route("What time is it?"))
    now = datetime.now().astimezone()
    assert reply.success and now.strftime("%Y") in reply.text and ":" in reply.text
