"""APEX P2: SETUP → TRIGGER → ORDER_PLAN, the execution model, persisted signals.

Bars here are hand-built or generated test data (``synthetic_bars``); nothing is a market
result. The tests check rules: when a trigger counts, which price side fills, that a proxy
never gets an order plan, that every stored signal satisfies the contract and stays as written.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jarvis.apex import trigger
from jarvis.apex.data import SYNTHETIC_LABEL, Series, synthetic_bars
from jarvis.apex.engine import analyze, plans_at
from jarvis.apex.execution import Execution, execution_for, fill_model
from jarvis.apex.resolve import resolve
from jarvis.apex.service import market_contexts
from jarvis.apex.signal import EntryType, Signal, SignalStatus, ValidationState, from_analysis
from jarvis.learning.market import Bars
from jarvis.runtime import Runtime
from jarvis.settings import ApexSettings, load_settings
from jarvis.storage.database import MIGRATIONS, Database
from tests.conftest import make_settings

SETTINGS = load_settings(environ={})
CFG: ApexSettings = SETTINGS.apex
CONTEXTS = market_contexts(SETTINGS)
GOLD = CONTEXTS["XAUUSD"].spec
T = int(datetime(2026, 3, 18, 15, 5, tzinfo=UTC).timestamp())  # a 5-minute boundary, 11:05 NY


def zone(zid: str, side: str, low: float, high: float, inside: bool = False) -> dict[str, Any]:
    return {"id": zid, "side": side, "low": low, "high": high, "inside": inside}


ZONES = [
    zone("S1", "support", 99.0, 100.0),
    zone("S2", "support", 95.0, 96.0),
    zone("R1", "resistance", 106.0, 107.0),
    zone("R2", "resistance", 109.0, 110.0),
]


def five(rows: list[tuple[float, float, float, float]], end: int = T) -> Bars:
    """5-minute bars whose last one closed at ``end``."""
    a = np.array(rows, dtype=np.float64)
    t = end - 300 * len(rows) + np.arange(len(rows), dtype=np.int64) * 300
    return Bars(t, a[:, 0], a[:, 1], a[:, 2], a[:, 3], np.ones(len(rows)), 5)


def minutes(rows: list[tuple[float, float, float, float]], start: int = T) -> Bars:
    a = np.array(rows, dtype=np.float64)
    t = start + np.arange(len(rows), dtype=np.int64) * 60
    return Bars(t, a[:, 0], a[:, 1], a[:, 2], a[:, 3], np.ones(len(rows)))


def evaluate(bars5: Bars, price: float, *, as_of: int = T, zones: Any = None) -> dict[str, Any]:
    return trigger.evaluate(
        "XAUUSD", zones or ZONES, bars5, price=price, atr15=2.0, as_of=as_of,
        trend_60="up", spec=GOLD, cfg=CFG,
    )  # fmt: skip


# -- 1 · the 5-minute trigger -------------------------------------------------------------------


def test_a_bullish_rejection_of_s1_triggers_a_buy_stop_with_valid_geometry() -> None:
    out = evaluate(five([(101, 101.5, 100.6, 100.8), (100.3, 101.2, 99.6, 101.0)]), price=101.0)
    assert out["status"] == "CANDIDATE" and out["direction"] == "long"
    plan = out["candidate"]
    assert plan["entry"] == {"type": "stop", "price": 101.21}  # one tick above the trigger bar
    assert plan["stop"] == pytest.approx(98.7)  # below min(bar low, S1 low) by 0.15 ATR
    assert [t["price"] for t in plan["targets"]] == [105.99, 108.99]  # a tick before R1, R2
    assert plan["stop"] < plan["entry"]["price"] < plan["targets"][0]["price"]
    assert plan["targets"][0]["rr"] == pytest.approx((105.99 - 101.21 - 0.40) / 2.51, abs=0.01)
    assert plan["valid_until"] == T + 60 * CFG.trigger_valid_minutes
    assert out["trigger"]["closed_utc"] == T and plan["strategy_version"] == trigger.VERSION


def test_without_a_confirmed_trigger_it_waits_and_says_what_is_missing() -> None:
    # Touched S1 but closed inside it: setup yes, trigger no.
    out = evaluate(five([(100.3, 100.5, 99.5, 99.8)]), price=99.8)
    assert out["status"] == "WAITING_FOR_TRIGGER" and out["candidate"] is None
    assert out["setups"][0]["zone"] == "S1"
    assert "close back out of it" in " ".join(out["reasons"])
    # The last 5-minute bar closed 15 minutes ago → no fresh trigger either.
    stale = evaluate(five([(100.3, 101.2, 99.6, 101.0)], end=T - 900), price=101.0)
    assert stale["status"] == "WAITING_FOR_TRIGGER"
    assert "no closed 5-minute bar" in " ".join(stale["reasons"])
    # No zone within reach → NO_TRADE, nothing to wait for.
    far = evaluate(five([(120.0, 120.5, 119.5, 120.2)]), price=120.2, zones=ZONES[:2])
    assert far["status"] == "NO_TRADE" and not far["setups"]


def test_a_bearish_rejection_of_r1_mirrors_it() -> None:
    out = evaluate(five([(106.7, 107.2, 105.8, 105.9)]), price=105.9)
    assert out["status"] == "CANDIDATE" and out["direction"] == "short"
    plan = out["candidate"]
    assert plan["entry"] == {"type": "stop", "price": 105.79}
    assert plan["stop"] > plan["entry"]["price"] > plan["targets"][0]["price"]
    assert plan["targets"][0]["price"] == 100.01  # a tick above S1


def test_a_plan_with_too_little_reward_is_rejected_with_its_reason() -> None:
    near = [zone("S1", "support", 99.0, 100.0), zone("R1", "resistance", 102.5, 103.0)]
    out = evaluate(five([(100.3, 101.2, 99.6, 101.0)]), price=101.0, zones=near)
    assert out["status"] == "WAITING_FOR_TRIGGER" and out["candidate"] is None
    assert "Reward/risk" in out["rejected"][0]["reason"]


# -- 2 · fills on the right price side ----------------------------------------------------------


def stop_plan(**over: Any) -> dict[str, Any]:
    return {
        "created_at": T,
        "direction": "long",
        "entry": {"type": "stop", "price": 101.21},
        "stop": 98.7,
        "targets": [{"price": 105.99}],
        "valid_until": T + 900,
        "max_hold_minutes": 480,
        **over,
    }


def test_a_stop_entry_fills_only_when_the_move_continues_and_gaps_fill_worse() -> None:
    bars = minutes(
        [(101.0, 101.1, 100.9, 101.0), (101.15, 101.5, 101.1, 101.4), (101.4, 106.2, 101.3, 106.0)]
    )
    out = resolve(stop_plan(), bars, cost_points=0.1, now=T + 3600)
    assert out["state"] == "win" and out["filled_at"] == T + 60 and out["fill_price"] == 101.21
    assert out["r"] == pytest.approx((105.99 - 101.21 - 0.1) / 2.51, abs=1e-4)
    gap = resolve(
        stop_plan(), minutes([(101.6, 101.7, 101.5, 101.6), (101.6, 106.5, 101.5, 106)]),
        cost_points=0.0, now=T + 3600,
    )  # fmt: skip
    assert gap["fill_price"] == 101.6 and gap["r"] == pytest.approx((105.99 - 101.6) / 2.51, 1e-3)


def test_a_stop_entry_is_cancelled_when_price_closes_beyond_the_stop_first() -> None:
    bars = minutes([(100.0, 100.2, 98.5, 98.6), (98.6, 101.5, 98.5, 101.4)])
    out = resolve(stop_plan(), bars, cost_points=0.0, now=T + 3600)
    assert out["state"] == "expired" and out["cancelled"] and out["filled_at"] is None


def test_on_bid_data_buys_pay_the_spread_and_short_stops_trigger_on_the_ask() -> None:
    short = {
        "created_at": T, "direction": "short", "entry": {"type": "limit", "price": 100.0},
        "stop": 102.0, "targets": [{"price": 96.0}], "valid_until": T + 1800,
        "max_hold_minutes": 60,
    }  # fmt: skip
    bars = minutes(
        [(99.5, 100.1, 99.4, 99.9), (99.9, 101.6, 99.8, 101.0)] + [(101, 101, 100, 100.5)] * 70
    )
    # Bid high 101.6 never reaches the stop 102 — but the ask (bid + 0.5) does.
    assert resolve(short, bars, cost_points=0.0, now=T + 7200)["state"] == "timeout"
    hit = resolve(short, bars, cost_points=0.0, now=T + 7200, spread=0.5)
    assert hit["state"] == "loss" and hit["r"] == pytest.approx(-1.0)
    # A buy limit at 100 needs the ask at 100: bid 99.8 + 0.5 spread is not enough.
    long = {**short, "direction": "long", "stop": 98.0, "targets": [{"price": 104.0}]}
    dip = minutes([(100.5, 100.6, 99.8, 100.2)] + [(100.2, 100.4, 100.1, 100.3)] * 40)
    assert resolve(long, dip, cost_points=0.0, now=T + 7200)["state"] != "pending"
    assert resolve(long, dip, cost_points=0.0, now=T + 7200, spread=0.5)["state"] == "expired"


# -- 3 · the execution model ----------------------------------------------------------------------


def gold_series(provenance: dict[str, Any], *, source: str = "dukascopy") -> Series:
    bars = synthetic_bars("XAUUSD", T, GOLD.tick)
    return Series("XAUUSD", source, "Dukascopy XAU/USD", False, bars, provenance=provenance)


def test_bid_only_gold_is_labelled_limited_with_a_measured_or_estimated_spread() -> None:
    measured = execution_for(
        gold_series({"source_id": "dukascopy:XAUUSD",
                     "spread": {"measured": True, "session_median": 0.35}}), GOLD,
    )  # fmt: skip
    assert measured.assumption == "execution_assumption_limited" and measured.quotes == "bid"
    assert (measured.spread, measured.spread_source) == (0.35, "measured")
    assert measured.costs == pytest.approx(0.05) and measured.total_cost == pytest.approx(0.40)
    estimate = execution_for(gold_series({"source_id": "dukascopy:XAUUSD"}), GOLD)
    assert (estimate.spread, estimate.spread_source) == (GOLD.spread_points, "estimate")
    wide = execution_for(
        gold_series({"source_id": "dukascopy:XAUUSD",
                     "spread": {"measured": True, "session_median": 0.55}}), GOLD,
    )  # fmt: skip
    assert wide.costs == 0.0 and wide.total_cost == pytest.approx(0.55)  # never below the spread
    futures = execution_for(gold_series({"source_id": "vault:NQ"}, source="databento"), GOLD)
    assert futures.quotes == "trades" and futures.spread == 0 and futures.costs == GOLD.cost_points
    stressed = measured.stressed(1.5)
    assert stressed.total_cost == pytest.approx(0.60)
    assert Execution.from_dict(measured.as_dict()) == measured


def test_a_cfd_proxy_never_gets_an_order_plan_for_nq() -> None:
    ctx = CONTEXTS["NQ"]
    bars = synthetic_bars("NQ", T, ctx.spec.tick)
    proxy = Series("NQ", "dukascopy", "Dukascopy USA Tech 100", False, bars,
                   "CFD proxy", provenance={"source_id": "dukascopy:NQ"})  # fmt: skip
    out = analyze(proxy, ctx, CFG, T)
    assert out["execution"]["tradable"] is False and out["candidates"] == []
    assert out["zones"]  # structure and zones are still shown, labelled as proxy
    for decision in out["strategies"].values():
        assert decision["status"] == "BLOCKED"
        assert decision["reasons"][0].startswith("DATA_UNAVAILABLE")
    signals = from_analysis({**out, "id": "apa-x"}, {}, created_at=datetime.now(UTC))
    assert {s.status for s in signals} == {SignalStatus.BLOCKED}
    assert all(s.entry is None and s.entry_type == EntryType.NONE for s in signals)


def test_journaled_candidates_are_judged_with_their_own_execution_model() -> None:
    model = Execution("execution_assumption_limited", "bid", 0.3, "estimate", 0.1)
    assert fill_model({"execution": model.as_dict()}, 0.4) == (0.1, 0.3)
    assert fill_model({}, 0.4) == (0.4, 0.0)  # recorded before the model: flat costs


# -- 4 · decisions are point-in-time and become valid signals ------------------------------------


@pytest.mark.parametrize("market", ["XAUUSD", "NQ"])
def test_both_strategies_decide_without_look_ahead(market: str) -> None:
    ctx = CONTEXTS[market]
    full = synthetic_bars(market, T + 3 * 86400, ctx.spec.tick)
    series = Series(
        market, "fixture", SYNTHETIC_LABEL, True, full.between(T - 60 * 86400, T + 3 * 86400)
    )
    execution = execution_for(series, ctx.spec)
    for k in range(0, 24 * 12, 7):  # moments every 35 minutes over a day
        when = T - k * 300
        a, _ = plans_at(series.until(when), ctx, CFG, when, execution)
        b, _ = plans_at(series, ctx, CFG, when, execution)  # later bars present
        assert a == b


def test_every_signal_of_an_analysis_satisfies_the_contract() -> None:
    seen: set[SignalStatus] = set()
    for market in ("XAUUSD", "NQ"):
        ctx = CONTEXTS[market]
        bars = synthetic_bars(market, T, ctx.spec.tick)
        series = Series(market, "fixture", SYNTHETIC_LABEL, True, bars.between(T - 90 * 86400, T))
        for k in range(0, 60):
            when = T - k * 3600
            out = analyze(series, ctx, CFG, when)
            signals = from_analysis({**out, "id": "apa-x"}, {}, created_at=datetime.now(UTC))
            names = {s.strategy_id for s in signals}
            assert names == {"zone_reaction", "zone_rejection_5m"}
            for s in signals:
                seen.add(s.status)
                assert s.validation_state == ValidationState.UNVALIDATED
                assert s.execution_assumption == "synthetic_flat_costs"
                if s.strategy_id == "zone_rejection_5m" and s.status == SignalStatus.CANDIDATE:
                    assert s.entry_type == EntryType.STOP and s.trigger is not None
                    assert s.timeframe == "5m"
                Signal.model_validate(s.as_record())  # survives a round trip through JSON
    assert SignalStatus.CANDIDATE in seen and len(seen) >= 2


# -- 5 · storage: immutable signals, reversible migration ---------------------------------------


@pytest.fixture
async def rt(tmp_path: Path) -> Any:
    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture", "history_days": 60})
    runtime = Runtime(settings.model_copy(update={"apex": apex}))
    runtime.apex._now = lambda: T  # type: ignore[method-assign]
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


async def test_each_analysis_records_its_signals_once_and_never_changes_them(rt: Runtime) -> None:
    out = await rt.apex.analyze("XAUUSD")
    assert {s["strategy_id"] for s in out["signals"]} == {"zone_reaction", "zone_rejection_5m"}
    stored = await rt.apex.signals("XAUUSD")
    assert {s["signal_id"] for s in stored} == {s["signal_id"] for s in out["signals"]}
    assert all(s["synthetic"] for s in stored)
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute("UPDATE apex_signals SET status = 'CANDIDATE'")
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute("DELETE FROM apex_signals")
    await rt.apex.analyze("XAUUSD")  # the same moment again
    assert len(await rt.apex.signals("XAUUSD")) == len(stored)


async def test_migration_21_rolls_back_and_applies_again(tmp_path: Path) -> None:
    db = Database(tmp_path / "jarvis.db")
    await db.connect()
    await db.execute(
        "INSERT INTO apex_analyses VALUES ('apa-1', 'XAUUSD', 1, 'x', 'fixture', 1, 'h', '{}')"
    )
    await db.rollback_to(20)
    tables = {r["name"] for r in await db.fetch_all("SELECT name FROM sqlite_master")}
    assert "apex_signals" not in tables and "apex_analyses" in tables
    kept = await db.fetch_one("SELECT COUNT(*) AS n FROM apex_analyses")
    assert kept is not None and kept["n"] == 1  # existing data stays
    assert list((tmp_path / "backups").glob("jarvis-v*-*.db"))  # copied before the rollback
    await db.close()
    again = Database(tmp_path / "jarvis.db")
    await again.connect()
    row = await again.fetch_one("SELECT MAX(version) AS v FROM schema_version")
    assert row is not None and row["v"] == len(MIGRATIONS)
    assert await again.fetch_one("SELECT 1 FROM sqlite_master WHERE name = 'apex_signals'")
    await again.close()


def test_the_signal_journal_is_served_read_only(tmp_path: Path) -> None:
    from jarvis.api.app import create_app

    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture", "history_days": 60})
    with TestClient(create_app(settings.model_copy(update={"apex": apex}))) as client:
        client.app.state.runtime.apex._now = lambda: T  # type: ignore[attr-defined]
        assert client.post("/apex/analyze", json={"market": "XAUUSD"}).status_code == 200
        rows = client.get("/apex/signals", params={"market": "XAUUSD"}).json()
        assert rows and all(r["symbol"] == "XAUUSD" for r in rows)
        only = client.get("/apex/signals", params={"strategy": "zone_rejection_5m"}).json()
        assert {r["strategy_id"] for r in only} == {"zone_rejection_5m"}
        bad = client.post("/apex/evaluations", json={"market": "XAUUSD", "strategy": "magic"})
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "UNKNOWN_STRATEGY"


async def test_sentinel_passes_an_analysis_whose_plans_carry_a_track_record(rt: Runtime) -> None:
    await rt.apex.evaluate("XAUUSD", days=10)
    await rt.apex.wait_idle()
    out = await rt.apex.analyze("XAUUSD", as_of=T - 86400 - 10 * 3600)  # a moment with plans
    assert out["candidates"]
    assert all(c["track_record"] for c in out["candidates"] if c["template"] == "zone_reaction")
    audit = await rt.apex.audit(out["id"])
    assert audit["passed"], audit["result"]["checks"]
