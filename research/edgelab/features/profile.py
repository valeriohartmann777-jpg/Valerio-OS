"""Volume Profile and TPO (Market Profile) construction.

Bar data does not reveal where inside a bar the volume traded, so the allocation
is an explicit, documented approximation:

* ``uniform``  - spread each bar's volume evenly over every price level in [low, high]
* ``typical``  - put all volume at the typical price (H+L+C)/3
* ``close``    - put all volume at the close

With trade-level data, the exact price-volume histogram replaces all three.
Research must check that conclusions do not hinge on the chosen method.

Price levels are bin centres at integer multiples of ``bin_size`` (bin_size =
tick_size * bin_ticks). A level ``L`` collects prices in [L - bin/2, L + bin/2).

Value area (default 70%): start at the POC and repeatedly add the adjacent level
(``single``) or adjacent pair of levels (``dual``, the CBOT two-row method) that
holds more volume; ties expand both sides. Deterministic by construction.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import SessionTemplate
from ..sessions import phase_end_times
from ..timing import as_ns


@dataclass(frozen=True)
class Profile:
    """A price-volume histogram on a regular level grid (ascending prices)."""

    base_index: int          # level index of volume[0]; price = (base_index + i) * bin_size
    volume: np.ndarray
    bin_size: float

    @property
    def levels(self) -> np.ndarray:
        return (self.base_index + np.arange(len(self.volume))) * self.bin_size

    @property
    def total(self) -> float:
        return float(self.volume.sum())


def level_index(price: np.ndarray | float, bin_size: float) -> np.ndarray:
    """Index of the level that contains ``price`` (round half up)."""
    return np.floor(np.asarray(price, dtype=float) / bin_size + 0.5 + 1e-9).astype(np.int64)


def build_profile(
    low: np.ndarray,
    high: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    bin_size: float,
    method: str = "uniform",
) -> Profile:
    """Allocate bar volume onto price levels."""
    low = np.asarray(low, float)
    high = np.asarray(high, float)
    close = np.asarray(close, float)
    vol = np.nan_to_num(np.asarray(volume, float))
    ok = np.isfinite(low) & np.isfinite(high) & np.isfinite(close)
    low, high, close, vol = low[ok], high[ok], close[ok], vol[ok]
    if len(low) == 0:
        return Profile(0, np.zeros(0), bin_size)
    lo = level_index(low, bin_size)
    hi = level_index(high, bin_size)
    base = int(lo.min())
    size = int(hi.max()) - base + 1
    if method == "uniform":
        n = hi - lo + 1
        share = vol / n
        diff = np.zeros(size + 1)
        np.add.at(diff, lo - base, share)
        np.add.at(diff, hi - base + 1, -share)
        hist = np.cumsum(diff)[:-1]
    elif method in ("typical", "close"):
        p = (low + high + close) / 3.0 if method == "typical" else close
        idx = np.clip(level_index(p, bin_size), lo, hi) - base
        hist = np.bincount(idx, weights=vol, minlength=size).astype(float)
    else:
        raise ValueError(f"unknown allocation method {method!r}")
    hist[np.abs(hist) < 1e-12] = 0.0
    return Profile(base, hist, bin_size)


def poc_index(volume: np.ndarray) -> int:
    """Highest-volume level; ties go to the level nearest the volume-weighted mean, then the lower one."""
    v = np.asarray(volume, float)
    if v.size == 0 or v.sum() <= 0:
        return -1
    mx = v.max()
    cand = np.flatnonzero(v >= mx - 1e-12 * max(1.0, mx))
    if cand.size == 1:
        return int(cand[0])
    mean = float((np.arange(v.size) * v).sum() / v.sum())
    d = np.abs(cand - mean)
    return int(cand[np.argmin(d)])  # argmin returns the first (lowest) on exact ties


def value_area(volume: np.ndarray, va_pct: float = 0.70, method: str = "single") -> tuple[int, int, int]:
    """Return (poc, val, vah) as indices into ``volume``."""
    v = np.asarray(volume, float)
    poc = poc_index(v)
    if poc < 0:
        return -1, -1, -1
    total = v.sum()
    target = va_pct * total
    lo = hi = poc
    acc = v[poc]
    n = v.size
    step = 1 if method == "single" else 2
    if method not in ("single", "dual"):
        raise ValueError(method)
    while acc < target - 1e-9 * total and (lo > 0 or hi < n - 1):
        up = v[hi + 1: min(n, hi + 1 + step)].sum() if hi < n - 1 else -1.0
        dn = v[max(0, lo - step): lo].sum() if lo > 0 else -1.0
        if up > dn:
            new_hi = min(n - 1, hi + step)
            acc += v[hi + 1: new_hi + 1].sum()
            hi = new_hi
        elif dn > up:
            new_lo = max(0, lo - step)
            acc += v[new_lo:lo].sum()
            lo = new_lo
        else:
            new_hi = min(n - 1, hi + step)
            new_lo = max(0, lo - step)
            acc += v[hi + 1: new_hi + 1].sum() + v[new_lo:lo].sum()
            hi, lo = new_hi, new_lo
    return poc, lo, hi


def profile_levels(profile: Profile, va_pct: float = 0.70, va_method: str = "single") -> dict[str, float]:
    """POC / VAL / VAH prices plus range and volume of a profile."""
    poc, val, vah = value_area(profile.volume, va_pct, va_method)
    if poc < 0:
        return {"poc": np.nan, "val": np.nan, "vah": np.nan, "prof_low": np.nan, "prof_high": np.nan, "prof_volume": 0.0}
    lv = profile.levels
    nz = np.flatnonzero(profile.volume > 0)
    return {
        "poc": float(lv[poc]),
        "val": float(lv[val]),
        "vah": float(lv[vah]),
        "prof_low": float(lv[nz[0]]),
        "prof_high": float(lv[nz[-1]]),
        "prof_volume": profile.total,
    }


def volume_nodes(profile: Profile, smooth_sigma_bins: float = 2.0, prominence_frac: float = 0.10) -> dict[str, np.ndarray]:
    """High- and low-volume nodes from peaks/troughs of a Gaussian-smoothed profile.

    HVN = local maxima, LVN = local minima lying between two HVNs. ``prominence_frac``
    is relative to the smoothed maximum. Stability across bin size and smoothing must
    be checked before any strategy relies on these nodes.
    """
    from scipy.ndimage import gaussian_filter1d
    from scipy.signal import find_peaks

    v = profile.volume
    if v.size < 3 or v.sum() <= 0:
        return {"hvn": np.array([]), "lvn": np.array([])}
    s = gaussian_filter1d(v, smooth_sigma_bins, mode="constant") if smooth_sigma_bins > 0 else v
    prom = prominence_frac * s.max()
    pk, _ = find_peaks(s, prominence=prom)
    tr, _ = find_peaks(-s, prominence=prom)
    if pk.size >= 2:
        tr = tr[(tr > pk.min()) & (tr < pk.max())]
    else:
        tr = tr[:0]
    lv = profile.levels
    return {"hvn": lv[pk], "lvn": lv[tr]}


def profile_shape(profile: Profile, va_pct: float = 0.70) -> dict[str, float | str]:
    """Numeric shape descriptors and an a-priori label (D / P / b / double).

    Labels are fixed BEFORE looking at data and are themselves hypotheses:
    * double: two HVNs separated by an LVN below 50% of the smaller peak
    * P: POC in the top third of the range;  b: POC in the bottom third;  D: otherwise
    """
    lv = profile_levels(profile, va_pct)
    if not np.isfinite(lv["poc"]):
        return {"shape": "none", "poc_rel": np.nan, "va_width_rel": np.nan}
    rng = lv["prof_high"] - lv["prof_low"]
    poc_rel = (lv["poc"] - lv["prof_low"]) / rng if rng > 0 else 0.5
    va_rel = (lv["vah"] - lv["val"]) / rng if rng > 0 else 1.0
    nodes = volume_nodes(profile, smooth_sigma_bins=max(1.0, len(profile.volume) / 40), prominence_frac=0.15)
    shape = "D"
    if len(nodes["hvn"]) >= 2 and len(nodes["lvn"]) >= 1:
        from scipy.ndimage import gaussian_filter1d

        s = gaussian_filter1d(profile.volume, max(1.0, len(profile.volume) / 40), mode="constant")
        idx = level_index(nodes["hvn"], profile.bin_size) - profile.base_index
        tidx = level_index(nodes["lvn"], profile.bin_size) - profile.base_index
        if s[tidx].min() < 0.5 * np.sort(s[idx])[-2]:
            shape = "double"
    if shape != "double":
        if poc_rel >= 2 / 3:
            shape = "P"
        elif poc_rel <= 1 / 3:
            shape = "b"
    return {"shape": shape, "poc_rel": float(poc_rel), "va_width_rel": float(va_rel)}


def session_profiles(
    bars: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    tick_size: float,
    template: SessionTemplate,
    phase: str | None = "RTH",
    bin_ticks: int = 1,
    method: str = "uniform",
    va_pct: float = 0.70,
    va_method: str = "single",
    with_shape: bool = True,
) -> pd.DataFrame:
    """One completed profile per trading date (optionally for one phase).

    ``available_at`` = scheduled end of the phase: today's RTH POC is unknown until
    16:00, whatever the data contains.
    """
    mask = np.ones(len(bars), bool) if phase is None else (labels["phase"] == phase).to_numpy()
    b = bars[mask]
    td = labels.loc[mask, "trading_date"].to_numpy()
    bin_size = tick_size * bin_ticks
    rows = []
    if len(b):
        ts = b.index
        lo, hi, cl, vo = (b[k].to_numpy(float) for k in ("low", "high", "close", "volume"))
        bounds = np.flatnonzero(np.r_[True, td[1:] != td[:-1], True])
        for s, e in zip(bounds[:-1], bounds[1:]):
            prof = build_profile(lo[s:e], hi[s:e], cl[s:e], vo[s:e], bin_size, method)
            row = {"trading_date": td[s], **profile_levels(prof, va_pct, va_method)}
            if with_shape:
                row.update(profile_shape(prof, va_pct))
            row["first_ts"] = ts[s]
            row["last_ts"] = ts[e - 1]
            rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.set_index(pd.DatetimeIndex(out.pop("trading_date"), name="trading_date"))
    out["available_at"] = as_ns(phase_end_times(out.index, template, phase))
    return out


def composite_profiles(
    bars: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    tick_size: float,
    template: SessionTemplate,
    n_sessions: int,
    phase: str | None = "RTH",
    bin_ticks: int = 1,
    method: str = "uniform",
    va_pct: float = 0.70,
) -> pd.DataFrame:
    """Rolling composite profile of the last ``n_sessions`` completed sessions.

    Row ``d`` summarises sessions ``d-n+1 .. d`` and is available after ``d`` closes.
    """
    mask = np.ones(len(bars), bool) if phase is None else (labels["phase"] == phase).to_numpy()
    b = bars[mask]
    td = labels.loc[mask, "trading_date"].to_numpy()
    bin_size = tick_size * bin_ticks
    if len(b) == 0:
        return pd.DataFrame()
    lo, hi, cl, vo = (b[k].to_numpy(float) for k in ("low", "high", "close", "volume"))
    bounds = np.flatnonzero(np.r_[True, td[1:] != td[:-1], True])
    sess = [(td[s], s, e) for s, e in zip(bounds[:-1], bounds[1:])]
    rows = []
    for k in range(len(sess)):
        if k + 1 < n_sessions:
            continue
        s0 = sess[k - n_sessions + 1][1]
        e0 = sess[k][2]
        prof = build_profile(lo[s0:e0], hi[s0:e0], cl[s0:e0], vo[s0:e0], bin_size, method)
        row = {"trading_date": sess[k][0], **{f"c{n_sessions}_{key}": val for key, val in profile_levels(prof, va_pct).items()}}
        row["last_ts"] = b.index[e0 - 1]
        rows.append(row)
    keys = [f"c{n_sessions}_{k}" for k in ("poc", "val", "vah", "prof_low", "prof_high", "prof_volume")]
    if not rows:
        return pd.DataFrame(columns=keys + ["last_ts", "available_at"], index=pd.DatetimeIndex([], name="trading_date"))
    out = pd.DataFrame(rows).set_index("trading_date")
    out.index = pd.DatetimeIndex(out.index, name="trading_date")
    out["available_at"] = as_ns(phase_end_times(out.index, template, phase))
    return out


def developing_value(
    bars: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    tick_size: float,
    phase: str | None = "RTH",
    bin_ticks: int = 1,
    method: str = "uniform",
    va_pct: float = 0.70,
    va_method: str = "single",
    va_every: int = 1,
) -> pd.DataFrame:
    """Developing POC / VAH / VAL inside each session, using only bars up to each row.

    The value area is recomputed every ``va_every`` bars and carried forward in between
    (carrying an older value forward is still causal). POC is computed on every bar.
    """
    n = len(bars)
    out = pd.DataFrame({"dev_poc": np.full(n, np.nan), "dev_val": np.full(n, np.nan), "dev_vah": np.full(n, np.nan)}, index=bars.index)
    mask = np.ones(n, bool) if phase is None else (labels["phase"] == phase).to_numpy()
    td = labels["trading_date"].to_numpy()
    pos_all = np.flatnonzero(mask)
    if pos_all.size == 0:
        return out
    bin_size = tick_size * bin_ticks
    lo_all, hi_all, cl_all, vo_all = (bars[k].to_numpy(float) for k in ("low", "high", "close", "volume"))
    tdm = td[pos_all]
    bounds = np.flatnonzero(np.r_[True, tdm[1:] != tdm[:-1], True])
    poc_col, val_col, vah_col = (np.full(n, np.nan) for _ in range(3))
    for s, e in zip(bounds[:-1], bounds[1:]):
        p = pos_all[s:e]
        lo, hi, cl, vo = lo_all[p], hi_all[p], cl_all[p], np.nan_to_num(vo_all[p])
        li = level_index(lo, bin_size)
        hi_i = level_index(hi, bin_size)
        base = int(li.min())
        size = int(hi_i.max()) - base + 1
        m = len(p)
        inc = np.zeros((m, size + 1))
        rows = np.arange(m)
        if method == "uniform":
            share = vo / (hi_i - li + 1)
            np.add.at(inc, (rows, li - base), share)
            np.add.at(inc, (rows, hi_i - base + 1), -share)
            inc = np.cumsum(inc, axis=1)[:, :-1]
        else:
            pr = (lo + hi + cl) / 3.0 if method == "typical" else cl
            idx = np.clip(level_index(pr, bin_size), li, hi_i) - base
            inc = np.zeros((m, size))
            np.add.at(inc, (rows, idx), vo)
        cum = np.cumsum(inc, axis=0)
        levels = (base + np.arange(size)) * bin_size
        last_val = last_vah = np.nan
        next_va_row = 0
        for r in range(m):
            v = cum[r]
            if v.sum() <= 0:
                continue
            poc_col[p[r]] = levels[poc_index(v)]
            if r >= next_va_row:
                _, a, b_ = value_area(v, va_pct, va_method)
                last_val, last_vah = levels[a], levels[b_]
                next_va_row = r + va_every
            val_col[p[r]] = last_val
            vah_col[p[r]] = last_vah
    out["dev_poc"], out["dev_val"], out["dev_vah"] = poc_col, val_col, vah_col
    return out


def tpo_profile(
    bars: pd.DataFrame,
    minute_of_day: np.ndarray,
    *,
    tick_size: float,
    start_minute: int,
    period_minutes: int = 30,
    bin_ticks: int = 1,
) -> dict[str, object]:
    """TPO profile of ONE session's bars.

    Each ``period_minutes`` period marks every level between its low and high once.
    Returns levels, TPO counts and Dalton-style descriptors:

    * poc_tpo / val_tpo / vah_tpo: 70% of TPOs around the TPO POC
    * single_prints: levels touched by exactly one period, inside the session range
    * poor_high / poor_low: the extreme level was touched by >= 2 periods (no excess)
    * excess_high / excess_low: number of consecutive single-print levels at the extreme
    """
    bin_size = tick_size * bin_ticks
    per = (np.asarray(minute_of_day) - start_minute) // period_minutes
    lo_all = bars["low"].to_numpy(float)
    hi_all = bars["high"].to_numpy(float)
    if len(lo_all) == 0:
        return {"levels": np.array([]), "tpo": np.array([])}
    uniq = np.unique(per)
    plo = np.array([np.nanmin(lo_all[per == k]) for k in uniq])
    phi = np.array([np.nanmax(hi_all[per == k]) for k in uniq])
    li = level_index(plo, bin_size)
    hi_i = level_index(phi, bin_size)
    base = int(li.min())
    size = int(hi_i.max()) - base + 1
    diff = np.zeros(size + 1)
    np.add.at(diff, li - base, 1.0)
    np.add.at(diff, hi_i - base + 1, -1.0)
    tpo = np.round(np.cumsum(diff)[:-1]).astype(int)
    levels = (base + np.arange(size)) * bin_size
    poc, val, vah = value_area(tpo.astype(float), 0.70, "single")
    excess_high = 0
    for k in range(size - 1, -1, -1):
        if tpo[k] == 1:
            excess_high += 1
        else:
            break
    excess_low = 0
    for k in range(size):
        if tpo[k] == 1:
            excess_low += 1
        else:
            break
    return {
        "levels": levels,
        "tpo": tpo,
        "periods": int(len(uniq)),
        "poc_tpo": float(levels[poc]),
        "val_tpo": float(levels[val]),
        "vah_tpo": float(levels[vah]),
        "single_prints": int((tpo == 1).sum()),
        "poor_high": bool(tpo[-1] >= 2),
        "poor_low": bool(tpo[0] >= 2),
        "excess_high": int(excess_high),
        "excess_low": int(excess_low),
        "ib_low": float(np.min(plo[: min(2, len(plo))])),
        "ib_high": float(np.max(phi[: min(2, len(phi))])),
    }
