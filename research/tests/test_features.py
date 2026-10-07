"""Feature correctness on hand-built examples (synthetic, software tests only)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import bars_from_rows
from edgelab.features.fvg import detect_fvgs, fvg_outcomes, fvg_state
from edgelab.features.levels import (
    developing_extremes, equal_levels, opening_range, prior_week_levels, session_levels,
)
from edgelab.features.profile import (
    build_profile, developing_value, poc_index, profile_levels, session_profiles, tpo_profile, value_area,
)
from edgelab.features.sr import level_touches, pivot_zones
from edgelab.features.structure import structure_breaks, structure_trend
from edgelab.features.swings import find_pivots, swing_state, zigzag_pivots
from edgelab.features.volatility import atr, efficiency_ratio, true_range
from edgelab.features.vwap import event_anchored_vwap, session_vwap
from edgelab.resample import resample_bars, session_bars
from edgelab.sessions import label_sessions
from edgelab.timing import attach_asof

TZ = "America/New_York"


# ---------------------------------------------------------------- resampling / HTF leakage
def test_resample_aggregates_and_availability(week_bars):
    b5 = resample_bars(week_bars, 5)
    first = week_bars.iloc[:5]
    row = b5.iloc[0]
    assert row["open"] == first["open"].iloc[0] and row["close"] == first["close"].iloc[-1]
    assert row["high"] == first["high"].max() and row["low"] == first["low"].min()
    assert row["volume"] == first["volume"].sum()
    assert (b5["available_at"] - b5.index == pd.Timedelta(minutes=5)).all()


def test_hourly_bins_can_anchor_at_rth_open(week_bars):
    h = resample_bars(week_bars, 60, anchor="09:30")
    starts = h.index[(h.index.hour >= 9) & (h.index.hour < 16)]
    assert set(starts.strftime("%M")) == {"30"}


def test_htf_candle_not_visible_before_it_closes(week_bars):
    """The spec's example: the 10:00-11:00 bar must not reach a 10:35 decision."""
    b5 = resample_bars(week_bars, 5)
    h1 = resample_bars(week_bars, 60, anchor="18:00")
    att = attach_asof(b5.index, 5, h1, ["high", "close"], prefix="h1_")
    day = "2024-03-05"
    bar_1030 = pd.Timestamp(f"{day} 10:30", tz=TZ)  # 5m bar 10:30-10:35, decision 10:35
    bar_1055 = pd.Timestamp(f"{day} 10:55", tz=TZ)  # decision 11:00
    h_1000 = h1.loc[pd.Timestamp(f"{day} 10:00", tz=TZ)]
    h_0900 = h1.loc[pd.Timestamp(f"{day} 09:00", tz=TZ)]
    assert att.loc[bar_1030, "h1_close"] == h_0900["close"]
    assert att.loc[bar_1055, "h1_close"] == h_1000["close"]
    assert att.loc[bar_1055, "h1_high"] == h_1000["high"]


# ---------------------------------------------------------------- ATR / VWAP
def test_true_range_and_atr():
    b = bars_from_rows([(10, 11, 9, 10), (10, 15, 10, 14), (14, 14, 12, 13)])
    assert true_range(b).tolist() == [2.0, 5.0, 2.0]
    a = atr(b, n=2, method="sma")
    assert np.isnan(a.iloc[0]) and a.iloc[1] == 3.5 and a.iloc[2] == 3.5
    er = efficiency_ratio(b["close"], 2)
    assert er.iloc[2] == pytest.approx(3 / 5)


def test_session_vwap_hand_computed_and_resets():
    b = bars_from_rows([(10, 12, 8, 10, 100), (10, 13, 10, 13, 300), (20, 20, 20, 20, 50)])
    group = pd.Series(["d1", "d1", "d2"], index=b.index)
    v = session_vwap(b, group)
    tp = np.array([10.0, 12.0, 20.0])
    assert v["vwap"].iloc[0] == pytest.approx(10.0)
    assert v["vwap"].iloc[1] == pytest.approx((10 * 100 + 12 * 300) / 400)
    assert v["vwap"].iloc[2] == pytest.approx(20.0)  # reset
    var1 = (100 * tp[0] ** 2 + 300 * tp[1] ** 2) / 400 - v["vwap"].iloc[1] ** 2
    assert v["vwap_sd"].iloc[1] == pytest.approx(np.sqrt(var1))


