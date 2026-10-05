"""Model training: the touch data set, the model and its honest evaluation."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jarvis.api.app import create_app
from jarvis.briefing.levels import KeyLevel, LevelRules
from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.learning.market import Bars, DataError, Instrument, MarketData, Progress, resample
from jarvis.runtime import Runtime
from jarvis.settings import Settings
from jarvis.storage.database import Database
from jarvis.storage.preferences import Preferences
from jarvis.tools.learning import _trained_model
from jarvis.training.dataset import (
    FEATURES,
    Context,
    Dataset,
    LevelSet,
    TouchRules,
    _day_touches,
    build,
    outcome,
    session_levels,
    session_windows,
    touch_features,
)
from jarvis.training.live import level_odds
from jarvis.training.model import (
    CANDIDATES,
    HOLDOUT_T,
    Periods,
    _epoch,
    clustered_t,
    train,
    walk_forward,
)
from jarvis.training.service import TrainingService, TrainingState, TrainingStatus
from jarvis.training.store import Bundle, TrainingStore
from tests.conftest import Recorder, eventually, make_settings
from tests.learning_data import NY, futures_bars

NQ_RULES = LevelRules(round_step=100, major_step=500, merge_within=3, regular_session=True)
GOLD_RULES = LevelRules(round_step=10, major_step=50, merge_within=0.8, regular_session=False)
RULES = TouchRules()
RTH = (9 * 60 + 30, 16 * 60)


def feature(x: np.ndarray, name: str) -> float:
    return float(x[FEATURES.index(name)])


def five_minute_bars(rows: list[tuple[float, float, float, float]], start: datetime) -> Bars:
    """5-minute bars from (open, high, low, close) rows, the first at ``start``."""
    first = int(start.timestamp())
    t = first + 300 * np.arange(len(rows), dtype=np.int64)
    o, h, low, c = (np.array([r[k] for r in rows], dtype=np.float64) for k in range(4))
    return Bars(t, o, h, low, c, np.full(len(rows), 100.0), 5)


FLAT = [(105.0, 105.5, 104.5, 105.0)] * 20  # true range 1 → ATR(14) = 1


def context(*after: tuple[float, float, float, float], at: str = "09:00") -> Context:
    hh, mm = (int(x) for x in at.split(":"))
    return Context(five_minute_bars([*FLAT, *after], datetime(2024, 3, 5, hh, mm, tzinfo=NY)))


def at_100(kinds: tuple[str, ...] = ("prev_low",)) -> KeyLevel:
    return KeyLevel(100.0, ["Tief RTH Vortag"], list(kinds))


# Touches and outcomes ------------------------------------------------------------------


def test_a_touch_from_above_is_support_and_its_outcome_comes_after_it() -> None:
    touch = (105.0, 105.2, 100.05, 101.0)  # down into the zone (100 + 0.1 ATR)
    held = context(touch, (101.0, 101.5, 100.6, 101.2))
    levels = LevelSet([at_100()], 105.0, 500.0)
    assert _day_touches(held, 15, 21, levels, RULES) == [(20, 0, "support")]
    assert outcome(held, 20, 100.0, "support", RULES) == "held"  # +1 ATR first

    broken = context(touch, (101.0, 101.5, 99.4, 99.6))  # both in one bar: broken
    assert outcome(broken, 20, 100.0, "support", RULES) == "broken"
    through = context((105.0, 105.2, 99.4, 100.2))  # through the level within the touch
    assert outcome(through, 20, 100.0, "support", RULES) == "through"
    waiting = context(touch, *[(100.5, 100.8, 99.7, 100.4)] * 13)
    assert outcome(waiting, 20, 100.0, "support", RULES) == "undecided"


def test_only_touches_from_one_side_count() -> None:
    # Bar 20 gaps below the level (a support touch, broken at once); bar 21
    # rallies back up into it from below: resistance.
    gap = context((99.0, 99.5, 98.5, 99.0), (99.0, 99.95, 98.9, 99.5))
    levels = LevelSet([at_100()], 105.0, 500.0)
    assert _day_touches(gap, 15, 21, levels, RULES) == [(20, 0, "support"), (21, 0, "resistance")]
    assert outcome(gap, 20, 100.0, "support", RULES) == "through"


def test_the_outcome_ends_with_the_trading_day() -> None:
    rows = [*FLAT, (105.0, 105.2, 100.05, 101.0), (100.8, 100.9, 100.6, 100.8)]
    bars = five_minute_bars(rows, datetime(2024, 3, 5, 15, 10, tzinfo=NY))  # touch at 16:50
    evening = int(datetime(2024, 3, 5, 18, 0, tzinfo=NY).timestamp())  # the next trading day
    bars = Bars(
        np.r_[bars.t, evening],
        np.r_[bars.open, 101.0],
        np.r_[bars.high, 102.0],  # would be "held" — but it's another day
        np.r_[bars.low, 100.9],
        np.r_[bars.close, 101.8],
        np.r_[bars.volume, 100.0],
        5,
    )
    assert outcome(Context(bars), 20, 100.0, "support", RULES) == "undecided"


def test_features_are_signed_in_favour_of_the_level() -> None:
    ctx = context((105.0, 105.2, 100.05, 101.0), (101.0, 101.5, 100.6, 101.2))
    support = LevelSet([at_100(), KeyLevel(95.0, ["Runde Zahl"], ["round"])], 105.0, 500.0)
    x = touch_features(ctx, 15, 20, at_100(), "support", 1, 99.0, support, "NQ", 9 * 60)
    assert x is not None
    assert feature(x, "support") == 1.0
    assert feature(x, "offset") == pytest.approx(1.0)  # closed 1 ATR above support
    assert feature(x, "wick") == pytest.approx(0.95)  # 101 - 100.05
    assert feature(x, "approach_30") < 0  # fell into the level
    assert feature(x, "room") == pytest.approx(5.0)  # next level below: 95
    assert feature(x, "flipped") == 0.0  # below the price when the day began
    assert feature(x, "k_day_extreme") == 1.0 and feature(x, "k_round") == 0.0
    assert feature(x, "nq") == 1.0

    rally = context((99.0, 99.5, 98.5, 99.0), (99.0, 99.95, 98.9, 99.2))
    x = touch_features(rally, 15, 21, at_100(), "resistance", 2, 1.0, support, "XAUUSD", 9 * 60)
    assert x is not None
    assert feature(x, "support") == 0.0
    assert feature(x, "offset") == pytest.approx(0.8 / rally.atr[20])  # closed below resistance
    assert feature(x, "flipped") == 1.0  # was support in the morning
    assert feature(x, "touch") == 2.0 and feature(x, "nq") == 0.0


# The data set --------------------------------------------------------------------------


def test_the_data_set_never_looks_ahead() -> None:
    """Rows decided before a cut are identical whether or not later data exists."""
    full = futures_bars(date(2024, 1, 1), date(2024, 3, 15), 20000.0, noise=2.0, seed=5)
    cut = int(datetime(2024, 2, 20, 12, 0, tzinfo=NY).timestamp())
    early = full.between(0, cut)
    a = build(full, "NQ", NQ_RULES, RTH, RULES)
    b = build(early, "NQ", NQ_RULES, RTH, RULES)
    keep_a = a.t + 3600 <= cut
    keep_b = b.t + 3600 <= cut
    assert keep_a.sum() > 300
    assert keep_a.sum() == keep_b.sum()
    np.testing.assert_array_equal(a.x[keep_a], b.x[keep_b])
    np.testing.assert_array_equal(a.y[keep_a], b.y[keep_b])


def test_the_data_set_covers_the_session_and_its_levels() -> None:
    bars = futures_bars(date(2024, 1, 1), date(2024, 2, 29), 2000.0, noise=0.3, seed=9)
    data = build(bars, "XAUUSD", GOLD_RULES, (180, 810), RULES, market_index=1)
    assert len(data) > 200
    assert set(np.unique(data.y)) == {0, 1}
    assert data.skipped["through"] > 0
    minutes = data.x[:, FEATURES.index("minutes")]
    assert minutes.min() >= 5 and minutes.max() <= 810 - 180
    assert (data.cluster // 1_000_000 == 1).all()
    kinds = [f for f in FEATURES if f.startswith("k_") and f != "k_major_round"]
    assert all(data.x[:, FEATURES.index(k)].sum() > 0 for k in kinds), "every kind is touched"


def test_data_sets_of_two_markets_combine_in_time_order() -> None:
    nq = futures_bars(date(2024, 1, 1), date(2024, 1, 31), 20000.0, noise=2.0, seed=1)
    gold = futures_bars(date(2024, 1, 1), date(2024, 1, 31), 2000.0, noise=0.3, seed=2)
    a = build(nq, "NQ", NQ_RULES, RTH, RULES, market_index=0)
    b = build(gold, "XAUUSD", GOLD_RULES, (180, 810), RULES, market_index=1)
    both = Dataset.concat([a, b])
    assert both.markets == ["NQ", "XAUUSD"]
    assert len(both) == len(a) + len(b)
    assert (np.diff(both.t) >= 0).all()
    assert (both.market == 1).sum() == len(b)
    assert both.skipped["through"] == a.skipped["through"] + b.skipped["through"]


# The model -----------------------------------------------------------------------------

PERIODS = Periods(oos_start=date(2023, 1, 1), holdout_start=date(2024, 1, 1))
FAST = CANDIDATES[:1] + CANDIDATES[3:]  # one boosting model + logistic regression


def synthetic(signal: float, *, seed: int = 0, per_day: int = 8) -> Dataset:
    """Touches on weekdays 2021-2024. Whether a level holds depends on how the
    touching candle closed (the baseline knows that), a shared mood per day
    (nobody knows that) and, with ``signal``, on the time of day."""
    rng = np.random.default_rng(seed)
    days = [d for d in range(18631, 20089) if (d + 3) % 7 < 5]  # 2021-01-04 … 2024-12-31
    t = np.repeat(np.array(days, dtype=np.int64) * 86400 + 15 * 3600, per_day)
    t += np.tile(np.arange(per_day, dtype=np.int64) * 1200, len(days))
    n = len(t)
    x = rng.normal(size=(n, len(FEATURES)))
    offset = rng.uniform(-0.4, 2.0, n)
    x[:, FEATURES.index("offset")] = offset
    morning = rng.random(n) < 0.5
    x[:, FEATURES.index("minutes")] = np.where(
        morning, rng.uniform(5, 120, n), rng.uniform(120, 390, n)
    )
    mood = np.repeat(rng.normal(0, 0.6, len(days)), per_day)
    logit = -0.6 + 1.1 * offset + mood + signal * np.where(morning, 1.0, -1.0)
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(np.int64)
    cluster = t // 86400
    return Dataset(x, y, t, cluster, np.zeros(n, dtype=np.int64), ["NQ"], {"through": 0})


def test_the_clustered_t_is_calibrated() -> None:
    """Under the null, t ≥ 1.645 about 5% of the time — even when touches of a
    day move together (counting them as independent would say far more often)."""
    rng = np.random.default_rng(1)
    hits = naive_hits = 0
    runs = 400
    cluster = np.repeat(np.arange(150), 10)
    for _ in range(runs):
        values = np.repeat(rng.normal(0, 1, 150), 10) + rng.normal(0, 1, 1500)
        hits += clustered_t(values, cluster) >= HOLDOUT_T
        naive_hits += values.mean() / (values.std(ddof=1) / np.sqrt(len(values))) >= HOLDOUT_T
    assert 0.02 <= hits / runs <= 0.09
    assert naive_hits / runs > 0.15


def test_walk_forward_never_trains_on_the_month_it_predicts() -> None:
    data = synthetic(0.0, per_day=2)
    data.x[:, FEATURES.index("minutes")] = data.t  # the fake model can see the time
    fits: list[tuple[float, float]] = []

    class Spy:
        def fit(self, x: np.ndarray, y: np.ndarray) -> None:
            self.seen = float(x[:, FEATURES.index("minutes")].max())

        def predict_proba(self, x: np.ndarray) -> np.ndarray:
            fits.append((self.seen, float(x[:, FEATURES.index("minutes")].min())))
            return np.full((len(x), 2), 0.5)

    start = _epoch(PERIODS.holdout_start)
    mask, p_model, _ = walk_forward(data, Spy, start, 3600)
    assert len(fits) == 12  # one model per month of 2024
    assert all(seen + 3600 <= first for seen, first in fits)
    assert mask.sum() == (data.t >= start).sum()
    assert np.isfinite(p_model[mask]).all() and np.isnan(p_model[~mask]).all()


def test_a_real_pattern_is_found_and_confirmed() -> None:
    trained = train(synthetic(0.5), PERIODS, candidates=FAST)
    report = trained.report
    assert report["status"] == "confirmed", report["reason"]
    assert report["holdout"]["improvement"] > 0.01
    assert report["holdout"]["t"] >= HOLDOUT_T
    assert report["holdout_months"] == 12
    assert report["usable_for"] == ["NQ"]
    assert report["importance"][0]["feature"] in ("offset", "minutes")
    assert {f["feature"] for f in report["importance"][:2]} == {"offset", "minutes"}
    assert trained.model is not None and trained.baseline is not None
    assert trained.shapes.shape == (200, 4)
    # Its percentages mean what they say on months it never saw.
    for row in report["calibration"]:
        if row["touches"] >= 300:
            assert abs(row["predicted"] - row["held"]) < 0.06


@pytest.mark.parametrize("seed", [2, 3])
def test_no_pattern_is_not_confirmed(seed: int) -> None:
    report = train(synthetic(0.0, seed=seed), PERIODS, candidates=FAST).report
    assert report["status"] in ("no_edge", "unconfirmed")
    assert report["usable_for"] == []


def test_too_little_data_says_so() -> None:
    data = synthetic(0.5)
    report = train(data.subset(data.t >= _epoch(date(2022, 10, 1))), PERIODS).report
    assert report["status"] == "too_little_data"
    assert "needs 1000" in report["reason"]


def test_random_prices_teach_the_model_nothing() -> None:
    """End to end on a random walk: levels are just prices there."""
    nq = futures_bars(date(2023, 1, 1), date(2024, 6, 30), 20000.0, noise=2.0, seed=21)
    gold = futures_bars(date(2023, 1, 1), date(2024, 6, 30), 2000.0, noise=0.3, seed=22)
    data = Dataset.concat(
        [
            build(nq, "NQ", NQ_RULES, RTH, RULES, market_index=0),
            build(gold, "XAUUSD", GOLD_RULES, (180, 810), RULES, market_index=1),
        ]
    )
    periods = Periods(oos_start=date(2023, 10, 1), holdout_start=date(2024, 1, 1))
    report = train(data, periods, candidates=FAST).report
    assert report["status"] in ("no_edge", "unconfirmed")
    assert report["holdout"]["t"] < HOLDOUT_T
    assert set(report["holdout_by_market"]) == {"NQ", "XAUUSD"}


# The service --------------------------------------------------------------------------


class SyntheticMarket(MarketData):
    """Serves fixed minute bars instead of Dukascopy's."""

    def __init__(self, root: Path, bars: dict[str, Bars], *, down: bool = False) -> None:
        super().__init__(root)
        self.bars, self.down = bars, down
        self.syncs: list[tuple[str, date, date]] = []

    async def sync(
        self, instrument: Instrument, start: date, end: date, progress: Progress | None = None
    ) -> int:
        self.syncs.append((instrument.name, start, end))
        if self.down:
            raise DataError("I can't reach Dukascopy's data right now (test).")
        return 0

    def load(self, instrument: Instrument, start: date, end: date) -> Bars:
        lo = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
        hi = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp()) + 86400
        return self.bars[instrument.name].between(lo, hi)

    def coverage(self, instrument: Instrument) -> tuple[date, date, int] | None:
        bars = self.bars.get(instrument.name)
        if bars is None or len(bars) == 0:
            return None
        first, last = (datetime.fromtimestamp(int(t), UTC).date() for t in bars.t[[0, -1]])
        return first, last, len(bars)

    async def recent(self, instrument: Instrument, days: int) -> Bars:
        if self.down or instrument.name not in self.bars:
            raise DataError(f"No recent {instrument.name} data.")
        return self.bars[instrument.name]


