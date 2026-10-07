"""Swing (pivot) detection with explicit confirmation delay.

A fractal pivot high at bar ``j`` needs ``right`` later bars, so it is only KNOWN
at the close of bar ``j + right`` (``confirm_pos``). Every consumer must key on
``confirm_pos``; the per-bar state helpers below already do.

Definition (ties resolved deterministically):
    pivot high at j  <=>  high[j] >  max(high[j-left : j])
                     and  high[j] >= max(high[j+1 : j+right+1])
    pivot low  at j  <=>  low[j]  <  min(low[j-left : j])
                     and  low[j]  <= min(low[j+1 : j+right+1])
On a flat top the FIRST bar of the plateau is the pivot.

The ATR zigzag is an alternative, bar-count-free swing definition: a swing high
is confirmed the moment price has fallen ``threshold`` points below the running
high of the current up-leg.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

PIVOT_COLUMNS = ["kind", "pivot_pos", "pivot_ts", "price", "confirm_pos", "confirm_ts"]


def _prev_extreme(x: np.ndarray, w: int, fn) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan)
    if w <= 0:
        return out
    if n > w:
        out[w:] = fn(sliding_window_view(x, w)[: n - w], axis=1)
    return out


def _next_extreme(x: np.ndarray, w: int, fn) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan)
    if w <= 0:
        return out
    if n > w:
        out[: n - w] = fn(sliding_window_view(x, w)[1:], axis=1)
    return out


def find_pivots(bars: pd.DataFrame, left: int, right: int) -> pd.DataFrame:
    """Fractal pivots with confirmation positions; sorted by ``confirm_pos`` then kind."""
    if left < 1 or right < 0:
        raise ValueError("left >= 1 and right >= 0 required")
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    n = len(h)
    idx = bars.index
    ph = _prev_extreme(h, left, np.max)
    pl = _prev_extreme(l, left, np.min)
    if right > 0:
        nh = _next_extreme(h, right, np.max)
        nl = _next_extreme(l, right, np.min)
        is_h = (h > ph) & (h >= nh)
        is_l = (l < pl) & (l <= nl)
    else:
        is_h = h > ph
        is_l = l < pl
    rows = []
    for kind, mask, price in (("H", is_h, h), ("L", is_l, l)):
        pos = np.flatnonzero(mask)
        conf = pos + right
        keep = conf < n
        pos, conf = pos[keep], conf[keep]
        rows.append(
            pd.DataFrame(
                {
                    "kind": kind,
                    "pivot_pos": pos,
                    "pivot_ts": idx[pos],
                    "price": price[pos],
                    "confirm_pos": conf,
                    "confirm_ts": idx[conf],
                }
            )
        )
    out = pd.concat(rows, ignore_index=True)
    return out.sort_values(["confirm_pos", "kind", "pivot_pos"], kind="stable").reset_index(drop=True)


def zigzag_pivots(bars: pd.DataFrame, threshold: np.ndarray | float) -> pd.DataFrame:
    """Causal ATR zigzag. ``threshold`` (points, scalar or per bar) sets the reversal size.

    A swing is confirmed only by a bar AFTER the one that set the extreme, because the
    order of high and low inside a single bar is unknown.
    """
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    n = len(h)
    thr = np.broadcast_to(np.asarray(threshold, float), (n,))
    idx = bars.index
    rows: list[tuple] = []
    direction = 0
    ext_hi, ext_hi_pos = -np.inf, -1
    ext_lo, ext_lo_pos = np.inf, -1
    for i in range(n):
        if not (np.isfinite(thr[i]) and np.isfinite(h[i]) and np.isfinite(l[i])):
            continue
        # 1) extend the extreme(s) of the current leg
        if direction >= 0 and h[i] > ext_hi:
            ext_hi, ext_hi_pos = h[i], i
        if direction <= 0 and l[i] < ext_lo:
            ext_lo, ext_lo_pos = l[i], i
        # 2) a reversal of `thr` points from an extreme set on an EARLIER bar confirms it at
        #    bar i. A bar that sets the extreme cannot also confirm it: its intrabar order
        #    is unknown, so a wide bar never confirms its own swing.
        if direction >= 0 and 0 <= ext_hi_pos < i and l[i] <= ext_hi - thr[i]:
            rows.append(("H", ext_hi_pos, idx[ext_hi_pos], ext_hi, i, idx[i]))
            direction = -1
            ext_lo, ext_lo_pos = l[i], i
            ext_hi, ext_hi_pos = -np.inf, -1
        elif direction <= 0 and 0 <= ext_lo_pos < i and h[i] >= ext_lo + thr[i]:
            rows.append(("L", ext_lo_pos, idx[ext_lo_pos], ext_lo, i, idx[i]))
            direction = 1
            ext_hi, ext_hi_pos = h[i], i
            ext_lo, ext_lo_pos = np.inf, -1
    return pd.DataFrame(rows, columns=PIVOT_COLUMNS)


def swing_state(n: int, pivots: pd.DataFrame, index: pd.Index | None = None) -> pd.DataFrame:
    """Per-bar view of the confirmed swing sequence, as known at each bar's close.

    Columns for kind H and L: ``last_{k}`` (price), ``last_{k}_pos`` (pivot bar),
    ``prev_{k}`` (the one before) and ``{k}_label`` (HH/LH for highs, HL/LL for lows,
    comparing the latest two confirmed swings).
    """
    out = {}
    for kind, up, down in (("H", "HH", "LH"), ("L", "HL", "LL")):
        p = pivots[pivots["kind"] == kind].sort_values(["confirm_pos", "pivot_pos"], kind="stable")
        p = p.drop_duplicates("confirm_pos", keep="last")
        last = np.full(n, np.nan)
        last_pos = np.full(n, np.nan)
        prev = np.full(n, np.nan)
        cp = p["confirm_pos"].to_numpy()
        last[cp] = p["price"].to_numpy()
        last_pos[cp] = p["pivot_pos"].to_numpy()
        prev[cp] = p["price"].shift(1).to_numpy()
        last = pd.Series(last).ffill().to_numpy()
        last_pos = pd.Series(last_pos).ffill().to_numpy()
        prev = pd.Series(prev).ffill().to_numpy()
        k = kind.lower()
        out[f"last_{k}"] = last
        out[f"last_{k}_pos"] = last_pos
        out[f"prev_{k}"] = prev
        lab = np.where(np.isnan(prev) | np.isnan(last), "", np.where(last > prev, up, np.where(last < prev, down, "EQ")))
        out[f"{k}_label"] = lab
    return pd.DataFrame(out, index=index if index is not None else pd.RangeIndex(n))
