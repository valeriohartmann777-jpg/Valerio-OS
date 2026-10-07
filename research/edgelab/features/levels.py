"""Reference levels: prior session, overnight, opening range, prior week, equal highs/lows.

Two semantics, both causal:

* ``previous``: levels of the most recent session whose trading date is strictly
  earlier than the bar's trading date (classic PDH/PDL, prior value).
* ``current``: levels of a phase of the bar's own trading date (e.g. the overnight
  range during today's RTH). Masked to NaN until the phase is complete
  (``available_at`` <= bar close), so the developing overnight high is never
  mistaken for the final one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import SessionTemplate, minutes_of
from ..resample import session_bars, weekly_bars
from ..timing import as_ns, decision_times


def _map_by_date(
    index: pd.DatetimeIndex,
    bar_dates: pd.Series,
    base_minutes: int,
    table: pd.DataFrame,
    columns: list[str],
    which: str,
    prefix: str,
) -> pd.DataFrame:
    """Join per-date rows onto bars (previous date or current date) and mask by availability."""
    tbl = table.sort_index()
    dates = pd.DatetimeIndex(tbl.index)
    bd = pd.DatetimeIndex(bar_dates.to_numpy())
    if which == "previous":
        k = dates.searchsorted(bd, side="left") - 1
    elif which == "current":
        k = dates.searchsorted(bd, side="left")
        k = np.where((k < len(dates)) & (dates[np.clip(k, 0, len(dates) - 1)] == bd), k, -1)
    else:
        raise ValueError(which)
    ok = k >= 0
    kk = np.clip(k, 0, max(len(tbl) - 1, 0))
    out = pd.DataFrame(index=index)
    if len(tbl) == 0:
        for c in columns:
            out[prefix + c] = np.nan
        return out
    avail = as_ns(pd.DatetimeIndex(tbl["available_at"])).to_numpy()[kk]
    known = ok & (avail <= decision_times(index, base_minutes).to_numpy())
    for c in columns:
        vals = tbl[c].to_numpy()[kk]
        if np.issubdtype(np.asarray(vals).dtype, np.number):
            out[prefix + c] = np.where(known, vals, np.nan)
        else:
            out[prefix + c] = pd.Series(vals, index=index).where(known)
    return out


def session_levels(
    bars: pd.DataFrame,
    labels: pd.DataFrame,
    base_minutes: int,
    template: SessionTemplate,
    *,
    phase: str | None = "RTH",
    which: str = "previous",
    lag: int = 0,
    columns: tuple[str, ...] = ("open", "high", "low", "close"),
    prefix: str | None = None,
    table: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Per-bar session levels (``previous`` or ``current`` trading date, optional extra lag).

    Pass ``table`` (e.g. from :func:`edgelab.features.profile.session_profiles`) to map
    any per-date summary such as POC/VAH/VAL with identical semantics.
    """
    tbl = session_bars(bars, labels, template, phase) if table is None else table
    cols = list(columns)
    if lag:
        shifted = tbl[["available_at"]].copy()
        for c in cols:
            shifted[c] = tbl[c].shift(lag)
        tbl = shifted
    if prefix is None:
        tag = (phase or "full").lower()
        prefix = f"{'pd' if which == 'previous' else 'cd'}{lag or ''}_{tag}_"
    return _map_by_date(bars.index, labels["trading_date"], base_minutes, tbl, cols, which, prefix)


def developing_extremes(bars: pd.DataFrame, labels: pd.DataFrame, phase: str | None = "RTH") -> pd.DataFrame:
    """Session high/low so far (including the current bar) and as of the previous bar."""
    key = labels["trading_date"].astype("int64") if phase is None else labels["trading_date"].where(labels["phase"] == phase)
    mask = key.notna().to_numpy()
    h = bars["high"].where(mask)
    l = bars["low"].where(mask)
    g = key.to_numpy()
    hi = h.groupby(g).cummax()
    lo = l.groupby(g).cummin()
    out = pd.DataFrame(index=bars.index)
    out["dev_high"] = hi.reindex(bars.index)
    out["dev_low"] = lo.reindex(bars.index)
    out["dev_high_prev"] = out["dev_high"].groupby(g).shift(1)
    out["dev_low_prev"] = out["dev_low"].groupby(g).shift(1)
    out.loc[~mask, :] = np.nan
    return out


