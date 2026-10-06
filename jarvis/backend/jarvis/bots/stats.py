"""From MetaTrader's deals to honest numbers.

A trade is a position: its result is the sum of profit, commission and swap of
all its deals, booked when it is closed. Returns are measured against the
balance before the trade closed, so a bot that compounds its lot size is
judged per trade, not by its growing account.

Periods are cut by closing time: in-sample (what the research model iterates
on), out-of-sample (pass/fail only) and holdout (only for the user).

Drawdowns use the closed-trade balance. Prop firms measure equity including
open trades, so the real intraday drawdown can be larger — reported as such.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from statistics import NormalDist
from typing import Any

DEAL_BALANCE = 2  # DEAL_TYPE_BALANCE (deposits)
ENTRY_IN = 0


class DealsError(ValueError):
    pass


@dataclass(frozen=True)
class Trade:
    position: int
    opened: int  # epoch seconds
    closed: int
    side: str  # buy / sell
    volume: float
    pnl: float  # profit + commission + swap


@dataclass
class Ledger:
    deposit: float
    trades: list[Trade]


def parse_deals(text: str) -> Ledger:
    """The CSV the export hook writes (time,type,entry,volume,price,profit,…)."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    needed = {"time", "type", "entry", "volume", "profit", "commission", "swap", "position"}
    if rows and not needed <= set(rows[0]):
        raise DealsError("unexpected deals file")
    deposit = 0.0
    positions: dict[int, dict[str, Any]] = {}
    try:
        for row in rows:
            kind = int(row["type"])
            profit = float(row["profit"])
            if kind == DEAL_BALANCE:
                deposit += profit
                continue
            if kind not in (0, 1):  # buy / sell; credits, charges etc. don't count as trades
                continue
            pos = int(row["position"])
            entry = int(row["entry"])
            moment = int(row["time"])
            item = positions.setdefault(
                pos, {"opened": moment, "closed": moment, "side": "", "volume": 0.0, "pnl": 0.0}
            )
            item["pnl"] += profit + float(row["commission"]) + float(row["swap"])
            if entry == ENTRY_IN:
                item["opened"] = min(item["opened"], moment)
                item["side"] = item["side"] or ("buy" if kind == 0 else "sell")
                item["volume"] += float(row["volume"])
            else:
                item["closed"] = max(item["closed"], moment)
    except (KeyError, ValueError) as exc:
        raise DealsError(f"can't read the deals file ({exc})") from exc
    trades = [
        Trade(pos, v["opened"], v["closed"], v["side"] or "?", v["volume"], v["pnl"])
        for pos, v in positions.items()
    ]
    trades.sort(key=lambda t: (t.closed, t.position))
    return Ledger(deposit, trades)


def _day(ts: int) -> date:
    return datetime.fromtimestamp(ts, UTC).date()


def _epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


@dataclass(frozen=True)
class PropRules:
    daily_loss_pct: float = 5.0
    max_loss_pct: float = 10.0


