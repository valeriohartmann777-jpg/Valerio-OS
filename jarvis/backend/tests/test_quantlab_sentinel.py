"""SENTINEL's second engine agrees with the primary engine, trade by trade.

Both engines run on a few hundred sessions of a random walk sampled inside each
minute (so stops, targets, sweeps and retests all happen, including same-bar
conflicts and gaps) for every rule family and many variants, in conservative and
optimistic mode and under cost stress. Any difference in a single field of a
single trade fails the test.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

import numpy as np
import pytest

from jarvis.quantlab.futures.engine import MINUTE, Bars, simulate
from jarvis.quantlab.futures.sessions import Window
from jarvis.quantlab.futures.spec import parse, template
from jarvis.quantlab.ideas import sentinel
from tests.test_quantlab_futures import BASE, NQ, NS

TICK = 250_000_000


def walk(sessions: int, seed: int, sub: int = 12) -> tuple[Bars, list[Window]]:
    rng = np.random.default_rng(seed)
    minutes = 390
    ts, o, h, lo, c, wins = [], [], [], [], [], []
    level = 80_000
    for s in range(sessions):
        start = BASE + s * 86_400 * NS
        steps = rng.choice([-2, -1, 0, 1, 2], size=minutes * sub, p=[0.1, 0.3, 0.2, 0.3, 0.1])
        gap = int(rng.integers(-30, 31))  # overnight gaps, for the gap and prior-close filters
        path = level + gap + np.cumsum(steps).reshape(minutes, sub)
        opens = np.concatenate(([level + gap], path[:-1, -1]))
        ts.append(start + np.arange(minutes) * MINUTE)
        o.append(opens)
        h.append(np.maximum(opens, path.max(axis=1)))
        lo.append(np.minimum(opens, path.min(axis=1)))
        c.append(path[:, -1])
        level = int(path[-1, -1])
        wins.append(
            Window(
                date.fromordinal(737_425 + s),
                start,
                start + minutes * MINUTE,
                start + (minutes - 5) * MINUTE,
                start + 180 * MINUTE,
                start + minutes * MINUTE,
                False,
            )
        )

    def cat(parts: list[np.ndarray], scale: int = TICK) -> np.ndarray:
        return (np.concatenate(parts).astype(np.int64)) * scale

    ids = np.full(sessions * minutes, NQ.instrument_id, dtype=np.int64)
    return Bars(cat(ts, 1), ids, cat(o), cat(h), cat(lo), cat(c)), wins


def make(
    kind: str, rule: dict[str, Any] | None = None, exits: dict[str, Any] | None = None, **top: Any
) -> Any:
    raw = template(kind, "NQ")
    if rule:
        raw["rule"].update(rule)
    if exits:
        raw["exits"] = exits
    raw["session"]["entry_cutoff"] = None
    raw["validation"]["parameter_grid"] = {}
    for key, value in top.items():
        if key == "execution":
            raw["execution"].update(value)
        else:
            raw[key] = value
    return parse(raw)


TARGET_2R = {"type": "r_multiple", "value": 2}
CASES = {
    "orb stop entries": make("opening_range_breakout"),
    "orb close entries, latency, tick exits": make(
        "opening_range_breakout",
        {"entry": "close_beyond_range", "direction": "short"},
        {"stop": {"type": "ticks", "ticks": 20}, "target": {"type": "ticks", "value": 30}},
        execution={"latency_bars": 1},
    ),
    "orb filtered": make(
        "opening_range_breakout",
        {"range_minutes": 10},
        filters={
            "min_range_ticks": 20,
            "max_range_ticks": 120,
            "entry_after": "10:00",
            "prior_close_bias": True,
        },
    ),
    "orb touch fills": make("opening_range_breakout", execution={"limit_fill": "touch"}),
    "ma 1-minute": make("ma_crossover"),
    "ma 5-minute bars": make(
        "ma_crossover",
        {"fast": 3, "slow": 8, "bar_minutes": 5},
        {"stop": {"type": "ticks", "ticks": 20}, "target": {"type": "ticks", "value": 40}},
        filters={"entry_after": "09:45"},
    ),
    "sweep close back inside": make("level_sweep_reclaim"),
    "sweep stop back through": make(
        "level_sweep_reclaim",
        {
            "reclaim": "stop_back_through",
            "entry_buffer_ticks": 1,
            "sweep_min_ticks": 3,
            "reclaim_within_bars": 4,
        },
    ),
    "sweep prior session": make(
        "level_sweep_reclaim",
        {
            "level": "prior_session",
            "range_minutes": None,
            "reclaim_within_bars": 3,
            "sweep_min_ticks": 2,
        },
        {
            "stop": {"type": "setup_extreme", "ticks": 2},
            "target": {"type": "r_multiple", "value": 1.5},
        },
    ),
    "sweep with gap filter": make("level_sweep_reclaim", filters={"max_gap_ticks": 20}),
    "orb tight exits (many same-bar conflicts)": make(
        "opening_range_breakout",
        exits={"stop": {"type": "ticks", "ticks": 2}, "target": {"type": "ticks", "value": 3}},
    ),
    "sweep stop back through, tight": make(
        "level_sweep_reclaim",
        {"reclaim": "stop_back_through", "reclaim_within_bars": 6},
        {
            "stop": {"type": "setup_extreme", "ticks": 1},
            "target": {"type": "r_multiple", "value": 0.5},
        },
    ),
    "retest range stop": make("opening_range_retest"),
    "retest extreme stop": make(
        "opening_range_retest",
        {"retest_tolerance_ticks": 4, "breakout_buffer_ticks": 2},
        {"stop": {"type": "setup_extreme", "ticks": 1}, "target": TARGET_2R},
    ),
}


def key(t: Any) -> tuple[Any, ...]:
    return (
        t.session,
        t.entry_bar_ts,
        t.direction,
        t.entry_ticks,
        t.exit_bar_ts,
        t.exit_ticks,
        t.exit_reason,
        t.net,
        t.ambiguous,
    )


def ref_key(t: sentinel.RefTrade) -> tuple[Any, ...]:
    return (
        t.session,
        t.entry_ts,
        t.direction,
        t.entry_ticks,
        t.exit_ts,
        t.exit_ticks,
        t.reason,
        t.net,
        t.ambiguous,
    )


@pytest.fixture(scope="module")
def data() -> tuple[Bars, list[Window]]:
    return walk(240, seed=5)


@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.parametrize("mode", ["conservative", "optimistic"])
def test_second_engine_agrees_trade_by_trade(
    name: str, mode: Literal["conservative", "optimistic"], data: tuple[Bars, list[Window]]
) -> None:
    spec = CASES[name]
    bars, wins = data
    contracts = {NQ.instrument_id: NQ}
    for multiplier in (1.0, 2.0):
        primary = simulate(
            spec,
            bars,
            contracts,
            wins,
            mode=mode,
            cost_multiplier=multiplier,  # type: ignore[arg-type]
            record_equity=False,
        )
        second = sentinel.recompute(
            spec, bars, contracts, wins, mode=mode, cost_multiplier=multiplier
        )
        a = [key(t) for t in primary.trades]
        b = [ref_key(t) for t in second]
        assert len(a) >= 15, f"{name}: too few trades to mean anything ({len(a)})"
        diff = [(x, y) for x, y in zip(a, b, strict=False) if x != y]
        assert a == b, (name, mode, multiplier, len(a), len(b), diff[:3])


def test_split_runs_use_the_real_previous_session(data: tuple[Bars, list[Window]]) -> None:
    """Simulating every other day must still use each day's actual predecessor."""
    spec = CASES["sweep prior session"]
    bars, wins = data
    contracts = {NQ.instrument_id: NQ}
    picked = wins[1::2]
    primary = simulate(spec, bars, contracts, picked, context=wins, record_equity=False)
    second = sentinel.recompute(spec, bars, contracts, picked, context=wins)
    assert [key(t) for t in primary.trades] == [ref_key(t) for t in second]
    naive = simulate(spec, bars, contracts, picked, record_equity=False)
    assert [key(t) for t in naive.trades] != [key(t) for t in primary.trades]


