"""Idea-to-Edge missions end to end (A15 UX-01, DATA-01/02, AG-01/02/03, VAL-01/02).

Everything around the model is the real runtime: intake, claims checks, blueprint
compiler, Data Hub (offline fixture provider, memory keystore), engine, validation
suite, SENTINEL's second engine, trial ledger and dossier. The model replies are
scripted (``JARVIS_QUANTLAB_ARCHITECT_SCRIPT`` format) and labelled as such; the
market data are synthetic — so these tests prove plumbing and rules, never an edge.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from jarvis.quantlab.futures.spec import template
from jarvis.quantlab.hub import fixture
from jarvis.quantlab.ideas.missions import MissionError
from jarvis.runtime import Runtime
from tests.conftest import make_settings

IDEA = (
    "NQ futures, New York open.\n\n"
    "Wait for a liquidity sweep of the 15 minute opening range high.\n\n"
    "When price reclaims the level, enter short. Stop one tick above the sweep wick, target 2R.\n\n"
    "This strategy wins 90% of the time."
)


def claims_step() -> dict[str, Any]:
    return {
        "tool": "submit_claims",
        "input": {
            "summary": "Short NQ after a sweep of the 15-minute opening range high and a reclaim.",
            "claims": [
                {
                    "kind": "CONTEXT",
                    "content": "NQ futures.",
                    "quote": "NQ futures",
                    "segment_ids": ["s1"],
                    "field_mapping": ["instrument"],
                    "extraction_confidence": "high",
                    "claim_status": "defined",
                },
                {
                    "kind": "RULE",
                    "content": "After the New York open.",
                    "quote": "New York open",
                    "segment_ids": ["s1"],
                    "field_mapping": ["session"],
                    "extraction_confidence": "high",
                    "claim_status": "partially_defined",
                    "unresolved": ["which clock"],
                },
                {
                    "kind": "RULE",
                    "content": "Wait for a sweep of the 15-minute opening range high.",
                    "quote": "liquidity sweep of the 15 minute opening range high",
                    "segment_ids": ["s2"],
                    "field_mapping": ["setup", "level", "opening_range"],
                    "extraction_confidence": "high",
                    "claim_status": "partially_defined",
                    "unresolved": ["sweep threshold"],
                },
                {
                    "kind": "RULE",
                    "content": "When price reclaims the level, enter short.",
                    "quote": "When price reclaims the level, enter short.",
                    "segment_ids": ["s3"],
                    "field_mapping": ["entry_trigger", "direction"],
                    "extraction_confidence": "high",
                    "claim_status": "partially_defined",
                    "unresolved": ["reclaim definition"],
                },
                {
                    "kind": "RULE",
                    "content": "Stop one tick above the sweep's wick.",
                    "quote": "Stop one tick above the sweep wick",
                    "segment_ids": ["s3"],
                    "field_mapping": ["stop"],
                    "extraction_confidence": "high",
                    "claim_status": "defined",
                },
                {
                    "kind": "RULE",
                    "content": "Target 2R.",
                    "quote": "target 2R",
                    "segment_ids": ["s3"],
                    "field_mapping": ["target"],
                    "extraction_confidence": "high",
                    "claim_status": "defined",
                },
            ],
        },
    }


def blueprint_step() -> dict[str, Any]:
    spec = template("level_sweep_reclaim", "NQ")
    spec["rule"].update(range_minutes=15, sides="fade_highs")
    spec["exits"] = {
        "stop": {"type": "setup_extreme", "ticks": 1},
        "target": {"type": "r_multiple", "value": 2},
    }
    spec["validation"].update(parameter_grid={}, min_trades_oos=20)
    spec["validation"]["walk_forward"] = {
        "train_sessions": 40,
        "test_sessions": 10,
        "mode": "rolling",
    }
    spec["validation"]["bootstrap"]["samples"] = 300
    spec.pop("assumptions")

    def cite(field: str, *ids: str) -> dict[str, Any]:
        return {"field": field, "class": "EXPLICIT_SOURCE", "claim_ids": list(ids)}

    return {
        "tool": "submit_blueprint",
        "input": {
            "no_rule": False,
            "summary": "Short NQ after a sweep of the 15-minute opening range high.",
            "spec": spec,
            "provenance": [
                cite("instrument.product", "c1"),
                cite("rule.type", "c3", "c4"),
                cite("rule.level", "c3"),
                cite("rule.range_minutes", "c3"),
                cite("rule.sides", "c4"),
                cite("exits.stop.type", "c5"),
                cite("exits.stop.ticks", "c5"),
                cite("exits.target.type", "c6"),
                cite("exits.target.value", "c6"),
            ],
            "ambiguities": [
                {"term": "ny_open", "chosen": None, "basis": "open"},
                {"term": "opening_range", "chosen": "or_15", "basis": "source"},
                {"term": "liquidity_sweep", "chosen": None, "basis": "open"},
                {"term": "stop_beyond_wick", "chosen": "wick_1t", "basis": "source"},
                {"term": "r_multiple", "chosen": "r_fill_to_stop", "basis": "default"},
            ],
        },
    }


PROTOCOL = {
    "tool": "submit_protocol",
    "input": {
        "months": 6,
        "min_trades_oos": 20,
        "max_cost_share": 0.5,
        "tests": ["OUT_OF_SAMPLE", "WALK_FORWARD", "COST_STRESS", "BOOTSTRAP", "SELECTION_BIAS"],
        "acceptance": ["Out-of-sample net after costs > 0 with ≥ 20 trades"],
        "rejection": ["Out-of-sample net ≤ 0 after costs"],
        "limitations": ["1-minute bars: the sweep's intrabar path is unknown"],
        "rationale": "Six months give enough sessions for a 30% out-of-sample segment.",
    },
}
OVERCLAIM = {
    "tool": "submit_report",
    "input": {
        "text": "This is a verified edge: 96% sure it works and it made $5,000.",
        "next_step": "Trade it.",
    },
}
REPORT = {
    "tool": "submit_report",
    "input": {
        "text": "Synthetic fixture data, so this is an engineering check of the pipeline, "
        "not evidence. The rule from the text was frozen, tested on development data with "
        "the holdout sealed, and SENTINEL reproduced the trades with its second engine.",
        "next_step": "Repeat on licensed Databento data.",
    },
}


def variants(*changes: tuple[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "tool": "propose_variants",
        "input": {
            "variants": [
                {
                    "title": title,
                    "changes": ch,
                    "mechanism": "A deeper sweep may mark real stop runs.",
                    "prediction": "If so, out-of-sample net per trade rises; trades stay ≥ 20.",
                }
                for title, ch in changes
            ]
        },
    }


SCRIPT = {
    "atlas": [claims_step()],
    "jarvis": [blueprint_step(), OVERCLAIM, REPORT, REPORT, REPORT],
    "cipher": [
        PROTOCOL,
        variants(
            ("Deeper sweep", [{"path": "rule.sweep_min_ticks", "value": 8}]),
            ("Narrow ranges only", [{"path": "filters.max_range_ticks", "value": 160}]),
        ),
        variants(("Wider reclaim window", [{"path": "rule.reclaim_within_bars", "value": 10}])),
    ],
}


def runtime(tmp: Path, script: dict[str, Any] = SCRIPT) -> Runtime:
    path = tmp / "script.json"
    path.write_text(json.dumps(script), encoding="utf-8")
    settings = make_settings(tmp)
    lab = settings.quantlab.model_copy(
        update={"provider": "fixture", "keystore": "memory", "architect_script": str(path)}
    )
    return Runtime(settings.model_copy(update={"quantlab": lab}))


async def until(rt: Runtime, mission_id: str, *states: str, timeout: float = 240) -> dict[str, Any]:  # noqa: ASYNC109
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        m = await rt.quant_missions.mission(mission_id)
        if m["state"] in states:
            return m
        if m["state"] in ("BLOCKED", "FAILED") and m["state"] not in states:
            raise AssertionError(f"mission {m['state']}: {m['error']}")
        await asyncio.sleep(0.1)
    raise AssertionError(f"mission still {m['state']} at {m['stage']}")


async def to_verdict(rt: Runtime) -> dict[str, Any]:
    src = await rt.sources.add_text(IDEA, note="Only the short side, as in the text.")
    [listed] = await rt.quant_missions.missions()  # auto-started by the intake hook
    m = await until(rt, listed["id"], "WAITING_USER")
    assert m["waiting"]["kind"] == "questions" and m["stage"] == "boundary"
    terms = [q["term"] for q in m["waiting"]["questions"] if q["kind"] == "term"]
    assert terms == ["ny_open", "liquidity_sweep"]  # SP-01: no silent definitions
    await rt.quant_missions.answer(m["id"], {"accept_defaults": True})
    m = await until(rt, m["id"], "WAITING_USER")  # DATA-01: no Databento connection yet
    assert m["waiting"]["kind"] == "connect_data" and m["stage"] == "data"
    status = await rt.hub.status()
    assert status["status"] == "NOT_CONNECTED"
    await rt.hub.connect(fixture.VALID_KEY)  # the owner connects in the Data Hub
    m = await until(rt, m["id"], "WAITING_APPROVAL")
    waiting = m["waiting"]
    assert waiting["kind"] == "data_purchase" and waiting["cost_usd"] > 0 and waiting["fixture"]
    assert waiting["request"]["start"] == "2026-01-01" and waiting["request"]["end"] == "2026-07-01"
    with pytest.raises(MissionError) as over:
        await rt.quant_missions.approve_data(m["id"], 999)
    assert over.value.code == "OVER_MISSION_BUDGET"
    await rt.quant_missions.approve_data(m["id"], round(waiting["cost_usd"] + 1, 2))
    m = await until(rt, m["id"], "COMPLETE", timeout=400)
    return {"mission": m, "source": src}


async def test_text_to_verdict_with_one_question_one_approval_and_an_audit(tmp_path: Path) -> None:
    rt = runtime(tmp_path)
    await rt.start()
    try:
        out = await to_verdict(rt)
        m = out["mission"]
        assert (
            m["verdict"] == "INSUFFICIENT_EVIDENCE"
        )  # synthetic data: capped, whatever the gates say
        agents = [t["agent"] for t in m["tasks"]]
        assert agents[:4] == ["archive", "atlas", "jarvis", "sentinel"]
        assert {"cipher", "vector", "sentinel", "jarvis", "archive", "atlas"} <= set(agents)
        refs = m["refs"]
        # Frozen before data: the protocol's spec hash is the frozen version's.
        assert refs["protocol"]["spec_sha256"] == refs["spec_sha256"]
        assert refs["protocol"]["sha256"] and refs["boundary"]["passed"]
        # SENTINEL: the second engine reproduced every stored trade.
        audit = refs["audit"]
        assert audit["passed"], audit["checks"]
        assert (
            audit["second_engine"]["mismatch_count"] == 0
            and audit["second_engine"]["compared"] > 10
        )
        # AG-02: the overclaiming note was refused; the shown note is checked.
        note = refs["verdict"]["note"]
        assert note["checked"] and "verified edge" not in note["text"]
        report = next(t for t in m["tasks"] if t["kind"] == "report")
        assert any(
            "refused" in a["text"] or "attempt" in a["text"].lower() or "checked" in a["text"]
            for a in report["actions"]
        )
        # Provenance survives to the dossier; the boast stays a claim.
        dossier = await rt.quant_missions.dossier(m["id"])
        md = dossier["markdown"]
        assert "PERFORMANCE_CLAIM" in md and "SYNTHETIC FIXTURE DATA" in md
        assert "BAR_ONLY_CONSERVATIVE" in md and "Second engine" not in md.split("## 1")[0]
        assert dossier["json"]["evidence"]["execution_fidelity"] == "BAR_ONLY_CONSERVATIVE"
        # Source audit says what left the computer: text for the model, no media.
        src = await rt.sources.source(out["source"]["id"])
        sent = [a for a in src["audit"] if a["action"] == "TEXT_SENT_TO_MODEL"]
        assert sent and sent[0]["detail"]["media_sent"] is False
        # UX-03: agent statuses come from real tasks.
        roster = await rt.quant_missions.agents()
        cipher = next(a for a in roster if a["id"] == "cipher")
        assert cipher["tasks"].get("COMPLETE", 0) >= 2 and cipher["status"] == "idle"
        assert next(a for a in roster if a["id"] == "forge")["status"] == "not_used"
    finally:
        await rt.stop()


async def test_evolution_counts_every_trial_and_a_second_holdout_look_is_contaminated(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path)
    await rt.start()
    try:
        base = (await to_verdict(rt))["mission"]
        evo = await rt.quant_missions.evolve(base["id"], {"max_variants": 2, "max_iterations": 1})
        assert evo["kind"] == "evolution" and evo["budget"]["max_paid_data_usd"] == 0
        evo = await until(rt, evo["id"], "WAITING_USER", timeout=400)
        assert evo["waiting"]["kind"] == "lock_candidate"
        trials = evo["trials"]
        assert [t["iteration"] for t in trials] == [0, 1, 1]  # baseline + two variants, all kept
        assert all(t["audit"]["passed"] for t in trials if t["iteration"]), [
            (t["title"], t["audit"], t["outcome"]) for t in trials
        ]
        assert {c["path"] for t in trials for c in t["changes"]} == {
            "rule.sweep_min_ticks",
            "filters.max_range_ticks",
        }
        # VAL-01: the family's deflated Sharpe counts every evaluated variant.
        assert max(t["metrics"]["family_trials"] or 0 for t in trials if t["iteration"]) >= 3
        assert any("in-sample" in n for n in evo["refs"]["comparison"]["notes"])
        candidate = evo["waiting"]["candidates"][0]["version_id"]
        with pytest.raises(MissionError):
            await rt.quant_missions.lock_candidate(evo["id"], candidate, False)
        await rt.quant_missions.lock_candidate(evo["id"], candidate, True)
        evo = await until(rt, evo["id"], "COMPLETE", timeout=300)
        assert evo["refs"]["holdout"]["independent"] is True
        # The append-only ledger can't be edited.
        with pytest.raises(sqlite3.IntegrityError):
            await rt.db.execute("UPDATE qm_trials SET outcome = 'X'")
        # VAL-02: retuning after the holdout look — the next look isn't independent.
        again = await rt.quant_missions.evolve(base["id"], {"max_variants": 1, "max_iterations": 1})
        again = await until(rt, again["id"], "WAITING_USER", timeout=400)
        assert all(t["post_holdout"] for t in again["trials"])
        await rt.quant_missions.lock_candidate(
            again["id"], again["waiting"]["candidates"][0]["version_id"], True
        )
        again = await until(rt, again["id"], "COMPLETE", timeout=300)
        assert again["refs"]["holdout"]["independent"] is False
        assert any("contaminated" in r for r in again["refs"]["verdict"]["reasons"])
    finally:
        await rt.stop()


async def test_restart_resumes_without_a_second_purchase(tmp_path: Path) -> None:
    rt = runtime(tmp_path)
    await rt.start()
    src = await rt.sources.add_text(IDEA)
    [listed] = await rt.quant_missions.missions()
    m = await until(rt, listed["id"], "WAITING_USER")
    await rt.quant_missions.answer(m["id"], {"accept_defaults": True})
    await rt.hub.connect(fixture.VALID_KEY)
    m = await until(rt, m["id"], "WAITING_APPROVAL")
    cost = m["waiting"]["cost_usd"]
    await rt.quant_missions.approve_data(m["id"], cost + 1)
    again = await rt.quant_missions.approve_data(m["id"], cost + 1)  # a double click
    first_job = again["refs"]["job_id"]
    assert first_job and len(await rt.hub.jobs()) == 1
    await rt.stop()  # JARVIS quits mid-download
    rt2 = runtime(tmp_path, {**SCRIPT, "atlas": [], "jarvis": [REPORT], "cipher": []})
    await rt2.start()
    try:
        # The test keystore lives in memory: the key is gone after a restart (the OS keystore
        # keeps it). The mission waits for the connection, then continues on its own.
        waiting = await until(rt2, m["id"], "WAITING_USER", timeout=60)
        assert waiting["waiting"]["kind"] == "connect_data"
        await rt2.hub.connect(fixture.VALID_KEY)
        resumed = await until(rt2, m["id"], "WAITING_APPROVAL", "COMPLETE", timeout=400)
        if resumed["state"] == "WAITING_APPROVAL":
            # Interrupted: only the days not yet delivered are quoted — and need approval again.
            assert resumed["waiting"]["cost_usd"] <= cost
            await rt2.quant_missions.approve_data(m["id"], resumed["waiting"]["cost_usd"] + 1)
        done = await until(rt2, m["id"], "COMPLETE", timeout=400)
        assert done["id"] == m["id"] and done["source_id"] == src["id"]
        assert any(e["kind"] == "resume" for e in done["events"])
        # AG-03: no day was ever bought twice, across every job.
        bought: dict[tuple[str, str], int] = {}
        for job in await rt2.hub.jobs():
            for chunk in (await rt2.hub.job(job["id"]))["chunks"]:
                if chunk["status"] != "DONE":
                    continue
                day = date.fromisoformat(chunk["start"])
                while day < date.fromisoformat(chunk["end"]):
                    key = (chunk["schema"], day.isoformat())
                    bought[key] = bought.get(key, 0) + 1
                    day += timedelta(days=1)
        assert bought and max(bought.values()) == 1
    finally:
        await rt2.stop()


async def test_no_model_blocks_honestly_and_link_without_media_is_unavailable(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    lab = settings.quantlab.model_copy(update={"provider": "fixture", "keystore": "memory"})
    rt = Runtime(settings.model_copy(update={"quantlab": lab}))
    await rt.start()
    try:
        await rt.sources.add_text(IDEA)
        [listed] = await rt.quant_missions.missions()
        m = await until(rt, listed["id"], "BLOCKED")
        assert m["error"].startswith("NO_MODEL") and m["stage"] == "claims"
        assert [t["state"] for t in m["tasks"]] == ["COMPLETE", "BLOCKED"]
        roster = await rt.quant_missions.agents()
        assert all(a["status"] != "working" for a in roster)
    finally:
        await rt.stop()
