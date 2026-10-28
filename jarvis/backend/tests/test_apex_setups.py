"""APEX Ultimate U3: the three zone setups, their stages, the eligibility gate, the alert cap.

Hand-built zone maps and 5-minute bars (and generated data for the integration), so every
expected number can be checked by hand. Acceptance tests 22-28 and scenarios E, H, I, K, L of
the APEX build kit: a touch is never a trade, a breakout is a continuation case, a quality score
or a path share never passes the probability gate, an uncalibrated 0.8 shows PROBABILITY NOT
VALIDATED, NQ and MNQ dollar risk differ by ten, and the fifth alert of a day is refused.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jarvis.apex import eligibility, setups
from jarvis.apex.alerts import admit, alert_record, trading_day
from jarvis.apex.data import SYNTHETIC_LABEL, Series, synthetic_bars
from jarvis.apex.engine import plans_at
from jarvis.apex.execution import execution_for
from jarvis.apex.resolve import resolve
from jarvis.apex.service import market_contexts
from jarvis.apex.signal import SetupState, Signal, SignalStatus, from_analysis
from jarvis.apex.zonemap import zone_view
from jarvis.api.app import create_app
from jarvis.learning.market import Bars
from jarvis.runtime import Runtime
from jarvis.settings import load_settings
from tests.conftest import make_settings

SETTINGS = load_settings(environ={})
CFG = SETTINGS.apex
CONTEXTS = market_contexts(SETTINGS)
NQ, MNQ, XAU = CONTEXTS["NQ"].spec, CONTEXTS["MNQ"].spec, CONTEXTS["XAUUSD"].spec
A = 10.0  # ATR(15m) of the hand-built maps
T = int(datetime(2026, 3, 18, 15, 0, tzinfo=UTC).timestamp())  # Wednesday 11:00 New York
CONFIRMED = T - 86400


# -- fixtures --------------------------------------------------------------------------------------


def zone(zid: str, low: float, high: float, role: str, **extra: Any) -> dict[str, Any]:
    return {"zone_id": zid, "role_at_confirmation": role, "bounds": {"low": low, "high": high},
            "state": extra.get("state", "ACTIVE"), "quality_score": extra.get("quality", 60.0),
            "confirmed_at": extra.get("confirmed", CONFIRMED),
            "broken_at_t": extra.get("broken_at"), "kinds": ["swing"], "rank": None}  # fmt: skip


def zmap(price: float, zones: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Place zones at ``price`` as the zone map does; rank the live ones nearest first."""
    placed = []
    for z in zones:
        lo, hi = z["bounds"]["low"], z["bounds"]["high"]
        inside = lo <= price <= hi
        side = "SUPPORT" if hi < price or (inside and z["role_at_confirmation"] == "support") \
            else "RESISTANCE"  # fmt: skip
        dist = 0.0 if inside else abs(price - (hi if side == "SUPPORT" else lo)) / A
        placed.append({**z, "side": side, "inside": inside, "distance_atr": round(dist, 2)})
    live = [z for z in placed if z["state"] not in ("BROKEN", "EXPIRED")]
    out: dict[str, Any] = {"price": price, "atr15": A, "min_score": 30.0}
    for side, key in (("SUPPORT", "support"), ("RESISTANCE", "resistance")):
        ranked = sorted((z for z in live if z["side"] == side), key=lambda z: z["distance_atr"])
        for k, z in enumerate(ranked[:4], 1):
            z["rank"] = k
        out[key] = ranked[:4]
    return out, placed


def five(rows: list[tuple[float, float, float, float]], end: int = T) -> Bars:
    """5-minute bars whose last one closes at ``end``."""
    n = len(rows)
    t = end - 300 * np.arange(n, 0, -1, dtype=np.int64)
    o, h, lo, c = (np.array([r[k] for r in rows], dtype=float) for k in range(4))
    return Bars(t, o, h, lo, c, np.ones(n), 5)


EMPTY = np.array([], dtype=float)
NO_MINUTES = Bars(np.array([], dtype=np.int64), EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, 1)


