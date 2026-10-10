"""Event-driven intraday futures simulator on 1-minute OHLCV bars.

Phases per bar: market event → observable state → signal eligibility → order
submission → fill simulation → position/cash accounting → risk exits → next bar.

Ground rules (each is tested):

* A bar stamped ``t`` (its start, Databento convention) is complete at
  ``t + 1 minute``; nothing it contains is used before then.
* Prices are integer ticks of the traded contract; money is ``Decimal``.
  P&L = ticks × tick value × contracts, so NQ and MNQ can't be confused.
* Market orders fill at the next eligible bar's open plus slippage. Stop
  orders fill at their price, or at the open if price gapped through, plus
  slippage. Targets (limit orders) fill at their price, only when price
  trades through it unless ``touch`` is chosen (at the open if it opened beyond).
* Within one minute the order of high and low is unknown. When a stop and a
  target are both inside a bar, ``conservative`` mode assumes the stop and
  ``optimistic`` mode the target; the run reports both. Breakouts triggering
  both directions inside one bar are excluded and counted, never guessed.
* Positions are flat at every session end, so no position spans a contract
  roll; a session whose bars span two contracts is skipped and recorded.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, Decimal
from typing import Any, Literal

import numpy as np

from jarvis.quantlab.futures.contracts import ContractSpec
from jarvis.quantlab.futures.sessions import Window
from jarvis.quantlab.futures.spec import (
    FuturesSpec,
    LevelSweepReclaim,
    MovingAverageCrossover,
    OpeningRangeBreakout,
    OpeningRangeRetest,
    clock,
)

ENGINE_NAME = "quantlab-futures-bar-engine"
ENGINE_VERSION = "1.0.0"
NS = 1_000_000_000
MINUTE = 60 * NS
Mode = Literal["conservative", "optimistic"]


class Cancelled(Exception):
    pass


@dataclass
class Bars:
    ts: np.ndarray
    iid: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    @classmethod
    def from_table(cls, table: Any) -> Bars:
        def col(name: str) -> np.ndarray:
            return np.asarray(table.column(name).to_numpy(), dtype=np.int64)

        return cls(
            col("ts_event"), col("instrument_id"), col("open"), col("high"), col("low"),
            col("close"),
        )  # fmt: skip

    def __len__(self) -> int:
        return int(self.ts.size)


@dataclass
class Order:
    id: int
    session: str
    created_ns: int
    kind: str  # STOP_ENTRY, MARKET_ENTRY, STOP_LOSS, TARGET, MARKET_EXIT
    side: int  # +1 buy, -1 sell
    price_ticks: int | None
    status: str = "WORKING"  # FILLED, CANCELED, EXPIRED
    closed_ns: int | None = None
    note: str = ""


@dataclass
class Fill:
    order_id: int
    session: str
    bar_ts: int
    known_at_ns: int  # when the information that caused the order was available
    instrument_id: int
    side: int
    contracts: int
    ticks: int
    reference_ticks: int  # price before slippage
    slippage_ticks: int
    fee: Decimal
    reason: str
    ambiguous: bool = False


@dataclass
class Trade:
    number: int
    session: str
    instrument_id: int
    raw_symbol: str
    direction: int
    contracts: int
    entry_bar_ts: int
    entry_ticks: int
    exit_bar_ts: int
    exit_ticks: int
    exit_reason: str
    gross: Decimal
    fees: Decimal
    net: Decimal
    slippage_cost: Decimal
    tick_size_fixed: int
    tick_value: Decimal
    stop_ticks: int | None
    target_ticks: int | None
    risk_ticks: int | None
    mae_ticks: int
    mfe_ticks: int
    bars_held: int
    signal_known_at: int
    ambiguous: bool

    @property
    def r_multiple(self) -> Decimal | None:
        if not self.risk_ticks:
            return None
        moved = (self.exit_ticks - self.entry_ticks) * self.direction
        return Decimal(moved) / Decimal(self.risk_ticks)


@dataclass
class SessionLog:
    label: str
    status: (
        str  # TRADED, NO_SIGNAL, NO_DATA, NO_RANGE, SKIPPED_ROLL, NO_SPEC, EXPIRED,
        # AMBIGUOUS_ENTRY, FILTERED, NO_LEVEL, NO_PRIOR
    )
    instrument_id: int | None = None
    raw_symbol: str | None = None
    range_high: int | None = None
    range_low: int | None = None
    trades: int = 0
    net: Decimal = Decimal(0)
    window_minutes: int = 0
    minutes_in_market: int = 0
    early_close: bool = False
    note: str = ""


@dataclass
class Result:
    spec_sha256: str
    mode: Mode
    cost_multiplier: float
    capital: Decimal
    trades: list[Trade] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    sessions: list[SessionLog] = field(default_factory=list)
    equity_ts: list[int] = field(default_factory=list)
    equity: list[Decimal] = field(default_factory=list)
    fee_per_side: Decimal = Decimal(0)
    slippage_ticks: int = 0
    contracts_used: dict[int, ContractSpec] = field(default_factory=dict)

    @property
    def final_equity(self) -> Decimal:
        return self.capital + sum((t.net for t in self.trades), Decimal(0))

    @property
    def ambiguous_entries(self) -> int:
        return sum(1 for s in self.sessions if s.status == "AMBIGUOUS_ENTRY")

    @property
    def ambiguous_exits(self) -> int:
        return sum(1 for t in self.trades if t.ambiguous)


@dataclass(frozen=True)
class PriorSession:
    """The previous window of the same contract: its high, low and last close (fixed prices)."""

    instrument_id: int
    high: int
    low: int
    close: int


@dataclass
class _Position:
    direction: int
    entry_ticks: int
    entry_ts: int
    entry_index: int
    known_at: int
    stop: int | None
    target: int | None
    risk: int | None
    entry_slippage: int
    ambiguous: bool = False
    mae: int = 0
    mfe: int = 0


class _Session:
    """One window of one trading day: orders, fills and at most a few trades."""

    def __init__(self, sim: _Simulator, window: Window, lo: int, hi: int) -> None:
        self.sim = sim
        self.w = window
        self.label = window.label.isoformat()
        b = sim.bars
        self.ts = b.ts[lo:hi].tolist()
        self.iid = b.iid[lo:hi].tolist()
        self.raw = (b.open[lo:hi], b.high[lo:hi], b.low[lo:hi], b.close[lo:hi])
        self.log = SessionLog(
            label=self.label,
            status="NO_SIGNAL",
            window_minutes=(window.end_ns - window.start_ns) // MINUTE,
            early_close=window.early_close,
        )
        self.pos: _Position | None = None
        self.contract: ContractSpec | None = None
        spec = sim.spec
        self.entry_from = window.start_ns
        if spec.filters.entry_after:
            start, after = clock(spec.session.start), clock(spec.filters.entry_after)
            offset = (after.hour * 60 + after.minute) - (start.hour * 60 + start.minute)
            self.entry_from = window.start_ns + offset * MINUTE
        self.prior = sim.prior

    # setup -------------------------------------------------------------------------------

    def prepare(self) -> bool:
        if not self.ts:
            self.log.status = "NO_DATA"
            return False
        ids = set(self.iid)
        if len(ids) > 1:
            self.log.status = "SKIPPED_ROLL"
            self.log.note = f"bars from {len(ids)} contracts in one window ({sorted(ids)})"
            return False
        instrument = self.iid[0]
        self.log.instrument_id = instrument
        contract = self.sim.contracts.get(instrument)
        if contract is None:
            self.log.status = "NO_SPEC"
            return False
        self.log.raw_symbol = contract.raw_symbol
        if contract.expiration_ns is not None and self.w.start_ns >= contract.expiration_ns:
            self.log.status = "EXPIRED"
            return False
        tick = contract.tick_size_fixed
        o, h, lo, c = self.raw
        if bool(np.any((o % tick) | (h % tick) | (lo % tick) | (c % tick))):
            raise ValueError(f"{self.label}: prices off the {contract.raw_symbol} tick grid")
        self.o, self.h, self.l, self.c = (
            (o // tick).tolist(), (h // tick).tolist(), (lo // tick).tolist(), (c // tick).tolist(),
        )  # fmt: skip
        self.contract = contract
        self.sim.result.contracts_used[instrument] = contract
        return True

    # orders and fills --------------------------------------------------------------------

    def order(self, created: int, kind: str, side: int, price: int | None, note: str = "") -> Order:
        sim = self.sim
        record = Order(
            len(sim.result.orders) + 1, self.label, created, kind, side, price, note=note
        )
        sim.result.orders.append(record)
        return record

    def close_order(self, record: Order, status: str, at: int) -> None:
        if record.status == "WORKING":
            record.status, record.closed_ns = status, at

    def fill(
        self, record: Order, j: int, ticks: int, reference: int, reason: str, known_at: int,
        ambiguous: bool = False,
    ) -> Fill:  # fmt: skip
        assert self.contract is not None
        sim = self.sim
        record.status, record.closed_ns = "FILLED", self.ts[j]
        fee = sim.fee_per_side * sim.contracts_n
        result = Fill(
            order_id=record.id,
            session=self.label,
            bar_ts=self.ts[j],
            known_at_ns=known_at,
            instrument_id=self.contract.instrument_id,
            side=record.side,
            contracts=sim.contracts_n,
            ticks=ticks,
            reference_ticks=reference,
            slippage_ticks=abs(ticks - reference),
            fee=fee,
            reason=reason,
            ambiguous=ambiguous,
        )
        sim.result.fills.append(result)
        sim.cash -= fee
        return result

    # position lifecycle ------------------------------------------------------------------

    def open(self, j: int, direction: int, reference: int, known_at: int,
             record: Order | None, intrabar: bool,
             range_hl: tuple[int, int] | None = None,
             extreme: int | None = None) -> None:  # fmt: skip
        """Enter at ``reference`` (+ slippage) on bar j; fills ``record`` or a new market order."""
        sim = self.sim
        slip = sim.slippage
        ticks = reference + direction * slip
        if record is None:
            record = self.order(known_at, "MARKET_ENTRY", direction, None)
        reason = "ENTRY_STOP" if record.kind == "STOP_ENTRY" else "ENTRY_MARKET"
        self.fill(record, j, ticks, reference, reason, known_at)
        spec = sim.spec
        stop: int | None = None
        stop_rule = spec.exits.stop
        if stop_rule.type == "range_opposite" and range_hl is not None:
            buffer = spec.rule.buffer_ticks if isinstance(spec.rule, OpeningRangeBreakout) else 0
            hi, lo = range_hl
            stop = lo - buffer if direction > 0 else hi + buffer
        elif stop_rule.type == "setup_extreme" and extreme is not None and stop_rule.ticks:
            stop = extreme - direction * stop_rule.ticks
        elif stop_rule.type == "ticks" and stop_rule.ticks:
            stop = ticks - direction * stop_rule.ticks
        risk = (ticks - stop) * direction if stop is not None else None
        if risk is not None and risk <= 0:
            # Filled beyond the stop (gap): the position is stopped on the next check.
            risk = None
        target: int | None = None
        target_rule = spec.exits.target
        if target_rule.type == "r_multiple" and risk:
            move = (Decimal(str(target_rule.value)) * risk).to_integral_value(ROUND_CEILING)
            target = ticks + direction * int(move)
        elif target_rule.type == "ticks" and target_rule.value:
            target = ticks + direction * math.ceil(target_rule.value)
        self.pos = _Position(direction, ticks, self.ts[j], j, known_at, stop, target, risk, slip)
        self.stop_order = (
            self.order(self.ts[j], "STOP_LOSS", -direction, stop) if stop is not None else None
        )
        self.target_order = (
            self.order(self.ts[j], "TARGET", -direction, target) if target is not None else None
        )

    def close(self, j: int, reference: int, reason: str, ambiguous: bool, at_ts: int | None = None,
              order_kind: str | None = None) -> None:  # fmt: skip
        assert self.pos is not None and self.contract is not None
        sim, pos = self.sim, self.pos
        slip = 0 if reason == "TARGET" else sim.slippage
        ticks = reference - pos.direction * slip
        if reason == "STOP" and self.stop_order is not None:
            record = self.stop_order
        elif reason == "TARGET" and self.target_order is not None:
            record = self.target_order
        else:
            record = self.order(
                at_ts or self.ts[j], order_kind or "MARKET_EXIT", -pos.direction, None
            )
        exit_fill = self.fill(record, j, ticks, reference, reason, at_ts or self.ts[j], ambiguous)
        for other in (self.stop_order, self.target_order):
            if other is not None and other is not record:
                self.close_order(other, "CANCELED", self.ts[j])
        contract = self.contract
        moved = (ticks - pos.entry_ticks) * pos.direction
        gross = Decimal(moved) * contract.tick_value * sim.contracts_n
        fees = exit_fill.fee * 2
        slip_ticks = pos.entry_slippage + exit_fill.slippage_ticks
        trade = Trade(
            number=len(sim.result.trades) + 1,
            session=self.label,
            instrument_id=contract.instrument_id,
            raw_symbol=contract.raw_symbol,
            direction=pos.direction,
            contracts=sim.contracts_n,
            entry_bar_ts=pos.entry_ts,
            entry_ticks=pos.entry_ticks,
            exit_bar_ts=self.ts[j],
            exit_ticks=ticks,
            exit_reason=reason,
            gross=gross,
            fees=fees,
            net=gross - fees,
            slippage_cost=Decimal(slip_ticks) * contract.tick_value * sim.contracts_n,
            tick_size_fixed=contract.tick_size_fixed,
            tick_value=contract.tick_value,
            stop_ticks=pos.stop,
            target_ticks=pos.target,
            risk_ticks=pos.risk,
            mae_ticks=pos.mae,
            mfe_ticks=pos.mfe,
            bars_held=j - pos.entry_index + 1,
            signal_known_at=pos.known_at,
            ambiguous=pos.ambiguous or ambiguous,
        )
        sim.cash += gross
        sim.result.trades.append(trade)
        self.log.trades += 1
        self.log.net += trade.net
        self.log.minutes_in_market += (self.ts[j] - pos.entry_ts) // MINUTE + 1
        self.log.status = "TRADED"
        self.pos = None

    def track(self, j: int) -> None:
        pos = self.pos
        if pos is None:
            return
        if pos.direction > 0:
            pos.mae = max(pos.mae, pos.entry_ticks - self.l[j])
            pos.mfe = max(pos.mfe, self.h[j] - pos.entry_ticks)
        else:
            pos.mae = max(pos.mae, self.h[j] - pos.entry_ticks)
            pos.mfe = max(pos.mfe, pos.entry_ticks - self.l[j])

    def target_hit(self, price: int) -> bool:
        pos = self.pos
        assert pos is not None and pos.target is not None
        through = self.sim.spec.execution.limit_fill == "trade_through"
        if pos.direction > 0:
            return price > pos.target if through else price >= pos.target
        return price < pos.target if through else price <= pos.target

    def exits(self, j: int, intrabar_entry: bool) -> bool:
        """Apply time, stop and target exits on bar j. Returns True when flat."""
        pos = self.pos
        assert pos is not None
        optimistic = self.sim.mode == "optimistic"
        o, h, low, c = self.o[j], self.h[j], self.l[j], self.c[j]
        self.track(j)
        if not intrabar_entry and self.ts[j] >= self.w.flatten_ns:
            self.close(j, o, "TIME_EXIT", False)
            return True
        d = pos.direction
        stop, target = pos.stop, pos.target
        if intrabar_entry:
            # Entered somewhere inside this bar: what happened afterwards is unknown.
            stop_inside = stop is not None and (low <= stop if d > 0 else h >= stop)
            if stop_inside and not optimistic:
                pos.ambiguous = True
                assert stop is not None
                self.close(j, stop, "STOP", True)
                return True
            if optimistic and target is not None and (c > target if d > 0 else c < target):
                pos.ambiguous = True
                self.close(j, target, "TARGET", True)
                return True
            return False
        if stop is not None and (o <= stop if d > 0 else o >= stop):
            self.close(j, o, "STOP", False)  # gapped through the stop: filled at the open
            return True
        if target is not None and self.target_hit(o):
            self.close(j, o, "TARGET", False)  # opened beyond the limit: filled at the open
            return True
        stop_hit = stop is not None and (low <= stop if d > 0 else h >= stop)
        target_hit = target is not None and self.target_hit(h if d > 0 else low)
        if stop_hit and target_hit:
            assert stop is not None and target is not None
            if optimistic:
                self.close(j, target, "TARGET", True)
            else:
                self.close(j, stop, "STOP", True)
            return True
        if stop_hit:
            assert stop is not None
            self.close(j, stop, "STOP", False)
            return True
        if target_hit:
            assert target is not None
            self.close(j, target, "TARGET", False)
            return True
        return False

    def finish(self, last: int) -> None:
        """No bar at or after the flatten time: exit at the last bar's close (flagged)."""
        if self.pos is None:
            return
        self.close(last, self.c[last], "LAST_BAR_CLOSE", False, at_ts=self.ts[last] + MINUTE)
        result = self.sim.result
        if self.sim.record_equity:
            # The bar's mark was taken with the position still open: realized value replaces it.
            if result.equity_ts and result.equity_ts[-1] == self.ts[last] + MINUTE:
                result.equity[-1] = self.sim.cash
            else:
                result.equity_ts.append(self.ts[last] + MINUTE)
                result.equity.append(self.sim.cash)

    def mark(self, j: int) -> None:
        sim = self.sim
        if not sim.record_equity:
            return
        equity = sim.cash
        if self.pos is not None and self.contract is not None:
            moved = (self.c[j] - self.pos.entry_ticks) * self.pos.direction
            equity += Decimal(moved) * self.contract.tick_value * sim.contracts_n
        sim.result.equity_ts.append(self.ts[j] + MINUTE)
        sim.result.equity.append(equity)

    # rules -------------------------------------------------------------------------------

    def mark_from(self, start: int) -> None:
        for j in range(start, len(self.ts)):
            self.mark(j)

    def prior_ticks(self) -> tuple[int, int, int] | None:
        """(high, low, close) of the previous window in this contract's ticks, if comparable."""
        prior, contract = self.prior, self.contract
        if prior is None or contract is None or prior.instrument_id != contract.instrument_id:
            return None
        tick = contract.tick_size_fixed
        return prior.high // tick, prior.low // tick, prior.close // tick

    def range_ok(self, hi: int, lo: int) -> bool:
        f = self.sim.spec.filters
        width = hi - lo
        if (f.min_range_ticks and width < f.min_range_ticks) or (
            f.max_range_ticks and width > f.max_range_ticks
        ):
            self.log.status = "FILTERED"
            self.log.note = f"opening range {width} ticks wide, outside the filter"
            return False
        return True

    def bias_ok(self, direction: int, reference: int) -> bool:
        if not self.sim.spec.filters.prior_close_bias:
            return True
        prior = self.prior_ticks()
        if prior is None:
            return False
        return reference > prior[2] if direction > 0 else reference < prior[2]

    def day_ok(self) -> bool:
        f = self.sim.spec.filters
        if not (f.max_gap_ticks or f.prior_close_bias):
            return True
        prior = self.prior_ticks()
        if prior is None:
            self.log.status = "NO_PRIOR"
            self.log.note = "the filter needs the prior session of the same contract"
            return False
        if f.max_gap_ticks and abs(self.o[0] - prior[2]) > f.max_gap_ticks:
            self.log.status = "FILTERED"
            self.log.note = f"opened {abs(self.o[0] - prior[2])} ticks from the prior close"
            return False
        return True

    def run(self) -> None:
        rule = self.sim.spec.rule
        if not self.day_ok():
            self.mark_from(0)
        elif isinstance(rule, OpeningRangeBreakout):
            self.opening_range(rule)
        elif isinstance(rule, LevelSweepReclaim):
            self.sweep(rule)
        elif isinstance(rule, OpeningRangeRetest):
            self.retest(rule)
        else:
            self.crossover(rule)
        if self.sim.record_equity and self.ts:
            last = len(self.ts) - 1
            if (
                not self.sim.result.equity_ts
                or self.sim.result.equity_ts[-1] != self.ts[last] + MINUTE
            ):
                self.mark(last)

    def opening_range(self, rule: OpeningRangeBreakout) -> None:
        w, n = self.w, len(self.ts)
        range_end = w.start_ns + rule.range_minutes * MINUTE
        in_range = [j for j in range(n) if self.ts[j] < range_end]
        if not in_range:
            self.log.status = "NO_RANGE"
            return
        hi = max(self.h[j] for j in in_range)
        lo = min(self.l[j] for j in in_range)
        self.log.range_high, self.log.range_low = hi, lo
        for j in in_range:
            self.mark(j)
        first = in_range[-1] + 1
        if not self.range_ok(hi, lo):
            self.mark_from(first)
            return
        buy, sell = hi + rule.buffer_ticks, lo - rule.buffer_ticks
        longs, shorts = rule.direction in ("long", "both"), rule.direction in ("short", "both")
        active = max(range_end + self.sim.spec.execution.latency_bars * MINUTE, self.entry_from)
        if rule.entry == "stop_through_range":
            longs = longs and self.bias_ok(1, buy)
            shorts = shorts and self.bias_ok(-1, sell)
            pending = [
                self.order(range_end, "STOP_ENTRY", +1, buy) if longs else None,
                self.order(range_end, "STOP_ENTRY", -1, sell) if shorts else None,
            ]
            j = first
            while j < n:
                if self.ts[j] < active:
                    self.mark(j)
                    j += 1
                    continue
                if self.ts[j] >= min(w.entry_cutoff_ns, w.flatten_ns):
                    break
                o = self.o[j]
                up = longs and self.h[j] >= buy
                down = shorts and self.l[j] <= sell
                if up and down:
                    if o >= buy:
                        down = False
                    elif o <= sell:
                        up = False
                    else:
                        self.log.status = "AMBIGUOUS_ENTRY"
                        self.log.note = "both breakout stops inside one 1-minute bar"
                        for p in pending:
                            if p is not None:
                                self.close_order(p, "CANCELED", self.ts[j])
                        return
                if up or down:
                    direction = 1 if up else -1
                    level = buy if up else sell
                    gapped = o >= buy if up else o <= sell
                    reference = o if gapped else level
                    triggered = pending[0] if up else pending[1]
                    for p in pending:
                        if p is not None and p is not triggered:
                            self.close_order(p, "CANCELED", self.ts[j])  # one-cancels-other
                    self.open(j, direction, reference, range_end, triggered, not gapped, (hi, lo))
                    if self.exits(j, intrabar_entry=not gapped):
                        self.mark(j)
                        return
                    self.mark(j)
                    self.manage(j + 1)
                    return
                self.mark(j)
                j += 1
            for p in pending:
                if p is not None:
                    self.close_order(p, "EXPIRED", w.entry_cutoff_ns)
            return
        # close_beyond_range: a completed bar beyond the range → market order next bar
        for j in range(first, n):
            self.mark(j)
            known = self.ts[j] + MINUTE
            if known < self.entry_from:
                continue
            long_signal = longs and self.c[j] > buy and self.bias_ok(1, self.c[j])
            short_signal = shorts and self.c[j] < sell and self.bias_ok(-1, self.c[j])
            if not (long_signal or short_signal):
                continue
            eligible = known + self.sim.spec.execution.latency_bars * MINUTE
            k = next((i for i in range(j + 1, n) if self.ts[i] >= eligible), None)
            if k is None or self.ts[k] >= min(w.entry_cutoff_ns, w.flatten_ns):
                self.log.note = "signal too late to execute before the entry cutoff"
                return
            self.open(k, 1 if long_signal else -1, self.o[k], known, None, False, (hi, lo))
            self.manage(k, entered_at_open=True)
            return

    def manage(self, start: int, entered_at_open: bool = False) -> None:
        n = len(self.ts)
        for j in range(start, n):
            if self.pos is None:
                return
            flat = self.exits(j, intrabar_entry=False)
            self.mark(j)
            if flat:
                for rest in range(j + 1, n):
                    self.mark(rest)
                return
        if self.pos is not None:
            self.finish(n - 1)

    def crossover(self, rule: MovingAverageCrossover) -> None:
        w, n = self.w, len(self.ts)
        longs, shorts = rule.direction in ("long", "both"), rule.direction in ("short", "both")
        # Decision points: the end of each 1-minute bar, or of each N-minute bar built from
        # them (aligned to the session start; known only once its period has ended).
        span = rule.bar_minutes * MINUTE
        decide: dict[int, int] = {}  # 1-minute bar index → known_at of the bar it completes
        closes: list[int] = []
        for j in range(n):
            bucket = (self.ts[j] - w.start_ns) // span
            if j + 1 < n and (self.ts[j + 1] - w.start_ns) // span == bucket:
                continue
            decide[j] = (
                self.ts[j] + MINUTE
                if rule.bar_minutes == 1
                else min(w.start_ns + (bucket + 1) * span, w.end_ns)
            )
            closes.append(self.c[j])
        prefix = [0]
        for value in closes:
            prefix.append(prefix[-1] + value)

        def diff(k: int) -> int | None:
            if k < 0 or k + 1 < rule.slow:
                return None
            fast = prefix[k + 1] - prefix[k + 1 - rule.fast]
            slow = prefix[k + 1] - prefix[k + 1 - rule.slow]
            return fast * rule.slow - slow * rule.fast  # sign of SMA(fast) − SMA(slow), exact

        pending: tuple[int, int] | None = None  # (wanted position, known_at)
        latency = self.sim.spec.execution.latency_bars * MINUTE
        k = -1
        for j in range(n):
            ts = self.ts[j]
            if pending is not None and ts >= pending[1] + latency:
                wanted, known = pending
                pending = None
                current = self.pos.direction if self.pos else 0
                if ts < w.flatten_ns and wanted != current:
                    if self.pos is not None:
                        self.close(j, self.o[j], "SIGNAL_EXIT", False, order_kind="MARKET_EXIT")
                    if wanted != 0 and ts < w.entry_cutoff_ns:
                        self.open(j, wanted, self.o[j], known, None, False)
            if self.pos is not None and self.exits(j, intrabar_entry=False):
                pass
            self.mark(j)
            if j not in decide:
                continue
            k += 1
            now, before = diff(k), diff(k - 1)
            if now is None or before is None:
                continue
            known_at = decide[j]
            if before <= 0 < now:
                wanted = 1 if longs else 0
            elif before >= 0 > now:
                wanted = -1 if shorts else 0
            else:
                continue
            if wanted and (known_at < self.entry_from or not self.bias_ok(wanted, closes[k])):
                wanted = 0  # filtered: the signal can still take the position flat
            pending = (wanted, known_at)
        if self.pos is not None:
            self.finish(n - 1)

    # sweep and retest ---------------------------------------------------------------------

    def _range(self, minutes: int) -> tuple[int, int, int, int] | None:
        """(high, low, first bar after the range, range end ns) — or None, logged."""
        w, n = self.w, len(self.ts)
        range_end = w.start_ns + minutes * MINUTE
        in_range = [j for j in range(n) if self.ts[j] < range_end]
        if not in_range:
            self.log.status = "NO_RANGE"
            self.mark_from(0)
            return None
        hi = max(self.h[j] for j in in_range)
        lo = min(self.l[j] for j in in_range)
        self.log.range_high, self.log.range_low = hi, lo
        for j in in_range:
            self.mark(j)
        first = in_range[-1] + 1
        if not self.range_ok(hi, lo):
            self.mark_from(first)
            return None
        return hi, lo, first, range_end

    def _ambiguous(self, j: int, orders: list[Order | None]) -> None:
        self.log.status = "AMBIGUOUS_ENTRY"
        self.log.note = "both sides triggered inside one 1-minute bar"
        for record in orders:
            if record is not None:
                self.close_order(record, "CANCELED", self.ts[j])
        self.mark_from(j)

    def _market_entry(self, j: int, direction: int, extreme: int | None,
                      range_hl: tuple[int, int] | None) -> None:  # fmt: skip
        """A signal on the close of bar j → market order at the next eligible bar's open."""
        w, n = self.w, len(self.ts)
        known = self.ts[j] + MINUTE
        eligible = known + self.sim.spec.execution.latency_bars * MINUTE
        k = next((i for i in range(j + 1, n) if self.ts[i] >= eligible), None)
        if k is None or self.ts[k] >= min(w.entry_cutoff_ns, w.flatten_ns):
            self.log.note = "signal too late to execute before the entry cutoff"
            self.mark_from(j + 1)
            return
        for i in range(j + 1, k):
            self.mark(i)
        self.open(k, direction, self.o[k], known, None, False, range_hl, extreme)
        self.manage(k, entered_at_open=True)

    def sweep(self, rule: LevelSweepReclaim) -> None:
        w, n = self.w, len(self.ts)
        latency = self.sim.spec.execution.latency_bars * MINUTE
        range_hl: tuple[int, int] | None = None
        if rule.level == "opening_range":
            assert rule.range_minutes is not None
            got = self._range(rule.range_minutes)
            if got is None:
                return
            hi, lo, first, usable = got
            range_hl = (hi, lo)
            armed = {1: True, -1: True}  # the range's own closes lie inside it
        else:
            prior = self.prior_ticks()
            if prior is None:
                self.log.status = "NO_LEVEL"
                self.log.note = "no prior session on this contract (first day or a roll)"
                self.mark_from(0)
                return
            hi, lo, _ = prior
            self.log.range_high, self.log.range_low = hi, lo
            first, usable = 0, w.start_ns
            armed = {1: False, -1: False}  # a side counts after a close back inside the level
        sides = []
        if rule.sides in ("fade_highs", "both"):
            sides.append(1)  # the high is swept → short
        if rule.sides in ("fade_lows", "both"):
            sides.append(-1)  # the low is swept → long
        level = {1: hi, -1: lo}
        k = rule.sweep_min_ticks
        limit = min(w.entry_cutoff_ns, w.flatten_ns)
        sweep: dict[int, list[int] | None] = {1: None, -1: None}  # [first bar, extreme]
        orders: dict[int, Order | None] = {1: None, -1: None}
        for j in range(first, n):
            ts = self.ts[j]
            if ts < usable + latency:
                self.mark(j)
                continue
            if ts >= limit:
                break
            o, h, low, c = self.o[j], self.h[j], self.l[j], self.c[j]
            if rule.reclaim == "stop_back_through":
                fills: list[tuple[int, int, bool]] = []  # (side, reference, gapped)
                for side in sides:
                    record, sw = orders[side], sweep[side]
                    if record is None or sw is None or record.price_ticks is None:
                        continue
                    if j > sw[0] + rule.reclaim_within_bars:
                        self.close_order(record, "EXPIRED", ts)
                        orders[side], sweep[side], armed[side] = None, None, False
                        continue
                    if ts < record.created_ns + latency or ts < self.entry_from:
                        continue
                    price = record.price_ticks
                    hit = low <= price if side == 1 else h >= price
                    if hit:
                        gapped = o <= price if side == 1 else o >= price
                        fills.append((side, o if gapped else price, gapped))
                if len(fills) > 1:
                    self._ambiguous(j, list(orders.values()))
                    return
                if fills:
                    side, reference, gapped = fills[0]
                    sw = sweep[side]
                    assert sw is not None
                    record = orders[side]
                    for other in sides:
                        if other != side and orders[other] is not None:
                            self.close_order(orders[other], "CANCELED", ts)  # type: ignore[arg-type]
                    self.open(j, -side, reference, record.created_ns if record else ts, record,
                              not gapped, range_hl, sw[1])  # fmt: skip
                    if self.exits(j, intrabar_entry=not gapped):
                        self.mark_from(j)
                        return
                    self.mark(j)
                    self.manage(j + 1)
                    return
            signals: list[tuple[int, int]] = []  # (side, extreme)
            for side in sides:
                lvl, sw = level[side], sweep[side]
                beyond = h >= lvl + k if side == 1 else low <= lvl - k
                inside = c < lvl if side == 1 else c > lvl
                if sw is None and armed[side] and beyond:
                    sw = sweep[side] = [j, h if side == 1 else low]
                    if rule.reclaim == "stop_back_through":
                        price = (
                            lvl - rule.entry_buffer_ticks
                            if side == 1
                            else lvl + rule.entry_buffer_ticks
                        )
                        if self.bias_ok(-side, price):
                            what = "high" if side == 1 else "low"
                            orders[side] = self.order(
                                ts + MINUTE, "STOP_ENTRY", -side, price, note=f"sweep of {what}"
                            )
                        else:
                            sweep[side], armed[side] = None, False
                        continue
                elif sw is not None:
                    sw[1] = max(sw[1], h) if side == 1 else min(sw[1], low)
                if rule.reclaim == "close_back_inside" and sw is not None:
                    if inside:
                        known = ts + MINUTE
                        if known >= self.entry_from and self.bias_ok(-side, c):
                            signals.append((side, sw[1]))
                        sweep[side] = None  # used (or filtered); the side is inside again
                        armed[side] = True
                        continue
                    if j - sw[0] + 1 >= rule.reclaim_within_bars:
                        sweep[side], armed[side] = None, False  # expired beyond the level
                        continue
                if sweep[side] is None and not armed[side] and inside:
                    armed[side] = True
            if len(signals) > 1:
                self._ambiguous(j, list(orders.values()))
                return
            if signals:
                side, extreme = signals[0]
                self.mark(j)
                self._market_entry(j, -side, extreme, range_hl)
                return
            self.mark(j)
        for record in orders.values():
            if record is not None:
                self.close_order(record, "EXPIRED", limit)
        self.mark_from(max(first, min(n, next((i for i in range(n) if self.ts[i] >= limit), n))))

    def retest(self, rule: OpeningRangeRetest) -> None:
        w, n = self.w, len(self.ts)
        got = self._range(rule.range_minutes)
        if got is None:
            return
        hi, lo, first, range_end = got
        latency = self.sim.spec.execution.latency_bars * MINUTE
        limit = min(w.entry_cutoff_ns, w.flatten_ns)
        sides = []
        if rule.direction in ("long", "both"):
            sides.append(1)
        if rule.direction in ("short", "both"):
            sides.append(-1)
        broke: dict[int, int | None] = {1: None, -1: None}
        armed = {1: True, -1: True}
        tol, buf = rule.retest_tolerance_ticks, rule.breakout_buffer_ticks
        for j in range(first, n):
            ts = self.ts[j]
            if ts < range_end + latency:
                self.mark(j)
                continue
            if ts >= limit:
                break
            low, h, c = self.l[j], self.h[j], self.c[j]
            signals: list[tuple[int, int]] = []
            for side in sides:
                edge = hi if side == 1 else lo
                beyond = c > hi + buf if side == 1 else c < lo - buf
                inside = c <= hi if side == 1 else c >= lo
                start = broke[side]
                if start is None:
                    if armed[side] and beyond:
                        broke[side] = j  # the breakout bar; the retest comes later
                    elif not armed[side] and inside:
                        armed[side] = True
                    continue
                if j - start > rule.retest_within_bars:
                    broke[side], armed[side] = None, inside
                    continue
                if inside:
                    broke[side], armed[side] = None, True  # failed: back inside the range
                    continue
                touched = low <= edge + tol if side == 1 else h >= edge - tol
                if touched:
                    broke[side], armed[side] = None, False
                    known = ts + MINUTE
                    if known >= self.entry_from and self.bias_ok(side, c):
                        signals.append((side, low if side == 1 else h))
            if len(signals) > 1:
                self._ambiguous(j, [])
                return
            if signals:
                side, extreme = signals[0]
                self.mark(j)
                self._market_entry(j, side, extreme, (hi, lo))
                return
            self.mark(j)
        self.mark_from(max(first, min(n, next((i for i in range(n) if self.ts[i] >= limit), n))))


