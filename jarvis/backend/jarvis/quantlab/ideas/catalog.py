"""Ambiguous trading vocabulary and its operational definitions — fixed in code.

Influencer terms ("liquidity sweep", "retest", "NY open") don't define a rule.
For each, this catalog lists clearly different operational definitions that the
rule language can execute, each as a patch to the spec. A chosen definition is
part of the hypothesis: other definitions are separate hypothesis families, and
testing them counts as further trials. Terms without an operator in the rule
language are listed as unsupported — never silently mapped to something else.

The catalog is deterministic; models may pick from it but can't extend it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Alternative:
    id: str
    label: str
    definition: str
    patch: dict[str, Any] = field(default_factory=dict)
    supported: bool = True


@dataclass(frozen=True)
class Term:
    id: str
    name: str
    pattern: re.Pattern[str]
    why: str
    rules: tuple[str, ...]  # rule types the term matters for ("*" = any)
    alternatives: tuple[Alternative, ...]
    default: str | None
    material: bool  # changes the hypothesis: an open choice blocks the test

    def alternative(self, alt_id: str) -> Alternative | None:
        return next((a for a in self.alternatives if a.id == alt_id), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "why": self.why,
            "material": self.material,
            "default": self.default,
            "alternatives": [
                {
                    "id": a.id,
                    "label": a.label,
                    "definition": a.definition,
                    "patch": a.patch,
                    "supported": a.supported,
                }
                for a in self.alternatives
            ],
        }


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


SWEEP_RULE = ("level_sweep_reclaim",)
OR_RULES = ("opening_range_breakout", "opening_range_retest", "level_sweep_reclaim")

TERMS: tuple[Term, ...] = (
    Term(
        "ny_open",
        "“New York open”",
        _re(r"\b(new york|ny|nyse|cash|us(?: equity| stock)?|stock market)\s+(open|opening)\b"),
        "Several clocks are called the New York open; each starts a different window.",
        ("*",),
        (
            Alternative(
                "rth_0930",
                "09:30 New York",
                "US equity cash open (NYSE), 09:30 America/New_York — follows US daylight saving.",
                {"session.start": "09:30"},
            ),
            Alternative(
                "data_0830",
                "08:30 New York",
                "US economic data releases at 08:30 "
                "America/New_York; futures trade, the cash market isn't open yet.",
                {"session.start": "08:30"},
            ),
            Alternative(
                "globex_1800",
                "18:00 New York (Globex reopen)",
                "The futures session reopening the evening before — needs overnight positions.",
                {},
                supported=False,
            ),
        ),
        "rth_0930",
        True,
    ),
    Term(
        "opening_range",
        "Opening range length",
        _re(r"\b(opening range|ORB|first (\d{1,2}|five|fifteen|thirty) ?(min|minute)s?)\b"),
        "The range's length decides its high and low — and with them every later signal.",
        OR_RULES,
        (
            Alternative(
                "or_5",
                "5 minutes",
                "High and low of the first 5 one-minute bars.",
                {"rule.range_minutes": 5},
            ),
            Alternative(
                "or_15",
                "15 minutes",
                "High and low of the first 15 one-minute bars.",
                {"rule.range_minutes": 15},
            ),
            Alternative(
                "or_30",
                "30 minutes",
                "High and low of the first 30 one-minute bars.",
                {"rule.range_minutes": 30},
            ),
        ),
        "or_15",
        True,
    ),
    Term(
        "liquidity_sweep",
        "“Liquidity sweep” / “reclaim”",
        _re(
            r"\b(liquidity\s+(sweep|grab|raid)|sweep(s|ing|ed)?\s+(of\s+)?(the\s+)?(liquidity|high|low|level)|stop\s+hunt|reclaim(s|ed)?)\b"
        ),
        "How far beyond the level counts as a sweep, and what counts as getting back inside, "
        "are not defined by the words.",
        SWEEP_RULE,
        (
            Alternative(
                "sweep_1t_same_bar",
                "1 tick beyond, back inside on the same bar",
                "A 1-minute bar trades at least 1 tick beyond the level and closes back inside it.",
                {
                    "rule.sweep_min_ticks": 1,
                    "rule.reclaim": "close_back_inside",
                    "rule.reclaim_within_bars": 1,
                },
            ),
            Alternative(
                "sweep_4t_close_5",
                "1 point beyond, a close back inside within 5 min",
                "Price trades at least 4 ticks (1 NQ point) beyond the level; a 1-minute "
                "bar closes back inside within 5 bars.",
                {
                    "rule.sweep_min_ticks": 4,
                    "rule.reclaim": "close_back_inside",
                    "rule.reclaim_within_bars": 5,
                },
            ),
            Alternative(
                "sweep_4t_stop_5",
                "1 point beyond, trades back through within 5 min",
                "Price trades at least 4 ticks beyond; afterwards a stop order at the "
                "level fills when price trades back through it within 5 bars.",
                {
                    "rule.sweep_min_ticks": 4,
                    "rule.reclaim": "stop_back_through",
                    "rule.reclaim_within_bars": 5,
                    "rule.entry_buffer_ticks": 0,
                },
            ),
        ),
        "sweep_4t_close_5",
        True,
    ),
    Term(
        "retest",
        "“Retest”",
        _re(r"\b(re-?test(s|ed|ing)?|pull ?back to (the )?(level|breakout))\b"),
        "How close to the level, how soon after the breakout, and whether the bar must hold "
        "beyond it are open.",
        ("opening_range_retest",),
        (
            Alternative(
                "retest_touch_10",
                "Exact touch within 10 min",
                "A bar touches the broken level and closes beyond it within 10 bars.",
                {"rule.retest_tolerance_ticks": 0, "rule.retest_within_bars": 10},
            ),
            Alternative(
                "retest_2t_10",
                "Within 2 ticks within 10 min",
                "A bar comes within 2 ticks of the level and closes beyond it within 10 bars.",
                {"rule.retest_tolerance_ticks": 2, "rule.retest_within_bars": 10},
            ),
            Alternative(
                "retest_4t_20",
                "Within 1 point within 20 min",
                "A bar comes within 4 ticks of the level and closes beyond it within 20 bars.",
                {"rule.retest_tolerance_ticks": 4, "rule.retest_within_bars": 20},
            ),
        ),
        "retest_2t_10",
        True,
    ),
    Term(
        "stop_beyond_wick",
        "Stop “above/below the wick”",
        _re(r"\bstop\b[^.]{0,40}\b(wick|sweep|swing high|swing low|the high|the low)\b"),
        "The distance beyond the wick (and tick rounding) is not stated.",
        ("level_sweep_reclaim", "opening_range_retest"),
        (
            Alternative(
                "wick_1t",
                "1 tick beyond",
                "Stop 1 tick beyond the setup's extreme.",
                {"exits.stop.type": "setup_extreme", "exits.stop.ticks": 1},
            ),
            Alternative(
                "wick_4t",
                "1 point beyond",
                "Stop 4 ticks beyond the setup's extreme.",
                {"exits.stop.type": "setup_extreme", "exits.stop.ticks": 4},
            ),
        ),
        "wick_1t",
        False,
    ),
    Term(
        "r_multiple",
        "“R” targets",
        _re(r"\b(\d+(\.\d+)?\s?R\b|(\d|two|three)\s*(to|:)\s*(1|one)\b|risk[- /]?reward)"),
        "R is defined here as the distance from the actual fill to the stop at entry.",
        ("*",),
        (
            Alternative(
                "r_fill_to_stop",
                "Fill to stop",
                "1R = |fill price − initial stop|; "
                "targets are placed at fill ± k·R, rounded away to the next tick.",
            ),
        ),
        "r_fill_to_stop",
        False,
    ),
    Term(
        "candle_timeframe",
        "Candle timeframe for closes",
        _re(r"\b(\d{1,2})\s?-?(m|min|minute)\s*(chart|candle|close|bar|timeframe)s?\b"),
        "“A close back inside” on a 5-minute candle is a different rule from a 1-minute close.",
        (*SWEEP_RULE, "opening_range_retest", "opening_range_breakout"),
        (
            Alternative("m1", "1-minute closes", "Signals use completed 1-minute bars."),
            Alternative(
                "htf_close",
                "Higher-timeframe closes",
                "Signals on 5- or 15-minute candle closes — not yet in the rule "
                "language for this setup (only the MA crossover has bar_minutes).",
                supported=False,
            ),
        ),
        "m1",
        True,
    ),
)

UNSUPPORTED: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("fvg", "Fair value gaps / imbalances", _re(r"\b(fvg|fair value gaps?|imbalances?)\b")),
    ("order_block", "Order blocks", _re(r"\border ?blocks?\b")),
    (
        "structure",
        "Market structure shift / break of structure",
        _re(r"\b(market structure|mss|choch|change of character|bos|break of structure)\b"),
    ),
    ("smc", "Smart-money concepts (umbrella term)", _re(r"\b(smart money|smc|ict)\b")),
    ("vwap", "VWAP", _re(r"\bvwap\b")),
    (
        "indicator",
        "Indicators other than simple moving averages",
        _re(r"\b(ema|rsi|macd|bollinger|stoch(astic)?|atr|fibonacci|fib)\b"),
    ),
    (
        "news",
        "News / economic-calendar days",
        _re(r"\b(news|cpi|fomc|nfp|fed day|economic calendar)\b"),
    ),
    ("london", "London session levels", _re(r"\blondon\b")),
    (
        "confirmation_candle",
        "Confirmation / engulfing candles",
        _re(r"\b(confirmation candle|engulfing|pin ?bar|hammer)\b"),
    ),
    (
        "scaling",
        "Scaling in or out, partial exits",
        _re(r"\b(scale (in|out)|partials?|take half|runner)\b"),
    ),
)
REASONS = {
    "fvg": "No gap/imbalance operator in the rule language yet; definitions vary widely.",
    "order_block": "No order-block operator; the term has no single objective definition.",
    "structure": "No swing-structure operator yet (needs a defined swing algorithm).",
    "smc": "An umbrella term — the concrete rules underneath have to be named.",
    "vwap": "No VWAP operator yet (needs volume-weighted prices per session).",
    "indicator": "Only simple moving averages of closes exist in the rule language.",
    "news": "No economic-calendar data is connected, so news days can't be identified.",
    "london": "No London-session level yet (a window high/low operator is planned).",
    "confirmation_candle": "Candle patterns aren't in the rule language yet.",
    "scaling": "Positions are one size in, one exit out.",
}

PERFORMANCE = _re(
    r"(\b\d{1,3}(\.\d+)?\s?%\s*(win(ning)?\s*rate|wins?|accuracy|accurate|of the time|success|"
    r"profitable)|\bwin(ning)?\s*rate\b|\bnever (lose|loses|lost)\b|\b(guaranteed|easy money|"
    r"free money|can'?t lose)\b|\bmade\s+\$?\d[\d,.]*\s*k?\b|\$\d[\d,.]*\s*k?\s*(a|per|every)\s*"
    r"(day|week|month)|\b(ninety|eighty|seventy)[- ]?(percent|%)|\bwins?\s+\d{1,3}\s?(%|percent)|"
    r"\bprofit factor\b|\bsharpe\b)"
)


def relevant(term: Term, rule_type: str | None) -> bool:
    return "*" in term.rules or (rule_type is not None and rule_type in term.rules)


def detect(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Which catalog terms, unsupported concepts and performance claims the text contains."""
    hits: dict[str, list[str]] = {}
    unsupported: dict[str, list[str]] = {}
    performance: list[dict[str, str]] = []
    for seg in segments:
        text, sid = seg["text"], seg["id"]
        for term in TERMS:
            if term.pattern.search(text):
                hits.setdefault(term.id, []).append(sid)
        for key, _name, pattern in UNSUPPORTED:
            if pattern.search(text):
                unsupported.setdefault(key, []).append(sid)
        for match in PERFORMANCE.finditer(text):
            performance.append({"segment": sid, "text": match.group(0)})
    return {"terms": hits, "unsupported": unsupported, "performance": performance}


BY_ID = {t.id: t for t in TERMS}
UNSUPPORTED_NAMES = {key: name for key, name, _ in UNSUPPORTED}


def apply_patch(raw: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """A copy of a spec dict with dotted-path values set."""
    import copy

    out = copy.deepcopy(raw)
    for path, value in patch.items():
        node = out
        keys = path.split(".")
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
    return out


def get_path(raw: dict[str, Any], path: str) -> Any:
    node: Any = raw
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node