def run(name: str, zm: tuple[dict[str, Any], list[dict[str, Any]]], bars5: Bars, *,
        spec: Any = NQ, minute: Bars = NO_MINUTES, spread: float = 0.0,
        costs: float | None = None) -> dict[str, Any]:  # fmt: skip
    return setups.evaluate(
        name, "NQ", zm[0], zm[1], bars5, minute, as_of=T, trend_60="range", spec=spec, cfg=CFG,
        costs=spec.cost_points if costs is None else costs, spread=spread,
    )  # fmt: skip


def side(decision: dict[str, Any], direction: str) -> dict[str, Any]:
    return next(s for s in decision["setups"] if s["direction"] == direction)


S1 = zone("z-s1", 19995.0, 20000.0, "support")
R1 = zone("z-r1", 20060.0, 20070.0, "resistance")
R2 = zone("z-r2", 20140.0, 20150.0, "resistance")
ABOVE = (20004.0, 20006.0, 20003.0, 20005.0)
REJECTION = (20000.5, 20003.0, 19996.0, 20002.5)  # wick 64 %, close location 93 %, risk 10


# -- 22 / E · a touch is a WATCH, a rejection with its trigger is a plan -------------------------


def test_a_touch_without_confirmation_is_watch_never_an_entry() -> None:
    touch = (20004.0, 20005.0, 19996.0, 19998.0)  # into the zone, closed inside it
    out = run(setups.REJECTION_ROTATION, zmap(19998.0, [S1, R1, R2]), five([ABOVE, touch]))
    long = side(out, "long")
    assert long["stage"] == "WATCHING_SETUP" and long["setup_state"] == "WATCH"
    assert "without confirmation" in long["reasons"][0] and long["trigger"] is None
    assert out["candidates"] == [] and out["status"] == "WAITING_FOR_TRIGGER"
    analysis = _analysis({setups.REJECTION_ROTATION: out})
    (sig,) = from_analysis(analysis, {}, created_at=datetime.now(UTC))
    assert sig.setup_state == SetupState.WATCH and sig.entry is None and sig.stop_loss is None
    assert sig.eligible is False and sig.status == SignalStatus.WAITING_FOR_TRIGGER


def test_a_confirmed_rejection_makes_a_research_plan_with_its_trigger() -> None:
    out = run(setups.REJECTION_ROTATION, zmap(20002.5, [S1, R1, R2]), five([ABOVE, REJECTION]))
    (plan,) = out["candidates"]
    assert side(out, "long")["stage"] == "ENTRY_ARMED" and plan["direction"] == "long"
    assert plan["entry"] == {"type": "stop", "price": 20003.25}  # trigger high + 1 tick
    assert plan["stop"] == 19993.25  # below min(bar low, zone low) by 0.15 ATR + 1 tick
    assert plan["targets"][0]["price"] == 20059.75 and plan["targets"][0]["zone"] == "z-r1"
    assert plan["targets"][1]["price"] == 20139.75  # the next ranked resistance
    assert plan["trade_success_probability"]["label"] == "PROBABILITY NOT VALIDATED"
    assert plan["user_executable"] is False and plan["zone_confirmed_at"] == CONFIRMED
    assert side(out, "long")["setup_state"] == "RESEARCH_ONLY"  # a plan, not a signal


def test_a_weakening_zone_or_a_zone_confirmed_too_late_is_no_rejection_setup() -> None:
    weak = {**S1, "state": "WEAKENING"}
    out = run(setups.REJECTION_ROTATION, zmap(20002.5, [weak, R1]), five([ABOVE, REJECTION]))
    assert out["candidates"] == [] and "WEAKENING" in side(out, "long")["reasons"][0]
    late = {**S1, "confirmed_at": T}
    out = run(setups.REJECTION_ROTATION, zmap(20002.5, [late, R1]), five([ABOVE, REJECTION]))
    assert out["candidates"] == [] and "confirmed after" in side(out, "long")["reasons"][0]