def opening_range(
    bars: pd.DataFrame,
    labels: pd.DataFrame,
    template: SessionTemplate,
    base_minutes: int,
    minutes: int | None = None,
) -> pd.DataFrame:
    """Opening range of the first ``minutes`` of RTH (default: initial balance, 60 min).

    The range becomes available at RTH start + ``minutes`` (scheduled end), and is
    attached only to bars whose close is at or after that time.
    """
    minutes = minutes or template.initial_balance_minutes
    start = minutes_of(template.rth_start)
    mod = labels["minute_of_day"].to_numpy()
    in_or = (mod >= start) & (mod < start + minutes)
    b = bars[in_or]
    td = labels.loc[in_or, "trading_date"]
    if b.empty:
        return pd.DataFrame(index=bars.index, columns=["or_high", "or_low", "or_mid"], dtype=float)
    g = b.groupby(td.to_numpy())
    tbl = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "n": g.size()})
    tbl.index = pd.DatetimeIndex(tbl.index)
    end_wall = tbl.index + pd.Timedelta(minutes=start + minutes)
    tbl["available_at"] = as_ns(end_wall.tz_localize(template.timezone))
    out = _map_by_date(bars.index, labels["trading_date"], base_minutes, tbl, ["high", "low"], "current", "or_")
    out["or_mid"] = (out["or_high"] + out["or_low"]) / 2.0
    return out


def prior_week_levels(bars: pd.DataFrame, labels: pd.DataFrame, base_minutes: int, template: SessionTemplate, phase: str | None = None) -> pd.DataFrame:
    """High/low/close of the previous completed Monday-Friday week."""
    sb = session_bars(bars, labels, template, phase)
    wk = weekly_bars(sb)
    out = pd.DataFrame(index=bars.index)
    if wk.empty:
        for c in ("pw_high", "pw_low", "pw_close"):
            out[c] = np.nan
        return out
    bar_week = pd.DatetimeIndex(labels["trading_date"].to_numpy()).to_period("W-FRI")
    wk_idx = wk.index
    pos = wk_idx.searchsorted(bar_week, side="left") - 1
    ok = pos >= 0
    pp = np.clip(pos, 0, len(wk) - 1)
    avail = as_ns(pd.DatetimeIndex(wk["available_at"])).to_numpy()[pp]
    known = ok & (avail <= decision_times(bars.index, base_minutes).to_numpy())
    for src, dst in (("high", "pw_high"), ("low", "pw_low"), ("close", "pw_close")):
        out[dst] = np.where(known, wk[src].to_numpy()[pp], np.nan)
    return out


def equal_levels(
    bars: pd.DataFrame,
    pivots: pd.DataFrame,
    atr_series: pd.Series,
    *,
    kind: str = "H",
    tol_atr: float = 0.10,
    min_sep_bars: int = 5,
    max_sep_bars: int = 300,
    min_reaction_atr: float = 0.50,
) -> pd.DataFrame:
    """Equal highs (kind H) or equal lows (kind L), defined mathematically.

    Two confirmed pivots p1 < p2 of the same kind form an equal level when
      |price2 - price1| <= tol_atr * ATR(confirm of p2),
      min_sep_bars <= p2 - p1 <= max_sep_bars,
      price retraced >= min_reaction_atr * ATR between them, and
      no trade exceeded the pair's outer price between them (the first pivot still rests).
    The level (max of the two highs / min of the two lows) is known at p2's confirmation.
    """
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    a = atr_series.to_numpy(float)
    p = pivots[pivots["kind"] == kind].sort_values("pivot_pos").reset_index(drop=True)
    rows = []
    for j in range(1, len(p)):
        p2 = p.iloc[j]
        atr2 = a[int(p2.confirm_pos)]
        if not np.isfinite(atr2) or atr2 <= 0:
            continue
        for i in range(j - 1, -1, -1):
            p1 = p.iloc[i]
            sep = int(p2.pivot_pos - p1.pivot_pos)
            if sep > max_sep_bars:
                break
            if sep < min_sep_bars:
                continue
            if abs(p2.price - p1.price) > tol_atr * atr2:
                continue
            s, e = int(p1.pivot_pos) + 1, int(p2.pivot_pos)
            if e <= s:
                continue
            if kind == "H":
                outer = max(p1.price, p2.price)
                if h[s:e].max() > outer:
                    continue
                if outer - l[s:e].min() < min_reaction_atr * atr2:
                    continue
            else:
                outer = min(p1.price, p2.price)
                if l[s:e].min() < outer:
                    continue
                if h[s:e].max() - outer < min_reaction_atr * atr2:
                    continue
            rows.append(
                {
                    "kind": kind,
                    "level": outer,
                    "p1_pos": int(p1.pivot_pos),
                    "p2_pos": int(p2.pivot_pos),
                    "confirm_pos": int(p2.confirm_pos),
                    "confirm_ts": bars.index[int(p2.confirm_pos)],
                    "diff_atr": abs(p2.price - p1.price) / atr2,
                    "sep_bars": sep,
                }
            )
            break
    return pd.DataFrame(rows, columns=["kind", "level", "p1_pos", "p2_pos", "confirm_pos", "confirm_ts", "diff_atr", "sep_bars"])


def round_number_levels(price: float, step: float, n: int = 3) -> np.ndarray:
    """Round-number grid around a price (control levels for 'does this level matter' tests)."""
    base = np.floor(price / step) * step
    return base + step * np.arange(-n, n + 1)
