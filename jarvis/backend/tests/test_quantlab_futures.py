"""Futures engine against hand-calculated cases.

Every expected number below was worked out by hand from the bars in the test
(tick = 0.25 points; NQ $5 per tick, MNQ $0.50) and written down as a literal —
the engine never computes its own expectations.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from jarvis.quantlab.futures import contracts as ct
from jarvis.quantlab.futures import sessions
from jarvis.quantlab.futures.audit import audit, passed
from jarvis.quantlab.futures.engine import MINUTE, Bars, simulate
from jarvis.quantlab.futures.metrics import full, segment
from jarvis.quantlab.futures.sessions import Window
from jarvis.quantlab.futures.spec import FuturesSpecError, describe, parse, template

NS = 1_000_000_000
BASE = int(datetime(2026, 3, 2, 14, 30, tzinfo=UTC).timestamp()) * NS  # 09:30 New York (EST)
NQ = ct.ContractSpec(42261, "NQH6", "NQ", 250_000_000, Decimal(5), Decimal(20), "USD", None,
                     "definition", None)  # fmt: skip
MNQ = ct.ContractSpec(42261, "MNQH6", "MNQ", 250_000_000, Decimal("0.5"), Decimal(2), "USD", None,
                      "definition", None)  # fmt: skip


def bars(rows: list[tuple[int, float, float, float, float]], iid: int = 42261) -> Bars:
    """(minute offset, open, high, low, close) in index points → fixed-precision arrays."""

    def fixed(points: float) -> int:
        return round(points * 4) * 250_000_000

    return Bars(
        ts=np.array([BASE + m * MINUTE for m, *_ in rows], dtype=np.int64),
        iid=np.full(len(rows), iid, dtype=np.int64),
        open=np.array([fixed(r[1]) for r in rows], dtype=np.int64),
        high=np.array([fixed(r[2]) for r in rows], dtype=np.int64),
        low=np.array([fixed(r[3]) for r in rows], dtype=np.int64),
        close=np.array([fixed(r[4]) for r in rows], dtype=np.int64),
    )


def window(flatten_min: int = 385, cutoff_min: int = 150, end_min: int = 390) -> list[Window]:
    return [
        Window(
            label=date(2026, 3, 2),
            start_ns=BASE,
            end_ns=BASE + end_min * MINUTE,
            flatten_ns=BASE + flatten_min * MINUTE,
            entry_cutoff_ns=BASE + cutoff_min * MINUTE,
            session_close_ns=BASE + end_min * MINUTE,
            early_close=False,
        )
    ]


def orb(**changes: Any) -> Any:
    raw = template("opening_range_breakout", "NQ")
    raw["rule"].update(range_minutes=2, buffer_ticks=1, direction="both")
    raw["execution"]["slippage_ticks"] = 1
    raw["costs"].update(commission_per_contract_side=2.0, exchange_fees_per_contract_side=1.0)
    raw["validation"]["parameter_grid"] = {}
    for path, value in changes.items():
        node = raw
        keys = path.split("__")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return parse(raw)


# Opening range of the first two bars: high 101.50, low 99.50.
# Buy stop 101.75 (1-tick buffer), sell stop 99.25.
RANGE = [
    (0, 100.00, 101.00, 99.50, 100.50),
    (1, 100.50, 101.50, 100.00, 101.25),
    (2, 101.25, 101.50, 100.75, 101.00),  # inside the range: nothing
]
BREAKOUT = (3, 101.50, 102.50, 101.25, 102.25)  # high 102.50 ≥ 101.75 → long


def run(spec: Any, rows: list[Any], contract: ct.ContractSpec = NQ, **kw: Any) -> Any:
    b = bars(rows)
    w = kw.pop("windows", None) or window()
    result = simulate(spec, b, {contract.instrument_id: contract}, w, **kw)
    checks = audit(result, b, w, spec)
    assert passed(checks), [c for c in checks if c["result"] == "FAIL"]
    return result


def test_long_breakout_hits_its_target() -> None:
    rows = [
        *RANGE,
        BREAKOUT,
        (4, 102.25, 104.00, 102.00, 103.75),
        (5, 103.75, 107.75, 103.50, 107.50),
    ]
    result = run(orb(), rows)
    (trade,) = result.trades
    # Entry: stop 101.75 + 1 tick slippage = 102.00. Stop 99.25 → risk 11 ticks.
    # Target 2R = 22 ticks above 102.00 = 107.50; bar 5's high 107.75 trades through it.
    assert (trade.direction, trade.entry_ticks, trade.exit_ticks) == (1, 408, 430)
    assert (trade.stop_ticks, trade.target_ticks, trade.risk_ticks) == (397, 430, 11)
    assert trade.exit_reason == "TARGET" and not trade.ambiguous
    assert trade.gross == Decimal(110)  # 22 ticks × $5
    assert trade.fees == Decimal(6)  # 2 sides × ($2 + $1)
    assert trade.net == Decimal(104)
    assert trade.slippage_cost == Decimal(5)  # one tick on the entry stop
    assert (trade.mae_ticks, trade.mfe_ticks) == (3, 23)
    assert trade.r_multiple == Decimal(2)
    assert result.final_equity == Decimal(50_104)
    assert result.equity[-1] == Decimal(50_104)
    # The entry used only the completed range: known at 09:32, filled in the 09:33 bar.
    entry = result.fills[0]
    assert entry.known_at_ns == BASE + 2 * MINUTE and entry.bar_ts == BASE + 3 * MINUTE


def test_micro_contract_scales_by_its_own_tick_value() -> None:
    rows = [
        *RANGE,
        BREAKOUT,
        (4, 102.25, 104.00, 102.00, 103.75),
        (5, 103.75, 107.75, 103.50, 107.50),
    ]
    spec = orb(instrument__product="MNQ", instrument__symbol="MNQ.v.0")
    (trade,) = run(spec, rows, MNQ).trades
    assert trade.gross == Decimal(11)  # 22 ticks × $0.50 — a tenth of NQ
    assert trade.net == Decimal(5)


def test_stop_and_target_in_one_bar_conservative_and_optimistic() -> None:
    rows = [
        *RANGE,
        BREAKOUT,
        (4, 102.25, 104.00, 102.00, 103.75),
        (5, 103.75, 107.75, 99.00, 100.00),
    ]
    worst = run(orb(), rows).trades[0]
    # Both 99.25 and 107.50 inside bar 5: the stop is assumed first. 99.25 − 1 tick = 99.00.
    assert (worst.exit_reason, worst.exit_ticks, worst.ambiguous) == ("STOP", 396, True)
    assert worst.gross == Decimal(-60) and worst.net == Decimal(-66)
    best = run(orb(), rows, mode="optimistic").trades[0]
    assert (best.exit_reason, best.exit_ticks, best.net) == ("TARGET", 430, Decimal(104))


def test_both_breakouts_in_one_bar_are_excluded() -> None:
    rows = [*RANGE, (3, 100.50, 102.00, 99.00, 101.00)]
    result = run(orb(), rows)
    assert result.trades == []
    assert result.sessions[0].status == "AMBIGUOUS_ENTRY"
    assert result.ambiguous_entries == 1


def test_gap_through_the_stop_fills_at_the_open() -> None:
    rows = [*RANGE, BREAKOUT, (4, 99.00, 99.50, 98.50, 99.25)]
    (trade,) = run(orb(), rows).trades
    # Opens at 99.00, below the 99.25 stop: filled at the open − 1 tick = 98.75.
    assert (trade.exit_reason, trade.exit_ticks) == ("STOP", 395)
    assert trade.net == Decimal(-13 * 5 - 6)


def test_time_exit_at_the_flatten_bar_open() -> None:
    rows = [*RANGE, BREAKOUT, (4, 102.25, 104.00, 102.00, 103.75), (5, 103.75, 104.50, 103.50, 104.00),  # noqa: E501
            (6, 104.00, 104.25, 103.75, 104.00)]  # fmt: skip
    (trade,) = run(orb(), rows, windows=window(flatten_min=6, end_min=10)).trades
    # The 09:36 bar is at the flatten time: market exit at its open 104.00 − 1 tick.
    assert (trade.exit_reason, trade.exit_ticks, trade.exit_bar_ts) == (
        "TIME_EXIT",
        415,
        BASE + 6 * MINUTE,
    )
    assert trade.net == Decimal(7 * 5 - 6)


def test_last_bar_close_exit_when_no_bar_reaches_the_flatten_time() -> None:
    rows = [*RANGE, BREAKOUT, (4, 102.25, 104.00, 102.00, 103.75)]
    (trade,) = run(orb(), rows, windows=window(flatten_min=20, end_min=30)).trades
    assert trade.exit_reason == "LAST_BAR_CLOSE"
    assert trade.exit_ticks == 414  # close 103.75 − 1 tick


def test_close_beyond_range_enters_at_the_next_open() -> None:
    rows = [
        *RANGE,
        BREAKOUT,
        (4, 102.25, 104.00, 102.00, 103.75),
        (5, 103.75, 104.50, 103.50, 104.00),
    ]
    spec = orb(rule__entry="close_beyond_range")
    (trade,) = run(spec, rows, windows=window(flatten_min=20, end_min=30)).trades
    # Bar 3 closes 102.25 > 101.75 → known 09:34 → filled at bar 4's open 102.25 + 1 tick.
    assert (trade.entry_ticks, trade.entry_bar_ts) == (410, BASE + 4 * MINUTE)
    assert trade.signal_known_at == BASE + 4 * MINUTE
    assert (trade.stop_ticks, trade.target_ticks) == (397, 436)  # risk 13 → 2R = 26 ticks


def test_short_side_mirror() -> None:
    rows = [*RANGE, (3, 99.50, 99.75, 98.50, 98.75), (4, 98.75, 99.00, 94.00, 94.50)]
    (trade,) = run(orb(), rows).trades
    # Sell stop 99.25 − 1 tick = 99.00 (396). Stop 101.75 (407): risk 11 → target 396 − 22 = 374.
    # Bar 4's low 94.00 (376)… needs < 374 (93.50) to trade through: not reached.
    assert (trade.direction, trade.entry_ticks, trade.target_ticks) == (-1, 396, 374)
    assert trade.exit_reason == "LAST_BAR_CLOSE"
    assert trade.exit_ticks == 378 + 1  # close 94.50 + 1 tick slippage for a buy


def test_latency_delays_the_fill() -> None:
    rows = [
        *RANGE,
        BREAKOUT,
        (4, 102.25, 104.00, 102.00, 103.75),
        (5, 103.75, 104.50, 103.50, 104.00),
    ]
    spec = orb(rule__entry="close_beyond_range", execution__latency_bars=1)
    (trade,) = run(spec, rows, windows=window(flatten_min=20, end_min=30)).trades
    assert trade.entry_bar_ts == BASE + 5 * MINUTE and trade.entry_ticks == 416  # 103.75 + 1 tick


def test_future_bars_cannot_change_past_trades() -> None:
    rows = [
        *RANGE,
        BREAKOUT,
        (4, 102.25, 104.00, 102.00, 103.75),
        (5, 103.75, 107.75, 103.50, 107.50),
    ]
    later = [(9, 107.50, 200.0, 50.0, 60.0), (10, 60.0, 61.0, 1.0, 2.0)]
    first = run(orb(), rows).trades[0]
    again = run(orb(), rows + later).trades[0]
    assert (first.entry_ticks, first.exit_ticks, first.net) == (
        again.entry_ticks,
        again.exit_ticks,
        again.net,
    )


def test_moving_average_crossover_reverses() -> None:
    closes = [100.0, 100.0, 100.0, 101.0, 102.0, 101.0, 99.0, 98.0]
    rows = []
    prev = closes[0]
    for m, c in enumerate(closes):
        rows.append((m, prev, max(prev, c), min(prev, c), c))
        prev = c
    raw = template("ma_crossover", "NQ")
    raw["rule"].update(fast=2, slow=3, direction="both")
    raw["exits"] = {"stop": {"type": "none"}, "target": {"type": "none"}}
    raw["execution"]["slippage_ticks"] = 1
    raw["costs"].update(commission_per_contract_side=2.0, exchange_fees_per_contract_side=1.0)
    raw["validation"]["parameter_grid"] = {}
    result = run(parse(raw), rows, windows=window(flatten_min=20, end_min=30))
    # SMA(2) crosses above SMA(3) at bar 3 → long at bar 4's open 101.00 + 1 tick (405).
    # Crosses below at bar 6 → at bar 7's open 99.00: sell 395 (exit), sell short 395.
    # The window's last bar closes 98.00 → buy back at 98.00 + 1 tick (393).
    long, short = result.trades
    assert (long.direction, long.entry_ticks, long.exit_ticks, long.exit_reason) == (
        1,
        405,
        395,
        "SIGNAL_EXIT",
    )
    assert (short.direction, short.entry_ticks, short.exit_ticks) == (-1, 395, 393)
    assert long.net == Decimal(-10 * 5 - 6) and short.net == Decimal(2 * 5 - 6)
    assert result.final_equity == Decimal(50_000 - 56 + 4)


def test_roll_inside_a_window_is_skipped_not_traded() -> None:
    rows = [*RANGE, BREAKOUT]
    b = bars(rows)
    b.iid[3] = 42262  # the continuous symbol switches contract mid-session
    other = ct.ContractSpec(42262, "NQM6", "NQ", 250_000_000, Decimal(5), Decimal(20), "USD", None,
                            "definition", None)  # fmt: skip
    result = simulate(orb(), b, {42261: NQ, 42262: other}, window())
    assert result.trades == [] and result.sessions[0].status == "SKIPPED_ROLL"


def test_cost_stress_scales_fees_and_slippage() -> None:
    rows = [
        *RANGE,
        BREAKOUT,
        (4, 102.25, 104.00, 102.00, 103.75),
        (5, 103.75, 107.75, 103.50, 107.50),
    ]
    (trade,) = run(orb(), rows, cost_multiplier=2.0).trades
    # 2 ticks slippage on entry (101.75 + 0.50 = 102.25 → 409), fees $6 per side.
    assert trade.entry_ticks == 409 and trade.fees == Decimal(12)


def test_metrics_reconcile_and_say_why_values_are_missing() -> None:
    rows = [
        *RANGE,
        BREAKOUT,
        (4, 102.25, 104.00, 102.00, 103.75),
        (5, 103.75, 107.75, 103.50, 107.50),
    ]
    result = run(orb(), rows)
    m = full(result)
    assert m["net_pnl"] == 104.0 and m["fees"] == 6.0 and m["trades"] == 1
    assert m["final_equity"] == 50_104.0
    assert m["profit_factor"] is None and "undefined" in m["notes"]["profit_factor"]
    assert m["sharpe"] is None and "20" in m["notes"]["sharpe"]
    empty = segment(result, labels=[])
    assert empty["trades"] == 0 and empty["win_rate"] is None


def test_audit_catches_a_tampered_ledger() -> None:
    rows = [
        *RANGE,
        BREAKOUT,
        (4, 102.25, 104.00, 102.00, 103.75),
        (5, 103.75, 107.75, 103.50, 107.50),
    ]
    b = bars(rows)
    result = simulate(orb(), b, {NQ.instrument_id: NQ}, window())
    result.trades[0].gross += Decimal(1000)
    result.fills[0].bar_ts = BASE  # filled inside the range, before it was known
    failing = {c["id"] for c in audit(result, b, window(), orb()) if c["result"] == "FAIL"}
    assert {"PNL_RECONCILES", "NO_LOOKAHEAD"} <= failing


def test_spec_rules_and_plain_language() -> None:
    spec = orb()
    text = " ".join(describe(spec))
    assert "known only once the range's last bar has closed" in text
    assert "excluded and counted, never guessed" in text
    for change, field in (
        ({"rule__range_minutes": 500}, "range_minutes"),
        ({"instrument__symbol": "MNQ.v.0"}, "instrument"),
        ({"exits__stop__type": "none"}, "R-multiple"),
        ({"session__flatten_at": "09:00"}, "session"),
        ({"validation__parameter_grid": {"fast": [1]}}, "parameter_grid"),
    ):
        with pytest.raises(FuturesSpecError) as bad:
            orb(**change)
        assert field in bad.value.message, change
    with pytest.raises(FuturesSpecError):
        parse({**template(), "surprise": 1})  # unknown fields are refused
    variant = orb(validation__parameter_grid={"range_minutes": [2, 3]}).with_params(
        {"range_minutes": 3}
    )
    assert variant.rule.range_minutes == 3  # type: ignore[union-attr]


def test_windows_follow_dst_holidays_and_early_closes() -> None:
    wins = {
        w.label: w
        for w in sessions.windows(
            "XNYS", "America/New_York", time(9, 30), time(16, 0), time(15, 55), time(12, 0),
            date(2025, 11, 24), date(2025, 12, 2),
        )
    }  # fmt: skip
    assert date(2025, 11, 27) not in wins  # Thanksgiving
    friday = wins[date(2025, 11, 28)]  # NYSE closes 13:00
    assert friday.early_close
    assert datetime.fromtimestamp(friday.end_ns / NS, UTC).time() == time(18, 0)
    assert datetime.fromtimestamp(friday.flatten_ns / NS, UTC).time() == time(17, 55)
    march = {
        w.label: w
        for w in sessions.windows(
            "XNYS", "America/New_York", time(9, 30), time(16, 0), time(15, 55), None,
            date(2026, 3, 6), date(2026, 3, 10),
        )
    }  # fmt: skip
    # US daylight saving starts 8 March 2026: 09:30 New York moves from 14:30 to 13:30 UTC.
    assert datetime.fromtimestamp(march[date(2026, 3, 6)].start_ns / NS, UTC).time() == time(14, 30)
    assert datetime.fromtimestamp(march[date(2026, 3, 9)].start_ns / NS, UTC).time() == time(13, 30)


def test_contract_master_from_definitions_and_mismatch() -> None:
    import pyarrow as pa

    definitions = pa.table(
        {
            "ts_recv": [BASE - 86_400 * NS],
            "instrument_id": [42261],
            "raw_symbol": ["NQH6"],
            "asset": ["NQ"],
            "min_price_increment": [250_000_000],
            "unit_of_measure_qty": [20 * NS],
            "min_price_increment_amount": [5 * NS],
            "expiration": [BASE + 30 * 86_400 * NS],
            "currency": ["USD"],
        }
    )
    specs, findings = ct.build("NQ", {42261: BASE}, definitions)
    assert specs[42261].tick_value == Decimal(5) and specs[42261].multiplier == Decimal(20)
    assert specs[42261].provenance == "definition" and not findings
    _, wrong = ct.build("MNQ", {42261: BASE}, definitions)
    assert wrong[0]["code"] == "INSTRUMENT_MISMATCH" and wrong[0]["level"] == "BLOCK"
    assumed, notes = ct.build("NQ", {9: BASE}, None)
    assert assumed[9].provenance == "assumed" and notes[0]["code"] == "SPEC_ASSUMED"
    broken = definitions.set_column(6, "min_price_increment_amount", pa.array([4 * NS]))
    _, bad = ct.build("NQ", {42261: BASE}, broken)
    assert bad[0]["code"] == "SPEC_INCONSISTENT"


def test_no_edge_on_a_driftless_random_walk() -> None:
    """With zero costs on a driftless walk sampled inside each minute, the breakout must
    show no positive expectancy — a systematic optimistic fill bias would show up here."""
    rng = np.random.default_rng(11)
    sessions, minutes, sub = 800, 390, 20
    rows_t, rows_o, rows_h, rows_l, rows_c, wins = [], [], [], [], [], []
    for s in range(sessions):
        start = BASE + s * 86_400 * NS
        path = 84_000 + np.cumsum(rng.choice([-1, 1], size=minutes * sub)).reshape(minutes, sub)
        opens = np.concatenate(([84_000], path[:-1, -1]))
        rows_t.append(start + np.arange(minutes) * MINUTE)
        rows_o.append(opens)
        rows_h.append(np.maximum(opens, path.max(axis=1)))
        rows_l.append(np.minimum(opens, path.min(axis=1)))
        rows_c.append(path[:, -1])
        wins.append(
            Window(date(2020, 1, 1).fromordinal(737_425 + s), start, start + minutes * MINUTE,
                   start + (minutes - 5) * MINUTE, start + 150 * MINUTE, start + minutes * MINUTE,
                   False)
        )  # fmt: skip
    tick = 250_000_000

    def cat(parts: list[np.ndarray]) -> np.ndarray:
        return np.concatenate(parts).astype(np.int64)

    ids = np.full(sessions * minutes, NQ.instrument_id, dtype=np.int64)
    b = Bars(cat(rows_t), ids, cat(rows_o) * tick, cat(rows_h) * tick, cat(rows_l) * tick,
             cat(rows_c) * tick)  # fmt: skip
    spec = orb(
        rule__range_minutes=15,
        execution__slippage_ticks=0,
        costs__commission_per_contract_side=0,
        costs__exchange_fees_per_contract_side=0,
    )
    result = simulate(spec, b, {NQ.instrument_id: NQ}, wins, record_equity=False)
    r = np.array([float(t.r_multiple or 0) for t in result.trades])
    assert len(r) > 700
    t_stat = r.mean() / (r.std() / np.sqrt(len(r)))
    assert r.mean() < 0.05 and t_stat < 2.5, (r.mean(), t_stat)