def seven_months() -> dict[str, Bars]:
    """Until Tuesday 2024-07-30, whose evening session ends on the 31st (UTC)."""
    return {
        "NQ": futures_bars(date(2024, 1, 1), date(2024, 7, 30), 20000.0, noise=2.0, seed=31),
        "XAUUSD": futures_bars(date(2024, 1, 1), date(2024, 7, 30), 2000.0, noise=0.3, seed=32),
    }


def short_settings(tmp_path: Path) -> Settings:
    settings = make_settings(tmp_path)
    learning = settings.learning.model_copy(
        update={
            "data_start": date(2024, 1, 1),
            "oos_start": date(2024, 4, 1),
            "holdout_start": date(2024, 6, 1),
        }
    )
    training = settings.training.model_copy(update={"enabled": True})
    return settings.model_copy(update={"learning": learning, "training": training})


async def make_training(
    tmp_path: Path, market: MarketData, db: Database
) -> tuple[TrainingService, Recorder]:
    settings = short_settings(tmp_path)
    bus = EventBus()
    service = TrainingService(
        settings=settings.training,
        learning=settings.learning,
        briefing=settings.briefing,
        market=market,
        store=TrainingStore(db, tmp_path / "training"),
        bus=bus,
        preferences=Preferences(tmp_path / "prefs.json"),
        today_utc=lambda: date(2024, 8, 1),
    )
    return service, Recorder(bus)


