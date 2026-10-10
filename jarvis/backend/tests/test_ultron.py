"""ULTRON: missions end to end, with scripted model replies and everything else real —
git worktrees, sandboxed test runs, policy, approvals, persistence, pause/stop, recovery."""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.api.app import create_app
from jarvis.llm.base import ModelReply, ToolCall, ToolDefinition, ToolOutcome, Usage
from jarvis.runtime import Runtime
from jarvis.settings import Settings
from jarvis.ultron import plan as planning
from jarvis.ultron import sandbox
from jarvis.ultron.policy import Command, PolicyError, check_command, check_write, glob_match
from tests.conftest import Recorder, make_settings
from tests.fakes import ScriptedChatModel

# Script building blocks ---------------------------------------------------------------------


def calls(*items: tuple[str, dict[str, Any]], usage: Usage | None = None) -> ModelReply:
    blocks = []
    tool_calls = []
    for i, (name, args) in enumerate(items):
        call_id = f"toolu_{name}_{i}_{id(args)}"
        tool_calls.append(ToolCall(id=call_id, name=name, input=args))
        blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": args})
    return ModelReply(
        text="",
        tool_calls=tool_calls,
        stop_reason="tool_use",
        assistant_message={"role": "assistant", "content": blocks},
        model="claude-test",
        usage=usage or Usage(input_tokens=1000, output_tokens=200),
    )


PLAN: dict[str, Any] = {
    "title": "Slugify utility",
    "objective": "A tested slugify(text) function in a new Python package.",
    "in_scope": ["slugify function", "unit tests"],
    "out_of_scope": ["unicode transliteration"],
    "assumptions": ["ASCII input"],
    "success_criteria": [
        {"criterion": "All unit tests pass", "check": ["python", "-m", "pytest", "-q"]},
        {"criterion": "slugify turns 'Hello World' into 'hello-world'"},
    ],
    "tasks": [
        {
            "key": "spec",
            "title": "Specify slugify",
            "owner": "axiom",
            "objective": "Write the specification for slugify into docs/SPEC.md.",
            "deliverables": ["docs/SPEC.md"],
            "done_when": ["docs/SPEC.md defines signature, behaviour and tests"],
            "allowed_paths": ["docs/**"],
        },
        {
            "key": "impl",
            "title": "Implement slugify",
            "owner": "forge",
            "depends_on": ["spec"],
            "objective": "Implement textutil.slugify with tests, following docs/SPEC.md.",
            "deliverables": ["textutil/__init__.py", "tests/test_textutil.py"],
            "done_when": ["pytest passes"],
            "allowed_paths": ["textutil/**", "tests/**"],
            "checks": [["python", "-m", "pytest", "-q"]],
        },
        {
            "key": "verify",
            "title": "Independent review",
            "owner": "sentinel",
            "depends_on": ["impl"],
            "objective": "Independently verify slugify against the spec.",
            "done_when": ["review passes with evidence"],
        },
    ],
}

SPEC = "# slugify\n\nslugify(text: str) -> str: lowercase, spaces become '-', strip ends.\n"
BUGGY = "def slugify(text):\n    return text.lower()\n"
FIXED = "def slugify(text):\n    return '-'.join(text.strip().lower().split())\n"
TESTS = (
    "from textutil import slugify\n\n\n"
    "def test_basic():\n    assert slugify('Hello World') == 'hello-world'\n\n\n"
    "def test_strip():\n    assert slugify('  A  b ') == 'a-b'\n"
)
OK_CRITERIA = [{"criterion": "pytest passes", "met": True, "evidence": "2 passed"}]


def submit(summary: str = "done") -> ModelReply:
    return calls(("submit_result", {"summary": summary, "criteria": OK_CRITERIA}))


def review(verdict: str, finding: str = "") -> tuple[ModelReply, ModelReply]:
    findings = [{"severity": "major", "issue": finding, "evidence": "probe"}] if finding else []
    return calls(
        ("read_file", {"path": "textutil/__init__.py"}),
        ("run_command", {"argv": ["python", "-m", "pytest", "-q"]}),
    ), calls(
        (
            "submit_review",
            {
                "verdict": verdict,
                "summary": f"review: {verdict}",
                "findings": findings,
                "criteria": [
                    {
                        "criterion": "slugify turns 'Hello World' into 'hello-world'",
                        "met": verdict == "pass",
                        "evidence": "pytest + read",
                    }
                ],
            },
        )
    )


