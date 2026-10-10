"""AI routing & billing — the 15 acceptance scenarios of the Smart Billing directive.

Everything around the providers is real: router, policy, usage ledger, checkpoints,
keystore (process-memory test backend), API endpoints. The providers are stand-ins and
labelled as such: ``FakeCli`` plays Claude Code's JSON output in-process, the
``fake_claude.py`` script plays the binary for the process-isolation test, and ``FakeApi``
plays the Anthropic SDK. No test talks to Anthropic or uses a real subscription.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from jarvis.ai import messages as msg
from jarvis.ai.checkpoint import RunCheckpoint
from jarvis.ai.classify import classify_cli, cli_error, parse_reset
from jarvis.ai.keys import ApiKeyStore
from jarvis.ai.plan import ClaudeCli, isolated_env
from jarvis.ai.router import RoleSpec, RoutedChatModel, SmartRouter
from jarvis.ai.store import AiStore
from jarvis.ai.types import AIPaused, Failure, ai_scope
from jarvis.api.app import create_app
from jarvis.events.bus import EventBus
from jarvis.events.types import Event
from jarvis.llm.base import ModelError, ModelReply, ToolCall, ToolDefinition, ToolOutcome, Usage
from jarvis.runtime import Runtime
from jarvis.settings import AiSettings
from jarvis.storage.database import Database
from jarvis.ultron.runner import Budget, run_agent
from tests.conftest import make_settings
from tests.test_ultron import Models, free_prices, happy_scripts, make_runtime  # noqa: F401
from tests.test_ultron import until as ultron_until

KEY = "sk-ant-api03-" + "y" * 40 + "WxYz"
FAKE_BINARY = Path(__file__).parent / "fixtures" / "ai" / "fake_claude.py"
CODING = RoleSpec("ultron", "forge", "coding", "claude-sonnet-5-5", None, 500, 60)
CHAT = RoleSpec("brain", "fast", "chat", "claude-sonnet-5-5", None, 500, 60)
PAID = {"monthly_budget_usd": 5.0, "per_mission_cap_usd": 2.0}


# -- stand-ins --------------------------------------------------------------------------------


def plan_ok(
    text: str = "plan says hi", calls: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "structured_output": {"text": text, "tool_calls": calls or []},
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "total_cost_usd": 0.01,
    }


PLAN_LIMIT = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "api_error_status": 429,
    "result": "You've hit your session limit · resets 3:45pm",
}


class FakeCli:
    """Claude Code's observable behaviour, in-process (test stand-in, never a real call)."""

    def __init__(self, method: str = "claude.ai", logged_in: bool = True) -> None:
        self.method = method
        self.logged_in = logged_in
        self.outcomes: list[Any] = []
        self.calls: list[tuple[list[str], str]] = []

    async def __call__(
        self, args: list[str], stdin: bytes | None, limit: float, cwd: Path
    ) -> tuple[int, bytes, bytes]:
        cmd = args[1:]
        if cmd[:1] == ["--version"]:
            return 0, b"2.1.296 (Claude Code)\n", b""
        if cmd[:2] == ["auth", "status"]:
            body = {"loggedIn": self.logged_in, "authMethod": self.method}
            return (0 if self.logged_in else 1), json.dumps(body).encode(), b""
        self.calls.append((cmd, (stdin or b"").decode()))
        item = self.outcomes.pop(0) if self.outcomes else plan_ok()
        if item == "network":
            return 1, b"", b"Error: getaddrinfo ENOTFOUND api.anthropic.com"
        return 0, json.dumps(item).encode(), b""