def test_a_confirmed_breakout_is_a_continuation_case_never_an_automatic_short() -> None:
    broken = {**R1, "state": "BROKEN", "broken_at_t": T - 2 * 3600, "quality_score": 70.0}
    minute = Bars(np.array([T - 3600, T - 1800], dtype=np.int64), np.array([20072.0, 20080.0]),
                  np.array([20082.0, 20085.0]), np.array([20071.0, 20078.0]),
                  np.array([20080.0, 20083.0]), np.ones(2), 1)  # fmt: skip
    retest = (20075.0, 20080.0, 20071.0, 20078.0)  # back to the broken top, held above it
    zm = zmap(20078.0, [S1, broken, R2])
    bars5 = five([(20084.0, 20085.0, 20079.0, 20080.0), retest])
    out = run(setups.BREAKOUT_RETEST, zm, bars5, minute=minute)
    (plan,) = out["candidates"]
    assert plan["direction"] == "long" and plan["zone"] == "z-r1"
    assert plan["entry"]["price"] == 20080.25 and plan["stop"] == 20058.25
    assert "retest of broken" in plan["trigger"]["rule"]
    # The broken resistance can't be shorted by a rejection: it is no longer a resistance.
    rej = run(setups.REJECTION_ROTATION, zm, bars5)
    assert all(c["direction"] != "short" for c in rej["candidates"])
    assert side(rej, "short")["zone"] != "z-r1"
    # Before the break: pressing R1 is watching a breakout, not a trade.
    pressing = run(setups.BREAKOUT_RETEST, zmap(20058.0, [S1, R1, R2]),
                   five([(20050.0, 20058.0, 20049.0, 20057.0)] * 2))  # fmt: skip
    long = side(pressing, "long")
    assert long["stage"] == "WATCHING_SETUP" and "Watching a breakout" in long["reasons"][0]
    assert pressing["candidates"] == []
    # Broken but without displacement: acceptance is still pending.
    flat = Bars(minute.t, minute.open, np.array([20074.0, 20075.0]), minute.low, minute.close,
                np.ones(2), 1)  # fmt: skip
    pending = run(setups.BREAKOUT_RETEST, zm, bars5, minute=flat)
    assert side(pending, "long")["stage"] == "TRIGGER_PENDING" and pending["candidates"] == []


def test_a_sweep_is_pending_until_reclaimed_and_too_deep_is_a_break() -> None:
    before = (20003.0, 20005.0, 20001.0, 20002.0)
    sweep = (20001.0, 20002.0, 19986.0, 19988.0)  # 0.9 ATR below the low, closed below
    reclaim = (19988.0, 19998.0, 19987.0, 19997.0)
    pending = run(setups.SWEEP_RECLAIM, zmap(19988.0, [S1, R1]), five([ABOVE, before, sweep]))
    assert side(pending, "long")["stage"] == "TRIGGER_PENDING"
    assert side(pending, "long")["setup_state"] == "TRIGGER_PENDING"
    assert pending["candidates"] == []
    out = run(setups.SWEEP_RECLAIM, zmap(19997.0, [S1, R1]), five([ABOVE, before, sweep, reclaim]))
    (plan,) = out["candidates"]
    assert plan["entry"]["price"] == 19998.25
    assert plan["stop"] == 19984.25  # the sweep's extreme 19986 - 0.15 ATR - 1 tick
    assert plan["trigger"]["sweep_extreme"] == 19986.0
    deep = (20001.0, 20002.0, 19975.0, 19978.0)  # 1.5 ATR below → a break
    broke = run(setups.SWEEP_RECLAIM, zmap(19997.0, [S1, R1]),
                five([ABOVE, before, deep, reclaim]))  # fmt: skip
    assert broke["candidates"] == [] and "a break, not a sweep" in side(broke, "long")["reasons"][0]


def test_no_target_or_a_poor_route_means_no_plan_and_no_invented_price() -> None:
    out = run(setups.REJECTION_ROTATION, zmap(20002.5, [S1]), five([ABOVE, REJECTION]))
    assert out["candidates"] == [] and "no target is invented" in out["rejected"][0]["reason"]
    near = zone("z-near", 20009.0, 20012.0, "resistance")
    out = run(setups.REJECTION_ROTATION, zmap(20002.5, [S1, near]), five([ABOVE, REJECTION]))
    assert out["candidates"] == [] and "route" in out["rejected"][0]["reason"]


