"""The single gateway for moving information across time scales.

Every derived table that summarises a span of bars (a higher-timeframe candle, a
session profile, an overnight range, an opening range) carries an ``available_at``
column: the first instant at which the summary is fully known. Lower-timeframe
bars receive such information ONLY through :func:`attach_asof`, which matches on
the bar's decision time (its close). This is what makes higher-timeframe and
prior-session leakage structurally impossible, and it is tested directly.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def as_ns(x: pd.DatetimeIndex | pd.Series) -> pd.DatetimeIndex | pd.Series:
    """Normalise datetime resolution to nanoseconds (pandas 3 infers us/s otherwise)."""
    if isinstance(x, pd.Series):
        return x.dt.as_unit("ns")
    return x.as_unit("ns")


def decision_times(index: pd.DatetimeIndex, bar_minutes: int) -> pd.DatetimeIndex:
    """Close time of each bar = the instant a signal on that bar can be computed."""
    return as_ns(index + pd.Timedelta(minutes=bar_minutes))


def attach_asof(
    index: pd.DatetimeIndex,
    bar_minutes: int,
    table: pd.DataFrame,
    columns: Iterable[str],
    prefix: str = "",
    available_col: str = "available_at",
) -> pd.DataFrame:
    """Attach the latest row of ``table`` already available at each bar's close.

    A row is visible to a bar when ``row.available_at <= bar.ts + bar_minutes``.
    Returns a frame indexed like ``index`` with the requested columns (prefixed).
    """
    cols = list(columns)
    if available_col not in table.columns:
        raise ValueError(f"table lacks {available_col!r}; every summary table must state when it is known")
    right = table[cols + [available_col]].copy()
    right[available_col] = as_ns(pd.to_datetime(right[available_col]))
    right = right.dropna(subset=[available_col]).sort_values(available_col, kind="stable")
    left = pd.DataFrame({"_decision": decision_times(index, bar_minutes)})
    left["_order"] = np.arange(len(left))
    left = left.sort_values("_decision", kind="stable")
    merged = pd.merge_asof(
        left,
        right,
        left_on="_decision",
        right_on=available_col,
        direction="backward",
        allow_exact_matches=True,
    )
    merged = merged.sort_values("_order", kind="stable")
    out = pd.DataFrame(index=index)
    for c in cols:
        out[prefix + c] = merged[c].to_numpy()
    return out


def lag_sessions(table: pd.DataFrame, k: int, columns: Iterable[str], available_col: str = "available_at") -> pd.DataFrame:
    """Shift summary columns by ``k`` rows while keeping each row's own ``available_at``.

    Used for 'two sessions ago' style features (value migration). Because the shifted
    values are older than the row's availability time, no leakage is introduced.
    """
    cols = list(columns)
    out = table[[available_col]].copy()
    for c in cols:
        out[c] = table[c].shift(k)
    return out