class FakeApiModel:
    def __init__(self, api: FakeApi, model: str) -> None:
        self._api = api
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def label(self) -> str:
        return self._model

    def user_message(self, blocks: list[str]) -> Any:
        return msg.user_message(blocks)

    def assistant_text(self, text: str) -> Any:
        return msg.assistant_text(text)

    def tool_results(self, outcomes: list[ToolOutcome]) -> Any:
        return msg.tool_results(outcomes)

    async def complete(
        self,
        *,
        system: str,
        messages: list[Any],
        tools: list[ToolDefinition],
        server_tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply:
        self._api.calls += 1
        self._api.seen.append(json.dumps(msg.jsonable(messages)))
        item = self._api.outcomes.pop(0) if self._api.outcomes else api_reply()
        if isinstance(item, ModelError):
            raise item
        assert isinstance(item, ModelReply)
        return item


class FakeApi:
    """The Anthropic SDK's observable behaviour (test stand-in)."""

    def __init__(self) -> None:
        self.outcomes: list[Any] = []
        self.calls = 0
        self.seen: list[str] = []
        self.keys: list[str] = []

    def factory(self, key: str, spec: RoleSpec, model: str, refusal: bool) -> FakeApiModel:
        self.keys.append(key[-4:])
        return FakeApiModel(self, model)


def api_reply(
    text: str = "api says hi",
    calls: list[ToolCall] | None = None,
    usage: Usage | None = None,
    model: str = "claude-sonnet-5-5",
) -> ModelReply:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    content += [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls or []
    ]
    return ModelReply(
        text=text,
        tool_calls=calls or [],
        stop_reason="tool_use" if calls else "end_turn",
        assistant_message={"role": "assistant", "content": content},
        model=model,
        usage=usage or Usage(input_tokens=1000, output_tokens=500),  # $0.007 on Sonnet 5.5
    )


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@asynccontextmanager
async def routed(
    tmp_path: Path,
    *,
    cli: Any = None,
    api: FakeApi | None = None,
    key: str | None = KEY,
    verify_error: ModelError | None = None,
    local_transport: Any = None,
) -> AsyncIterator[tuple[SmartRouter, FakeCli, FakeApi, list[float], list[Event], Clock]]:
    db = Database(tmp_path / "ai.db")
    await db.connect()
    bus = EventBus()
    events: list[Event] = []

    async def record(event: Event) -> None:
        events.append(event)

    bus.subscribe("*", record)
    keys = ApiKeyStore(memory=True, env_file=tmp_path / ".env", env_key=None)
    if key:
        keys.put(key)
    fake_cli = cli if cli is not None else FakeCli()
    fake_api = api or FakeApi()
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def verify(_key: str, _models: list[str]) -> None:
        if verify_error is not None:
            raise verify_error

    clock = Clock()
    router = SmartRouter(
        store=AiStore(db),
        bus=bus,
        settings=AiSettings(keystore="memory"),
        keys=keys,
        cli=ClaudeCli(
            path="" if not isinstance(fake_cli, str) else fake_cli,
            workdir=tmp_path / "cc",
            runner=None if isinstance(fake_cli, str) else fake_cli,
        ),
        models_for_check=["claude-sonnet-5-5"],
        api_factory=fake_api.factory,
        api_verify=verify,
        local_transport=local_transport,
        sleep=sleep,
        clock=clock,
    )
    await router.start(probe=False)
    try:
        yield router, fake_cli, fake_api, sleeps, events, clock
    finally:
        await router.stop()
        await db.close()


async def ask(router: SmartRouter, spec: RoleSpec = CODING, text: str = "hi") -> ModelReply:
    return await router.complete(spec, "You are a test.", [msg.user_message([text])], [])


async def billed(router: SmartRouter) -> float:
    return await router.store.billed("2026-10")


# -- 1. subscription available and permitted -------------------------------------------------


async def test_01_plan_first_and_no_api_charge(tmp_path: Path) -> None:
    async with routed(tmp_path) as (router, _cli, api, _, _, _):
        await router.enable_plan(personal_use=True)
        await router.approve_paid(PAID, confirm=True)  # even with paid approved: plan first
        reply = await ask(router)
        assert reply.route == "plan" and reply.text == "plan says hi"
        assert api.calls == 0 and await billed(router) == 0.0
        [row] = await router.store.recent_usage()
        assert row["provider"] == "plan" and row["billed"] == 0 and row["cost_usd"] == 0
        assert row["list_cost_usd"] == pytest.approx(0.01)  # informational only
        assert router.current()["route"] == "plan"


# -- 2. plan unavailable / unsupported → API only with the cost opt-in ------------------------


async def test_02_unsupported_plan_needs_the_paid_opt_in(tmp_path: Path) -> None:
    cli = FakeCli(method="api_key")  # Claude Code signed in with an API key, not a plan
    async with routed(tmp_path, cli=cli) as (router, cli, api, _, _, _):
        await router.enable_plan(personal_use=True)
        snap = await router.snapshot()
        assert snap["providers"]["plan"]["state"] == "NOT_SUPPORTED"
        with pytest.raises(AIPaused) as paused:
            await ask(router)
        assert "paid fallback not approved" in paused.value.message
        assert api.calls == 0 and cli.calls == []
        await router.approve_paid(PAID, confirm=True)
        reply = await ask(router)
        assert reply.route == "api" and api.calls == 1
        assert await billed(router) == pytest.approx(0.007)


async def test_02b_paid_fallback_needs_confirm_and_caps(tmp_path: Path) -> None:
    async with routed(tmp_path) as (router, _, _, _, _, _):
        with pytest.raises(ValueError):
            await router.approve_paid(PAID, confirm=False)
        with pytest.raises(ValueError):
            await router.approve_paid({"monthly_budget_usd": 5, "per_mission_cap_usd": 9}, True)
        assert router.config.paid.enabled is False
        await router.approve_paid(PAID, confirm=True)
        kinds = [a["kind"] for a in await router.store.approvals()]
        assert kinds == ["paid_approved"]


# -- 3. plan limit → checkpoint, authorised API fallback, status ------------------------------


TOOLS = [
    ToolDefinition("write_note", "Write a note.", {"type": "object", "properties": {}}),
    ToolDefinition("submit_work", "Finish.", {"type": "object", "properties": {}}),
]


class CountingBroker:
    def __init__(self) -> None:
        self.executed: list[str] = []

    async def execute(self, call: ToolCall) -> tuple[str, bool]:
        self.executed.append(call.id)
        return f"note written ({call.name})", False


async def free_budget() -> float:
    return 100.0


def run_kwargs(model: Any, broker: Any, checkpoint: RunCheckpoint | None = None) -> dict[str, Any]:
    async def gate() -> None:
        return None

    async def sink(_usage: Usage, _cost: float) -> None:
        return None

    return {
        "model": model,
        "system": "You are FORGE (test).",
        "opening": "Write one note, then submit.",
        "tools": TOOLS,
        "broker": broker,
        "validate": lambda _name, _raw: None,
        "max_rounds": 6,
        "gate": gate,
        "budget": Budget(None, 500, free_budget),
        "on_usage": sink,
        "final_tools": frozenset({"submit_work"}),
        "checkpoint": checkpoint,
    }


async def test_03_plan_limit_fails_over_to_the_approved_api_mid_run(tmp_path: Path) -> None:
    async with routed(tmp_path) as (router, cli, api, _, events, clock):
        await router.enable_plan(personal_use=True)
        await router.approve_paid(PAID, confirm=True)
        cli.outcomes = [plan_ok("writing", [{"name": "write_note", "input": {}}]), PLAN_LIMIT]
        api.outcomes = [api_reply("done", [ToolCall("toolu_api_1", "submit_work", {})])]
        model = RoutedChatModel(router, CODING)
        broker = CountingBroker()
        checkpoint = RunCheckpoint(router.store, "ultron:task-3")
        with ai_scope("ultron", mission_id="m-3", task_id="task-3", agent="forge"):
            result = await run_agent(**run_kwargs(model, broker, checkpoint))
        assert result.final_tool == "submit_work"
        assert len(broker.executed) == 1  # the note was written once
        plan = (await router.snapshot())["providers"]["plan"]
        assert plan["state"] == "LIMIT_REACHED" and plan["until"]
        assert any(e.payload.get("kind") == "failover" for e in events)
        assert any("Claude plan → Claude API" in e.message for e in events)
        # The run stayed on the API after the switch; the checkpoint holds the conversation.
        saved = await checkpoint.load()
        assert saved is not None and len(saved[0]) >= 4
        # New work starts on the API while the limit stands, on the plan after the reset.
        assert (await ask(router)).route == "api"
        clock.now = datetime.fromisoformat(plan["until"]) + timedelta(minutes=1)
        assert (await ask(router, text="new task")).route == "plan"


# -- 4. no paid opt-in → never a paid call -----------------------------------------------------


async def test_04_without_opt_in_the_api_is_never_called(tmp_path: Path) -> None:
    async with routed(tmp_path) as (router, cli, api, _, _, _):
        await router.enable_plan(personal_use=True)
        cli.outcomes = [PLAN_LIMIT]
        with pytest.raises(AIPaused) as paused:
            await ask(router)
        assert "Claude API: paid fallback not approved" in paused.value.reasons
        assert api.calls == 0 and api.keys == [] and await billed(router) == 0.0
        rows = await router.store.recent_usage()
        assert [(r["provider"], r["outcome"], r["failure"]) for r in rows] == [
            ("plan", "error", "plan_limit")
        ]


# -- 5. an API key in the environment can't override the plan login -------------------------


async def test_05_process_isolation_keeps_api_keys_away_from_claude_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "copied-token")
    (home / ".fake-claude.json").write_text(json.dumps({"method": "claude.ai"}))
    env = isolated_env({"ANTHROPIC_API_KEY": KEY, "PATH": "/usr/bin", "HOME": "/h"})
    assert "ANTHROPIC_API_KEY" not in env and env["HOME"] == "/h"
    async with routed(tmp_path, cli=str(FAKE_BINARY)) as (router, _, api, _, _, _):
        await router.enable_plan(personal_use=True)
        reply = await router.complete(CODING, "sys", [msg.user_message(["hi"])], [TOOLS[0]])
        assert reply.route == "plan" and reply.text == "Plan answer"
        log = [
            json.loads(line) for line in (home / ".fake-claude-log.jsonl").read_text().splitlines()
        ]
        call = next(entry for entry in log if "-p" in entry["args"])
        for secret in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            assert secret not in call["env"]
        assert KEY not in json.dumps(log)
        assert "--safe-mode" in call["args"] and "--tools" in call["args"]
        assert api.calls == 0
    # A Claude Code login that would bill the API is refused before any model call.
    (home / ".fake-claude.json").write_text(json.dumps({"method": "api_key"}))
    (home / ".fake-claude-log.jsonl").unlink()
    async with routed(tmp_path / "b", cli=str(FAKE_BINARY)) as (router, _, _, _, _, _):
        await router.enable_plan(personal_use=True)
        with pytest.raises(AIPaused):
            await ask(router)
        raw = (home / ".fake-claude-log.jsonl").read_text()
        assert '"-p"' not in raw


