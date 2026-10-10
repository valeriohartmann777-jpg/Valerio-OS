"""FuturesSpec 1.0 — the restricted, versioned rule language for intraday futures research.

Only what the engine implements exactly can be written down: two rule templates
(opening-range breakout, intraday moving-average crossover), stop/target/time
exits, explicit costs, fixed contract sizing and a pre-registered validation plan.
Unknown fields are refused; nothing here is executed as code. Every value an AI
or a template filled in without the user saying so is listed in ``assumptions``;
an ``unknown`` assumption blocks the run until the user resolves it.
"""

from __future__ import annotations

import itertools
import re
from datetime import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jarvis.quantlab.spec import canonical_json, sha256_text

SPEC_VERSION = "1.0"
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
GRID_KEYS = {
    "opening_range_breakout": {"range_minutes", "buffer_ticks", "target_value", "stop_ticks"},
    "ma_crossover": {"fast", "slow", "target_value", "stop_ticks"},
    "level_sweep_reclaim": {
        "range_minutes", "sweep_min_ticks", "reclaim_within_bars", "target_value", "stop_ticks",
    },
    "opening_range_retest": {
        "range_minutes", "retest_within_bars", "retest_tolerance_ticks", "target_value",
        "stop_ticks",
    },
}  # fmt: skip
INT_PARAMS = (
    "range_minutes", "buffer_ticks", "fast", "slow", "sweep_min_ticks", "reclaim_within_bars",
    "retest_within_bars", "retest_tolerance_ticks",
)  # fmt: skip
OR_RULES = ("opening_range_breakout", "opening_range_retest")
MAX_GRID = 60


class FuturesSpecError(ValueError):
    def __init__(self, code: str, message: str, details: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Clock = Annotated[str, Field(pattern=_CLOCK.pattern)]


def clock(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


class Instrument(_Strict):
    product: Literal["NQ", "MNQ", "ES", "MES"]
    dataset: Literal["GLBX.MDP3"] = "GLBX.MDP3"
    symbol: str = Field(pattern=r"^(NQ|MNQ|ES|MES)(\.[cvn]\.0|[HMUZ]\d{1,2})$")
    stype_in: Literal["continuous", "raw_symbol"]

    @model_validator(mode="after")
    def _consistent(self) -> Instrument:
        root = re.match(r"^(MNQ|MES|NQ|ES)", self.symbol)
        if root is None or root.group(1) != self.product:
            raise ValueError(f"symbol {self.symbol} isn't a {self.product} contract")
        continuous = "." in self.symbol
        if continuous != (self.stype_in == "continuous"):
            raise ValueError("stype_in must be 'continuous' for X.v.0 symbols, else 'raw_symbol'")
        return self


class Session(_Strict):
    calendar: Literal["XNYS"] = "XNYS"
    timezone: Literal["America/New_York"] = "America/New_York"
    start: Clock = "09:30"
    end: Clock = "16:00"
    flatten_at: Clock = "15:55"
    entry_cutoff: Clock | None = None
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], min_length=1)

    @model_validator(mode="after")
    def _order(self) -> Session:
        start, end, flat = clock(self.start), clock(self.end), clock(self.flatten_at)
        if not start < flat <= end:
            raise ValueError("session times must satisfy start < flatten_at ≤ end")
        if self.entry_cutoff and not start < clock(self.entry_cutoff) <= flat:
            raise ValueError("entry_cutoff must be after start and not after flatten_at")
        if any(d not in range(5) for d in self.weekdays):
            raise ValueError("weekdays are 0 (Mon) … 4 (Fri)")
        return self


class OpeningRangeBreakout(_Strict):
    type: Literal["opening_range_breakout"]
    range_minutes: int = Field(ge=1, le=120)
    entry: Literal["stop_through_range", "close_beyond_range"]
    direction: Literal["long", "short", "both"]
    buffer_ticks: int = Field(0, ge=0, le=200)
    max_trades_per_session: Literal[1] = 1


