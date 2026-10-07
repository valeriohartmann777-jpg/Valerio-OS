"""Test fixtures.

SYNTHETIC DATA NOTICE: every price series in this directory is a random walk
generated for SOFTWARE TESTS ONLY. It carries no information about any market and
must never be used or reported as strategy evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgelab.config import load_instrument, load_session_template  # noqa: E402
from edgelab.sessions import label_sessions  # noqa: E402

TZ = "America/New_York"


def cme_schedule(first_trading_date: str, n_days: int, minutes: int = 1) -> pd.DatetimeIndex:
    """Bar open times of a CME equity-index week: 18:00 (prior day) to 16:59, Mon-Fri."""
    dates = pd.bdate_range(first_trading_date, periods=n_days)
    parts = []
    for d in dates:
        start = (d - pd.Timedelta(days=1)).replace(hour=18, minute=0)  # Monday opens Sunday 18:00
        end = d.replace(hour=17, minute=0)
        wall = pd.date_range(start, end, freq=f"{minutes}min", inclusive="left")
        parts.append(wall.tz_localize(TZ))
    return parts[0].append(parts[1:]) if len(parts) > 1 else parts[0]


def random_walk_bars(index: pd.DatetimeIndex, seed: int = 0, tick: float = 0.25, start: float = 5000.0, vol_ticks: float = 2.0) -> pd.DataFrame:
    """Tick-grid OHLCV random walk (synthetic, tests only)."""
    rng = np.random.default_rng(seed)
    n = len(index)
    sub = 4
    steps = np.round(rng.normal(0, vol_ticks / np.sqrt(sub), size=(n, sub))) * tick
    o = np.empty(n)
    h = np.empty(n)
    l = np.empty(n)
    c = np.empty(n)
    p = start
    for i in range(n):
        path = p + np.cumsum(steps[i])
        o[i] = p
        h[i] = max(p, path.max())
        l[i] = min(p, path.min())
        c[i] = path[-1]
        p = c[i]
    v = rng.integers(50, 500, size=n).astype(float)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=pd.DatetimeIndex(index, name="ts"))


@pytest.fixture(scope="session")
def template():
    return load_session_template("cme_equity_index")


@pytest.fixture(scope="session")
def es():
    return load_instrument("ES")


@pytest.fixture(scope="session")
def week_bars():
    idx = cme_schedule("2024-03-04", 5)
    return random_walk_bars(idx, seed=1)


@pytest.fixture(scope="session")
def week_labels(week_bars, template):
    return label_sessions(week_bars.index, template)


def bars_from_rows(rows: list[tuple], start: str = "2024-03-05 10:00", minutes: int = 1) -> pd.DataFrame:
    """Hand-built bars: rows of (open, high, low, close[, volume])."""
    idx = pd.date_range(start, periods=len(rows), freq=f"{minutes}min", tz=TZ, name="ts")
    arr = np.array([r if len(r) == 5 else (*r, 100.0) for r in rows], dtype=float)
    return pd.DataFrame(arr, columns=["open", "high", "low", "close", "volume"], index=idx)
