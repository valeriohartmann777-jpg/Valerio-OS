"""Market-structure breaks from confirmed swings (BOS / CHoCH / MSS building block).

At bar ``i`` the reference levels are the latest swings confirmed by the close of
bar ``i-1``; a break is the first bar after confirmation that closes (``close``
mode) or trades (``wick`` mode) beyond the level. Each swing can be broken once.

``choch`` marks a break against the prevailing structure (the first up-break
after a down-break or vice versa). Whether any of this carries information is a
research question, not an assumption.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def structure_breaks(bars: pd.DataFrame, pivots: pd.DataFrame, mode: str = "close") -> pd.DataFrame:
    """List of breaks: pos, ts, direction (+1 up / -1 down), level, swing_pivot_pos, choch."""
    if mode not in ("close", "wick"):
        raise ValueError(mode)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    n = len(c)
    up_test = c if mode == "close" else h
    dn_test = c if mode == "close" else l
    # swings becoming active at confirm_pos + 1
    act = {}
    for r in pivots.itertuples(index=False):
        act.setdefault(int(r.confirm_pos) + 1, []).append((r.kind, float(r.price), int(r.pivot_pos)))
    ref_h = ref_l = np.nan
    ref_h_pos = ref_l_pos = -1
    trend = 0
    rows = []
    for i in range(n):
        for kind, price, ppos in act.get(i, ()):
            if kind == "H":
                ref_h, ref_h_pos = price, ppos
            else:
                ref_l, ref_l_pos = price, ppos
        if np.isfinite(ref_h) and up_test[i] > ref_h:
            rows.append((i, bars.index[i], 1, ref_h, ref_h_pos, trend == -1))
            trend = 1
            ref_h, ref_h_pos = np.nan, -1
        if np.isfinite(ref_l) and dn_test[i] < ref_l:
            rows.append((i, bars.index[i], -1, ref_l, ref_l_pos, trend == 1))
            trend = -1
            ref_l, ref_l_pos = np.nan, -1
    return pd.DataFrame(rows, columns=["pos", "ts", "direction", "level", "swing_pivot_pos", "choch"])


def structure_trend(n: int, breaks: pd.DataFrame) -> np.ndarray:
    """Per-bar structural trend (+1 after an up-break, -1 after a down-break, 0 before any)."""
    t = np.full(n, np.nan)
    if len(breaks):
        b = breaks.drop_duplicates("pos", keep="last")
        t[b["pos"].to_numpy()] = b["direction"].to_numpy()
    return pd.Series(t).ffill().fillna(0).to_numpy()
