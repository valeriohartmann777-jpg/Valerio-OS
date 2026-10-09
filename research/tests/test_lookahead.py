"""Lookahead property tests.

Two properties must hold for every feature, level, event detector and for the
execution engine:

1. TRUNCATION: computing on data that ends at bar k gives the same values for all
   rows < k as computing on the full data.
2. FUTURE PERTURBATION: replacing every bar from k onwards by a different price path
   (same timestamps) leaves all rows < k unchanged.

Property 2 is stronger: it also catches full-sample normalisation, centred windows
and ``available_at`` taken from the last bar in the file instead of the schedule.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import cme_schedule, random_walk_bars
from edgelab.config import load_cost_model, load_instrument, load_session_template
from edgelab.execution.engine import ExecConfig, simulate
from edgelab.features.candles import candle_features
from edgelab.features.fvg import detect_fvgs, fvg_state
from edgelab.features.levels import developing_extremes, equal_levels, opening_range, prior_week_levels, session_levels
from edgelab.features.profile import composite_profiles, developing_value, session_profiles
from edgelab.features.regime import daily_regimes
from edgelab.features.sr import level_touches, pivot_zones
from edgelab.features.structure import structure_breaks, structure_trend
from edgelab.features.swings import find_pivots, swing_state, zigzag_pivots
from edgelab.features.volatility import adx, atr, efficiency_ratio, trailing_percentile
from edgelab.features.vwap import session_vwap
from edgelab.resample import resample_bars, session_bars
from edgelab.sessions import label_sessions
from edgelab.timing import attach_asof

TPL = load_session_template("cme_equity_index")
ES = load_instrument("ES")
TZ = "America/New_York"


@pytest.fixture(scope="module")
def data():
    idx = cme_schedule("2024-03-04", 8)  # spans the 2024-03-10 DST change
    bars = random_walk_bars(idx, seed=7)
    return bars


def cut_points(bars: pd.DataFrame) -> list[int]:
    idx = bars.index
    want = [
        pd.Timestamp("2024-03-06 03:17", tz=TZ),  # mid overnight
        pd.Timestamp("2024-03-07 09:30", tz=TZ),  # exactly the RTH open
        pd.Timestamp("2024-03-11 11:43", tz=TZ),  # mid RTH, after DST change
        pd.Timestamp("2024-03-12 16:20", tz=TZ),  # post-close
        pd.Timestamp("2024-03-12 18:00", tz=TZ),  # session boundary (opens 03-13 trade date)
    ]
    ks = [int(idx.searchsorted(t)) for t in want]
    assert all(0 < k < len(idx) for k in ks), "every cut must fall inside the data"
    return ks


def perturb_after(bars: pd.DataFrame, k: int, seed: int = 99) -> pd.DataFrame:
    """Same timestamps; a different random path from bar k on."""
    alt = random_walk_bars(bars.index[k:], seed=seed, start=float(bars["close"].iloc[k - 1]) + 7.0, vol_ticks=5.0)
    out = bars.copy()
    out.iloc[k:] = alt.to_numpy()
    return out


def L(b):
    return label_sessions(b.index, TPL)


def _atr(b):
    return atr(b, 14)


FEATURES = {
    "atr": lambda b: _atr(b).to_frame(),
    "adx": lambda b: adx(b, 14),
    "efficiency_ratio": lambda b: efficiency_ratio(b["close"], 20).to_frame(),
    "atr_percentile": lambda b: trailing_percentile(_atr(b), 300).to_frame(),
    "candles": lambda b: candle_features(b, _atr(b)),
    "vwap_rth": lambda b: session_vwap(b, L(b)["trading_date"].where(L(b)["is_rth"])),
    "vwap_globex": lambda b: session_vwap(b, L(b)["trading_date"]),
    "dev_value_rth": lambda b: developing_value(b, L(b), tick_size=ES.tick_size, phase="RTH", va_every=7),
    "prior_rth_ohlc": lambda b: session_levels(b, L(b), 1, TPL, phase="RTH", which="previous"),
    "prior_full_ohlc": lambda b: session_levels(b, L(b), 1, TPL, phase=None, which="previous"),
    "prior2_rth_ohlc": lambda b: session_levels(b, L(b), 1, TPL, phase="RTH", which="previous", lag=1),
    "current_overnight": lambda b: session_levels(b, L(b), 1, TPL, phase="ON", which="current"),
    "prior_value_area": lambda b: session_levels(
        b, L(b), 1, TPL, table=session_profiles(b, L(b), tick_size=ES.tick_size, template=TPL, phase="RTH"),
        columns=("poc", "vah", "val", "shape"), prefix="pv_"),
    "composite_3d": lambda b: session_levels(
        b, L(b), 1, TPL, table=composite_profiles(b, L(b), tick_size=ES.tick_size, template=TPL, n_sessions=3),
        columns=("c3_poc", "c3_vah", "c3_val"), prefix=""),
    "opening_range": lambda b: opening_range(b, L(b), TPL, 1, minutes=30),
    "initial_balance": lambda b: opening_range(b, L(b), TPL, 1),
    "dev_extremes": lambda b: developing_extremes(b, L(b), "RTH"),
    "prior_week": lambda b: prior_week_levels(b, L(b), 1, TPL),
    "daily_regime": lambda b: session_levels(
        b, L(b), 1, TPL, table=daily_regimes(session_bars(b, L(b), TPL, "RTH"), window=3), columns=("vol_regime", "trend_regime", "atr_d"), prefix="rg_"),
    "swing_state": lambda b: swing_state(len(b), find_pivots(b, 3, 3), b.index),
    "zigzag_state": lambda b: swing_state(len(b), zigzag_pivots(b, 3 * _atr(b).to_numpy()), b.index),
    "fvg_state": lambda b: fvg_state(b, detect_fvgs(b, _atr(b)), invalidate="close_through", max_age=120),
    "structure_trend": lambda b: pd.DataFrame({"t": structure_trend(len(b), structure_breaks(b, find_pivots(b, 3, 3)))}, index=b.index),
    "sr_zones": lambda b: pivot_zones(b, find_pivots(b, 5, 5), _atr(b))[0],
    "htf_60m": lambda b: attach_asof(b.index, 1, resample_bars(b, 60), ["open", "high", "low", "close"], prefix="h1_"),
    "htf_240m": lambda b: attach_asof(b.index, 1, resample_bars(b, 240), ["high", "low", "close"], prefix="h4_"),
    "htf_30m_rth_anchor": lambda b: attach_asof(b.index, 1, resample_bars(b, 30, anchor="09:30"), ["high", "close"], prefix="m30_"),
}


def _eq(a: pd.DataFrame, b: pd.DataFrame, k: int, name: str) -> None:
    left = a.iloc[:k].reset_index(drop=True)
    right = b.iloc[:k].reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False, check_categorical=False, obj=name)


@pytest.mark.parametrize("name", sorted(FEATURES))
def test_truncation_invariance(data, name):
    fn = FEATURES[name]
    full = fn(data)
    for k in cut_points(data):
        _eq(full, fn(data.iloc[:k]), k, f"{name}@trunc{k}")


@pytest.mark.parametrize("name", sorted(FEATURES))
def test_future_perturbation_invariance(data, name):
    fn = FEATURES[name]
    full = fn(data)
    for k in cut_points(data):
        _eq(full, fn(perturb_after(data, k)), k, f"{name}@perturb{k}")


EVENTS = {
    "pivots": lambda b: find_pivots(b, 3, 3),
    "fvgs": lambda b: detect_fvgs(b, _atr(b)).rename(columns={"pos": "confirm_pos"}),
    "equal_highs": lambda b: equal_levels(b, find_pivots(b, 3, 3), _atr(b), kind="H", tol_atr=0.25, min_sep_bars=5),
    "structure_breaks": lambda b: structure_breaks(b, find_pivots(b, 3, 3)).rename(columns={"pos": "confirm_pos"}),
    "touches_prior_low": lambda b: level_touches(
        b, session_levels(b, L(b), 1, TPL, phase="RTH", which="previous")["pd_rth_low"].to_numpy(), _atr(b), side="support"),
}


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_event_detectors_are_causal(data, name):
    fn = EVENTS[name]
    full = fn(data)
    for k in cut_points(data):
        for variant in (data.iloc[:k], perturb_after(data, k)):
            ev = fn(variant)
            a = full[full["confirm_pos"] < k].reset_index(drop=True)
            b = ev[ev["confirm_pos"] < k].reset_index(drop=True)
            pd.testing.assert_frame_equal(a, b, check_dtype=False, obj=f"{name}@{k}")


def test_execution_engine_is_causal(data):
    lab = L(data)
    rng = np.random.default_rng(5)
    pos = np.sort(rng.choice(np.arange(100, len(data) - 100), 300, replace=False))
    c = data["close"].to_numpy()
    a = _atr(data).to_numpy()
    d = rng.choice([1, -1], len(pos))
    kinds = rng.choice(["market", "limit", "stop"], len(pos))
    sig = pd.DataFrame({
        "decision_ts": data.index[pos] + pd.Timedelta(minutes=1),
        "direction": d,
        "entry_type": kinds,
        "entry_price": np.where(kinds == "limit", c[pos] - d * 0.5 * a[pos], np.where(kinds == "stop", c[pos] + d * 0.5 * a[pos], np.nan)),
        "stop_price": c[pos] - d * 2.0 * a[pos],
        "target_price": c[pos] + d * 3.0 * a[pos],
    })
    sig["entry_price"] = np.round(sig["entry_price"] / 0.25) * 0.25
    sig["stop_price"] = np.round(sig["stop_price"] / 0.25) * 0.25
    sig["target_price"] = np.round(sig["target_price"] / 0.25) * 0.25
    cost = load_cost_model("ES", "BASE")
    cfg = ExecConfig(allow_overlap=True, max_hold_bars=90)
    full = simulate(data, lab, sig, ES, cost, cfg).trades
    for k in cut_points(data):
        cut_ts = data.index[k]
        for variant in (data.iloc[:k], perturb_after(data, k)):
            tr = simulate(variant, L(variant), sig, ES, cost, cfg).trades
            a_ = full[full["exit_ts"] < cut_ts].reset_index(drop=True)
            b_ = tr[tr["exit_ts"] < cut_ts].reset_index(drop=True)
            # a trade that exited before the cut in the full run must be identical
            pd.testing.assert_frame_equal(a_, b_, check_dtype=False, obj=f"engine@{k}")


# ------------------------------------------------------------------ harness self-check
def _observed_overnight(b):
    """BUGGY on purpose: treats the last bar in the data as the end of the overnight session."""
    lab = L(b)
    sb = session_bars(b, lab, TPL, "ON")
    sb["available_at"] = sb["last_ts"] + pd.Timedelta(minutes=1)
    return session_levels(b, lab, 1, TPL, table=sb, which="current", columns=("high", "low"), prefix="bug_")


def _htf_by_bin_start(b):
    """BUGGY on purpose: joins 1H bars on their start time, i.e. before they close."""
    h = resample_bars(b, 60)
    h["available_at"] = h.index
    return attach_asof(b.index, 1, h, ["high", "close"])


LEAKY = {
    "centered_window": lambda b: b["close"].rolling(5, center=True).mean().to_frame(),
    "full_sample_zscore": lambda b: ((b["close"] - b["close"].mean()) / b["close"].std()).to_frame(),
    "todays_final_high": lambda b: b.groupby(L(b)["trading_date"].to_numpy())["high"].transform("max").to_frame(),
    "htf_by_bin_start": _htf_by_bin_start,
    "observed_session_end": _observed_overnight,
}


@pytest.mark.parametrize("name", sorted(LEAKY))
def test_harness_detects_known_leaks(data, name):
    """Each deliberately leaky feature must FAIL at least one invariance check."""
    fn = LEAKY[name]
    full = fn(data)
    caught = False
    for k in cut_points(data):
        for variant in (data.iloc[:k], perturb_after(data, k)):
            try:
                _eq(full, fn(variant), k, name)
            except AssertionError:
                caught = True
    assert caught, f"harness failed to detect the leak in {name}"