def test_a_stale_trigger_bar_is_not_a_trigger() -> None:
    old = five([ABOVE, REJECTION], end=T - 900)
    out = run(setups.REJECTION_ROTATION, zmap(20002.5, [S1, R1]), old)
    assert out["candidates"] == [] and side(out, "long")["setup_state"] == "WATCH"


# -- 23 / K · side-specific prices, ticks, dollars -------------------------------------------------


def test_nq_and_mnq_dollar_risk_differ_by_ten_on_the_same_ten_point_stop() -> None:
    zm, bars5 = zmap(20002.5, [S1, R1, R2]), five([ABOVE, REJECTION])
    (nq,) = run(setups.REJECTION_ROTATION, zm, bars5, spec=NQ)["candidates"]
    (mnq,) = run(setups.REJECTION_ROTATION, zm, bars5, spec=MNQ)["candidates"]
    assert nq["risk_points"] == mnq["risk_points"] == 10.0 and nq["risk_ticks"] == 40
    assert nq["risk_usd"] == 200.0 and mnq["risk_usd"] == 20.0  # before fees, one contract
    assert nq["tick_value_usd"] == 5.0 and mnq["tick_value_usd"] == 0.5
    assert nq["cost_usd"] == 20.0 and mnq["cost_usd"] == 2.0
    for plan, spec in ((nq, NQ), (mnq, MNQ)):
        ok, why = eligibility._feasible(plan, spec.tick, spec.point_value)
        assert ok and f"{plan['risk_usd']:g} USD" in why


def test_buy_side_prices_carry_the_spread_on_bid_only_data() -> None:
    s = zone("g-s", 2990.0, 2991.0, "support")
    r = zone("g-r", 3010.0, 3011.0, "resistance")
    r2 = zone("g-r2", 3020.0, 3021.0, "resistance")
    bar = (2991.3, 2991.9, 2990.4, 2991.8)
    long = run(setups.REJECTION_ROTATION, zmap(2991.8, [s, r, r2]), five([(2992, 2993, 2991.5,
               2992), bar]), spec=XAU, spread=0.3, costs=0.1)["candidates"][0]  # fmt: skip
    assert long["entry"]["price"] == 2992.21  # bid high 2991.9 + spread 0.3 + 1 tick (ask)
    assert long["targets"][0]["price"] == 3009.99  # a sell: on the bid, a tick before R
    short_bar = (3010.2, 3010.9, 3009.5, 3009.6)
    short = run(setups.REJECTION_ROTATION, zmap(3009.6, [s, r]), five([(3009.0, 3009.5, 3008.8,
                3009.2), short_bar]), spec=XAU, spread=0.3, costs=0.1)  # fmt: skip
    (plan,) = [c for c in short["candidates"] if c["direction"] == "short"]
    assert plan["entry"]["price"] == 3009.49  # a sell stop below the bar's bid low
    assert plan["stop"] == round(3011.0 + 0.15 * A + 0.01 + 0.3, 2)  # a buy: on the ask
    assert plan["targets"][0]["price"] == round(2991.0 + 0.3 + 0.01, 2)


def test_net_expected_r_uses_stressed_costs() -> None:
    plan = {"entry": {"price": 100.0}, "stop": 90.0, "targets": [{"price": 120.0}],
            "stressed_costs_points": 1.5}  # fmt: skip
    # win: (20 - 1.5) / 10 = 1.85 R, loss: (10 + 1.5) / 10 = 1.15 R
    assert eligibility.net_expected_r(plan, 0.7) == pytest.approx(0.7 * 1.85 - 0.3 * 1.15)
    assert eligibility.net_expected_r(plan, 11.5 / 30) == pytest.approx(0.0, abs=1e-4)


# -- 24 · one bar through stop and target is a loss, not a win -------------------------------------


