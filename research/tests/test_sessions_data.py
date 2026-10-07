"""Sessions, timezone/DST handling, loading and data-quality checks."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from edgelab.config import load_session_template
from edgelab.data.loader import infer_bar_minutes, load_bars
from edgelab.data.quality import check_bars, render_markdown
from edgelab.data.rolls import equity_index_roll_dates, third_friday
from edgelab.sessions import label_sessions, phase_end_times, session_calendar

TZ = "America/New_York"


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz=TZ)


def test_trading_date_and_buckets(template):
    idx = pd.DatetimeIndex([
        ts("2024-03-03 18:00"),  # Sunday evening -> Monday session
        ts("2024-03-04 07:59"), ts("2024-03-04 08:00"), ts("2024-03-04 09:29"),
        ts("2024-03-04 09:30"), ts("2024-03-04 10:29"), ts("2024-03-04 10:30"),
        ts("2024-03-04 12:00"), ts("2024-03-04 14:00"), ts("2024-03-04 15:59"),
        ts("2024-03-04 16:00"), ts("2024-03-04 16:59"), ts("2024-03-04 17:30"),
        ts("2024-03-04 18:00"),  # Monday evening -> Tuesday session
    ])
    lab = label_sessions(idx, template)
    td = [str(d.date()) for d in lab["trading_date"]]
    assert td[0] == "2024-03-04" and td[-1] == "2024-03-05"
    assert all(d == "2024-03-04" for d in td[1:-1])
    assert list(lab["bucket"].astype(str)) == [
        "OVERNIGHT", "OVERNIGHT", "PREMARKET", "PREMARKET", "RTH_OPEN", "RTH_OPEN", "MORNING",
        "MIDDAY", "AFTERNOON", "AFTERNOON", "POST", "POST", "BREAK", "OVERNIGHT",
    ]
    assert list(lab["phase"].astype(str)) == ["ON", "ON", "ON", "ON", "RTH", "RTH", "RTH", "RTH", "RTH", "RTH", "POST", "POST", "BREAK", "ON"]
    assert lab["is_rth"].tolist() == [False] * 4 + [True] * 6 + [False] * 4
    assert lab["session_minute"].iloc[0] == 0


def test_dst_utc_input_maps_to_correct_new_york_session(template):
    # 13:30 UTC is 08:30 ET before the 2024-03-10 change and 09:30 ET after it
    utc = pd.DatetimeIndex([pd.Timestamp("2024-03-08 13:30", tz="UTC"), pd.Timestamp("2024-03-11 13:30", tz="UTC")])
    lab = label_sessions(utc.tz_convert(TZ), template)
    assert lab["bucket"].astype(str).tolist() == ["PREMARKET", "RTH_OPEN"]
    # Fall back: 2024-11-03 01:30 occurs twice in New York; both instants must stay distinct
    a = pd.Timestamp("2024-11-03 05:30", tz="UTC").tz_convert(TZ)
    b = pd.Timestamp("2024-11-03 06:30", tz="UTC").tz_convert(TZ)
    assert a.strftime("%H:%M") == b.strftime("%H:%M") == "01:30"
    lab2 = label_sessions(pd.DatetimeIndex([a, b]), load_session_template("crypto_24x7_ny"))
    assert lab2["minute_of_day"].tolist() == [90, 90]


def test_phase_end_times_are_scheduled(template):
    d = pd.DatetimeIndex(["2024-03-08", "2024-03-11"])
    rth = phase_end_times(d, template, "RTH")
    on = phase_end_times(d, template, "ON")
    assert [t.strftime("%Y-%m-%d %H:%M %z") for t in rth] == ["2024-03-08 16:00 -0500", "2024-03-11 16:00 -0400"]
    assert [t.strftime("%H:%M") for t in on] == ["09:30", "09:30"]


def test_session_calendar_flags_early_close(week_bars, template):
    bars = week_bars.copy()
    lab = label_sessions(bars.index, template)
    # cut Wednesday's RTH at 13:00 (an early close)
    wed = pd.Timestamp("2024-03-06")
    drop = (lab["trading_date"] == wed) & (lab["minute_of_day"] >= 13 * 60)
    bars = bars[~drop.to_numpy()]
    lab = label_sessions(bars.index, template)
    cal = session_calendar(lab, 1, template)
    assert bool(cal.loc[wed, "early_close"]) and not bool(cal.loc[wed, "full_rth"])
    assert cal.drop(index=wed)["full_rth"].all()


def test_loader_close_label_and_aliases(tmp_path):
    # Sierra-Chart-like export: separate Date/Time, 'Last', labelled by bar CLOSE, naive NY time
    p = tmp_path / "es.csv"
    p.write_text(
        "Date, Time, Open, High, Low, Last, Volume\n"
        "2024/03/05, 09:31:00, 5000.00, 5001.00, 4999.75, 5000.50, 120\n"
        "2024/03/05, 09:32:00, 5000.50, 5002.00, 5000.25, 5001.75, 80\n"
    )
    bars, meta = load_bars(p, tz_in=TZ, label="close", bar_minutes=1)
    assert bars.index[0] == ts("2024-03-05 09:30")
    assert list(bars.columns[:5]) == ["open", "high", "low", "close", "volume"]
    assert meta["label_convention"] == "close" and len(meta["sha256"]) == 64


def test_loader_epoch_ms_and_ambiguous_fallback(tmp_path):
    p = tmp_path / "btc.csv"
    t0 = int(pd.Timestamp("2024-11-03 05:00", tz="UTC").timestamp() * 1000)
    rows = [f"{t0 + k * 60000},1,2,0.5,1.5,10" for k in range(5)]
    p.write_text("open_time,open,high,low,close,volume\n" + "\n".join(rows) + "\n")
    bars, _ = load_bars(p, tz_in="UTC", label="open")
    assert bars.index[0] == pd.Timestamp("2024-11-03 05:00", tz="UTC").tz_convert(TZ)  # 01:00 EDT (first of two)
    assert infer_bar_minutes(bars.index) == 1
    # naive New York wall-clock data through the repeated 01:xx hour: 'infer' keeps order
    q = tmp_path / "ny.csv"
    wall = ["2024-11-03 01:58", "2024-11-03 01:59", "2024-11-03 01:00", "2024-11-03 01:01"]
    q.write_text("timestamp,open,high,low,close,volume\n" + "\n".join(f"{w},1,1,1,1,1" for w in wall) + "\n")
    b2, _ = load_bars(q, tz_in=TZ, label="open")
    assert b2.index.is_monotonic_increasing and b2.index.is_unique
    assert (b2.index[2] - b2.index[1]) == pd.Timedelta(minutes=1)


def test_quality_report_counts_problems(week_bars, template, es):
    bars = week_bars.iloc[:3000].copy()
    bars.iloc[10, bars.columns.get_loc("high")] = bars.iloc[10]["low"] - 1.0   # high < low
    bars.iloc[20, bars.columns.get_loc("volume")] = 0.0
    bars.iloc[30, bars.columns.get_loc("volume")] = -5.0
    dup = bars.iloc[[40]]
    bars = pd.concat([bars, dup]).sort_index(kind="stable")
    rep = check_bars(bars, instrument="ES", source="synthetic-test", template=template, bar_minutes=1, tick_size=es.tick_size, roll_cfg=es.roll)
    assert rep["invalid_ohlc"]["high_lt_low"] == 1
    assert rep["zero_volume"] == 1 and rep["negative_volume"] == 1
    assert rep["duplicate_timestamps"] == 1
    assert rep["tick_grid_conformity_pct"] > 99.0
    md = render_markdown([rep])
    assert "DATA_QUALITY_REPORT" in md and "Futures rolls" in md


def test_roll_calendar():
    assert third_friday(2024, 3).isoformat() == "2024-03-15"
    rd = equity_index_roll_dates(pd.Timestamp("2024-01-01").date(), pd.Timestamp("2024-12-31").date())
    assert [r.isoformat() for r in rd] == ["2024-03-07", "2024-06-13", "2024-09-12", "2024-12-12"]