async def test_the_service_trains_on_new_data_and_keeps_the_model(tmp_path: Path) -> None:
    db = Database(tmp_path / "jarvis.db")
    await db.connect()
    try:
        await _train_and_restart(tmp_path, db)
    finally:
        await db.close()  # an open connection would keep the test process alive


async def _train_and_restart(tmp_path: Path, db: Database) -> None:
    market = SyntheticMarket(tmp_path / "market", seven_months())
    service, recorder = await make_training(tmp_path, market, db)
    try:
        await service.start()
        await eventually(lambda: service.status.report is not None, 120)
        status = service.status
        assert status.history[0]["status"] == "done"
        assert status.history[0]["data_until"] == "2024-07-31"
        assert status.report is not None
        assert status.report["status"] in ("no_edge", "unconfirmed")  # random prices
        assert status.model is not None and status.model["data_until"] == "2024-07-31"
        assert ("NQ", date(2024, 1, 1), date(2024, 7, 31)) in market.syncs
        assert (tmp_path / "training" / "model.pkl").exists()
        states = {e.payload["training"]["state"] for e in recorder.of("training.changed")}
        assert {"preparing", "training"} <= states
        line = service.briefing_line()
        assert line is not None and "kein belegter Vorteil" in line
    finally:
        await service.stop()

    # After a restart with no new trading day: the saved model, no new run.
    again, _ = await make_training(tmp_path, market, db)
    try:
        await again.start()
        await eventually(lambda: again.status.state == "waiting", 30)
        assert again.bundle is not None and again.bundle.run == 1
        assert len(again.status.history) == 1
        await again.train_now()  # "Train now" trains anyway
        await eventually(lambda: len(again.status.history) == 2, 120)
        await eventually(lambda: again.status.history[0]["status"] == "done", 120)
        await eventually(lambda: again.bundle is not None and again.bundle.run == 2, 30)
    finally:
        await again.stop()