def test_event_anchored_vwap_respects_activation():
    b = bars_from_rows([(10, 10, 10, 10, 100), (20, 20, 20, 20, 100), (30, 30, 30, 30, 100), (40, 40, 40, 40, 100)])
    av = event_anchored_vwap(b, anchor_pos=np.array([0, 1]), active_from_pos=np.array([0, 3]))
    assert av.tolist()[:3] == [10.0, 15.0, 20.0]  # second anchor not active yet
    assert av.iloc[3] == pytest.approx(30.0)        # anchored at bar 1: (20+30+40)/3
    with pytest.raises(ValueError):
        event_anchored_vwap(b, np.array([2]), np.array([1]))


# ---------------------------------------------------------------- profiles
def test_uniform_allocation_and_value_area():
    p = build_profile(np.array([100.0]), np.array([101.0]), np.array([100.5]), np.array([50.0]), 0.25, "uniform")
    assert p.levels.tolist() == [100.0, 100.25, 100.5, 100.75, 101.0]
    assert np.allclose(p.volume, 10.0)
    vol = np.array([1, 2, 5, 9, 4, 3, 1], float)
    assert value_area(vol, 0.70, "single") == (3, 2, 4)
    tie = np.array([1, 5, 1, 5, 1], float)  # two equal peaks: nearest the weighted mean (index 2) -> lower
    assert poc_index(tie) == 1


@settings(max_examples=150, deadline=None)
@given(st.lists(st.integers(min_value=0, max_value=50), min_size=1, max_size=60), st.sampled_from(["single", "dual"]))
def test_value_area_invariants(vols, method):
    v = np.array(vols, float)
    if v.sum() == 0:
        assert value_area(v)[0] == -1
        return
    poc, lo, hi = value_area(v, 0.70, method)
    assert lo <= poc <= hi
    assert v[lo:hi + 1].sum() >= 0.70 * v.sum() - 1e-9
    assert v[poc] == v.max()


def test_session_profile_only_known_after_session(week_bars, week_labels, template, es):
    prof = session_profiles(week_bars, week_labels, tick_size=es.tick_size, template=template, phase="RTH")
    lv = session_levels(week_bars, week_labels, 1, template, table=prof, columns=("poc", "vah", "val"), prefix="pv_")
    d1, d2 = prof.index[0], prof.index[1]
    td = week_labels["trading_date"]
    rth = week_labels["is_rth"]
    assert lv.loc[(td == d1).to_numpy(), "pv_poc"].isna().all()
    assert (lv.loc[((td == d2) & rth).to_numpy(), "pv_poc"] == prof.loc[d1, "poc"]).all()
    # developing value at the last RTH bar equals the completed session profile
    dv = developing_value(week_bars, week_labels, tick_size=es.tick_size, phase="RTH")
    last_rth = np.flatnonzero(((td == d1) & rth).to_numpy())[-1]
    assert dv["dev_poc"].iloc[last_rth] == prof.loc[d1, "poc"]
    assert dv["dev_vah"].iloc[last_rth] == prof.loc[d1, "vah"]
    assert dv["dev_val"].iloc[last_rth] == prof.loc[d1, "val"]


def test_tpo_profile_counts_and_poor_high():
    b = bars_from_rows([(100, 101, 100, 101), (101, 101, 100, 100), (100, 100.5, 99.5, 100)], start="2024-03-05 09:30", minutes=30)
    mod = (b.index.hour * 60 + b.index.minute).to_numpy()
    t = tpo_profile(b, mod, tick_size=0.5, start_minute=570, period_minutes=30)
    assert t["levels"].tolist() == [99.5, 100.0, 100.5, 101.0]
    assert t["tpo"].tolist() == [1, 3, 3, 2]
    assert t["poor_high"] and not t["poor_low"] and t["excess_low"] == 1