# -- 6. Max/Team API credits vs plan limits: no invented balances ------------------------------


async def test_06_credits_and_plan_limits_are_not_invented(tmp_path: Path) -> None:
    async with routed(tmp_path) as (router, _, _, _, _, _):
        snap = await router.snapshot()
        assert "Max or Team" in snap["credits_note"]
        assert "can't read your credit balance" in snap["credits_note"]
        assert snap["providers"]["plan"]["usage"].startswith("Not retrievable")
        text = json.dumps(snap)
        assert "balance_usd" not in text and "remaining_tokens" not in text
        assert snap["providers"]["api"]["budget_left_usd"] is None  # no budget approved yet
        await router.approve_paid(PAID, confirm=True)
        snap = await router.snapshot()
        assert snap["providers"]["api"]["budget_left_usd"] == 5.0  # our own budget, not a balance


# -- 7. paid budget exceeded → new paid calls blocked, the running one booked honestly --------


async def test_07_budget_reached_blocks_new_paid_calls(tmp_path: Path) -> None:
    async with routed(tmp_path) as (router, _, api, _, events, _):
        await router.approve_paid(
            {"monthly_budget_usd": 0.02, "per_mission_cap_usd": 0.02, "warn_at": [50, 80, 100]},
            confirm=True,
        )
        expensive = Usage(input_tokens=1000, output_tokens=3000)  # $0.032 — above the budget
        api.outcomes = [api_reply(usage=expensive)]
        assert (await ask(router)).route == "api"
        assert await billed(router) == pytest.approx(0.032)  # the call that ran is booked
        warnings = [
            e.payload.get("kind") for e in events if e.payload.get("kind") == "budget_warning"
        ]
        assert len(warnings) == 3  # 50 %, 80 %, 100 % — once each
        assert router.config.paid.enabled is False  # stop at budget: approval needed again
        with pytest.raises(AIPaused):
            await ask(router)
        assert api.calls == 1
        assert "paid_disabled_at_budget" in [a["kind"] for a in await router.store.approvals()]


