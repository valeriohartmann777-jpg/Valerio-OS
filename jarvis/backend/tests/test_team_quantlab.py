"""ULTRON QuantLab (JARVIS 2.0, acceptance 16-22): one engine, one Data Vault, two workspaces.

Synthetic fixture data (labelled) and a scripted CIPHER (labelled) — the checks are about
authority, versioning and determinism, not about market results.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from jarvis.quantlab.vault.lab import LabError
from jarvis.runtime import Runtime
from jarvis.team.workspace import TeamError
from tests.conftest import make_settings
from tests.test_quantlab_vault import backtest, import_dbn, strategy

PROPOSAL = {
    "tool": "propose_variants",
    "input": {
        "variants": [
            {
                "title": "Narrow ranges only",
                "changes": [{"path": "filters.max_range_ticks", "value": 160}],
                "mechanism": "Wide opening ranges leave little room to the target.",
                "prediction": "Out-of-sample net per trade rises; trades stay above 20.",
            }
        ]
    },
}


@pytest.fixture
async def rt(tmp_path: Path) -> Any:
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"cipher": [PROPOSAL]}), encoding="utf-8")
    settings = make_settings(tmp_path)
    lab = settings.quantlab.model_copy(
        update={"provider": "fixture", "keystore": "memory", "architect_script": str(script)}
    )
    runtime = Runtime(settings.model_copy(update={"quantlab": lab}))
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


async def shared(rt: Runtime, tmp_path: Path, **grants: bool) -> tuple[str, str, dict[str, Any]]:
    await import_dbn(rt, tmp_path)
    solo_version = await strategy(rt)
    project = await rt.team.share(solo_version, grants, confirm=True)
    return solo_version, await rt.research.version_owner(solo_version), project


async def team_run(rt: Runtime, project_id: str, kind: str = "validation") -> dict[str, Any]:
    out = await rt.team.backtest(
        project_id, {"symbol": "NQ", "timeframe": "5m", "advanced": {"kind": kind}}
    )
    await rt.research.wait_idle(300)
    run = await rt.research.run(out["run"]["id"])
    assert run["status"] == "COMPLETED", run["error"]
    return run


async def test_sharing_needs_confirmation_and_gives_the_team_only_a_copy(
    rt: Runtime, tmp_path: Path
) -> None:
    await import_dbn(rt, tmp_path)
    solo_version = await strategy(rt)
    with pytest.raises(TeamError) as err:
        await rt.team.share(solo_version, {"read_spec": True}, confirm=False)
    assert err.value.code == "CONFIRM_REQUIRED"
    with pytest.raises(TeamError) as err:
        await rt.team.share(solo_version, {"trade_live": True}, confirm=True)
    assert err.value.code == "UNKNOWN_GRANT"
    solo_id = await rt.research.version_owner(solo_version)
    project = await rt.team.share(solo_version, {"read_spec": True}, confirm=True)
    team_id = project["team_strategy_id"]
    assert team_id != solo_id and project["team_strategy"]["versions"][0]["origin"] == "team-share"
    # Two workspaces over one registry: Solo never lists the team copy, the team only it.
    solo_ids = {s["id"] for s in (await rt.backtests.options())["strategies"]}
    team_ids = {
        s["id"] for s in (await rt.backtests.options(f"team:{project['id']}"))["strategies"]
    }
    assert solo_id in solo_ids and team_id not in solo_ids and team_ids == {team_id}
    # A team version can't be started as Solo work, and a Solo version not as team work.
    with pytest.raises(LabError) as lab:
        await rt.backtests.launch(
            {
                "version_id": project["team_strategy"]["versions"][0]["id"],
                "symbol": "NQ",
                "timeframe": "5m",
            }
        )
    assert lab.value.code == "TEAM_WORKSPACE"
    with pytest.raises(TeamError) as err:
        await rt.team.backtest(
            project["id"], {"version_id": solo_version, "symbol": "NQ", "timeframe": "5m"}
        )
    assert err.value.code == "NOT_IN_PROJECT"


async def test_agents_act_only_within_explicit_grants(rt: Runtime, tmp_path: Path) -> None:
    _, solo_id, project = await shared(rt, tmp_path, read_spec=True)
    pid = project["id"]
    with pytest.raises(TeamError) as err:  # acceptance 18: no grant, no proposal
        await rt.team.ask(pid, "cipher", "improve")
    assert err.value.code == "NOT_GRANTED" and err.value.status == 403
    with pytest.raises(TeamError) as err:
        await rt.team.ask(pid, "sentinel", "challenge")
    assert err.value.code == "NO_RUN"
    run = await team_run(rt, pid)
    task = await rt.team.ask(pid, "sentinel", "challenge")
    await rt.team.wait_idle()
    done = await rt.team.store.task(task["id"])
    assert done is not None and done["state"] == "COMPLETE" and done["result"]["passed"] is True
    # 5-minute run: the second engine (1m only) is reported as not run, never as a pass.
    assert done["result"]["second_engine"]["skipped"] is True
    assert any(w["id"] == "SECOND_ENGINE" for w in done["result"]["warnings"])
    assert done["request"]["run_id"] == run["id"]
    # The HQ shows SENTINEL's finished team task (from tm_tasks, not invented).
    snap = await rt.team_hq.snapshot()
    sentinel = next(r for r in snap["robots"] if r["id"] == "sentinel")
    assert any(w["source"] == "team" and w["state"] == "complete" for w in sentinel["work"])
    # Revoking access is logged; the audit can't be edited.
    await rt.team.set_grants(pid, {"read_spec": False}, confirm=True)
    with pytest.raises(TeamError) as err:
        await rt.team.ask(pid, "sentinel", "challenge")
    assert err.value.code == "NOT_GRANTED"
    actions = [a["action"] for a in (await rt.team.project(pid))["audit"]]
    assert actions == ["share", "backtest", "ask", "grants"]
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute("UPDATE tm_audit SET actor = 'cipher'")
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute("DELETE FROM tm_audit")
    assert len((await rt.research.strategy(solo_id))["versions"]) == 1


async def test_solo_and_team_share_one_engine_one_dataset_one_ledger(
    rt: Runtime, tmp_path: Path
) -> None:
    solo_version, _, project = await shared(rt, tmp_path, read_spec=True)
    solo = (await backtest(rt, solo_version, "5m"))["run"]
    team = await team_run(rt, project["id"], kind="backtest")
    assert (
        solo["manifest"]["dataset"]["snapshot_sha256"]
        == team["manifest"]["dataset"]["snapshot_sha256"]
    )
    assert solo["manifest"]["engine"]["version"] == team["manifest"]["engine"]["version"]
    assert solo["manifest"]["spec_sha256"] == team["manifest"]["spec_sha256"]

    def ledger(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        return [
            (r["session"], r["entry_time"], r["exit_time"], r["direction"], r["net"]) for r in rows
        ]

    solo_rows = (await rt.research.trades(solo["id"], 0, 10**9))["rows"]
    team_rows = (await rt.research.trades(team["id"], 0, 10**9))["rows"]
    assert solo_rows and ledger(solo_rows) == ledger(team_rows)  # acceptance 17


async def test_cipher_proposals_become_new_team_versions_and_solo_stays_untouched(
    rt: Runtime, tmp_path: Path
) -> None:
    _, solo_id, project = await shared(
        rt, tmp_path, read_spec=True, run_backtests=True, propose_versions=True
    )
    pid = project["id"]
    solo_before = await rt.research.strategy(solo_id)
    await team_run(rt, pid)
    task = await rt.team.ask(pid, "cipher", "improve")
    assert task["state"] == "RUNNING" and task["mission_id"]
    mission = await until(rt, task["mission_id"], "WAITING_USER")
    assert (
        mission["waiting"]["kind"] == "lock_candidate"
    )  # the holdout stays sealed (acceptance 22)
    trials = mission["trials"]
    assert [t["iteration"] for t in trials] == [0, 1] and trials[0][
        "title"
    ] == "Baseline (team run)"
    assert trials[1]["changes"] == [{"path": "filters.max_range_ticks", "value": 160}]
    view = await rt.team.project(pid)
    versions = view["team_strategy"]["versions"]
    assert [v["origin"] for v in versions] == ["team-share", "ai"]  # acceptance 19
    assert view["tasks"][0]["state"] == "COMPLETE" and view["tasks"][0]["result"]["trials"] == 2
    solo_after = await rt.research.strategy(solo_id)
    assert solo_after["versions"] == solo_before["versions"]  # acceptance 18: Solo unchanged
    # Only the owner copies a reviewed version back — as a new Solo version, nothing overwritten.
    with pytest.raises(TeamError):
        await rt.team.return_to_solo(pid, versions[1]["id"], confirm=False)
    out = await rt.team.return_to_solo(pid, versions[1]["id"], confirm=True)
    solo_final = await rt.research.strategy(solo_id)
    assert [v["origin"] for v in solo_final["versions"]] == ["user", "team"]
    assert solo_final["versions"][0] == solo_before["versions"][0]
    assert out["solo_version_id"] == solo_final["versions"][1]["id"]


async def test_cipher_needs_an_ai_route(
    rt: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, project = await shared(
        rt, tmp_path, read_spec=True, run_backtests=True, propose_versions=True
    )
    await team_run(rt, project["id"])
    monkeypatch.setattr(rt.team, "_ai_ready", lambda: (False, "No AI route — connect your plan."))
    with pytest.raises(TeamError) as err:
        await rt.team.ask(project["id"], "cipher", "improve")
    assert err.value.code == "NO_AI_ROUTE" and "connect" in err.value.message


async def until(rt: Runtime, mission_id: str, *states: str, timeout: float = 400) -> dict[str, Any]:  # noqa: ASYNC109
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        m = await rt.quant_missions.mission(mission_id)
        if m["state"] in states:
            return m
        if m["state"] in ("BLOCKED", "FAILED"):
            notes = [e["message"] for e in m["events"]][:8]
            raise AssertionError(f"mission {m['state']}: {m['error']} — {notes}")
        await asyncio.sleep(0.1)
    raise AssertionError(f"mission still {m['state']} at {m['stage']}")
