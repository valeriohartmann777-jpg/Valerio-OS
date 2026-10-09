"""The reference engine: small, sequential, deterministic, and checked.

One instrument, long-only, market orders, no leverage. A signal exists only
once its bar has closed (``bar_start + interval``); its order fills at the
*next* bar's open, adjusted by slippage against the trader. Cash is booked
exactly (``Decimal``): ``BUY: cash -= qty * fill + fee``, ``SELL: cash +=
qty * fill - fee``. An order the cash can't pay for is rejected, never
financed. A signal on the last bar has no next bar and is not executed; a
position still open at the end is marked at the last close and reported as
unrealised — never sold by assumption.

``audit`` re-derives every booking from the ledger independently and reports
each invariant; a single failure makes the experiment INVALID.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any, Literal

from jarvis.quantlab.data import Bar

ENGINE_NAME = "reference_equity_long_only"
ENGINE_VERSION = "r1.0.0"
PRECISION = 60  # decimal digits: sums and products of real prices stay exact

Side = Literal["BUY", "SELL"]
_ZERO = Decimal(0)
_BPS = Decimal(10_000)


class Cancelled(Exception):
    """The user cancelled the run."""


def dec(value: float | int) -> Decimal:
    """The shortest decimal that round-trips the float — what the CSV said."""
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)


@dataclass(frozen=True)
class Config:
    interval: timedelta
    size_units: int
    initial_cash: Decimal
    fee_fixed: Decimal
    fee_bps: Decimal
    slippage_bps: Decimal


# Signals -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Crossing:
    index: int
    side: Side
    fast: Decimal  # SMA values at the signal bar, for the record
    slow: Decimal


def sma_crossings(closes: Sequence[Decimal], fast: int, slow: int) -> list[Crossing]:
    """Strict crossings of SMA(fast) over/under SMA(slow), computed on closed bars only.

    BUY at bar t if fast[t-1] <= slow[t-1] and fast[t] > slow[t]; SELL if
    fast[t-1] >= slow[t-1] and fast[t] < slow[t]. Both averages need a full
    window at t-1 and t. Comparisons are exact (cross-multiplied window sums),
    so a tie stays a tie instead of becoming a float-rounding "cross"."""
    if fast < 1 or slow < 1:
        raise ValueError("windows must be positive")
    first = max(fast, slow) - 1  # first bar where both averages exist
    out: list[Crossing] = []
    with localcontext() as ctx:
        ctx.prec = PRECISION
        sum_fast = sum(closes[:fast], _ZERO)
        sum_slow = sum(closes[:slow], _ZERO)
        prev: int | None = None
        for t in range(len(closes)):
            if t >= fast:
                sum_fast += closes[t] - closes[t - fast]
            if t >= slow:
                sum_slow += closes[t] - closes[t - slow]
            if t < first:
                continue
            diff = sum_fast * slow - sum_slow * fast
            rel = (diff > 0) - (diff < 0)
            if prev is not None:
                if prev <= 0 < rel:
                    out.append(Crossing(t, "BUY", sum_fast / fast, sum_slow / slow))
                elif prev >= 0 > rel:
                    out.append(Crossing(t, "SELL", sum_fast / fast, sum_slow / slow))
            prev = rel
    return out


# Ledger --------------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalRecord:
    index: int
    bar_start: datetime
    available_at: datetime
    side: Side
    disposition: str  # ORDER | IGNORED_ALREADY_LONG | IGNORED_FLAT | NO_NEXT_BAR
    fast: Decimal | None = None
    slow: Decimal | None = None


@dataclass(frozen=True)
class Order:
    id: str
    side: Side
    quantity: int
    signal_index: int
    signal_bar_start: datetime
    signal_available_at: datetime
    status: str  # FILLED | REJECTED | NOT_EXECUTABLE
    reason: str | None = None
    fill_index: int | None = None
    fill_time: datetime | None = None
    reference_open: Decimal | None = None
    fill_price: Decimal | None = None
    notional: Decimal | None = None
    fee: Decimal | None = None
    slippage_cost: Decimal | None = None
    cash_before: Decimal | None = None
    cash_after: Decimal | None = None
    position_after: int | None = None


@dataclass(frozen=True)
class Trade:
    id: str
    status: str  # CLOSED | OPEN
    quantity: int
    entry_order: str
    entry_index: int
    entry_time: datetime
    entry_price: Decimal
    entry_fee: Decimal
    exit_order: str | None
    exit_index: int | None
    exit_time: datetime | None
    exit_price: Decimal | None  # the fill, or the mark for an open trade
    exit_fee: Decimal
    gross_pnl: Decimal  # realised, or unrealised at the mark
    fees: Decimal
    net_pnl: Decimal
    return_pct: Decimal  # net / (entry notional + entry fee)
    bars_held: int


@dataclass
class Result:
    bars: Sequence[Bar]
    config: Config
    signals: list[SignalRecord]
    orders: list[Order]
    trades: list[Trade]
    cash: list[Decimal]  # after each bar's fills
    position: list[int]
    equity: list[Decimal]  # cash + position × close, per bar
    closes: list[Decimal] = field(repr=False)
    opens: list[Decimal] = field(repr=False)

    @property
    def final_equity(self) -> Decimal:
        return self.equity[-1] if self.equity else self.config.initial_cash


def simulate(
    bars: Sequence[Bar],
    signals: Mapping[int, Side],
    config: Config,
    *,
    crossings: Mapping[int, Crossing] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Result:
    """Run the signals through the market, one bar at a time.

    ``signals`` maps a bar index to the side signalled at that bar's close. A
    BUY while long or a SELL while flat is recorded and ignored (no pyramiding,
    no shorts)."""
    opens = [dec(b.open) for b in bars]
    closes = [dec(b.close) for b in bars]
    qty = config.size_units
    slip = config.slippage_bps / _BPS
    records: list[SignalRecord] = []
    orders: list[Order] = []
    trades: list[Trade] = []
    cash_series: list[Decimal] = []
    position_series: list[int] = []
    equity_series: list[Decimal] = []
    cash = config.initial_cash
    position = 0
    pending: SignalRecord | None = None
    entry: Order | None = None
    last = len(bars) - 1
    with localcontext() as ctx:
        ctx.prec = PRECISION
        for i, bar in enumerate(bars):
            if cancelled is not None and i % 4096 == 0 and cancelled():
                raise Cancelled
            if pending is not None:
                order = _fill(pending, i, bar, opens[i], qty, slip, config, cash, position)
                order = replace(order, id=f"O{len(orders) + 1}")
                orders.append(order)
                if order.status == "FILLED":
                    assert order.cash_after is not None and order.position_after is not None
                    cash, position = order.cash_after, order.position_after
                    if order.side == "BUY":
                        entry = order
                    else:
                        assert entry is not None
                        trades.append(_closed_trade(len(trades) + 1, entry, order))
                        entry = None
                pending = None
            cash_series.append(cash)
            position_series.append(position)
            equity_series.append(cash + position * closes[i])

            side = signals.get(i)
            if side is None:
                continue
            cross = crossings.get(i) if crossings else None
            record = SignalRecord(
                index=i,
                bar_start=bar.ts,
                available_at=bar.ts + config.interval,
                side=side,
                disposition="ORDER",
                fast=cross.fast if cross else None,
                slow=cross.slow if cross else None,
            )
            if side == "BUY" and position > 0:
                record = _with(record, "IGNORED_ALREADY_LONG")
            elif side == "SELL" and position == 0:
                record = _with(record, "IGNORED_FLAT")
            elif i == last:
                record = _with(record, "NO_NEXT_BAR")
                orders.append(
                    Order(
                        id=f"O{len(orders) + 1}",
                        side=side,
                        quantity=qty,
                        signal_index=i,
                        signal_bar_start=bar.ts,
                        signal_available_at=record.available_at,
                        status="NOT_EXECUTABLE",
                        reason="NO_NEXT_BAR: the data ends before the next open",
                    )
                )
            else:
                pending = record
            records.append(record)

        if entry is not None and bars:
            trades.append(_open_trade(len(trades) + 1, entry, last, closes[last]))
    return Result(
        bars=bars,
        config=config,
        signals=records,
        orders=orders,
        trades=trades,
        cash=cash_series,
        position=position_series,
        equity=equity_series,
        closes=closes,
        opens=opens,
    )


def _with(record: SignalRecord, disposition: str) -> SignalRecord:
    return replace(record, disposition=disposition)


def _fill(
    signal: SignalRecord,
    index: int,
    bar: Bar,
    open_price: Decimal,
    qty: int,
    slip: Decimal,
    config: Config,
    cash: Decimal,
    position: int,
) -> Order:
    order = Order(
        id="",
        side=signal.side,
        quantity=qty,
        signal_index=signal.index,
        signal_bar_start=signal.bar_start,
        signal_available_at=signal.available_at,
        status="NOT_EXECUTABLE",
    )
    if bar.ts < signal.available_at:
        return replace(order, reason="BAR_BEFORE_SIGNAL_AVAILABLE")
    price = open_price * (1 + slip) if signal.side == "BUY" else open_price * (1 - slip)
    notional = price * qty
    fee = config.fee_fixed + notional * config.fee_bps / _BPS
    if signal.side == "BUY":
        need = notional + fee
        if need > cash:
            return replace(
                order,
                status="REJECTED",
                reason=f"INSUFFICIENT_CASH: needs {need:.2f}, has {cash:.2f}",
                fill_index=index,
                fill_time=bar.ts,
                reference_open=open_price,
                cash_before=cash,
                cash_after=cash,
                position_after=position,
            )
        after, held = cash - need, position + qty
    else:
        after, held = cash + notional - fee, position - qty
    return replace(
        order,
        status="FILLED",
        fill_index=index,
        fill_time=bar.ts,
        reference_open=open_price,
        fill_price=price,
        notional=notional,
        fee=fee,
        slippage_cost=abs(price - open_price) * qty,
        cash_before=cash,
        cash_after=after,
        position_after=held,
    )


def _closed_trade(number: int, entry: Order, exit_: Order) -> Trade:
    assert entry.fill_price is not None and entry.fee is not None and entry.notional is not None
    assert exit_.fill_price is not None and exit_.fee is not None
    assert entry.fill_index is not None and entry.fill_time is not None
    gross = (exit_.fill_price - entry.fill_price) * entry.quantity
    fees = entry.fee + exit_.fee
    net = gross - fees
    return Trade(
        id=f"T{number}",
        status="CLOSED",
        quantity=entry.quantity,
        entry_order=entry.id,
        entry_index=entry.fill_index,
        entry_time=entry.fill_time,
        entry_price=entry.fill_price,
        entry_fee=entry.fee,
        exit_order=exit_.id,
        exit_index=exit_.fill_index,
        exit_time=exit_.fill_time,
        exit_price=exit_.fill_price,
        exit_fee=exit_.fee,
        gross_pnl=gross,
        fees=fees,
        net_pnl=net,
        return_pct=net / (entry.notional + entry.fee),
        bars_held=(exit_.fill_index or 0) - entry.fill_index,
    )


def _open_trade(number: int, entry: Order, last: int, mark: Decimal) -> Trade:
    assert entry.fill_price is not None and entry.fee is not None and entry.notional is not None
    assert entry.fill_index is not None and entry.fill_time is not None
    gross = (mark - entry.fill_price) * entry.quantity
    net = gross - entry.fee
    return Trade(
        id=f"T{number}",
        status="OPEN",
        quantity=entry.quantity,
        entry_order=entry.id,
        entry_index=entry.fill_index,
        entry_time=entry.fill_time,
        entry_price=entry.fill_price,
        entry_fee=entry.fee,
        exit_order=None,
        exit_index=None,
        exit_time=None,
        exit_price=mark,
        exit_fee=_ZERO,
        gross_pnl=gross,
        fees=entry.fee,
        net_pnl=net,
        return_pct=net / (entry.notional + entry.fee),
        bars_held=last - entry.fill_index,
    )


def run(
    bars: Sequence[Bar],
    *,
    fast: int,
    slow: int,
    config: Config,
    cancelled: Callable[[], bool] | None = None,
) -> Result:
    """SMA crossover from closed bars, then the simulation."""
    crossings = sma_crossings([dec(b.close) for b in bars], fast, slow)
    by_index = {c.index: c for c in crossings}
    return simulate(
        bars,
        {c.index: c.side for c in crossings},
        config,
        crossings=by_index,
        cancelled=cancelled,
    )


# Audit ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    id: str
    passed: bool
    detail: str
    checked: int


def audit(result: Result) -> list[Check]:
    """Re-derive the books from the ledger and the bars, independently of the loop."""
    cfg, bars = result.config, result.bars
    filled = [o for o in result.orders if o.status == "FILLED"]
    checks: list[Check] = []

    def check(id_: str, failures: list[str], checked: int, ok: str) -> None:
        detail = ok if not failures else f"{len(failures)} violation(s): " + "; ".join(failures[:3])
        checks.append(Check(id_, not failures, detail, checked))

    with localcontext() as ctx:
        ctx.prec = PRECISION
        slip = cfg.slippage_bps / _BPS

        bad = [
            o.id
            for o in filled
            if o.fill_index is None
            or o.fill_index <= o.signal_index
            or o.fill_time is None
            or o.fill_time < o.signal_available_at
            or o.signal_available_at != bars[o.signal_index].ts + cfg.interval
        ]
        check(
            "causality.fill_after_signal",
            bad,
            len(filled),
            "Every fill is on a later bar than its signal and not before the signal bar closed.",
        )

        bad = []
        for o in filled:
            assert o.fill_index is not None and o.fill_price is not None
            open_ = dec(bars[o.fill_index].open)
            expected = open_ * (1 + slip) if o.side == "BUY" else open_ * (1 - slip)
            if o.fill_price != expected or o.fill_index != o.signal_index + 1:
                bad.append(o.id)
        check(
            "execution.next_open_price",
            bad,
            len(filled),
            "Every fill is the next bar's open, slippage applied against the trade.",
        )

        bad = []
        cash, position = cfg.initial_cash, 0
        for o in filled:
            assert o.fill_price is not None and o.fee is not None
            notional = o.fill_price * o.quantity
            fee = cfg.fee_fixed + notional * cfg.fee_bps / _BPS
            if o.side == "BUY":
                cash, position = cash - notional - fee, position + o.quantity
            else:
                cash, position = cash + notional - fee, position - o.quantity
            if o.fee != fee or o.cash_after != cash or o.position_after != position:
                bad.append(o.id)
            if cash < 0:
                bad.append(f"{o.id} negative cash")
            if position not in (0, cfg.size_units):
                bad.append(f"{o.id} position {position}")
        check(
            "accounting.cash_flows",
            bad,
            len(filled),
            "Cash and position replay exactly from the fills; cash never negative, "
            "no shorts, no pyramiding.",
        )

        bad = []
        cash, position, cursor = cfg.initial_cash, 0, 0
        for i in range(len(bars)):
            while cursor < len(filled) and filled[cursor].fill_index == i:
                after, held = filled[cursor].cash_after, filled[cursor].position_after
                assert after is not None and held is not None
                cash, position = after, held
                cursor += 1
            expected = cash + position * dec(bars[i].close)
            if result.equity[i] != expected or result.cash[i] != cash:
                bad.append(f"bar {i}")
        check(
            "accounting.equity_identity",
            bad,
            len(bars),
            "Equity = cash + shares × close on every bar.",
        )

        realised = sum((t.net_pnl for t in result.trades if t.status == "CLOSED"), _ZERO)
        open_mtm = sum((t.net_pnl for t in result.trades if t.status == "OPEN"), _ZERO)
        total = result.final_equity - cfg.initial_cash
        check(
            "accounting.pnl_identity",
            [] if total == realised + open_mtm else [f"{total} != {realised} + {open_mtm}"],
            len(result.trades),
            "Final equity − initial cash = realised net P&L + open position marked to market.",
        )

        by_id = {o.id: o for o in result.orders}
        bad = []
        for t in result.trades:
            entry = by_id.get(t.entry_order)
            if entry is None or entry.side != "BUY" or entry.status != "FILLED":
                bad.append(t.id)
            if t.status == "CLOSED":
                exit_ = by_id.get(t.exit_order or "")
                if (
                    exit_ is None
                    or exit_.side != "SELL"
                    or exit_.status != "FILLED"
                    or (exit_.fill_index or 0) <= t.entry_index
                ):
                    bad.append(t.id)
            elif t.exit_order is not None or t is not result.trades[-1]:
                bad.append(f"{t.id} open trade not last")
        check(
            "ledger.matched_round_trips",
            bad,
            len(result.trades),
            "Every trade has a filled entry and (if closed) a later filled exit.",
        )

        fill_bars = [o.fill_index for o in filled]
        bad = [str(i) for i in set(fill_bars) if fill_bars.count(i) > 1]
        bad += [o.id for o in filled if o.fill_index is not None and o.fill_index > len(bars) - 1]
        check(
            "execution.no_phantom_fills",
            bad,
            len(filled),
            "At most one fill per bar and no fill beyond the data.",
        )
    return checks


# Metrics -------------------------------------------------------------------------------


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def segment_metrics(result: Result, start: int, end: int) -> dict[str, Any]:
    """Metrics for bars ``start..end`` (inclusive), from the one continuous run.

    The segment starts from the equity at the previous bar's close (the
    initial cash for the first bar). Trades belong to the segment they were
    entered in; a position carried in from before is reported separately."""
    cfg = result.config
    start_equity = cfg.initial_cash if start == 0 else result.equity[start - 1]
    end_equity = result.equity[end]
    net = end_equity - start_equity
    peak = start_equity
    max_dd_abs = _ZERO
    max_dd_pct = _ZERO
    trough_at = None
    with localcontext() as ctx:
        ctx.prec = PRECISION
        for i in range(start, end + 1):
            value = result.equity[i]
            if value > peak:
                peak = value
            dd = peak - value
            if dd > max_dd_abs:
                max_dd_abs, trough_at = dd, i
            if peak > 0 and dd / peak > max_dd_pct:
                max_dd_pct = dd / peak
        trades = [t for t in result.trades if start <= t.entry_index <= end]
        closed = [t for t in trades if t.status == "CLOSED"]
        carried = [
            t
            for t in result.trades
            if t.entry_index < start and (t.exit_index is None or t.exit_index >= start)
        ]
        wins = [t for t in closed if t.net_pnl > 0]
        losses = [t for t in closed if t.net_pnl < 0]
        gross_win = sum((t.net_pnl for t in wins), _ZERO)
        gross_loss = -sum((t.net_pnl for t in losses), _ZERO)
        filled = [
            o
            for o in result.orders
            if o.status == "FILLED" and o.fill_index is not None and start <= o.fill_index <= end
        ]
        fees = sum((o.fee or _ZERO for o in filled), _ZERO)
        slippage = sum((o.slippage_cost or _ZERO for o in filled), _ZERO)
        exposed = sum(1 for i in range(start, end + 1) if result.position[i] > 0)
        bars = end - start + 1
        reference = result.closes[start - 1] if start > 0 else result.opens[0]
        benchmark = result.closes[end] / reference - 1
        return {
            "bars": bars,
            "start_utc": _iso(result.bars[start].ts),
            "end_utc": _iso(result.bars[end].ts),
            "start_equity": _f(start_equity),
            "end_equity": _f(end_equity),
            "net_pnl": _f(net),
            "return_pct": _f(net / start_equity) if start_equity else None,
            "return_denominator": "equity at segment start" if start else "initial cash",
            "fees": _f(fees),
            "slippage_cost": _f(slippage),
            "gross_pnl": _f(net + fees),
            "max_drawdown_abs": _f(max_dd_abs),
            "max_drawdown_pct": _f(max_dd_pct),
            "max_drawdown_at_utc": _iso(result.bars[trough_at].ts)
            if trough_at is not None
            else None,
            "trades_entered": len(trades),
            "trades_closed": len(closed),
            "trades_open": len(trades) - len(closed),
            "trades_carried_in": len(carried),
            "win_rate": _f(Decimal(len(wins)) / len(closed)) if closed else None,
            "avg_net_per_closed_trade": _f(sum((t.net_pnl for t in closed), _ZERO) / len(closed))
            if closed
            else None,
            "profit_factor": _f(gross_win / gross_loss) if gross_loss > 0 else None,
            "largest_win": _f(max((t.net_pnl for t in wins), default=None)),
            "largest_loss": _f(min((t.net_pnl for t in losses), default=None)),
            "exposure": exposed / bars if bars else None,
            "benchmark_price_return": _f(benchmark),
            "benchmark_note": "buy & hold price return over the same bars; no costs, "
            "not exposure-matched",
        }


def full_metrics(result: Result) -> dict[str, Any]:
    cfg = result.config
    overall = segment_metrics(result, 0, len(result.bars) - 1)
    closed = [t for t in result.trades if t.status == "CLOSED"]
    open_ = [t for t in result.trades if t.status == "OPEN"]
    counts: dict[str, int] = {}
    for o in result.orders:
        counts[o.status] = counts.get(o.status, 0) + 1
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return {
            **overall,
            "initial_cash": _f(cfg.initial_cash),
            "final_cash": _f(result.cash[-1]) if result.cash else _f(cfg.initial_cash),
            "final_position": result.position[-1] if result.position else 0,
            "final_equity": _f(result.final_equity),
            "realized_net_pnl": _f(sum((t.net_pnl for t in closed), _ZERO)),
            "realized_gross_pnl": _f(sum((t.gross_pnl for t in closed), _ZERO)),
            "unrealized_gross_pnl": _f(sum((t.gross_pnl for t in open_), _ZERO)),
            "open_position_entry_fees": _f(sum((t.entry_fee for t in open_), _ZERO)),
            "mark_price": _f(open_[0].exit_price) if open_ else None,
            "orders_filled": counts.get("FILLED", 0),
            "orders_rejected": counts.get("REJECTED", 0),
            "orders_not_executable": counts.get("NOT_EXECUTABLE", 0),
            "signals": len(result.signals),
            "sharpe": {
                "status": "NOT_RUN",
                "reason": "Annualising a Sharpe ratio needs a defensible sampling frequency and "
                "serial-dependence assumption; R1 doesn't make one.",
            },
        }


def split_index(bars: int, oos_fraction: float) -> int:
    """First out-of-sample bar: the last ceil(n × fraction) bars are OOS."""
    oos = max(1, math.ceil(bars * oos_fraction))
    return bars - oos


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# Serialisation -------------------------------------------------------------------------


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def order_rows(result: Result) -> list[dict[str, Any]]:
    return [
        {
            "id": o.id,
            "side": o.side,
            "quantity": o.quantity,
            "status": o.status,
            "reason": o.reason,
            "signal_index": o.signal_index,
            "signal_bar_start_utc": _iso(o.signal_bar_start),
            "signal_available_at_utc": _iso(o.signal_available_at),
            "fill_index": o.fill_index,
            "fill_time_utc": _iso(o.fill_time) if o.fill_time else None,
            "reference_open": _s(o.reference_open),
            "fill_price": _s(o.fill_price),
            "notional": _s(o.notional),
            "fee": _s(o.fee),
            "slippage_cost": _s(o.slippage_cost),
            "cash_before": _s(o.cash_before),
            "cash_after": _s(o.cash_after),
            "position_after": o.position_after,
        }
        for o in result.orders
    ]


def trade_rows(result: Result) -> list[dict[str, Any]]:
    return [
        {
            "id": t.id,
            "status": t.status,
            "quantity": t.quantity,
            "entry_order": t.entry_order,
            "entry_index": t.entry_index,
            "entry_time_utc": _iso(t.entry_time),
            "entry_price": _s(t.entry_price),
            "entry_fee": _s(t.entry_fee),
            "exit_order": t.exit_order,
            "exit_index": t.exit_index,
            "exit_time_utc": _iso(t.exit_time) if t.exit_time else None,
            "exit_price": _s(t.exit_price),
            "exit_price_is_mark": t.status == "OPEN",
            "exit_fee": _s(t.exit_fee),
            "gross_pnl": _s(t.gross_pnl),
            "fees": _s(t.fees),
            "net_pnl": _s(t.net_pnl),
            "return_pct": _s(t.return_pct),
            "bars_held": t.bars_held,
        }
        for t in result.trades
    ]


def signal_rows(result: Result) -> list[dict[str, Any]]:
    return [
        {
            "index": s.index,
            "bar_start_utc": _iso(s.bar_start),
            "available_at_utc": _iso(s.available_at),
            "side": s.side,
            "disposition": s.disposition,
            "sma_fast": _s(s.fast),
            "sma_slow": _s(s.slow),
        }
        for s in result.signals
    ]


def equity_rows(result: Result, cutoff: int) -> dict[str, list[Any]]:
    """Column-wise per-bar series (exact values as strings)."""
    peak = result.config.initial_cash
    drawdown: list[str | None] = []
    for value in result.equity:
        peak = max(peak, value)
        drawdown.append(_s((value - peak) / peak) if peak > 0 else None)
    return {
        "ts_utc": [_iso(b.ts) for b in result.bars],
        "close": [_s(c) for c in result.closes],
        "cash": [_s(c) for c in result.cash],
        "position": list(result.position),
        "equity": [_s(e) for e in result.equity],
        "drawdown": drawdown,
        "segment": ["train" if i < cutoff else "oos" for i in range(len(result.bars))],
    }
