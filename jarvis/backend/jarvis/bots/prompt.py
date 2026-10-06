"""What the research model reads when it improves one of the user's MT5 EAs."""

from __future__ import annotations

import json
from typing import Any

from jarvis.llm.base import ToolDefinition

SYSTEM_PROMPT = """You improve one of the user's MetaTrader 5 Expert Advisors (MQL5) that trades gold (XAUUSD). JARVIS compiles your versions and runs them in MetaTrader's strategy tester; you never trade and nothing you write reaches a live account — the user reviews and installs versions themselves.

The goal is an EA that would really make money from now on: a robust edge after spread and commission, steady months, and drawdowns inside prop-firm limits (stated in the briefing). Dollar targets are reached by position size later — JARVIS works that out. Do not chase a dollar number, and do not raise risk per trade to make a backtest look bigger.

How results are judged:
- You see the in-sample period only (full statistics and the monthly table). Iterate there.
- validate checks a backtest on the out-of-sample period and tells you pass or fail with a reason, no numbers. The bar rises with every validation of this bot, so validate only what you believe in.
- A holdout period after that is never shown to you; it only informs the user.

What makes an improvement real:
- One idea per version, with a reason grounded in how gold trades (sessions, volatility, spread, news spikes, trend vs. range, support/resistance) — not a number tuned until the past looks good.
- Fewer conditions and parameters beat more. Round, plausible parameter values. If a change only works for one exact value, it isn't real.
- Read the monthly table: an improvement that comes from one or two months is luck.
- Risk controls that help a prop-firm account count: daily loss stop, max open risk, no martingale/grid escalation, spread filter, session filter, no trading into high-impact news spikes if the EA can detect them from price.
- Check the trade count: fewer than about 60 in-sample trades can't be judged.

Editing:
- create_version applies exact search/replace edits to a base version's source. Each "find" must match the current source exactly once — copy it verbatim, including indentation. Keep edits small and focused.
- Keep it compiling: valid MQL5, declare what you use. JARVIS returns compiler errors; fix them with another create_version on the same base.
- Never add DLL imports (#import), WebRequest, sockets, file operations or anything outside the trading logic — such versions are refused.
- Don't define OnTester (JARVIS adds its own to read the results).
- Changing an input's default value in the source is how a version gets new parameters. backtest can also override inputs for a run.

Each round: look at what was tried (versions, results, notes), form one or two hypotheses, create versions, backtest them, validate only convincing ones, write a short note about what you learned, then call finish_round. Backtests take minutes; you have a limited number per round."""

TOOLS = [
    ToolDefinition(
        name="create_version",
        description=(
            "Create a new version from a base version by exact search/replace edits. JARVIS "
            "compiles it and returns the version number or the compiler errors."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "base_version": {"type": "integer", "minimum": 0},
                "title": {"type": "string", "maxLength": 80},
                "hypothesis": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Why this should be a real improvement",
                },
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "properties": {
                            "find": {"type": "string"},
                            "replace": {"type": "string"},
                        },
                        "required": ["find", "replace"],
                    },
                },
            },
            "required": ["base_version", "title", "hypothesis", "edits"],
        },
    ),
    ToolDefinition(
        name="backtest",
        description=(
            "Run a version in MetaTrader's strategy tester (takes minutes). Returns in-sample "
            "statistics, the monthly table and the prop-firm check. Optional input overrides."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "version": {"type": "integer", "minimum": 0},
                "inputs": {
                    "type": "object",
                    "description": "Input name → value (as text), only those to change",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["version"],
        },
    ),
    ToolDefinition(
        name="validate",
        description=(
            "Out-of-sample check of a finished backtest (by its test number). Pass or fail with "
            "a reason, no numbers. The bar rises with every validation."
        ),
        input_schema={
            "type": "object",
            "properties": {"test": {"type": "integer", "minimum": 1}},
            "required": ["test"],
        },
    ),
    ToolDefinition(
        name="write_note",
        description="Record a lesson about this EA for later rounds (one or two sentences).",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 600}},
            "required": ["text"],
        },
    ),
    ToolDefinition(
        name="finish_round",
        description="End this round with a one-sentence summary.",
        input_schema={
            "type": "object",
            "properties": {"summary": {"type": "string", "maxLength": 400}},
            "required": ["summary"],
        },
    ),
]

MAX_SOURCE_CHARS = 160_000


def briefing(
    *,
    bot: str,
    settings: dict[str, Any],
    periods: dict[str, str],
    prop: dict[str, Any],
    inputs: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    tests: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    validations: int,
    next_bar: float,
    budget_left: float,
    backtests_per_round: int,
    base: dict[str, Any],
) -> str:
    source = base["source"]
    if len(source) > MAX_SOURCE_CHARS:
        source = source[:MAX_SOURCE_CHARS] + "\n// … (truncated: the file is very long)"
    history = [
        {
            "version": v["number"],
            "base": v["parent"],
            "title": v["title"],
            "hypothesis": v["hypothesis"],
            "compiled": v["compiled"],
        }
        for v in versions
    ]
    runs = [
        {
            "test": t["number"],
            "version": t["version"],
            "inputs": t["inputs"],
            "status": t["status"],
            "in_sample": _headline(t["in_sample"]),
            "validated": t["validated"],
            "validation": t["validation_reason"],
        }
        for t in tests
    ]
    parts = [
        f"EA: {bot}. Tester: {settings['symbol']} {settings['period']}, model {settings['model']}, "
        f"deposit {settings['deposit']:g} {settings.get('currency', 'USD')}, leverage 1:{settings['leverage']}.",
        f"In-sample period: {periods['in_sample']}. (Out-of-sample and holdout come after it.)",
        f"Prop-firm limits to respect: daily loss < {prop['daily_loss_pct']} %, "
        f"max loss < {prop['max_loss_pct']} % (closed trades).",
        f"Validations so far for this EA: {validations}; the next one needs out-of-sample "
        f"t ≥ {next_bar:.2f}.",
        f"Budget left today: ${budget_left:.2f}. Backtests this round: {backtests_per_round}.",
        "Inputs of the original: " + json.dumps(inputs),
        "Versions: " + json.dumps(history),
        "Backtests (newest first): " + json.dumps(runs),
        "Notes: " + json.dumps([n["text"] for n in notes][-20:]),
        f"Source of version {base['number']} ({base['title']}) — base your edits on it:",
        "```mql5\n" + source + "\n```",
    ]
    return "\n\n".join(parts)


def _headline(stats: dict[str, Any] | None) -> dict[str, Any] | None:
    if not stats:
        return None
    keys = ("trades", "net", "profit_factor", "win_rate", "return_pct", "t_stat",
            "max_drawdown_pct", "max_daily_loss_pct", "median_month_pct", "worst_month_pct",
            "positive_months", "months")  # fmt: skip
    return {k: stats.get(k) for k in keys}
