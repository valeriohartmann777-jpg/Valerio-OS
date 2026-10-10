"""Independent checks of a futures run, re-derived from the ledger and the raw bars.

Nothing here trusts the engine's own bookkeeping: prices are looked up in the
bars again, money is recomputed from ticks and the contract table, and every
fill is checked against the time its information became available.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np

from jarvis.quantlab.futures.engine import MINUTE, Bars, Result
from jarvis.quantlab.futures.sessions import Window
from jarvis.quantlab.futures.spec import FuturesSpec, OpeningRangeBreakout


def audit(
    result: Result, bars: Bars, windows: list[Window], spec: FuturesSpec
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    by_label = {w.label.isoformat(): w for w in windows}

    def check(id_: str, title: str, failures: list[str], checked: int) -> None:
        checks.append(
            {
                "id": id_,
                "title": title,
                "result": "PASS" if not failures else "FAIL",
                "checked": checked,
                "failures": len(failures),
                "examples": failures[:5],
            }
        )

    def bar(ts: int) -> int | None:
        i = int(np.searchsorted(bars.ts, ts))
        return i if i < len(bars) and int(bars.ts[i]) == ts else None

    # Fill prices lie inside the bar they were filled in (before slippage).
    grid: list[str] = []
    for f in result.fills:
        i = bar(f.bar_ts)
        spec_c = result.contracts_used.get(f.instrument_id)
        if i is None or spec_c is None:
            grid.append(f"order {f.order_id}: no bar at {f.bar_ts}")
            continue
        tick = spec_c.tick_size_fixed
        lo, hi = int(bars.low[i]) // tick, int(bars.high[i]) // tick
        if f.reason == "LAST_BAR_CLOSE":
            ok = f.reference_ticks == int(bars.close[i]) // tick
        else:
            ok = lo <= f.reference_ticks <= hi
        if not ok or int(bars.iid[i]) != f.instrument_id:
            grid.append(
                f"order {f.order_id} ({f.reason}) at {f.reference_ticks} outside [{lo}, {hi}]"
            )
        if f.ticks - f.reference_ticks not in (f.slippage_ticks, -f.slippage_ticks):
            grid.append(f"order {f.order_id}: slippage mismatch")
    check("PRICES_IN_BAR", "Every fill price was traded in its bar", grid, len(result.fills))

    # Causality: no fill before the information behind it existed.
    early: list[str] = []
    for f in result.fills:
        if f.reason == "LAST_BAR_CLOSE":
            if f.known_at_ns != f.bar_ts + MINUTE:
                early.append(f"order {f.order_id}: close exit not at the bar's end")
        elif f.bar_ts < f.known_at_ns:
            early.append(f"order {f.order_id}: filled in bar {f.bar_ts} before {f.known_at_ns}")
    check("NO_LOOKAHEAD", "No fill before its signal was known", early, len(result.fills))

    # Fills inside the session window; positions flat at every session end.
    outside: list[str] = []
    for t in result.trades:
        w = by_label.get(t.session)
        if (
            w is None
            or not (w.start_ns <= t.entry_bar_ts < w.end_ns)
            or not (w.start_ns <= t.exit_bar_ts < w.end_ns)
        ):
            outside.append(f"trade {t.number} outside its session window")
    if len(result.fills) != 2 * len(result.trades):
        outside.append(f"{len(result.fills)} fills for {len(result.trades)} round trips")
    check("FLAT_EACH_SESSION", "Trades stay inside one session; flat at its end", outside,
          len(result.trades))  # fmt: skip

    # One contract per trade (no P&L across a roll).
    mixed: list[str] = []
    for t in result.trades:
        for ts in (t.entry_bar_ts, t.exit_bar_ts):
            i = bar(ts)
            if i is None or int(bars.iid[i]) != t.instrument_id:
                mixed.append(f"trade {t.number} spans contracts")
                break
    check("ONE_CONTRACT", "Entry and exit on the same contract", mixed, len(result.trades))

    # Fees: exactly one charge per side per contract.
    fees: list[str] = []
    expected_fee = result.fee_per_side * spec.sizing.contracts
    seen: set[int] = set()
    for f in result.fills:
        if f.fee != expected_fee:
            fees.append(f"order {f.order_id}: fee {f.fee} ≠ {expected_fee}")
        if f.order_id in seen:
            fees.append(f"order {f.order_id} filled twice")
        seen.add(f.order_id)
    check("FEES", "One fee per side per contract, no double fills", fees, len(result.fills))

    # Money: recomputed from ticks × tick value; ledger reconciles to equity.
    money: list[str] = []
    total = Decimal(0)
    for t in result.trades:
        contract = result.contracts_used[t.instrument_id]
        gross = (
            Decimal((t.exit_ticks - t.entry_ticks) * t.direction)
            * contract.tick_value
            * t.contracts
        )
        if gross != t.gross:
            money.append(f"trade {t.number}: gross {t.gross} ≠ {gross}")
        if t.net != t.gross - t.fees:
            money.append(f"trade {t.number}: net ≠ gross − fees")
        total += t.net
    if result.capital + total != result.final_equity:
        money.append("sum of trades doesn't reconcile with final equity")
    if result.equity and result.equity[-1] != result.final_equity:
        money.append(f"equity curve ends at {result.equity[-1]}, ledger at {result.final_equity}")
    check("PNL_RECONCILES", "P&L = ticks × tick value × contracts; ledger = equity", money,
          len(result.trades))  # fmt: skip

    # Stops and targets honoured at their levels.
    levels: list[str] = []
    for t in result.trades:
        exit_fill = next(
            (f for f in result.fills if f.session == t.session and f.bar_ts == t.exit_bar_ts
             and f.reason == t.exit_reason),
            None,
        )  # fmt: skip
        if exit_fill is None:
            continue
        ref = exit_fill.reference_ticks
        if t.exit_reason == "STOP" and t.stop_ticks is not None:
            i = bar(t.exit_bar_ts)
            tick = result.contracts_used[t.instrument_id].tick_size_fixed
            opening = int(bars.open[i]) // tick if i is not None else None
            if ref != t.stop_ticks and ref != opening:
                levels.append(f"trade {t.number}: stop filled at {ref}, stop {t.stop_ticks}")
        if t.exit_reason == "TARGET" and t.target_ticks is not None:
            better = (ref - t.target_ticks) * t.direction
            if better < 0:
                levels.append(f"trade {t.number}: target filled worse than its limit")
    check("ORDER_LEVELS", "Stops and targets filled at their levels (or a gap open)", levels,
          len(result.trades))  # fmt: skip

    # Ambiguous breakout days produce no trade; ORB entries only after the range.
    ambiguous = [s.label for s in result.sessions if s.status == "AMBIGUOUS_ENTRY" and s.trades]
    check("AMBIGUOUS_EXCLUDED", "Days with an unresolvable breakout have no trade", ambiguous,
          sum(1 for s in result.sessions if s.status == "AMBIGUOUS_ENTRY"))  # fmt: skip
    if isinstance(spec.rule, OpeningRangeBreakout):
        minutes = spec.rule.range_minutes
        before: list[str] = []
        for t in result.trades:
            w = by_label[t.session]
            if t.entry_bar_ts < w.start_ns + minutes * MINUTE:
                before.append(f"trade {t.number} entered inside the opening range")
        check("RANGE_BEFORE_ENTRY", "Entries only after the opening range completed", before,
              len(result.trades))  # fmt: skip
    return checks


def passed(checks: list[dict[str, Any]]) -> bool:
    return all(c["result"] == "PASS" for c in checks)
