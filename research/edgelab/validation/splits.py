"""Chronological DEV / VAL / TEST splits and the guard that keeps TEST untouched.

* The split is computed once per instrument and dataset and written to
  ``results/split_ledger.json``. Recomputing it later with different dates raises.
* The TEST period may be evaluated only by a candidate whose status is FROZEN, and
  only once per candidate. ``results/test_set_ledger.json`` records each use; a second
  request for the same candidate raises, because a consumed test set is no longer
  out-of-sample.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import RESULTS_DIR

PERIODS = ("DEV", "VAL", "TEST")


def chronological_split(
    trading_dates: pd.DatetimeIndex | list,
    fractions: dict[str, float] | None = None,
    embargo_days: int = 5,
) -> dict[str, tuple[str, str]]:
    """Split sorted unique trading dates into DEV/VAL/TEST, never shuffling.

    ``embargo_days`` trading dates are dropped at the start of VAL and of TEST so that
    multi-day features or holding periods cannot straddle a boundary.
    """
    fr = fractions or {"DEV": 0.5, "VAL": 0.25, "TEST": 0.25}
    if abs(sum(fr.values()) - 1.0) > 1e-9:
        raise ValueError("fractions must sum to 1")
    d = pd.DatetimeIndex(sorted(set(pd.DatetimeIndex(trading_dates))))
    n = len(d)
    c1 = int(round(n * fr["DEV"]))
    c2 = int(round(n * (fr["DEV"] + fr["VAL"])))
    spans = {"DEV": (0, c1), "VAL": (c1 + embargo_days, c2), "TEST": (c2 + embargo_days, n)}
    out = {}
    for k, (a, b) in spans.items():
        if b - a <= 0:
            raise ValueError(f"period {k} is empty; dataset too short for this split")
        out[k] = (str(d[a].date()), str(d[b - 1].date()))
    return out


def period_mask(trading_date: pd.Series | np.ndarray, split: dict[str, tuple[str, str]], period: str) -> np.ndarray:
    a, b = (pd.Timestamp(x) for x in split[period])
    td = pd.DatetimeIndex(np.asarray(trading_date))
    return np.asarray((td >= a) & (td <= b))


class SplitLedger:
    """Freezes the split for (instrument, dataset) the first time it is registered."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else RESULTS_DIR / "split_ledger.json"

    def _load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def register(self, instrument: str, dataset_sha: str, split: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
        led = self._load()
        key = f"{instrument}:{dataset_sha[:16]}"
        if key in led:
            frozen = {k: tuple(v) for k, v in led[key]["split"].items()}
            if frozen != {k: tuple(v) for k, v in split.items()}:
                raise RuntimeError(f"split for {key} already frozen as {frozen}; refusing to change it")
            return frozen
        led[key] = {"split": split, "registered_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(led, indent=2))
        return split

    def get(self, instrument: str, dataset_sha: str) -> dict[str, tuple[str, str]] | None:
        rec = self._load().get(f"{instrument}:{dataset_sha[:16]}")
        return {k: tuple(v) for k, v in rec["split"].items()} if rec else None


class TestSetGuard:
    """One-shot access to the TEST period per frozen candidate."""

    __test__ = False

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else RESULTS_DIR / "test_set_ledger.json"

    def _load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def consumed(self, candidate: str) -> bool:
        return candidate in self._load()

    def request(self, *, candidate: str, experiment_id: str, status: str, params_hash: str, instrument: str) -> None:
        if status != "FROZEN":
            raise PermissionError(f"{experiment_id}: TEST access requires status FROZEN (got {status})")
        led = self._load()
        if candidate in led:
            prev = led[candidate]
            raise PermissionError(
                f"TEST period already consumed by {candidate} ({prev['experiment_id']}, params {prev['params_hash']}) "
                "- results on it are no longer out-of-sample"
            )
        led[candidate] = {
            "experiment_id": experiment_id,
            "params_hash": params_hash,
            "instrument": instrument,
            "consumed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(led, indent=2))


def walk_forward_windows(
    trading_dates: pd.DatetimeIndex | list,
    train_months: int = 12,
    test_months: int = 3,
    step_months: int | None = None,
    anchored: bool = False,
) -> list[dict[str, pd.Timestamp]]:
    """Rolling (or anchored) calendar-month windows over the available trading dates."""
    d = pd.DatetimeIndex(sorted(set(pd.DatetimeIndex(trading_dates))))
    if len(d) == 0:
        return []
    step = step_months or test_months
    start = d[0].to_period("M").to_timestamp()
    last = d[-1]
    out = []
    k = 0
    while True:
        tr_start = start if anchored else start + pd.DateOffset(months=k * step)
        tr_end = start + pd.DateOffset(months=k * step + train_months) - pd.Timedelta(days=1)
        te_start = tr_end + pd.Timedelta(days=1)
        te_end = te_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1)
        if te_start > last:
            break
        out.append({"train_start": tr_start, "train_end": tr_end, "test_start": te_start, "test_end": min(te_end, last)})
        k += 1
    return out
