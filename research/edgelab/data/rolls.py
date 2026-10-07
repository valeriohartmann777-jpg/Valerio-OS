"""Futures roll calendars and roll detection.

Roll gaps in an unadjusted front-month series are artefacts, not market events.
Every level that is carried from one session to the next (prior-day high, prior
value area, prior week) is wrong on the first session after an unadjusted roll,
so research code must either use adjusted data or exclude those sessions.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd


def third_friday(year: int, month: int) -> dt.date:
    """Third Friday of a month (CME equity-index expiry day)."""
    first = dt.date(year, month, 1)
    offset = (4 - first.weekday()) % 7  # Friday == 4
    return first + dt.timedelta(days=offset + 14)


def equity_index_roll_dates(
    start: dt.date, end: dt.date, months: tuple[int, ...] = (3, 6, 9, 12), roll_offset_days: int = 8
) -> list[dt.date]:
    """Conventional roll dates (expiry minus ``roll_offset_days``) between ``start`` and ``end``."""
    out: list[dt.date] = []
    for y in range(start.year - 1, end.year + 2):
        for m in months:
            r = third_friday(y, m) - dt.timedelta(days=roll_offset_days)
            if start <= r <= end:
                out.append(r)
    return sorted(out)


def contract_changes(bars: pd.DataFrame, contract_col: str = "contract") -> pd.DataFrame:
    """Rows where the contract symbol changes, with the price gap across the change."""
    if contract_col not in bars.columns:
        return pd.DataFrame(columns=["ts", "from_contract", "to_contract", "prev_close", "new_open", "gap_points"])
    c = bars[contract_col].astype(str).to_numpy()
    chg = np.flatnonzero(c[1:] != c[:-1]) + 1
    rows = []
    for i in chg:
        rows.append(
            {
                "ts": bars.index[i],
                "from_contract": c[i - 1],
                "to_contract": c[i],
                "prev_close": float(bars["close"].iloc[i - 1]),
                "new_open": float(bars["open"].iloc[i]),
                "gap_points": float(bars["open"].iloc[i] - bars["close"].iloc[i - 1]),
            }
        )
    return pd.DataFrame(rows)


def roll_exclusion_dates(roll_dates: list[dt.date], trading_dates: pd.DatetimeIndex, sessions_after: int = 1) -> set:
    """Trading dates to exclude after each roll (default: the first session on the new contract)."""
    td = pd.DatetimeIndex(sorted(set(trading_dates)))
    excl: set = set()
    for r in roll_dates:
        pos = td.searchsorted(pd.Timestamp(r))
        for k in range(pos, min(pos + sessions_after + 1, len(td))):
            excl.add(td[k])
    return excl
