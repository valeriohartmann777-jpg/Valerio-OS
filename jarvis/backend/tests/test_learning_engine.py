"""Strategy language, features (no look-ahead), backtest rules and evaluation."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from jarvis.learning.backtest import simulate, summarize
from jarvis.learning.evaluate import evaluate, t_required
from jarvis.learning.features import (
    FeatureFrame,
    ewm,
    pivots,
    rolling_max,
    rolling_mean,
    rolling_std,
    rsi,
    volume_profile,
)
from jarvis.learning.levels import LevelStudy, run_study
from jarvis.learning.market import Bars
from jarvis.learning.strategy import (
    FEATURES,
    BinOp,
    Num,
    Ref,
    RuleError,
    Session,
    Strategy,
    parse_condition,
    parse_expression,
)
from jarvis.settings import InstrumentSettings, LearningGates, LearningSettings
from tests.learning_data import NY, session_bars, weekdays

RTH = Session(start="09:30", end="16:00")


def strategy(**overrides: Any) -> Strategy:
    spec: dict[str, Any] = {
        "name": "test",
        "hypothesis": "test",
        "instrument": "NQ",
        "style": "daytrading",
        "timeframe": "5m",
        "session": {"start": "09:30", "end": "16:00"},
        "entries": [{"side": "long", "when": ["minutes >= 30", "minutes <= 30"]}],
        "exit": {"stop": "10", "target_r": 2},
        "max_trades_per_day": 1,
    }
    spec.update(overrides)
    return Strategy.model_validate(spec)


# Language -----------------------------------------------------------------------------


def test_rules_parse_into_a_tree() -> None:
    cond = parse_condition("close > vwap + 0.5 * atr(14)")
    assert cond.op == ">"
    assert cond.left == Ref("close", ())
    assert cond.right == BinOp("+", Ref("vwap", ()), BinOp("*", Num(0.5), Ref("atr", (14,))))
    assert parse_condition("rsi(2)[1] crosses_above 10").left == Ref("rsi", (2,), 1)
    assert parse_expression("window_high(19:00, 03:00)") == Ref("window_high", (1140, 180))
    assert parse_expression("-(high - low) / 2") is not None


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        ("close > magic(3)", "unknown feature"),
        ("close > ema(0)", "1 to 500"),
        ("close > ema", "ends too early"),
        ("close > vwap(3)", "takes no arguments"),
        ("close > close[x]", "bars-ago"),
        ("close == open", "unexpected character"),
        ("close open", "expected one of"),
        ("close > open > low", "unexpected '>'"),
        ("close > window_high(25:00, 03:00)", "not a time"),
        ("__import__('os')", "unexpected character"),
        ("__import__ > 1", "unknown feature"),
    ],
)
def test_bad_rules_are_explained(rule: str, message: str) -> None:
    with pytest.raises(RuleError, match=message):
        parse_condition(rule)


def test_strategy_validation() -> None:
    assert strategy().bar_minutes == 5
    with pytest.raises(ValidationError, match="daytrading uses the timeframes 5m, 10m, 15m, 30m"):
        strategy(timeframe="1m")
    with pytest.raises(ValidationError, match="max_minutes of at most 60"):
        strategy(style="scalping", timeframe="1m")
    with pytest.raises(ValidationError, match="multiple of the 5-minute bars"):
        strategy(entries=[{"side": "long", "when": ["close > or_high(12)"]}])
    with pytest.raises(ValidationError, match="either target or target_r"):
        strategy(exit={"stop": "10", "target": "20", "target_r": 2})
    with pytest.raises(ValidationError, match="needs an exit"):
        strategy(exit=None)
    with pytest.raises(ValidationError, match="start before it ends"):
        strategy(session={"start": "16:00", "end": "09:30"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        strategy(code="import os")


# Features -----------------------------------------------------------------------------


def test_rolling_helpers_match_naive_versions() -> None:
    x = np.random.default_rng(1).normal(20000, 50, 400)
    for n in (1, 3, 20, 50):
        naive_max = [x[i - n + 1 : i + 1].max() if i >= n - 1 else np.nan for i in range(400)]
        assert np.allclose(rolling_max(x, n), naive_max, equal_nan=True)
        naive_mean = [x[i - n + 1 : i + 1].mean() if i >= n - 1 else np.nan for i in range(400)]
        assert np.allclose(rolling_mean(x, n), naive_mean, equal_nan=True)
    naive_std = [x[i - 19 : i + 1].std(ddof=1) if i >= 19 else np.nan for i in range(400)]
    assert np.allclose(rolling_std(x, 20), naive_std, equal_nan=True)


def test_ema_and_rsi_follow_their_definitions() -> None:
    x = np.array([1.0, 2.0, 3.0, 2.0, 1.0, 2.0])
    out = ewm(x, 0.5, seed=2)
    assert np.isnan(out[0]) and out[1] == 1.5
    assert out[2] == pytest.approx(0.5 * 3 + 0.5 * 1.5)
    up = rsi(np.arange(30, dtype=np.float64), 14)
    assert np.isnan(up[13]) and up[14] == 100.0 and up[-1] == 100.0
    down = rsi(np.arange(30, 0, -1, dtype=np.float64), 14)
    assert down[-1] == 0.0


@pytest.mark.parametrize("minute", [37, 600, 1200])  # in a window, a session, a window
def test_no_feature_looks_ahead(minute: int) -> None:
    """A value at bar i may not change when later bars are added."""
    days = weekdays(date(2024, 2, 26), date(2024, 3, 8))  # two weeks
    bars = session_bars(days, start="00:00", end="23:59")
    cut = 7 * 1439 + minute  # on the second Wednesday
    session = Session(start="09:30", end="16:00")
    full = FeatureFrame(bars, session)
    part = FeatureFrame(bars.between(0, int(bars.t[cut])), session)
    args = {"period": 14, "minutes": 30, "time": 0, "step": 25}
    for name, spec in FEATURES.items():
        values = tuple(args[kind] for kind in spec.args)
        if name.startswith("window_"):
            values = (19 * 60, 3 * 60)
        a, b = full.feature(name, values)[:cut], part.feature(name, values)
        assert np.allclose(a, b, equal_nan=True), name
        if name in ("pivot_high", "prev_week_low", "prev_poc", "round_above"):
            assert np.isfinite(a).any(), name  # the check isn't vacuous


def test_session_features() -> None:
    day = date(2024, 3, 5)
    bars = session_bars([date(2024, 3, 4), day], noise=0.5)
    frame = FeatureFrame(bars, RTH)
    second = np.flatnonzero(frame.session_id == frame.session_id.max())
    first = np.flatnonzero(frame.session_id == frame.session_id.min())
    or_high = frame.feature("or_high", (15,))[second]
    assert np.isnan(or_high[:14]).all()  # known once the 15th minute has closed
    assert or_high[14] == bars.high[second[:15]].max() == or_high[-1]
    assert frame.feature("prev_high")[second[0]] == bars.high[first].max()
    assert np.isnan(frame.feature("prev_high")[first]).all()
    vwap = frame.feature("vwap")[second]
    typical = (bars.high + bars.low + bars.close)[second[:3]] / 3
    volume = bars.volume[second[:3]]
    assert vwap[2] == pytest.approx((typical * volume).sum() / volume.sum())
    assert frame.feature("minutes")[second[0]] == 1.0
    assert frame.feature("weekday")[second[0]] == 1.0  # Tuesday


def test_pivots_appear_once_confirmed() -> None:
    high = np.array([1, 2, 3, 9, 4, 3, 2, 5, 6, 4, 3, 2], dtype=np.float64)
    levels = pivots(high, 2)
    # The 9 at bar 3 is a swing high once bars 4 and 5 have closed.
    assert np.isnan(levels[:5]).all() and levels[5] == 9
    # The 6 at bar 8 is confirmed at bar 10.
    assert levels[9] == 9 and levels[10] == 6 and levels[11] == 6
    flat = np.array([5, 5, 5, 5, 5, 5, 5], dtype=np.float64)
    assert np.isnan(pivots(flat, 2)).all()  # a plateau is not a swing


def test_weekly_levels_round_numbers_and_profile() -> None:
    days = weekdays(date(2024, 3, 4), date(2024, 3, 15))
    bars = session_bars(days, noise=2.0)
    frame = FeatureFrame(bars, RTH)
    week_two = bars.t >= int(datetime(2024, 3, 11, tzinfo=NY).timestamp())
    first_week = ~week_two
    prev_high = frame.feature("prev_week_high")
    assert np.isnan(prev_high[first_week]).all()
    assert (prev_high[week_two] == bars.high[first_week].max()).all()
    above, below = frame.feature("round_above", (25,)), frame.feature("round_below", (25,))
    assert (above % 25 == 0).all() and (below % 25 == 0).all()
    assert (below <= bars.close).all() and (bars.close <= above).all()

    price = np.array([100.0] * 10 + [101.0] * 3 + [99.0] * 2 + [104.0])
    poc, vah, val = volume_profile(price, np.ones(len(price)), 100.0)
    assert 100.0 <= poc < 100.03
    assert val <= poc <= vah and vah >= 101.0 and vah < 104.0  # 70% of the volume, no tail


# Level studies ------------------------------------------------------------------------


def _bouncing(days: list[date], seed: int) -> Bars:
    """Every morning price drops 30 points below the open and bounces: a real
    support at session_open - 30, in an otherwise random market."""

    def path(_: int, minute: int) -> float:
        if minute < 20:
            return -1.5
        if minute < 40:
            return 1.5
        return 0.0

    return session_bars(days, path=path, noise=1.0, seed=seed)


def _study(**overrides: Any) -> LevelStudy:
    spec: dict[str, Any] = {
        "name": "fixed support",
        "question": "does the morning low hold?",
        "instrument": "NQ",
        "timeframe": "1m",
        "session": {"start": "09:30", "end": "16:00"},
        "level": "session_open - 30",
        "side": "support",
        "tolerance": "2",
        "hold": "10",
        "breach": "5",
        "horizon_minutes": 60,
    }
    spec.update(overrides)
    return LevelStudy.model_validate(spec)


def test_a_real_level_beats_its_placebo() -> None:
    days = weekdays(date(2024, 1, 2), date(2024, 3, 29))
    result = run_study(_study(), _bouncing(days, seed=4), date(2024, 1, 1), date(2024, 4, 1))
    assert result["touches"] >= 50
    assert result["held_rate"] > 0.7 and result["expected_rate"] < 0.5
    assert result["edge_z"] is not None and result["edge_z"] > 3
    assert result["by_touch"]["first"]["decided"] >= 40
    assert set(result["by_year"]) == {"2024"}


def test_an_arbitrary_level_shows_no_edge() -> None:
    days = weekdays(date(2024, 1, 2), date(2024, 6, 28))
    noise = session_bars(days, noise=1.0, seed=11)
    study = _study(level="pivot_low(10)", tolerance="0.5", hold="6", breach="3")
    result = run_study(study, noise, date(2024, 1, 1), date(2024, 7, 1))
    assert result["touches"] > 200
    assert abs(result["edge_z"]) < 3  # random walks have no support


@pytest.mark.parametrize("seed", [21, 22, 23])
def test_a_trend_doesnt_make_support(seed: int) -> None:
    """In a rising random market every support seems to hold — the control
    group rises too, so no edge is reported."""
    days = weekdays(date(2024, 1, 2), date(2024, 6, 28))
    rising = session_bars(days, noise=1.0, seed=seed, path=lambda _d, _m: 0.05)
    study = _study(level="pivot_low(10)", tolerance="2", hold="10", breach="5")
    result = run_study(study, rising, date(2024, 1, 1), date(2024, 7, 1))
    assert result["held_rate"] > 0.5  # it does look like support …
    assert abs(result["edge_z"]) < 2.5  # … but no more than any price


def test_studies_only_see_the_period_they_are_given() -> None:
    days = weekdays(date(2024, 1, 2), date(2024, 3, 29))
    bars = _bouncing(days, seed=4)
    january = run_study(_study(), bars, date(2024, 1, 1), date(2024, 2, 1))
    everything = run_study(_study(), bars, date(2024, 1, 1), date(2024, 4, 1))
    assert 0 < january["touches"] < everything["touches"] / 2


def test_bad_studies_are_explained() -> None:
    with pytest.raises(ValidationError, match="unknown feature"):
        _study(level="magic_level")
    with pytest.raises(ValidationError, match="side"):
        _study(side="sideways")


# Backtest -----------------------------------------------------------------------------


def _bars(rows: list[tuple[float, float, float, float]], start: str = "2024-03-05 09:30") -> Bars:
    opening = datetime.fromisoformat(start).replace(tzinfo=NY)
    t0 = int(opening.astimezone(UTC).timestamp())
    arr = np.array(rows, dtype=np.float64)
    n = len(rows)
    return Bars(
        t0 + np.arange(n, dtype=np.int64) * 300,
        arr[:, 0],
        arr[:, 1],
        arr[:, 2],
        arr[:, 3],
        np.ones(n),
        minutes=5,
    )


FLAT = (100.0, 100.5, 99.5, 100.0)


def _run(rows: list[tuple[float, float, float, float]], **overrides: Any) -> list[Any]:
    sim = simulate(strategy(**overrides), _bars(rows), cost_points=1.0, min_stop_points=1.0)
    return sim.trades


def test_entry_is_at_the_next_open_and_targets_fill_exactly() -> None:
    rows = [FLAT] * 6 + [(101.0, 125.0, 100.5, 121.0)] + [FLAT] * 10
    # bar 5 closes at 09:59:59 → minutes == 30 → enter at bar 6's open (101)
    [trade] = _run(rows)
    assert trade.entry == 101.0 and trade.exit == 121.0 and trade.reason == "target"
    assert trade.points == 19.0 and trade.r == pytest.approx(1.9)


def test_stop_wins_when_one_bar_touches_both() -> None:
    rows = [FLAT] * 6 + [(101.0, 125.0, 90.0, 110.0)] + [FLAT] * 10
    [trade] = _run(rows)
    assert trade.reason == "stop" and trade.exit == 91.0 and trade.points == -11.0


def test_a_gap_through_the_stop_fills_at_the_worse_open() -> None:
    rows = [FLAT] * 6 + [(101.0, 102.0, 100.0, 101.0), (85.0, 86.0, 84.0, 85.0)] + [FLAT] * 10
    [trade] = _run(rows)
    assert trade.reason == "stop" and trade.exit == 85.0


def test_time_stop_and_session_end() -> None:
    rows = [FLAT] * 6 + [(101.0, 102.0, 100.0, 101.0)] * 5 + [FLAT] * 5
    [timed] = _run(rows, exit={"stop": "10", "target_r": 2, "max_minutes": 15})
    assert timed.reason == "time" and timed.exit_t - timed.entry_t == 15 * 60
    [held] = _run(rows)
    assert held.reason == "session_end"


def test_short_trades_and_trade_limits() -> None:
    rows = [FLAT] * 6 + [(99.0, 99.5, 70.0, 75.0)] + [FLAT] * 10
    entries = [{"side": "short", "when": ["minutes >= 30"]}]
    trades = _run(rows, entries=entries, max_trades_per_day=2)
    assert len(trades) == 2
    assert trades[0].side == -1 and trades[0].reason == "target" and trades[0].exit == 79.0
    # the second trade can only start at the first one's exit bar
    assert trades[1].entry_t > trades[0].entry_t


def test_stops_smaller_than_the_minimum_are_skipped() -> None:
    rows = [FLAT] * 20
    sim = simulate(
        strategy(exit={"stop": "0.5", "target_r": 2}),
        _bars(rows),
        cost_points=1.0,
        min_stop_points=1.0,
    )
    assert sim.trades == [] and sim.skipped_small_stop == 1


def test_summary_statistics() -> None:
    rows = [FLAT] * 6 + [(101.0, 125.0, 100.5, 121.0)] + [FLAT] * 10
    trades = _run(rows) * 3
    stats = summarize(trades, months=1.0)
    assert stats.trades == 3 and stats.win_rate == 1.0
    assert stats.avg_r == pytest.approx(1.9) and stats.profit_factor == 99.0
    assert stats.exits == {"target": 3} and stats.by_year == {"2024": {"trades": 3, "avg_r": 1.9}}


# Evaluation ---------------------------------------------------------------------------


def _settings() -> LearningSettings:
    return LearningSettings(
        data_start=date(2024, 1, 1),
        oos_start=date(2024, 4, 1),
        holdout_start=date(2024, 6, 1),
        instruments={
            "NQ": InstrumentSettings(
                symbol="USATECHIDXUSD",
                price_range=(4000, 39000),
                cost_points=1.0,
                min_stop_points=2.0,
            )
        },
        gates=LearningGates(
            min_trades={"daytrading": 40, "scalping": 40},
            min_oos_trades={"daytrading": 20, "scalping": 20},
        ),
    )


def test_t_bar_rises_with_every_out_of_sample_test() -> None:
    assert t_required(1, 0.05) == pytest.approx(1.645, abs=1e-3)
    assert t_required(10, 0.05) > t_required(2, 0.05) > t_required(1, 0.05)


def test_a_real_pattern_is_validated_and_noise_is_not() -> None:
    days = weekdays(date(2024, 1, 2), date(2024, 7, 31))

    # Every day the market rises 30 points between 10:00 and 10:30.
    def drift(_: int, minute: int) -> float:
        return 1.0 if 30 <= minute < 60 else 0.0

    trending = session_bars(days, path=drift, noise=0.6)
    rule = strategy(exit={"stop": "8", "target_r": 2.5})
    outcome = evaluate(rule, trending, _settings(), oos_evaluations_before=0)
    assert outcome.status == "validated", outcome.reason
    assert outcome.in_sample and outcome.in_sample.avg_r > 1
    assert outcome.holdout is not None and outcome.holdout_confirmed is True

    noise = session_bars(days, noise=0.6, seed=3)
    outcome = evaluate(rule, noise, _settings(), oos_evaluations_before=0)
    assert outcome.status == "rejected"
    assert outcome.out_of_sample is None and outcome.holdout is None  # never computed


def test_a_holdout_merely_in_the_plus_doesnt_confirm() -> None:
    """The pattern exists until May, then the market turns random: the strategy
    validates, but the holdout (June/July) must not call it confirmed."""
    days = weekdays(date(2024, 1, 2), date(2024, 7, 31))
    june = days.index(next(d for d in days if d >= date(2024, 6, 1)))

    def fading(day: int, minute: int) -> float:
        return 1.0 if day < june and 30 <= minute < 60 else 0.0

    bars = session_bars(days, path=fading, noise=0.6)
    rule = strategy(exit={"stop": "8", "target_r": 2.5})
    outcome = evaluate(rule, bars, _settings(), oos_evaluations_before=0)
    assert outcome.status == "validated"
    assert outcome.holdout is not None and outcome.holdout.t_stat < 1.645
    assert outcome.holdout_confirmed is False


async def test_stored_verdicts_are_recomputed(tmp_path: Path) -> None:
    from jarvis.storage.database import MIGRATIONS, Database

    db = Database(tmp_path / "jarvis.db")
    await db.connect()
    try:
        for number, t_stat in ((1, 0.8), (2, 2.4)):
            holdout = json.dumps({"avg_r": 0.05, "profit_factor": 1.04, "t_stat": t_stat})
            await db.execute(
                "INSERT INTO learning_tests (id, number, created_at, name, status, reason, spec, "
                "holdout, holdout_confirmed) VALUES (?, ?, '', 'x', 'validated', '', '{}', ?, 1)",
                (f"id{number}", number, holdout),
            )
        # As before the update: migrations 6 and later haven't run yet.
        await db.execute("DELETE FROM schema_version WHERE version >= 6")
        for statements in MIGRATIONS[6:]:
            for statement in statements:
                table = re.search(r"CREATE TABLE (\w+)", statement)
                if table:
                    await db.execute(f"DROP TABLE {table.group(1)}")
        await db.close()
        await db.connect()  # the update's migration runs
        rows = await db.fetch_all(
            "SELECT number, holdout_confirmed FROM learning_tests ORDER BY number"
        )
        assert [(r["number"], r["holdout_confirmed"]) for r in rows] == [(1, 0), (2, 1)]
    finally:
        await db.close()  # an open connection would keep the test process alive
    await db.connect()  # the update's migration runs
    rows = await db.fetch_all(
        "SELECT number, holdout_confirmed FROM learning_tests ORDER BY number"
    )
    assert [(r["number"], r["holdout_confirmed"]) for r in rows] == [(1, 0), (2, 1)]
    await db.close()