@dataclass
class Stats:
    trades: int
    net: float
    profit_factor: float | None
    win_rate: float | None
    avg_trade: float | None
    return_pct: float  # compounded over the period
    t_stat: float  # of per-trade returns
    max_drawdown_pct: float  # closed balance, from the peak
    max_daily_loss_pct: float  # worst day vs. that day's start balance
    months: int
    avg_month_pct: float | None
    median_month_pct: float | None
    worst_month_pct: float | None
    positive_months: int
    monthly: list[dict[str, Any]] = field(default_factory=list)

    def brief(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("monthly")
        return out


def summarize(ledger: Ledger, start: date, end: date | None) -> Stats:
    """Trades closed in [start, end)."""
    lo, hi = _epoch(start), _epoch(end) if end else 2**62
    balance = ledger.deposit
    rows: list[tuple[Trade, float]] = []  # (trade, balance before it closed)
    for trade in ledger.trades:
        if lo <= trade.closed < hi:
            rows.append((trade, balance))
        balance += trade.pnl
    if not rows:
        return Stats(0, 0.0, None, None, None, 0.0, 0.0, 0.0, 0.0, 0, None, None, None, 0)
    pnl = [t.pnl for t, _ in rows]
    returns = [t.pnl / b for t, b in rows if b > 0]
    gains = sum(p for p in pnl if p > 0)
    losses = -sum(p for p in pnl if p < 0)
    start_balance = rows[0][1]
    # Drawdown on the closed balance within the period.
    peak = equity = start_balance
    worst = 0.0
    for p in pnl:
        equity += p
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    # Days and months by closing time.
    days: dict[date, list[float]] = {}
    months: dict[str, list[float]] = {}
    for trade, before in rows:
        day = _day(trade.closed)
        days.setdefault(day, [before, 0.0])[1] += trade.pnl
        key = f"{day:%Y-%m}"
        months.setdefault(key, [before, 0.0, 0])
        months[key][1] += trade.pnl
        months[key][2] += 1
    daily_worst = max((-pl / b for b, pl in days.values() if b > 0), default=0.0)
    monthly: list[dict[str, Any]] = [
        {
            "month": key,
            "pnl": round(pl, 2),
            "pct": round(pl / b * 100, 2) if b > 0 else None,
            "trades": n,
            "start_balance": round(b, 2),
        }
        for key, (b, pl, n) in sorted(months.items())
    ]
    pcts: list[float] = sorted(float(m["pct"]) for m in monthly if m["pct"] is not None)
    growth = 1.0
    for r in returns:
        growth *= 1 + r
    return Stats(
        trades=len(rows),
        net=round(sum(pnl), 2),
        profit_factor=round(min(gains / losses, 99.0), 3)
        if losses > 0
        else (99.0 if gains else None),
        win_rate=round(sum(p > 0 for p in pnl) / len(pnl), 4),
        avg_trade=round(sum(pnl) / len(pnl), 2),
        return_pct=round((growth - 1) * 100, 2),
        t_stat=round(_t(returns), 2),
        max_drawdown_pct=round(worst * 100, 2),
        max_daily_loss_pct=round(max(daily_worst, 0.0) * 100, 2),
        months=len(monthly),
        avg_month_pct=round(sum(pcts) / len(pcts), 2) if pcts else None,
        median_month_pct=round(pcts[len(pcts) // 2], 2) if pcts else None,
        worst_month_pct=round(pcts[0], 2) if pcts else None,
        positive_months=sum(p > 0 for p in pcts),
        monthly=monthly,
    )


def _profit_factor(gains: float, losses: float) -> float | None:
    if losses > 0:
        return round(min(gains / losses, 99.0), 3)
    return 99.0 if gains > 0 else None


def _t(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var <= 0:
        return 0.0 if mean == 0 else math.copysign(99.0, mean)
    return max(-99.0, min(99.0, mean / math.sqrt(var / n)))


def prop_check(stats: Stats, prop: PropRules) -> dict[str, Any]:
    """Would the period have broken a prop firm's loss limits (closed trades)?"""
    return {
        "daily_loss_ok": stats.max_daily_loss_pct < prop.daily_loss_pct,
        "max_loss_ok": stats.max_drawdown_pct < prop.max_loss_pct,
        "daily_loss_pct": stats.max_daily_loss_pct,
        "max_drawdown_pct": stats.max_drawdown_pct,
        "limits": asdict(prop),
    }


SAFETY = 1.5  # future drawdowns are usually deeper than the backtest's


def scaling(
    stats: Stats, *, account: float, drawdown_limit_pct: float, target: float
) -> dict[str, Any] | None:
    """What it takes to make ``target`` per month at a drawdown the account can bear.

    The lot size is scaled by k so that the backtest's worst drawdown times a
    safety margin stays within ``drawdown_limit_pct``. Monthly profit then is
    account * median monthly return * k. Linear scaling ignores slippage on
    bigger size — an optimistic simplification, said so in the UI.
    """
    if stats.median_month_pct is None or stats.months < 3:
        return None
    dd = max(stats.max_drawdown_pct, 0.5)  # a drawdown below 0.5 % is not believable
    k = drawdown_limit_pct / (dd * SAFETY)
    monthly_pct = stats.median_month_pct * k
    monthly_usd = account * monthly_pct / 100
    needed = target / (monthly_pct / 100) if monthly_pct > 0 else None
    return {
        "account": account,
        "drawdown_limit_pct": drawdown_limit_pct,
        "risk_scale": round(k, 2),
        "monthly_pct": round(monthly_pct, 2),
        "monthly_usd": round(monthly_usd, 0),
        "account_for_target": round(needed, -3) if needed else None,
        "target": target,
        "expected_drawdown_pct": round(dd * k, 1),
    }


def t_required(evaluations: int, alpha: float) -> float:
    return NormalDist().inv_cdf(1 - alpha / max(1, evaluations))


HOLDOUT_T = NormalDist().inv_cdf(0.95)
