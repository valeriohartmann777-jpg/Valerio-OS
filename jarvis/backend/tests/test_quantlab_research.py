"""QuantLab research runs end to end on the offline fixture provider, plus the
validation statistics and the verdict policy in isolation.

The fixture's prices are synthetic: these tests check plumbing, accounting,
reproducibility and honesty rules — never that a strategy "works".
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from jarvis.quantlab.futures import validation as val
from jarvis.quantlab.futures.sessions import Window
from jarvis.quantlab.futures.spec import parse, template
from jarvis.quantlab.hub import fixture
from jarvis.runtime import Runtime
from tests.conftest import make_settings

REQUEST = {
    "dataset": "GLBX.MDP3",
    "schema": "ohlcv-1m",
    "stype_in": "continuous",
    "symbols": ["NQ.v.0"],
    "start": "2025-11-03",
    "end": "2026-03-03",
}


def small_spec(**rule: Any) -> dict[str, Any]:
    raw = template("opening_range_breakout", "NQ")
    raw["rule"].update(rule)
    raw["validation"]["walk_forward"] = {
        "train_sessions": 30,
        "test_sessions": 10,
        "mode": "rolling",
    }
    raw["validation"]["parameter_grid"] = {"range_minutes": [10, 15, 20], "target_value": [1.5, 2]}
    raw["validation"]["min_trades_oos"] = 10
    raw["validation"]["bootstrap"]["samples"] = 400
    return raw


@pytest.fixture
async def lab(tmp_path: Path) -> Any:
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
        yield rt
    finally:
        await rt.stop()


async def dataset(rt: Runtime) -> dict[str, Any]:
    await rt.hub.connect(fixture.VALID_KEY)
    quote = await rt.hub.quote(REQUEST)
    await rt.hub.approve(quote["id"], quote["cost_usd"] + 1)
    await rt.hub.wait_idle(120)
    return await rt.hub.build_dataset({**REQUEST, "symbol": "NQ.v.0"})


async def finish(rt: Runtime, run: dict[str, Any]) -> dict[str, Any]:
    await rt.research.wait_idle(300)
    return await rt.research.run(run["id"])


async def test_backtest_validation_holdout_and_reproduction(lab: Runtime) -> None:
    ds = await dataset(lab)
    assert ds["fixture"] is True
    strategy = await lab.research.create_strategy(small_spec())
    version = strategy["versions"][0]
    assert version["state"] == "READY"
    assert any("known only once the range" in line for line in version["description"])

    backtest = await finish(lab, await lab.research.start_run(version["id"], ds["id"]))
    assert backtest["status"] == "COMPLETED", backtest["error"]
    summary = backtest["summary"]
    assert summary["verdict"] is None and summary["tests"] == []  # a backtest gives no verdict
    assert all(c["result"] == "PASS" for c in summary["audit"]["conservative"])
    assert summary["fitness"]["status"] == "FIT_WITH_LIMITATIONS"
    codes = {r["code"] for r in summary["fitness"]["reasons"]}
    assert {"INTRABAR_ORDER", "NO_SPREAD"} <= codes
    assert summary["contracts"][0]["provenance"] == "definition"
    assert {c["raw_symbol"] for c in summary["contracts"]} <= {"NQH6", "NQZ5"}
    assert "holdout" not in summary["segments"]  # sealed
    # Same request → same run (manifest hash), not a second experiment.
    again = await lab.research.start_run(version["id"], ds["id"])
    assert again["id"] == backtest["id"]

    run = await finish(
        lab, await lab.research.start_run(version["id"], ds["id"], kind="validation")
    )
    assert run["status"] == "COMPLETED", run["error"]
    tests = {t["id"]: t for t in run["summary"]["tests"]}
    assert tests["LEAKAGE"]["status"] == "PASSED"
    assert tests["INDEPENDENT_REFERENCE"]["status"] == "PASSED"
    assert tests["HOLDOUT"]["status"] == "NOT_RUN"
    assert tests["FORWARD_PAPER"]["status"] == "NOT_RUN"
    assert tests["PARAMETER_SENSITIVITY"]["metric"]["grid"]  # in-sample grid ran
    assert tests["WALK_FORWARD"]["status"] in ("PASSED", "WARNING", "FAILED")
    assert tests["SELECTION_BIAS"]["metric"].get("trials", 0) >= 6
    verdict = run["summary"]["verdict"]
    assert verdict["verdict"] == "INSUFFICIENT_EVIDENCE"  # synthetic data never earns more
    assert "SYNTHETIC" in verdict["reasons"][0] and verdict["would_be"] in val.VERDICTS
    for t in run["summary"]["tests"]:
        assert {"assumptions", "requirements", "interpretation", "status"} <= set(t)

    # Trade explorer, chart, report, reproduction.
    trades = await lab.research.trades(run["id"], 0, 5)
    assert trades["total"] > 10
    first = trades["rows"][0]
    assert first["signal_known_at"] <= first["entry_time"]
    detail = await lab.research.trade(run["id"], first["number"])
    assert detail["bars"] and detail["range"]["known_at"] == first["signal_known_at"]
    assert detail["costs"]["total"] == pytest.approx(
        detail["costs"]["commission"] + detail["costs"]["exchange_fees"] + first["slippage_cost"]
    )
    chart = await lab.research.chart(run["id"])
    assert chart["equity"] and chart["candles"] and len(chart["markers"]) == trades["total"]
    report = await lab.research.report(run["id"])
    assert "SYNTHETIC FIXTURE DATA" in report and "Results SHA-256" in report
    assert "places no orders" in report
    reproduced = await lab.research.reproduce(run["id"])
    assert reproduced["identical"] is True

    # The holdout needs an explicit confirmation, then counts as looked-at.
    with pytest.raises(Exception) as unconfirmed:
        await lab.research.start_run(
            version["id"], ds["id"], kind="validation", include_holdout=True
        )
    assert getattr(unconfirmed.value, "code", "") == "HOLDOUT_NOT_CONFIRMED"
    sealed = await finish(
        lab,
        await lab.research.start_run(
            version["id"], ds["id"], kind="validation", include_holdout=True, confirm_holdout=True
        ),
    )
    holdout = next(t for t in sealed["summary"]["tests"] if t["id"] == "HOLDOUT")
    assert holdout["status"] != "NOT_RUN" and holdout["metric"]["independent"] is True
    assert "holdout" in sealed["summary"]["segments"]

    # A second version looking at the same holdout is no longer independent.
    edited = small_spec(range_minutes=20)
    strategy = await lab.research.add_version(strategy["id"], edited, note="wider range")
    second = strategy["versions"][-1]
    assert second["number"] == 2
    look = await finish(
        lab,
        await lab.research.start_run(
            second["id"], ds["id"], kind="validation", include_holdout=True, confirm_holdout=True
        ),
    )
    holdout2 = next(t for t in look["summary"]["tests"] if t["id"] == "HOLDOUT")
    assert holdout2["metric"]["independent"] is False
    detail = await lab.research.strategy(strategy["id"])
    assert len(detail["holdouts"]) == 2
    assert detail["trials"]["variants"] >= 7  # every grid point and version counts

    compared = await lab.research.compare([run["id"], look["id"]])
    assert any(d["field"] == "rule.range_minutes" for d in compared["spec_differences"])


async def test_runs_refuse_unknowns_and_wrong_instruments(lab: Runtime) -> None:
    ds = await dataset(lab)
    raw = small_spec()
    raw["assumptions"].append({"field": "costs.commission_per_contract_side", "state": "unknown",
                               "note": "Which broker?"})  # fmt: skip
    strategy = await lab.research.create_strategy(raw)
    version = strategy["versions"][0]
    assert version["state"] == "DRAFT"
    with pytest.raises(Exception) as unknown:
        await lab.research.start_run(version["id"], ds["id"])
    assert getattr(unknown.value, "code", "") == "REQUIRES_CLARIFICATION"

    micro = small_spec()
    micro["instrument"] = {"product": "MNQ", "dataset": "GLBX.MDP3", "symbol": "MNQ.v.0",
                           "stype_in": "continuous"}  # fmt: skip
    strategy = await lab.research.create_strategy(micro)
    run = await finish(lab, await lab.research.start_run(strategy["versions"][0]["id"], ds["id"]))
    fitness = run["summary"]["fitness"]
    assert fitness["status"] == "INVALID"  # MNQ rules on NQ data: multipliers differ tenfold
    assert {"INSTRUMENT_MISMATCH"} <= {r["code"] for r in fitness["reasons"]}


async def test_cancel_and_restart(lab: Runtime) -> None:
    ds = await dataset(lab)
    strategy = await lab.research.create_strategy(small_spec())
    run = await lab.research.start_run(strategy["versions"][0]["id"], ds["id"], kind="validation")
    await lab.research.cancel(run["id"])
    done = await finish(lab, run)
    assert done["status"] == "CANCELED"
    # A cancelled run can be started again with the same manifest.
    rerun = await finish(
        lab,
        await lab.research.start_run(strategy["versions"][0]["id"], ds["id"], kind="validation"),
    )
    assert rerun["id"] == run["id"] and rerun["status"] == "COMPLETED"


def test_api_research_flow(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from jarvis.api.app import create_app

    settings = make_settings(tmp_path)
    settings = settings.model_copy(
        update={
            "quantlab": settings.quantlab.model_copy(
                update={"provider": "fixture", "keystore": "memory"}
            )
        }
    )
    with TestClient(create_app(settings)) as client:
        templates = client.get("/quantlab/research/templates").json()
        assert {t["kind"] for t in templates} == {"opening_range_breakout", "ma_crossover"}
        bad = client.post(
            "/quantlab/research/strategies/check", json={"spec": {"kind": "x"}}
        ).json()
        assert bad["valid"] is False and bad["errors"]
        ok = client.post("/quantlab/research/strategies/check", json={"spec": small_spec()}).json()
        assert ok["valid"] and ok["state"] == "READY" and ok["description"]
        created = client.post("/quantlab/research/strategies", json={"spec": small_spec()})
        assert created.status_code == 200
        missing = client.post(
            "/quantlab/research/runs",
            json={"version_id": created.json()["versions"][0]["id"], "dataset_id": "dh-nope"},
        )
        assert missing.status_code == 404
        note = client.post(
            f"/quantlab/research/strategies/{created.json()['id']}/notes",
            json={"kind": "decision", "text": "Park this until real data is in."},
        ).json()
        assert note["notes"][0]["kind"] == "decision"
        assert client.post("/quantlab/stop-all").json() == {"runs": [], "downloads": []}
        assert client.get("/quantlab/research/overview").json()["hub"]["fixture"] is True


# -- statistics and policy in isolation -------------------------------------------------------


def windows(n: int) -> list[Window]:
    return [
        Window(
            date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + i),
            i,
            i + 1,
            i,
            i,
            i + 1,
            False,
        )
        for i in range(n)
    ]


def test_split_is_chronological_with_embargo() -> None:
    spec = parse(small_spec())
    ws = windows(100)
    sp = val.split(ws, spec)
    assert len(sp.holdout) == 15 and len(sp.oos) == 30 and len(sp.embargo) == 2
    assert len(sp.insample) == 100 - 15 - 30 - 2
    order = [w.label for w in sp.insample + sp.oos + sp.holdout]
    assert order == sorted(order)
    assert sp.insample[-1].label < sp.embargo[0].label < sp.oos[0].label
    assert sp.oos[-1].label < sp.embargo[1].label < sp.holdout[0].label


def test_bootstrap_is_seeded_and_dsr_penalises_many_trials() -> None:
    rng = np.random.default_rng(3)
    daily = list(rng.normal(20, 100, 120))
    a = val.stationary_bootstrap(daily, 5, 300, seed=7)
    b = val.stationary_bootstrap(daily, 5, 300, seed=7)
    assert np.array_equal(a, b) and a.shape == (300, 120)
    returns = [d / 50_000 for d in daily]
    one = val.deflated_sharpe(returns, [], 1)
    many = val.deflated_sharpe(returns, list(rng.normal(0.05, 0.05, 50)), 50)
    assert one["dsr"] is not None and many["dsr"] is not None
    assert many["dsr"] < one["dsr"]  # the same Sharpe is less convincing after 50 tries
    assert val.deflated_sharpe(returns[:10], [], 1)["dsr"] is None


def _t(id_: str, status: str) -> dict[str, Any]:
    return {"id": id_, "status": status}


def test_verdict_policy() -> None:
    passing = [_t(k, "PASSED") for k in val.ROBUSTNESS] + [
        _t("OUT_OF_SAMPLE", "PASSED"), _t("DATA_INTEGRITY", "PASSED"), _t("LEAKAGE", "PASSED"),
        _t("INDEPENDENT_REFERENCE", "PASSED"),
    ]  # fmt: skip

    def with_(**changes: str) -> list[dict[str, Any]]:
        return [_t(t["id"], changes.get(t["id"], t["status"])) for t in passing]

    assert val.verdict(passing, fixture=False)["verdict"] == "FORWARD_VALIDATION_REQUIRED"
    assert (
        val.verdict(with_(LEAKAGE="FAILED"), fixture=False)["verdict"] == "INVALID_DATA_OR_METHOD"
    )
    assert (
        val.verdict(with_(OUT_OF_SAMPLE="INCONCLUSIVE"), fixture=False)["verdict"]
        == "INSUFFICIENT_EVIDENCE"
    )
    assert (
        val.verdict(with_(OUT_OF_SAMPLE="FAILED"), fixture=False)["verdict"]
        == "REJECTED_HYPOTHESIS"
    )
    assert val.verdict(with_(HOLDOUT="FAILED"), fixture=False)["verdict"] == "REJECTED_HYPOTHESIS"
    promising = val.verdict(with_(HOLDOUT="NOT_RUN", COST_STRESS="WARNING"), fixture=False)
    assert promising["verdict"] == "PROMISING_RESEARCH_CANDIDATE"
    assert any("holdout" in s.lower() for s in promising["next_steps"])
    capped = val.verdict(passing, fixture=True)
    assert (
        capped["verdict"] == "INSUFFICIENT_EVIDENCE"
        and capped["would_be"] == "FORWARD_VALIDATION_REQUIRED"
    )
    assert "ROBUST_UNDER_TESTED_ASSUMPTIONS" not in {
        val.verdict(v, fixture=False)["verdict"] for v in (passing, with_(HOLDOUT="NOT_RUN"))
    }
    assert math.isclose(len(val.VERDICTS), 6)