async def test_no_market_data_is_an_error_not_a_crash(tmp_path: Path) -> None:
    db = Database(tmp_path / "jarvis.db")
    await db.connect()
    market = SyntheticMarket(tmp_path / "market", {}, down=True)
    service, _ = await make_training(tmp_path, market, db)
    try:
        await service.start()
        await eventually(lambda: service.status.state == "error", 30)
        assert "Dukascopy" in (service.status.detail or "")
        assert service.status.history == [] and service.bundle is None
    finally:
        await service.stop()
        await db.close()


async def test_turning_training_off_and_on(tmp_path: Path) -> None:
    db = Database(tmp_path / "jarvis.db")
    await db.connect()
    market = SyntheticMarket(tmp_path / "market", {}, down=True)
    service, _ = await make_training(tmp_path, market, db)
    try:
        await service.set_enabled(False)
        await service.start()
        assert service.status.state == "off" and not service.status.enabled
        await asyncio.sleep(0.1)
        assert market.syncs == []  # off: it doesn't even look for data
        await service.set_enabled(True)
        await eventually(lambda: market.syncs, 30)
    finally:
        await service.stop()
        await db.close()


# Live odds ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def nq_bundle() -> tuple[Bundle, Bars]:
    nq = futures_bars(date(2023, 6, 1), date(2024, 7, 31), 20000.0, noise=2.0, seed=41)
    data = build(nq, "NQ", NQ_RULES, RTH, RULES)
    periods = Periods(oos_start=date(2024, 3, 1), holdout_start=date(2024, 6, 1))
    trained = train(data, periods, candidates=FAST)
    assert trained.model is not None
    return Bundle(trained, 1, "2024-07-31"), nq


