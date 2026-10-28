"""APEX P5: the paper forward log — live analyses on a schedule, evidence from live plans only.

Generated test data; the tests check when the log runs, what counts as forward evidence (live,
not replayed; one plan per day, side and entry; the current strategy version) and that only
real data can lift a strategy to FORWARD_SUPPORTED.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jarvis.apex.engine import STRATEGIES
from jarvis.runtime import Runtime
from tests.conftest import make_settings

T = int(datetime(2026, 3, 18, 15, 7, tzinfo=UTC).timestamp())  # Wednesday 11:07 New York
SATURDAY = int(datetime(2026, 3, 21, 15, 7, tzinfo=UTC).timestamp())


@pytest.fixture
async def rt(tmp_path: Path) -> Any:
    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture", "history_days": 40})
    runtime = Runtime(settings.model_copy(update={"apex": apex}))
    runtime.apex._now = lambda: T  # type: ignore[method-assign]
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


async def test_the_forward_log_is_opt_in_and_survives_a_restart(rt: Runtime) -> None:
    assert (await rt.apex.forward())["enabled"] is False
    on = await rt.apex.set_forward(True)
    assert on["enabled"] is True and rt.preferences.get("apex_forward") is True
    assert rt.apex._forward_task is not None and not rt.apex._forward_task.done()
    off = await rt.apex.set_forward(False)
    assert off["enabled"] is False and rt.apex._forward_task is None


async def test_a_round_analyses_markets_in_session_and_records_live_signals(rt: Runtime) -> None:
    status = await rt.apex.forward_tick()
    assert {s["state"] for s in status.values()} == {"ok"}  # 11:07 New York: all sessions open
    signals = await rt.apex.signals("XAUUSD")
    assert signals and all(not s["replay"] for s in signals)
    assert {s["strategy_id"] for s in signals} == set(STRATEGIES)
    rt.apex._now = lambda: SATURDAY  # type: ignore[method-assign]
    quiet = await rt.apex.forward_tick()
    assert {s["state"] for s in quiet.values()} == {"outside_session"}


async def _journal(rt: Runtime, rows: list[dict[str, Any]], *, synthetic: bool) -> None:
    await rt.apex.store.add_analysis(
        "apa-forward",
        {"market": "XAUUSD", "as_of": T, "data": {"source": "dukascopy", "synthetic": synthetic,
                                                     "sha256": "h"}},
    )  # fmt: skip
    for k, row in enumerate(rows):
        c = {
            "id": f"apx-{k}",
            "template": row.get("template", "zone_rejection_5m"),
            "strategy_version": row.get("version", STRATEGIES["zone_rejection_5m"]),
            "market": "XAUUSD",
            "direction": "long",
            "created_at": row["at"],
            "valid_until": row["at"] + 900,
            "entry": {"type": "stop", "price": row["entry"]},
            "stop": row["entry"] - 2,
            "targets": [{"price": row["entry"] + 4, "rr": 1.9}],
            "synthetic": synthetic,
            "replay": row.get("replay", False),
        }
        await rt.apex.store.add_candidate("apa-forward", c)
        await rt.apex.store.set_outcome(
            c["id"], {"state": row["state"], "final": True, "r": row["r"], "filled_at": row["at"],
                      "exit_at": row["at"] + 600},
        )  # fmt: skip


async def test_forward_evidence_counts_each_live_plan_once(rt: Runtime) -> None:
    day = T - 40 * 86400
    rows = []
    for k in range(34):
        at = day + k * 86400
        rows.append({"at": at, "entry": 2000.0 + k, "state": "win", "r": 1.8})
        rows.append({"at": at + 300, "entry": 2000.0 + k, "state": "win", "r": 1.8})  # re-made
    rows.append({"at": day, "entry": 1500.0, "state": "loss", "r": -1.0, "replay": True})
    rows.append({"at": day, "entry": 1600.0, "state": "loss", "r": -1.0, "version": "0.9.0"})
    await _journal(rt, rows, synthetic=False)
    real = await rt.apex.forward_evidence("XAUUSD", "zone_rejection_5m", synthetic=False)
    assert real["filled"] == 34 and real["trade_days"] == 34  # duplicates, replays, old version out
    assert real["passed"] and real["needed"] == 0
    test_data = await rt.apex.forward_evidence("XAUUSD", "zone_rejection_5m", synthetic=True)
    assert test_data["filled"] == 0 and not test_data["passed"]


async def test_forward_evidence_lifts_only_an_oos_supported_version_on_real_data(
    rt: Runtime,
) -> None:
    finished = iter(range(10))

    async def validation(state: str, synthetic: bool) -> None:
        result = {"verdict": {"validation_state": state}}
        await rt.db.execute(
            "INSERT INTO apex_validations (id, market, strategy, version, protocol_version, "
            "source_id, state, progress, synthetic, data_sha256, result, created_at, finished_at) "
            "VALUES (?, 'XAUUSD', 'zone_rejection_5m', ?, '1.1.0', 'dukascopy:XAUUSD', "
            "'COMPLETED', 1, ?, 'h', ?, ?, ?)",
            (f"apv-{state}-{synthetic}", STRATEGIES["zone_rejection_5m"], int(synthetic),
             json.dumps(result), datetime.now(UTC).isoformat(),
             (datetime.now(UTC) + timedelta(seconds=next(finished))).isoformat()),
        )  # fmt: skip

    await validation("OOS_SUPPORTED", False)
    states, _ = await rt.apex._validation_states("XAUUSD", False)
    assert states["zone_rejection_5m"] == "OOS_SUPPORTED"  # no forward trades yet
    day = T - 40 * 86400
    await _journal(rt, [{"at": day + k * 86400, "entry": 2000.0 + k, "state": "win", "r": 1.8}
                        for k in range(31)], synthetic=False)  # fmt: skip
    states, _ = await rt.apex._validation_states("XAUUSD", False)
    assert states["zone_rejection_5m"] == "FORWARD_SUPPORTED"
    # A REJECTED version is never lifted, however good the forward trades look.
    await validation("REJECTED", False)
    states, _ = await rt.apex._validation_states("XAUUSD", False)
    assert states["zone_rejection_5m"] == "REJECTED"


async def test_validation_runs_appear_as_cipher_work_in_the_hq(rt: Runtime) -> None:
    started = await rt.apex.validate("XAUUSD", "zone_reaction")
    rows = await rt.apex.hq_tasks("2000-01-01")
    assert any(r["id"] == started["id"] and r["agent"] == "cipher" for r in rows)
    await rt.apex.cancel_validation(started["id"])
    await rt.apex.wait_validation("XAUUSD")