def test_stop_and_target_in_the_same_minute_after_the_fill_count_as_a_loss() -> None:
    (plan,) = run(setups.REJECTION_ROTATION, zmap(20002.5, [S1, R1, R2]),
                  five([ABOVE, REJECTION]))["candidates"]  # fmt: skip
    t = np.array([T, T + 60, T + 120], dtype=np.int64)
    bars = Bars(t, np.array([20002.5, 20004.0, 20010.0]), np.array([20003.5, 20012.0, 20065.0]),
                np.array([20002.0, 20003.0, 19990.0]), np.array([20003.4, 20010.0, 20000.0]),
                np.ones(3), 1)  # fmt: skip
    out = resolve(plan, bars, cost_points=NQ.cost_points, now=T + 180)
    assert out["state"] == "loss" and out["r"] < -1.0


# -- 25 / 26 / H / I · the probability gate --------------------------------------------------------


GOOD_P = {"event_definition": setups.EVENT, "value": 0.74, "validated": True, "calibrated": True,
          "effective_sample_size": 60, "uncertainty_lower": 0.64, "uncertainty_upper": 0.83,
          "label": None, "model_id": "pm-test"}  # fmt: skip


def gate(plan: dict[str, Any], **change: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "build": "abc123", "quality": {"status": "OK", "stale": False}, "mode": "DELAYED",
        "data_age_minutes": 2.0, "max_age_minutes": 10,
        "execution": {"tradable": True, "quotes": "trades", "spread_source": "inside_costs"},
        "capability": {"market_type": "futures", "schema": "ohlcv"}, "probability": GOOD_P,
        "context": {"macro": {"status": "CONFIRMED"}}, "in_session": True,
        "tick": NQ.tick, "point_value": NQ.point_value, "as_of": T,
    }  # fmt: skip
    return eligibility.gate(plan, **{**args, **change})


def a_plan() -> dict[str, Any]:
    (plan,) = run(setups.REJECTION_ROTATION, zmap(20002.5, [S1, R1, R2]),
                  five([ABOVE, REJECTION]))["candidates"]  # fmt: skip
    return dict(plan)


def test_only_a_validated_calibrated_tp_before_sl_probability_passes() -> None:
    plan = a_plan()
    ok = gate(plan)
    assert ok["pre_budget_passed"] and ok["net_expected_r"] > 0
    done = eligibility.finish(ok, {"admitted": True, "reason": "alert 1 of at most 4"})
    assert done["eligible"] and done["setup_state"] == "ENTRY_ARMED"
    assert done["conviction"] == "QUALIFIED (≥ 0.70)" and len(done["checks"]) == 11
    high = gate(plan, probability={**GOOD_P, "value": 0.84, "uncertainty_lower": 0.75})
    assert high["conviction"] == "HIGH (≥ 0.80)"


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        ({"probability": {**GOOD_P, "value": 0.66}}, "PROBABILITY"),
        ({"probability": {**GOOD_P, "effective_sample_size": 12}}, "UNCERTAINTY_SAMPLE"),
        ({"probability": {**GOOD_P, "uncertainty_lower": 0.51}}, "UNCERTAINTY_SAMPLE"),
        ({"quality": {"status": "OK", "stale": True}}, "DATA_FIT"),
        ({"data_age_minutes": 45.0}, "DATA_FIT"),
        ({"mode": "REPLAY"}, "DATA_FIT"),
        ({"mode": "SYNTHETIC"}, "DATA_FIT"),
        ({"context": {"macro": {"status": "CONTRADICTED", "reason": "US CPI in 12 min"}}},
         "MACRO_LIQUIDITY"),
        ({"context": {"macro": {"status": "UNAVAILABLE"}}}, "MACRO_LIQUIDITY"),
        ({"in_session": False}, "MACRO_LIQUIDITY"),
        ({"execution": {"tradable": True, "quotes": "bid", "spread_source": "estimate"}},
         "SOURCE_CAPABILITY"),
        ({"build": "unknown"}, "APP_READY"),
    ],
)  # fmt: skip
def test_every_missing_condition_keeps_the_plan_research_only(
    change: dict[str, Any], failed: str
) -> None:
    out = eligibility.finish(gate(a_plan(), **change), None)
    assert not out["eligible"] and failed in out["failed"]
    assert out["setup_state"] in ("RESEARCH_ONLY", "BLOCKED")
    if failed in eligibility.BLOCKING:
        assert out["setup_state"] == "BLOCKED"