def until(bars: Bars, day: date, hhmm: str) -> Bars:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return bars.between(
        0, int(datetime(day.year, day.month, day.day, hh, mm, tzinfo=NY).timestamp())
    )


def test_odds_during_the_session(nq_bundle: tuple[Bundle, Bars]) -> None:
    bundle, nq = nq_bundle
    odds = level_odds(bundle, until(nq, date(2024, 7, 31), "11:02"), "NQ", NQ_RULES, RTH, RULES)
    assert odds["session"] == "open"
    assert odds["model_used"] is False  # random prices: not confirmed
    assert "isn't confirmed" in odds["model_note"]
    sides = [level["side"] for level in odds["levels"]]
    assert sides.count("resistance") == 2 and sides.count("support") == 2
    for level in odds["levels"]:
        assert 0.0 < level["p_hold_baseline"] < 1.0
        assert level["p_hold_model"] is None
        assert level["distance_atr"] >= 0
    # The 11:00 candle is still forming at 11:02: odds use complete candles only.
    assert odds["as_of"] == datetime(2024, 7, 31, 11, 0, tzinfo=NY).astimezone(UTC).isoformat()

    proven = Bundle(bundle.trained, 1, "2024-07-31")
    proven.trained.report = {**bundle.trained.report, "status": "confirmed", "usable_for": ["NQ"]}
    try:
        odds = level_odds(proven, until(nq, date(2024, 7, 31), "11:02"), "NQ", NQ_RULES, RTH, RULES)
        assert odds["model_used"] is True
        assert all(0.0 < level["p_hold_model"] < 1.0 for level in odds["levels"])
    finally:
        proven.trained.report = bundle.trained.report


