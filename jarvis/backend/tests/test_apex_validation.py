"""APEX P3: the validation protocol — split, embargo, bootstrap, baseline, trials, holdout.

All market data is generated test data (``synthetic_bars``): these tests check the protocol's
mechanics and its honesty (a generated series can never be "supported"), not a market.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jarvis.apex.data import SYNTHETIC_LABEL, Series, synthetic_bars
from jarvis.apex.execution import execution_for
from jarvis.apex.service import market_contexts
from jarvis.apex.validation import (
    baseline,
    day_bootstrap,
    develop,
    embargoed_times,
    horizon_minutes,
    plan_split,
    trading_days,
    verdict,
)
from jarvis.runtime import Runtime
from jarvis.settings import ApexValidationSettings, load_settings
from tests.conftest import make_settings

SETTINGS = load_settings(environ={})
CFG = SETTINGS.apex
CONTEXTS = market_contexts(SETTINGS)
T = int(datetime(2026, 3, 18, 15, 7, tzinfo=UTC).timestamp())
QUICK = ApexValidationSettings().model_copy(
    update={
        "evidence": ApexValidationSettings().evidence.model_copy(
            update={"bootstrap_resamples": 300, "baseline_runs": 20}
        )
    }
)


def fixture(market: str = "XAUUSD", days: int = 70) -> Series:
    bars = synthetic_bars(market, T, CONTEXTS[market].spec.tick)
    return Series(market, "fixture", SYNTHETIC_LABEL, True, bars.between(T - days * 86400, T))


def test_the_registered_protocol_file_is_what_the_code_judges_with() -> None:
    assert SETTINGS.apex_validation == ApexValidationSettings()
    assert SETTINGS.apex_validation.protocol_version == "1.1.0"


# -- split and embargo --------------------------------------------------------------------------


def test_the_split_is_chronological_and_the_holdout_stays_frozen() -> None:
    days = trading_days(fixture())
    assert days == sorted(days) and all(d.weekday() < 5 for d in days)
    split = plan_split(days, QUICK, None)
    folds = split["folds"]
    assert len(folds) == QUICK.split.folds
    assert all(a[1] == b[0] for a, b in pairwise(folds))  # contiguous
    assert split["dev"][1] == split["holdout"][0]  # development ends where the holdout starts
    first_dev = datetime.fromtimestamp(split["dev"][0], UTC).date()
    assert first_dev >= days[QUICK.split.warmup_days] - timedelta(days=1)  # warm-up kept out
    # Later, with more days, a locked holdout is reused exactly.
    more = [*days, *(days[-1] + timedelta(days=k) for k in range(1, 30) if
                     (days[-1] + timedelta(days=k)).weekday() < 5)]  # fmt: skip
    again = plan_split(more, QUICK, split["holdout"])
    assert again["holdout"] == split["holdout"] and again["dev"][1] == split["holdout"][0]
    with pytest.raises(ValueError, match="too few"):
        plan_split(days[:20], QUICK, None)


def test_no_decision_has_an_outcome_window_reaching_into_the_next_period() -> None:
    ctx = CONTEXTS["XAUUSD"]
    split = plan_split(trading_days(fixture()), QUICK, None)
    for strategy in ("zone_reaction", "zone_rejection_5m"):
        embargo = max(QUICK.split.embargo_minutes, horizon_minutes(strategy, CFG))
        assert embargo >= horizon_minutes(strategy, CFG)
        for fold in split["folds"]:
            times = embargoed_times(fold, ctx, 60, embargo)
            assert times and all(fold[0] <= t and t + 60 * embargo <= fold[1] for t in times)
    assert horizon_minutes("zone_reaction", CFG) == CFG.valid_minutes + CFG.max_hold_minutes


# -- statistics ---------------------------------------------------------------------------------


def test_the_day_bootstrap_resamples_whole_days() -> None:
    r = np.array([1.0, 1.0, -1.0, -1.0, 2.0, 2.0])
    days = np.array([1, 1, 2, 2, 3, 3])
    ci = day_bootstrap(r, days, 2000, seed=1)
    assert ci is not None and ci[0] <= float(r.mean()) <= ci[1]
    # Two trades on the same day are one observation, not two.
    assert day_bootstrap(np.array([1.0, 2.0]), np.array([5, 5]), 100, seed=1) is None
    assert day_bootstrap(np.array([1.0]), np.array([5]), 100, seed=1) is None


def test_the_random_baseline_only_beats_chance_when_choice_matters() -> None:
    series = fixture()
    execution = execution_for(series, CONTEXTS["XAUUSD"].spec)
    split = plan_split(trading_days(series), QUICK, None)
    times = embargoed_times(split["dev"], CONTEXTS["XAUUSD"], 60, 720)
    sample = int(times[len(times) // 2])
    price = float(series.bars.close[np.searchsorted(series.bars.t, sample) - 1])
    trades = [
        {"state": "win", "r": 2.0, "entry": price, "stop": price - 2, "target": price + 4,
         "day": k} for k in range(30)
    ]  # fmt: skip
    strong = baseline(trades, series, execution, times, runs=30, seed=3,
                      strategy_mean=2.0, hold_minutes=480)  # fmt: skip
    assert strong["p_value"] is not None and strong["p_value"] < 0.05  # always +2 R: rare by chance
    weak = baseline(trades, series, execution, times, runs=30, seed=3,
                    strategy_mean=-5.0, hold_minutes=480)  # fmt: skip
    assert weak["p_value"] == 1.0  # every random run did at least as well
    assert -1.5 < strong["mean_r"] < 1.0


# -- the development measurements ---------------------------------------------------------------


def test_development_measures_everything_and_reconciles_to_its_ledger() -> None:
    series = fixture()
    ctx = CONTEXTS["XAUUSD"]
    split = plan_split(trading_days(series), QUICK, None)
    dev = develop(series, ctx, CFG, QUICK, "zone_reaction", execution_for(series, ctx.spec),
                  split, prior_trials=3, seed=11)  # fmt: skip
    codes = [c["code"] for c in dev["criteria"]]
    assert codes == ["EVIDENCE", "DEV_CI", "BASELINE", "COST_STRESS", "NEIGHBOURS", "FOLDS"]
    assert len(dev["neighbours"]) == 6 and dev["trials"]["total"] == 3 + 7
    assert dev["trials"]["alpha_corrected"] == pytest.approx(0.05 / 10)
    ledger_r = sum(t["r"] for t in dev["ledger"] if t["state"] in ("win", "loss", "timeout"))
    assert dev["stats"]["total_r"] == pytest.approx(ledger_r, abs=1e-3)
    assert sum(f["filled"] for f in dev["folds"]) == dev["stats"]["filled"]
    # Costs only make it worse.
    if dev["stats"]["mean_r"] is not None:
        assert dev["cost_stress"]["mean_r"] <= dev["stats"]["mean_r"]
    # Every trade belongs to the development window, none to the holdout.
    assert all(split["dev"][0] <= t["at"] < split["holdout"][0] for t in dev["ledger"])
    assert develop(series, ctx, CFG, QUICK, "zone_reaction", execution_for(series, ctx.spec),
                   split, prior_trials=3, seed=11) == dev  # fmt: skip  # reproducible


def test_the_verdict_never_overstates() -> None:
    criteria = [{"code": c, "passed": True} for c in ("EVIDENCE", "DEV_CI", "BASELINE")]
    good = {"enough": True, "passed": True, "criteria": criteria}
    thin = {"enough": False, "passed": False, "criteria": criteria}
    bad = {"enough": True, "passed": False,
           "criteria": [*criteria, {"code": "FOLDS", "passed": False}]}  # fmt: skip
    held_ok = {"enough": True, "passed": True}
    assert verdict(thin, None, synthetic=False)["validation_state"] == "RESEARCH_ONLY"
    rejected = verdict(bad, None, synthetic=False)
    assert rejected["validation_state"] == "REJECTED" and "FOLDS" in rejected["text"]
    assert (
        verdict(good, {"enough": False, "passed": False}, synthetic=False)["validation_state"]
        == "RESEARCH_ONLY"
    )
    assert (
        verdict(good, {"enough": True, "passed": False}, synthetic=False)["validation_state"]
        == "REJECTED"
    )
    assert verdict(good, held_ok, synthetic=False)["validation_state"] == "OOS_SUPPORTED"
    assert verdict(good, held_ok, synthetic=True)["validation_state"] == "RESEARCH_ONLY"


# -- the service: trials, frozen holdout, cancel ------------------------------------------------


@pytest.fixture
async def rt(tmp_path: Path) -> Any:
    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture", "history_days": 45})
    runtime = Runtime(settings.model_copy(update={"apex": apex, "apex_validation": QUICK}))
    runtime.apex._now = lambda: T  # type: ignore[method-assign]
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


async def test_a_validation_run_registers_its_trials_and_freezes_the_holdout(rt: Runtime) -> None:
    started = await rt.apex.validate("XAUUSD", "zone_reaction")
    assert started["state"] == "RUNNING" and started["id"].startswith("apv-")
    await rt.apex.wait_validation("XAUUSD")
    done = await rt.apex.validation(started["id"])
    assert done["state"] == "COMPLETED", done.get("error")
    result = done["result"]
    assert result["protocol_version"] == "1.1.0" and result["data"]["synthetic"]
    assert result["verdict"]["validation_state"] == "RESEARCH_ONLY"  # generated data, never more
    assert "Generated test data" in result["verdict"]["text"]
    trials = await rt.apex.trials("XAUUSD", "zone_reaction")
    assert len(trials["synthetic"]) == 7 and not trials["real"]  # registered + 6 neighbours
    assert {t["kind"] for t in trials["synthetic"]} == {"registered", "neighbour"}
    holdout = await rt.apex.store.holdout("XAUUSD", "fixture", True)
    assert holdout is not None
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute('UPDATE apex_holdouts SET "end" = 0')
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute("DELETE FROM apex_trials")
    if not result["development"]["passed"]:  # failed or thin: the holdout stays closed
        assert result["holdout"] is None
        assert await rt.apex.store.holdout_opened(
            "XAUUSD", "fixture", True, "zone_reaction", "1.1.0"
        ) is None  # fmt: skip
    # A second run counts the earlier trials (stricter alpha) and keeps the same holdout.
    again = await rt.apex.validate("XAUUSD", "zone_reaction")
    await rt.apex.wait_validation("XAUUSD")
    second = (await rt.apex.validation(again["id"]))["result"]
    assert second["development"]["trials"]["total"] == 14
    assert second["split"]["holdout"] == result["split"]["holdout"]
    # The new signals of this strategy carry the validation state and its evidence.
    analysis = await rt.apex.analyze("XAUUSD")
    rule = [s for s in analysis["signals"] if s["strategy_id"] == "zone_reaction"]
    assert rule and all(s["validation_state"] == "RESEARCH_ONLY" for s in rule)
    assert all(again["id"] in s["evidence_ids"] for s in rule)


async def test_a_running_validation_can_be_cancelled(rt: Runtime) -> None:
    started = await rt.apex.validate("NQ", "zone_rejection_5m")
    with pytest.raises(Exception, match="already running"):
        await rt.apex.validate("NQ", "zone_reaction")
    await rt.apex.cancel_validation(started["id"])
    await rt.apex.wait_validation("NQ")
    assert (await rt.apex.validation(started["id"]))["state"] == "CANCELLED"


async def test_a_holdout_is_opened_once_per_strategy_version(rt: Runtime) -> None:
    await rt.db.execute(
        "INSERT INTO apex_validations (id, market, strategy, version, protocol_version, "
        "source_id, state, progress, synthetic, data_sha256, created_at) VALUES "
        "('apv-x', 'XAUUSD', 'zone_reaction', '1.1.0', '1.1.0', 'fixture', 'COMPLETED', 1, 1, "
        "'h', 'now')"
    )
    row = {"market": "XAUUSD", "source_id": "fixture", "synthetic": True,
           "strategy": "zone_reaction", "version": "1.1.0", "validation_id": "apv-x",
           "result": {"passed": False}}  # fmt: skip
    assert await rt.apex.store.open_holdout(row)
    assert not await rt.apex.store.open_holdout({**row, "result": {"passed": True}})
    opened = await rt.apex.store.holdout_opened("XAUUSD", "fixture", True, "zone_reaction", "1.1.0")
    assert opened is not None and opened["result"] == {"passed": False}  # the first one stands


def test_the_validation_api_starts_lists_and_refuses_unknown_strategies(tmp_path: Path) -> None:
    from jarvis.api.app import create_app

    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture", "history_days": 45})
    app = create_app(settings.model_copy(update={"apex": apex, "apex_validation": QUICK}))
    with TestClient(app) as client:
        client.app.state.runtime.apex._now = lambda: T  # type: ignore[attr-defined]
        bad = client.post("/apex/validations", json={"market": "XAUUSD", "strategy": "magic"})
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "UNKNOWN_STRATEGY"
        ok = client.post(
            "/apex/validations", json={"market": "XAUUSD", "strategy": "zone_reaction"}
        )
        assert ok.status_code == 200 and ok.json()["state"] == "RUNNING"
        listed = client.get("/apex/validations", params={"market": "XAUUSD"}).json()
        assert listed[0]["id"] == ok.json()["id"]
        cancelled = client.post(f"/apex/validations/{ok.json()['id']}/cancel")
        assert cancelled.status_code in (200, 409)  # 409 when it already finished
        assert client.get("/apex/trials", params={"market": "XAUUSD", "strategy": "zone_reaction"}
                          ).status_code == 200  # fmt: skip


def test_holdout_dates_are_new_york_midnights() -> None:
    split = plan_split(trading_days(fixture()), QUICK, None)
    start = datetime.fromtimestamp(split["holdout"][0], UTC)
    assert start.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).hour == 0
    assert isinstance(datetime.fromtimestamp(split["holdout"][1], UTC).date(), date)