# ---------------------------------------------------------------- swings / structure
def test_pivot_confirmation_delay():
    highs = [1, 2, 5, 3, 2, 1, 1, 1]
    b = bars_from_rows([(h, h, h - 1, h) for h in highs])
    p = find_pivots(b, left=2, right=2)
    ph = p[p["kind"] == "H"]
    assert ph["pivot_pos"].tolist() == [2] and ph["confirm_pos"].tolist() == [4]
    stt = swing_state(len(b), p, b.index)
    assert np.isnan(stt["last_h"].iloc[3]) and stt["last_h"].iloc[4] == 5


def test_pivot_plateau_takes_first_bar():
    highs = [1, 2, 5, 5, 3, 2, 1]
    b = bars_from_rows([(h, h, h - 1, h) for h in highs])
    ph = find_pivots(b, 2, 2).query("kind == 'H'")
    assert ph["pivot_pos"].tolist() == [2]


def test_zigzag_confirms_on_reversal():
    rows = [(100, 101, 99, 100), (100, 105, 100, 104), (104, 110, 104, 109), (109, 109, 106, 107), (107, 107, 104, 105)]
    z = zigzag_pivots(bars_from_rows(rows), threshold=5.0)
    h = z[z["kind"] == "H"].iloc[0]
    assert h["pivot_pos"] == 2 and h["price"] == 110 and h["confirm_pos"] == 4


def test_structure_break_needs_confirmed_swing():
    highs = [1, 2, 5, 3, 2, 3, 6, 4]
    b = bars_from_rows([(h - 0.5, h, h - 1, h - 0.2) for h in highs])
    p = find_pivots(b, 2, 2)
    br = structure_breaks(b, p, mode="close")
    up = br[br["direction"] == 1]
    assert up["pos"].tolist() == [6] and up["level"].tolist() == [5]
    tr = structure_trend(len(b), br)
    assert tr[5] == 0 or tr[5] == -1
    assert tr[6] == 1


# ---------------------------------------------------------------- FVG
def test_fvg_detection_and_fill():
    rows = [(100, 101, 99, 100.5), (100.5, 104, 100.5, 103.8), (103.8, 105, 102, 104.5), (104.5, 104.6, 101.5, 102), (102, 102, 100.5, 100.8)]
    b = bars_from_rows(rows)
    f = detect_fvgs(b)
    bull = f[f["direction"] == 1].iloc[0]
    assert bull["pos"] == 2 and bull["bottom"] == 101 and bull["top"] == 102
    out = fvg_outcomes(b, f[f["direction"] == 1], max_bars=5).iloc[0]
    assert out["touch_pos"] == 3 and out["mid_pos"] == 3 and out["full_pos"] == 4
    stt = fvg_state(b, f[f["direction"] == 1], invalidate="close_through")
    assert np.isnan(stt["bull_top"].iloc[1]) and stt["bull_top"].iloc[2] == 102
    assert np.isnan(stt["bull_top"].iloc[4])  # close 100.8 < bottom 101 -> invalidated


# ---------------------------------------------------------------- levels
def test_prior_day_and_overnight_levels(week_bars, week_labels, template):
    pd_rth = session_levels(week_bars, week_labels, 1, template, phase="RTH", which="previous")
    on_cur = session_levels(week_bars, week_labels, 1, template, phase="ON", which="current")
    sb = session_bars(week_bars, week_labels, template, "RTH")
    on = session_bars(week_bars, week_labels, template, "ON")
    td = week_labels["trading_date"]
    d1, d2 = sb.index[0], sb.index[1]
    m2 = (td == d2).to_numpy()
    assert (pd_rth.loc[m2, "pd_rth_high"] == sb.loc[d1, "high"]).all()
    assert pd_rth.loc[(td == d1).to_numpy(), "pd_rth_high"].isna().all()
    on_bars = m2 & (week_labels["phase"] == "ON").to_numpy()
    rth_bars = m2 & week_labels["is_rth"].to_numpy()
    assert on_cur.loc[on_bars, "cd_on_high"].iloc[:-1].isna().all()   # developing overnight never used
    assert (on_cur.loc[rth_bars, "cd_on_high"] == on.loc[d2, "high"]).all()


