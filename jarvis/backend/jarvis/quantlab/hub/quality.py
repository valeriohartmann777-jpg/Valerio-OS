"""Data quality for canonical 1-minute OHLCV futures bars.

Findings are BLOCK (the data can't be used as is), WARN (usable, limitation
shown) or INFO. Nothing is repaired silently: duplicates stay duplicates in
the report, gaps are counted, not filled.

Databento OHLCV bars exist only for intervals with trades, so a missing minute
can be legitimate. Gaps are therefore judged against the venue calendar:
a missing *session* is a finding, a few quiet overnight minutes are not.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pyarrow as pa

from jarvis.quantlab.futures import sessions as cal

NS = 1_000_000_000
MINUTE = 60 * NS
JUMP_FRACTION = 0.015
RTH_GAP_FLAG = 0.02  # sessions missing more than 2% of RTH minutes are listed


def _finding(
    level: str, code: str, message: str, count: int = 0, examples: list[str] | None = None
) -> dict[str, Any]:
    return {
        "level": level,
        "code": code,
        "message": message,
        "count": count,
        "examples": (examples or [])[:5],
    }


def _iso(ts: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts / NS, UTC).strftime("%Y-%m-%d %H:%M UTC")


def contract_specs(definitions: pa.Table | None) -> dict[int, dict[str, Any]]:
    """The latest definition per instrument id (point-in-time handling happens in the engine)."""
    out: dict[int, dict[str, Any]] = {}
    if definitions is None or definitions.num_rows == 0:
        return out
    for row in definitions.to_pylist():
        out[int(row["instrument_id"])] = row
    return out


def assess_bars(
    bars: pa.Table,
    *,
    start: date,
    end: date,
    conditions: dict[str, str],
    definitions: pa.Table | None,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    n = bars.num_rows
    if n == 0:
        findings.append(_finding("BLOCK", "EMPTY", "The dataset has no bars."))
        return {"status": "BLOCK", "findings": findings, "summary": {"records": 0}}

    ts = np.asarray(bars.column("ts_event").to_numpy(), dtype=np.int64)
    iid = np.asarray(bars.column("instrument_id").to_numpy(), dtype=np.int64)
    o, h, lo, c = (
        np.asarray(bars.column(k).to_numpy(), dtype=np.int64)
        for k in ("open", "high", "low", "close")
    )
    vol = np.asarray(bars.column("volume").to_numpy(), dtype=np.int64)

    # Integrity -----------------------------------------------------------------------
    keys = ts.astype(np.int64) * 1_000_003 + iid
    dup = n - np.unique(keys).size
    if dup:
        findings.append(
            _finding("BLOCK", "DUPLICATES", "Bars with the same time and contract.", dup)
        )
    disorder = int(np.sum(np.diff(ts) < 0))
    if disorder:
        findings.append(_finding("BLOCK", "ORDER", "Timestamps go backwards.", disorder))
    bad = np.flatnonzero((lo > np.minimum(o, c)) | (h < np.maximum(o, c)) | (lo > h))
    if bad.size:
        findings.append(
            _finding(
                "BLOCK", "OHLC", "High/low don't contain open/close.", int(bad.size),
                [_iso(int(ts[i])) for i in bad[:5]],
            )
        )  # fmt: skip
    nonpos = np.flatnonzero((o <= 0) | (h <= 0) | (lo <= 0) | (c <= 0))
    if nonpos.size:
        findings.append(
            _finding(
                "BLOCK", "NONPOSITIVE",
                "Zero or negative prices (impossible for equity index futures; some other "
                "markets can trade negative and need their own rules).",
                int(nonpos.size), [_iso(int(ts[i])) for i in nonpos[:5]],
            )
        )  # fmt: skip
    misaligned = np.flatnonzero(ts % MINUTE != 0)
    if misaligned.size:
        findings.append(
            _finding(
                "BLOCK", "MISALIGNED", "Bar starts not on a minute boundary.", int(misaligned.size)
            )
        )
    zero = int(np.sum(vol <= 0))
    if zero:
        findings.append(
            _finding("WARN", "ZERO_VOLUME", "Bars without volume (OHLCV bars imply trades).", zero)
        )

    # Contract specs and tick grid ----------------------------------------------------
    specs = contract_specs(definitions)
    ids = sorted({int(x) for x in np.unique(iid)})
    undefined = [i for i in ids if i not in specs]
    if undefined:
        findings.append(
            _finding(
                "WARN", "NO_DEFINITION",
                "No contract definition for some instruments — tick size and multiplier would "
                "have to be assumed.", len(undefined), [str(i) for i in undefined],
            )
        )  # fmt: skip
    off_grid = 0
    for instrument, spec in specs.items():
        tick = spec.get("min_price_increment")
        if not tick or tick <= 0:
            continue
        mask = iid == instrument
        off_grid += int(
            np.sum(
                (o[mask] % tick != 0)
                | (h[mask] % tick != 0)
                | (lo[mask] % tick != 0)
                | (c[mask] % tick != 0)
            )
        )
    if off_grid:
        findings.append(
            _finding("BLOCK", "OFF_TICK", "Prices not on the contract's tick grid.", off_grid)
        )

    # Rolls and jumps -----------------------------------------------------------------
    rolls: list[dict[str, Any]] = []
    jumps: list[str] = []
    change = np.flatnonzero(np.diff(iid) != 0) + 1
    for i in change:
        rolls.append(
            {
                "at": _iso(int(ts[i])),
                "from_instrument_id": int(iid[i - 1]),
                "to_instrument_id": int(iid[i]),
                "last_close_fixed": int(c[i - 1]),
                "first_open_fixed": int(o[i]),
                "jump_points": (int(o[i]) - int(c[i - 1])) / NS,
            }
        )
    same = np.flatnonzero(np.diff(iid) == 0) + 1
    rel = np.abs(o[same] - c[same - 1]) / np.maximum(c[same - 1], 1)
    for i in same[rel > JUMP_FRACTION][:20]:
        jumps.append(f"{_iso(int(ts[i]))}: {(int(o[i]) - int(c[i - 1])) / NS:+.2f}")
    if jumps:
        findings.append(
            _finding(
                "WARN", "JUMPS", f"Moves over {JUMP_FRACTION:.1%} between consecutive bars of one "
                "contract.", len(jumps), jumps,
            )
        )  # fmt: skip
    if rolls:
        findings.append(
            _finding(
                "INFO", "ROLLS",
                "The continuous symbol switches contracts here. Prices are not back-adjusted: "
                "the jump is a different contract, never profit or loss.",
                len(rolls), [f"{r['at']}: {r['jump_points']:+.2f} pts" for r in rolls],
            )
        )  # fmt: skip

    # Calendar coverage ---------------------------------------------------------------
    # A Globex session opens the evening before its label date, so the session labelled
    # the day after the window's last UTC day already covers that day's evening bars.
    globex = cal.sessions("CMES", start, end + timedelta(days=1))
    rth = cal.sessions("XNYS", start, end)
    session_bounds = np.array([(s.open_ns, s.close_ns) for s in globex], dtype=np.int64)
    outside = 0
    if session_bounds.size:
        pos = np.searchsorted(session_bounds[:, 0], ts, side="right") - 1
        inside = (pos >= 0) & (ts < session_bounds[np.clip(pos, 0, None), 1])
        outside = int(np.sum(~inside))
    if outside:
        findings.append(
            _finding(
                "WARN", "OUTSIDE_SESSION",
                "Bars outside the CME Globex calendar's sessions (calendar or data issue).",
                outside,
            )
        )  # fmt: skip
    empty_sessions: list[str] = []
    rth_rows: list[dict[str, Any]] = []
    for s in rth:
        lo_i, hi_i = np.searchsorted(ts, [s.open_ns, s.close_ns])
        present = np.unique(ts[lo_i:hi_i]).size
        expected = (s.close_ns - s.open_ns) // MINUTE
        if present == 0:
            empty_sessions.append(s.label.isoformat())
        rth_rows.append(
            {
                "session": s.label.isoformat(),
                "expected_minutes": int(expected),
                "minutes_with_bars": int(present),
                "early_close": s.early_close,
            }
        )
    if empty_sessions:
        findings.append(
            _finding(
                "WARN", "MISSING_SESSIONS", "Trading sessions with no bars at all.",
                len(empty_sessions), empty_sessions,
            )
        )  # fmt: skip
    thin = [
        r["session"]
        for r in rth_rows
        if r["minutes_with_bars"]
        and r["minutes_with_bars"] < r["expected_minutes"] * (1 - RTH_GAP_FLAG)
    ]
    if thin:
        findings.append(
            _finding(
                "WARN", "RTH_GAPS",
                f"Sessions missing more than {RTH_GAP_FLAG:.0%} of regular-hours minutes "
                "(no trades or missing data).", len(thin), thin,
            )
        )  # fmt: skip
    flagged = {d: c for d, c in conditions.items() if c and c != "available"}
    if flagged:
        findings.append(
            _finding(
                "WARN", "PROVIDER_CONDITION",
                "Days the provider marks as degraded, pending or missing.", len(flagged),
                [f"{d}: {c}" for d, c in sorted(flagged.items())],
            )
        )  # fmt: skip

    levels = {f["level"] for f in findings}
    status = "BLOCK" if "BLOCK" in levels else "WARN" if "WARN" in levels else "OK"
    return {
        "status": status,
        "findings": findings,
        "summary": {
            "records": n,
            "first_bar": _iso(int(ts[0])),
            "last_bar": _iso(int(ts[-1])),
            "instruments": len(ids),
            "globex_sessions": len(globex),
            "rth_sessions": len(rth),
            "rth_sessions_with_bars": sum(1 for r in rth_rows if r["minutes_with_bars"]),
            "rth_minutes_expected": sum(r["expected_minutes"] for r in rth_rows),
            "rth_minutes_with_bars": sum(r["minutes_with_bars"] for r in rth_rows),
            "outside_session_bars": outside,
            "calendar": f"{cal.LIBRARY} {cal.library_version()} (CMES, XNYS)",
        },
        "rolls": rolls,
        "rth": rth_rows,
        "capabilities": CAPABILITIES["ohlcv-1m"],
    }


# What a schema can and can't support — shown with every dataset (the data-adequacy matrix).
CAPABILITIES: dict[str, list[dict[str, str]]] = {
    "ohlcv-1m": [
        {"use": "Signals on completed 1-minute bars, market fills at the next bar's open",
         "status": "FIT", "why": "Bar start time is known; the bar is complete one minute later."},
        {"use": "Stop and limit orders inside a bar", "status": "LIMITED",
         "why": "The order of high and low within a minute is unknown; ambiguous bars are "
                "resolved conservatively and counted."},
        {"use": "Spread-aware fills (bid/ask)", "status": "NOT_SUPPORTED",
         "why": "OHLCV has trades only; spread is a stated slippage assumption."},
        {"use": "Queue position, market impact", "status": "NOT_SUPPORTED",
         "why": "Needs order-book data (MBP-1/MBO), not ingested yet."},
    ],
}  # fmt: skip