class MovingAverageCrossover(_Strict):
    type: Literal["ma_crossover"]
    fast: int = Field(ge=1, le=500)
    slow: int = Field(ge=2, le=1000)
    direction: Literal["long", "short", "both"]
    price: Literal["close"] = "close"
    # Averages of N-minute bars built from the 1-minute data; an N-minute bar is only
    # known when its period has ended. Left out of the canonical JSON when 1.
    bar_minutes: int = Field(1, ge=1, le=60, exclude_if=lambda v: v == 1)

    @model_validator(mode="after")
    def _windows(self) -> MovingAverageCrossover:
        if self.fast >= self.slow:
            raise ValueError("fast must be shorter than slow")
        return self


class LevelSweepReclaim(_Strict):
    """Fade a sweep: price trades beyond a known level, then comes back inside it.

    ``fade_highs``: a sweep above the level's high, then a reclaim below it → short.
    ``fade_lows``: a sweep below the level's low, then a reclaim above it → long.
    """

    type: Literal["level_sweep_reclaim"]
    level: Literal["opening_range", "prior_session"]
    range_minutes: int | None = Field(None, ge=1, le=120)
    sides: Literal["fade_highs", "fade_lows", "both"]
    sweep_min_ticks: int = Field(ge=1, le=400)
    reclaim: Literal["close_back_inside", "stop_back_through"]
    reclaim_within_bars: int = Field(ge=1, le=120)
    entry_buffer_ticks: int = Field(0, ge=0, le=100)
    max_trades_per_session: Literal[1] = 1

    @model_validator(mode="after")
    def _level(self) -> LevelSweepReclaim:
        if self.level == "opening_range" and self.range_minutes is None:
            raise ValueError("an opening-range level needs range_minutes")
        if self.level == "prior_session" and self.range_minutes is not None:
            raise ValueError("range_minutes only applies to the opening-range level")
        return self


class OpeningRangeRetest(_Strict):
    """Breakout, then a retest of the broken level that holds, then entry."""

    type: Literal["opening_range_retest"]
    range_minutes: int = Field(ge=1, le=120)
    direction: Literal["long", "short", "both"]
    breakout_buffer_ticks: int = Field(0, ge=0, le=200)
    retest_within_bars: int = Field(ge=1, le=120)
    retest_tolerance_ticks: int = Field(0, ge=0, le=200)
    max_trades_per_session: Literal[1] = 1


Rule = Annotated[
    OpeningRangeBreakout | MovingAverageCrossover | LevelSweepReclaim | OpeningRangeRetest,
    Field(discriminator="type"),
]


def has_opening_range(rule: Any) -> bool:
    return rule.type in OR_RULES or (
        rule.type == "level_sweep_reclaim" and rule.level == "opening_range"
    )


def range_minutes_of(rule: Any) -> int | None:
    return getattr(rule, "range_minutes", None) if has_opening_range(rule) else None


class Stop(_Strict):
    # setup_extreme: beyond the most extreme price of the setup (the sweep's wick, the
    # retest's low/high), by ``ticks``.
    type: Literal["range_opposite", "ticks", "none", "setup_extreme"]
    ticks: int | None = Field(None, ge=1, le=10_000)


class Target(_Strict):
    type: Literal["r_multiple", "ticks", "none"]
    value: float | None = Field(None, gt=0, le=10_000)


class Exits(_Strict):
    stop: Stop
    target: Target


class Execution(_Strict):
    market_fill: Literal["next_bar_open"] = "next_bar_open"
    slippage_ticks: int = Field(ge=0, le=50)
    limit_fill: Literal["trade_through", "touch"] = "trade_through"
    same_bar_ambiguity: Literal["conservative"] = "conservative"
    latency_bars: int = Field(0, ge=0, le=5)


class Costs(_Strict):
    commission_per_contract_side: float = Field(ge=0, le=100)
    exchange_fees_per_contract_side: float = Field(ge=0, le=100)
    currency: Literal["USD"] = "USD"


