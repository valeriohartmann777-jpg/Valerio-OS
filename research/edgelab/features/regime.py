"""Causal regime classifiers with self-normalising thresholds.

Regimes are terciles of a trailing distribution (e.g. the last 252 sessions), so
there is no threshold to tune: "high volatility" means the current value is in the
top third of its own recent history. Daily regimes are computed from COMPLETED
sessions and become available for the next session only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .volatility import atr, efficiency_ratio


def tercile(series: pd.Series, window: int = 252, min_periods: int = 60) -> pd.Series:
    """0 / 1 / 2 = low / mid / high tercile of the current value within its trailing window."""
    pct = series.rolling(window, min_periods=min(min_periods, window)).rank(pct=True)
    out = pd.Series(np.where(pct.isna(), np.nan, np.where(pct > 2 / 3, 2, np.where(pct > 1 / 3, 1, 0))), index=series.index)
    return out


def daily_regimes(sessions: pd.DataFrame, atr_n: int = 14, er_n: int = 10, window: int = 252) -> pd.DataFrame:
    """Per-session regime labels from daily bars (one row per completed session).

    vol_regime    tercile of daily ATR(atr_n)
    trend_regime  tercile of the er_n-day efficiency ratio of closes
    Row d describes sessions up to and including d (available after d closes).
    """
    d = sessions[["open", "high", "low", "close", "available_at"]].copy()
    d["atr_d"] = atr(d, atr_n)
    d["er_d"] = efficiency_ratio(d["close"], er_n)
    d["vol_regime"] = tercile(d["atr_d"], window)
    d["trend_regime"] = tercile(d["er_d"], window)
    return d


def intraday_vwap_slope(vwap: pd.Series, atr_series: pd.Series, n: int = 12) -> pd.Series:
    """Change of the session VWAP over ``n`` bars, in ATR units (sign = drift direction)."""
    return (vwap - vwap.shift(n)) / atr_series
