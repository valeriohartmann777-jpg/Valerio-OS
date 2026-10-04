"""Evaluates strategy rules over bars — vectorised, and never looking ahead.

Every value at bar ``i`` uses only bars up to and including ``i`` (a decision
is made at the bar's close; ``backtest.py`` enters at the next bar's open).
Session features exist only inside the strategy's session and are NaN outside
it; comparisons with NaN are false, so no rule fires on missing data.
"""

from __future__ import annotations

import math

import numpy as np

from jarvis.learning.market import Bars, local_clock
from jarvis.learning.strategy import BinOp, Condition, Neg, Node, Num, Session, clock_minutes

NAN = np.nan


class FeatureFrame:
    def __init__(self, bars: Bars, session: Session) -> None:
        self.bars = bars
        self.n = len(bars)
        self.clock = local_clock(bars.t, session.tz)
        self.start = clock_minutes(session.start)
        self.end = clock_minutes(session.end)
        opens = self.clock.minute
        closes = opens + bars.minutes
        self.in_session = (opens >= self.start) & (closes <= self.end)
        self.session_id = np.where(self.in_session, self.clock.day, -1)
        self.minutes_in = np.where(self.in_session, closes - self.start, NAN).astype(np.float64)
        # Contiguous runs of in-session bars that belong to the same local day.
        inside = np.flatnonzero(self.in_session)
        if len(inside):
            breaks = np.flatnonzero(np.diff(self.session_id[inside]) != 0) + 1
            self.group_starts = inside[np.r_[0, breaks]]
            self.group_ends = inside[np.r_[breaks - 1, len(inside) - 1]]
        else:
            self.group_starts = self.group_ends = np.empty(0, dtype=np.int64)
        self._cache: dict[tuple[str, tuple[int, ...]], np.ndarray] = {}

    # Public --------------------------------------------------------------------------

    def condition(self, cond: Condition) -> np.ndarray:
        a, b = self.value(cond.left), self.value(cond.right)
        out: np.ndarray
        with np.errstate(invalid="ignore"):
            if cond.op == ">":
                out = a > b
            elif cond.op == "<":
                out = a < b
            elif cond.op == ">=":
                out = a >= b
            elif cond.op == "<=":
                out = a <= b
            elif cond.op == "crosses_above":
                out = (a > b) & (_shift(a, 1) <= _shift(b, 1))
            else:
                out = (a < b) & (_shift(a, 1) >= _shift(b, 1))
        return out

    def value(self, node: Node) -> np.ndarray:
        out: np.ndarray
        if isinstance(node, Num):
            out = np.full(self.n, node.value)
        elif isinstance(node, Neg):
            out = -self.value(node.operand)
        elif isinstance(node, BinOp):
            a, b = self.value(node.left), self.value(node.right)
            with np.errstate(invalid="ignore", divide="ignore"):
                if node.op == "+":
                    out = a + b
                elif node.op == "-":
                    out = a - b
                elif node.op == "*":
                    out = a * b
                else:
                    out = a / b
                    out[~np.isfinite(out)] = NAN
        else:
            base = self.feature(node.name, node.args)
            out = _shift(base, node.lag) if node.lag else base
        return out

    def feature(self, name: str, args: tuple[int, ...] = ()) -> np.ndarray:
        key = (name, args)
        if key not in self._cache:
            self._cache[key] = self._compute(name, args)
        return self._cache[key]

    # Features ------------------------------------------------------------------------

    def _compute(self, name: str, args: tuple[int, ...]) -> np.ndarray:
        b = self.bars
        if name == "open":
            return b.open
        if name == "high":
            return b.high
        if name == "low":
            return b.low
        if name == "close":
            return b.close
        if name == "volume":
            return b.volume
        if name == "range":
            return _diff(b.high, b.low)
        if name == "body":
            return _diff(b.close, b.open)
        if name == "sma":
            return rolling_mean(b.close, args[0])
        if name == "ema":
            return ewm(b.close, 2.0 / (args[0] + 1), seed=args[0])
        if name == "rsi":
            return rsi(b.close, args[0])
        if name == "atr":
            return atr(b.high, b.low, b.close, args[0])
        if name == "stdev":
            return rolling_std(b.close, args[0])
        if name == "highest":
            return rolling_max(b.high, args[0])
        if name == "lowest":
            return -rolling_max(-b.low, args[0])
        if name == "vol_sma":
            return rolling_mean(b.volume, args[0])
        if name == "change":
            return _diff(b.close, _shift(b.close, args[0]))
        if name == "minutes":
            return self.minutes_in
        if name == "weekday":
            return self.clock.weekday.astype(np.float64)
        if name in ("window_high", "window_low"):
            return self._window(args[0], args[1], high=name == "window_high")
        return self._session(name, args)

    def _session(self, name: str, args: tuple[int, ...]) -> np.ndarray:
        b = self.bars
        out = np.full(self.n, NAN)
        prev: tuple[float, float, float] | None = None
        for s, e in zip(self.group_starts.tolist(), self.group_ends.tolist(), strict=True):
            sl = slice(s, e + 1)
            if name == "session_open":
                out[sl] = b.open[s]
            elif name == "session_high":
                out[sl] = np.maximum.accumulate(b.high[sl])
            elif name == "session_low":
                out[sl] = np.minimum.accumulate(b.low[sl])
            elif name == "vwap":
                typical = (b.high[sl] + b.low[sl] + b.close[sl]) / 3.0
                volume = b.volume[sl]
                with np.errstate(invalid="ignore", divide="ignore"):
                    out[sl] = np.cumsum(typical * volume) / np.cumsum(volume)
            elif name in ("or_high", "or_low"):
                minutes = self.minutes_in[sl]
                in_range = minutes <= args[0]
                if minutes[-1] >= args[0] and in_range.any():
                    window = b.high[sl] if name == "or_high" else b.low[sl]
                    level = window[in_range].max() if name == "or_high" else window[in_range].min()
                    ready = np.flatnonzero(minutes >= args[0])[0]
                    out[s + ready : e + 1] = level
            elif name in ("prev_high", "prev_low", "prev_close"):
                if prev is not None:
                    out[sl] = {"prev_high": prev[0], "prev_low": prev[1], "prev_close": prev[2]}[
                        name
                    ]
                prev = (float(b.high[sl].max()), float(b.low[sl].min()), float(b.close[e]))
            else:  # pragma: no cover - the parser only knows the features above
                raise KeyError(name)
        return out

    def _window(self, start: int, end: int, *, high: bool) -> np.ndarray:
        """High/low of the most recent completed local time window."""
        minute = self.clock.minute
        if start < end:
            member = (minute >= start) & (minute < end)
        else:
            member = (minute >= start) | (minute < end)
        out = np.full(self.n, NAN)
        idx = np.flatnonzero(member)
        if len(idx) == 0:
            return out
        # A new occurrence starts after a gap of more than the window's length.
        length = ((end - start) % 1440 or 1440) * 60
        breaks = np.flatnonzero(np.diff(self.bars.t[idx]) > length) + 1
        firsts = np.r_[0, breaks]
        lasts = idx[np.r_[breaks - 1, len(idx) - 1]]
        values = (self.bars.high if high else self.bars.low)[idx]
        levels = (np.maximum if high else np.minimum).reduceat(values, firsts)
        # Known for sure only once a bar outside the window has closed: from the
        # bar after the occurrence's last one until the next occurrence is over.
        for k, last in enumerate(lasts.tolist()):
            until = int(lasts[k + 1]) + 1 if k + 1 < len(lasts) else self.n
            out[last + 1 : until] = levels[k]
        return out