class Sizing(_Strict):
    contracts: int = Field(ge=1, le=100)
    account_capital: float = Field(gt=0, le=1e9)
    account_currency: Literal["USD"] = "USD"
    initial_margin_per_contract: float | None = Field(None, ge=0, le=1e7)


class WalkForward(_Strict):
    train_sessions: int = Field(60, ge=20, le=2000)
    test_sessions: int = Field(20, ge=5, le=1000)
    mode: Literal["rolling", "anchored"] = "rolling"


class Bootstrap(_Strict):
    block_sessions: int = Field(5, ge=1, le=60)
    samples: int = Field(2000, ge=200, le=20_000)
    seed: int = Field(7, ge=0, le=2**31 - 1)


class Validation(_Strict):
    oos_fraction: float = Field(0.3, ge=0.1, le=0.5)
    holdout_fraction: float = Field(0.15, ge=0.0, le=0.3)
    embargo_sessions: int = Field(1, ge=0, le=10)
    min_trades_oos: int = Field(30, ge=5, le=5000)
    parameter_grid: dict[str, list[float]] = Field(default_factory=dict)
    walk_forward: WalkForward = Field(default_factory=WalkForward)
    cost_stress: list[float] = Field(default_factory=lambda: [2.0, 3.0], max_length=4)
    bootstrap: Bootstrap = Field(default_factory=Bootstrap)


class Filters(_Strict):
    """Optional day and entry filters; each is left out of the canonical JSON when unused."""

    min_range_ticks: int | None = Field(None, ge=1, le=100_000, exclude_if=lambda v: v is None)
    max_range_ticks: int | None = Field(None, ge=1, le=100_000, exclude_if=lambda v: v is None)
    entry_after: Clock | None = Field(None, exclude_if=lambda v: v is None)
    # Longs only when the entry reference is above the prior session's close, shorts only below.
    prior_close_bias: bool = Field(False, exclude_if=lambda v: v is False)
    # Skip the day when the first bar opens this far from the prior session's close.
    max_gap_ticks: int | None = Field(None, ge=1, le=100_000, exclude_if=lambda v: v is None)

    @property
    def active(self) -> bool:
        return self != Filters()


class Assumption(_Strict):
    field: str = Field(min_length=1, max_length=80)
    state: Literal["confirmed", "assumed", "unknown"]
    note: str = Field("", max_length=400)