class _Simulator:
    def __init__(
        self,
        spec: FuturesSpec,
        bars: Bars,
        contracts: dict[int, ContractSpec],
        mode: Mode,
        cost_multiplier: float,
        record_equity: bool,
    ) -> None:
        self.spec = spec
        self.bars = bars
        self.contracts = contracts
        self.mode = mode
        self.record_equity = record_equity
        self.contracts_n = spec.sizing.contracts
        base_fee = Decimal(str(spec.costs.commission_per_contract_side)) + Decimal(
            str(spec.costs.exchange_fees_per_contract_side)
        )
        self.fee_per_side = base_fee * Decimal(str(cost_multiplier))
        self.slippage = math.ceil(spec.execution.slippage_ticks * cost_multiplier)
        self.cash = Decimal(str(spec.sizing.account_capital))
        self.prior: PriorSession | None = None
        self.result = Result(
            spec_sha256=spec.sha256(),
            mode=mode,
            cost_multiplier=cost_multiplier,
            capital=self.cash,
            fee_per_side=self.fee_per_side,
            slippage_ticks=self.slippage,
        )


def prior_session(bars: Bars, window: Window | None) -> PriorSession | None:
    """High, low and last close of one window, if it holds bars of exactly one contract."""
    if window is None:
        return None
    lo, hi = (int(x) for x in np.searchsorted(bars.ts, [window.start_ns, window.end_ns]))
    if hi <= lo:
        return None
    ids = np.unique(bars.iid[lo:hi])
    if ids.size != 1:
        return None
    return PriorSession(
        int(ids[0]),
        int(bars.high[lo:hi].max()),
        int(bars.low[lo:hi].min()),
        int(bars.close[hi - 1]),
    )