def happy_scripts() -> dict[str, list[Any]]:
    look, verdict = review("pass")
    return {
        "jarvis": [calls(("submit_plan", PLAN))],
        "axiom": [calls(("write_file", {"path": "docs/SPEC.md", "content": SPEC})), submit("spec")],
        "forge": [
            # attempt 1: buggy code, plus a write outside the allowed paths (must be refused)
            calls(
                ("write_file", {"path": "textutil/__init__.py", "content": BUGGY}),
                ("write_file", {"path": "tests/test_textutil.py", "content": TESTS}),
                ("write_file", {"path": "../escape.txt", "content": "nope"}),
                ("write_file", {"path": "README.md", "content": "overwritten"}),
            ),
            submit("first try"),
            # attempt 2: the runtime's failing check came back as feedback
            calls(("write_file", {"path": "textutil/__init__.py", "content": FIXED})),
            submit("fixed"),
        ],
        "sentinel": [look, verdict],
    }


class Models:
    """Per-agent scripted models, created fresh for each runtime."""

    def __init__(self, scripts: dict[str, list[Any]]) -> None:
        self.models = {agent: ScriptedChatModel(steps) for agent, steps in scripts.items()}

    def __call__(self, agent: str) -> Any:
        return self.models.get(agent)


def ultron_settings(tmp_path: Path, **ultron: Any) -> Settings:
    settings = make_settings(tmp_path)
    # Scripted test replies cost nothing; the budget logic is tested separately.
    values = {"export_dir": str(tmp_path / "exports"), "scripted_model": "", **ultron}
    return settings.model_copy(update={"ultron": settings.ultron.model_copy(update=values)})


@pytest.fixture
async def make_runtime(tmp_path: Path) -> AsyncIterator[Callable[..., Any]]:
    started: list[Runtime] = []

    async def factory(models: Callable[[str], Any], **kwargs: Any) -> Runtime:
        settings = kwargs.pop("settings", None) or ultron_settings(tmp_path)
        rt = Runtime(settings, ultron_model=models, **kwargs)
        await rt.start()
        started.append(rt)
        return rt

    yield factory
    for rt in started:
        with contextlib.suppress(Exception):  # some tests stop a runtime themselves
            await rt.stop()


async def until(rt: Runtime, mission_id: str, *states: str, seconds: float = 30) -> dict[str, Any]:
    async with asyncio.timeout(seconds):
        while True:
            mission = await rt.ultron.mission(mission_id)
            if mission["state"] in states:
                return mission
            await asyncio.sleep(0.05)


def free_prices(rt: Runtime) -> None:
    """Scripted replies are free: no price → no spend (budget tested separately)."""
    rt.ultron._settings = rt.ultron._settings.model_copy(update={"scripted_model": "test"})


# The acceptance demo -------------------------------------------------------------------------


