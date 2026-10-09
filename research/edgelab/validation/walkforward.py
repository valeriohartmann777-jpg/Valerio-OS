"""Walk-forward evaluation with in-sample parameter choice on a coarse grid."""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from ..metrics import trade_metrics

Evaluate = Callable[[dict, pd.Timestamp, pd.Timestamp], pd.DataFrame]


def _score(trades: pd.DataFrame, min_trades: int) -> float:
    if len(trades) < min_trades:
        return -np.inf
    return float(trades["R_net"].mean())


def walk_forward(
    windows: list[dict[str, pd.Timestamp]],
    param_grid: list[dict],
    evaluate: Evaluate,
    *,
    min_trades: int = 30,
    neighbors: Callable[[int], list[int]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """For each window pick parameters on TRAIN, then trade them on TEST.

    With ``neighbors`` (index -> indices of adjacent grid cells) the choice maximises the
    mean score of the cell and its neighbours, which prefers plateaus over spikes.
    Returns (per-window summary, concatenated out-of-sample trades).
    """
    rows, oos = [], []
    for w, win in enumerate(windows):
        is_trades = [evaluate(p, win["train_start"], win["train_end"]) for p in param_grid]
        scores = np.array([_score(t, min_trades) for t in is_trades])
        if neighbors is not None:
            smooth = np.array([np.mean([scores[j] for j in [i, *neighbors(i)] if np.isfinite(scores[j])] or [-np.inf]) for i in range(len(scores))])
        else:
            smooth = scores
        if not np.isfinite(smooth).any():
            rows.append({"window": w, **win, "chosen": None, "is_expectancy": np.nan, "oos_expectancy": np.nan, "oos_trades": 0})
            continue
        best = int(np.nanargmax(smooth))
        te = evaluate(param_grid[best], win["test_start"], win["test_end"])
        mi = trade_metrics(is_trades[best])
        mo = trade_metrics(te) if len(te) else {"trades": 0}
        rows.append(
            {
                "window": w,
                **win,
                "chosen": param_grid[best],
                "is_trades": mi.get("trades", 0),
                "is_expectancy": mi.get("expectancy_R", np.nan),
                "is_pf": mi.get("profit_factor", np.nan),
                "oos_trades": mo.get("trades", 0),
                "oos_expectancy": mo.get("expectancy_R", np.nan),
                "oos_pf": mo.get("profit_factor", np.nan),
                "oos_max_dd_R": mo.get("max_drawdown_R", np.nan),
            }
        )
        if len(te):
            t = te.copy()
            t["wf_window"] = w
            oos.append(t)
    summary = pd.DataFrame(rows)
    if len(summary):
        summary["oos_is_expectancy_ratio"] = summary["oos_expectancy"] / summary["is_expectancy"]
        summary["oos_is_pf_ratio"] = summary["oos_pf"] / summary["is_pf"]
    return summary, (pd.concat(oos, ignore_index=True) if oos else pd.DataFrame())
