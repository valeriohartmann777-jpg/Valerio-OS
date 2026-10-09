"""Coarse grids, plateau detection and +/-10% / +/-20% parameter perturbation."""

from __future__ import annotations

import itertools
from typing import Callable

import numpy as np
import pandas as pd


def grid(space: dict[str, list]) -> list[dict]:
    """Cartesian product of a coarse parameter space."""
    keys = list(space)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(space[k] for k in keys))]


def grid_neighbors(space: dict[str, list]) -> Callable[[int], list[int]]:
    """Index -> indices of cells one grid step away on any single axis."""
    keys = list(space)
    shape = [len(space[k]) for k in keys]
    coords = list(itertools.product(*(range(s) for s in shape)))
    lookup = {c: i for i, c in enumerate(coords)}

    def nb(i: int) -> list[int]:
        c = coords[i]
        out = []
        for ax in range(len(shape)):
            for step in (-1, 1):
                cc = list(c)
                cc[ax] += step
                if 0 <= cc[ax] < shape[ax]:
                    out.append(lookup[tuple(cc)])
        return out

    return nb


def plateau_table(results: pd.DataFrame, space: dict[str, list], metric: str) -> pd.DataFrame:
    """Add neighbour mean and an isolation score (cell minus neighbour mean) per grid cell.

    ``results`` must hold one row per grid cell in :func:`grid` order. A best cell whose
    neighbours are much worse (high isolation) is a warning sign of overfitting.
    """
    nb = grid_neighbors(space)
    vals = results[metric].to_numpy(float)
    nmean = np.array([np.nanmean(vals[nb(i)]) if nb(i) else np.nan for i in range(len(vals))])
    out = results.copy()
    out[f"{metric}_neighbor_mean"] = nmean
    out[f"{metric}_isolation"] = vals - nmean
    return out


def perturbations(base: dict, keys: list[str], pcts: tuple[float, ...] = (-0.2, -0.1, 0.1, 0.2), integer_keys: tuple[str, ...] = ()) -> list[tuple[str, float, dict]]:
    """One-at-a-time multiplicative perturbations of numeric parameters."""
    out = []
    for k in keys:
        for p in pcts:
            v = base[k] * (1 + p)
            if k in integer_keys:
                v = int(round(v))
                if v == base[k]:
                    v = base[k] + (1 if p > 0 else -1)
            out.append((k, p, {**base, k: v}))
    return out


def perturbation_table(base: dict, keys: list[str], evaluate: Callable[[dict], dict], **kw) -> pd.DataFrame:
    """Metric changes under each perturbation relative to the base parameters."""
    ref = evaluate(base)
    rows = [{"param": "BASE", "pct": 0.0, **ref}]
    for k, p, params in perturbations(base, keys, **kw):
        rows.append({"param": k, "pct": p, **evaluate(params)})
    df = pd.DataFrame(rows)
    if "expectancy_R" in df:
        df["expectancy_change_pct"] = (df["expectancy_R"] / ref.get("expectancy_R", np.nan) - 1) * 100
    return df
