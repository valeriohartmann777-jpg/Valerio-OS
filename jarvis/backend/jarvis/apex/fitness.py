"""Data fitness: is this series fit for an APEX analysis, and if not, exactly why.

Computed on the bars an analysis would use now (``Series.until``), with the market calendar
of ``data.trading`` (weekend and the daily 17:00-18:00 New York break are not trading
minutes, so they are never "missing"). Nothing here changes a bar or loosens the quality
gate: it explains the gate's verdict with counts and examples.

- **Coverage:** expected trading minutes between the first bar and the last closed bar,
  bars present, share covered.
- **Gaps by reason:** whole trading days without a bar (holiday, or not in the cache), long
  gaps (>= ``LONG_GAP`` missing minutes in a row) and short gaps (quiet minutes without
  trades) inside days that have bars; bars at minutes outside the calendar.
- **Spikes** and large moves with examples (the gate's rules, ``data._outliers``).
- **Quote side, granularity, contract** and the execution assumption that follows from them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np

from jarvis.apex.data import NY, Series, assess, trading, trading_minutes
from jarvis.apex.engine import MarketContext
from jarvis.apex.execution import Execution
from jarvis.learning.market import local_clock
from jarvis.settings import ApexSettings

LONG_GAP = 30  # missing minutes in a row inside a trading day
EPOCH_ORDINAL = datetime(1970, 1, 1).toordinal()


def _utc(t: int) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _runs(missing: np.ndarray) -> list[tuple[int, int]]:
    """(first index, length) of each run of consecutive missing minutes."""
    if len(missing) == 0:
        return []
    breaks = np.flatnonzero(np.diff(missing) != 60) + 1
    starts = np.r_[0, breaks]
    ends = np.r_[breaks, len(missing)]
    return [(int(a), int(b - a)) for a, b in zip(starts, ends, strict=True)]


def fitness(
    series: Series,
    ctx: MarketContext,
    cfg: ApexSettings,
    as_of: int,
    execution: Execution,
) -> dict[str, Any]:
    known = series.until(as_of)
    bars = known.bars
    quality = assess(
        known,
        as_of,
        price_range=ctx.price_range,
        stale_minutes=cfg.stale_minutes,
        window_days=cfg.level_days,
        need_days=cfg.history_days,
    )
    out: dict[str, Any] = {
        "market": ctx.name,
        "as_of": as_of,
        "source": known.source,
        "source_id": known.provenance.get("source_id") or known.source,
        "label": known.label,
        "synthetic": known.synthetic,
        "symbol": known.provenance.get("symbol") or ctx.name,
        "contract": _contract(known, ctx),
        "quote_side": execution.quotes,
        "execution": execution.as_dict(),
        "execution_text": execution.describe(),
        "granularity": f"{bars.minutes} minute",
        "timeframes": "5, 15 and 60 minutes are built from the minute bars (closed bars only).",
        "status": "DATA_UNAVAILABLE" if not execution.tradable else quality["status"],
        "quality": quality,
    }
    if len(bars) == 0:
        return {**out, "bars": 0, "coverage": None, "gaps": None, "spikes": [], "hints": [
            "No bars: download the data (APEX → Data → Repair) or import it (Data Vault)."
        ]}  # fmt: skip
    t = bars.t
    last_closed = int(t[-1]) + 60 * bars.minutes
    expected = trading_minutes(int(t[0]), last_closed)
    present = np.isin(expected, t)
    missing = expected[~present]
    outside = int(np.sum(~trading(t)))
    duplicates = int(np.sum(np.diff(t) <= 0))
    misaligned = int(np.sum(t % 60 != 0))

    clock = local_clock(expected, NY)
    have_day = set(local_clock(t, NY).day.tolist())
    day_of_missing = clock.day[~present]
    empty_days = sorted({int(d) for d in day_of_missing.tolist() if int(d) not in have_day})
    in_empty = np.isin(day_of_missing, np.array(empty_days, dtype=np.int64))
    inside = missing[~in_empty]
    runs = _runs(inside)
    long_runs = [(a, n) for a, n in runs if n >= LONG_GAP]
    long_minutes = sum(n for _, n in long_runs)
    short_minutes = len(inside) - long_minutes
    days = sorted(set(clock.day.tolist()))

    out.update(
        {
            "bars": len(bars),
            "first_bar": int(t[0]),
            "last_closed_bar": last_closed,
            "last_closed_text": _utc(last_closed),
            "span_days": len(days),
            "coverage": {
                "expected_bars": len(expected),
                "present_bars": int(np.sum(present)),
                "share": round(float(np.mean(present)), 4) if len(expected) else None,
            },
            "gaps": {
                "calendar": "Weekend (Fri 17:00 - Sun 18:00 New York) and the daily break "
                "17:00-18:00 New York are not trading minutes and never count as missing.",
                "missing_minutes": len(missing),
                "by_reason": [
                    {
                        "reason": "day_without_bars",
                        "text": "Trading day without a single bar (holiday, or not downloaded)",
                        "days": len(empty_days),
                        "minutes": int(np.sum(in_empty)),
                        "examples": [_day(d) for d in empty_days[:8]],
                    },
                    {
                        "reason": "long_gap",
                        "text": f"{LONG_GAP}+ minutes in a row without bars inside a trading day",
                        "count": len(long_runs),
                        "minutes": long_minutes,
                        "examples": [
                            {"from": _utc(int(inside[a])), "minutes": n}
                            for a, n in sorted(long_runs, key=lambda r: -r[1])[:8]
                        ],
                    },
                    {
                        "reason": "short_gap",
                        "text": f"Shorter gaps (under {LONG_GAP} minutes: quiet minutes without "
                        "trades)",
                        "minutes": short_minutes,
                    },
                ],
                "outside_calendar_bars": outside,
            },
            "integrity": {"duplicates_or_disorder": duplicates, "misaligned": misaligned},
            "spikes": quality["summary"].get("spikes") or [],
            "large_moves": quality["summary"].get("large_moves") or [],
            "rolls": [_utc(b) for b in known.breaks],
        }
    )
    out["hints"] = _hints(out, quality, execution)
    return out


def _day(ordinal_days: int) -> str:
    return datetime.fromordinal(EPOCH_ORDINAL + int(ordinal_days)).date().isoformat()


def _contract(series: Series, ctx: MarketContext) -> str:
    contracts = series.provenance.get("contracts")
    if contracts:
        ids = ", ".join(str(c) for c in list(contracts)[-3:])
        symbol = series.provenance.get("symbol") or ctx.name
        rolls = f", {len(series.breaks)} roll(s) marked" if series.breaks else ""
        return f"{symbol} front contract (instrument ids {ids}{rolls})"
    if series.synthetic:
        return "none (generated test data)"
    if series.proxy:
        return "none — a CFD proxy, not the futures contract"
    return "spot (no expiry)" if ctx.name == "XAUUSD" else "unknown"


def _hints(out: dict[str, Any], quality: dict[str, Any], execution: Execution) -> list[str]:
    codes = {f["code"] for f in quality["findings"]}
    hints = []
    if not execution.tradable:
        hints.append(execution.reason)
    if "STALE" in codes or "NO_RECENT_DATA" in codes:
        hints.append("Update the data (APEX → Update data, or Data → Repair).")
    if "GAPS" in codes or (out["gaps"]["by_reason"][0]["days"] and not out["synthetic"]):
        hints.append("Re-check the days without bars (Data → Repair downloads what is missing).")
    if "SPIKES" in codes:
        hints.append("Spikes are flagged, not removed: check the listed minutes before trusting "
                     "a zone that sits on one.")  # fmt: skip
    if execution.quotes == "bid":
        hints.append(execution.describe())
    if "SHORT_HISTORY" in codes:
        hints.append("Too few days for base rates and validation — download more history.")
    return hints
