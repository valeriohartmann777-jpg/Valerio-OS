"""APEX Ultimate U0/U1: capability matrix, evidence statuses and the causal zone map.

Hand-built bars and generated test data (``synthetic_bars``) only — the tests check rules:
a pivot exists only after its right-hand bars closed, one visit is one episode, a zone flips
only after acceptance and a rejected retest, four per side at most (fewer when evidence is
thin), nothing after the moment changes the map, snapshots are immutable, and a price-bar
feed never claims traded volume, delta or absorption.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from jarvis.apex.capability import Evidence, Mode, capability_for, factor_matrix, mode_of
from jarvis.apex.data import SYNTHETIC_LABEL, Series, synthetic_bars
from jarvis.apex.engine import analyze
from jarvis.apex.service import market_contexts
from jarvis.apex.zonemap import MIN_SCORE, Member, ZoneState, _walk, candidates, rank
from jarvis.learning.market import Bars
from jarvis.runtime import Runtime
from jarvis.settings import load_settings
from jarvis.storage.database import Database
from tests.conftest import make_settings

SETTINGS = load_settings(environ={})
CFG = SETTINGS.apex
CONTEXTS = market_contexts(SETTINGS)
GOLD = CONTEXTS["XAUUSD"].spec
T = int(datetime(2026, 3, 18, 15, 0, tzinfo=UTC).timestamp())


def series(source: str, provenance: dict[str, Any], *, synthetic: bool = False) -> Series:
    bars = synthetic_bars("XAUUSD", T, GOLD.tick).between(T - 5 * 86400, T)
    return Series("XAUUSD", source, "label", synthetic, bars, provenance=provenance)


def status(rows: list[dict[str, Any]], factor: str) -> str:
    return str(next(r["status"] for r in rows if r["factor"] == factor))


# -- U0 · what a source can prove -----------------------------------------------------------------


def test_bid_only_spot_never_claims_spread_volume_delta_or_absorption() -> None:
    cap = capability_for(series("dukascopy", {"source_id": "dukascopy:XAUUSD"}), GOLD)
    assert (cap.has_bid, cap.has_ask, cap.has_actual_traded_volume) == (True, False, False)
    assert any("ASK REQUIRED FOR REALISTIC EXECUTION" in r for r in cap.reasons)
    rows = factor_matrix(cap, stale=False)
    assert status(rows, "spread") == Evidence.PROXY
    assert status(rows, "volume_profile") == Evidence.PROXY  # tick counts are not traded volume
    assert status(rows, "vwap") == Evidence.PROXY
    for factor in ("delta", "absorption", "options", "macro_calendar", "cross_market"):
        assert status(rows, factor) == Evidence.UNAVAILABLE
    measured = capability_for(
        series("dukascopy", {"source_id": "dukascopy:XAUUSD", "spread": {"measured": True}}), GOLD
    )
    assert status(factor_matrix(measured, stale=False), "spread") == Evidence.CONFIRMED


def test_futures_bars_give_a_bar_vwap_but_never_a_precise_poc_or_delta() -> None:
    cap = capability_for(series("databento", {"source_id": "vault:NQ", "schema": "ohlcv-1m"}), GOLD)
    rows = factor_matrix(cap, stale=False)
    assert cap.market_type == "futures" and cap.has_actual_traded_volume
    assert status(rows, "vwap") == Evidence.CONFIRMED
    assert status(rows, "volume_profile") == Evidence.PROXY
    assert status(rows, "delta") == Evidence.UNAVAILABLE
    assert status(factor_matrix(cap, stale=True), "price_structure") == Evidence.STALE
    proxy = capability_for(series("dukascopy", {"source_id": "dukascopy:NQ"}), GOLD)
    assert any("not CME NQ/MNQ futures" in r for r in proxy.reasons)


def test_nothing_is_ever_labelled_live_without_a_live_entitlement() -> None:
    real = series("dukascopy", {"source_id": "dukascopy:XAUUSD"})
    test = series("fixture", {}, synthetic=True)
    assert mode_of(test, T, T, replay=False, behind_minutes=0) == Mode.SYNTHETIC
    assert mode_of(real, T - 3600, T, replay=True, behind_minutes=0) == Mode.REPLAY
    assert mode_of(real, T, T, replay=False, behind_minutes=3) == Mode.DELAYED
    assert mode_of(real, T, T, replay=False, behind_minutes=400) == Mode.HISTORICAL


# -- U1 · causal zones ---------------------------------------------------------------------------


def minute_bars(rows: list[tuple[float, float, float, float]], start: int) -> Bars:
    """One-minute bars that build exactly the given 15-minute OHLC rows."""
    t, o, h, lo, c = [], [], [], [], []
    for k, (op, hi, low, cl) in enumerate(rows):
        base = start + k * 900
        path = [op, hi, low] + [cl] * 12  # touches high and low early in the bar
        for m in range(15):
            prev = path[m - 1] if m else op
            t.append(base + 60 * m)
            o.append(prev)
            h.append(max(prev, path[m]))
            lo.append(min(prev, path[m]))
            c.append(path[m])
    n = len(t)
    return Bars(np.array(t, dtype=np.int64), np.array(o), np.array(h), np.array(lo), np.array(c),
                np.ones(n), 1)  # fmt: skip


def staircase() -> list[tuple[float, float, float, float]]:
    rows = []
    price = 100.0
    for _ in range(30):  # gentle rise
        rows.append((price, price + 1.2, price - 0.4, price + 0.5))
        price += 0.5
    rows += [(price, price + 0.3, price - 3.0, price - 2.5)]  # the dip
    low = price - 3.0
    rows += [(price - 2.5, price - 1.0, low - 4.0, price - 2.0)]  # pivot bar: lowest low
    for k in range(4):  # right-hand bars, each with a higher low
        rows.append((price - 2 + k, price - 0.5 + k, low - 2.0 + k, price - 1.5 + k))
    return rows


def test_a_swing_low_exists_only_once_its_right_hand_bars_have_closed() -> None:
    rows = staircase()
    start = T - 900 * len(rows)
    bars = minute_bars(rows, start)
    pivot = 31  # index of the pivot bar
    pivot_low = rows[pivot][2]
    confirm = start + (pivot + 3) * 900 + 900  # close of the third right-hand bar

    def lows(as_of: int) -> list[Member]:
        cut = bars.between(int(bars.t[0]), as_of)
        found = candidates(cut, as_of, 1.0, 0.01, 50.0)
        return [
            m
            for m in found
            if m.kind == "swing" and m.role == "support" and abs(m.low - pivot_low) < 1e-9
        ]

    assert lows(confirm - 900) == []  # one bar early: not a swing yet
    found = lows(confirm)
    assert found and found[0].confirmed == confirm and found[0].formed == start + pivot * 900


Row = tuple[float, float, float, float]


def fifteen(rows: list[Row]) -> Bars:
    a = np.array(rows, dtype=np.float64)
    t = T + np.arange(len(rows), dtype=np.int64) * 900
    return Bars(t, a[:, 0], a[:, 1], a[:, 2], a[:, 3], np.ones(len(rows)), 15)


def support_zone() -> ZoneState:
    return ZoneState(
        100.0, 101.0, [Member("swing", "15m swing low", 100.0, 101.0, T - 7200, T, "support")]
    )


def test_one_visit_is_one_episode_however_many_bars_it_lasts() -> None:
    rows: list[Row] = [(103, 103.5, 102.5, 103)]  # confirmation bar: price above the zone
    rows += [(102, 102.5, 100.6, 100.8)] * 10  # sits in the zone for ten bars
    rows += [(101.5, 104.0, 101.2, 103.8), (103.8, 104.5, 103.5, 104.2)]  # leaves upward
    rows += [(104, 104.4, 103.6, 104.0)] * 8
    z = support_zone()
    _walk(z, fifteen(rows), np.full(len(rows), 1.0), int(T + 900 * len(rows)))
    touches = [e for e in z.episodes if e["kind"] != "flip_retest"]
    assert len(touches) == 1 and touches[0]["kind"] == "rejection"
    assert z.state == "ACTIVE"


def test_a_zone_flips_only_after_acceptance_and_a_rejected_retest() -> None:
    above: list[Row] = [(103, 103.5, 102.5, 103)]
    one_close: list[Row] = [
        (101, 101.2, 99.5, 99.7),
        (99.7, 101.8, 99.6, 101.5),
    ]  # one close below, back
    breakdown: list[Row] = [(101, 101.2, 99.6, 99.7), (99.7, 99.8, 98.6, 98.7)]  # two closes below
    retest: list[Row] = [
        (98.7, 100.4, 98.6, 99.6)
    ]  # back up into the zone from below, closes below
    drop: list[Row] = [(99.6, 99.7, 97.0, 97.2), (97.2, 97.5, 96.8, 97.0)] + [
        (97, 97.2, 96.8, 97)
    ] * 8
    z = support_zone()
    rows = above + one_close + [(101.5, 102, 101.4, 101.8)] * 3
    _walk(z, fifteen(rows), np.full(len(rows), 1.0), T + 900 * len(rows))
    assert z.state != "BROKEN" and z.broken_at is None  # one close below is no break
    z = support_zone()
    rows = above + breakdown + [(98.7, 98.9, 98.4, 98.6)] * 2
    _walk(z, fifteen(rows), np.full(len(rows), 1.0), T + 900 * len(rows))
    assert z.state == "BROKEN" and z.flipped_at is None  # accepted below, no retest yet
    z = support_zone()
    rows = above + breakdown + retest + drop
    _walk(z, fifteen(rows), np.full(len(rows), 1.0), T + 900 * len(rows))
    assert z.state == "FLIPPED" and z.role == "resistance"
    assert [s["state"] for s in z.transitions][:3] == ["CONFIRMED", "BROKEN", "FLIPPED"]
    z = support_zone()
    rows = (
        above
        + breakdown
        + [(98.7, 101.6, 98.6, 101.4), (101.4, 102.0, 101.2, 101.8)]
        + [(101.8, 102, 101.6, 101.9)] * 9
    )
    _walk(z, fifteen(rows), np.full(len(rows), 1.0), T + 900 * len(rows))
    assert z.state != "FLIPPED" and z.fakeouts == 1  # closed back above twice: a fakeout


def fake_zone(zid: str, side: str, score: float, distance: float) -> dict[str, Any]:
    return {"zone_id": zid, "side": side, "state": "ACTIVE", "quality_score": score,
            "rank_value": score, "distance_atr": distance, "rank": None,
            "bounds": {"low": 0.0, "high": 1.0}}  # fmt: skip


def test_four_per_side_at_most_and_fewer_when_the_evidence_is_thin() -> None:
    zones = [fake_zone(f"s{k}", "SUPPORT", 40 + k, k) for k in range(6)]
    zones += [fake_zone(f"r{k}", "RESISTANCE", 50 + k, k) for k in range(7)]
    zones.append(fake_zone("weak", "SUPPORT", MIN_SCORE - 1, 0.1))
    out = rank(zones, "XAUUSD", T, 100.0, 1.0, 3.0)
    assert [z["zone_id"] for z in out["support"]] == ["s5", "s4", "s3", "s2"]
    assert [z["rank"] for z in out["resistance"]] == [1, 2, 3, 4] and not out["notes"]
    thin = rank([fake_zone("r0", "RESISTANCE", 60, 1), fake_zone("r1", "RESISTANCE", 55, 2)],
                "XAUUSD", T, 100.0, 1.0, 3.0)  # fmt: skip
    assert len(thin["resistance"]) == 2 and thin["support"] == []
    assert any("Only 2 resistance" in n for n in thin["notes"])
    assert thin["sell_zone"]["zone_id"] == "r0" and thin["buy_zone"] is None
    assert out["score_is_probability"] is False


@pytest.mark.parametrize("market", ["XAUUSD", "NQ"])
def test_the_zone_map_at_a_moment_ignores_every_later_bar(market: str) -> None:
    ctx = CONTEXTS[market]
    full = synthetic_bars(market, T + 2 * 86400, ctx.spec.tick).between(
        T - 40 * 86400, T + 2 * 86400
    )
    a = Series(market, "fixture", SYNTHETIC_LABEL, True, full)
    later = full.t >= T

    def lift(x: np.ndarray) -> np.ndarray:
        return np.where(later, x * 1.2, x)

    shifted = Bars(
        full.t, lift(full.open), lift(full.high), lift(full.low), lift(full.close), full.volume, 1
    )
    b = Series(market, "fixture", SYNTHETIC_LABEL, True, shifted)
    left, right = analyze(a, ctx, CFG, T), analyze(b, ctx, CFG, T)
    assert left["zone_map"] == right["zone_map"]
    zm = left["zone_map"]
    assert 1 <= len(zm["support"]) <= 4 and 1 <= len(zm["resistance"]) <= 4
    for z in zm["support"] + zm["resistance"]:
        assert z["confirmed_time_utc"] <= zm["as_of_utc"] and z["quality_score"] >= MIN_SCORE
        assert z["state"] in ("CONFIRMED", "ACTIVE", "TESTED", "WEAKENING", "FLIPPED")
        assert {e["status"] for e in z["evidence"]} <= {s.value for s in Evidence}
        assert next(e for e in z["evidence"] if e["factor"] == "delta")["status"] == "UNAVAILABLE"
    lows = [z["bounds"]["low"] for z in zm["support"] + zm["resistance"]]
    assert all(z["bounds"]["high"] < zm["price"] or z["inside"] for z in zm["support"])
    assert len(set(lows)) == len(lows)


# -- immutability and the migration ---------------------------------------------------------------


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


async def test_zone_snapshots_are_stored_once_and_never_changed(rt: Runtime) -> None:
    out = await rt.apex.analyze("XAUUSD")
    assert out["mode"] == "SYNTHETIC" and out["capability"]["market_type"] == "generated"
    snapshot = await rt.apex.zones("XAUUSD")
    assert snapshot["snapshot_id"] == out["zone_snapshot_id"]
    assert snapshot["support"] == out["zone_map"]["support"]
    again = await rt.apex.analyze("XAUUSD")  # the same moment: the same snapshot, stored once
    assert again["zone_snapshot_id"] == out["zone_snapshot_id"]
    assert len(snapshot["history"]) == 1
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute("UPDATE apex_zone_snapshots SET payload = '{}'")
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute("DELETE FROM apex_zone_snapshots")


async def test_migrations_21_and_22_roll_back_and_apply_again(tmp_path: Path) -> None:
    db = Database(tmp_path / "jarvis.db")
    await db.connect()
    await db.rollback_to(20)
    names = {r["name"] for r in await db.fetch_all("SELECT name FROM sqlite_master")}
    assert not {"apex_zone_snapshots", "apex_macro_events", "apex_signals"} & names
    await db.close()
    again = Database(tmp_path / "jarvis.db")
    await again.connect()
    names = {r["name"] for r in await again.fetch_all("SELECT name FROM sqlite_master")}
    assert {"apex_zone_snapshots", "apex_macro_events", "apex_signals"} <= names
    await again.close()
