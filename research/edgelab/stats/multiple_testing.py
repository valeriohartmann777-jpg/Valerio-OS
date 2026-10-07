"""Multiple-testing control and the append-only test registry.

Every statistical test run during research is appended to
``results/test_registry.csv``. Significance is then judged with Benjamini-Hochberg
across ALL tests ever run (globally and per family), so a hypothesis that only
"works" after dozens of attempts is judged as such.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import RESULTS_DIR

REGISTRY_FIELDS = [
    "test_id", "timestamp_utc", "experiment_id", "hypothesis_id", "family", "description",
    "metric", "horizon", "n", "effect", "p_value", "data_period", "instrument", "git_commit", "notes",
]


def benjamini_hochberg(pvals: np.ndarray, q: float = 0.10) -> tuple[np.ndarray, np.ndarray]:
    """BH step-up: returns (adjusted p-values, reject mask) at FDR ``q``."""
    p = np.asarray(pvals, float)
    m = np.isfinite(p).sum()
    adj = np.full_like(p, np.nan)
    if m == 0:
        return adj, np.zeros_like(p, dtype=bool)
    idx = np.flatnonzero(np.isfinite(p))
    order = idx[np.argsort(p[idx])]
    ranked = p[order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    adj[order] = np.minimum(ranked, 1.0)
    return adj, np.nan_to_num(adj, nan=1.0) <= q


def bonferroni(pvals: np.ndarray, alpha: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    p = np.asarray(pvals, float)
    m = np.isfinite(p).sum()
    adj = np.minimum(p * max(m, 1), 1.0)
    return adj, np.nan_to_num(adj, nan=1.0) <= alpha


class TestRegistry:
    """Append-only CSV of every test performed."""

    __test__ = False  # not a pytest class

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else RESULTS_DIR / "test_registry.csv"

    def register(self, **row: object) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        existing = 0 if new else sum(1 for _ in open(self.path, encoding="utf-8")) - 1
        rec = {k: "" for k in REGISTRY_FIELDS}
        rec.update({k: v for k, v in row.items() if k in REGISTRY_FIELDS})
        rec["test_id"] = rec["test_id"] or f"T{existing + 1:05d}"
        rec["timestamp_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        with open(self.path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=REGISTRY_FIELDS)
            if new:
                w.writeheader()
            w.writerow(rec)
        return str(rec["test_id"])

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=REGISTRY_FIELDS)
        return pd.read_csv(self.path)

    def adjusted(self, q: float = 0.10) -> pd.DataFrame:
        """Registry with BH-adjusted p-values globally and within each family."""
        df = self.load()
        if df.empty:
            return df
        p = pd.to_numeric(df["p_value"], errors="coerce").to_numpy()
        df["p_bh_global"], df["sig_bh_global"] = benjamini_hochberg(p, q)
        df["p_bh_family"] = np.nan
        df["sig_bh_family"] = False
        for fam, idx in df.groupby("family").groups.items():
            adj, rej = benjamini_hochberg(p[np.asarray(list(idx))], q)
            df.loc[idx, "p_bh_family"] = adj
            df.loc[idx, "sig_bh_family"] = rej
        df["n_tests_total"] = len(df)
        return df