async def test_full_mission_plan_spec_code_retry_review_export(
    make_runtime: Callable[..., Any], tmp_path: Path
) -> None:
    models = Models(happy_scripts())
    rt = await make_runtime(models)
    free_prices(rt)
    recorder = Recorder(rt.bus)
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    mission = await until(rt, created["id"], "WAITING_APPROVAL", "BLOCKED", "FAILED")
    assert mission["state"] == "WAITING_APPROVAL", mission["blocker"]

    tasks = {t["key"]: t for t in mission["tasks"]}
    assert [t["state"] for t in tasks.values()] == ["COMPLETE"] * 3
    assert tasks["impl"]["attempts"] == 2  # the runtime's own check failed attempt 1
    assert tasks["impl"]["result"]["checks"][0]["passed"] is True
    assert tasks["verify"]["result"]["accepted"] is True
    assert tasks["verify"]["result"]["runtime_checks_passed"] is True

    kinds = [a["kind"] for a in mission["artifacts"]]
    assert {"charter", "spec", "patch", "checks", "review", "report"} <= set(kinds)
    first_checks = next(a for a in mission["artifacts"] if a["name"].endswith("attempt1.log"))
    log = await rt.ultron.artifact(first_checks["id"])
    assert "exit 1" in log["content"] and log["intact"]

    # Out-of-scope writes were refused and audited; nothing escaped the worktree.
    activity = await rt.ultron.activity(created["id"], 1000)
    denied = [e for e in activity if e["kind"] == "policy.denied"]
    assert len(denied) == 2 and all(e["data"]["category"] == "workspace.write" for e in denied)
    workspace = Path(mission["workspace"])
    assert not (workspace / "tasks" / "escape.txt").exists()
    repo_readme = (workspace / "repo" / "README.md").read_text()
    assert "overwritten" not in repo_readme

    # Every agent's run is on record.
    agents = sorted({r["agent"] for r in mission["runs"]})
    assert agents == ["axiom", "forge", "jarvis", "sentinel"]
    assert any(e.type == "ultron.task.changed" for e in recorder.events)

    # The export is an approval-gated effect.
    (approval,) = mission["approvals"]
    assert approval["state"] == "pending" and approval["category"] == "export.project"
    decided = await rt.ultron.decide(approval["id"], True)
    assert decided["state"] == "executed"
    destination = Path(approval["effect"]["destination"])
    assert (destination / "textutil" / "__init__.py").read_text() == FIXED
    assert (destination / "docs" / "SPEC.md").read_text() == SPEC
    final = await rt.ultron.mission(created["id"])
    assert final["state"] == "COMPLETE"
    report = final["report"]
    assert {c["status"] for c in report["criteria"]} <= {"verified", "reviewed"}
    assert report["criteria"][0]["status"] == "verified"
    with pytest.raises(Exception, match="Already"):
        await rt.ultron.decide(approval["id"], True)  # no double execution


async def test_mission_survives_a_restart(make_runtime: Callable[..., Any], tmp_path: Path) -> None:
    rt = await make_runtime(Models(happy_scripts()))
    free_prices(rt)
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    await until(rt, created["id"], "WAITING_APPROVAL")
    before = await rt.ultron.mission(created["id"])
    await rt.stop()
    again = await make_runtime(Models({}))
    after = await again.ultron.mission(created["id"])
    assert after["state"] == before["state"]
    assert [t["state"] for t in after["tasks"]] == [t["state"] for t in before["tasks"]]
    assert len(after["artifacts"]) == len(before["artifacts"])


# Gated models: pause, stop, crash recovery ---------------------------------------------------


class Gated:
    """Holds every model call until released — to catch the runtime mid-task."""

    def __init__(self, inner: ScriptedChatModel) -> None:
        self.inner = inner
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    @property
    def model_id(self) -> str:
        return "claude-test"

    @property
    def label(self) -> str:
        return "Test"

    async def complete(
        self, *, system: str, messages: list[Any], tools: list[ToolDefinition]
    ) -> ModelReply:
        self.calls += 1
        self.waiting.set()
        await self.release.wait()
        return await self.inner.complete(system=system, messages=messages, tools=tools)

    def user_message(self, blocks: list[str]) -> Any:
        return self.inner.user_message(blocks)

    def assistant_text(self, text: str) -> Any:
        return self.inner.assistant_text(text)

    def tool_results(self, outcomes: list[ToolOutcome]) -> Any:
        return self.inner.tool_results(outcomes)


async def test_pause_holds_agents_and_resume_continues(make_runtime: Callable[..., Any]) -> None:
    scripts = happy_scripts()
    models = Models(scripts)
    gated = Gated(models.models["forge"])
    models.models["forge"] = gated  # type: ignore[assignment]
    rt = await make_runtime(models)
    free_prices(rt)
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    await asyncio.wait_for(gated.waiting.wait(), 20)  # FORGE is mid-call
    paused = await rt.ultron.pause(created["id"])
    assert paused["state"] == "PAUSED"
    gated.release.set()  # the in-flight call returns …
    await asyncio.sleep(0.5)
    assert gated.calls == 1  # … but no further step starts while paused
    resumed = await rt.ultron.resume(created["id"])
    assert resumed["state"] == "RUNNING"
    mission = await until(rt, created["id"], "WAITING_APPROVAL", "BLOCKED")
    assert mission["state"] == "WAITING_APPROVAL"