def test_compare_reports_each_disagreement() -> None:
    bars, wins = walk(30, seed=9)
    spec = CASES["orb stop entries"]
    primary = simulate(spec, bars, {NQ.instrument_id: NQ}, wins, record_equity=False)
    rows: list[dict[str, Any]] = [
        {
            "session": t.session,
            "direction": "LONG" if t.direction > 0 else "SHORT",
            "entry_time": _iso(t.entry_bar_ts),
            "exit_time": _iso(t.exit_bar_ts),
            "entry_price": t.entry_ticks * 0.25,
            "exit_price": t.exit_ticks * 0.25,
            "exit_reason": t.exit_reason,
            "net": float(t.net),
        }
        for t in primary.trades
    ]
    ref = sentinel.recompute(spec, bars, {NQ.instrument_id: NQ}, wins)
    clean = sentinel.compare(rows, ref, 0.25)
    assert clean["mismatch_count"] == 0 and clean["agree"] == len(rows) > 5
    rows[2] = {**rows[2], "exit_price": rows[2]["exit_price"] + 1.0, "net": rows[2]["net"] + 20}
    del rows[4]
    broken = sentinel.compare(rows, ref, 0.25)
    assert broken["mismatch_count"] == 2
    assert any("exit" in m and "net" in m for m in broken["mismatches"])
    assert any("the ledger doesn't" in m for m in broken["mismatches"])


def _iso(ns: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ns / NS, UTC).isoformat().replace("+00:00", "Z")


def test_narrative_guard_refuses_invented_numbers() -> None:
    evidence = "OOS net +$1,363.50 on 25 trades; max drawdown $8,821.70; win rate 28.8%"
    assert (
        sentinel.narrative_guard("Out of sample it made $1,363.50 over 25 trades.", evidence) == []
    )
    assert sentinel.narrative_guard("It has a 96% chance to work and made $5,000.", evidence) == [
        "96%",
        "$5,000."[:-1],
    ]