async def test_07b_a_call_that_could_exceed_the_cap_never_starts(tmp_path: Path) -> None:
    async with routed(tmp_path) as (router, _, api, _, _, _):
        await router.approve_paid({"monthly_budget_usd": 1.0, "per_mission_cap_usd": 0.01}, True)
        with ai_scope("ultron", mission_id="m-7"):
            assert (await ask(router)).route == "api"  # $0.007 booked to m-7
            with pytest.raises(AIPaused) as paused:
                await ask(router)
        assert "per-mission cap" in paused.value.message
        assert api.calls == 1


# -- 8. API credit exhausted → pause / local, no endless loop ----------------------------------


def ollama_transport() -> httpx2.MockTransport:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/tags":
            return httpx2.Response(200, json={"models": [{"name": "llama3.2:latest"}]})
        return httpx2.Response(
            200,
            json={"message": {"role": "assistant", "content": "local hello"}, "eval_count": 3},
        )

    return httpx2.MockTransport(handle)


async def test_08_credit_exhausted_pauses_without_a_retry_loop(tmp_path: Path) -> None:
    async with routed(tmp_path, local_transport=ollama_transport()) as (router, _, api, _, _, _):
        await router.approve_paid(PAID, confirm=True)
        api.outcomes = [ModelError("billing", "The Anthropic account has no credit left.")]
        with pytest.raises(AIPaused):
            await ask(router)
        assert api.calls == 1
        assert (await router.snapshot())["providers"]["api"]["state"] == "INSUFFICIENT_CREDIT"
        with pytest.raises(AIPaused):
            await ask(router)
        assert api.calls == 1  # no loop: the route rests
        await router.set_local(True, "http://127.0.0.1:11434", "llama3.2")
        reply = await ask(router, CHAT)
        assert reply.route == "local" and reply.text == "local hello"
        with pytest.raises(AIPaused) as paused:  # coding is never handed to the local model
            await ask(router, CODING)
        assert "not suitable" in paused.value.message
        with pytest.raises(ValueError):
            await router.set_local(True, "http://example.com:11434", "x")  # loopback only