async def test_stop_cancels_running_work(make_runtime: Callable[..., Any]) -> None:
    models = Models(happy_scripts())
    gated = Gated(models.models["forge"])
    models.models["forge"] = gated  # type: ignore[assignment]
    rt = await make_runtime(models)
    free_prices(rt)
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    await asyncio.wait_for(gated.waiting.wait(), 20)
    cancelled = await rt.ultron.cancel(created["id"])
    assert cancelled["state"] == "CANCELLED"
    states = {t["key"]: t["state"] for t in cancelled["tasks"]}
    assert states["impl"] == "CANCELLED" and states["verify"] == "CANCELLED"
    assert "impl" in cancelled["blocker"]["message"]  # in-flight work is named
    gated.release.set()
    await asyncio.sleep(0.3)
    assert gated.calls == 1


async def test_emergency_stop_all(make_runtime: Callable[..., Any]) -> None:
    models = Models(happy_scripts())
    gated = Gated(models.models["forge"])
    models.models["forge"] = gated  # type: ignore[assignment]
    rt = await make_runtime(models)
    free_prices(rt)
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    await asyncio.wait_for(gated.waiting.wait(), 20)
    assert await rt.ultron.stop_all() == 1
    assert (await rt.ultron.mission(created["id"]))["state"] == "CANCELLED"


async def test_interrupted_task_runs_again_after_restart(
    make_runtime: Callable[..., Any],
) -> None:
    models = Models(happy_scripts())
    gated = Gated(models.models["forge"])
    models.models["forge"] = gated  # type: ignore[assignment]
    rt = await make_runtime(models)
    free_prices(rt)
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    await asyncio.wait_for(gated.waiting.wait(), 20)
    await rt.stop()  # JARVIS quits while FORGE works

    scripts = happy_scripts()
    fresh = Models({"forge": scripts["forge"], "sentinel": scripts["sentinel"]})
    again = await make_runtime(fresh)
    free_prices(again)
    mission = await until(again, created["id"], "WAITING_APPROVAL", "BLOCKED")
    assert mission["state"] == "WAITING_APPROVAL"
    impl = next(t for t in mission["tasks"] if t["key"] == "impl")
    assert impl["attempts"] == 3  # interrupted + failed check + fixed
    assert any(r["state"] == "INTERRUPTED" for r in mission["runs"])


# Review, retries, budget ---------------------------------------------------------------------


async def test_sentinel_rejection_sends_work_back_to_forge(
    make_runtime: Callable[..., Any],
) -> None:
    scripts = happy_scripts()
    scripts["forge"] = [
        calls(
            ("write_file", {"path": "textutil/__init__.py", "content": FIXED}),
            ("write_file", {"path": "tests/test_textutil.py", "content": TESTS}),
        ),
        submit("v1"),
        calls(
            (
                "edit_file",
                {
                    "path": "textutil/__init__.py",
                    "old": "def slugify(text):",
                    "new": "def slugify(text: str) -> str:",
                },
            )
        ),
        submit("typed"),
    ]
    look1, reject = review("fail", "no type hints although the spec asks for them")
    look2, accept = review("pass")
    scripts["sentinel"] = [look1, reject, look2, accept]
    rt = await make_runtime(Models(scripts))
    free_prices(rt)
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    mission = await until(rt, created["id"], "WAITING_APPROVAL", "BLOCKED")
    assert mission["state"] == "WAITING_APPROVAL"
    tasks = {t["key"]: t for t in mission["tasks"]}
    assert tasks["impl"]["attempts"] == 2 and tasks["verify"]["attempts"] == 2
    reviews = [a for a in mission["artifacts"] if a["kind"] == "review"]
    first = json.loads((await rt.ultron.artifact(reviews[0]["id"]))["content"])
    assert first["accepted"] is False and first["findings"]


