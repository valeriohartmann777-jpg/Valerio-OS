"""SENTINEL: independent checks of a research run before any verdict is shown.

1. **A second engine.** ``recompute`` re-derives every trade from the raw bars with a
   separately written, deliberately plain implementation of the rule semantics
   (``describe()`` is its specification): find the entry, then walk the bars to the
   exit. It shares no simulation code with ``futures.engine``. It is written by the
   same author from the same written rules, so it catches implementation slips —
   not a misunderstanding both share. That limitation is stated in every report.
2. **Ledger checks** on the stored artifacts: trade-by-trade agreement, P&L sums,
   signal-before-fill, the holdout's state, the trial count, the verdict function.
3. **Narrative guard**: every number in a model-written text must appear in the
   evidence, or the text is refused.

The report is sealed with the SHA-256 of its canonical JSON (tamper-evident, not a
cryptographic signature).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from fractions import Fraction
from typing import Any

import numpy as np

from jarvis.quantlab.futures.contracts import ContractSpec
from jarvis.quantlab.futures.engine import Bars
from jarvis.quantlab.futures.sessions import Window
from jarvis.quantlab.futures.spec import FuturesSpec, clock
from jarvis.quantlab.spec import canonical_json, sha256_text

MIN = 60_000_000_000
LIMITATION = (
    "The second engine is an independent re-implementation of the same written rules by the "
    "same author: it catches implementation errors, not a misunderstanding of the rules "
    "that both share."
)


@dataclass(frozen=True)
class RefTrade:
    session: str
    direction: int
    entry_ts: int
    entry_ticks: int
    exit_ts: int
    exit_ticks: int
    reason: str
    net: Decimal
    ambiguous: bool


@dataclass
class _Day:
    label: str
    w: Window
    ts: list[int]
    o: list[int]
    h: list[int]
    lo: list[int]
    c: list[int]
    contract: ContractSpec
    prior: tuple[int, int, int] | None  # high, low, close in ticks (same contract only)


@dataclass(frozen=True)
class _Entry:
    k: int  # bar of the fill
    side: int
    ref: int  # price before slippage
    intrabar: bool  # filled inside bar k (stop order), not at its open
    extreme: int | None
    rng: tuple[int, int] | None


class _Costs:
    def __init__(self, spec: FuturesSpec, multiplier: float) -> None:
        per_side = Decimal(str(spec.costs.commission_per_contract_side)) + Decimal(
            str(spec.costs.exchange_fees_per_contract_side)
        )
        self.n = spec.sizing.contracts
        self.fee = per_side * Decimal(str(multiplier)) * self.n
        self.slip = math.ceil(spec.execution.slippage_ticks * multiplier)


# ------------------------------------------------------------------------------------------
# loading days


def _days(
    spec: FuturesSpec,
    bars: Bars,
    contracts: dict[int, ContractSpec],
    windows: list[Window],
    context: list[Window] | None,
) -> list[_Day | None]:
    order = context if context is not None else windows
    index = {w.label: i for i, w in enumerate(order)}
    out: list[_Day | None] = []
    for w in windows:
        a = int(np.searchsorted(bars.ts, w.start_ns))
        b = int(np.searchsorted(bars.ts, w.end_ns))
        ids = set(bars.iid[a:b].tolist())
        contract = contracts.get(next(iter(ids))) if len(ids) == 1 else None
        if b <= a or contract is None:
            out.append(None)
            continue
        if contract.expiration_ns is not None and w.start_ns >= contract.expiration_ns:
            out.append(None)
            continue
        tick = contract.tick_size_fixed
        i = index.get(w.label, 0)
        prior = None
        if i > 0:
            p = order[i - 1]
            pa = int(np.searchsorted(bars.ts, p.start_ns))
            pb = int(np.searchsorted(bars.ts, p.end_ns))
            if pb > pa and set(bars.iid[pa:pb].tolist()) == {contract.instrument_id}:
                prior = (
                    int(bars.high[pa:pb].max()) // tick,
                    int(bars.low[pa:pb].min()) // tick,
                    int(bars.close[pb - 1]) // tick,
                )
        out.append(
            _Day(
                w.label.isoformat(),
                w,
                bars.ts[a:b].tolist(),
                [int(x) // tick for x in bars.open[a:b]],
                [int(x) // tick for x in bars.high[a:b]],
                [int(x) // tick for x in bars.low[a:b]],
                [int(x) // tick for x in bars.close[a:b]],
                contract,
                prior,
            )
        )
    return out


def _entry_from(spec: FuturesSpec, w: Window) -> int:
    if not spec.filters.entry_after:
        return w.start_ns
    a, b = clock(spec.session.start), clock(spec.filters.entry_after)
    return w.start_ns + ((b.hour - a.hour) * 60 + (b.minute - a.minute)) * MIN


def _bias(spec: FuturesSpec, day: _Day, side: int, price: int) -> bool:
    if not spec.filters.prior_close_bias:
        return True
    if day.prior is None:
        return False
    return price > day.prior[2] if side > 0 else price < day.prior[2]


def _range(day: _Day, minutes: int, spec: FuturesSpec) -> tuple[int, int, int, int] | None:
    end = day.w.start_ns + minutes * MIN
    idx = [i for i, t in enumerate(day.ts) if t < end]
    if not idx:
        return None
    hi, lo = max(day.h[i] for i in idx), min(day.lo[i] for i in idx)
    f = spec.filters
    width = hi - lo
    if (f.min_range_ticks and width < f.min_range_ticks) or (
        f.max_range_ticks and width > f.max_range_ticks
    ):
        return None
    return hi, lo, idx[-1] + 1, end


def _next_open(day: _Day, signal_bar: int, spec: FuturesSpec) -> int | None:
    """Bar index for a market order decided at the close of ``signal_bar``."""
    ready = day.ts[signal_bar] + MIN + spec.execution.latency_bars * MIN
    for i in range(signal_bar + 1, len(day.ts)):
        if day.ts[i] >= ready:
            return i if day.ts[i] < min(day.w.entry_cutoff_ns, day.w.flatten_ns) else None
    return None


# ------------------------------------------------------------------------------------------
# entries per rule (one trade per day)


def _orb(spec: FuturesSpec, day: _Day) -> _Entry | None:
    rule: Any = spec.rule
    got = _range(day, rule.range_minutes, spec)
    if got is None:
        return None
    hi, lo, first, end = got
    buf = rule.buffer_ticks
    up_level, down_level = hi + buf, lo - buf
    allow_up = rule.direction in ("long", "both")
    allow_down = rule.direction in ("short", "both")
    stop_cut = min(day.w.entry_cutoff_ns, day.w.flatten_ns)
    if rule.entry == "stop_through_range":
        allow_up = allow_up and _bias(spec, day, 1, up_level)
        allow_down = allow_down and _bias(spec, day, -1, down_level)
        start = max(end + spec.execution.latency_bars * MIN, _entry_from(spec, day.w))
        for i in range(first, len(day.ts)):
            if day.ts[i] < start:
                continue
            if day.ts[i] >= stop_cut:
                return None
            up = allow_up and day.h[i] >= up_level
            down = allow_down and day.lo[i] <= down_level
            if up and down:
                if day.o[i] >= up_level:
                    down = False
                elif day.o[i] <= down_level:
                    up = False
                else:
                    return None  # both stops inside one minute: excluded
            if up:
                gap = day.o[i] >= up_level
                return _Entry(i, 1, day.o[i] if gap else up_level, not gap, None, (hi, lo))
            if down:
                gap = day.o[i] <= down_level
                return _Entry(i, -1, day.o[i] if gap else down_level, not gap, None, (hi, lo))
        return None
    for i in range(first, len(day.ts)):
        if day.ts[i] + MIN < _entry_from(spec, day.w):
            continue
        side = 0
        if allow_up and day.c[i] > up_level and _bias(spec, day, 1, day.c[i]):
            side = 1
        elif allow_down and day.c[i] < down_level and _bias(spec, day, -1, day.c[i]):
            side = -1
        if side:
            k = _next_open(day, i, spec)
            return None if k is None else _Entry(k, side, day.o[k], False, None, (hi, lo))
    return None


def _sweep(spec: FuturesSpec, day: _Day) -> _Entry | None:
    rule: Any = spec.rule
    if rule.level == "opening_range":
        got = _range(day, rule.range_minutes, spec)
        if got is None:
            return None
        hi, lo, first, usable = got
        rng: tuple[int, int] | None = (hi, lo)
        ready = {+1: True, -1: True}
    else:
        if day.prior is None:
            return None
        hi, lo = day.prior[0], day.prior[1]
        first, usable, rng = 0, day.w.start_ns, None
        ready = {+1: False, -1: False}
    sides = [
        s
        for s, ok in (
            (+1, rule.sides in ("fade_highs", "both")),
            (-1, rule.sides in ("fade_lows", "both")),
        )
        if ok
    ]
    lat = spec.execution.latency_bars * MIN
    stop_cut = min(day.w.entry_cutoff_ns, day.w.flatten_ns)
    entry_from = _entry_from(spec, day.w)
    started: dict[int, int | None] = {+1: None, -1: None}  # bar where the sweep began
    far: dict[int, int] = {}  # extreme price of each running sweep
    stop_at: dict[int, tuple[int, int]] = {}  # side → (order price, order time)
    for i in range(first, len(day.ts)):
        t = day.ts[i]
        if t < usable + lat:
            continue
        if t >= stop_cut:
            return None
        if rule.reclaim == "stop_back_through":
            hits = []
            for s in sides:
                if s not in stop_at:
                    continue
                begun = started[s]
                assert begun is not None
                if i > begun + rule.reclaim_within_bars:
                    del stop_at[s]
                    started[s], ready[s] = None, False
                    continue
                price, placed = stop_at[s]
                if t < placed + lat or t < entry_from:
                    continue
                touched = day.lo[i] <= price if s > 0 else day.h[i] >= price
                if touched:
                    gap = day.o[i] <= price if s > 0 else day.o[i] >= price
                    hits.append(_Entry(i, -s, day.o[i] if gap else price, not gap, far[s], rng))
            if len(hits) > 1:
                return None
            if hits:
                return hits[0]
        closes_back = []
        for s in sides:
            level = hi if s > 0 else lo
            beyond = (
                day.h[i] >= level + rule.sweep_min_ticks
                if s > 0
                else day.lo[i] <= level - rule.sweep_min_ticks
            )
            back_inside = day.c[i] < level if s > 0 else day.c[i] > level
            if started[s] is None and ready[s] and beyond:
                started[s] = i
                far[s] = day.h[i] if s > 0 else day.lo[i]
                if rule.reclaim == "stop_back_through":
                    price = (
                        level - rule.entry_buffer_ticks
                        if s > 0
                        else level + rule.entry_buffer_ticks
                    )
                    if _bias(spec, day, -s, price):
                        stop_at[s] = (price, t + MIN)
                    else:
                        started[s], ready[s] = None, False
                    continue
            elif started[s] is not None:
                far[s] = max(far[s], day.h[i]) if s > 0 else min(far[s], day.lo[i])
            begun = started[s]
            if rule.reclaim == "close_back_inside" and begun is not None:
                if back_inside:
                    if t + MIN >= entry_from and _bias(spec, day, -s, day.c[i]):
                        closes_back.append(s)
                    started[s], ready[s] = None, True
                    continue
                if i - begun + 1 >= rule.reclaim_within_bars:
                    started[s], ready[s] = None, False
                    continue
            if started[s] is None and not ready[s] and back_inside:
                ready[s] = True
        if len(closes_back) > 1:
            return None
        if closes_back:
            s = closes_back[0]
            k = _next_open(day, i, spec)
            return None if k is None else _Entry(k, -s, day.o[k], False, far[s], rng)
    return None


def _retest(spec: FuturesSpec, day: _Day) -> _Entry | None:
    rule: Any = spec.rule
    got = _range(day, rule.range_minutes, spec)
    if got is None:
        return None
    hi, lo, first, end = got
    sides = [
        s
        for s, ok in (
            (+1, rule.direction in ("long", "both")),
            (-1, rule.direction in ("short", "both")),
        )
        if ok
    ]
    lat = spec.execution.latency_bars * MIN
    stop_cut = min(day.w.entry_cutoff_ns, day.w.flatten_ns)
    entry_from = _entry_from(spec, day.w)
    broke: dict[int, int | None] = {+1: None, -1: None}
    ready = {+1: True, -1: True}
    for i in range(first, len(day.ts)):
        t = day.ts[i]
        if t < end + lat:
            continue
        if t >= stop_cut:
            return None
        found = []
        for s in sides:
            edge = hi if s > 0 else lo
            past = (
                day.c[i] > hi + rule.breakout_buffer_ticks
                if s > 0
                else day.c[i] < lo - rule.breakout_buffer_ticks
            )
            inside = day.c[i] <= hi if s > 0 else day.c[i] >= lo
            b = broke[s]
            if b is None:
                if ready[s] and past:
                    broke[s] = i
                elif not ready[s] and inside:
                    ready[s] = True
                continue
            if i - b > rule.retest_within_bars:
                broke[s], ready[s] = None, inside
                continue
            if inside:
                broke[s], ready[s] = None, True
                continue
            near = (
                day.lo[i] <= edge + rule.retest_tolerance_ticks
                if s > 0
                else day.h[i] >= edge - rule.retest_tolerance_ticks
            )
            if near:
                broke[s], ready[s] = None, False
                if t + MIN >= entry_from and _bias(spec, day, s, day.c[i]):
                    found.append((s, day.lo[i] if s > 0 else day.h[i]))
        if len(found) > 1:
            return None
        if found:
            s, extreme = found[0]
            k = _next_open(day, i, spec)
            return None if k is None else _Entry(k, s, day.o[k], False, extreme, (hi, lo))
    return None


# ------------------------------------------------------------------------------------------
# holding a position


def _levels(
    spec: FuturesSpec, side: int, fill: int, extreme: int | None, rng: tuple[int, int] | None
) -> tuple[int | None, int | None]:
    st, tg = spec.exits.stop, spec.exits.target
    stop: int | None = None
    if st.type == "range_opposite" and rng is not None:
        pad = (
            getattr(spec.rule, "buffer_ticks", 0)
            if spec.rule.type == "opening_range_breakout"
            else 0
        )
        stop = rng[1] - pad if side > 0 else rng[0] + pad
    elif st.type == "ticks" and st.ticks:
        stop = fill - side * st.ticks
    elif st.type == "setup_extreme" and st.ticks and extreme is not None:
        stop = extreme - side * st.ticks
    risk = (fill - stop) * side if stop is not None else None
    if risk is not None and risk <= 0:
        risk = None
    target: int | None = None
    if tg.type == "r_multiple" and risk and tg.value is not None:
        target = fill + side * int((Decimal(str(tg.value)) * risk).to_integral_value(ROUND_CEILING))
    elif tg.type == "ticks" and tg.value:
        target = fill + side * math.ceil(tg.value)
    return stop, target


def _past(spec: FuturesSpec, side: int, price: int, target: int) -> bool:
    if spec.execution.limit_fill == "trade_through":
        return price > target if side > 0 else price < target
    return price >= target if side > 0 else price <= target


def _walk(
    spec: FuturesSpec,
    day: _Day,
    side: int,
    k: int,
    stop: int | None,
    target: int | None,
    intrabar: bool,
    mode: str,
) -> tuple[int, int, str, bool]:
    """(bar, price before slippage, reason, ambiguous) of the exit."""
    first = k
    if intrabar:
        inside = stop is not None and (day.lo[k] <= stop if side > 0 else day.h[k] >= stop)
        if mode == "conservative" and inside:
            assert stop is not None
            return k, stop, "STOP", True
        if (
            mode == "optimistic"
            and target is not None
            and (day.c[k] > target if side > 0 else day.c[k] < target)
        ):
            return k, target, "TARGET", True
        first = k + 1
    for i in range(first, len(day.ts)):
        if day.ts[i] >= day.w.flatten_ns:
            return i, day.o[i], "TIME_EXIT", False
        if stop is not None and (day.o[i] <= stop if side > 0 else day.o[i] >= stop):
            return i, day.o[i], "STOP", False
        if target is not None and _past(spec, side, day.o[i], target):
            return i, day.o[i], "TARGET", False
        stop_hit = stop is not None and (day.lo[i] <= stop if side > 0 else day.h[i] >= stop)
        target_hit = target is not None and _past(
            spec, side, day.h[i] if side > 0 else day.lo[i], target
        )
        if stop_hit and target_hit:
            assert stop is not None and target is not None
            return (
                (i, stop, "STOP", True) if mode == "conservative" else (i, target, "TARGET", True)
            )
        if stop_hit:
            assert stop is not None
            return i, stop, "STOP", False
        if target_hit:
            assert target is not None
            return i, target, "TARGET", False
    last = len(day.ts) - 1
    return last, day.c[last], "LAST_BAR_CLOSE", False


def _trade(
    day: _Day,
    side: int,
    k: int,
    fill: int,
    x_bar: int,
    x_ref: int,
    reason: str,
    ambiguous: bool,
    costs: _Costs,
) -> RefTrade:
    x = x_ref if reason == "TARGET" else x_ref - side * costs.slip
    gross = Decimal((x - fill) * side) * day.contract.tick_value * costs.n
    return RefTrade(
        day.label, side, day.ts[k], fill, day.ts[x_bar], x, reason, gross - 2 * costs.fee, ambiguous
    )


def _single(spec: FuturesSpec, day: _Day, mode: str, costs: _Costs) -> list[RefTrade]:
    entry = {
        "opening_range_breakout": _orb,
        "level_sweep_reclaim": _sweep,
        "opening_range_retest": _retest,
    }[spec.rule.type](spec, day)
    if entry is None:
        return []
    fill = entry.ref + entry.side * costs.slip
    stop, target = _levels(spec, entry.side, fill, entry.extreme, entry.rng)
    bar, ref, reason, amb = _walk(
        spec, day, entry.side, entry.k, stop, target, entry.intrabar, mode
    )
    return [_trade(day, entry.side, entry.k, fill, bar, ref, reason, amb, costs)]


def _crossover(spec: FuturesSpec, day: _Day, mode: str, costs: _Costs) -> list[RefTrade]:
    rule: Any = spec.rule
    span = rule.bar_minutes * MIN
    # Decision points: the last 1-minute bar of each N-minute period.
    points: list[tuple[int, int, int]] = []  # (bar, known_at, close)
    for i, t in enumerate(day.ts):
        period = (t - day.w.start_ns) // span
        nxt = day.ts[i + 1] if i + 1 < len(day.ts) else None
        if nxt is not None and (nxt - day.w.start_ns) // span == period:
            continue
        known = (
            t + MIN
            if rule.bar_minutes == 1
            else min(day.w.start_ns + (period + 1) * span, day.w.end_ns)
        )
        points.append((i, known, day.c[i]))
    closes = [p[2] for p in points]

    def gap(n: int) -> Fraction | None:
        if n < 0 or n + 1 < rule.slow:
            return None
        return Fraction(sum(closes[n + 1 - rule.fast : n + 1]), rule.fast) - Fraction(
            sum(closes[n + 1 - rule.slow : n + 1]), rule.slow
        )

    by_bar = {p[0]: n for n, p in enumerate(points)}
    lat = spec.execution.latency_bars * MIN
    entry_from = _entry_from(spec, day.w)
    trades: list[RefTrade] = []
    pos: tuple[int, int, int, int | None, int | None] | None = None  # side, bar, fill, stop, target
    wait: tuple[int, int] | None = None

    def leave(i: int, ref: int, reason: str, amb: bool) -> None:
        nonlocal pos
        assert pos is not None
        side, k, fill, _, _ = pos
        trades.append(_trade(day, side, k, fill, i, ref, reason, amb, costs))
        pos = None

    for i, t in enumerate(day.ts):
        if wait is not None and t >= wait[1] + lat:
            want, _ = wait
            wait = None
            have = pos[0] if pos else 0
            if t < day.w.flatten_ns and want != have:
                if pos is not None:
                    leave(i, day.o[i], "SIGNAL_EXIT", False)
                if want and t < day.w.entry_cutoff_ns:
                    fill = day.o[i] + want * costs.slip
                    stop, target = _levels(spec, want, fill, None, None)
                    pos = (want, i, fill, stop, target)
        if pos is not None:
            side, _, _, stop, target = pos
            if t >= day.w.flatten_ns:
                leave(i, day.o[i], "TIME_EXIT", False)
            elif stop is not None and (day.o[i] <= stop if side > 0 else day.o[i] >= stop):
                leave(i, day.o[i], "STOP", False)
            elif target is not None and _past(spec, side, day.o[i], target):
                leave(i, day.o[i], "TARGET", False)
            else:
                s_hit = stop is not None and (day.lo[i] <= stop if side > 0 else day.h[i] >= stop)
                t_hit = target is not None and _past(
                    spec, side, day.h[i] if side > 0 else day.lo[i], target
                )
                if s_hit and t_hit:
                    assert stop is not None and target is not None
                    if mode == "conservative":
                        leave(i, stop, "STOP", True)
                    else:
                        leave(i, target, "TARGET", True)
                elif s_hit:
                    assert stop is not None
                    leave(i, stop, "STOP", False)
                elif t_hit:
                    assert target is not None
                    leave(i, target, "TARGET", False)
        n = by_bar.get(i)
        if n is None:
            continue
        now, before = gap(n), gap(n - 1)
        if now is None or before is None:
            continue
        if before <= 0 < now:
            want = 1 if rule.direction in ("long", "both") else 0
        elif before >= 0 > now:
            want = -1 if rule.direction in ("short", "both") else 0
        else:
            continue
        known = points[n][1]
        if want and (known < entry_from or not _bias(spec, day, want, points[n][2])):
            want = 0
        wait = (want, known)
    if pos is not None:
        last = len(day.ts) - 1
        leave(last, day.c[last], "LAST_BAR_CLOSE", False)
    return trades


def recompute(
    spec: FuturesSpec,
    bars: Bars,
    contracts: dict[int, ContractSpec],
    windows: list[Window],
    *,
    context: list[Window] | None = None,
    mode: str = "conservative",
    cost_multiplier: float = 1.0,
) -> list[RefTrade]:
    costs = _Costs(spec, cost_multiplier)
    out: list[RefTrade] = []
    f = spec.filters
    for day in _days(spec, bars, contracts, windows, context):
        if day is None:
            continue
        if f.max_gap_ticks or f.prior_close_bias:
            if day.prior is None:
                continue
            if f.max_gap_ticks and abs(day.o[0] - day.prior[2]) > f.max_gap_ticks:
                continue
        if spec.rule.type == "ma_crossover":
            out += _crossover(spec, day, mode, costs)
        else:
            out += _single(spec, day, mode, costs)
    return out


# ------------------------------------------------------------------------------------------
# comparing with the stored ledger


def _ns(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp()) * 1_000_000_000 + dt.microsecond * 1000


def compare(
    stored: list[dict[str, Any]], ref: list[RefTrade], tick_points: float
) -> dict[str, Any]:
    """Trade-by-trade agreement between the stored ledger and the second engine."""
    mine = {(r.session, r.entry_ts): r for r in ref}
    seen: set[tuple[str, int]] = set()
    mismatches: list[str] = []
    agree = 0
    for row in stored:
        key = (row["session"], _ns(row["entry_time"]))
        other = mine.get(key)
        if other is None:
            mismatches.append(f"{row['session']} {row['entry_time'][11:16]}: only in the ledger")
            continue
        seen.add(key)
        side = 1 if row["direction"] == "LONG" else -1
        problems = []
        if side != other.direction:
            problems.append("direction")
        if round(row["entry_price"] / tick_points) != other.entry_ticks:
            problems.append(f"entry {row['entry_price']} vs {other.entry_ticks * tick_points}")
        if round(row["exit_price"] / tick_points) != other.exit_ticks:
            problems.append(f"exit {row['exit_price']} vs {other.exit_ticks * tick_points}")
        if row["exit_reason"] != other.reason:
            problems.append(f"reason {row['exit_reason']} vs {other.reason}")
        if _ns(row["exit_time"]) != other.exit_ts:
            problems.append("exit time")
        if abs(Decimal(str(row["net"])) - other.net) > Decimal("0.005"):
            problems.append(f"net {row['net']} vs {other.net}")
        if problems:
            mismatches.append(
                f"{row['session']} {row['entry_time'][11:16]}: " + ", ".join(problems)
            )
        else:
            agree += 1
    for key, other in mine.items():
        if key not in seen:
            mismatches.append(f"{other.session}: the second engine trades, the ledger doesn't")
    return {
        "compared": len(stored),
        "second_engine_trades": len(ref),
        "agree": agree,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:25],
    }


def seal(report: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in report.items() if k != "sha256"}
    return {**body, "sha256": sha256_text(canonical_json(body))}


_NUMBER = re.compile(r"(?<![\w.])[-−+]?\$?\d[\d,]*(?:\.\d+)?%?")


def numbers_in(text: str) -> list[str]:
    return [m.group(0) for m in _NUMBER.finditer(text)]


def _value(token: str) -> float | None:
    clean = (
        token.replace("−", "-").replace("$", "").replace(",", "").replace("%", "").replace("+", "")
    )
    try:
        return float(clean)
    except ValueError:
        return None


def narrative_guard(text: str, evidence: str) -> list[str]:
    """Numbers in ``text`` that don't appear in ``evidence`` (empty: the text may be shown)."""
    allowed = {v for t in numbers_in(evidence) if (v := _value(t)) is not None}
    allowed |= {abs(v) for v in allowed}
    unknown = []
    for token in numbers_in(text):
        value = _value(token)
        if value is None or value in allowed or abs(value) in allowed:
            continue
        if value.is_integer() and 0 <= value <= 12:
            continue  # small counting words ("3 tests", "2 variants") are fine
        unknown.append(token)
    return unknown


