"""Exact volume-at-price from executed trades.

The input is minute-price volume of ONE contract per trading date (``data/trades.py``
keeps only the active contract), keyed by integer ticks, so the histogram is exact:
``volume_at_price[price] += trade size`` with no OHLCV allocation. POC and value area use
the same algorithm as the bar-based profiles in ``features/profile.py`` (70 %, single-level
expansion, POC ties to the level nearest the volume-weighted mean, then the lower one).

Availability
------------
* A completed session profile carries ``available_at`` = the scheduled end of its phase
  (``sessions.phase_end_times``), never the time of the last trade in the file.
* The developing value on the minute that opens at ``t`` uses exactly the trades with
  ``ts_event < t + 1 min`` and carries ``known_at = t + 1 min`` (that minute's close), the
  same convention as any feature on a bar.
* A composite uses completed sessions of ONE contract only and is available when the
  last of them has ended; a window that would cross a roll is left empty.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import SessionTemplate
from ..sessions import label_sessions, phase_end_times
from ..timing import as_ns
from .profile import Profile, poc_index, profile_levels, value_area

PHASES = ("RTH", "ALL")
NS_MIN = 60 * 1_000_000_000


def value_area_single(volume: np.ndarray, va_pct: float = 0.70) -> tuple[int, int, int]:
    """``profile.value_area(volume, va_pct, "single")`` with the expansion loop on Python
    floats (several times faster per call; the result is identical and tested to be)."""
    v = np.asarray(volume, float)
    poc = poc_index(v)
    if poc < 0:
        return -1, -1, -1
    total = float(v.sum())
    stop = va_pct * total - 1e-9 * total
    x = v.tolist()
    n = len(x)
    lo = hi = poc
    acc = x[poc]
    while acc < stop and (lo > 0 or hi < n - 1):
        up = x[hi + 1] if hi < n - 1 else -1.0
        dn = x[lo - 1] if lo > 0 else -1.0
        if up > dn:
            hi += 1
            acc += up
        elif dn > up:
            lo -= 1
            acc += dn
        else:
            add = 0.0
            if hi < n - 1:
                hi += 1
                add += up
            if lo > 0:
                lo -= 1
                add += dn
            acc += add
    return poc, lo, hi


def label_minutes(mpv: pd.DataFrame, template: SessionTemplate) -> pd.DataFrame:
    """Add ``trading_date`` and ``phase`` to minute-price volume (``minute`` = UTC ns)."""
    um, inv = np.unique(mpv["minute"].to_numpy(np.int64), return_inverse=True)
    lab = label_sessions(pd.DatetimeIndex(pd.to_datetime(um, unit="ns", utc=True)), template)
    out = mpv.copy()
    out["trading_date"] = lab["trading_date"].to_numpy()[inv]
    out["phase"] = lab["phase"].astype(str).to_numpy()[inv]
    return out


def _mask(df: pd.DataFrame, phase: str) -> np.ndarray:
    if phase == "ALL":
        return np.ones(len(df), dtype=bool)
    if phase == "RTH":
        return (df["phase"] == "RTH").to_numpy()
    raise ValueError(f"phase must be one of {PHASES}")


def session_vap(mpv_l: pd.DataFrame, phase: str, tick_size: float) -> pd.DataFrame:
    """Long table ``trading_date, tick, price, volume`` of every session (one contract each)."""
    d = mpv_l[_mask(mpv_l, phase)]
    tick_fixed = int(round(tick_size * 1e9))
    ticks = d["price"].to_numpy(np.int64) // tick_fixed
    g = (pd.DataFrame({"trading_date": d["trading_date"].to_numpy(), "tick": ticks, "volume": d["volume"].to_numpy(np.int64)})
         .groupby(["trading_date", "tick"], as_index=False, sort=True)["volume"].sum())
    g["price"] = g["tick"] * tick_size
    return g[["trading_date", "tick", "price", "volume"]]


def levels_from_ticks(ticks: np.ndarray, vols: np.ndarray, tick_size: float, va_pct: float = 0.70,
                      va_method: str = "single") -> dict[str, float]:
    """POC / VAL / VAH / range / volume / VWAP of an exact tick histogram."""
    ticks = np.asarray(ticks, np.int64)
    vols = np.asarray(vols, float)
    if len(ticks) == 0 or vols.sum() <= 0:
        return {"poc": np.nan, "val": np.nan, "vah": np.nan, "prof_low": np.nan, "prof_high": np.nan,
                "prof_volume": 0.0, "vwap": np.nan}
    base = int(ticks.min())
    dense = np.zeros(int(ticks.max()) - base + 1)
    np.add.at(dense, ticks - base, vols)
    lv = profile_levels(Profile(base, dense, tick_size), va_pct, va_method)
    lv["vwap"] = float((ticks * vols).sum() / vols.sum() * tick_size)
    return lv


def session_levels(vap: pd.DataFrame, *, tick_size: float, template: SessionTemplate, phase: str,
                   va_pct: float = 0.70, va_method: str = "single") -> pd.DataFrame:
    """One row per session: exact POC/VAL/VAH/VWAP, ``available_at`` = scheduled phase end."""
    rows = []
    for d, g in vap.groupby("trading_date", sort=True):
        rows.append({"trading_date": d, **levels_from_ticks(g["tick"].to_numpy(), g["volume"].to_numpy(), tick_size,
                                                            va_pct, va_method)})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.set_index(pd.DatetimeIndex(out.pop("trading_date"), name="trading_date"))
    out["available_at"] = as_ns(phase_end_times(out.index, template, None if phase == "ALL" else phase))
    return out


def developing_levels(mpv_l: pd.DataFrame, *, phase: str, tick_size: float, va_pct: float = 0.70,
                      va_method: str = "single") -> pd.DataFrame:
    """Developing POC / VAL / VAH / VWAP per minute with trades up to that minute's close."""
    d = mpv_l[_mask(mpv_l, phase)]
    tick_fixed = int(round(tick_size * 1e9))
    cols = ["ts", "known_at", "trading_date", "dev_poc", "dev_val", "dev_vah", "dev_vwap", "cum_volume"]
    parts = []
    for td, g in d.groupby("trading_date", sort=True):
        mins = g["minute"].to_numpy(np.int64)
        ticks = g["price"].to_numpy(np.int64) // tick_fixed
        vols = g["volume"].to_numpy(float)
        um, mi = np.unique(mins, return_inverse=True)
        base = int(ticks.min())
        rel = ticks - base
        inc = np.zeros((len(um), int(rel.max()) + 1))
        np.add.at(inc, (mi, rel), vols)
        cum = np.cumsum(inc, axis=0)
        pv = np.cumsum(np.bincount(mi, weights=ticks * vols, minlength=len(um)))
        cv = cum.sum(axis=1)
        # the profile at minute r spans only the prices traded up to r: the session's later
        # range must not decide where the value-area expansion stops (that would be lookahead)
        lo_m = np.full(len(um), np.iinfo(np.int64).max)
        hi_m = np.full(len(um), -1)
        np.minimum.at(lo_m, mi, rel)
        np.maximum.at(hi_m, mi, rel)
        run_lo, run_hi = np.minimum.accumulate(lo_m), np.maximum.accumulate(hi_m)
        poc = np.empty(len(um))
        val = np.empty(len(um))
        vah = np.empty(len(um))
        for r in range(len(um)):
            lo, hi = int(run_lo[r]), int(run_hi[r])
            seg = cum[r, lo: hi + 1]
            p, a, b = value_area_single(seg, va_pct) if va_method == "single" else value_area(seg, va_pct, va_method)
            poc[r], val[r], vah[r] = base + lo + p, base + lo + a, base + lo + b
        parts.append(pd.DataFrame({
            "ts": pd.to_datetime(um, unit="ns", utc=True), "known_at": pd.to_datetime(um + NS_MIN, unit="ns", utc=True),
            "trading_date": td, "dev_poc": poc * tick_size, "dev_val": val * tick_size, "dev_vah": vah * tick_size,
            "dev_vwap": pv / cv * tick_size, "cum_volume": cv,
        }))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)


