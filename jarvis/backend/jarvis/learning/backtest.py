"""Conservative intraday backtest of one strategy.

- A rule is checked at a bar's close; the trade opens at the next bar's open
  (same session). One position at a time, at most ``max_trades_per_day``.
- Stop and target are distances from the entry, evaluated at the signal bar.
  When one bar touches both, the stop counts (we can't see inside the bar).
  A stop gapped through fills at the worse open; targets never fill better.
- Every trade pays the instrument's round-trip cost (commission, spread,
  slippage) in points. Results are in points and in R (multiples of the stop).
- Positions are closed at the time stop or at the last bar of the session.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from jarvis.learning.features import FeatureFrame
from jarvis.learning.market import Bars
from jarvis.learning.strategy import Strategy, parse_condition, parse_expression


@dataclass(frozen=True)
class Trade:
    entry_t: int  # epoch seconds of the entry bar's open
    exit_t: int  # epoch seconds of the exit bar's close
    side: int  # +1 long, -1 short
    entry: float
    exit: float
    risk: float  # stop distance in points
    points: float  # after costs
    r: float
    reason: str  # stop | target | time | session_end


@dataclass(frozen=True)
class Simulation:
    trades: list[Trade]
    skipped_small_stop: int = 0


def simulate(
    strategy: Strategy, bars: Bars, *, cost_points: float, min_stop_points: float
) -> Simulation:
    frame = FeatureFrame(bars, strategy.session)
    n = frame.n
    if n < 2:
        return Simulation([])
    sid = frame.session_id
    # The last bar of each bar's session.
    session_last = np.full(n, -1, dtype=np.int64)
    for s, e in zip(frame.group_starts.tolist(), frame.group_ends.tolist(), strict=True):
        session_last[s : e + 1] = e
    can_enter = np.zeros(n, dtype=bool)
    can_enter[:-1] = (sid[:-1] >= 0) & (sid[1:] == sid[:-1])

    signals: list[tuple[int, int, np.ndarray, np.ndarray | None, float | None, int | None]] = []
    for entry in strategy.entries:
        mask = can_enter.copy()
        for rule in entry.when:
            mask &= frame.condition(parse_condition(rule))
        exit_rule = strategy.exit_for(entry)
        stop = frame.value(parse_expression(exit_rule.stop))
        target = frame.value(parse_expression(exit_rule.target)) if exit_rule.target else None
        max_bars = (
            math.ceil(exit_rule.max_minutes / bars.minutes) if exit_rule.max_minutes else None
        )
        side = 1 if entry.side == "long" else -1
        for i in np.flatnonzero(mask).tolist():
            signals.append((i, side, stop, target, exit_rule.target_r, max_bars))
    # Earlier bars first; on the same bar the first entry rule wins.
    signals.sort(key=lambda s: s[0])

    trades: list[Trade] = []
    skipped = 0
    free_from = 0
    per_session: Counter[int] = Counter()
    seconds = bars.minutes * 60
    for i, side, stop, target, target_r, max_bars in signals:
        if i < free_from or per_session[int(sid[i])] >= strategy.max_trades_per_day:
            continue
        risk = float(stop[i])
        if not math.isfinite(risk) or risk < min_stop_points:
            skipped += 1
            continue
        if target is not None:
            reward = float(target[i])
            if not math.isfinite(reward) or reward <= 0:
                continue
        elif target_r is not None:
            reward = target_r * risk
        else:
            reward = math.inf
        j = i + 1
        last = int(session_last[i])
        if max_bars is not None:
            last = min(last, j + max_bars - 1)
        entry_px = float(bars.open[j])
        stop_px = entry_px - side * risk
        target_px = entry_px + side * reward
        lows, highs = bars.low[j : last + 1], bars.high[j : last + 1]
        no_hits = np.empty(0, dtype=np.int64)
        if side > 0:
            stop_hits = np.flatnonzero(lows <= stop_px)
            target_hits = np.flatnonzero(highs >= target_px) if math.isfinite(reward) else no_hits
        else:
            stop_hits = np.flatnonzero(highs >= stop_px)
            target_hits = np.flatnonzero(lows <= target_px) if math.isfinite(reward) else no_hits
        first_stop = int(stop_hits[0]) if len(stop_hits) else None
        first_target = int(target_hits[0]) if len(target_hits) else None
        if first_stop is not None and (first_target is None or first_stop <= first_target):
            k = j + first_stop
            gap = float(bars.open[k])
            gapped = k > j and (gap < stop_px if side > 0 else gap > stop_px)
            exit_px, reason = (gap if gapped else stop_px), "stop"
        elif first_target is not None:
            k = j + first_target
            exit_px, reason = target_px, "target"
        else:
            k = last
            exit_px = float(bars.close[k])
            reason = "session_end" if k == session_last[i] else "time"
        points = side * (exit_px - entry_px) - cost_points
        trades.append(
            Trade(
                entry_t=int(bars.t[j]),
                exit_t=int(bars.t[k]) + seconds,
                side=side,
                entry=entry_px,
                exit=exit_px,
                risk=risk,
                points=points,
                r=points / risk,
                reason=reason,
            )
        )
        per_session[int(sid[i])] += 1
        free_from = k  # a new signal may come at the exit bar's close
    return Simulation(trades, skipped)


# Statistics ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stats:
    trades: int
    win_rate: float
    avg_r: float
    t_stat: float
    total_r: float
    profit_factor: float
    max_drawdown_r: float
    avg_points: float
    trades_per_month: float
    avg_minutes: float
    long_trades: int
    short_trades: int
    exits: dict[str, int] = field(default_factory=dict)
    by_year: dict[str, dict[str, float]] = field(default_factory=dict)

    def brief(self) -> dict[str, object]:
        return {
            "trades": self.trades,
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
            "t_stat": round(self.t_stat, 2),
            "profit_factor": round(self.profit_factor, 2),
            "max_drawdown_r": round(self.max_drawdown_r, 1),
            "avg_points": round(self.avg_points, 2),
            "trades_per_month": round(self.trades_per_month, 1),
            "avg_minutes": round(self.avg_minutes, 1),
            "long_short": [self.long_trades, self.short_trades],
            "exits": self.exits,
            "by_year": self.by_year,
        }


PF_CAP = 99.0
T_CAP = 99.0


def summarize(trades: list[Trade], months: float) -> Stats:
    count = len(trades)
    if count == 0:
        return Stats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)
    r = np.array([t.r for t in trades])
    points = np.array([t.points for t in trades])
    std = float(r.std(ddof=1)) if count > 1 else 0.0
    mean = float(r.mean())
    if std > 0:
        t_stat = max(-T_CAP, min(T_CAP, mean / (std / math.sqrt(count))))
    else:  # identical results: as certain as it gets, in whichever direction
        t_stat = math.copysign(T_CAP, mean) if mean else 0.0
    gains, losses = points[points > 0].sum(), -points[points < 0].sum()
    pf = PF_CAP if losses == 0 else min(PF_CAP, float(gains / losses))
    equity = np.cumsum(r)
    drawdown = float((np.maximum.accumulate(np.r_[0.0, equity])[1:] - equity).max())
    years: dict[str, list[float]] = {}
    for trade in trades:
        year = str(datetime.fromtimestamp(trade.entry_t, UTC).year)
        years.setdefault(year, []).append(trade.r)
    return Stats(
        trades=count,
        win_rate=float((points > 0).mean()),
        avg_r=float(r.mean()),
        t_stat=t_stat,
        total_r=float(r.sum()),
        profit_factor=pf,
        max_drawdown_r=max(0.0, drawdown),
        avg_points=float(points.mean()),
        trades_per_month=count / months if months > 0 else 0.0,
        avg_minutes=float(np.mean([(t.exit_t - t.entry_t) / 60 for t in trades])),
        long_trades=sum(1 for t in trades if t.side > 0),
        short_trades=sum(1 for t in trades if t.side < 0),
        exits=dict(Counter(t.reason for t in trades)),
        by_year={
            y: {"trades": len(v), "avg_r": round(float(np.mean(v)), 3)}
            for y, v in sorted(years.items())
        },
    )