def test_opening_range_availability(week_bars, week_labels, template):
    orr = opening_range(week_bars, week_labels, template, 1, minutes=30)
    day = "2024-03-05"
    t_0958 = pd.Timestamp(f"{day} 09:58", tz=TZ)  # decision 09:59
    t_0959 = pd.Timestamp(f"{day} 09:59", tz=TZ)  # decision 10:00 = OR complete
    window = week_bars.loc[pd.Timestamp(f"{day} 09:30", tz=TZ): t_0959]
    assert np.isnan(orr.loc[t_0958, "or_high"])
    assert orr.loc[t_0959, "or_high"] == window["high"].max()


def test_developing_extremes_and_prior_week(week_bars, week_labels, template):
    de = developing_extremes(week_bars, week_labels, "RTH")
    rth = week_labels["is_rth"].to_numpy()
    i = np.flatnonzero(rth)[5]
    day_mask = (week_labels["trading_date"] == week_labels["trading_date"].iloc[i]).to_numpy() & rth
    first = np.flatnonzero(day_mask)[0]
    assert de["dev_high"].iloc[i] == week_bars["high"].iloc[first:i + 1].max()
    assert de["dev_high_prev"].iloc[i] == week_bars["high"].iloc[first:i].max()
    pw = prior_week_levels(week_bars, week_labels, 1, template)
    assert pw["pw_high"].isna().all()  # only one week of data


def test_equal_highs_definition():
    hs = [10, 11, 15, 12, 11, 10, 9, 10, 12, 15.1, 12, 11, 10]
    b = bars_from_rows([(h - 0.5, h, h - 1, h - 0.5) for h in hs])
    p = find_pivots(b, 2, 2)
    a = pd.Series(np.full(len(b), 2.0), index=b.index)
    eq = equal_levels(b, p, a, kind="H", tol_atr=0.1, min_sep_bars=3, min_reaction_atr=1.0)
    assert len(eq) == 1 and eq["level"].iloc[0] == 15.1 and eq["confirm_pos"].iloc[0] == 11


# ---------------------------------------------------------------- S/R
def test_level_touches_counts_once_per_visit():
    lvl = 100.0
    closes = [105, 103, 100.5, 100.2, 100.4, 102, 104, 101, 100.1, 103, 99, 98]
    rows = [(c, c + 0.3, c - 0.3, c) for c in closes]
    b = bars_from_rows(rows)
    a = pd.Series(np.full(len(b), 2.0), index=b.index)
    ev = level_touches(b, np.full(len(b), lvl), a, side="support", zone_atr=0.25, away_atr=0.5, break_atr=0.25)
    touches = ev[ev["event"] == "touch"]
    assert touches["touch_number"].tolist() == [1, 2]
    assert touches["confirm_pos"].tolist() == [5, 9]
    assert ev["event"].tolist() == ["touch", "touch", "break"]  # one break, then the level is dead
    assert ev["confirm_pos"].iloc[-1] == 10


def test_level_touches_needs_price_on_the_right_side_first():
    # price starts BELOW the "support": nothing until a close above level + zone arms it
    closes = [97, 98, 99, 100.2, 101, 102, 100.3, 102.5, 103]
    b = bars_from_rows([(c, c + 0.3, c - 0.3, c) for c in closes])
    a = pd.Series(np.full(len(b), 2.0), index=b.index)
    ev = level_touches(b, np.full(len(b), 100.0), a, side="support", zone_atr=0.25, away_atr=0.5, break_atr=0.25)
    assert ev["event"].tolist() == ["touch"] and ev["confirm_pos"].tolist() == [7]


def test_pivot_zones_merge_nearby_pivots():
    hs = [10, 12, 20, 12, 10, 12, 20.2, 12, 10, 11, 10]
    b = bars_from_rows([(h - 0.5, h, h - 1, h - 0.5) for h in hs])
    p = find_pivots(b, 2, 2)
    a = pd.Series(np.full(len(b), 2.0), index=b.index)
    state, zones = pivot_zones(b, p, a, merge_tol_atr=0.25)
    z20 = zones[(zones["center"] > 19) & (zones["center"] < 21)]
    assert len(z20) == 1 and z20["n_pivots"].iloc[0] == 2