def predecessors(windows: list[Window], context: list[Window] | None) -> list[Window | None]:
    """The trading session before each window — from the full calendar when given.

    A split run simulates segments (in-sample, out-of-sample) without the embargo
    days between them; "the prior session" must still be the actual previous
    trading day, which ``context`` (all calendar windows) provides.
    """
    if context is None:
        return [None, *windows[:-1]]
    position = {w.label: i for i, w in enumerate(context)}
    return [
        context[position[w.label] - 1] if position.get(w.label, 0) > 0 else None for w in windows
    ]


def simulate(
    spec: FuturesSpec,
    bars: Bars,
    contracts: dict[int, ContractSpec],
    windows: list[Window],
    *,
    mode: Mode = "conservative",
    cost_multiplier: float = 1.0,
    record_equity: bool = True,
    cancelled: Callable[[], bool] | None = None,
    context: list[Window] | None = None,
) -> Result:
    sim = _Simulator(spec, bars, contracts, mode, cost_multiplier, record_equity)
    ts = bars.ts
    before = predecessors(windows, context)
    for number, window in enumerate(windows):
        if cancelled is not None and number % 20 == 0 and cancelled():
            raise Cancelled
        lo, hi = (int(x) for x in np.searchsorted(ts, [window.start_ns, window.end_ns]))
        sim.prior = prior_session(bars, before[number])
        session = _Session(sim, window, lo, hi)
        if session.prepare():
            session.run()
        sim.result.sessions.append(session.log)
    return sim.result


def session_dates(windows: list[Window]) -> list[date]:
    return [w.label for w in windows]
