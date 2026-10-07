"""Charts for manual trade audits, equity curves and parameter heatmaps.

Audit samples are drawn at random with a fixed seed (20 winners, 20 losers, 20 of
any outcome). Cherry-picking examples defeats the purpose of the audit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def audit_sample(trades: pd.DataFrame, n: int = 20, seed: int = 0, r_col: str = "R_net") -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)

    def pick(df: pd.DataFrame) -> pd.DataFrame:
        if len(df) <= n:
            return df
        return df.iloc[np.sort(rng.choice(len(df), n, replace=False))]

    return {
        "winners": pick(trades[trades[r_col] > 0]),
        "losers": pick(trades[trades[r_col] <= 0]),
        "random": pick(trades),
    }


def plot_trade(
    bars: pd.DataFrame,
    trade: pd.Series,
    path: str | Path,
    *,
    before: int = 60,
    after: int = 30,
    overlays: Mapping[str, pd.Series] | None = None,
    hlines: Mapping[str, float] | None = None,
    title: str = "",
) -> None:
    """Candles around one trade with entry/exit/stop/target and optional levels."""
    i0 = bars.index.get_indexer([trade["entry_ts"]], method="nearest")[0]
    i1 = bars.index.get_indexer([trade["exit_ts"]], method="nearest")[0]
    s, e = max(0, i0 - before), min(len(bars), i1 + after + 1)
    b = bars.iloc[s:e]
    x = np.arange(len(b))
    fig, ax = plt.subplots(figsize=(12, 5.5))
    up = b["close"] >= b["open"]
    ax.vlines(x, b["low"], b["high"], color="#555", linewidth=0.6)
    ax.bar(x[up], (b["close"] - b["open"])[up], bottom=b["open"][up], color="#2e7d32", width=0.7)
    ax.bar(x[~up], (b["close"] - b["open"])[~up], bottom=b["open"][~up], color="#c62828", width=0.7)
    for name, ser in (overlays or {}).items():
        ax.plot(x, ser.reindex(b.index).to_numpy(), linewidth=1.0, label=name)
    for name, lvl in (hlines or {}).items():
        if np.isfinite(lvl):
            ax.axhline(lvl, linestyle=":", linewidth=0.9, label=f"{name} {lvl:g}")
    xe, xx = i0 - s, i1 - s
    ax.scatter([xe], [trade["entry_price"]], marker="^" if trade["direction"] > 0 else "v", s=90, color="blue", zorder=5, label="entry")
    ax.scatter([xx], [trade["exit_price"]], marker="x", s=90, color="black", zorder=5, label=f"exit ({trade['exit_reason']})")
    ax.hlines(trade["stop_price"], xe, xx, colors="red", linestyles="--", linewidth=1.0, label="stop")
    if np.isfinite(trade.get("target_price", np.nan)):
        ax.hlines(trade["target_price"], xe, xx, colors="green", linestyles="--", linewidth=1.0, label="target")
    ticks = np.linspace(0, len(b) - 1, min(8, len(b))).astype(int)
    ax.set_xticks(ticks)
    ax.set_xticklabels([b.index[t].strftime("%m-%d %H:%M") for t in ticks], rotation=0, fontsize=8)
    ax.set_title(title or f"{trade.get('signal_id', '')}  R={trade['R_net']:.2f}")
    ax.legend(fontsize=7, loc="best")
    fig.tight_layout()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_equity(trades: pd.DataFrame, path: str | Path, title: str = "", r_col: str = "R_net") -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    if len(trades):
        ax.plot(pd.to_datetime(trades["exit_ts"]), trades[r_col].cumsum(), linewidth=1.2)
    ax.set_ylabel("cumulative R")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_heatmap(table: pd.DataFrame, x: str, y: str, value: str, path: str | Path, title: str = "") -> None:
    pv = table.pivot_table(index=y, columns=x, values=value)
    fig, ax = plt.subplots(figsize=(7, 5))
    im = ax.imshow(pv.to_numpy(), cmap="RdYlGn", aspect="auto", origin="lower")
    ax.set_xticks(range(len(pv.columns)), [f"{c:g}" if isinstance(c, (int, float)) else str(c) for c in pv.columns])
    ax.set_yticks(range(len(pv.index)), [f"{c:g}" if isinstance(c, (int, float)) else str(c) for c in pv.index])
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    for (i, j), v in np.ndenumerate(pv.to_numpy()):
        if np.isfinite(v):
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax)
    ax.set_title(title or value)
    fig.tight_layout()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)
