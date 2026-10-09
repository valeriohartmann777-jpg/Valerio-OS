"""Fair Value Gaps: detection (causal) and fill outcomes (for event studies only).

Three-candle definition, known at the CLOSE of candle 3 (position ``pos``):
    bullish FVG:  high[pos-2] < low[pos]    zone = [high[pos-2], low[pos]]
    bearish FVG:  low[pos-2]  > high[pos]   zone = [high[pos], low[pos-2]]

``fvg_outcomes`` looks FORWARD from ``pos`` and must only be used to measure what
happened after an FVG formed (event studies). The per-bar ``fvg_state`` is the
causal view a strategy may use.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def detect_fvgs(bars: pd.DataFrame, atr_series: pd.Series | None = None, min_width: float = 0.0) -> pd.DataFrame:
    """All FVGs with width >= ``min_width`` points.

    Columns: pos, ts, direction, bottom, top, width, width_atr (ATR as of candle 2),
    mid_body_atr (body of the displacement candle / ATR as of candle 1).
    """
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    o = bars["open"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    n = len(h)
    if n < 3:
        return pd.DataFrame(columns=["pos", "ts", "direction", "bottom", "top", "width", "width_atr", "mid_body_atr"])
    a = atr_series.to_numpy(float) if atr_series is not None else np.full(n, np.nan)
    pos = np.arange(2, n)
    bull = h[pos - 2] < l[pos]
    bear = l[pos - 2] > h[pos]
    rows = []
    for d, m in ((1, bull), (-1, bear)):
        p = pos[m]
        bottom = h[p - 2] if d == 1 else h[p]
        top = l[p] if d == 1 else l[p - 2]
        width = top - bottom
        keep = width >= min_width
        p, bottom, top, width = p[keep], bottom[keep], top[keep], width[keep]
        rows.append(
            pd.DataFrame(
                {
                    "pos": p,
                    "ts": bars.index[p],
                    "direction": d,
                    "bottom": bottom,
                    "top": top,
                    "width": width,
                    "width_atr": width / a[p - 1],
                    "mid_body_atr": np.abs(c[p - 1] - o[p - 1]) / a[p - 2],
                }
            )
        )
    return pd.concat(rows, ignore_index=True).sort_values(["pos", "direction"], kind="stable").reset_index(drop=True)


def fvg_outcomes(bars: pd.DataFrame, fvgs: pd.DataFrame, max_bars: int) -> pd.DataFrame:
    """Forward-looking fill statistics for each FVG within ``max_bars`` bars.

    touch_pos: first bar that trades into the zone (bull: low <= top)
    mid_pos:   first bar reaching the zone midpoint
    full_pos:  first bar trading through the far edge (bull: low <= bottom)
    *_bars:    bars from formation to the event (NaN if not reached)
    """
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    n = len(h)
    res = {k: np.full(len(fvgs), np.nan) for k in ("touch_pos", "mid_pos", "full_pos")}
    for k, r in enumerate(fvgs.itertuples(index=False)):
        s, e = int(r.pos) + 1, min(n, int(r.pos) + 1 + max_bars)
        if s >= e:
            continue
        mid = (r.top + r.bottom) / 2.0
        if r.direction == 1:
            seg = l[s:e]
            tests = (seg <= r.top, seg <= mid, seg <= r.bottom)
        else:
            seg = h[s:e]
            tests = (seg >= r.bottom, seg >= mid, seg >= r.top)
        for key, t in zip(("touch_pos", "mid_pos", "full_pos"), tests):
            if t.any():
                res[key][k] = s + int(np.argmax(t))
    out = fvgs.copy()
    for key, v in res.items():
        out[key] = v
        out[key.replace("_pos", "_bars")] = v - out["pos"].to_numpy()
    return out


def fvg_state(bars: pd.DataFrame, fvgs: pd.DataFrame, invalidate: str = "close_through", max_age: int | None = None) -> pd.DataFrame:
    """Causal per-bar view of open FVGs.

    An FVG is open from the bar after formation until it is invalidated
    (``close_through``: a close beyond the far edge; ``full_fill``: any trade through
    the far edge) or exceeds ``max_age`` bars. Reports the most recent open bullish and
    bearish gap and how many of each are open.
    """
    c = bars["close"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    n = len(c)
    cols = {k: np.full(n, np.nan) for k in ("bull_top", "bull_bottom", "bear_top", "bear_bottom")}
    n_bull = np.zeros(n, dtype=np.int32)
    n_bear = np.zeros(n, dtype=np.int32)
    by_pos: dict[int, list] = {}
    for r in fvgs.itertuples(index=False):
        by_pos.setdefault(int(r.pos), []).append((int(r.direction), float(r.bottom), float(r.top), int(r.pos)))
    open_bull: list = []
    open_bear: list = []
    for i in range(n):
        # invalidation uses bar i itself (its close is known at the close)
        if invalidate == "close_through":
            open_bull = [g for g in open_bull if not (c[i] < g[1])]
            open_bear = [g for g in open_bear if not (c[i] > g[2])]
        else:
            open_bull = [g for g in open_bull if not (l[i] <= g[1])]
            open_bear = [g for g in open_bear if not (h[i] >= g[2])]
        if max_age is not None:
            open_bull = [g for g in open_bull if i - g[3] <= max_age]
            open_bear = [g for g in open_bear if i - g[3] <= max_age]
        for g in by_pos.get(i, ()):
            (open_bull if g[0] == 1 else open_bear).append(g)
        if open_bull:
            g = open_bull[-1]
            cols["bull_bottom"][i], cols["bull_top"][i] = g[1], g[2]
        if open_bear:
            g = open_bear[-1]
            cols["bear_bottom"][i], cols["bear_top"][i] = g[1], g[2]
        n_bull[i], n_bear[i] = len(open_bull), len(open_bear)
    out = pd.DataFrame(cols, index=bars.index)
    out["n_open_bull"] = n_bull
    out["n_open_bear"] = n_bear
    return out