# -- 9. network failure → bounded retries, no paid switch, no double charge -------------------


async def test_09_temporary_errors_retry_boundedly_and_never_switch_to_paid(
    tmp_path: Path,
) -> None:
    async with routed(tmp_path) as (router, cli, api, sleeps, _, _):
        await router.enable_plan(personal_use=True)
        await router.approve_paid(PAID, confirm=True)
        cli.outcomes = ["network", "network", plan_ok("third time")]
        reply = await ask(router)
        assert reply.route == "plan" and reply.text == "third time"
        assert sleeps == [2.0, 4.0] and api.calls == 0
        cli.outcomes = ["network", "network", "network"]
        with pytest.raises(AIPaused) as paused:
            await ask(router)
        assert paused.value.temporary and api.calls == 0  # never paid because of a hiccup
        assert (await router.snapshot())["providers"]["plan"]["state"] == "UNAVAILABLE"
        rows = await router.store.recent_usage()
        assert sum(r["billed"] for r in rows) == 0


async def test_09b_api_overload_is_retried_and_charged_once(tmp_path: Path) -> None:
    async with routed(tmp_path) as (router, _, api, sleeps, _, _):
        await router.approve_paid(PAID, confirm=True)
        api.outcomes = [
            ModelError("overloaded", "The model is temporarily overloaded.", retryable=True),
            ModelError("rate_limited", "Slow down.", retryable=True, retry_after=7),
            api_reply(),
        ]
        assert (await ask(router)).route == "api"
        assert api.calls == 3 and sleeps == [2.0, 7.0]  # Retry-After honoured
        assert await billed(router) == pytest.approx(0.007)


# -- 10. restart / pause mid-mission → resume from checkpoint, tools not repeated -------------


class Scripted:
    """A model that answers from a list (test stand-in); AIPaused simulates a lost route."""

    def __init__(self, steps: list[Any]) -> None:
        self.steps = steps
        self.model_id = "scripted"
        self.label = "scripted"

    def user_message(self, blocks: list[str]) -> Any:
        return msg.user_message(blocks)

    def assistant_text(self, text: str) -> Any:
        return msg.assistant_text(text)

    def tool_results(self, outcomes: list[ToolOutcome]) -> Any:
        return msg.tool_results(outcomes)

    async def complete(self, **_: Any) -> ModelReply:
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, ModelReply)
        return step


