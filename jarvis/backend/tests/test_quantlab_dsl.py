"""New rule families against hand-calculated cases (Idea-to-Edge R2/R3).

NQ: tick 0.25 points = $5. Costs here: $2 commission + $1 exchange per side → $6 per
round trip; 1 tick slippage on market and stop fills, none on targets. Every
expected number was worked out by hand from the bars and is written as a literal.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from jarvis.quantlab.futures.engine import MINUTE, Bars, simulate
from jarvis.quantlab.futures.sessions import Window
from jarvis.quantlab.futures.spec import FuturesSpecError, describe, parse, template
from tests.test_quantlab_futures import BASE, NQ, RANGE, bars, orb, run, window

DAY = 1440


def spec(
    kind: str, rule: dict[str, Any], stop: dict[str, Any], target: dict[str, Any], **top: Any
) -> Any:
    raw = template(kind, "NQ")
    raw["rule"] = {"type": kind, **rule}
    raw["exits"] = {"stop": stop, "target": target}
    raw["execution"]["slippage_ticks"] = 1
    raw["costs"].update(commission_per_contract_side=2.0, exchange_fees_per_contract_side=1.0)
    raw["validation"]["parameter_grid"] = {}
    raw.update(top)
    return parse(raw)


def sweep(**rule: Any) -> Any:
    base = {
        "level": "opening_range",
        "range_minutes": 2,
        "sides": "fade_highs",
        "sweep_min_ticks": 2,
        "reclaim": "close_back_inside",
        "reclaim_within_bars": 3,
    }
    return spec(
        "level_sweep_reclaim",
        {**base, **rule},
        {"type": "setup_extreme", "ticks": 1},
        {"type": "r_multiple", "value": 2},
    )


def money(trade: Any) -> tuple[int, int, str, Decimal]:
    return trade.entry_ticks, trade.exit_ticks, trade.exit_reason, trade.net


def t(points: float) -> int:  # points → NQ ticks
    return round(points * 4)


# The TikTok example from the directive (A14) ----------------------------------------------
# Opening range (2 bars): high 101.50, low 99.50. A sweep needs ≥ 2 ticks: high ≥ 102.00.
SWEEP = [
    *RANGE[:2],
    (2, 101.25, 101.75, 101.00, 101.50),  # 1 tick above: not a sweep; close not below the level
    (3, 101.50, 102.50, 101.25, 102.00),  # sweep starts, extreme 102.50; no reclaim
    (4, 102.00, 102.75, 101.00, 101.25),  # extreme 102.75; closes 101.25 < 101.50 → short
    (5, 101.25, 101.50, 100.75, 101.00),  # sell at the open 101.25 − 1 tick = 101.00
    (6, 101.00, 101.25, 98.00, 98.50),
    (7, 98.50, 98.75, 96.75, 97.00),  # target 97.00 traded through → filled at 97.00
]


def test_sweep_of_the_high_then_close_back_inside_goes_short() -> None:
    result = run(sweep(), SWEEP)
    [trade] = result.trades
    # stop = extreme 102.75 + 1 tick = 103.00; risk 8 ticks; target 101.00 − 16 ticks = 97.00
    assert trade.direction == -1
    assert (trade.stop_ticks, trade.target_ticks, trade.risk_ticks) == (t(103.00), t(97.00), 8)
    assert money(trade) == (t(101.00), t(97.00), "TARGET", Decimal(74))  # 16 ticks × $5 − $6
    assert trade.r_multiple == 2
    assert trade.signal_known_at == BASE + 5 * MINUTE  # the reclaim bar's close
    assert trade.entry_bar_ts == BASE + 5 * MINUTE
    assert result.sessions[0].range_high == t(101.50)


def test_stop_back_through_is_conservative_inside_the_fill_bar() -> None:
    rule = {"reclaim": "stop_back_through", "entry_buffer_ticks": 1}
    # Sell stop 101.25 placed when the sweep bar (3) closes; bar 4 trades through it.
    # Stop-loss from completed bars only: 102.50 + 1 tick = 102.75; bar 4's high 102.75 is
    # inside the fill bar → order unknown → conservative: stopped at 102.75 + 1 tick slippage.
    result = run(sweep(**rule), SWEEP)
    [trade] = result.trades
    assert money(trade) == (t(101.00), t(103.00), "STOP", Decimal(-46))  # −8 ticks − $6
    assert trade.ambiguous
    # Optimistic bound: the position survives and reaches the target 101.00 − 14 ticks.
    best = run(sweep(**rule), SWEEP, mode="optimistic")
    [trade] = best.trades
    assert money(trade) == (t(101.00), t(97.50), "TARGET", Decimal(64))  # 14 ticks − $6


def test_one_bar_sweep_and_rearming() -> None:
    rows = [
        *RANGE[:2],
        (2, 101.25, 102.25, 101.00, 101.75),  # sweep but closes above: window of 1 bar used up
        (3, 101.75, 101.75, 101.25, 101.25),  # closes back inside: the side is armed again
        (4, 101.25, 102.00, 101.00, 101.25),  # sweep (102.00) and reclaim on the same bar
        (5, 101.25, 101.25, 100.75, 101.00),  # short at 101.00; stop 102.25; target 98.50
        (6, 101.00, 101.00, 98.25, 98.50),
    ]
    result = run(sweep(reclaim_within_bars=1), rows)
    [trade] = result.trades
    assert trade.signal_known_at == BASE + 5 * MINUTE
    assert (trade.stop_ticks, trade.target_ticks) == (t(102.25), t(98.50))
    assert money(trade) == (t(101.00), t(98.50), "TARGET", Decimal(44))  # 10 ticks − $6


def two_days(day1: list[Any], day2: list[Any], iid2: int = 42261) -> tuple[Bars, list[Window]]:
    a, b = bars(day1), bars(day2, iid=iid2)
    joined = Bars(
        *(
            np.concatenate([getattr(a, f), getattr(b, f)])
            for f in ("ts", "iid", "open", "high", "low", "close")
        )
    )
    w1 = window()[0]
    w2 = Window(
        date(2026, 3, 3),
        w1.start_ns + DAY * MINUTE,
        w1.end_ns + DAY * MINUTE,
        w1.flatten_ns + DAY * MINUTE,
        w1.entry_cutoff_ns + DAY * MINUTE,
        w1.session_close_ns + DAY * MINUTE,
        False,
    )
    return joined, [w1, w2]


DAY1 = [
    (0, 100.00, 101.00, 99.50, 100.50),
    (1, 100.50, 101.50, 99.00, 100.00),
    (2, 100.00, 100.50, 99.75, 100.00),  # high 101.50, low 99.00, close 100.00
]
DAY2 = [
    (DAY + 0, 99.25, 99.50, 99.25, 99.50),  # closes above 99.00: the low side is armed
    (DAY + 1, 99.50, 99.50, 98.25, 98.75),  # sweep ≤ 98.50, extreme 98.25
    (DAY + 2, 98.75, 99.50, 98.50, 99.25),  # closes 99.25 > 99.00 → long
    (DAY + 3, 99.25, 99.75, 99.00, 99.50),  # buy 99.25 + 1 tick = 99.50; stop 98.00; 1R 101.00
    (DAY + 4, 99.50, 101.25, 99.25, 101.00),  # target 101.00
]


def prior_spec() -> Any:
    rule = {
        "level": "prior_session",
        "sides": "fade_lows",
        "sweep_min_ticks": 2,
        "reclaim": "close_back_inside",
        "reclaim_within_bars": 2,
    }
    return spec(
        "level_sweep_reclaim",
        rule,
        {"type": "setup_extreme", "ticks": 1},
        {"type": "r_multiple", "value": 1},
    )


def test_prior_session_low_sweep_goes_long_and_the_first_day_has_no_level() -> None:
    b, w = two_days(DAY1, DAY2)
    result = simulate(prior_spec(), b, {NQ.instrument_id: NQ}, w)
    assert result.sessions[0].status == "NO_LEVEL"
    [trade] = result.trades
    assert (trade.stop_ticks, trade.target_ticks, trade.risk_ticks) == (t(98.00), t(101.00), 6)
    assert money(trade) == (t(99.50), t(101.00), "TARGET", Decimal(24))  # 6 ticks − $6
    assert result.sessions[1].range_low == t(99.00)


def test_prior_session_level_is_not_carried_across_a_roll() -> None:
    other = NQ.__class__(**{**NQ.__dict__, "instrument_id": 42270, "raw_symbol": "NQM6"})
    b, w = two_days(DAY1, DAY2, iid2=42270)
    result = simulate(prior_spec(), b, {NQ.instrument_id: NQ, 42270: other}, w)
    assert [s.status for s in result.sessions] == ["NO_LEVEL", "NO_LEVEL"]
    assert result.trades == []


def test_opening_range_retest_long() -> None:
    raw = {
        "range_minutes": 2,
        "direction": "long",
        "breakout_buffer_ticks": 0,
        "retest_within_bars": 3,
        "retest_tolerance_ticks": 1,
    }
    rule = spec(
        "opening_range_retest",
        raw,
        {"type": "setup_extreme", "ticks": 1},
        {"type": "r_multiple", "value": 2},
    )
    rows = [
        *RANGE[:2],
        (2, 101.25, 102.00, 101.25, 101.75),  # closes above 101.50: the breakout bar
        (3, 101.75, 102.50, 101.75, 102.25),  # back to 101.75 (≤ level + 1 tick), holds → long
        (4, 102.25, 102.50, 102.00, 102.25),  # buy 102.50; stop 101.75 − 1 tick = 101.50
        (5, 102.25, 104.75, 102.25, 104.50),  # target 102.50 + 2 × 4 ticks = 104.50
    ]
    [trade] = run(rule, rows).trades
    assert (trade.stop_ticks, trade.target_ticks) == (t(101.50), t(104.50))
    assert money(trade) == (t(102.50), t(104.50), "TARGET", Decimal(34))  # 8 ticks − $6
    # A close back inside the range cancels the breakout: no trade.
    failed = [*rows[:3], (3, 101.75, 101.75, 101.00, 101.25), *rows[4:]]
    assert run(rule, failed).trades == []


def test_higher_timeframe_signal_waits_for_the_bar_to_close() -> None:
    raw = template("ma_crossover", "NQ")
    raw["rule"] = {
        "type": "ma_crossover",
        "fast": 1,
        "slow": 2,
        "direction": "long",
        "bar_minutes": 5,
    }
    raw["exits"] = {"stop": {"type": "ticks", "ticks": 40}, "target": {"type": "none"}}
    raw["execution"]["slippage_ticks"] = 1
    raw["costs"].update(commission_per_contract_side=2.0, exchange_fees_per_contract_side=1.0)
    raw["validation"]["parameter_grid"] = {}
    htf = parse(raw)
    closes = [100.00] * 5 + [99.00] * 5 + [100.00, 101.50, 101.00, 101.25, 101.00] + [101.00] * 3
    rows = [(m, c, c, c, c) for m, c in enumerate(closes)]
    # 5-minute closes: 100.00, 99.00, 101.00 → SMA(1) crosses above SMA(2) when the third
    # 5-minute bar ends (minute 15), although 1-minute closes rose from minute 10.
    [trade] = run(htf, rows).trades
    assert trade.signal_known_at == BASE + 15 * MINUTE
    assert trade.entry_bar_ts == BASE + 15 * MINUTE
    # Bought 101.00 + 1 tick; no bar reaches the flatten time → sold at the last close 101.00
    # − 1 tick slippage (a market exit) = 100.75: −2 ticks × $5 − $6.
    assert money(trade) == (t(101.25), t(100.75), "LAST_BAR_CLOSE", Decimal(-16))
    assert "5-minute" in " ".join(describe(htf))
    # The same rule on 1-minute bars trades earlier — the difference is the point.
    raw["rule"]["bar_minutes"] = 1
    early = run(parse(raw), rows).trades[0]
    assert early.signal_known_at < trade.signal_known_at


def test_range_width_and_entry_time_filters() -> None:
    rows = [*RANGE, (3, 101.50, 102.50, 101.25, 102.25), (4, 102.25, 102.50, 101.75, 102.00)]
    assert run(orb(), rows).trades  # unfiltered: the long at minute 3
    narrow = orb(filters={"max_range_ticks": 4})  # this range is 8 ticks wide
    result = run(narrow, rows)
    assert result.trades == [] and result.sessions[0].status == "FILTERED"
    assert run(orb(filters={"min_range_ticks": 4, "max_range_ticks": 8}), rows).trades
    # Stop orders only work from 09:35 (minute 5): the breakout at minute 3 is not taken;
    # minute 6 trades through 101.75 again → bought at 101.75 + 1 tick.
    late = orb(filters={"entry_after": "09:35"})
    rows2 = [*rows, (5, 101.50, 101.50, 101.00, 101.25), (6, 101.25, 102.00, 101.25, 101.75)]
    [trade] = run(late, rows2).trades
    assert trade.entry_bar_ts == BASE + 6 * MINUTE and trade.entry_ticks == t(102.00)


def test_prior_close_and_gap_filters() -> None:
    day2 = [
        (DAY + 0, 102.00, 102.50, 101.75, 102.25),
        (DAY + 1, 102.25, 102.75, 102.00, 102.50),  # range 101.75–102.75, all above 100.00
        (DAY + 2, 102.50, 102.50, 101.25, 101.50),  # breaks down through 101.50
        (DAY + 3, 101.50, 101.75, 101.00, 101.25),
    ]
    b, w = two_days(DAY1, day2)
    base = orb(**{"rule__range_minutes": 2})
    plain = simulate(base, b, {NQ.instrument_id: NQ}, w)
    assert any(tr.direction == -1 for tr in plain.trades)
    # The sell stop (101.50) is above the prior close (100.00): shorts are filtered out.
    biased = orb(filters={"prior_close_bias": True})
    result = simulate(biased, b, {NQ.instrument_id: NQ}, w)
    assert result.sessions[0].status == "NO_PRIOR"
    assert all(tr.direction == 1 for tr in result.trades)
    # Day 2 opens 8 ticks above the prior close (102.00 vs 100.00): a 4-tick gap limit skips it.
    gap = simulate(orb(filters={"max_gap_ticks": 4}), b, {NQ.instrument_id: NQ}, w)
    assert gap.sessions[1].status == "FILTERED" and "opened 8 ticks" in gap.sessions[1].note


def test_new_rules_validate_strictly() -> None:
    bad = [
        (
            {
                "level": "opening_range",
                "sides": "both",
                "sweep_min_ticks": 2,
                "reclaim": "close_back_inside",
                "reclaim_within_bars": 3,
            },
            "range_minutes",
        ),
        (
            {
                "level": "prior_session",
                "range_minutes": 5,
                "sides": "both",
                "sweep_min_ticks": 2,
                "reclaim": "close_back_inside",
                "reclaim_within_bars": 3,
            },
            "range_minutes",
        ),
    ]
    for rule, needle in bad:
        with pytest.raises(FuturesSpecError) as err:
            spec("level_sweep_reclaim", rule, {"type": "ticks", "ticks": 8}, {"type": "none"})
        assert needle in err.value.message
    with pytest.raises(FuturesSpecError):  # setup_extreme belongs to sweep/retest rules
        orb(exits={"stop": {"type": "setup_extreme", "ticks": 1}, "target": {"type": "none"}})
    with pytest.raises(FuturesSpecError):  # width filters need an opening range
        raw = template("ma_crossover", "NQ")
        raw["filters"] = {"max_range_ticks": 10}
        parse(raw)
    for kind in ("level_sweep_reclaim", "opening_range_retest"):
        assert describe(parse(template(kind, "NQ")))
