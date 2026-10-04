"""The learning loop with a scripted model and synthetic market data."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.api.app import create_app
from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.learning.evaluate import evaluate
from jarvis.learning.journal import LearningJournal
from jarvis.learning.market import Bars, DataError, Instrument, MarketData, Progress
from jarvis.learning.prompt import SYSTEM_PROMPT
from jarvis.learning.service import LearningService, LearningState, ResearchModel
from jarvis.learning.strategy import Strategy
from jarvis.llm.base import ModelError, Usage
from jarvis.runtime import Runtime
from jarvis.settings import LearningGates, LearningSettings, load_settings
from jarvis.storage.database import Database
from jarvis.storage.preferences import Preferences
from tests.conftest import Recorder, eventually, make_settings
from tests.fakes import ScriptedChatModel, Step, call
from tests.learning_data import session_bars, weekdays

DAYS = weekdays(date(2024, 1, 2), date(2024, 7, 31))


def _rise(_: int, minute: int) -> float:  # every day +30 points from 10:00 to 10:30
    return 1.0 if 30 <= minute < 60 else 0.0


TRENDING = session_bars(DAYS, path=_rise, noise=0.6)


class FakeMarket(MarketData):
    def __init__(self, bars: Bars, *, error: str | None = None) -> None:
        super().__init__(Path("/nonexistent"))
        self.bars = bars
        self.error = error
        self.synced: list[str] = []

    async def sync(
        self, instrument: Instrument, start: date, end: date, progress: Progress | None = None
    ) -> int:
        if self.error:
            raise DataError(self.error)
        self.synced.append(instrument.name)
        if progress:
            await progress(1, 1)
        return 1

    def load(self, instrument: Instrument, start: date, end: date) -> Bars:
        return self.bars

    def coverage(self, instrument: Instrument) -> tuple[date, date, int] | None:
        return (date(2024, 1, 2), date(2024, 7, 31), len(self.bars))


def learning_settings(**overrides: Any) -> LearningSettings:
    base = load_settings(environ={}).learning
    return base.model_copy(
        update={
            "data_start": date(2024, 1, 1),
            "oos_start": date(2024, 4, 1),
            "holdout_start": date(2024, 6, 1),
            "round_interval_minutes": 60.0,
            "gates": LearningGates(
                min_trades={"daytrading": 40, "scalping": 40},
                min_oos_trades={"daytrading": 20, "scalping": 20},
            ),
            **overrides,
        }
    )


STRATEGY = {
    "name": "10:00 drift",
    "hypothesis": "Something lifts the market every morning after 10:00.",
    "instrument": "NQ",
    "style": "daytrading",
    "timeframe": "5m",
    "session": {"start": "09:30", "end": "16:00"},
    "entries": [{"side": "long", "when": ["minutes >= 30", "minutes <= 30"]}],
    "exit": {"stop": "8", "target_r": 2.5},
    "max_trades_per_day": 1,
}


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.bus = EventBus()
        self.recorder = Recorder(self.bus)
        self.db = Database(tmp_path / "jarvis.db")
        self.journal = LearningJournal(self.db)
        self.prefs = Preferences(tmp_path / "preferences.json")
        self.market = FakeMarket(TRENDING)
        self.model: ScriptedChatModel | None = None
        self.service: LearningService | None = None

    async def start(self, script: list[Step] | None, **overrides: Any) -> LearningService:
        await self.db.connect()
        model = None if script is None else ScriptedChatModel(script, model_id="claude-opus-5-5")
        self.model = model

        def factory() -> ResearchModel | None:
            return model

        self.service = LearningService(
            settings=learning_settings(**overrides),
            bus=self.bus,
            journal=self.journal,
            market=self.market,
            preferences=self.prefs,
            model_factory=factory,
            yesterday_utc=lambda: date(2024, 7, 31),
        )
        await self.service.start()
        return self.service

    async def close(self) -> None:
        if self.service:
            await self.service.stop()
        await self.db.close()

    def tool_results(self, call_index: int) -> list[dict[str, Any]]:
        """The tool results the model received with its ``call_index``-th call."""
        assert self.model is not None
        message = self.model.calls[call_index]["messages"][-1]
        return [block for block in message["content"] if block.get("type") == "tool_result"]

    def briefing(self, call_index: int = 0) -> str:
        assert self.model is not None
        text: str = self.model.calls[call_index]["messages"][0]["content"][0]["text"]
        return text


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path)
    try:
        yield h
    finally:
        await h.close()


def finish(summary: str = "done", focus: str = "") -> Step:
    return call("finish_round", {"summary": summary, "next_focus": focus}, "toolu_f")


def state_is(service: LearningService, state: LearningState) -> Callable[[], bool]:
    return lambda: service.status.state == state


async def test_a_round_tests_learns_and_waits(harness: Harness) -> None:
    service = await harness.start(
        [
            call("run_backtest", STRATEGY, "toolu_1"),
            call(
                "write_note",
                {"topic": "Morning drift", "text": "T1 held up.", "sources": ["https://x.test/a"]},
                "toolu_2",
            ),
            finish("Found the 10:00 drift.", "Check whether it works short too."),
        ]
    )
    await service.enable()
    await eventually(state_is(service, LearningState.WAITING), seconds=10)

    [test] = await harness.journal.tests()
    assert test["status"] == "validated" and test["holdout_confirmed"] is True
    [note] = await harness.journal.notes()
    assert note["topic"] == "Morning drift" and note["sources"] == ["https://x.test/a"]
    [round_] = await harness.journal.rounds()
    assert round_["status"] == "completed" and round_["summary"] == "Found the 10:00 drift."
    assert round_["cost_usd"] > 0 and service.status.spent_today_usd == pytest.approx(
        round_["cost_usd"]
    )
    assert service.status.counts["validated"] == 1 and service.status.next_round_at
    assert harness.market.synced == ["NQ", "XAUUSD"]

    # What the model saw: in-sample numbers and a verdict, never later periods.
    [result] = harness.tool_results(1)
    body = json.loads(result["content"])
    assert body["result"] == "validated" and body["in_sample"]["trades"] > 40
    assert "out_of_sample" not in body and "holdout" not in body
    assert "Round 1" in harness.briefing() and "(none yet)" in harness.briefing()

    messages = [e.message for e in harness.recorder.of(EventType.LEARNING_CHANGED)]
    found = [e for e in harness.recorder.of(EventType.LEARNING_CHANGED) if "Validated" in e.message]
    assert found and found[0].severity == Severity.IMPORTANT
    assert any(m.startswith("Learning round 1: 1 test, 1 validated") for m in messages)


async def test_later_rounds_see_findings_but_never_the_holdout(harness: Harness) -> None:
    service = await harness.start(
        [call("run_backtest", STRATEGY), finish("first", "go short"), finish("second")],
        round_interval_minutes=0.0,
        stall_limit=1,
    )
    await service.enable()
    await eventually(state_is(service, LearningState.STALLED), seconds=10)
    [test] = await harness.journal.tests()
    briefing = harness.briefing(2)
    assert "Round 2" in briefing and "Your plan from last round: go short" in briefing
    assert f"T1 {STRATEGY['name']}" in briefing
    assert test["holdout"] and test["out_of_sample"]  # stored for the user …
    assert "holdout" not in briefing.lower() and "out_of_sample" not in briefing  # … not shown


async def test_invalid_strategies_are_explained(harness: Harness) -> None:
    broken = {**STRATEGY, "entries": [{"side": "long", "when": ["close > magic(3)"]}]}
    service = await harness.start([call("run_backtest", broken), finish()])
    await service.enable()
    await eventually(state_is(service, LearningState.WAITING), seconds=10)
    [result] = harness.tool_results(1)
    assert result["is_error"] and "unknown feature 'magic'" in result["content"]
    [test] = await harness.journal.tests()
    assert test["status"] == "invalid"


async def test_test_budget_per_round(harness: Harness) -> None:
    service = await harness.start(
        [
            call("run_backtest", STRATEGY, "toolu_1"),
            call("run_backtest", STRATEGY, "toolu_2"),
            finish(),
        ],
        tests_per_round=1,
    )
    await service.enable()
    await eventually(state_is(service, LearningState.WAITING), seconds=10)
    [result] = harness.tool_results(2)
    assert result["is_error"] and "backtests are used up" in result["content"]
    assert len(await harness.journal.tests()) == 1


async def test_the_daily_budget_is_never_exceeded(harness: Harness) -> None:
    expensive = Usage(input_tokens=1_000_000, output_tokens=0)  # $4 at Opus prices
    service = await harness.start(
        [call("run_backtest", STRATEGY, usage=expensive)], daily_budget_usd=3.0
    )
    await service.enable()
    await eventually(state_is(service, LearningState.BUDGET), seconds=10)
    assert harness.model and len(harness.model.calls) == 1  # no second call that day
    [round_] = await harness.journal.rounds()
    assert round_["summary"] == "Stopped early: today's budget is used up."
    status = service.status
    assert status.spent_today_usd == pytest.approx(4.0, abs=0.01)
    tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date().isoformat()
    assert status.next_round_at and status.next_round_at.startswith(f"{tomorrow}T00:01")


async def test_no_round_starts_without_room_in_the_budget(harness: Harness) -> None:
    service = await harness.start([], daily_budget_usd=0.01)
    await service.enable()
    await eventually(state_is(service, LearningState.BUDGET), seconds=10)
    assert harness.model and harness.model.calls == []


async def test_learning_stops_itself_without_progress(harness: Harness) -> None:
    service = await harness.start(
        [finish("nothing"), finish("still nothing")], round_interval_minutes=0.0, stall_limit=2
    )
    await service.enable()
    await eventually(state_is(service, LearningState.STALLED), seconds=10)
    status = service.status
    assert not status.enabled and status.stall_rounds == 2
    assert "stopped itself" in (status.detail or "")
    warnings = [
        e for e in harness.recorder.of(EventType.LEARNING_CHANGED) if e.severity == Severity.WARNING
    ]
    assert warnings and "No new validated finding in 2 rounds" in warnings[-1].message

    # Starting it again grants fresh rounds.
    assert harness.model is not None
    harness.model._script.append(finish("again"))
    await service.enable()
    await eventually(lambda: service.status.counts.get("rounds") == 3, seconds=10)


async def test_waits_for_claude_and_wakes_up_when_it_is_connected(harness: Harness) -> None:
    service = await harness.start(None)
    await service.enable()
    await eventually(state_is(service, LearningState.NEEDS_BRAIN))
    assert "Settings → Brain" in (service.status.detail or "")


async def test_data_problems_are_reported(harness: Harness) -> None:
    harness.market.error = "I can't reach Dukascopy's data feed right now (HTTP 503)."
    service = await harness.start([])
    await service.enable()
    await eventually(state_is(service, LearningState.ERROR))
    assert "Dukascopy" in (service.status.detail or "") and "30 minutes" in (
        service.status.detail or ""
    )


async def test_web_search_is_offered_and_dropped_when_the_key_cant_use_it(
    harness: Harness,
) -> None:
    refused = ModelError(
        "bad_request", "The model rejected my request.", detail="web_search is not enabled"
    )
    service = await harness.start([refused, finish()])
    await service.enable()
    await eventually(state_is(service, LearningState.WAITING), seconds=10)
    assert harness.model is not None
    first, second = harness.model.calls
    assert first["server_tools"][0]["type"] == "web_search_20250305"
    assert first["server_tools"][0]["max_uses"] == 2
    assert second["server_tools"] == []
    assert service.status.web_search is False


async def test_model_errors_pause_the_loop(harness: Harness) -> None:
    service = await harness.start([ModelError("billing", "No credit left.", suggestion="Top up.")])
    await service.enable()
    await eventually(state_is(service, LearningState.ERROR))
    assert service.status.detail == "No credit left. Top up."
    [round_] = await harness.journal.rounds()
    assert round_["status"] == "failed"


async def test_it_resumes_after_a_restart(harness: Harness) -> None:
    harness.prefs.update(**{"learning.enabled": True})
    await harness.db.connect()
    await harness.journal.start_round("2026-10-04")  # left running by a crash
    await harness.db.close()
    service = await harness.start([finish()])
    await eventually(state_is(service, LearningState.WAITING), seconds=10)
    rounds = await harness.journal.rounds()
    assert [r["status"] for r in rounds] == ["completed", "interrupted"]


async def test_stopping_cancels_the_loop(harness: Harness) -> None:
    service = await harness.start(None)
    await service.enable()
    await eventually(state_is(service, LearningState.NEEDS_BRAIN))
    status = await service.disable()
    assert status.state == LearningState.OFF and not status.enabled


def test_cost_follows_the_price_table() -> None:
    service = LearningService(
        settings=learning_settings(),
        bus=EventBus(),
        journal=LearningJournal(Database(":memory:")),
        market=FakeMarket(TRENDING),
        preferences=Preferences(Path("/nonexistent/prefs.json")),
        model_factory=lambda: None,
    )
    usage = Usage(input_tokens=1000, output_tokens=1000, cache_read_tokens=10_000, web_searches=2)
    # Opus 5.5: $4 in, $20 out, $0.20 cache read per million; $0.01 per search
    assert service._cost(usage) == pytest.approx(0.004 + 0.02 + 0.002 + 0.02)


def test_the_prompt_asks_for_honesty_not_survival() -> None:
    lowered = SYSTEM_PROMPT.lower()
    for word in ("shut down", "survive", "survival", "switched off", "abgestellt"):
        assert word not in lowered
    assert "negative results are still knowledge" in lowered


def test_api_and_snapshot(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    runtime = Runtime(settings, research_model=lambda: None, market=FakeMarket(TRENDING))
    with TestClient(create_app(runtime=runtime)) as client:
        assert client.get("/snapshot").json()["learning"]["state"] == "off"
        started = client.post("/learning/start").json()
        assert started["enabled"] is True
        for _ in range(200):
            if client.get("/learning").json()["state"] == "needs_brain":
                break
        assert client.get("/learning").json()["state"] == "needs_brain"
        assert client.get("/learning/tests").json() == []
        assert client.get("/learning/notes").json() == []
        assert client.get("/learning/rounds").json() == []
        assert client.get("/learning/tests", params={"status": "bogus"}).status_code == 422
        stopped = client.post("/learning/stop").json()
        assert stopped["state"] == "off" and stopped["enabled"] is False


async def test_jarvis_can_report_what_it_learned(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    rt = Runtime(settings, research_model=lambda: None, market=FakeMarket(TRENDING))
    await rt.start()
    try:
        empty = await rt.executor.run("learning_report", {}, ctx=TraceContext.new(), reason="test")
        assert empty.success and "0 backtests, 0 validated" in empty.summary

        strategy = Strategy.model_validate(STRATEGY)
        outcome = evaluate(strategy, TRENDING, learning_settings(), oos_evaluations_before=0)
        await rt.learning_journal.add_test(None, strategy.name, STRATEGY, outcome)
        await rt.learning_journal.add_note("Morning drift", "It holds.", [], round_id=None)
        await rt.learning._refresh()

        report = await rt.executor.run("learning_report", {}, ctx=TraceContext.new(), reason="test")
        assert "1 backtests, 1 validated (1 confirmed on the holdout), 1 notes" in report.summary
        [finding] = report.data["validated"]
        assert finding["test"] == "T1" and finding["confirmed_on_holdout"] is True
        assert finding["holdout"]["trades"] > 0  # the user may see the holdout
        [note] = report.data["notes"]
        assert note == {"note": "N1", "topic": "Morning drift", "text": "It holds."}
    finally:
        await rt.stop()