class FuturesSpec(_Strict):
    schema_version: Literal["1.0"]
    kind: Literal["futures_intraday"]
    name: str = Field(min_length=3, max_length=120)
    hypothesis: str = Field(min_length=5, max_length=1500)
    instrument: Instrument
    session: Session
    rule: Rule
    exits: Exits
    execution: Execution
    costs: Costs
    sizing: Sizing
    validation: Validation = Field(default_factory=Validation)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=60)
    filters: Filters = Field(default_factory=Filters, exclude_if=lambda f: not f.active)

    @model_validator(mode="after")
    def _coherent(self) -> FuturesSpec:
        rule, stop, target = self.rule, self.exits.stop, self.exits.target
        if stop.type == "ticks" and stop.ticks is None:
            raise ValueError("a 'ticks' stop needs exits.stop.ticks")
        if stop.type == "range_opposite" and not has_opening_range(rule):
            raise ValueError("a range_opposite stop needs a rule with an opening range")
        if stop.type == "setup_extreme":
            if rule.type not in ("level_sweep_reclaim", "opening_range_retest"):
                raise ValueError("a setup_extreme stop needs a sweep or retest rule")
            if stop.ticks is None:
                raise ValueError("a setup_extreme stop needs exits.stop.ticks (distance beyond)")
        if target.type != "none" and target.value is None:
            raise ValueError("a target needs exits.target.value")
        if target.type == "r_multiple" and stop.type == "none":
            raise ValueError("an R-multiple target needs a stop (R is the stop distance)")
        span = range_minutes_of(rule)
        if span is not None:
            start = clock(self.session.start)
            minutes = start.hour * 60 + start.minute + span
            flat = clock(self.session.flatten_at)
            if minutes >= flat.hour * 60 + flat.minute:
                raise ValueError("the opening range must end before flatten_at")
        f = self.filters
        if (f.min_range_ticks or f.max_range_ticks) and span is None:
            raise ValueError("range-width filters need a rule with an opening range")
        if f.min_range_ticks and f.max_range_ticks and f.min_range_ticks > f.max_range_ticks:
            raise ValueError("filters.min_range_ticks must not exceed max_range_ticks")
        if f.entry_after and not (
            clock(self.session.start) < clock(f.entry_after) < clock(self.session.flatten_at)
        ):
            raise ValueError("filters.entry_after must fall between session start and flatten_at")
        allowed = GRID_KEYS[rule.type]
        unknown = set(self.validation.parameter_grid) - allowed
        if unknown:
            raise ValueError(f"parameter_grid keys allowed for {rule.type}: {sorted(allowed)}")
        combos = 1
        for values in self.validation.parameter_grid.values():
            if not values or len(values) > 12:
                raise ValueError("each grid parameter needs 1–12 values")
            combos *= len(values)
        if combos > MAX_GRID:
            raise ValueError(f"the parameter grid has {combos} combinations (max {MAX_GRID})")
        if any(k <= 1 for k in self.validation.cost_stress):
            raise ValueError("cost_stress multipliers must be > 1")
        return self

    def canonical(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    def sha256(self) -> str:
        return sha256_text(self.canonical())

    @property
    def unresolved(self) -> list[Assumption]:
        return [a for a in self.assumptions if a.state == "unknown"]

    def with_params(self, params: dict[str, float]) -> FuturesSpec:
        """A variant with grid parameters applied (validated like any spec)."""
        data = self.model_dump(mode="json")
        for key, value in params.items():
            if key in INT_PARAMS:
                data["rule"][key] = int(value)
            elif key == "target_value":
                data["exits"]["target"]["value"] = float(value)
            elif key == "stop_ticks":
                data["exits"]["stop"]["ticks"] = int(value)
        return parse(data)

    def grid(self) -> list[dict[str, float]]:
        grid = self.validation.parameter_grid
        if not grid:
            return []
        keys = sorted(grid)
        return [
            dict(zip(keys, combo, strict=True))
            for combo in itertools.product(*(grid[k] for k in keys))
        ]

    def current_params(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for key in self.validation.parameter_grid:
            if key in INT_PARAMS and getattr(self.rule, key, None) is not None:
                out[key] = float(getattr(self.rule, key))
            elif key == "target_value" and self.exits.target.value is not None:
                out[key] = float(self.exits.target.value)
            elif key == "stop_ticks" and self.exits.stop.ticks is not None:
                out[key] = float(self.exits.stop.ticks)
        return out


def parse(raw: Any) -> FuturesSpec:
    if not isinstance(raw, dict):
        raise FuturesSpecError("SPEC_INVALID", "A strategy spec must be a JSON object.")
    try:
        return FuturesSpec.model_validate(raw)
    except ValidationError as exc:
        details = [
            {"field": ".".join(str(p) for p in err["loc"]) or "(spec)", "problem": err["msg"]}
            for err in exc.errors()
        ]
        summary = "; ".join(f"{d['field']}: {d['problem']}" for d in details[:4])
        raise FuturesSpecError(
            "SPEC_INVALID", f"The spec isn't valid — {summary}", details
        ) from exc


def describe(spec: FuturesSpec) -> list[str]:
    """The rules in plain language, shown (and confirmed) before anything runs."""
    s, rule, ex, costs, size = spec.session, spec.rule, spec.execution, spec.costs, spec.sizing
    lines = [
        f"Trade {size.contracts} × {spec.instrument.product} ({spec.instrument.symbol}, "
        f"{spec.instrument.dataset}) on 1-minute bars, {s.start}–{s.end} New York time on NYSE "
        "trading days (holidays skipped, early closes shorten the day).",
    ]
    direction = getattr(rule, "direction", "both")
    sides = {"long": "long only", "short": "short only", "both": "long or short"}[direction]
    if isinstance(rule, OpeningRangeBreakout):
        lines.append(
            f"Opening range: high and low of the first {rule.range_minutes} minutes after "
            f"{s.start}; known only once the range's last bar has closed."
        )
        if rule.entry == "stop_through_range":
            lines.append(
                f"Entry ({sides}): a stop order {rule.buffer_ticks} tick(s) beyond the range; it "
                "fills at the stop price or the bar's open if price gaps through, plus slippage. "
                "At most one trade per day."
            )
        else:
            lines.append(
                f"Entry ({sides}): when a completed bar closes {rule.buffer_ticks} tick(s) beyond "
                "the range, a market order fills at the next bar's open plus slippage. At most "
                "one trade per day."
            )
        lines.append(
            "If both sides of the range trigger inside one minute, the order of events is unknown "
            "from 1-minute bars: that day is excluded and counted, never guessed."
        )
    elif isinstance(rule, MovingAverageCrossover):
        unit = "1-minute" if rule.bar_minutes == 1 else f"{rule.bar_minutes}-minute"
        lines.append(
            f"Entry ({sides}): when SMA({rule.fast}) of {unit} closes crosses SMA({rule.slow}) "
            "on a completed bar (averages restart each session), a market order fills at the "
            "next 1-minute bar's open plus slippage; an opposite cross reverses the position."
        )
        if rule.bar_minutes > 1:
            lines.append(
                f"{unit.capitalize()} bars are built from the 1-minute data from {s.start}; a "
                f"{unit} bar is only known once its {rule.bar_minutes} minutes have passed."
            )
    elif isinstance(rule, LevelSweepReclaim):
        if rule.level == "opening_range":
            lines.append(
                f"Level: high and low of the first {rule.range_minutes} minutes after {s.start}; "
                "usable only once the range's last bar has closed."
            )
        else:
            lines.append(
                f"Level: the previous trading session's high and low ({s.start}–{s.end}, same "
                "contract); a day after a contract roll has no level and is skipped."
            )
        fade = {
            "fade_highs": "short after a sweep of the high",
            "fade_lows": "long after a sweep of the low",
            "both": "short after a sweep of the high, long after a sweep of the low",
        }[rule.sides]
        lines.append(
            f"Sweep: price trades at least {rule.sweep_min_ticks} tick(s) beyond the level "
            "(a side only counts after price has closed back inside the level)."
        )
        if rule.reclaim == "close_back_inside":
            lines.append(
                f"Entry ({fade}): a completed bar closes back inside the level within "
                f"{rule.reclaim_within_bars} bar(s) of the sweep's first bar (that bar included); "
                "a market order fills at the next bar's open plus slippage. At most one trade "
                "per day."
            )
        else:
            lines.append(
                f"Entry ({fade}): after the sweep bar has closed, a stop order "
                f"{rule.entry_buffer_ticks} tick(s) back inside the level works for the next "
                f"{rule.reclaim_within_bars} bar(s); it fills at its price or the open if price "
                "gaps through, plus slippage. At most one trade per day."
            )
        lines.append(
            "If both sides trigger on the same bar, the order of events is unknown from "
            "1-minute bars: that day is excluded and counted, never guessed."
        )
    elif isinstance(rule, OpeningRangeRetest):
        lines.append(
            f"Opening range: high and low of the first {rule.range_minutes} minutes after "
            f"{s.start}; known only once the range's last bar has closed."
        )
        lines.append(
            f"Breakout ({sides}): a completed bar closes more than {rule.breakout_buffer_ticks} "
            "tick(s) beyond the range."
        )
        lines.append(
            f"Retest: within {rule.retest_within_bars} bar(s) after the breakout bar, a bar "
            f"comes back to within {rule.retest_tolerance_ticks} tick(s) of the broken level and "
            "still closes beyond it → a market order fills at the next bar's open plus slippage. "
            "A close back inside the range cancels the breakout. At most one trade per day."
        )
    stop, target = spec.exits.stop, spec.exits.target
    if stop.type == "range_opposite":
        buffer = " (with the same buffer)" if isinstance(rule, OpeningRangeBreakout) else ""
        lines.append(f"Stop: the opposite side of the opening range{buffer}.")
    elif stop.type == "setup_extreme":
        lines.append(
            f"Stop: {stop.ticks} tick(s) beyond the most extreme price of the setup (the sweep's "
            "wick or the retest's extreme), as known from completed bars when the order is placed."
        )
    elif stop.type == "ticks":
        lines.append(f"Stop: {stop.ticks} ticks from the entry price.")
    else:
        lines.append("Stop: none (only the time exit limits a loss).")
    if target.type == "r_multiple":
        lines.append(f"Target: {target.value:g} × the stop distance (R).")
    elif target.type == "ticks":
        lines.append(f"Target: {target.value:g} ticks from the entry price.")
    else:
        lines.append("Target: none.")
    f = spec.filters
    if f.min_range_ticks or f.max_range_ticks:
        low = f"{f.min_range_ticks} ticks" if f.min_range_ticks else "any width"
        high = f"{f.max_range_ticks} ticks" if f.max_range_ticks else "no maximum"
        lines.append(f"Filter: trade only days whose opening range is {low} to {high} wide.")
    if f.entry_after:
        lines.append(
            f"Filter: signals before {f.entry_after} New York time are ignored and stop orders "
            "only work from then on."
        )
    if f.prior_close_bias:
        lines.append(
            "Filter: longs only when the entry reference (the stop price, or the signal bar's "
            "close for market entries) is above the prior session's close, shorts only below it."
        )
    if f.max_gap_ticks:
        lines.append(
            f"Filter: skip days that open more than {f.max_gap_ticks} ticks away from the prior "
            "session's close."
        )
    lines.append(
        f"Flat by {s.flatten_at} (market order at the next bar's open); no position is held "
        "overnight or across a contract roll."
    )
    fill = "only if price trades through it" if ex.limit_fill == "trade_through" else "on touch"
    lines.append(
        f"Fills: stops and market orders get {ex.slippage_ticks} tick(s) slippage; a target fills "
        f"{fill}. If a stop and a target are both inside one bar, the stop is assumed first "
        "(conservative); the optimistic case is reported as a bound."
    )
    lines.append(
        f"Costs: ${costs.commission_per_contract_side:.2f} commission + "
        f"${costs.exchange_fees_per_contract_side:.2f} exchange fees per contract per side. "
        f"Account: ${size.account_capital:,.0f}."
    )
    v = spec.validation
    lines.append(
        f"Validation fixed in advance: last {v.holdout_fraction:.0%} of sessions sealed as a final "
        f"holdout, the {v.oos_fraction:.0%} before it out-of-sample, {v.embargo_sessions} "
        f"session(s) embargo between segments; at least {v.min_trades_oos} out-of-sample trades."
    )
    return lines


NAMES = {
    "opening_range_breakout": "opening range breakout",
    "ma_crossover": "MA crossover",
    "level_sweep_reclaim": "opening range sweep and reclaim",
    "opening_range_retest": "opening range breakout and retest",
}


def template(kind: str = "opening_range_breakout", product: str = "NQ") -> dict[str, Any]:
    """Editable starting points. Not trading signals — every value is marked as an assumption."""
    rule: dict[str, Any]
    grid: dict[str, list[float]]
    if kind == "ma_crossover":
        rule = {"type": "ma_crossover", "fast": 10, "slow": 30, "direction": "both"}
        exits = {"stop": {"type": "ticks", "ticks": 40}, "target": {"type": "none"}}
        grid = {"fast": [5, 10, 15], "slow": [30, 45]}
    elif kind == "level_sweep_reclaim":
        rule = {
            "type": "level_sweep_reclaim",
            "level": "opening_range",
            "range_minutes": 15,
            "sides": "both",
            "sweep_min_ticks": 2,
            "reclaim": "close_back_inside",
            "reclaim_within_bars": 5,
        }
        exits = {
            "stop": {"type": "setup_extreme", "ticks": 2},
            "target": {"type": "r_multiple", "value": 2},
        }
        grid = {"sweep_min_ticks": [1, 2, 4, 8], "reclaim_within_bars": [1, 3, 5]}
    elif kind == "opening_range_retest":
        rule = {
            "type": "opening_range_retest",
            "range_minutes": 15,
            "direction": "both",
            "breakout_buffer_ticks": 0,
            "retest_within_bars": 10,
            "retest_tolerance_ticks": 2,
        }
        exits = {"stop": {"type": "range_opposite"}, "target": {"type": "r_multiple", "value": 2}}
        grid = {"retest_within_bars": [5, 10, 20], "retest_tolerance_ticks": [0, 2, 4]}
    else:
        rule = {
            "type": "opening_range_breakout",
            "range_minutes": 15,
            "entry": "stop_through_range",
            "direction": "both",
            "buffer_ticks": 1,
        }
        exits = {"stop": {"type": "range_opposite"}, "target": {"type": "r_multiple", "value": 2}}
        grid = {"range_minutes": [10, 15, 20, 30], "target_value": [1.5, 2, 3]}
    symbol = f"{product}.v.0"
    return {
        "schema_version": SPEC_VERSION,
        "kind": "futures_intraday",
        "name": f"{product} {NAMES.get(kind, 'opening range breakout')}",
        "hypothesis": "Template — describe what you expect and why before testing it.",
        "instrument": {"product": product, "dataset": "GLBX.MDP3", "symbol": symbol,
                       "stype_in": "continuous"},
        "session": {"calendar": "XNYS", "timezone": "America/New_York", "start": "09:30",
                    "end": "16:00", "flatten_at": "15:55", "entry_cutoff": "12:00",
                    "weekdays": [0, 1, 2, 3, 4]},
        "rule": rule,
        "exits": exits,
        "execution": {"market_fill": "next_bar_open", "slippage_ticks": 1,
                      "limit_fill": "trade_through", "same_bar_ambiguity": "conservative",
                      "latency_bars": 0},
        "costs": {"commission_per_contract_side": 2.25, "exchange_fees_per_contract_side": 1.38,
                  "currency": "USD"},
        "sizing": {"contracts": 1, "account_capital": 50_000, "account_currency": "USD"},
        "validation": {"oos_fraction": 0.3, "holdout_fraction": 0.15, "embargo_sessions": 1,
                       "min_trades_oos": 30, "parameter_grid": grid,
                       "walk_forward": {"train_sessions": 60, "test_sessions": 20,
                                        "mode": "rolling"},
                       "cost_stress": [2, 3],
                       "bootstrap": {"block_sessions": 5, "samples": 2000, "seed": 7}},
        "assumptions": [
            {"field": "costs.commission_per_contract_side", "state": "assumed",
             "note": "Typical retail rate, not your broker's quote — confirm or change."},
            {"field": "costs.exchange_fees_per_contract_side", "state": "assumed",
             "note": "Approximate CME + NFA fees; schedules change over time."},
            {"field": "execution.slippage_ticks", "state": "assumed",
             "note": "One tick per market/stop fill; stress tests multiply it."},
            {"field": "sizing.account_capital", "state": "assumed",
             "note": "Only the denominator for returns; no margin model."},
        ],
    }  # fmt: skip