async def test_10_a_paused_run_resumes_without_repeating_a_tool(tmp_path: Path) -> None:
    async with routed(tmp_path) as (router, _, _, _, _, _):
        checkpoint = RunCheckpoint(router.store, "ultron:task-10")
        broker = CountingBroker()
        first = Scripted(
            [
                api_reply("note", [ToolCall("toolu_1", "write_note", {})]),
                AIPaused("no route"),
            ]
        )
        with pytest.raises(AIPaused):
            await run_agent(**run_kwargs(first, broker, checkpoint))
        assert broker.executed == ["toolu_1"]
        second = Scripted([api_reply("done", [ToolCall("toolu_2", "submit_work", {})])])
        result = await run_agent(**run_kwargs(second, broker, checkpoint))
        assert result.final_tool == "submit_work"
        assert broker.executed == ["toolu_1"]  # resumed after the note: not written twice
        # A crash between "the model asked" and "the results were saved": the ledger answers.
        await checkpoint.clear()
        await checkpoint.save(
            [
                msg.user_message(["go"]),
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "toolu_9", "name": "write_note", "input": {}}
                    ],
                },
            ],
            1,
        )
        await checkpoint.record("toolu_9", "write_note", {}, "note written (earlier)", False)
        third = Scripted([api_reply("done", [ToolCall("toolu_10", "submit_work", {})])])
        result = await run_agent(**run_kwargs(third, broker, checkpoint))
        assert result.final_tool == "submit_work"
        assert "toolu_9" not in broker.executed  # answered from the ledger


class PauseOnce:
    """Wraps an agent's scripted model: the first call finds no AI route (test stand-in)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.paused = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def complete(self, **kwargs: Any) -> ModelReply:
        if not self.paused:
            self.paused = True
            raise AIPaused("AI work is paused — Claude plan: usage limit reached")
        result: ModelReply = await self.inner.complete(**kwargs)
        return result


async def test_10b_ultron_mission_pauses_for_ai_and_resumes_by_itself(
    make_runtime: Any,  # noqa: F811 - the fixture from tests.test_ultron
) -> None:
    models = Models(happy_scripts())
    models.models["forge"] = PauseOnce(models.models["forge"])  # type: ignore[assignment]
    rt = await make_runtime(models)
    free_prices(rt)
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    blocked = await ultron_until(rt, created["id"], "BLOCKED")
    assert blocked["blocker"]["kind"] == "ai_route"
    forge = next(t for t in blocked["tasks"] if t["owner"] == "forge")
    assert forge["state"] == "READY" and forge["attempts"] == 0  # a pause isn't an attempt
    await rt.ultron.on_ai_available()  # what the router calls when a route is back
    done = await ultron_until(rt, created["id"], "WAITING_APPROVAL", seconds=60)
    assert done["state"] == "WAITING_APPROVAL"


# -- 11. Databento downloads keep their own quote and approval -------------------------------


async def test_11_ai_failover_never_buys_market_data(tmp_path: Path) -> None:
    from tests.test_quantlab_missions import IDEA, runtime, until

    rt = runtime(tmp_path)
    await rt.start()
    try:
        from jarvis.quantlab.hub import fixture

        await rt.sources.add_text(IDEA, note="Only the short side, as in the text.")
        [listed] = await rt.quant_missions.missions()
        m = await until(rt, listed["id"], "WAITING_USER")
        await rt.quant_missions.answer(m["id"], {"accept_defaults": True})
        m = await until(rt, m["id"], "WAITING_USER")
        await rt.hub.connect(fixture.VALID_KEY)
        m = await until(rt, m["id"], "WAITING_APPROVAL")
        quote_id = m["waiting"]["quote_id"]
        # Routing changes — paid fallback approved, then switched off — touch no purchase.
        await rt.ai.approve_paid(PAID, confirm=True)
        await rt.ai.disable_paid()
        await rt.ai.check("plan")
        after = await rt.quant_missions.mission(m["id"])
        assert after["state"] == "WAITING_APPROVAL" and after["refs"].get("job_id") is None
        quote = next(q for q in await rt.hub.quotes() if q["id"] == quote_id)
        assert quote["status"] != "APPROVED"
        assert all(r["consumer"] != "vector" for r in await rt.ai.store.recent_usage())
    finally:
        await rt.stop()


# -- 12. every agent uses the same router; caps and gates hold --------------------------------


async def test_12_every_consumer_goes_through_the_one_router(tmp_path: Path) -> None:
    api = FakeApi()
    rt = Runtime(make_settings(tmp_path), ai_api_factory=api.factory, key_verifier=accept_key)
    await rt.start()
    try:
        assert rt._ultron_model("forge") is None  # nothing set up: services say what they need
        rt.ai_keys.put(KEY)
        await rt.ai.approve_paid(PAID, confirm=True)
        await rt.refresh_brain()
        models = {
            "ultron": rt._ultron_model("forge"),
            "quantlab": rt._quant_model("atlas"),
            "architect": rt._architect_model(),
            "learning": rt._research_model(),
            "bots": rt._bot_model(),
            "brain": rt.brain._models.fast,
        }
        for consumer, model in models.items():
            assert isinstance(model, RoutedChatModel), consumer
            assert model._router is rt.ai
            with ai_scope(consumer, mission_id=f"m-{consumer}"):
                reply = await model.complete(
                    system="s", messages=[msg.user_message(["hi"])], tools=[]
                )
            assert reply.route == "api"
        consumers = {r["consumer"] for r in await rt.ai.store.recent_usage()}
        assert consumers == set(models)
        # The router is the only place keys are read: one key, one factory, one ledger.
        assert set(api.keys) == {"WxYz"}
    finally:
        await rt.stop()


async def test_12b_permission_gates_hold_on_the_plan_route(tmp_path: Path) -> None:
    """A tool call that arrives through the Claude plan still needs the owner's approval."""
    cli = FakeCli()
    shell = {"name": "open_application", "input": {"name": "powershell", "purpose": "A shell"}}
    cli.outcomes = [plan_ok("Opening it.", [shell]), plan_ok("Done.")]
    rt = Runtime(make_settings(tmp_path), ai_cli_runner=cli)
    await rt.start()
    try:
        await rt.ai.enable_plan(personal_use=True)
        await rt.refresh_brain()
        assert rt.brain.available
        task = asyncio.create_task(rt.core.handle("give me a shell"))
        for _ in range(200):
            if rt.permissions.pending():
                break
            await asyncio.sleep(0.02)
        [pending] = rt.permissions.pending()  # the gate stops the plan's tool call
        assert len(cli.calls) == 1
        await rt.permissions.approve(pending.id)
        await task
        await rt.core.wait_idle()
        assert len(cli.calls) == 2  # the result went back to the plan, not to the API
        assert '<tool_result id="toolu_plan_' in cli.calls[1][1]
        assert await rt.ai.store.billed("2026-10") == 0.0
    finally:
        await rt.stop()