def test_odds_score_a_level_the_last_candle_touched(nq_bundle: tuple[Bundle, Bars]) -> None:
    bundle, nq = nq_bundle
    day = date(2024, 7, 30)
    bars = resample(until(nq, day, "16:00"), 5)
    ctx = Context(bars)
    a, z = session_windows(ctx, *RTH)[-1]
    levels = session_levels(nq, int(bars.t[a]), "NQ", NQ_RULES, RULES)
    assert levels is not None
    for i, k, side in _day_touches(ctx, a + 1, z, levels, RULES):
        price, close = levels.levels[k].price, float(bars.close[i])
        prices = [lv.price for lv in levels.levels]
        same_side = [p for p in prices if (p > close) == (price > close)]
        if min(same_side, key=lambda p: abs(p - close)) != price:
            continue  # another level is nearer: not listed first
        moment = datetime.fromtimestamp(int(bars.t[i]) + 300, UTC).astimezone(NY)
        odds = level_odds(bundle, until(nq, day, f"{moment:%H:%M}"), "NQ", NQ_RULES, RTH, RULES)
        [touched] = [lv for lv in odds["levels"] if lv["price"] == price]
        assert touched["just_touched"] is True and touched["side"] == side
        assert touched["touches_today"] >= 1
        assert 0.0 < touched["p_hold_baseline"] < 1.0
        break
    else:
        pytest.fail("no touch found")


