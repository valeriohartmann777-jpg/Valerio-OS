"""Timeframes for a strategy: what's compatible, what changes, and the run's effective spec.

A strategy (a versioned FuturesSpec) holds the rules. The Backtest Lab runs it on a symbol
and a timeframe the owner picks; both are part of the run's identity, never edits of the
strategy. This module decides — before anything runs — whether a strategy can be tested on
a timeframe at all (``issues`` block), and what behaves differently there (``notes``:
an opening range of 15 minutes is three 5-minute bars, a time exit at 15:55 happens at the
open of the 15-minute bar holding 15:55, "within 3 bars" is 15 minutes, …).

``effective_spec`` binds a strategy to the chosen product and timeframe:

* ``instrument`` becomes the chosen product's local series (NQ ↔ MNQ is an explicit choice
  of the owner; tick values come from the contract master, never from the strategy);
* costs come from the strategy when the product is the strategy's own, otherwise from the
  chosen cost profile (an explicit, visible assumption);
* parameter-grid values that can't be measured on the timeframe (a 10-minute range on
  15-minute bars) are left out — and listed — instead of being computed wrongly.
"""

from __future__ import annotations

import re
from typing import Any

from jarvis.quantlab.futures.contracts import REFERENCE
from jarvis.quantlab.futures.spec import (
    FuturesSpec,
    MovingAverageCrossover,
    clock,
    parse,
    range_minutes_of,
)

TIMEFRAMES: tuple[int, ...] = (1, 2, 3, 5, 10, 15, 30, 60)
_LABEL = re.compile(r"^\s*(\d{1,3})\s*(m|min|h)?\s*$", re.IGNORECASE)

# Execution-cost profiles: explicit assumptions the owner sees and can override (Advanced
# settings). They are starting points, not broker quotes: set your own broker's numbers.
COST_PROFILES: dict[str, dict[str, Any]] = {
    "intraday_conservative": {
        "label": "Intraday Futures Conservative",
        "note": "Retail-broker-like commissions plus CME/NFA fees and 1 tick slippage per "
        "side — an assumption, not a quote. Replace with your broker's numbers.",
        "products": {
            "NQ": {"commission": 2.00, "fees": 1.40, "slippage_ticks": 1},
            "MNQ": {"commission": 0.50, "fees": 0.37, "slippage_ticks": 1},
            "ES": {"commission": 2.00, "fees": 1.40, "slippage_ticks": 1},
            "MES": {"commission": 0.50, "fees": 0.37, "slippage_ticks": 1},
        },
    },
    "intraday_stress": {
        "label": "Intraday Futures Stress",
        "note": "Double the conservative costs and 2 ticks slippage per side.",
        "products": {
            "NQ": {"commission": 4.00, "fees": 2.80, "slippage_ticks": 2},
            "MNQ": {"commission": 1.00, "fees": 0.74, "slippage_ticks": 2},
            "ES": {"commission": 4.00, "fees": 2.80, "slippage_ticks": 2},
            "MES": {"commission": 1.00, "fees": 0.74, "slippage_ticks": 2},
        },
    },
}


def label(minutes: int) -> str:
    return "1h" if minutes == 60 else f"{minutes}m"


def parse_timeframe(value: Any) -> int:
    """'5m', '5', 5, '1h' → minutes. Raises ``ValueError`` for anything else."""
    if isinstance(value, int) and not isinstance(value, bool):
        minutes = value
    else:
        match = _LABEL.match(str(value))
        if match is None:
            raise ValueError(f"Unknown timeframe {value!r}.")
        minutes = int(match.group(1)) * (60 if (match.group(2) or "").lower() == "h" else 1)
    if minutes not in TIMEFRAMES:
        raise ValueError(
            f"Timeframe {label(minutes)} isn't offered; choose one of "
            + ", ".join(label(m) for m in TIMEFRAMES)
            + "."
        )
    return minutes