# ------------------------------------------------------------------------------------------
# checks: extraction boundaries, run evidence, forbidden claims

FORBIDDEN = re.compile(
    r"\b(verified (edge|alpha|strategy|profit)|validated edge|proven (edge|strategy|profitable)|"
    r"guaranteed|risk[- ]free|can'?t lose|will (make|earn|win) (money|profits?)|sure (thing|win)|"
    r"\d{1,3}\s?% (sure|certain|chance (it|this) (works|wins)))\b",
    re.IGNORECASE,
)


def forbidden(text: str) -> list[str]:
    """Overclaiming phrases a report may never contain (AG-02)."""
    return sorted({m.group(0).lower() for m in FORBIDDEN.finditer(text or "")})


def _check(
    checks: list[dict[str, Any]], id_: str, title: str, ok: bool, detail: str, warn: bool = False
) -> None:
    checks.append(
        {
            "id": id_,
            "title": title,
            "result": "PASS" if ok else ("WARN" if warn else "FAIL"),
            "detail": detail,
        }
    )


def boundary(
    source: dict[str, Any],
    claims: list[dict[str, Any]],
    blueprint: dict[str, Any],
    detected: dict[str, Any],
) -> dict[str, Any]:
    """Extraction and ambiguity boundaries, checked before anything is frozen or bought."""
    checks: list[dict[str, Any]] = []
    model_quotes = [c for c in claims if c["origin"] == "model" and c.get("quote")]
    _check(
        checks,
        "QUOTES_VERBATIM",
        "Every quote appears word for word in its segments",
        all(c["quote_verified"] for c in model_quotes),
        f"{len(model_quotes)} quote(s)",
    )
    perf_ids = {c["id"] for c in claims if c["kind"] == "PERFORMANCE_CLAIM"}
    cited = {
        cid for p in blueprint.get("provenance", {}).values() for cid in p.get("claim_ids", [])
    }
    _check(
        checks,
        "CLAIMS_ARE_NOT_RESULTS",
        "Performance claims define no rule and are no result",
        not (perf_ids & cited),
        f"{len(perf_ids)} performance claim(s), none used as a rule"
        if not (perf_ids & cited)
        else "a performance claim is cited as a rule",
    )
    flagged = [
        s["id"] for s in source.get("segments", []) if "instruction_like" in (s.get("flags") or [])
    ]
    recorded = {sid for c in claims if c["kind"] == "INSTRUCTION_TO_AI" for sid in c["segment_ids"]}
    _check(
        checks,
        "INSTRUCTIONS_NOT_OBEYED",
        "Instruction-like text is recorded, not followed",
        set(flagged) <= recorded,
        f"{len(flagged)} flagged segment(s)",
    )
    prov = blueprint.get("provenance", {})
    guessed = [
        p for p, e in prov.items() if e["class"] == "INFERRED_NONCRITICAL" and p.startswith("rule.")
    ]
    _check(
        checks,
        "NO_GUESSED_RULES",
        "Rule-defining fields come from the source, you, or a labelled default",
        not guessed,
        "none guessed" if not guessed else f"guessed: {guessed}",
    )
    listed = {u["feature"] for u in blueprint.get("unsupported", [])}
    missing = [key for key in detected.get("unsupported", {}) if _name(key) not in listed]
    _check(
        checks,
        "UNSUPPORTED_LISTED",
        "Concepts the rule language lacks are listed, not stretched",
        not missing,
        f"{len(listed)} listed",
    )
    blocking = blueprint.get("blocking", [])
    _check(
        checks,
        "DEFINITIONS_COMPLETE",
        "No open definition before data is requested",
        not blocking,
        "complete" if not blocking else f"open: {', '.join(blocking)}",
        warn=True,
    )
    return seal(
        {"kind": "boundary", "checks": checks, "passed": all(c["result"] != "FAIL" for c in checks)}
    )