def test_negative_stressed_expected_value_fails_even_above_seventy_percent() -> None:
    plan = {**a_plan(), "targets": [{"price": 20007.25, "rr": 0.3, "zone": "z-x"}]}
    out = gate(plan, probability={**GOOD_P, "value": 0.72})  # break-even 11.5 / 14 ≈ 0.82
    assert "NET_EV_STRESSED" in out["failed"] and out["net_expected_r"] < 0


def test_a_path_share_or_a_quality_score_never_passes_the_probability_gate() -> None:
    plan = {**a_plan(), "zone_quality": 95.0}
    # P(UPPER_FIRST) = 0.8 from the path head is not P(TP before SL).
    path = {**GOOD_P, "event_definition": "UPPER_FIRST", "value": 0.8}
    out = gate(plan, probability=path)
    assert "PROBABILITY" in out["failed"] and "never qualifies" in out["checks"][6]["reason"]
    # A quality score of 95 with no validated probability: not a signal either.
    out = gate(plan, probability=setups.unvalidated())
    assert {"PROBABILITY", "UNCERTAINTY_SAMPLE", "NET_EV_STRESSED"} <= set(out["failed"])


def test_an_uncalibrated_eighty_percent_shows_probability_not_validated() -> None:
    fake = {**GOOD_P, "value": 0.8, "validated": False, "calibrated": False,
            "effective_sample_size": 3}  # fmt: skip
    out = gate(a_plan(), probability=fake)
    reason = next(c["reason"] for c in out["checks"] if c["check"] == "PROBABILITY")
    assert reason.startswith("PROBABILITY NOT VALIDATED") and "0.80" in reason
    assert out["probability_label"] == "PROBABILITY NOT VALIDATED"


def test_the_signal_contract_refuses_a_faked_qualified_signal() -> None:
    plan = a_plan()
    done = eligibility.finish(gate(plan), {"admitted": True, "reason": "alert 1 of at most 4"})
    analysis = _analysis({setups.REJECTION_ROTATION: {
        "strategy": setups.REJECTION_ROTATION, "version": "1.0.0", "status": "CANDIDATE",
        "setups": [], "trigger": plan["trigger"], "candidates": [plan], "reasons": []}},
        mode="DELAYED")  # fmt: skip
    real = {**plan, "trade_success_probability": GOOD_P, "eligibility": done}
    ok = analysis | {"strategies": {setups.REJECTION_ROTATION: {
        **analysis["strategies"][setups.REJECTION_ROTATION], "candidates": [real]}}}  # fmt: skip
    (sig,) = from_analysis(ok, {}, created_at=datetime.now(UTC))
    assert sig.eligible and sig.setup_state == SetupState.ENTRY_ARMED and not sig.user_executable
    record = sig.as_record()
    for bad in (
        {"trade_success_probability": {**GOOD_P, "validated": False}},
        {"trade_success_probability": {**GOOD_P, "value": 0.69}},
        {"trade_success_probability": {**GOOD_P, "event_definition": "UPPER_FIRST"}},
        {"mode": "REPLAY"},
        {"net_expected_r": -0.1},
        {"eligibility": [{"check": "PROBABILITY", "passed": False}]},
        {"setup_state": "RESEARCH_ONLY"},
    ):
        with pytest.raises(ValueError):
            Signal.model_validate({**record, **bad})
    with pytest.raises(ValueError):
        Signal.model_validate({**record, "user_executable": True})


# -- 27 / L · four a day, duplicates suppressed, zero is fine --------------------------------------


