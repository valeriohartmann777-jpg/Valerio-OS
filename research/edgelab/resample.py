"""Causal resampling of base bars into strategy and context timeframes.

Bins are anchored on wall-clock time in the session timezone (default anchor
18:00, the Globex open), so 30-minute bars line up with 09:30 and 1-hour bars can
be anchored at 09:30 for RTH work. Each output bar carries ``available_at`` =
bar open + bar length: the earliest moment its OHLC is final.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import SessionTemplate, parse_hhmm
from .sessions import phase_end_times
from .timing import as_ns


def bin_starts(index: pd.DatetimeIndex, minutes: int, tz: str = "America/New_York", anchor: str = "18:00") -> pd.DatetimeIndex:
    """Open time of the ``minutes``-bin that contains each timestamp.

    Binning happens on wall-clock time, then each bin label is converted back with
    the bar's own UTC offset, so the repeated 01:00 hour in November is not merged.
    """
    if index.tz is None:
        raise ValueError("tz-aware index required")
    wall = index.tz_convert(tz).tz_localize(None)
    utc = index.tz_convert("UTC").tz_localize(None)
    offset = wall - utc
    a = parse_hhmm(anchor)
    anchor_wall = pd.Timestamp("2000-01-01") + pd.Timedelta(hours=a.hour, minutes=a.minute)
    step = pd.Timedelta(minutes=minutes)
    k = (wall - anchor_wall) // step
    bin_wall = anchor_wall + pd.to_timedelta(np.asarray(k) * minutes, unit="m")
    bin_utc = pd.DatetimeIndex(bin_wall - offset)
    return bin_utc.tz_localize("UTC").tz_convert(tz)


def resample_bars(
    bars: pd.DataFrame,
    minutes: int,
    *,
    tz: str = "America/New_York",
    anchor: str = "18:00",
    sum_cols: tuple[str, ...] = ("bid_volume", "ask_volume", "n_trades"),
) -> pd.DataFrame:
    """Aggregate base bars into ``minutes`` bars labelled by open time.

    Partial bins (missing base bars) are kept; ``n_bars`` reports how many base bars
    contributed. ``available_at`` is conservative: the scheduled bin end.
    """
    starts = bin_starts(bars.index, minutes, tz=tz, anchor=anchor)
    g = bars.groupby(starts, sort=True)
    out = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
            "n_bars": g.size(),
        }
    )
    for c in sum_cols:
        if c in bars.columns:
            out[c] = g[c].sum()
    out.index.name = "ts"
    out["available_at"] = out.index + pd.Timedelta(minutes=minutes)
    return out


def session_bars(
    bars: pd.DataFrame,
    labels: pd.DataFrame,
    template: SessionTemplate,
    phase: str | None = None,
) -> pd.DataFrame:
    """One OHLCV row per trading date, optionally restricted to a phase (ON/RTH/POST).

    ``available_at`` is the SCHEDULED end of the phase on that date (see
    :func:`edgelab.sessions.phase_end_times`), never the last bar present in the data.
    ``high_ts``/``low_ts`` give the open time of the bar where the extreme first
    printed (descriptive, known only afterwards).
    """
    mask = np.ones(len(bars), dtype=bool) if phase is None else (labels["phase"] == phase).to_numpy()
    b = bars[mask]
    td = labels.loc[mask, "trading_date"]
    if b.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "n_bars", "first_ts", "last_ts", "high_ts", "low_ts", "available_at"])
    ts = pd.Series(b.index, index=b.index)
    g = b.groupby(td.to_numpy(), sort=True)
    gts = ts.groupby(td.to_numpy(), sort=True)
    out = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
            "n_bars": g.size(),
            "first_ts": gts.min(),
            "last_ts": gts.max(),
        }
    )
    out["high_ts"] = b["high"].groupby(td.to_numpy()).idxmax()
    out["low_ts"] = b["low"].groupby(td.to_numpy()).idxmin()
    out.index = pd.DatetimeIndex(out.index, name="trading_date")
    out["available_at"] = as_ns(phase_end_times(out.index, template, phase))
    return out


def weekly_bars(sessions: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-session rows (from :func:`session_bars`) into Monday-Friday weeks."""
    if sessions.empty:
        return sessions.copy()
    wk = pd.DatetimeIndex(sessions.index).to_period("W-FRI")
    g = sessions.groupby(wk, sort=True)
    out = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
            "n_sessions": g.size(),
            "available_at": g["available_at"].max(),
        }
    )
    out.index.name = "week"
    return out
