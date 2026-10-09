"""Trade-level performance metrics in R (and points where useful).

Daily statistics (Sharpe, Sortino) are computed on R summed per trading date over
EVERY trading date of the evaluated period, including days without trades.
Dropping no-trade days would inflate Sharpe.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats as sps


def max_drawdown(curve: np.ndarray) -> dict[str, float]:
    """Max peak-to-trough decline of a cumulative curve (starting from 0) and its duration in steps."""
    x = np.r_[0.0, np.asarray(curve, float)]
    peak = np.maximum.accumulate(x)
    dd = peak - x
    mdd = float(dd.max())
    under = dd > 1e-12
    longest = run = 0
    for u in under:
        run = run + 1 if u else 0
        longest = max(longest, run)
    return {"max_dd": mdd, "max_dd_duration": int(longest)}


def max_streaks(r: np.ndarray) -> tuple[int, int]:
    """Longest run of winning and of losing trades."""
    best_w = best_l = w = lo = 0
    for v in r:
        if v > 0:
            w, lo = w + 1, 0
        elif v < 0:
            w, lo = 0, lo + 1
        else:
            w = lo = 0
        best_w, best_l = max(best_w, w), max(best_l, lo)
    return best_w, best_l


def daily_r(trades: pd.DataFrame, trading_days: Iterable | None, r_col: str = "R_net", date_col: str = "trading_date") -> pd.Series:
    """R per trading date, zero-filled across all ``trading_days``."""
    s = trades.groupby(date_col)[r_col].sum() if len(trades) else pd.Series(dtype=float)
    if trading_days is not None:
        s = s.reindex(pd.DatetimeIndex(sorted(set(pd.DatetimeIndex(list(trading_days))))), fill_value=0.0)
    return s


def trade_metrics(
    trades: pd.DataFrame,
    *,
    r_col: str = "R_net",
    trading_days: Iterable | None = None,
    market_minutes: float | None = None,
) -> dict[str, float]:
    """The full metric set requested by the research protocol."""
    m: dict[str, float] = {}
    n = len(trades)
    m["trades"] = n
    if n == 0:
        return m
    r = trades[r_col].to_numpy(float)
    win, loss = r > 0, r < 0
    m["wins"], m["losses"] = int(win.sum()), int(loss.sum())
    m["win_rate"] = float(win.mean())
    m["avg_win_R"] = float(r[win].mean()) if win.any() else np.nan
    m["avg_loss_R"] = float(r[loss].mean()) if loss.any() else np.nan
    m["median_win_R"] = float(np.median(r[win])) if win.any() else np.nan
    m["median_loss_R"] = float(np.median(r[loss])) if loss.any() else np.nan
    m["expectancy_R"] = float(r.mean())
    m["median_R"] = float(np.median(r))
    gp, gl = float(r[win].sum()), float(-r[loss].sum())
    m["gross_profit_R"], m["gross_loss_R"] = gp, gl
    m["profit_factor"] = gp / gl if gl > 0 else np.inf
    m["net_R"] = float(r.sum())
    if {"R_gross", "risk_pts", "fees_pts", "slippage_pts"}.issubset(trades.columns):
        rg = trades["R_gross"].to_numpy(float)
        risk = trades["risk_pts"].to_numpy(float)
        m["expectancy_gross_R"] = float(rg.mean())
        m["commission_R"] = float((trades["fees_pts"].to_numpy(float) / risk).sum())
        m["slippage_R"] = float((trades["slippage_pts"].to_numpy(float) / risk).sum())
        eg = m["expectancy_gross_R"]
        m["cost_per_trade_R"] = float(eg - m["expectancy_R"])
        m["cost_share_of_gross_edge"] = float((eg - m["expectancy_R"]) / eg) if eg > 0 else np.nan
        m["median_risk_pts"] = float(np.median(risk))
    if "pnl_ccy" in trades.columns:
        m["net_pnl_ccy_1lot"] = float(trades["pnl_ccy"].sum())
    sd = r.std(ddof=1) if n > 1 else np.nan
    m["sharpe_per_trade"] = float(r.mean() / sd) if n > 1 and sd > 0 else np.nan
    if "trading_date" in trades.columns:
        dr = daily_r(trades, trading_days, r_col)
        dsd = dr.std(ddof=1)
        m["trading_days"] = int(len(dr))
        m["sharpe_daily_ann"] = float(dr.mean() / dsd * np.sqrt(252)) if dsd > 0 else np.nan
        downside = dr[dr < 0]
        dd_sd = np.sqrt((downside ** 2).sum() / len(dr)) if len(dr) else np.nan
        m["sortino_daily_ann"] = float(dr.mean() / dd_sd * np.sqrt(252)) if dd_sd and dd_sd > 0 else np.nan
        years = len(dr) / 252.0
        m["R_per_year"] = float(r.sum() / years) if years > 0 else np.nan
        m["trades_per_week"] = float(n / (len(dr) / 5.0)) if len(dr) else np.nan
        dmd = max_drawdown(dr.to_numpy().cumsum())
        m["max_dd_duration_days"] = dmd["max_dd_duration"]
    dd = max_drawdown(r.cumsum())
    m["max_drawdown_R"] = dd["max_dd"]
    m["max_dd_duration_trades"] = dd["max_dd_duration"]
    m["recovery_factor"] = float(r.sum() / dd["max_dd"]) if dd["max_dd"] > 0 else np.inf
    if "R_per_year" in m and dd["max_dd"] > 0:
        m["calmar_R"] = float(m["R_per_year"] / dd["max_dd"])
    mw, ml = max_streaks(r)
    m["max_consec_wins"], m["max_consec_losses"] = mw, ml
    if "duration_min" in trades.columns:
        dur = trades["duration_min"].to_numpy(float)
        m["avg_duration_min"] = float(dur.mean())
        m["median_duration_min"] = float(np.median(dur))
        if market_minutes:
            m["exposure"] = float(dur.sum() / market_minutes)
    m["skew"] = float(sps.skew(r)) if n > 2 else np.nan
    m["kurtosis"] = float(sps.kurtosis(r)) if n > 3 else np.nan
    for q in (5, 25, 75, 95):
        m[f"p{q:02d}_R"] = float(np.percentile(r, q))
    if "direction" in trades.columns:
        for side, lab in ((1, "long"), (-1, "short")):
            rs = r[trades["direction"].to_numpy() == side]
            m[f"{lab}_trades"] = int(len(rs))
            m[f"{lab}_expectancy_R"] = float(rs.mean()) if len(rs) else np.nan
            gps, gls = rs[rs > 0].sum(), -rs[rs < 0].sum()
            m[f"{lab}_profit_factor"] = float(gps / gls) if gls > 0 else (np.inf if len(rs) else np.nan)
    if "ambiguous" in trades.columns:
        m["ambiguous_share"] = float(trades["ambiguous"].mean())
    return m


def breakdown(trades: pd.DataFrame, by: str | pd.Series, r_col: str = "R_net") -> pd.DataFrame:
    """n, win rate, expectancy, profit factor and total R per group."""
    if len(trades) == 0:
        return pd.DataFrame()
    g = trades.groupby(by)[r_col]

    def pf(x: pd.Series) -> float:
        lo = -x[x < 0].sum()
        return float(x[x > 0].sum() / lo) if lo > 0 else np.inf

    return pd.DataFrame(
        {"n": g.size(), "win_rate": g.apply(lambda x: (x > 0).mean()), "expectancy_R": g.mean(), "profit_factor": g.apply(pf), "total_R": g.sum()}
    )


def time_breakdowns(trades: pd.DataFrame, r_col: str = "R_net") -> dict[str, pd.DataFrame]:
    """Stability tables by year, quarter, month, weekday, session bucket and direction."""
    if len(trades) == 0:
        return {}
    td = pd.DatetimeIndex(trades["trading_date"])
    out = {
        "year": breakdown(trades, td.year.to_numpy(), r_col),
        "quarter": breakdown(trades, td.to_period("Q").astype(str).to_numpy(), r_col),
        "month": breakdown(trades, td.to_period("M").astype(str).to_numpy(), r_col),
        "weekday": breakdown(trades, td.day_name().to_numpy(), r_col),
        "direction": breakdown(trades, trades["direction"].map({1: "long", -1: "short"}).to_numpy(), r_col),
    }
    if "entry_bucket" in trades.columns:
        out["bucket"] = breakdown(trades, trades["entry_bucket"].to_numpy(), r_col)
    return out
