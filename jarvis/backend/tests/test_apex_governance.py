"""APEX Ultimate U7: governance — scenario M and acceptance test 35.

Agents can propose a probability head, SENTINEL reviews and vetoes what isn't proven, only the
owner approves, and nobody can place orders, relax the 0.70 gate, reuse a sealed holdout or
erase a trial. An approved head is the only way a probability can ever reach the gate; every
step is an immutable event.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.apex.governance import (
    AGENTS,
    NEVER,
    OWNER,
    GovernanceError,
    authorize,
    sentinel_review,
    state_of,
)
from jarvis.apex.service import ApexError
from jarvis.api.app import create_app
from jarvis.runtime import Runtime
from tests.conftest import make_settings

GOOD = {"id": "ptp-good", "validated": True, "synthetic": False, "protocol_version": "1.2.0",
        "version": "1.0.0", "source_id": "vault:NQ", "verdict": {"code": "VALIDATED"}}  # fmt: skip


@pytest.mark.parametrize("agent", AGENTS)
def test_no_agent_can_approve_pay_or_read_secrets(agent: str) -> None:
    authorize(agent, "propose_promotion")  # proposing is fine
    for action in ("approve_promotion", "approve_paid_data", "read_secret", "revoke_promotion"):
        with pytest.raises(GovernanceError) as err:
            authorize(agent, action)
        assert err.value.code == "OWNER_ONLY"


@pytest.mark.parametrize("action", NEVER)
def test_some_actions_are_locked_for_everyone_the_owner_too(action: str) -> None:
    for actor in (OWNER, *AGENTS):
        with pytest.raises(GovernanceError) as err:
            authorize(actor, action)
        assert err.value.code == "LOCKED"
    with pytest.raises(GovernanceError):
        authorize("mallory", "propose_promotion")


def test_sentinel_vetoes_every_unproven_head() -> None:
    ok = sentinel_review(GOOD, protocol_version="1.2.0", strategy_state="OOS_SUPPORTED",
                         audit_failed=False)  # fmt: skip
    assert not ok["vetoed"] and all(c["passed"] for c in ok["checks"])
    for change, failed in (
        ({"validated": False, "verdict": {"code": "NOT_VALIDATED"}}, "HEAD_VALIDATED"),
        ({"synthetic": True}, "REAL_DATA"),
        ({"protocol_version": "1.1.0"}, "PROTOCOL"),
    ):
        out = sentinel_review({**GOOD, **change}, protocol_version="1.2.0",
                              strategy_state="OOS_SUPPORTED", audit_failed=False)  # fmt: skip
        assert out["vetoed"] and failed in {c["check"] for c in out["checks"] if not c["passed"]}
    for state, audit in ((None, False), ("REJECTED", False), ("RESEARCH_ONLY", False),
                         ("OOS_SUPPORTED", True)):  # fmt: skip
        assert sentinel_review(GOOD, protocol_version="1.2.0", strategy_state=state,
                               audit_failed=audit)["vetoed"]  # fmt: skip
    assert (
        state_of([]) == "NONE"
        and state_of([{"state": "PROPOSED"}, {"state": "VETOED"}]) == "VETOED"
    )


@pytest.fixture
async def rt(tmp_path: Path) -> Any:
    settings = make_settings(tmp_path)
    runtime = Runtime(settings.model_copy(update={"apex": settings.apex.model_copy(
        update={"provider": "fixture"})}))  # fmt: skip
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


def _head(**over: Any) -> dict[str, Any]:
    return {
        "id": "ptp-test", "market": "NQ", "setup": "sweep_reclaim", "version": "1.0.0",
        "protocol_version": "1.2.0", "source_id": "vault:NQ", "synthetic": False,
        "validated": True, "validation_id": "apv-x", "verdict": {"code": "VALIDATED", "text": ""},
        "params": {"mean": [0, 0, 0, 0, 0], "std": [1, 1, 1, 1, 1], "coef": [0, 0, 0, 0, 0],
                   "intercept": 2.0, "platt": {"a": 1.0, "b": 0.0}},
        "bins": [{"bin": [k / 10, (k + 1) / 10], "n": 50, "n_eff": 40.0, "predicted": 0.85,
                  "observed": 0.84, "ci": [0.72, 0.92]} for k in range(10)],
        "counts": {"fit": 100, "calibration": 40, "test": 50}, **over,
    }  # fmt: skip


async def test_a_head_without_strategy_evidence_is_vetoed_and_cannot_be_approved(
    rt: Runtime,
) -> None:
    await rt.apex.store.add_probability_model(_head())
    out = await rt.apex.propose_promotion("ptp-test", "cipher")
    assert out["state"] == "VETOED"  # the setup version never passed a holdout
    assert "STRATEGY_HOLDOUT" in {c["check"] for c in out["review"]["checks"] if not c["passed"]}
    with pytest.raises(ApexError) as err:
        await rt.apex.approve_promotion("ptp-test", "owner", confirm=True)
    assert err.value.code == "VETOED"
    with pytest.raises(GovernanceError):
        await rt.apex.approve_promotion("ptp-test", "sentinel", confirm=True)
    plan = {"setup": "sweep_reclaim", "break_even_probability": 0.3, "zone_quality": 80,
            "risk_points": 10, "atr15": 10, "direction": "long", "with_trend": True}  # fmt: skip
    p = await rt.apex._probability("NQ", plan, source_id="vault:NQ", synthetic=False)
    assert p["validated"] and not p["promoted"] and p["promotion"] == "VETOED"
    assert "NOT PROMOTED" in str(p["label"])
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute("UPDATE apex_promotions SET state = 'APPROVED'")


async def test_only_the_owner_approves_a_cleared_head_and_only_then_it_counts(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.apex.signal import ValidationState

    async def supported(market: str, synthetic: bool) -> Any:
        return {"sweep_reclaim": ValidationState.OOS_SUPPORTED}, {"sweep_reclaim": "apv-x"}

    monkeypatch.setattr(rt.apex, "_validation_states", supported)
    await rt.apex.store.add_probability_model(_head())
    plan = {"setup": "sweep_reclaim", "break_even_probability": 0.3, "zone_quality": 80,
            "risk_points": 10, "atr15": 10, "direction": "long", "with_trend": True}  # fmt: skip
    with pytest.raises(ApexError) as err:
        await rt.apex.approve_promotion("ptp-test", "owner", confirm=True)
    assert err.value.code == "NOT_REVIEWED"  # SENTINEL reviews before anyone can approve
    out = await rt.apex.propose_promotion("ptp-test", "cipher")
    assert out["state"] == "REVIEWED"
    assert not (await rt.apex._probability("NQ", plan, source_id="vault:NQ", synthetic=False))[
        "promoted"
    ]
    with pytest.raises(ApexError):
        await rt.apex.approve_promotion("ptp-test", "owner", confirm=False)
    assert (await rt.apex.approve_promotion("ptp-test", "owner", confirm=True))[
        "state"
    ] == "APPROVED"
    p = await rt.apex._probability("NQ", plan, source_id="vault:NQ", synthetic=False)
    assert p["promoted"] and p["validated"] and p["value"] is not None
    # Not on another source or on generated data.
    other = await rt.apex._probability("NQ", plan, source_id="vault:MNQ", synthetic=False)
    assert other["model_id"] is None
    await rt.apex.revoke_promotion("ptp-test", "owner", "new evidence against it")
    again = await rt.apex._probability("NQ", plan, source_id="vault:NQ", synthetic=False)
    assert not again["promoted"] and again["promotion"] == "REVOKED"
    events = await rt.apex.store.promotion_events("ptp-test")
    assert [e["state"] for e in events] == ["PROPOSED", "REVIEWED", "APPROVED", "REVOKED"]
    assert [e["actor"] for e in events] == ["cipher", "sentinel", "owner", "owner"]


def test_the_api_refuses_an_agent_approval_and_an_unknown_actor(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture"})
    with TestClient(create_app(settings.model_copy(update={"apex": apex}))) as client:
        bad = client.post("/apex/promotions", json={"model_id": "ptp-none", "actor": "mallory"})
        assert bad.status_code == 422
        missing = client.post("/apex/promotions", json={"model_id": "ptp-none", "actor": "cipher"})
        assert missing.status_code == 404
        no = client.post("/apex/promotions/ptp-none/approve", json={"confirm": False})
        assert no.status_code in (404, 422)


async def test_a_validated_head_is_proposed_by_cipher_and_reviewed_by_sentinel(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.apex.service as service

    def fake_develop(*args: Any, **kwargs: Any) -> dict[str, Any]:
        keep = ("validated", "verdict", "params", "bins", "counts")
        head = {k: v for k, v in _head().items() if k in keep}
        head.update(head_version="ptp-1.0.0", features=["break_even"], windows={})
        return {
            "variants": [{}], "neighbours": [], "stats": {"filled": 120, "mean_r": 0.1},
            "passed": False, "enough": True,
            "criteria": [{"code": "DEV_CI", "required": "", "measured": "", "passed": False}],
            "probability_head": head,
        }  # fmt: skip

    monkeypatch.setattr(service, "develop", fake_develop)
    started = await rt.apex.validate("NQ", "sweep_reclaim")
    await rt.apex.wait_validation("NQ", timeout=120)
    done = await rt.apex.validation(started["id"])
    assert done["state"] == "COMPLETED", done.get("error")
    (model,) = await rt.apex.probability_models("NQ")
    actors = [(e["actor"], e["state"]) for e in model["promotion_events"]]
    # Generated data and a setup that failed development: SENTINEL vetoes, nothing reaches the gate.
    assert actors == [("cipher", "PROPOSED"), ("sentinel", "VETOED")]
    vetoed = {c["check"] for c in model["promotion_events"][-1]["review"]["checks"]
              if not c["passed"]}  # fmt: skip
    assert {"REAL_DATA", "STRATEGY_HOLDOUT"} <= vetoed