async def test_exhausted_retries_block_and_resume_grants_more(
    make_runtime: Callable[..., Any],
) -> None:
    scripts = happy_scripts()
    buggy = calls(
        ("write_file", {"path": "textutil/__init__.py", "content": BUGGY}),
        ("write_file", {"path": "tests/test_textutil.py", "content": TESTS}),
    )
    scripts["forge"] = [
        buggy,
        submit(),
        calls(("read_file", {"path": "textutil/__init__.py"})),
        submit(),
        calls(("read_file", {"path": "textutil/__init__.py"})),
        submit(),
    ]
    rt = await make_runtime(Models(scripts))
    free_prices(rt)
    rt.ultron.update_config({"max_attempts": 3})
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    mission = await until(rt, created["id"], "BLOCKED", "WAITING_APPROVAL")
    assert mission["state"] == "BLOCKED"
    assert mission["blocker"]["kind"] == "retries" and mission["blocker"]["task"] == "impl"
    impl = next(t for t in mission["tasks"] if t["key"] == "impl")
    assert impl["state"] == "BLOCKED" and impl["attempts"] == 3
    assert all(t["state"] != "COMPLETE" for t in mission["tasks"] if t["key"] != "spec")
    # The user grants another round; FORGE fixes it this time.
    fix = calls(("write_file", {"path": "textutil/__init__.py", "content": FIXED}))
    rt.ultron._model = Models({"forge": [fix, submit()], "sentinel": list(review("pass"))})
    resumed = await rt.ultron.resume(created["id"])
    assert resumed["state"] == "RUNNING"
    assert (await until(rt, created["id"], "WAITING_APPROVAL"))["state"] == "WAITING_APPROVAL"


async def test_budget_is_checked_before_every_call(make_runtime: Callable[..., Any]) -> None:
    rt = await make_runtime(Models(happy_scripts()))  # real prices from config/ultron.yaml
    created = await rt.ultron.create_mission(
        "Build a tested slugify utility function.", budget_usd=0.1
    )
    mission = await until(rt, created["id"], "BLOCKED")
    assert mission["blocker"]["kind"] == "budget"
    assert mission["spent_usd"] == 0  # refused before the call, not after
    resumed = await rt.ultron.set_budget(created["id"], 20)
    assert resumed["state"] == "PLANNING"
    mission = await until(rt, created["id"], "WAITING_APPROVAL", "BLOCKED")
    assert mission["state"] == "WAITING_APPROVAL"
    assert mission["spent_usd"] > 0  # every scripted call reported usage at Claude prices
    assert sum(r["cost_usd"] for r in mission["runs"]) == pytest.approx(mission["spent_usd"])


# Planning ------------------------------------------------------------------------------------


def test_plan_validation_rules() -> None:
    plan, notes = planning.validate(PLAN)
    assert [t.key for t in plan.tasks] == ["spec", "impl", "verify"] and not notes

    no_review = {**PLAN, "tasks": PLAN["tasks"][:2]}
    plan, notes = planning.validate(no_review)
    assert plan.tasks[-1].owner == "sentinel" and plan.tasks[-1].depends_on == ["impl"]
    assert "SENTINEL must review" in notes[0]

    def broken(**changes: Any) -> list[str]:
        tasks = [dict(t) for t in PLAN["tasks"]]
        tasks[1].update(changes)
        with pytest.raises(planning.PlanError) as exc:
            planning.validate({**PLAN, "tasks": tasks})
        return exc.value.problems

    assert any("cycle" in p for p in broken(depends_on=["spec", "verify"]))
    assert any("check" in p for p in broken(checks=[]))
    assert any("refused" in p for p in broken(checks=[["curl", "https://x"]]))
    assert any("glob" in p for p in broken(allowed_paths=["../outside/**"]))
    assert any("owner" in p for p in broken(owner="atlas"))
    assert any("unknown dependency" in p for p in broken(depends_on=["nope"]))


async def test_invalid_plan_is_sent_back_and_blocked_after_rounds(
    make_runtime: Callable[..., Any],
) -> None:
    bad = {**PLAN, "tasks": [{**PLAN["tasks"][1], "depends_on": [], "checks": []}]}
    rt = await make_runtime(Models({"jarvis": [calls(("submit_plan", bad))] * 6}))
    free_prices(rt)
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    mission = await until(rt, created["id"], "BLOCKED")
    assert mission["blocker"]["kind"] == "plan"
    assert mission["tasks"] == []


