"""The strategy language the research model writes — data, never code.

A strategy is JSON: a session, one or two entry rules and exits. Rules are
small arithmetic comparisons over a fixed set of features, e.g.

    "close crosses_above or_high(15)"
    "close > vwap + 0.5 * atr(14)"
    "rsi(2)[1] < 10"                      ([k] = value k bars ago)

They are parsed here by a tiny recursive-descent parser into a tree that
``features.py`` evaluates with numpy. Nothing the model writes is executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Features ---------------------------------------------------------------------------

ArgKind = Literal["period", "minutes", "time"]


@dataclass(frozen=True)
class FeatureSpec:
    args: tuple[ArgKind, ...]
    help: str


FEATURES: dict[str, FeatureSpec] = {
    # bar
    "open": FeatureSpec((), "bar open"),
    "high": FeatureSpec((), "bar high"),
    "low": FeatureSpec((), "bar low"),
    "close": FeatureSpec((), "bar close"),
    "volume": FeatureSpec((), "bar volume (Dukascopy quote volume, relative only)"),
    "range": FeatureSpec((), "high - low"),
    "body": FeatureSpec((), "close - open"),
    # indicators (all bars, also outside the session)
    "sma": FeatureSpec(("period",), "simple moving average of close"),
    "ema": FeatureSpec(("period",), "exponential moving average of close"),
    "rsi": FeatureSpec(("period",), "Wilder RSI of close, 0-100"),
    "atr": FeatureSpec(("period",), "Wilder average true range, in points"),
    "stdev": FeatureSpec(("period",), "rolling standard deviation of close"),
    "highest": FeatureSpec(("period",), "highest high of the last n bars incl. this one"),
    "lowest": FeatureSpec(("period",), "lowest low of the last n bars incl. this one"),
    "vol_sma": FeatureSpec(("period",), "simple moving average of volume"),
    "change": FeatureSpec(("period",), "close - close n bars ago"),
    # session (the strategy's session; NaN outside it)
    "vwap": FeatureSpec((), "session VWAP, starting at the session start"),
    "session_open": FeatureSpec((), "first open of the session"),
    "session_high": FeatureSpec((), "session high so far"),
    "session_low": FeatureSpec((), "session low so far"),
    "or_high": FeatureSpec(("minutes",), "high of the session's first m minutes (unknown before)"),
    "or_low": FeatureSpec(("minutes",), "low of the session's first m minutes (unknown before)"),
    "prev_high": FeatureSpec((), "previous session's high"),
    "prev_low": FeatureSpec((), "previous session's low"),
    "prev_close": FeatureSpec((), "previous session's last close"),
    "minutes": FeatureSpec((), "minutes since the session start at this bar's close"),
    "weekday": FeatureSpec((), "0 = Monday … 4 = Friday (local)"),
    # time windows in the session's time zone, may cross midnight
    "window_high": FeatureSpec(("time", "time"), "high of the last completed local time window"),
    "window_low": FeatureSpec(("time", "time"), "low of the last completed local time window"),
}

COMPARATORS = (">=", "<=", ">", "<", "crosses_above", "crosses_below")
MAX_PERIOD = 500
MAX_LAG = 500


class RuleError(ValueError):
    pass


# Expression tree ------------------------------------------------------------------


@dataclass(frozen=True)
class Num:
    value: float


@dataclass(frozen=True)
class Ref:
    name: str
    args: tuple[int, ...]  # periods / minutes; times as minutes of day
    lag: int = 0


@dataclass(frozen=True)
class BinOp:
    op: str
    left: Node
    right: Node


@dataclass(frozen=True)
class Neg:
    operand: Node


Node = Num | Ref | BinOp | Neg


@dataclass(frozen=True)
class Condition:
    left: Node
    op: str
    right: Node


_TOKEN = re.compile(
    r"\s*(?:(?P<time>\d{1,2}:\d{2})|(?P<num>\d+(?:\.\d+)?|\.\d+)"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>>=|<=|[-+*/()\[\],<>]))"
)


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise RuleError(f"unexpected character at {text[pos : pos + 10]!r}")
        kind = match.lastgroup or ""
        value = match.group(kind)
        out.append((kind, value.lower() if kind == "name" else value))
        pos = match.end()
    return out


class _Parser:
    def __init__(self, text: str) -> None:
        if len(text) > 200:
            raise RuleError("rules are limited to 200 characters")
        self.tokens = _tokens(text)
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise RuleError("the rule ends too early")
        self.i += 1
        return token

    def expect(self, value: str) -> None:
        token = self.take()
        if token[1] != value:
            raise RuleError(f"expected {value!r}, found {token[1]!r}")

    def done(self) -> None:
        token = self.peek()
        if token is not None:
            raise RuleError(f"unexpected {token[1]!r}")

    def expr(self) -> Node:
        node = self.term()
        while (token := self.peek()) and token[1] in "+-" and token[0] == "op":
            self.take()
            node = BinOp(token[1], node, self.term())
        return node

    def term(self) -> Node:
        node = self.factor()
        while (token := self.peek()) and token[1] in "*/" and token[0] == "op":
            self.take()
            node = BinOp(token[1], node, self.factor())
        return node

    def factor(self) -> Node:
        kind, value = self.take()
        if kind == "op" and value == "-":
            return Neg(self.factor())
        if kind == "op" and value == "(":
            node = self.expr()
            self.expect(")")
            return node
        if kind == "num":
            return Num(float(value))
        if kind == "name":
            return self.ref(value)
        raise RuleError(f"unexpected {value!r}")

    def ref(self, name: str) -> Ref:
        spec = FEATURES.get(name)
        if spec is None:
            raise RuleError(f"unknown feature {name!r} (known: {', '.join(FEATURES)})")
        args: list[int] = []
        if spec.args:
            self.expect("(")
            for index, kind in enumerate(spec.args):
                if index:
                    self.expect(",")
                args.append(self.arg(name, kind))
            self.expect(")")
        elif (token := self.peek()) and token[1] == "(":
            raise RuleError(f"{name} takes no arguments")
        lag = 0
        if (token := self.peek()) and token[1] == "[":
            self.take()
            token_kind, value = self.take()
            if token_kind != "num" or not value.isdigit() or not 0 <= int(value) <= MAX_LAG:
                raise RuleError(f"bars-ago index must be a whole number from 0 to {MAX_LAG}")
            lag = int(value)
            self.expect("]")
        return Ref(name, tuple(args), lag)

    def arg(self, name: str, kind: ArgKind) -> int:
        token_kind, value = self.take()
        if kind == "time":
            if token_kind != "time":
                raise RuleError(f"{name} takes times like 19:00")
            hours, minutes = (int(p) for p in value.split(":"))
            if hours > 23 or minutes > 59:
                raise RuleError(f"{value} is not a time of day")
            return hours * 60 + minutes
        if token_kind != "num" or not value.isdigit():
            raise RuleError(f"{name} takes a whole number")
        number = int(value)
        limit = MAX_PERIOD if kind == "period" else 360
        if not 1 <= number <= limit:
            raise RuleError(f"{name}({number}): the number must be 1 to {limit}")
        return number


def parse_expression(text: str) -> Node:
    parser = _Parser(text)
    node = parser.expr()
    parser.done()
    return node


def parse_condition(text: str) -> Condition:
    parser = _Parser(text)
    left = parser.expr()
    token = parser.take()
    if token[1] not in COMPARATORS:
        raise RuleError(f"expected one of {', '.join(COMPARATORS)}, found {token[1]!r}")
    right = parser.expr()
    parser.done()
    return Condition(left, token[1], right)


def refs(node: Node) -> list[Ref]:
    if isinstance(node, Ref):
        return [node]
    if isinstance(node, BinOp):
        return refs(node.left) + refs(node.right)
    if isinstance(node, Neg):
        return refs(node.operand)
    return []


# Strategy ---------------------------------------------------------------------------

Instrument = Literal["NQ", "XAUUSD"]
Style = Literal["scalping", "daytrading"]
Timeframe = Literal["1m", "2m", "3m", "5m", "10m", "15m", "30m"]
TIMEFRAME_MINUTES = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30}
STYLE_TIMEFRAMES: dict[str, set[str]] = {
    "scalping": {"1m", "2m", "3m", "5m"},
    "daytrading": {"5m", "10m", "15m", "30m"},
}
SCALP_MAX_MINUTES = 60
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def clock_minutes(value: str) -> int:
    match = _HHMM.match(value)
    if not match:
        raise ValueError(f"{value!r} is not a time like 09:30")
    return int(match.group(1)) * 60 + int(match.group(2))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Session(_Strict):
    start: str
    end: str
    tz: Literal["America/New_York", "Europe/London"] = "America/New_York"

    @model_validator(mode="after")
    def _order(self) -> Session:
        if clock_minutes(self.start) >= clock_minutes(self.end):
            raise ValueError("the session must start before it ends on the same day")
        return self


class ExitRule(_Strict):
    stop: str = Field(description="stop distance from the entry, in points (an expression)")
    target: str | None = Field(None, description="target distance in points (an expression)")
    target_r: float | None = Field(None, gt=0, le=20, description="target in multiples of the stop")
    max_minutes: int | None = Field(None, ge=1, le=1440, description="time stop")

    @field_validator("stop", "target")
    @classmethod
    def _expression(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                parse_expression(value)
            except RuleError as exc:
                raise ValueError(str(exc)) from exc
        return value

    @model_validator(mode="after")
    def _one_target(self) -> ExitRule:
        if self.target is not None and self.target_r is not None:
            raise ValueError("give either target or target_r, not both")
        return self


class Entry(_Strict):
    side: Literal["long", "short"]
    when: list[str] = Field(min_length=1, max_length=6)
    exit: ExitRule | None = None

    @field_validator("when")
    @classmethod
    def _rules(cls, rules: list[str]) -> list[str]:
        for rule in rules:
            try:
                parse_condition(rule)
            except RuleError as exc:
                raise ValueError(f"{rule!r}: {exc}") from exc
        return rules


class Strategy(_Strict):
    name: str = Field(min_length=1, max_length=80)
    hypothesis: str = Field(min_length=1, max_length=600)
    instrument: Instrument
    style: Style
    timeframe: Timeframe
    session: Session
    entries: list[Entry] = Field(min_length=1, max_length=2)
    exit: ExitRule | None = None
    max_trades_per_day: int = Field(3, ge=1, le=20)

    @model_validator(mode="after")
    def _consistent(self) -> Strategy:
        if self.timeframe not in STYLE_TIMEFRAMES[self.style]:
            allowed = ", ".join(
                sorted(STYLE_TIMEFRAMES[self.style], key=lambda tf: TIMEFRAME_MINUTES[tf])
            )
            raise ValueError(f"{self.style} uses the timeframes {allowed}")
        for entry in self.entries:
            rule = entry.exit or self.exit
            if rule is None:
                raise ValueError("every entry needs an exit (on the entry or the strategy)")
            if self.style == "scalping" and (
                rule.max_minutes is None or rule.max_minutes > SCALP_MAX_MINUTES
            ):
                raise ValueError(f"scalps need max_minutes of at most {SCALP_MAX_MINUTES}")
        tf = TIMEFRAME_MINUTES[self.timeframe]
        for entry in self.entries:
            for text in entry.when:
                condition = parse_condition(text)
                for ref in refs(condition.left) + refs(condition.right):
                    if ref.name in ("or_high", "or_low") and ref.args[0] % tf:
                        raise ValueError(
                            f"{ref.name}({ref.args[0]}) must be a multiple of the {tf}-minute bars"
                        )
        return self

    def exit_for(self, entry: Entry) -> ExitRule:
        rule = entry.exit or self.exit
        assert rule is not None
        return rule

    @property
    def bar_minutes(self) -> int:
        return TIMEFRAME_MINUTES[self.timeframe]
