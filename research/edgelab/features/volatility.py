"""Volatility and trend-efficiency measures (all trailing / causal)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(bars: pd.DataFrame) -> pd.Series:
    """max(high-low, |high-prev_close|, |low-prev_close|); first bar uses high-low."""
    h, l, c = bars["high"], bars["low"], bars["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    tr.iloc[0] = h.iloc[0] - l.iloc[0]
    return tr.rename("tr")


def atr(bars: pd.DataFrame, n: int = 14, method: str = "wilder") -> pd.Series:
    """Average true range. ``wilder`` = RMA (alpha 1/n), ``sma`` = simple mean."""
    tr = true_range(bars)
    if method == "wilder":
        out = tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    elif method == "sma":
        out = tr.rolling(n, min_periods=n).mean()
    else:
        raise ValueError(method)
    return out.rename(f"atr{n}")


def realized_vol(close: pd.Series, n: int) -> pd.Series:
    """Rolling standard deviation of log returns over ``n`` bars."""
    r = np.log(close).diff()
    return r.rolling(n, min_periods=n).std().rename(f"rv{n}")


def efficiency_ratio(close: pd.Series, n: int) -> pd.Series:
    """Kaufman efficiency ratio: |net move| / path length over ``n`` bars (0..1)."""
    net = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n, min_periods=n).sum()
    return (net / path.replace(0, np.nan)).rename(f"er{n}")


def adx(bars: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Wilder's +DI, -DI and ADX."""
    h, l = bars["high"], bars["low"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=bars.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=bars.index)
    tr = true_range(bars)
    a = 1.0 / n
    atr_ = tr.ewm(alpha=a, adjust=False, min_periods=n).mean()
    pdi = 100 * plus_dm.ewm(alpha=a, adjust=False, min_periods=n).mean() / atr_
    mdi = 100 * minus_dm.ewm(alpha=a, adjust=False, min_periods=n).mean() / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx_ = dx.ewm(alpha=a, adjust=False, min_periods=n).mean()
    return pd.DataFrame({"plus_di": pdi, "minus_di": mdi, "adx": adx_})


def trailing_percentile(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    """Percentile rank (0..1] of the current value within the trailing window (inclusive)."""
    return series.rolling(window, min_periods=min_periods or window).rank(pct=True)