# -- 13. AI offline: data, backtests and reports still work -------------------------------------


async def test_13_without_any_ai_route_backtests_and_reports_run(tmp_path: Path) -> None:
    from tests.test_quantlab_research import dataset, finish, small_spec

    settings = make_settings(tmp_path)
    settings = settings.model_copy(
        update={
            "quantlab": settings.quantlab.model_copy(
                update={"provider": "fixture", "keystore": "memory"}
            )
        }
    )
    rt = Runtime(settings)
    await rt.start()
    try:
        assert rt.ai.current()["route"] == "paused" and not rt.brain.available
        ds = await dataset(rt)
        strategy = await rt.research.create_strategy(small_spec())
        run = await rt.research.start_run(strategy["versions"][0]["id"], ds["id"], kind="backtest")
        done = await finish(rt, run)
        assert done["status"] == "COMPLETED"
        assert done["summary"]["segments"]["insample"]["trades"] > 0
        assert "SYNTHETIC" in await rt.research.report(run["id"])
        assert await rt.ai.store.recent_usage() == []  # no model was involved
    finally:
        await rt.stop()


# -- 14. the billing status shown is the real one -------------------------------------------------


async def accept_key(_key: str, _models: list[str]) -> None:
    """Stands in for the free model check against Anthropic (no network in tests)."""


