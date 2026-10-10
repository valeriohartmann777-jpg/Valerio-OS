"""Research metrics for a futures run, every one derived from the trade ledger.

Conventions (also in docs/quantlab/VALIDATION_PROTOCOL.md):

* Daily series = net P&L per strategy session (sessions without a trade count
  as 0; sessions without data or skipped are left out and counted).
* Returns are P&L ÷ the stated account capital (no compounding, no margin
  model). Sharpe/Sortino: mean ÷ (downside) deviation of daily returns × √252,
  risk-free rate 0 — only with ≥ 20 sessions and non-zero deviation.
* Drawdown: from the running peak of cumulative P&L at session closes
  (intraday marks for the full run when recorded).
* Undefined values are ``None`` with the reason in ``notes`` — never infinity,
  never a made-up number.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from jarvis.quantlab.futures.engine import MINUTE, Result, Trade

OBSERVED = {"TRADED", "NO_SIGNAL", "NO_RANGE", "AMBIGUOUS_ENTRY"}
ANNUAL = 252


def _f(value: Decimal | float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _drawdown(values: list[float]) -> tuple[float, float, int]:
    """(max drawdown in money, as a fraction of the peak equity, longest underwater run)."""
    peak, worst, worst_frac, run, longest = -math.inf, 0.0, 0.0, 0, 0
    for value in values:
        if value > peak:
            peak, run = value, 0
        else:
            run += 1
            longest = max(longest, run)
        draw = peak - value
        if draw > worst:
            worst = draw
            worst_frac = draw / peak if peak > 0 else 0.0
    return worst, worst_frac, longest


def _streaks(trades: list[Trade]) -> tuple[int, int]:
    best = worst = wins = losses = 0
    for t in trades:
        if t.net > 0:
            wins, losses = wins + 1, 0
        elif t.net < 0:
            wins, losses = 0, losses + 1
        else:
            wins = losses = 0
        best, worst = max(best, wins), max(worst, losses)
    return best, worst


def segment(result: Result, labels: Iterable[str] | None = None) -> dict[str, Any]:
    """Metrics for the sessions in ``labels`` (all sessions when None)."""
    chosen = set(labels) if labels is not None else None
    sessions = [
        s for s in result.sessions if s.status in OBSERVED and (chosen is None or s.label in chosen)
    ]
    names = {s.label for s in sessions}
    trades = [t for t in result.trades if t.session in names]
    capital = float(result.capital)
    notes: dict[str, str] = {}
    daily = [float(s.net) for s in sessions]
    net = sum((t.net for t in trades), Decimal(0))
    gross = sum((t.gross for t in trades), Decimal(0))
    fees = sum((t.fees for t in trades), Decimal(0))
    slippage = sum((t.slippage_cost for t in trades), Decimal(0))
    wins = [t for t in trades if t.net > 0]
    losses = [t for t in trades if t.net < 0]
    out: dict[str, Any] = {
        "sessions": len(sessions),
        "sessions_with_trades": sum(1 for s in sessions if s.trades),
        "trades": len(trades),
        "long_trades": sum(1 for t in trades if t.direction > 0),
        "short_trades": sum(1 for t in trades if t.direction < 0),
        "net_pnl": _f(net),
        "gross_pnl": _f(gross),
        "fees": _f(fees),
        "slippage_cost": _f(slippage),
        "return_on_capital": _f(net / result.capital, 6) if result.capital else None,
        "capital": capital,
        "wins": len(wins),
        "losses": len(losses),
        "ambiguous_trades": sum(1 for t in trades if t.ambiguous),
        "ambiguous_sessions": sum(1 for s in sessions if s.status == "AMBIGUOUS_ENTRY"),
        "exit_reasons": {},
    }
    for t in trades:
        out["exit_reasons"][t.exit_reason] = out["exit_reasons"].get(t.exit_reason, 0) + 1
    if not trades:
        notes["trades"] = "No trades — trade statistics are undefined."
    out["win_rate"] = _f(len(wins) / len(trades), 4) if trades else None
    out["avg_win"] = _f(sum(t.net for t in wins) / len(wins)) if wins else None
    out["avg_loss"] = _f(sum(t.net for t in losses) / len(losses)) if losses else None
    if out["avg_win"] is not None and out["avg_loss"] is not None and out["avg_loss"] != 0:
        out["payoff_ratio"] = _f(abs(out["avg_win"] / out["avg_loss"]), 3)
    else:
        out["payoff_ratio"] = None
    gains = sum((t.net for t in wins), Decimal(0))
    pains = -sum((t.net for t in losses), Decimal(0))
    if pains > 0:
        out["profit_factor"] = _f(gains / pains, 3)
    else:
        out["profit_factor"] = None
        if trades:
            notes["profit_factor"] = "No losing trades — profit factor is undefined, not infinite."
    out["expectancy"] = _f(net / len(trades)) if trades else None
    if trades:
        ticks = [(t.exit_ticks - t.entry_ticks) * t.direction for t in trades]
        out["expectancy_ticks"] = _f(sum(ticks) / len(ticks), 3)
        rs = [t.r_multiple for t in trades if t.r_multiple is not None]
        out["expectancy_r"] = _f(sum(rs, Decimal(0)) / len(rs), 3) if rs else None
        out["median_trade"] = _f(statistics.median(float(t.net) for t in trades))
        out["best_trade"] = _f(max(t.net for t in trades))
        out["worst_trade"] = _f(min(t.net for t in trades))
        out["avg_mae_ticks"] = _f(sum(t.mae_ticks for t in trades) / len(trades), 2)
        out["avg_mfe_ticks"] = _f(sum(t.mfe_ticks for t in trades) / len(trades), 2)
        holds = [(t.exit_bar_ts - t.entry_bar_ts) // MINUTE + 1 for t in trades]
        out["avg_hold_minutes"] = _f(sum(holds) / len(holds), 1)
        best, worst = _streaks(trades)
        out["max_win_streak"], out["max_loss_streak"] = best, worst
        ranked = sorted((float(t.net) for t in trades), reverse=True)
        top = sum(ranked[:5])
        out["net_without_top5"] = _f(float(net) - top)
        out["top5_share"] = _f(top / float(net), 4) if float(net) > 0 else None
    else:
        for key in (
            "expectancy_ticks",
            "expectancy_r",
            "median_trade",
            "best_trade",
            "worst_trade",
            "avg_mae_ticks",
            "avg_mfe_ticks",
            "avg_hold_minutes",
            "max_win_streak",
            "max_loss_streak",
            "net_without_top5",
            "top5_share",
        ):
            out[key] = None  # fmt: skip
    if len(trades) >= 8:
        values = [float(t.net) for t in trades]
        mean, stdev = statistics.fmean(values), statistics.pstdev(values)
        if stdev > 0:
            out["skew"] = _f(sum(((v - mean) / stdev) ** 3 for v in values) / len(values), 3)
            out["excess_kurtosis"] = _f(
                sum(((v - mean) / stdev) ** 4 for v in values) / len(values) - 3, 3
            )
        else:
            out["skew"] = out["excess_kurtosis"] = None
    else:
        out["skew"] = out["excess_kurtosis"] = None
        notes["skew"] = "Fewer than 8 trades — shape statistics would be noise."
    window = sum(s.window_minutes for s in sessions)
    in_market = sum(s.minutes_in_market for s in sessions)
    out["exposure"] = _f(in_market / window, 4) if window else None
    cumulative, running = [], 0.0
    for value in daily:
        running += value
        cumulative.append(capital + running)
    dd, dd_frac, underwater = _drawdown([capital, *cumulative])
    out["max_drawdown"] = _f(dd)
    out["max_drawdown_pct"] = _f(dd_frac, 4)
    out["max_drawdown_sessions"] = underwater
    returns = [v / capital for v in daily]
    if len(returns) >= 20:
        mean = statistics.fmean(returns)
        stdev = statistics.stdev(returns)
        out["sharpe"] = _f(mean / stdev * math.sqrt(ANNUAL), 3) if stdev > 0 else None
        downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / len(returns))
        out["sortino"] = _f(mean / downside * math.sqrt(ANNUAL), 3) if downside > 0 else None
        if downside == 0:
            notes["sortino"] = "No losing sessions — Sortino is undefined."
        out["daily_mean"] = _f(statistics.fmean(daily))
        out["daily_std"] = _f(statistics.stdev(daily))
    else:
        out["sharpe"] = out["sortino"] = out["daily_mean"] = out["daily_std"] = None
        notes["sharpe"] = f"Only {len(returns)} sessions — at least 20 are needed."
    if len(returns) >= 60:
        out["annualized_return"] = _f(sum(returns) / len(returns) * ANNUAL, 4)
    else:
        out["annualized_return"] = None
        notes["annualized_return"] = "Fewer than 60 sessions — annualizing would mislead."
    if len(returns) >= 120 and dd_frac > 0 and out["annualized_return"] is not None:
        out["calmar"] = _f(out["annualized_return"] / dd_frac, 3)
    else:
        out["calmar"] = None
    if gross != 0:
        out["cost_share_of_gross"] = _f((fees + slippage) / abs(gross), 4)
    else:
        out["cost_share_of_gross"] = None
    out["notes"] = notes
    return out


def daily_series(result: Result, labels: Iterable[str] | None = None) -> list[tuple[str, float]]:
    chosen = set(labels) if labels is not None else None
    return [
        (s.label, float(s.net))
        for s in result.sessions
        if s.status in OBSERVED and (chosen is None or s.label in chosen)
    ]


def full(result: Result) -> dict[str, Any]:
    out = segment(result)
    if result.equity:
        dd, frac, _ = _drawdown([float(result.capital), *(float(e) for e in result.equity)])
        out["max_drawdown_intraday"] = _f(dd)
        out["max_drawdown_intraday_pct"] = _f(frac, 4)
    counts: dict[str, int] = {}
    for s in result.sessions:
        counts[s.status] = counts.get(s.status, 0) + 1
    out["session_status"] = counts
    out["final_equity"] = _f(result.final_equity)
    return out
