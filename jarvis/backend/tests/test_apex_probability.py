"""APEX Ultimate U4: the trade-success probability head P(TP before SL) — protocol 1.2.0.

Generated trades with a known truth: a random walk (outcomes independent of every feature)
must never be validated; tiny samples give no model at all; a head whose outcomes really follow
the features is validated with a calibrated number — the mechanics, on generated data only.
Fit, calibration and test windows never overlap, the holdout is never read, the same input
gives the same head, and a stored head is immutable.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from jarvis.apex import eligibility, setups
from jarvis.apex.probability import effective_sample, features, fit_head, label, model_id, predict
from jarvis.runtime import Runtime
from jarvis.settings import ApexProbabilitySettings, load_settings
from tests.conftest import make_settings

SETTINGS = load_settings(environ={})
CFG = SETTINGS.apex_validation.probability
T0 = int(datetime(2026, 1, 5, 15, 0, tzinfo=UTC).timestamp())
DAY = 86400
HORIZON = 15 + 480


def trades(
    n_days: int, per_day: int, truth: Any, *, seed: int = 1, start: int = T0
) -> list[dict[str, Any]]:
    """``per_day`` trades a day; ``truth(x) -> P(win)`` decides each outcome."""
    rng = np.random.default_rng(seed)
    out = []
    for d in range(n_days):
        for k in range(per_day):
            x = [float(rng.uniform(0.2, 0.6)), float(rng.uniform(0.3, 0.9)),
                 float(rng.integers(0, 2)), float(rng.integers(0, 2)),
                 float(rng.uniform(0.5, 2.5))]  # fmt: skip
            win = rng.random() < truth(x)
            out.append({"id": f"t{d}-{k}", "at": start + d * DAY + k * 1800, "day": d,
                        "state": "win" if win else "loss", "features": x})  # fmt: skip
    return out


def logistic(x: list[float]) -> float:
    return float(1 / (1 + np.exp(-(-6.0 + 13.0 * x[1] - 2.0 * x[0]))))


def dev_of(rows: list[dict[str, Any]]) -> tuple[int, int]:
    return T0, int(rows[-1]["at"]) + HORIZON * 60 + DAY


# -- 29 · tiny samples and random walks never validate -------------------------------------------


def test_a_tiny_sample_gives_no_model_and_no_number() -> None:
    rows = trades(6, 3, lambda x: 0.8)  # "80 % from a handful of trades"
    head = fit_head(rows, dev_of(rows), HORIZON, CFG, seed=1)
    assert head["verdict"]["code"] == "INSUFFICIENT_DATA" and not head["validated"]
    out = predict(head, {"break_even_probability": 0.4, "zone_quality": 80, "risk_points": 10,
                         "atr15": 10, "direction": "long", "with_trend": True})  # fmt: skip
    assert out["value"] is None and out["label"] == "PROBABILITY NOT VALIDATED"


@pytest.mark.parametrize("seed", [3, 4, 5])
def test_a_random_walk_negative_control_is_never_validated(seed: int) -> None:
    rows = trades(160, 8, lambda x: 0.45, seed=seed)  # outcomes ignore every feature
    head = fit_head(rows, dev_of(rows), HORIZON, CFG, seed=seed)
    assert head["verdict"]["code"] == "NOT_VALIDATED" and not head["validated"]
    failed = {c["code"] for c in head["criteria"] if not c["passed"]}
    assert failed & {"SKILL", "PERMUTATION"}  # no skill beyond chance
    plan = {"break_even_probability": 0.3, "zone_quality": 90, "risk_points": 10, "atr15": 10,
            "direction": "long", "with_trend": True}  # fmt: skip
    p = predict(head, plan)
    assert p["validated"] is False and p["label"] == "PROBABILITY NOT VALIDATED"
    gate = eligibility.gate({"zone_confirmed_at": 0, "trigger": {"bar_utc": 1, "closed_utc": 2},
                             "zone_state": "ACTIVE", "entry": {"price": 1.0}, "stop": 0.5,
                             "targets": [{"price": 2.0}], "direction": "long", "risk_usd": 0.5,
                             "stressed_costs_points": 0.0}, build="b",
                            quality={"status": "OK"}, mode="DELAYED", data_age_minutes=1,
                            max_age_minutes=10, execution={"quotes": "trades"},
                            capability={}, probability=p, context={"macro": {"status":
                            "CONFIRMED"}}, in_session=True, tick=0.25, point_value=1.0,
                            as_of=3)  # fmt: skip
    assert "PROBABILITY" in gate["failed"]


# -- the mechanics: a real dependence is found, calibrated and graded once -------------------------


def test_a_head_whose_outcomes_follow_the_features_is_validated_and_calibrated() -> None:
    rows = trades(220, 8, logistic, seed=11)
    head = fit_head(rows, dev_of(rows), HORIZON, CFG, seed=11)
    assert head["validated"], head["criteria"]
    m = head["metrics"]
    assert m["brier"] < m["brier_base_rate"] and m["brier_skill"] > head["permutation"]["p95_skill"]
    assert abs(m["calibration_slope"] - 1) <= CFG.max_slope_error and abs(m["mean_gap"]) <= 0.05
    assert m["log_loss"] > 0 and head["permutation"]["runs"] == CFG.permutation_runs
    assert sum(b["n"] for b in head["bins"]) == head["counts"]["test"]
    strong = {"break_even_probability": 0.25, "zone_quality": 88, "risk_points": 10,
              "atr15": 10, "direction": "long", "with_trend": True}  # fmt: skip
    p = predict({**head, "id": "ptp-x"}, strong)
    assert p["validated"] and p["calibrated"] and p["value"] > 0.7 and p["model_id"] == "ptp-x"
    assert p["uncertainty_lower"] <= p["bin"]["observed"] <= p["uncertainty_upper"]
    weak = {**strong, "zone_quality": 40}
    assert predict(head, weak)["value"] < p["value"]


def test_the_windows_are_consecutive_embargoed_and_the_holdout_is_never_read() -> None:
    rows = trades(220, 8, logistic, seed=11)
    dev = dev_of(rows)
    later = trades(30, 8, lambda x: 1.0, seed=2, start=dev[1] + DAY)  # "holdout": all wins
    a = fit_head(rows, dev, HORIZON, CFG, seed=11)
    b = fit_head(rows + later, dev, HORIZON, CFG, seed=11)
    assert a == b  # what lies after the development window changes nothing
    (f0, f1), (c0, c1), (t0, t1) = (a["windows"][k] for k in ("fit", "calibration", "test"))
    assert f0 == dev[0] and f1 == c0 and c1 == t0 and t1 == dev[1]
    assert a["counts"]["embargoed"] > 0  # trades whose label horizon crossed a boundary
    assert a["counts"]["fit"] + a["counts"]["calibration"] + a["counts"]["test"] + a["counts"][
        "embargoed"
    ] == len(rows)


def test_unfilled_plans_are_outside_the_event_and_counted_as_fill_rate() -> None:
    rows = trades(220, 8, logistic, seed=11)
    for r in rows[::5]:
        r["state"] = "expired"
    head = fit_head(rows, dev_of(rows), HORIZON, CFG, seed=11)
    assert head["counts"]["unfilled"] == len(rows[::5]) and 0.75 < head["fill_rate"] < 0.85
    assert label("expired") is None and label("missed") is None
    assert label("timeout") == 0 and label("loss") == 0 and label("win") == 1


def test_the_same_trades_give_the_same_head_and_id() -> None:
    rows = trades(220, 8, logistic, seed=11)
    a = fit_head(rows, dev_of(rows), HORIZON, CFG, seed=7)
    b = fit_head(rows, dev_of(rows), HORIZON, CFG, seed=7)
    assert a == b
    record = {**a, "market": "NQ", "setup": "sweep_reclaim", "version": "1.0.0", "source": "x",
              "protocol": "1.2.0"}  # fmt: skip
    assert model_id(record) == model_id(dict(record))


def test_day_clustering_shrinks_the_effective_sample() -> None:
    same_day = np.array([1.0] * 10 + [0.0] * 10)
    days = np.array([0] * 10 + [1] * 10)
    assert effective_sample(same_day, days) < 3  # two days, outcomes identical within each
    rng = np.random.default_rng(0)
    independent = rng.integers(0, 2, 400).astype(float)
    assert effective_sample(independent, np.repeat(np.arange(100), 4)) > 300


def test_older_strategies_have_no_features_and_never_get_a_number() -> None:
    assert features({"entry": {"price": 1}, "stop": 0.5, "direction": "long"}) is None
    assert predict(None, {})["label"] == "PROBABILITY NOT VALIDATED"


def test_the_registered_protocol_carries_the_probability_section() -> None:
    assert SETTINGS.apex_validation.protocol_version == "1.2.0"
    assert ApexProbabilitySettings() == CFG
    assert eligibility.MIN_PROBABILITY == 0.70 and eligibility.PREFERRED == 0.80


# -- 30 / 31 · the service: every head is registered, stored once, used only on its own data ------


@pytest.fixture
async def rt(tmp_path: Path) -> Any:
    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture", "history_days": 45})
    protocol = settings.apex_validation.model_copy(update={"neighbours": {}})
    runtime = Runtime(settings.model_copy(update={"apex": apex, "apex_validation": protocol}))
    runtime.apex._now = lambda: T0 + 60 * DAY  # type: ignore[method-assign]
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


async def test_validating_a_setup_stores_its_head_and_registers_the_trial(rt: Runtime) -> None:
    started = await rt.apex.validate("NQ", setups.REJECTION_ROTATION)
    await rt.apex.wait_validation("NQ", timeout=600)
    done = await rt.apex.validation(started["id"])
    assert done["state"] == "COMPLETED", done.get("error")
    head = done["result"]["development"]["probability_head"]
    assert head["event_definition"] == setups.EVENT and not head["validated"]
    models = await rt.apex.probability_models("NQ")
    assert [m["id"] for m in models] == [head["id"]] and models[0]["synthetic"]
    trials = await rt.apex.trials("NQ", setups.REJECTION_ROTATION)
    kinds = [t["kind"] for t in trials["synthetic"]]
    assert "probability_head" in kinds and "registered" in kinds  # failures stay registered
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute("UPDATE apex_probability_models SET validated = 1")
    plan = {"setup": setups.REJECTION_ROTATION, "break_even_probability": 0.3,
            "zone_quality": 80, "risk_points": 10, "atr15": 10, "direction": "long",
            "with_trend": True}  # fmt: skip
    # Used on its own kind of data and source only — never on real data.
    real = await rt.apex._probability("NQ", plan, source_id=models[0]["source_id"],
                                      synthetic=False)  # fmt: skip
    assert real["model_id"] is None and real["label"] == "PROBABILITY NOT VALIDATED"
    own = await rt.apex._probability("NQ", plan, source_id=models[0]["source_id"],
                                     synthetic=True)  # fmt: skip
    assert own["validated"] is False