def test_14_status_endpoints_reflect_real_checks_and_events(tmp_path: Path) -> None:
    api = FakeApi()
    runtime = Runtime(make_settings(tmp_path), ai_api_factory=api.factory, key_verifier=accept_key)
    with TestClient(create_app(runtime=runtime)) as client:
        status = client.get("/ai/status").json()
        assert status["providers"]["plan"]["state"] == "NOT_INSTALLED"  # no binary here
        assert status["providers"]["api"]["state"] == "NO_KEY"
        assert status["providers"]["local"]["state"] == "DISABLED"
        assert status["current"]["route"] == "paused"
        assert status["keystore_test_only"] is True  # labelled, never shown as a real keychain
        assert client.post("/ai/api-key", json={"api_key": "nope"}).status_code == 422
        saved = client.post("/ai/api-key", json={"api_key": KEY}).json()
        assert saved["providers"]["api"]["state"] == "CONNECTED"
        assert saved["providers"]["api"]["key_hint"] == "WxYz"
        assert saved["current"]["route"] == "paused"  # a key alone isn't permission to spend
        evil = {"origin": "https://evil.example"}
        body = {**PAID, "confirm": True}
        assert client.post("/ai/paid", json=body, headers=evil).status_code == 403
        assert client.post("/ai/paid", json=PAID).status_code == 422
        approved = client.post("/ai/paid", json=body).json()
        assert approved["current"]["route"] == "api"
        assert approved["providers"]["api"]["paid"]["monthly_budget_usd"] == 5.0
        usage = client.get("/ai/usage").json()
        assert {a["kind"] for a in usage["approvals"]} >= {"api_key_saved", "paid_approved"}
        assert any(e["kind"] == "available" for e in usage["events"])


# -- 15. no key, token or secret header anywhere -------------------------------------------------


async def test_15_secrets_never_reach_logs_events_ledgers_or_prompts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    async with routed(tmp_path) as (router, cli, api, _, events, _):
        await router.enable_plan(personal_use=True)
        await router.approve_paid(PAID, confirm=True)
        cli.outcomes = [PLAN_LIMIT]
        await ask(router, text="what is my key?")
        await router.connect_key(KEY)
        snapshot = json.dumps(await router.snapshot())
        everything = [
            snapshot,
            caplog.text,
            json.dumps([e.model_dump(mode="json") for e in events]),
            json.dumps(await router.store.recent_usage()),
            json.dumps(await router.store.events()),
            json.dumps(await router.store.approvals()),
            json.dumps(cli.calls),
            "\n".join(api.seen),
        ]
        for blob in everything:
            assert KEY not in blob
            assert KEY[:-4] not in blob
        assert "WxYz" in snapshot  # only the last four characters are ever shown
        assert os.environ.get("ANTHROPIC_API_KEY") != KEY


# -- classification ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "status", "failure"),
    [
        ("You've hit your session limit · resets 3:45pm", 429, Failure.PLAN_LIMIT),
        ("You've hit your weekly limit · resets Mon 12:00am", None, Failure.PLAN_LIMIT),
        (
            "You've hit your monthly spend limit · raise it at https://claude.ai",
            None,
            Failure.PLAN_LIMIT,
        ),
        ("Claude AI usage limit reached|1760000000", None, Failure.PLAN_LIMIT),
        ("Not logged in · Please run /login", None, Failure.AUTH),
        ("Login expired · Please run /login", None, Failure.AUTH),
        ("Credit balance is too low", None, Failure.CREDIT),
        ("Your organization has disabled Claude subscription access", None, Failure.POLICY),
        (
            "API Error: Request rejected (429) · this may be a temporary capacity issue.",
            429,
            Failure.RATE_LIMIT,
        ),
        (
            "API Error: Server is temporarily limiting requests (not your usage limit)",
            None,
            Failure.RATE_LIMIT,
        ),
        ("API Error: Repeated 529 Overloaded errors.", 529, Failure.OVERLOADED),
        ("getaddrinfo ENOTFOUND api.anthropic.com", None, Failure.NETWORK),
        ("something nobody documented", None, Failure.UNKNOWN),
    ],
)
def test_claude_code_messages_are_classified(
    text: str, status: int | None, failure: Failure
) -> None:
    assert classify_cli(text, status) is failure


def test_reset_times_are_read_not_guessed() -> None:
    now = datetime(2026, 10, 10, 14, 0, tzinfo=UTC)
    assert parse_reset("resets 3:45pm", now) == now.replace(hour=15, minute=45)
    assert parse_reset("resets 1:15pm", now) == (now + timedelta(days=1)).replace(
        hour=13, minute=15
    )
    monday = parse_reset("resets Mon 12:00am", now)
    assert monday is not None and monday.weekday() == 0 and monday.hour == 0
    assert parse_reset("limit reached|1760000000", now) == datetime.fromtimestamp(
        1760000000, tz=UTC
    )
    assert parse_reset("no time here", now) is None
    error = cli_error("You've hit your session limit · resets 3:45pm", 429, now=now)
    assert error.code == "plan_limit" and error.retry_after == pytest.approx(105 * 60)