async def test_blocking_questions_wait_for_the_user(make_runtime: Callable[..., Any]) -> None:
    asking = {**PLAN, "blocking_unknowns": ["Which naming style?"]}
    scripts = happy_scripts()
    scripts["jarvis"] = [calls(("submit_plan", asking)), calls(("submit_plan", PLAN))]
    rt = await make_runtime(Models(scripts))
    free_prices(rt)
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    mission = await until(rt, created["id"], "BLOCKED")
    assert mission["blocker"]["questions"] == ["Which naming style?"]
    await rt.ultron.answer(created["id"], "Kebab case.")
    mission = await until(rt, created["id"], "WAITING_APPROVAL", "BLOCKED")
    assert mission["state"] == "WAITING_APPROVAL"
    assert mission["charter"]["answers"] == ["Kebab case."]


async def test_jarvis_project_works_on_a_branch_never_the_checkout(
    make_runtime: Callable[..., Any], tmp_path: Path
) -> None:
    repo = tmp_path / "jarvis-repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "app.py").write_text("VERSION = 1\n")
    commit = ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], commit):
        subprocess.run(["git", *args], cwd=repo, check=True)  # noqa: ASYNC221 - test setup
    plan = {
        **PLAN,
        "success_criteria": [
            {"criterion": "tests pass", "check": ["python", "-m", "pytest", "-q", "backend"]}
        ],
        "tasks": [
            {
                **PLAN["tasks"][1],
                "depends_on": [],
                "allowed_paths": ["backend/**"],
                "checks": [["python", "-m", "pytest", "-q", "backend"]],
            },
            PLAN["tasks"][2],
        ],
    }
    scripts = {
        "jarvis": [calls(("submit_plan", plan))],
        "forge": [
            calls(
                ("write_file", {"path": "backend/textutil.py", "content": FIXED}),
                (
                    "write_file",
                    {
                        "path": "backend/test_textutil.py",
                        "content": TESTS.replace("from textutil", "from textutil"),
                    },
                ),
            ),
            submit(),
        ],
        "sentinel": list(review("pass")),
    }
    rt = await make_runtime(Models(scripts), ultron_repo=repo)
    free_prices(rt)
    created = await rt.ultron.create_mission("Add slugify to JARVIS.", project="jarvis")
    mission = await until(rt, created["id"], "COMPLETE", "BLOCKED", "WAITING_APPROVAL")
    assert mission["state"] == "COMPLETE", mission["blocker"]
    assert mission["approvals"] == []  # no export; merging is locked
    listed = ["git", "branch", "--list", "ultron/*"]
    branches = subprocess.run(listed, cwd=repo, capture_output=True, text=True).stdout  # noqa: ASYNC221
    assert f"ultron/{created['id']}" in branches
    assert not (repo / "backend" / "textutil.py").exists()  # the checkout is untouched
    assert "locked" in mission["report"]["delivery"]


# Policy, sandbox, approvals -------------------------------------------------------------------


def test_path_and_command_policy() -> None:
    assert glob_match("src/**", "src/a/b.py") and glob_match("docs/*.md", "docs/SPEC.md")
    assert not glob_match("docs/*.md", "docs/x/SPEC.md")
    assert check_write("./src/a.py", ["src/**"]) == "src/a.py"
    for path in ("../x", "/etc/passwd", "src/../../x", ".git/config", "C:/x", ""):
        with pytest.raises(PolicyError):
            check_write(path, ["**"])
    with pytest.raises(PolicyError, match="outside"):
        check_write("README.md", ["src/**"])

    assert check_command(["pytest", "-q"]).argv == ("python", "-m", "pytest", "-q")
    assert check_command(["git", "diff"]).label == "git diff"
    refusals = {
        ("python", "-c", "print(1)"): "command.run",
        ("python", "-m", "pip", "install", "x"): "dependency.install",
        ("curl", "https://x"): "network",
        ("git", "commit", "-m", "x"): "command.run",
        ("git", "-c", "core.pager=x", "log"): "command.run",
        ("bash", "-c", "ls"): "command.run",
    }
    for argv, category in refusals.items():
        with pytest.raises(PolicyError) as exc:
            check_command(list(argv))
        assert exc.value.category == category


