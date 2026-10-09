"""Session labelling for exchange-traded futures (and 24/7 proxies).

All logic runs on *wall-clock* time in the template's timezone, so DST changes are
handled by the tz database rather than by fixed UTC offsets. The trading date of a
bar follows CME convention: bars at or after ``trading_day_start`` (18:00 ET)
belong to the next trading date, so Sunday 18:00 ET opens Monday's session.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .config import SessionTemplate, minutes_of

MINUTES_PER_DAY = 24 * 60


def wall_clock(index: pd.DatetimeIndex, tz: str) -> pd.DatetimeIndex:
    """Naive wall-clock timestamps of a tz-aware index in ``tz``."""
    if index.tz is None:
        raise ValueError("session logic requires a tz-aware index; localize the data first")
    return index.tz_convert(tz).tz_localize(None)


def minute_of_day(index: pd.DatetimeIndex, tz: str) -> np.ndarray:
    """Wall-clock minutes since midnight (0..1439) in ``tz``."""
    wc = wall_clock(index, tz)
    return (wc.hour * 60 + wc.minute).to_numpy(dtype=np.int32)


def in_time_range(minutes: np.ndarray, start: dt.time, end: dt.time) -> np.ndarray:
    """Membership of wall-clock minutes in ``[start, end)``; wraps midnight when start > end."""
    a, b = minutes_of(start), minutes_of(end)
    if a == b:
        return np.ones_like(minutes, dtype=bool)
    if a < b:
        return (minutes >= a) & (minutes < b)
    return (minutes >= a) | (minutes < b)


def trading_dates(index: pd.DatetimeIndex, template: SessionTemplate) -> pd.DatetimeIndex:
    """Trading date (naive midnight) of every bar under the template's day-start rule."""
    wc = wall_clock(index, template.timezone)
    shift = (MINUTES_PER_DAY - minutes_of(template.trading_day_start)) % MINUTES_PER_DAY
    return (wc + pd.Timedelta(minutes=shift)).normalize()


def label_sessions(index: pd.DatetimeIndex, template: SessionTemplate) -> pd.DataFrame:
    """Session labels for every bar (bar open times).

    Columns
    -------
    trading_date    naive date (midnight) of the CME trade date
    bucket          mutually exclusive research partition (OVERNIGHT ... POST)
    phase           ON / RTH / POST / BREAK
    is_rth          bool, 09:30 <= open time < 16:00
    minute_of_day   wall-clock minutes since midnight
    session_minute  minutes since the trading-day start (18:00 -> 0)
    weekday         weekday of the trading date (0 = Monday)
    """
    mins = minute_of_day(index, template.timezone)
    out = pd.DataFrame(index=index)
    td = trading_dates(index, template)
    out["trading_date"] = td

    bucket = np.full(len(index), "", dtype=object)
    for name, start, end in template.buckets:
        bucket[in_time_range(mins, start, end) & (bucket == "")] = name
    unassigned = bucket == ""
    if unassigned.any():
        bucket[unassigned] = "UNASSIGNED"
    cats = list(template.bucket_order) + (["UNASSIGNED"] if unassigned.any() else [])
    out["bucket"] = pd.Categorical(bucket, categories=cats, ordered=True)

    phase = np.full(len(index), "", dtype=object)
    for name, start, end in template.phases:
        phase[in_time_range(mins, start, end) & (phase == "")] = name
    phase[phase == ""] = "UNASSIGNED"
    out["phase"] = pd.Categorical(phase)

    out["is_rth"] = in_time_range(mins, template.rth_start, template.rth_end)
    out["minute_of_day"] = mins
    start_min = minutes_of(template.trading_day_start)
    out["session_minute"] = (mins - start_min) % MINUTES_PER_DAY
    out["weekday"] = td.weekday.to_numpy()
    return out


def phase_end_times(trading_date_index: pd.DatetimeIndex, template: SessionTemplate, phase: str | None) -> pd.DatetimeIndex:
    """Scheduled END of a phase on each trading date, as tz-aware timestamps.

    This, not the last bar that happens to be in the file, is when a session summary
    becomes known. Using the last observed bar would make a half-finished session look
    complete whenever the data stops early (and in live trading, every session is
    half-finished until it ends). ``phase=None`` means the whole trading date, which
    ends when the next one starts (``trading_day_start`` on the trading date).
    """
    if phase is None:
        end = template.trading_day_start
    else:
        spans = {name: (a, b) for name, a, b in template.phases}
        if phase not in spans:
            raise KeyError(f"phase {phase!r} not in template {template.name}")
        end = spans[phase][1]
    d = pd.DatetimeIndex(trading_date_index).tz_localize(None).normalize()
    wall = d + pd.Timedelta(minutes=minutes_of(end))
    return wall.tz_localize(template.timezone, ambiguous="NaT", nonexistent="shift_forward")


def session_calendar(labels: pd.DataFrame, bar_minutes: int, template: SessionTemplate) -> pd.DataFrame:
    """One row per trading date: bar counts, RTH coverage and early-close / holiday flags.

    ``full_rth`` is True when the first RTH bar opens at the RTH start and the last
    RTH bar opens within one bar of the RTH end. Strategies that need a normal cash
    session should filter on it instead of assuming every date is complete.
    """
    rth_len = minutes_of(template.rth_end) - minutes_of(template.rth_start)
    expected_rth = rth_len // bar_minutes
    g = labels.groupby("trading_date", sort=True)
    ts = pd.Series(labels.index, index=labels.index)
    gts = ts.groupby(labels["trading_date"], sort=True)
    cal = pd.DataFrame({"n_bars": g.size(), "first_ts": gts.min(), "last_ts": gts.max()})
    rth = labels[labels["is_rth"]]
    gr = rth.groupby("trading_date", sort=True)
    cal["n_rth_bars"] = gr.size().reindex(cal.index).fillna(0).astype(int)
    first_rth_min = gr["minute_of_day"].min().reindex(cal.index)
    last_rth_min = gr["minute_of_day"].max().reindex(cal.index)
    cal["rth_first_minute"] = first_rth_min
    cal["rth_last_minute"] = last_rth_min
    cal["expected_rth_bars"] = expected_rth
    cal["rth_coverage"] = cal["n_rth_bars"] / expected_rth
    end_min = minutes_of(template.rth_end)
    cal["full_rth"] = (
        (first_rth_min == minutes_of(template.rth_start))
        & (last_rth_min >= end_min - bar_minutes)
        & (cal["rth_coverage"] >= 0.95)
    ).fillna(False)
    cal["early_close"] = (cal["n_rth_bars"] > 0) & (last_rth_min < end_min - 30)
    cal["no_rth"] = cal["n_rth_bars"] == 0
    cal["weekday"] = pd.DatetimeIndex(cal.index).weekday
    return cal