def _plans(n: int) -> list[dict[str, Any]]:
    return [{"id": f"apx-{k}", "zone": f"z-{k}", "direction": "long", "setup": "sweep_reclaim",
             "entry": {"price": 1.0}, "stop": 0.5, "targets": []} for k in range(n)]  # fmt: skip


def test_the_fifth_distinct_alert_of_a_day_and_repeats_are_refused() -> None:
    issued: list[dict[str, Any]] = []
    results = []
    for k, plan in enumerate(_plans(5)):
        when = T + 600 * k
        b = admit(plan, issued, as_of=when, cap=4, cooldown_minutes=240)
        results.append(b["admitted"])
        if b["admitted"]:
            issued.append(alert_record(plan, f"sig-{k}", "NQ", when, {}))
    assert results == [True, True, True, True, False]
    assert admit(_plans(5)[4], issued, as_of=T + 3600, cap=4, cooldown_minutes=240)[
        "reason"
    ].startswith("daily maximum")
    again = admit(_plans(1)[0], issued[:1], as_of=T + 1200, cap=4, cooldown_minutes=240)
    assert not again["admitted"] and "already alerted" in again["reason"]
    oscillation = {**_plans(1)[0], "id": "apx-new"}  # same zone and direction, new plan
    dup = admit(oscillation, issued[:1], as_of=T + 1200, cap=4, cooldown_minutes=240)
    assert not dup["admitted"] and "duplicate" in dup["reason"]
    assert admit(oscillation, [], as_of=T, cap=4, cooldown_minutes=240)["admitted"]  # zero is ok
    tomorrow = T + 86400
    assert admit(_plans(6)[5], issued, as_of=tomorrow, cap=4, cooldown_minutes=240)["admitted"]


def test_trading_days_start_at_six_in_the_evening_new_york() -> None:
    evening = int(datetime(2026, 3, 18, 22, 1, tzinfo=UTC).timestamp())  # 18:01 EDT
    assert trading_day(T) == "2026-03-18" and trading_day(evening) == "2026-03-19"


# -- service: synthetic never alerts, the budget is persisted, alerts are immutable ----------------


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


async def test_synthetic_analyses_show_the_setups_but_never_alert(rt: Runtime) -> None:
    out = await rt.apex.analyze("NQ")
    for name in setups.SETUPS:
        decision = out["strategies"][name]
        assert decision["stage"] in setups.STAGES and decision["setup_state"] in setups.SETUP_STATES
    for c in out["candidates"]:
        gate_ = c["eligibility"]
        assert not gate_["eligible"] and "DATA_FIT" in gate_["failed"]
        assert "PROBABILITY" in gate_["failed"]
    assert out["alerts"] == [] and await rt.apex.alerts("NQ") == []
    sigs = [s for s in out["signals"] if s["strategy_id"] in setups.SETUPS]
    assert len(sigs) >= 3 and not any(s["eligible"] for s in sigs)