def test_odds_outside_the_session(nq_bundle: tuple[Bundle, Bars]) -> None:
    bundle, nq = nq_bundle
    odds = level_odds(bundle, until(nq, date(2024, 7, 31), "08:00"), "NQ", NQ_RULES, RTH, RULES)
    assert odds["session"] == "not_started"
    assert odds["levels"] and all("p_hold_baseline" not in lv for lv in odds["levels"])
    odds = level_odds(None, until(nq, date(2024, 7, 31), "11:02"), "NQ", NQ_RULES, RTH, RULES)
    assert odds["model_note"] == "No trained model yet."
    assert all(lv["p_hold_baseline"] is None for lv in odds["levels"])


# API, tools and the briefing ------------------------------------------------------------


def test_training_api_and_the_odds_tool(tmp_path: Path) -> None:
    market = SyntheticMarket(tmp_path / "market", {}, down=True)
    runtime = Runtime(make_settings(tmp_path), market=market)
    with TestClient(create_app(runtime=runtime)) as client:
        status = client.get("/training").json()
        assert status["state"] == "off" and status["enabled"] is False  # tests keep it off
        assert status["report"] is None and status["history"] == []
        assert client.get("/snapshot").json()["training"]["state"] == "off"
        assert client.post("/training/preferences", json={"enabled": False}).status_code == 200
        client.post("/training/run")  # on request even when off
        for _ in range(300):
            if client.get("/training").json()["state"] == "error":
                break
            time.sleep(0.02)
        assert "Dukascopy" in client.get("/training").json()["detail"]

        result = client.portal.call(  # type: ignore[union-attr]
            lambda: runtime.executor.run(
                "level_odds", {"instrument": "NQ"}, ctx=TraceContext.new(), reason="test"
            )
        )
        assert not result.success and result.error is not None
        assert result.error.code == "no_data"


def test_the_report_and_the_briefing_mention_the_model() -> None:
    report = {
        "status": "confirmed",
        "reason": "significantly better than the baseline on unseen months",
        "chosen": "Gradient boosting (small)",
        "touches": {"decided": 41000},
        "out_of_sample": {"touches": 9000, "improvement": 0.012, "t": 2.6},
        "holdout": {"touches": 8000, "improvement": 0.009, "t": 2.1},
        "holdout_by_market": {"NQ": {"improvement": 0.01}, "XAUUSD": {"improvement": -0.002}},
        "usable_for": ["NQ"],
        "importance": [{"feature": "touch", "label": "Touch number today", "loss_increase": 0.01}],
        "calibration": [],
    }
    status = TrainingStatus(
        TrainingState.WAITING, True, None, None, None,
        {"run": 3, "data_until": "2026-10-02"}, report, 3, [],
    )  # fmt: skip
    summary = _trained_model(status)
    assert summary is not None
    assert summary["verdict"] == "confirmed" and summary["usable_for"] == ["NQ"]
    assert summary["unseen_months"]["t"] == 2.1
    assert summary["what_mattered"] == ["Touch number today"]
    assert summary["trained_on_data_until"] == "2026-10-02"
    assert _trained_model(None) is None


def test_starting_jarvis_does_not_load_scikit_learn() -> None:
    """It takes about a second; JARVIS starts far more often than it trains."""
    code = "import sys, jarvis.runtime; sys.exit('sklearn' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
