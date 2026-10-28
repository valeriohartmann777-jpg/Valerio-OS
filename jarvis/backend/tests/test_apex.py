"""JARVIS APEX — the analysis core (APEX-1).

All market data here is generated test data (``apex.data.synthetic_bars``) or hand-built
bars; nothing in these tests is a market result. The tests check mechanics: no look-ahead,
conservative resolution, honest rates, immutable records.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from jarvis.apex.candidates import generate
from jarvis.apex.data import (
    SYNTHETIC_LABEL,
    Series,
    assess,
    current_contract,
    from_table,
    synthetic_bars,
    trading_minutes,
)
from jarvis.apex.engine import analyze, plans_at
from jarvis.apex.evaluate import decision_times, summarize, track_record, verdict
from jarvis.apex.execution import execution_for
from jarvis.apex.resolve import resolve
from jarvis.apex.service import market_contexts
from jarvis.apex.stats import clustered_mean, wilson
from jarvis.apex.structure import structure, swings
from jarvis.learning.market import Bars
from jarvis.runtime import Runtime
from jarvis.settings import ApexSettings, load_settings
from tests.conftest import make_settings

SETTINGS = load_settings(environ={})
CFG: ApexSettings = SETTINGS.apex
CONTEXTS = market_contexts(SETTINGS)
# A Wednesday, 11:07 New York: inside both session windows (gold 03:00-13:30, NQ 09:30-16:00).
T = int(datetime(2026, 3, 18, 15, 7, tzinfo=UTC).timestamp())


def fixture_series(market: str, end: int, days: int = 160) -> Series:
    bars = synthetic_bars(market, end, CONTEXTS[market].spec.tick)
    return Series(market, "fixture", SYNTHETIC_LABEL, True, bars.between(end - days * 86400, end))


def bars_from(close: list[float], *, minutes: int = 1, start: int = T, spread: float = 0.1) -> Bars:
    c = np.array(close, dtype=np.float64)
    o = np.r_[c[0], c[:-1]]
    t = start + np.arange(len(c), dtype=np.int64) * 60 * minutes
    return Bars(
        t, o, np.maximum(o, c) + spread, np.minimum(o, c) - spread, c, np.ones(len(c)), minutes
    )


# -- 1 · no look-ahead ------------------------------------------------------------------------


@pytest.mark.parametrize("market", ["XAUUSD", "NQ"])
def test_an_analysis_never_sees_bars_after_its_moment(market: str) -> None:
    ctx = CONTEXTS[market]
    full = fixture_series(market, T + 5 * 86400)
    cut = full.until(T)
    assert int(cut.bars.t[-1]) + 60 <= T < int(full.bars.t[-1])
    reference = analyze(cut, ctx, CFG, T)
    assert analyze(full, ctx, CFG, T) == reference
    # Rewriting everything after T changes nothing about the analysis at T.
    later = full.bars.t >= T
    shift = np.where(later, 1.3, 1.0)
    tampered = Series(
        market,
        full.source,
        full.label,
        True,
        Bars(
            full.bars.t,
            full.bars.open * shift,
            full.bars.high * shift,
            full.bars.low * shift,
            full.bars.close * shift,
            full.bars.volume,
        ),
    )
    assert analyze(tampered, ctx, CFG, T) == reference
    assert reference["data"]["synthetic"] is True and reference["quality"]["status"] in (
        "OK",
        "WARN",
    )
    assert any(f["code"] == "SYNTHETIC" for f in reference["quality"]["findings"])


def test_an_unfinished_higher_timeframe_bar_is_not_used() -> None:
    ctx = CONTEXTS["XAUUSD"]
    series = fixture_series("XAUUSD", T + 86400)
    at_open = T - 7 * 60  # 11:00 New York: the 15-minute bar 11:00-11:15 has just begun
    a = analyze(series, ctx, CFG, at_open + 60)  # one minute into it
    b = analyze(series, ctx, CFG, at_open + 14 * 60)  # fourteen minutes into it
    assert a["structure"]["15"]["bars"] == b["structure"]["15"]["bars"]
    assert a["atr15"] == b["atr15"]


def test_synthetic_test_data_is_reproducible_and_extends_itself() -> None:
    short = synthetic_bars("NQ", T, 0.25)
    long = synthetic_bars("NQ", T + 10 * 86400, 0.25)
    assert np.array_equal(long.close[: len(short)], short.close)
    assert np.all(np.isclose(np.round(short.close / 0.25) * 0.25, short.close))
    assert np.all(short.high >= np.maximum(short.open, short.close))
    assert np.all(short.low <= np.minimum(short.open, short.close))


# -- 2 · structure ----------------------------------------------------------------------------


def test_a_swing_exists_only_after_its_confirming_bars_closed() -> None:
    path = [10, 11, 12, 13, 12, 11, 10, 11, 12, 13, 14, 15, 14, 13, 12, 13, 14, 15, 16, 17]
    bars = bars_from([float(x) for x in path], minutes=15)
    n = 2
    found = swings(bars, n)
    first_high = next(s for s in found if s.kind == "high")
    assert first_high.price == pytest.approx(13.1) and first_high.label == ""
    swing_bar = int(np.flatnonzero(bars.t == first_high.t)[0])
    assert first_high.confirmed == int(bars.t[swing_bar + n]) + 15 * 60
    before = bars.between(int(bars.t[0]), int(bars.t[swing_bar + n]))  # n-1 bars after it
    assert all(s.t != first_high.t for s in swings(before, n))


def test_structure_labels_trend_and_breaks() -> None:
    path = [10, 12, 14, 12, 11, 13, 16, 14, 13, 15, 18, 16, 15, 17, 20, 18, 17, 19, 21, 22]
    s = structure(bars_from([float(x) for x in path], minutes=15), 1)
    labels = [sw["label"] for sw in s["swings"] if sw["label"]]
    assert set(labels) <= {"HH", "HL"} and "HH" in labels and "HL" in labels
    assert s["trend"] == "up"
    assert any(e["type"] == "BOS" and e["direction"] == "up" for e in s["events"])
    down = structure(bars_from([float(40 - x) for x in path], minutes=15), 1)
    assert down["trend"] == "down"
    # A rally that closes above the last lower high in a downtrend is a change of character.
    reversal = structure(bars_from([float(40 - x) for x in path] + [21, 23, 26, 28], minutes=15), 1)
    assert any(e["type"] == "CHoCH" and e["direction"] == "up" for e in reversal["events"])


# -- 3 · zones and candidates -------------------------------------------------------------------


def test_zones_have_width_reasons_and_nearest_first_ids() -> None:
    out = analyze(fixture_series("XAUUSD", T), CONTEXTS["XAUUSD"], CFG, T)
    zones = out["zones"]
    price = out["price"]
    supports = [z for z in zones if z["side"] == "support"]
    resistances = [z for z in zones if z["side"] == "resistance"]
    assert supports and resistances
    assert [z["id"] for z in supports] == [f"S{k}" for k in range(1, len(supports) + 1)]
    assert all(a["high"] >= b["high"] for a, b in pairwise(supports))
    for z in zones:
        assert z["high"] - z["low"] >= 2 * 0.01 - 1e-9 and z["reasons"]
        if not z["inside"]:
            assert (z["low"] > price) == (z["side"] == "resistance")
    lows = sorted((z["low"], z["high"]) for z in zones)
    assert all(b[0] > a[1] for a, b in pairwise(lows))  # merged: no overlap


def _zone(zid: str, side: str, low: float, high: float, inside: bool = False) -> dict[str, Any]:
    return {
        "id": zid,
        "side": side,
        "low": low,
        "high": high,
        "inside": inside,
        "groups": ["k_round"],
    }


def test_a_long_candidate_follows_its_rules_exactly() -> None:
    spec = CONTEXTS["NQ"].spec
    zones = [
        _zone("R1", "resistance", 20100.0, 20110.0),
        _zone("R2", "resistance", 20200.0, 20210.0),
        _zone("S1", "support", 19980.0, 19990.0),
    ]
    made, rejected = generate(
        "NQ", zones, price=20000.0, atr15=20.0, as_of=T, trend_60="up", spec=spec, cfg=CFG
    )
    [long] = [c for c in made if c["direction"] == "long"]
    assert long["entry"] == {"type": "limit", "price": 19990.0}
    assert long["stop"] == 19977.0  # 19980 - 0.15 x 20 = 19977
    assert [t["price"] for t in long["targets"]] == [20099.75, 20199.75]
    risk = 13.0
    assert long["risk_points"] == risk and long["risk_usd"] == risk * 20
    assert long["targets"][0]["rr"] == round((20099.75 - 19990.0 - 1.0) / risk, 2)
    assert long["valid_until"] == T + 60 * CFG.valid_minutes and long["with_trend"] is True
    assert long["id"].startswith("apx-")
    # R1 is 5 ATR above the price (limit 3): no short, and the reason is kept.
    assert any("ATR away" in r["reason"] for r in rejected if r["direction"] == "short")


@pytest.mark.parametrize(
    ("zones", "price", "reason"),
    [
        (
            [
                _zone("S1", "support", 19995.0, 20005.0, inside=True),
                _zone("R1", "resistance", 20100.0, 20110.0),
            ],
            20000.0,
            "inside",
        ),
        (
            [_zone("S1", "support", 19980.0, 19990.0), _zone("R1", "resistance", 20000.0, 20010.0)],
            19995.0,
            "Reward/risk",
        ),
        (
            [_zone("S1", "support", 19989.5, 19990.0), _zone("R1", "resistance", 20100.0, 20110.0)],
            20000.0,
            "minimum",
        ),
        (
            [_zone("S1", "support", 19800.0, 19810.0), _zone("R1", "resistance", 20100.0, 20110.0)],
            20000.0,
            "ATR away",
        ),
    ],
)
def test_candidates_are_rejected_with_their_reason(
    zones: list[dict[str, Any]], price: float, reason: str
) -> None:
    spec = CONTEXTS["NQ"].spec
    tight = CFG.model_copy(update={"stop_buffer_atr": 0.0})
    made, rejected = generate(
        "NQ", zones, price=price, atr15=20.0, as_of=T, trend_60="range", spec=spec, cfg=tight
    )
    assert not [c for c in made if c["direction"] == "long"]
    assert any(reason in r["reason"] for r in rejected if r["direction"] == "long")


# -- 4 · conservative resolution --------------------------------------------------------------


def _candidate(direction: str = "long", **over: Any) -> dict[str, Any]:
    long = direction == "long"
    return {
        "created_at": T,
        "direction": direction,
        "entry": {"type": "limit", "price": 100.0},
        "stop": 98.0 if long else 102.0,
        "targets": [{"price": 104.0 if long else 96.0}],
        "valid_until": T + 30 * 60,
        "max_hold_minutes": 60,
        **over,
    }


def ohlc(rows: list[tuple[float, float, float, float]], start: int = T) -> Bars:
    a = np.array(rows, dtype=np.float64)
    t = start + np.arange(len(rows), dtype=np.int64) * 60
    return Bars(t, a[:, 0], a[:, 1], a[:, 2], a[:, 3], np.ones(len(rows)))


def test_resolution_rules_are_conservative() -> None:
    now = T + 3 * 3600
    # Stop and target in the same minute after the fill → a loss, net of costs.
    out = resolve(
        _candidate(), ohlc([(101, 101, 99.5, 100), (100, 105, 97, 100)]), cost_points=0.5, now=now
    )
    assert out["state"] == "loss" and out["r"] == pytest.approx((98 - 100 - 0.5) / 2)
    # A target inside the fill bar doesn't count; the next bar decides.
    out = resolve(
        _candidate(),
        ohlc([(101, 104.5, 99.8, 103), (103, 104.2, 102, 104)]),
        cost_points=0.0,
        now=now,
    )
    assert out["state"] == "win" and out["r"] == pytest.approx(2.0) and out["filled_at"] == T
    # Target first, never filled → missed (no chasing); nothing before the fill → expired.
    assert (
        resolve(_candidate(), ohlc([(101, 104.5, 100.5, 104)]), cost_points=0, now=now)["state"]
        == "missed"
    )
    flat = ohlc([(101, 101.5, 100.5, 101)] * 40)
    assert resolve(_candidate(), flat, cost_points=0, now=now)["state"] == "expired"
    # Still open after the maximum hold → closed at that minute's close.
    drift = ohlc([(100.5, 100.6, 99.9, 100.2)] + [(100.2, 100.8, 99.5, 100.5)] * 80)
    out = resolve(_candidate(), drift, cost_points=0, now=now)
    assert out["state"] == "timeout" and out["r"] == pytest.approx(0.25)
    # Not decided yet while data ends before the validity.
    assert (
        resolve(_candidate(), ohlc([(101, 101.5, 100.5, 101)] * 5), cost_points=0, now=T + 300)[
            "state"
        ]
        == "pending"
    )
    # Shorts mirror it.
    out = resolve(
        _candidate("short"),
        ohlc([(99, 100.2, 99, 99.5), (99.5, 99.6, 95.5, 96)]),
        cost_points=0,
        now=now,
    )
    assert out["state"] == "win" and out["r"] == pytest.approx(2.0)


def test_resolution_matches_the_random_walk_theory() -> None:
    """On a driftless random walk, P(+3 before -2) = 2/5 — the resolver must not add a bias."""
    rng = np.random.default_rng(7)
    wins = losses = 0
    for _ in range(300):
        close = 100 + np.cumsum(rng.choice([-0.25, 0.25], size=2500))
        open_ = np.r_[100.0, close[:-1]]
        bars = Bars(
            T + np.arange(2500, dtype=np.int64) * 60,
            open_,
            np.maximum(open_, close),
            np.minimum(open_, close),
            close,
            np.ones(2500),
        )
        c = _candidate(
            stop=98.0, targets=[{"price": 103.0}], valid_until=T + 10**7, max_hold_minutes=10**6
        )
        state = resolve(c, bars, cost_points=0, now=T + 10**7)["state"]
        wins += state == "win"
        losses += state == "loss"
    assert abs(wins / (wins + losses) - 0.4) < 0.07


# -- 5 · quality gate and data ----------------------------------------------------------------


def test_the_quality_gate_blocks_bad_data_and_stale_data_gets_no_candidates() -> None:
    ctx = CONTEXTS["XAUUSD"]
    good = fixture_series("XAUUSD", T)
    bad_high = good.bars.high.copy()
    bad_high[-100] = good.bars.low[-100] - 1  # high below low
    broken = Series(
        "XAUUSD",
        "fixture",
        SYNTHETIC_LABEL,
        True,
        Bars(
            good.bars.t, good.bars.open, bad_high, good.bars.low, good.bars.close, good.bars.volume
        ),
    )
    out = analyze(broken, ctx, CFG, T)
    assert out["blocked"] and out["candidates"] == [] and out["quality"]["status"] == "BLOCK"
    assert any(f["code"] == "OHLC" for f in out["quality"]["findings"])
    # Data that ends two hours before the moment: zones and structure, but no candidates.
    stale = analyze(good.until(T - 2 * 3600), ctx, CFG, T)
    assert stale["quality"]["stale"] and stale["candidates"] == [] and stale["zones"]
    assert "stale" in stale["rejected"][0]["reason"]
    empty = assess(
        Series("NQ", "x", "x", False, good.bars.between(0, 1)),
        T,
        price_range=(1, 2),
        stale_minutes=30,
        window_days=21,
        need_days=120,
    )
    assert empty["status"] == "BLOCK"


def test_futures_bars_keep_the_front_contract_and_never_mix_two() -> None:
    day = T  # 11:07 New York: all 180 minutes fall into one trading day
    rows = []
    for k in range(120):  # two contracts trade at once; the second has more volume
        ts = (day + k * 60) * 1_000_000_000
        rows.append((ts, 1, 20000e9, 20001e9, 19999e9, 20000e9, 10))
        rows.append((ts, 2, 20100e9, 20101e9, 20099e9, 20100e9, 50))
    for k in range(120, 180):  # the old contract alone later the same day
        rows.append(((day + k * 60) * 1_000_000_000, 1, 20000e9, 20001e9, 19999e9, 20000e9, 10))
    table = pa.table(
        {
            name: [r[i] for r in rows]
            for i, name in enumerate(
                ("ts_event", "instrument_id", "open", "high", "low", "close", "volume")
            )
        }
    )
    bars, breaks, contracts = from_table(table)
    assert contracts == [2] and np.all(bars.close == 20100.0) and len(bars) == 120 and breaks == ()
    # Across a roll: levels come only from the current contract.
    series = Series("NQ", "databento", "x", False, bars, breaks=(int(bars.t[60]),))
    assert len(current_contract(series).bars) == 60


def test_trading_calendar_skips_weekend_and_daily_break() -> None:
    saturday = int(datetime(2026, 3, 21, 12, 0, tzinfo=UTC).timestamp())
    assert len(trading_minutes(saturday, saturday + 3600)) == 0
    # 17:00-18:00 New York (21:00-22:00 UTC in March after DST) is the daily break.
    brk = int(datetime(2026, 3, 18, 21, 0, tzinfo=UTC).timestamp())
    assert len(trading_minutes(brk, brk + 3600)) == 0
    assert len(trading_minutes(T, T + 600)) == 10


# -- 6 · rates and track record -----------------------------------------------------------------


def test_rates_come_with_counts_and_say_insufficient_below_the_minimum() -> None:
    lo, hi = wilson(60, 100) or (0, 0)
    assert lo < 0.6 < hi and round(lo, 3) == 0.502
    out = analyze(fixture_series("XAUUSD", T), CONTEXTS["XAUUSD"], CFG, T)
    for sc in out["scenarios"]:
        rate = sc["rate"]
        assert rate["n"] >= 0 and rate["status"] in ("ok", "insufficient")
        if rate["status"] == "ok":
            assert rate["n"] >= CFG.min_samples and rate["ci"][0] <= rate["p"] <= rate["ci"][1]
        else:
            assert rate["p"] is None
    holds = {sc["zone"]: sc["rate"]["p"] for sc in out["scenarios"] if sc["kind"] == "hold"}
    breaks = {sc["zone"]: sc["rate"]["p"] for sc in out["scenarios"] if sc["kind"] == "break"}
    for zone, p in holds.items():
        if p is not None:
            assert p + breaks[zone] == pytest.approx(1.0)
    # Only touches decided before the moment count.
    assert out["base_rates"]["touches"] > 0
    few = CFG.model_copy(update={"min_samples": 10**6})
    assert all(
        sc["rate"]["p"] is None
        for sc in analyze(fixture_series("XAUUSD", T), CONTEXTS["XAUUSD"], few, T)["scenarios"]
    )


def test_the_track_record_uses_the_live_rules_and_is_reproducible() -> None:
    ctx = CONTEXTS["XAUUSD"]
    series = fixture_series("XAUUSD", T, days=90)
    start = datetime.fromtimestamp(T - 30 * 86400, UTC).date()
    end = datetime.fromtimestamp(T - 2 * 86400, UTC).date()
    first = track_record(series, ctx, CFG, start, end)
    assert first == track_record(series, ctx, CFG, start, end)
    assert first["data"]["synthetic"] and first["decisions"] == len(
        decision_times(start, end, ctx.window, CFG.eval_step_minutes)
    )
    trades = first["trades"]
    assert trades
    # Every trade is exactly what the live analysis would have produced at that moment.
    sample = trades[len(trades) // 2]
    known = series.until(sample["at"])
    decisions, _ = plans_at(known, ctx, CFG, sample["at"], execution_for(known, ctx.spec))
    assert sample["id"] in {c["id"] for c in decisions["zone_reaction"]["candidates"]}
    states = first["all"]["states"]
    assert sum(states.values()) == first["all"]["candidates"]
    assert first["all"]["filled"] == states["win"] + states["loss"] + states["timeout"]
    assert first["verdict"]["code"] in ("INSUFFICIENT", "NO_EDGE", "INCONCLUSIVE", "POSITIVE_OOS")


def test_summaries_and_verdicts_never_overstate() -> None:
    few = [{"state": "win", "r": 2.0, "day": 1}] * 5
    assert summarize(few, 30)["win_rate"] is None and summarize(few, 30)["status"] == "insufficient"
    mixed = [{"state": "win", "r": 1.5, "day": d} for d in range(20)] + [
        {"state": "loss", "r": -1.0, "day": d} for d in range(20)
    ]
    s = summarize(mixed, 30)
    assert s["win_rate"] == 0.5 and s["profit_factor"] == 1.5 and s["expectancy_r"]["mean"] == 0.25
    losing = summarize([{"state": "loss", "r": -1.0, "day": d} for d in range(40)], 30)
    assert verdict(s, losing, 30)["code"] == "NO_EDGE"
    assert verdict(summarize(few, 30), s, 30)["code"] == "INSUFFICIENT"
    assert clustered_mean(np.array([1.0, 1.0]), np.array([1, 1]))["ci"] is None


# -- 7 · service, journal, API ------------------------------------------------------------------


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


async def test_analyses_are_stored_and_candidates_journaled_immutably(rt: Runtime) -> None:
    out = await rt.apex.analyze("xauusd")
    assert out["data"]["synthetic"] and out["id"].startswith("apa-")
    assert await rt.apex.store.analysis(out["id"]) == out
    journal = await rt.apex.journal("XAUUSD")
    assert {c["id"] for c in journal["candidates"]} == {c["id"] for c in out["candidates"]}
    if out["candidates"]:
        with pytest.raises(sqlite3.IntegrityError):
            await rt.db.execute("UPDATE apex_candidates SET entry = 1")
        with pytest.raises(sqlite3.IntegrityError):
            await rt.db.execute("DELETE FROM apex_candidates")
    # The same moment again records nothing twice.
    again = await rt.apex.analyze("XAUUSD")
    assert again["id"] == out["id"]
    assert len((await rt.apex.journal("XAUUSD"))["candidates"]) == len(out["candidates"])


async def test_replayed_candidates_are_resolved_on_later_bars(rt: Runtime) -> None:
    past = T - 3 * 86400  # a replay three days back: its candidates can be judged now
    out = await rt.apex.analyze("NQ", as_of=past - past % 60)
    assert out["replay"] is True
    journal = await rt.apex.journal("NQ")
    for c in journal["candidates"]:
        assert c["outcome"]["state"] in ("win", "loss", "timeout", "expired", "missed")
        assert c["outcome"]["final"] is True
    if journal["candidates"]:
        with pytest.raises(sqlite3.IntegrityError):  # a final outcome is never rewritten
            await rt.db.execute("UPDATE apex_outcomes SET state = 'win'")
    assert journal["stats"]["real"]["candidates"] == 0  # test data never counts as real


def test_the_api_covers_markets_analysis_journal_and_track_records(tmp_path: Path) -> None:
    import time

    from fastapi.testclient import TestClient

    from jarvis.api.app import create_app

    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture", "history_days": 60})
    with TestClient(create_app(settings.model_copy(update={"apex": apex}))) as client:
        client.app.state.runtime.apex._now = lambda: T  # type: ignore[attr-defined]
        markets = client.get("/apex/markets").json()
        assert {m["market"] for m in markets} == {"XAUUSD", "NQ", "MNQ"}
        assert all(m["fixture"] for m in markets)
        unknown = client.post("/apex/analyze", json={"market": "ES"})
        assert unknown.json()["detail"]["code"] == "UNKNOWN_MARKET"
        future = client.post("/apex/analyze", json={"market": "NQ", "as_of": T + 3600})
        assert future.status_code == 422 and future.json()["detail"]["code"] == "FUTURE"
        analysis = client.post("/apex/analyze", json={"market": "MNQ"}).json()
        assert analysis["spec"]["point_value"] == 2 and analysis["market"] == "MNQ"
        for c in analysis["candidates"]:
            assert c["risk_usd"] == pytest.approx(c["risk_points"] * 2)
        assert client.get(f"/apex/analyses/{analysis['id']}").json()["id"] == analysis["id"]
        assert client.get("/apex/journal", params={"market": "MNQ"}).status_code == 200
        started = client.post("/apex/evaluations", json={"market": "XAUUSD", "days": 15}).json()
        assert started["state"] == "RUNNING"
        again = client.post("/apex/evaluations", json={"market": "XAUUSD", "days": 15})
        assert again.status_code in (200, 409)  # 409 while the first one still runs
        deadline = time.monotonic() + 120
        done = started
        while time.monotonic() < deadline and done["state"] == "RUNNING":
            time.sleep(0.2)
            done = client.get(f"/apex/evaluations/{started['id']}").json()
        assert done["state"] == "COMPLETED" and done["synthetic"] is True
        assert done["result"]["data"]["synthetic"] and done["result"]["verdict"]["code"]
        xau = next(m for m in client.get("/apex/markets").json() if m["market"] == "XAUUSD")
        assert xau["track_record"]["evaluation_id"] in {started["id"], done["id"]}


async def test_without_local_data_apex_says_where_to_get_it(tmp_path: Path) -> None:
    runtime = Runtime(make_settings(tmp_path))  # provider "local", empty caches
    runtime.apex._now = lambda: T  # type: ignore[method-assign]
    await runtime.start()
    try:
        from jarvis.apex.service import ApexError

        with pytest.raises(ApexError) as err:
            await runtime.apex.analyze("XAUUSD")
        assert err.value.code == "NO_DATA" and "Dukascopy" in err.value.remedy
        with pytest.raises(ApexError) as err:
            await runtime.apex.analyze("NQ")
        assert "Data Vault" in err.value.remedy
    finally:
        await runtime.stop()


# -- 8 · APEX-2: SENTINEL re-check, ULTRON HQ, JARVIS chat tool --------------------------------


async def test_sentinel_recomputes_a_stored_analysis_with_later_data(rt: Runtime) -> None:
    import json

    rt.apex._now = lambda: T - 86400  # type: ignore[method-assign]
    out = await rt.apex.analyze("XAUUSD")  # live, one day before T
    rt.apex._now = lambda: T  # type: ignore[method-assign]  # a day of new bars exists now
    await rt.apex.resolve_open("XAUUSD")
    audit = await rt.apex.audit(out["id"])
    checks = {c["code"]: c for c in audit["result"]["checks"]}
    assert audit["passed"] is True and set(checks) == {"SAME_DATA", "NO_LOOKAHEAD", "OUTCOMES"}
    assert all(c["passed"] for c in checks.values())
    # A stored analysis that was altered afterwards is caught, field by field.
    stored = await rt.apex.store.analysis(out["id"])
    assert stored is not None
    stored["zones"][0]["low"] -= 1.0
    await rt.db.execute(
        "UPDATE apex_analyses SET payload = ? WHERE id = ?", (json.dumps(stored), out["id"])
    )
    bad = await rt.apex.audit(out["id"])
    failed = {c["code"]: c for c in bad["result"]["checks"]}
    assert bad["passed"] is False and "zones" in failed["NO_LOOKAHEAD"]["detail"]
    assert [a["id"] for a in await rt.apex.store.audits("XAUUSD")] == [bad["id"], audit["id"]]
    # The ULTRON HQ shows SENTINEL's re-checks from the stored audits (not invented).
    snapshot = await rt.team_hq.snapshot()
    sentinel = next(r for r in snapshot["robots"] if r["id"] == "sentinel")
    assert any(w["source"] == "apex" for w in sentinel["work"])


async def test_cipher_runs_the_track_record_and_the_hq_shows_it(rt: Runtime) -> None:
    started = await rt.apex.evaluate("NQ", days=12)
    assert started["state"] == "RUNNING"
    await rt.apex.wait_idle(120)
    done = await rt.apex.evaluation(started["id"])
    assert done["state"] == "COMPLETED"
    snapshot = await rt.team_hq.snapshot()
    cipher = next(r for r in snapshot["robots"] if r["id"] == "cipher")
    work = [w for w in cipher["work"] if w["source"] == "apex"]
    assert work and work[0]["room"] == "quantlab" and work[0]["state"] == "complete"


async def test_jarvis_answers_from_apex_and_names_the_data(rt: Runtime) -> None:
    from jarvis.core.trace import TraceContext
    from jarvis.tools.apex import ApexAnalysisTool, ApexArgs

    tool = ApexAnalysisTool(lambda: rt.apex)
    result = await tool.execute(ApexArgs(market="NQ"), TraceContext(trace_id="t-apex"))
    assert result.success and "SYNTHETIC test data" in result.summary and "S1" in result.summary
    assert "chart" not in result.data and "nothing is ordered" in result.data["note"]
    assert rt.tools.get("apex_analysis") is not None


# -- 9 · APEX-3: calibration of the stated rates -------------------------------------------------


def _touches(support: list[bool], group: list[str], held: list[int], step: int = 7200) -> Any:
    from jarvis.training.dataset import FEATURES, Dataset

    n = len(held)
    x = np.zeros((n, len(FEATURES)))
    col = {name: k for k, name in enumerate(FEATURES)}
    for i in range(n):
        x[i, col["support"]] = 1.0 if support[i] else 0.0
        x[i, col[group[i]]] = 1.0
    t = T - 400 * 86400 + np.arange(n, dtype=np.int64) * step
    return Dataset(x, np.array(held), t, t // 86400, np.zeros(n, dtype=np.int64), ["XAUUSD"])


def test_calibration_finds_well_stated_rates_and_their_information() -> None:
    from jarvis.apex.calibration import calibration

    rng = np.random.default_rng(3)
    n = 4000
    kinds = rng.choice(["k_round", "k_week"], size=n).tolist()
    support = [True] * n
    truth = np.where(np.array(kinds) == "k_round", 0.75, 0.35)  # the kind of level matters
    held = (rng.random(n) < truth).astype(int).tolist()
    data = _touches(support, kinds, held)
    out = calibration(data, horizon_minutes=60, start=int(data.t[0]), end=T, min_samples=30)
    assert out["touches"] > 3800 and out["verdict"]["code"] == "CALIBRATED"
    assert out["skill"] > 0.1  # the zone kind adds information beyond the side
    big = [b for b in out["bins"] if b["n"] >= 30]
    assert all(abs(b["stated"] - b["observed"]) < 0.05 for b in big)


def test_calibration_uses_only_touches_decided_before_each_one() -> None:
    from jarvis.apex.calibration import calibration

    # Regime change: 60 broken touches, then 60 held ones. A rate stated before each touch
    # comes from the past only, so it lags behind — with look-ahead it would not.
    held = [0] * 60 + [1] * 60
    data = _touches([True] * 120, ["k_round"] * 120, held)
    out = calibration(data, horizon_minutes=60, start=int(data.t[0]), end=T, min_samples=30)
    late = [b for b in out["bins"] if b["observed"] == 1.0]
    assert late and all(b["stated"] < 0.5 for b in late)
    assert out["verdict"]["code"] == "MISCALIBRATED"
    assert out["skipped"] == 30  # the first 30 had too little history for a stated rate


def test_the_track_record_reports_the_calibration_of_its_period() -> None:
    ctx = CONTEXTS["NQ"]
    series = fixture_series("NQ", T, days=90)
    start = datetime.fromtimestamp(T - 25 * 86400, UTC).date()
    end = datetime.fromtimestamp(T - 2 * 86400, UTC).date()
    result = track_record(series, ctx, CFG, start, end)
    calibrated = result["calibration"]
    assert calibrated["touches"] > 0 and calibrated["brier"] is not None
    assert calibrated["verdict"]["code"] in ("CALIBRATED", "MISCALIBRATED", "INSUFFICIENT")