def test_the_alerts_endpoint_answers_with_zero_alerts(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture"})
    with TestClient(create_app(settings.model_copy(update={"apex": apex}))) as client:
        response = client.get("/apex/alerts", params={"market": "NQ"})
        assert response.status_code == 200 and response.json() == []


async def test_the_budget_holds_across_analyses_and_alerts_are_immutable(rt: Runtime) -> None:
    ctx = rt.apex.contexts["NQ"]

    async def valid(
        market: str, plan: dict[str, Any], *, source_id: str = "", synthetic: bool = True
    ) -> dict[str, Any]:
        return dict(GOOD_P)

    rt.apex._probability = valid  # type: ignore[method-assign]
    admitted = 0
    for k in range(6):
        when = T + 300 * k
        plan = {**a_plan(), "id": f"apx-{k}", "zone": f"z-{k}", "created_at": when}
        result = {
            "data": {"last_bar": when - 120}, "quality": {"status": "OK", "stale": False},
            "mode": "DELAYED", "execution": {"tradable": True, "quotes": "trades"},
            "capability": {"market_type": "futures", "schema": "ohlcv"},
            "context": {"macro": {"status": "CONFIRMED"}}, "candidates": [plan],
            "strategies": {"x": {"candidates": [plan], "setup_state": "RESEARCH_ONLY"}},
        }  # fmt: skip
        plan["trigger"] = {**plan["trigger"], "closed_utc": when}
        for p in await rt.apex._qualify(result, ctx, when):
            admitted += 1
            assert await rt.apex.store.add_alert(alert_record(p, f"sig-{k}", "NQ", when,
                                                              p["eligibility"]))  # fmt: skip
        assert plan["eligibility"]["eligible"] == (k < 4)
    assert admitted == 4 and len(await rt.apex.alerts("NQ")) == 4
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute("UPDATE apex_alerts SET direction = 'short'")
    with pytest.raises(sqlite3.IntegrityError):
        await rt.db.execute("DELETE FROM apex_alerts")


# -- 28 · a changed probability is a new record ----------------------------------------------------


def test_a_changed_probability_or_zone_set_is_a_new_signal_id() -> None:
    plan = a_plan()
    decision = {
        "strategy": setups.REJECTION_ROTATION,
        "version": "1.0.0",
        "status": "CANDIDATE",
        "setups": [],
        "trigger": plan["trigger"],
        "candidates": [plan],
        "reasons": [],
    }
    base = _analysis({setups.REJECTION_ROTATION: decision})
    (a,) = from_analysis(base, {}, created_at=datetime.now(UTC))
    other = {**plan, "trade_success_probability": {**plan["trade_success_probability"],
                                                   "value": 0.55}}  # fmt: skip
    changed = base | {"strategies": {setups.REJECTION_ROTATION: {
        **base["strategies"][setups.REJECTION_ROTATION], "candidates": [other]}}}  # fmt: skip
    (b,) = from_analysis(changed, {}, created_at=datetime.now(UTC))
    assert a.signal_id != b.signal_id and a.trade_success_probability != b.trade_success_probability


# -- the zone map cache is exact, so validation can walk every 5-minute close --------------------


@pytest.mark.parametrize("market", ["XAUUSD", "NQ"])
def test_cached_zone_maps_equal_fresh_ones_within_a_fifteen_minute_bar(market: str) -> None:
    ctx = CONTEXTS[market]
    bars = synthetic_bars(market, T, ctx.spec.tick).between(T - 20 * 86400, T)
    cache: dict[Any, Any] = {}
    kwargs: dict[str, Any] = {"tick": ctx.spec.tick, "round_step": ctx.level_rules.major_step,
                              "reach_atr": CFG.reach_atr, "source_ids": ["fixture"]}  # fmt: skip
    for when in (T - 3000, T - 2700, T - 2400, T - 2100, T - 1800):
        known = bars.between(int(bars.t[0]), when)
        fresh = zone_view(known, market, when, **kwargs)
        cached = zone_view(known, market, when, cache=cache, **kwargs)
        assert fresh == cached
    assert len(cache) <= 4
    series = Series(market, "fixture", SYNTHETIC_LABEL, True, bars)
    execution = execution_for(series, ctx.spec)
    names = tuple(setups.SETUPS)
    shared: dict[Any, Any] = {}
    for when in (T - 1500, T - 1200):
        seen = series.until(when)
        a, _ = plans_at(seen, ctx, CFG, when, execution, names)
        b, _ = plans_at(seen, ctx, CFG, when, execution, names, zone_cache=shared)
        assert a == b


# -- helpers ---------------------------------------------------------------------------------------


def _analysis(strategies: dict[str, Any], mode: str = "SYNTHETIC") -> dict[str, Any]:
    zm, _ = zmap(20002.5, [S1, R1, R2])
    return {
        "id": "apa-test", "market": "NQ", "as_of": T, "mode": mode,
        "data": {"source": "vault", "source_id": "vault:NQ", "sha256": "ab" * 32,
                 "last_bar": T - 60},
        "execution": {"quotes": "trades", "assumption": "futures_trade_prices"},
        "structure": {"60": {"trend": "range"}}, "quality": {"status": "OK"},
        "zones": [], "zone_map": {**zm, "others": []}, "strategies": strategies,
    }  # fmt: skip