def composite_levels(vap: pd.DataFrame, levels: pd.DataFrame, contract: pd.Series, complete: pd.Series, *,
                     n_sessions: int, tick_size: float, va_pct: float = 0.70, va_method: str = "single") -> pd.DataFrame:
    """Composite of the last ``n_sessions`` completed sessions of ONE contract.

    Row ``d`` covers the ``n_sessions`` sessions in the data ending with ``d``; it is
    empty when any of them is incomplete or traded another contract (no profile ever
    mixes contracts). ``available_at`` is that of session ``d``.
    """
    dates = levels.index
    by_date = {d: g for d, g in vap.groupby("trading_date", sort=True)}
    rows = []
    for k, d in enumerate(dates):
        row = {"trading_date": d, "n_sessions": n_sessions}
        win = dates[max(0, k - n_sessions + 1): k + 1]
        ok = (len(win) == n_sessions and all(bool(complete.get(w, False)) for w in win)
              and len({contract.get(w) for w in win}) == 1)
        if ok:
            g = pd.concat([by_date[w] for w in win if w in by_date])
            s = g.groupby("tick")["volume"].sum()
            row.update(levels_from_ticks(s.index.to_numpy(), s.to_numpy(), tick_size, va_pct, va_method))
        else:
            row.update({k2: np.nan for k2 in ("poc", "val", "vah", "prof_low", "prof_high", "prof_volume", "vwap")})
        row["available_at"] = levels.loc[d, "available_at"]
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.set_index(pd.DatetimeIndex(out.pop("trading_date"), name="trading_date"))