# Numeric helpers ----------------------------------------------------------------------


def _diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    out: np.ndarray = a - b
    return out


def _shift(x: np.ndarray, k: int) -> np.ndarray:
    if k == 0:
        return x
    out = np.full(len(x), NAN)
    if k < len(x):
        out[k:] = x[:-k]
    return out


def rolling_mean(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), NAN)
    if len(x) < n:
        return out
    centred = x - (x[0] if len(x) else 0.0)
    c = np.cumsum(np.r_[0.0, centred])
    out[n - 1 :] = (c[n:] - c[:-n]) / n + (x[0] if len(x) else 0.0)
    return out


def rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), NAN)
    if len(x) < n or n < 2:
        return out
    centred = x - x.mean()
    c1 = np.cumsum(np.r_[0.0, centred])
    c2 = np.cumsum(np.r_[0.0, centred * centred])
    s1 = c1[n:] - c1[:-n]
    s2 = c2[n:] - c2[:-n]
    var = np.maximum((s2 - s1 * s1 / n) / (n - 1), 0.0)
    out[n - 1 :] = np.sqrt(var)
    return out


def rolling_max(x: np.ndarray, n: int) -> np.ndarray:
    """Max of the last ``n`` values (van Herk/Gil-Werman, O(len))."""
    size = len(x)
    out = np.full(size, NAN)
    if size < n:
        return out
    if n == 1:
        return x.astype(np.float64).copy()
    blocks = math.ceil(size / n)
    padded = np.full(blocks * n, -np.inf)
    padded[:size] = x
    grid = padded.reshape(blocks, n)
    prefix = np.maximum.accumulate(grid, axis=1).ravel()
    suffix = np.maximum.accumulate(grid[:, ::-1], axis=1)[:, ::-1].ravel()
    ends = np.arange(n - 1, size)
    out[n - 1 :] = np.maximum(suffix[ends - n + 1], prefix[ends])
    return out


def ewm(x: np.ndarray, alpha: float, *, seed: int = 1) -> np.ndarray:
    """Exponential smoothing seeded with the mean of the first ``seed`` values."""
    size = len(x)
    out = np.full(size, NAN)
    if size < seed:
        return out
    values = x.tolist()
    level = sum(values[:seed]) / seed
    result = out.tolist()
    result[seed - 1] = level
    keep = 1.0 - alpha
    for i in range(seed, size):
        level = alpha * values[i] + keep * level
        result[i] = level
    return np.array(result, dtype=np.float64)


def rsi(close: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(close), NAN)
    if len(close) <= n:
        return out
    delta = np.diff(close)
    gain = ewm(np.maximum(delta, 0.0), 1.0 / n, seed=n)
    loss = ewm(np.maximum(-delta, 0.0), 1.0 / n, seed=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        flat = np.where(gain == 0, 50.0, 100.0)
        value = np.where(loss == 0, flat, 100.0 - 100.0 / (1.0 + gain / loss))
    out[1:] = np.where(np.isnan(gain), NAN, value)
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    prev_close = _shift(close, 1)
    true_range = np.fmax(high - low, np.fmax(np.abs(high - prev_close), np.abs(low - prev_close)))
    return ewm(true_range, 1.0 / n, seed=n)