def _name(key: str) -> str:
    from jarvis.quantlab.ideas.catalog import UNSUPPORTED_NAMES

    return UNSUPPORTED_NAMES[key]


def run_audit(materials: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The second engine against the stored ledger, plus the evidence checks around it."""
    from jarvis.quantlab.futures import validation

    run, spec, split = materials["run"], materials["spec"], materials["split"]
    summary = run["summary"] or {}
    checks: list[dict[str, Any]] = []
    eligible = split.insample + split.oos
    ref = recompute(
        spec, materials["bars"], materials["contracts"], eligible, context=materials["windows"]
    )
    stored = [r for r in rows if r["segment"] != "HOLDOUT"]
    tick = (
        next(iter(materials["contracts"].values())).tick_size_fixed / 1e9
        if materials["contracts"]
        else 0.25
    )
    cmp = compare(stored, ref, tick)
    _check(
        checks,
        "SECOND_ENGINE",
        "An independently written engine reproduces every trade",
        cmp["mismatch_count"] == 0,
        f"{cmp['agree']} of {cmp['compared']} trades agree; {cmp['mismatch_count']} difference(s)",
    )
    total = sum(Decimal(str(r["net"])) for r in stored)
    seg = summary.get("segments", {})
    expected = Decimal(str((seg.get("insample") or {}).get("net_pnl") or 0)) + Decimal(
        str((seg.get("oos") or {}).get("net_pnl") or 0)
    )
    embargo = sum(Decimal(str(r["net"])) for r in stored if r["segment"] == "EMBARGO")
    _check(
        checks,
        "LEDGER_SUMS",
        "Trade P&L adds up to the reported segment results",
        abs(total - embargo - expected) <= Decimal("0.02"),
        f"ledger {total - embargo:.2f} vs reported {expected:.2f}",
    )
    late = [r["number"] for r in stored if _ns(r["signal_known_at"]) > _ns(r["entry_time"])]
    _check(
        checks,
        "SIGNAL_BEFORE_FILL",
        "No fill uses information from after it",
        not late,
        "all fills after their signal" if not late else f"trades {late[:5]}",
    )
    holdout_rows = [r for r in rows if r["segment"] == "HOLDOUT"]
    sealed = not run.get("include_holdout")
    _check(
        checks,
        "HOLDOUT_STATE",
        "The final holdout is sealed (or opened once, on purpose)",
        (sealed and not holdout_rows) or (not sealed),
        "sealed — no holdout trade was computed" if sealed else "opened by explicit confirmation",
    )
    tests = summary.get("tests") or []
    again = validation.verdict(tests, fixture=bool(materials["fixture"]))
    stated = (summary.get("verdict") or {}).get("verdict")
    _check(
        checks,
        "VERDICT_RECOMPUTED",
        "The verdict follows from the tests by the fixed function",
        again["verdict"] == stated,
        f"{stated}",
    )
    selection = next((t for t in tests if t["id"] == "SELECTION_BIAS"), None)
    trials = (selection or {}).get("metric", {}).get("trials")
    _check(
        checks,
        "TRIALS_COUNTED",
        "Every evaluated variant is in the trial registry",
        bool(trials),
        f"{trials} variant(s) counted for the deflated Sharpe",
    )
    if materials["fixture"]:
        _check(
            checks,
            "SYNTHETIC_CAPPED",
            "Synthetic fixture data can't earn a positive verdict",
            stated == "INSUFFICIENT_EVIDENCE",
            "capped at INSUFFICIENT_EVIDENCE",
        )
    return seal(
        {
            "kind": "run_audit",
            "run_id": run["id"],
            "results_sha256": run.get("results_sha256"),
            "checks": checks,
            "second_engine": cmp,
            "passed": all(c["result"] != "FAIL" for c in checks),
            "limitation": LIMITATION,
        }
    )