async def test_sandbox_scrubs_secrets_and_blocks_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-super-secret")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os, socket\n"
        "print('KEY' if any('sk-ant' in v for v in os.environ.values()) else 'no-key')\n"
        "s = socket.socket()\ns.settimeout(3)\n"
        "try:\n    s.connect(('1.1.1.1', 53))\n    print('net-open')\n"
        "except OSError:\n    print('net-blocked')\n"
    )
    outcome = await sandbox.run(
        Command(("python", "probe.py"), "python probe.py"),
        cwd=tmp_path,
        scratch=tmp_path / "scratch",
        pythonpath=[],
        timeout=30,
    )
    assert "no-key" in outcome.output and "KEY" not in outcome.output
    if sandbox.detect().network_blocked:
        assert "net-blocked" in outcome.output
    slow = tmp_path / "slow.py"
    slow.write_text("import time\ntime.sleep(30)\n")
    timed = await sandbox.run(
        Command(("python", "slow.py"), "python slow.py"),
        cwd=tmp_path,
        scratch=tmp_path / "scratch",
        pythonpath=[],
        timeout=1,
    )
    assert timed.timed_out and not timed.passed


async def test_approvals_are_atomic_bound_and_locked_categories_refused(
    make_runtime: Callable[..., Any],
) -> None:
    rt = await make_runtime(Models(happy_scripts()))
    free_prices(rt)
    created = await rt.ultron.create_mission("Build a tested slugify utility function.")
    mission = await until(rt, created["id"], "WAITING_APPROVAL")
    (approval,) = mission["approvals"]
    # A locked category can't even be requested.
    with pytest.raises(PolicyError):
        await rt.ultron.request_approval(
            created["id"], None, "finance.transaction", "pay", {"kind": "x"}, requested_by="forge"
        )
    # Tampering with the stored effect is detected.
    await rt.db.execute(
        "UPDATE ul_approvals SET effect = ? WHERE id = ?",
        (json.dumps({**approval["effect"], "destination": "/tmp/elsewhere"}), approval["id"]),
    )
    with pytest.raises(Exception, match="changed"):
        await rt.ultron.decide(approval["id"], True)
    await rt.db.execute(
        "UPDATE ul_approvals SET effect = ? WHERE id = ?",
        (json.dumps(approval["effect"]), approval["id"]),
    )
    declined = await rt.ultron.decide(approval["id"], False)
    assert declined["state"] == "rejected"
    final = await rt.ultron.mission(created["id"])
    assert final["state"] == "COMPLETE" and "declined" in final["report"]["delivery"]
    assert not Path(approval["effect"]["destination"]).exists()  # noqa: ASYNC240


# API and brain ---------------------------------------------------------------------------------


def test_api_without_a_model_says_what_is_missing(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        overview = client.get("/ultron/overview").json()
        assert overview["model_ready"] is False
        assert [a["id"] for a in overview["agents"]][:4] == ["jarvis", "axiom", "forge", "sentinel"]
        assert sum(a["active"] for a in overview["agents"]) == 4  # the others are not faked
        assert any(
            p["category"] == "finance.transaction" and p["decision"] == "locked"
            for p in overview["policy"]
        )
        response = client.post("/ultron/missions", json={"goal": "Build a slugify tool please."})
        assert response.status_code == 422 and response.json()["detail"]["code"] == "NO_MODEL"
        assert (
            client.post("/ultron/missions/m001-abcdef/answer", json={"text": "x"}).status_code
            == 404
        )
        assert client.post("/ultron/missions/../x/pause").status_code in (404, 405, 422)
        assert client.post("/ultron/config", json={"max_parallel_workers": 99}).status_code == 422


async def test_brain_tool_starts_a_mission(make_runtime: Callable[..., Any]) -> None:
    from jarvis.core.trace import TraceContext

    rt = await make_runtime(Models(happy_scripts()))
    free_prices(rt)
    result = await rt.executor.run(
        "ultron_start_mission",
        {"goal": "Build a tested slugify utility function."},
        ctx=TraceContext.new(),
        reason="test",
    )
    assert result.success, result.summary
    mission_id = result.data["mission_id"]
    await until(rt, mission_id, "WAITING_APPROVAL")
    status = await rt.executor.run("ultron_status", {}, ctx=TraceContext.new(), reason="test")
    assert "Export the verified project" in status.data["status"]