def _minute_of_day(text: str) -> int:
    t = clock(text)
    return t.hour * 60 + t.minute


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def compatibility(spec: FuturesSpec, minutes: int, source_minutes: int = 1) -> dict[str, Any]:
    """Can ``spec`` be tested on ``minutes`` bars built from ``source_minutes`` bars?"""
    issues: list[dict[str, str]] = []
    notes: list[str] = []
    tf = label(minutes)
    if minutes not in TIMEFRAMES:
        issues.append(_issue("TIMEFRAME", f"{tf} isn't an offered timeframe."))
    if source_minutes > minutes or minutes % source_minutes:
        issues.append(
            _issue(
                "WRONG_GRANULARITY",
                f"The local data is {label(source_minutes)}; {tf} bars can't be built from it "
                "without inventing prices inside a bar.",
            )
        )
    s = spec.session
    start = _minute_of_day(s.start)
    window = _minute_of_day(s.end) - start
    if minutes > window:
        issues.append(
            _issue("WINDOW", f"The {window}-minute session window is shorter than one {tf} bar.")
        )
    rule = spec.rule
    span = range_minutes_of(rule)
    if span is not None and span % minutes:
        issues.append(
            _issue(
                "OPENING_RANGE",
                f"The {span}-minute opening range ends inside a {tf} bar — it can't be measured "
                f"on {tf} bars. Use a timeframe that divides {span} minutes.",
            )
        )
    elif span is not None and minutes > 1:
        notes.append(f"Opening range: the first {span // minutes} {tf} bar(s) ({span} minutes).")
    if isinstance(rule, MovingAverageCrossover):
        if rule.bar_minutes > 1 and (rule.bar_minutes < minutes or rule.bar_minutes % minutes):
            issues.append(
                _issue(
                    "MA_BARS",
                    f"The averages use {rule.bar_minutes}-minute bars, which can't be built "
                    f"from {tf} bars.",
                )
            )
        elif rule.bar_minutes <= 1 and minutes > 1:
            notes.append(
                f"Moving averages count {tf} bars: SMA({rule.fast}) vs SMA({rule.slow}) spans "
                f"{rule.fast * minutes} vs {rule.slow * minutes} minutes."
            )
    if minutes > 1:
        if s.flatten_at and (_minute_of_day(s.flatten_at) - start) % minutes:
            at = _minute_of_day(s.flatten_at) - start
            bar_open = start + (at // minutes) * minutes
            notes.append(
                f"Time exit: at the open of the {tf} bar holding {s.flatten_at} "
                f"({bar_open // 60:02d}:{bar_open % 60:02d}), i.e. earlier than on 1m."
            )
        if s.entry_cutoff and (_minute_of_day(s.entry_cutoff) - start) % minutes:
            notes.append(
                f"Entry cutoff {s.entry_cutoff} falls inside a {tf} bar: stop entries need the "
                "whole bar before the cutoff."
            )
        if spec.filters.entry_after and (_minute_of_day(spec.filters.entry_after) - start) % (
            minutes
        ):
            notes.append(
                f"Entries after {spec.filters.entry_after}: from the first {tf} bar starting "
                "at or after it."
            )
        if window % minutes:
            notes.append(
                f"The last {tf} bar of each session is {window % minutes} minutes long "
                "(cut at the session end, flagged as partial)."
            )
        counted = []
        if spec.execution.latency_bars:
            counted.append(f"latency {spec.execution.latency_bars} bar(s)")
        for name in ("reclaim_within_bars", "retest_within_bars"):
            value = getattr(rule, name, None)
            if value:
                counted.append(f"{name.replace('_', ' ')} {value}")
        if counted:
            notes.append(
                f"Counted in {tf} bars: " + ", ".join(counted) + f" (1 bar = {minutes} minutes)."
            )
        notes.append(
            f"Within one {tf} bar the order of high and low is unknown: stop-vs-target bars "
            "are resolved conservatively (stop first) and the optimistic bound is reported."
        )
        dropped = _dropped_grid(spec, minutes)
        if dropped:
            notes.append(
                "Grid values that can't be measured on "
                + tf
                + " are left out: "
                + ", ".join(f"{k} {v:g}" for k, v in dropped)
                + "."
            )
    return {
        "minutes": minutes,
        "label": tf,
        "compatible": not issues,
        "issues": issues,
        "notes": notes,
    }


def _dropped_grid(spec: FuturesSpec, minutes: int) -> list[tuple[str, float]]:
    dropped = []
    for value in spec.validation.parameter_grid.get("range_minutes", []):
        if int(value) % minutes:
            dropped.append(("range_minutes", float(value)))
    return dropped


def effective_spec(
    spec: FuturesSpec,
    *,
    product: str,
    symbol: str,
    minutes: int,
    cost_profile: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[FuturesSpec, list[str]]:
    """The spec as this run executes it (the strategy version itself is unchanged)."""
    if product not in REFERENCE:
        raise ValueError(f"No contract master for {product}.")
    data = spec.model_dump(mode="json")
    notes: list[str] = []
    data["instrument"] = {
        "product": product,
        "dataset": "GLBX.MDP3",
        "symbol": symbol,
        "stype_in": "continuous" if "." in symbol else "raw_symbol",
    }
    if product != spec.instrument.product:
        notes.append(
            f"Strategy written for {spec.instrument.product}, run on {product}: the rules "
            "(ticks, times) are unchanged; tick value and multiplier come from the "
            f"{product} contract master."
        )
    profile_name = cost_profile
    if profile_name is None and product != spec.instrument.product:
        profile_name = "intraday_conservative"
    if profile_name is not None:
        profile = COST_PROFILES.get(profile_name)
        if profile is None:
            raise ValueError(f"Unknown cost profile {profile_name!r}.")
        costs = profile["products"][product]
        data["costs"] = {
            "commission_per_contract_side": costs["commission"],
            "exchange_fees_per_contract_side": costs["fees"],
            "currency": "USD",
        }
        data["execution"]["slippage_ticks"] = costs["slippage_ticks"]
        notes.append(
            f"Costs: {profile['label']} for {product} — ${costs['commission']:.2f} commission + "
            f"${costs['fees']:.2f} fees per contract and side, {costs['slippage_ticks']} tick "
            "slippage (assumption)."
        )
    for key, value in (overrides or {}).items():
        if key == "contracts":
            data["sizing"]["contracts"] = int(value)
        elif key == "account_capital":
            data["sizing"]["account_capital"] = float(value)
        elif key == "direction" and "direction" in data["rule"]:
            data["rule"]["direction"] = str(value)
        elif key == "commission_per_contract_side":
            data["costs"]["commission_per_contract_side"] = float(value)
        elif key == "exchange_fees_per_contract_side":
            data["costs"]["exchange_fees_per_contract_side"] = float(value)
        elif key == "slippage_ticks":
            data["execution"]["slippage_ticks"] = int(value)
        elif key == "limit_fill":
            data["execution"]["limit_fill"] = str(value)
        else:
            raise ValueError(f"Unknown advanced setting {key!r}.")
        notes.append(f"Advanced setting: {key} = {value}.")
    grid = data["validation"].get("parameter_grid") or {}
    if "range_minutes" in grid and minutes > 1:
        kept = [v for v in grid["range_minutes"] if int(v) % minutes == 0]
        if kept:
            grid["range_minutes"] = kept
        else:
            grid.pop("range_minutes")
    return parse(data), notes
