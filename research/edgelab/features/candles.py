"""Single-bar and two-bar candle descriptors.

Normalisation uses the ATR of the PREVIOUS bar so a large candle does not dampen
its own ratio. Thresholds ("displacement", "strong close") are NOT defined here:
they are research parameters stated in each hypothesis.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def candle_features(bars: pd.DataFrame, atr_series: pd.Series, body_window: int = 100) -> pd.DataFrame:
    """Body, range, wick and close-location measures.

    Columns: body (signed), abs_body, range, body_atr, range_atr, clv (0 = close at low,
    1 = close at high), upper_wick, lower_wick, body_pctile (trailing rank of abs body),
    inside_bar, outside_bar, bull_engulf, bear_engulf.
    """
    o, h, l, c = (bars[k] for k in ("open", "high", "low", "close"))
    atr_prev = atr_series.shift(1)
    rng = h - l
    body = c - o
    out = pd.DataFrame(index=bars.index)
    out["body"] = body
    out["abs_body"] = body.abs()
    out["range"] = rng
    out["body_atr"] = out["abs_body"] / atr_prev
    out["range_atr"] = rng / atr_prev
    out["clv"] = np.where(rng > 0, (c - l) / rng.where(rng > 0, np.nan), 0.5)
    out["upper_wick"] = h - np.maximum(o, c)
    out["lower_wick"] = np.minimum(o, c) - l
    out["body_pctile"] = out["abs_body"].rolling(body_window, min_periods=body_window).rank(pct=True)
    ph, pl, po, pc = h.shift(1), l.shift(1), o.shift(1), c.shift(1)
    out["inside_bar"] = (h <= ph) & (l >= pl)
    out["outside_bar"] = (h > ph) & (l < pl)
    out["bull_engulf"] = (c > o) & (pc < po) & (c >= po) & (o <= pc)
    out["bear_engulf"] = (c < o) & (pc > po) & (c <= po) & (o >= pc)
    return out
