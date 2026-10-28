"""Minute bars for APEX: where they come from, whether they can be trusted, and test data.

A :class:`Series` always says where its prices come from (``source``, ``label``) and whether
they are generated (``synthetic``) or a stand-in for the traded contract (``proxy``). The
quality gate runs before every analysis; a BLOCK finding stops it, a stale series still gets
structure and zones (as of its last bar) but never trade candidates.

Trading calendar (CME Globex and spot gold alike): Sunday 18:00 → Friday 17:00 New York with a
daily break 17:00-18:00. Exchange holidays are not modelled; they show up as gaps.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from jarvis.learning.market import Bars, local_clock

NY = "America/New_York"
SYNTHETIC_LABEL = "SYNTHETIC TEST DATA — generated, not market prices"
# Generated series start here and are deterministic per market from this anchor on, so any
# moment after it shows the same prices in every run.
FIXTURE_ANCHOR = 1_756_684_800  # 2025-09-01 00:00 UTC
FIXTURE_BASE = {"XAUUSD": 2400.0, "NQ": 20000.0, "MNQ": 20000.0}
FIXTURE_DAILY_VOL = {"XAUUSD": 0.010, "NQ": 0.013, "MNQ": 0.013}


@dataclass(frozen=True)
class Series:
    """One-minute bars of one market and where they came from."""

    market: str
    source: str  # dukascopy | databento | import | fixture
    label: str  # shown to the owner next to every result
    synthetic: bool
    bars: Bars
    proxy: str | None = None  # e.g. "CFD on the index, not the NQ futures contract"
    breaks: tuple[int, ...] = ()  # epoch seconds where the contract changed (rolls)
    provenance: dict[str, Any] = field(default_factory=dict)

    def until(self, as_of: int) -> Series:
        """The series as known at ``as_of``: only bars that had closed by then."""
        bars = self.bars
        closed = int(np.searchsorted(bars.t + 60 * bars.minutes, as_of, side="right"))
        cut = Bars(
            bars.t[:closed],
            bars.open[:closed],
            bars.high[:closed],
            bars.low[:closed],
            bars.close[:closed],
            bars.volume[:closed],
            bars.minutes,
        )
        return Series(
            self.market,
            self.source,
            self.label,
            self.synthetic,
            cut,
            self.proxy,
            tuple(b for b in self.breaks if b <= as_of),
            self.provenance,
        )

    def digest(self) -> str:
        """Content hash of the bars (identifies exactly which data an analysis saw)."""
        h = hashlib.sha256()
        for column in (self.bars.t, self.bars.open, self.bars.high, self.bars.low, self.bars.close):
            h.update(np.ascontiguousarray(column).tobytes())
        return h.hexdigest()


# Calendar ----------------------------------------------------------------------------------


def trading(t: np.ndarray) -> np.ndarray:
    """Whether each minute (epoch seconds of its start) is inside the trading week."""
    clock = local_clock(t, NY)
    minute, weekday = clock.minute, clock.weekday
    closed = (
        (weekday == 5)
        | ((weekday == 6) & (minute < 18 * 60))
        | ((weekday == 4) & (minute >= 17 * 60))
        | ((minute >= 17 * 60) & (minute < 18 * 60))
    )
    return np.asarray(~closed)


def trading_minutes(start: int, end: int) -> np.ndarray:
    """Starts of all trading minutes in [start, end)."""
    first = -(-start // 60) * 60
    grid = np.arange(first, end, 60, dtype=np.int64)
    if len(grid) == 0:
        return grid
    kept: np.ndarray = grid[trading(grid)]
    return kept


# Quality gate ------------------------------------------------------------------------------


def _finding(level: str, code: str, message: str, count: int | None = None) -> dict[str, Any]:
    return {"level": level, "code": code, "message": message, "count": count}


def assess(
    series: Series,
    as_of: int,
    *,
    price_range: tuple[float, float],
    stale_minutes: int,
    window_days: int,
    need_days: int,
) -> dict[str, Any]:
    """Checks before analysis. ``status``: OK, WARN or BLOCK; ``stale`` blocks candidates."""
    bars = series.bars
    findings: list[dict[str, Any]] = []
    if series.synthetic:
        findings.append(_finding("INFO", "SYNTHETIC", SYNTHETIC_LABEL))
    if series.proxy:
        findings.append(_finding("WARN", "PROXY", series.proxy))
    if len(bars) == 0:
        findings.append(_finding("BLOCK", "EMPTY", "No minute bars before this moment."))
        return _verdict(findings, stale=True, summary={"bars": 0})

    t = bars.t
    disorder = int(np.sum(np.diff(t) <= 0))
    if disorder:
        findings.append(
            _finding("BLOCK", "ORDER", "Bars out of time order or duplicated.", disorder)
        )
    o, h, lo, c = bars.open, bars.high, bars.low, bars.close
    finite = np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c)
    bad_price = int(np.sum(~finite | (lo <= 0)))
    out_of_range = int(np.sum(finite & ((lo < price_range[0]) | (h > price_range[1]))))
    if bad_price or out_of_range:
        findings.append(
            _finding(
                "BLOCK",
                "PRICE",
                f"Prices missing, non-positive or outside {price_range[0]:g}-{price_range[1]:g}.",
                bad_price + out_of_range,
            )
        )
    with np.errstate(invalid="ignore"):
        inconsistent = int(
            np.sum(finite & ((h < np.maximum(o, c) - 1e-9) | (lo > np.minimum(o, c) + 1e-9)))
        )
    if inconsistent:
        findings.append(
            _finding("BLOCK", "OHLC", "High/low don't contain open/close.", inconsistent)
        )

    last_close = int(t[-1]) + 60 * bars.minutes
    behind = len(trading_minutes(last_close, as_of))
    stale = behind > stale_minutes
    if stale:
        findings.append(
            _finding(
                "WARN",
                "STALE",
                f"The newest bar is {behind} trading minutes old (closed {_utc(last_close)}) — "
                "no trade candidates on stale data. Update the data first.",
                behind,
            )
        )

    # Gaps: only over the span the data covers inside the window (first bar … newest bar).
    window_start = as_of - window_days * 86400
    inside = t >= window_start
    share: float | None = None
    largest: list[dict[str, Any]] = []
    if not inside.any():
        findings.append(
            _finding(
                "BLOCK",
                "NO_RECENT_DATA",
                f"No bar in the last {window_days} days — the newest closed {_utc(last_close)}. "
                "Zones and structure need the recent days: update the data (Data → Repair).",
            )
        )
    else:
        expected = trading_minutes(max(window_start, int(t[inside][0])), min(as_of, last_close))
        have = np.isin(expected, t[inside])
        missing = int(len(expected) - np.sum(have))
        share = missing / len(expected) if len(expected) else 0.0
        largest = _largest_gaps(expected, have)
        where = f" — largest from {largest[0]['from']}" if largest else ""
        if share > 0.5:
            findings.append(
                _finding(
                    "BLOCK",
                    "GAPS",
                    f"{share:.0%} of the trading minutes between the first and the newest bar of "
                    f"the last {window_days} days are missing{where}.",
                    missing,
                )
            )
        elif share > 0.02:
            findings.append(
                _finding(
                    "WARN",
                    "GAPS",
                    f"{share:.1%} of trading minutes in the last {window_days} days are missing "
                    f"(holidays and minutes without trades included){where}.",
                    missing,
                )
            )

    spikes, moves = _outliers(bars, finite)
    if spikes:
        findings.append(
            _finding(
                "WARN",
                "SPIKES",
                f"{len(spikes)} one-minute price spike(s) that reverse at once or reach far "
                f"beyond the neighbouring bars — likely bad ticks (first {spikes[0]['at']}).",
                len(spikes),
            )
        )
    if moves:
        findings.append(
            _finding(
                "INFO",
                "LARGE_MOVES",
                f"{len(moves)} one-minute move(s) above {SPIKE_SIGMA:g} robust standard "
                f"deviations that held (news?) — kept, not data errors (largest {moves[0]['at']}).",
                len(moves),
            )
        )

    covered = len(np.unique(t // 86400))
    if covered < 0.6 * need_days:
        findings.append(
            _finding(
                "WARN",
                "SHORT_HISTORY",
                f"Only {covered} days of data; base rates and the track record need about "
                f"{need_days}.",
                covered,
            )
        )
    rolls = [b for b in series.breaks if b >= window_start]
    if rolls:
        findings.append(
            _finding(
                "INFO",
                "ROLL",
                "Contract roll inside the window: levels are taken from the current contract only.",
                len(rolls),
            )
        )
    summary = {
        "bars": len(bars),
        "first": int(t[0]),
        "last_close": last_close,
        "behind_minutes": behind,
        "missing_share": round(share, 4) if share is not None else None,
        "days": covered,
        "largest_gaps": largest,
        "spikes": spikes[:10],
        "large_moves": moves[:5],
    }
    return _verdict(findings, stale=stale, summary=summary)


SPIKE_SIGMA = 15.0  # robust standard deviations of one-minute returns


def _utc(t: int) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _largest_gaps(expected: np.ndarray, have: np.ndarray, top: int = 3) -> list[dict[str, Any]]:
    """The longest runs of missing trading minutes: from, to (UTC) and their length."""
    gone = np.flatnonzero(~have)
    if len(gone) == 0:
        return []
    starts = np.r_[0, np.flatnonzero(np.diff(gone) > 1) + 1]
    ends = np.r_[starts[1:], len(gone)]
    runs = sorted(zip(starts.tolist(), ends.tolist(), strict=True), key=lambda r: r[0] - r[1])
    out = []
    for a, z in runs[:top]:
        first, last = int(expected[gone[a]]), int(expected[gone[z - 1]])
        out.append({"from": _utc(first), "to": _utc(last + 60), "t": first, "minutes": int(z - a)})
    return out


def _outliers(bars: Bars, finite: np.ndarray) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Bad-tick candidates and large moves, on **consecutive** minutes only (a reopen after
    the weekend or the daily break, or after missing minutes, is no one-minute move).

    - spike: a move above ``SPIKE_SIGMA`` robust standard deviations that the next minute
      takes back (70 % or more), or a high/low that reaches that far beyond its own body and
      both neighbouring bars;
    - large move: such a move that held (news) — listed, not a data error.
    """
    t, o, h, lo, c = bars.t, bars.open, bars.high, bars.low, bars.close
    if len(c) < 100 or not finite.all() or (c <= 0).any():
        return [], []
    step = 60 * bars.minutes
    consecutive = np.diff(t) == step
    r = np.diff(np.log(c))
    rc = r[consecutive]
    if len(rc) < 100:
        return [], []
    scale = 1.4826 * float(np.median(np.abs(rc - np.median(rc)))) or 1e-9
    limit = SPIKE_SIGMA * scale
    big = consecutive & (np.abs(r) > limit)
    following, follows = np.r_[r[1:], 0.0], np.r_[consecutive[1:], False]
    back = big & follows & (np.abs(r + following) < 0.3 * np.abs(r))
    reverting = np.r_[False, back[:-1]]  # the move back from a spike
    held = big & ~back & ~reverting
    neighbours = np.r_[False, consecutive] & np.r_[consecutive, False]
    high_n = np.maximum(np.r_[h[:1], h[:-1]], np.r_[h[1:], h[-1:]])
    low_n = np.minimum(np.r_[lo[:1], lo[:-1]], np.r_[lo[1:], lo[-1:]])
    body_hi, body_lo = np.maximum(o, c), np.minimum(o, c)
    reach = np.maximum(h - np.maximum(body_hi, high_n), np.minimum(body_lo, low_n) - lo) / c
    wick = neighbours & (reach > limit)
    spikes: list[dict[str, Any]] = [
        {"t": int(t[i + 1]), "at": _utc(int(t[i + 1])), "kind": "close",
         "sigma": round(abs(float(r[i])) / scale, 1)}
        for i in np.flatnonzero(back).tolist()
    ] + [
        {"t": int(t[i]), "at": _utc(int(t[i])), "kind": "wick",
         "sigma": round(float(reach[i]) / scale, 1)}
        for i in np.flatnonzero(wick).tolist()
    ]  # fmt: skip
    spikes.sort(key=lambda s: int(s["t"]))
    moves: list[dict[str, Any]] = sorted(
        (
            {"t": int(t[i + 1]), "at": _utc(int(t[i + 1])),
             "move_pct": round(float(np.expm1(r[i])) * 100, 3),
             "sigma": round(abs(float(r[i])) / scale, 1)}
            for i in np.flatnonzero(held).tolist()
        ),
        key=lambda m: -float(m["sigma"]),
    )  # fmt: skip
    return spikes, moves


def _verdict(
    findings: list[dict[str, Any]], *, stale: bool, summary: dict[str, Any]
) -> dict[str, Any]:
    levels = {f["level"] for f in findings}
    status = "BLOCK" if "BLOCK" in levels else "WARN" if "WARN" in levels else "OK"
    return {"status": status, "stale": stale, "findings": findings, "summary": summary}


# Bid / ask -----------------------------------------------------------------------------------


def measure_spread(
    bid: Bars,
    ask: Bars,
    *,
    start: int,
    end: int,
    window: tuple[int, int],
    cost_points: float,
) -> dict[str, Any]:
    """Ask minus bid close on the minutes in [start, end) that have both candles: median and
    90 % overall and inside the session window, against the configured round-trip costs."""
    b, a = bid.between(start, end), ask.between(start, end)
    common, ib, ia = np.intersect1d(b.t, a.t, return_indices=True)
    if len(common) < 100:
        return {
            "measured": False,
            "minutes": len(common),
            "reason": "Fewer than 100 minutes with both bid and ask candles in the period — "
            "the spread isn't measured; costs use the configured value.",
            "cost_points": cost_points,
        }
    spread = a.close[ia] - b.close[ib]
    minute = local_clock(common, NY).minute
    in_session = (minute >= window[0]) & (minute < window[1])
    session = spread[in_session] if int(in_session.sum()) >= 30 else spread

    def q(x: np.ndarray, p: float) -> float:
        return round(float(np.quantile(x, p)), 4)

    return {
        "measured": True,
        "minutes": len(common),
        "from": int(common[0]),
        "to": int(common[-1]) + 60,
        "median": q(spread, 0.5),
        "p90": q(spread, 0.9),
        "session_median": q(session, 0.5),
        "session_p90": q(session, 0.9),
        "max": round(float(spread.max()), 4),
        "negative": int(np.sum(spread < 0)),
        "cost_points": cost_points,
        "exceeds_costs": bool(q(session, 0.5) > cost_points),
    }


# Contract segments -------------------------------------------------------------------------


def current_contract(series: Series) -> Series:
    """Only the bars since the last contract change (levels never mix two contracts)."""
    if not series.breaks:
        return series
    start = series.breaks[-1]
    bars = (
        series.bars.between(start, int(series.bars.t[-1]) + 1) if len(series.bars) else series.bars
    )
    return Series(
        series.market,
        series.source,
        series.label,
        series.synthetic,
        bars,
        series.proxy,
        (),
        series.provenance,
    )


def from_table(table: Any) -> tuple[Bars, tuple[int, ...], list[int]]:
    """Databento-style OHLCV table (fixed-point prices, ns timestamps) → front-contract bars.

    On days with several contracts the one with the most volume that day is kept (as
    continuous front-month series do); ``breaks`` are the times where the contract changes.
    """
    ts = np.asarray(table.column("ts_event").to_numpy(), dtype=np.int64) // 1_000_000_000
    iid = np.asarray(table.column("instrument_id").to_numpy(), dtype=np.int64)
    price = {
        k: np.asarray(table.column(k).to_numpy(), dtype=np.float64) / 1e9
        for k in ("open", "high", "low", "close")
    }
    vol = np.asarray(table.column("volume").to_numpy(), dtype=np.float64)
    order = np.lexsort((iid, ts))
    ts, iid, vol = ts[order], iid[order], vol[order]
    price = {k: v[order] for k, v in price.items()}
    clock = local_clock(ts, NY)
    trading_day = (clock.day * 1440 + clock.minute + 360) // 1440
    keep = np.zeros(len(ts), dtype=bool)
    for day in np.unique(trading_day).tolist():
        mask = trading_day == day
        _, inverse = np.unique(iid[mask], return_inverse=True)
        totals = np.bincount(inverse, weights=vol[mask])
        keep[np.flatnonzero(mask)[inverse == int(np.argmax(totals))]] = True
    ts, iid, vol = ts[keep], iid[keep], vol[keep]
    price = {k: v[keep] for k, v in price.items()}
    changes = np.flatnonzero(np.diff(iid) != 0) + 1
    bars = Bars(ts, price["open"], price["high"], price["low"], price["close"], vol, 1)
    return bars, tuple(int(ts[i]) for i in changes), sorted(set(iid.tolist()))


# Test data ---------------------------------------------------------------------------------


def _seed(market: str, day: int) -> np.random.Generator:
    digest = hashlib.sha256(f"apex-fixture-{market}-{day}".encode()).digest()
    return np.random.Generator(np.random.PCG64(int.from_bytes(digest[:8], "big")))


def synthetic_bars(market: str, end: int, tick: float) -> Bars:
    """Generated one-minute bars from ``FIXTURE_ANCHOR`` to ``end`` — the same prices for the
    same minute in every run (each calendar day draws from its own seed). Random walk with an
    intraday volatility profile, slow drift regimes and mean reversion; no built-in edge.
    Labelled SYNTHETIC wherever it is shown."""
    grid = trading_minutes(FIXTURE_ANCHOR, end)
    n = len(grid)
    if n == 0:
        empty = np.empty(0)
        return Bars(grid, empty, empty, empty, empty, empty, 1)
    minute = local_clock(grid, NY).minute
    profile = np.where(
        (minute >= 9 * 60 + 30) & (minute < 11 * 60),
        1.8,
        np.where(
            (minute >= 3 * 60) & (minute < 5 * 60), 1.4, np.where(minute >= 18 * 60, 0.6, 1.0)
        ),
    )
    sigma = FIXTURE_DAILY_VOL[market] / np.sqrt(1380.0) * profile
    day = (grid - FIXTURE_ANCHOR) // 86400
    starts = np.flatnonzero(np.r_[True, day[1:] != day[:-1]])
    ends = np.r_[starts[1:], n]
    noise, wick_up, wick_down, volume = (np.empty(n) for _ in range(4))
    regime = np.empty(n)
    for a, z in zip(starts.tolist(), ends.tolist(), strict=True):
        rng = _seed(market, int(day[a]))
        m = z - a
        draws = rng.standard_normal((3, 1440))
        noise[a:z], wick_up[a:z], wick_down[a:z] = (
            draws[0, :m],
            np.abs(draws[1, :m]),
            np.abs(draws[2, :m]),
        )
        volume[a:z] = rng.lognormal(0.0, 0.5, 1440)[:m]
        regime[a:z] = rng.standard_normal()
    shocks = regime * sigma * 0.01 + sigma * noise
    # Deviation from the base follows x_i = k·x_(i-1) + shock_i (mean reversion), solved per
    # day in closed form so it stays exact and fast.
    k = 1.0 - 0.00002
    deviation = np.empty(n)
    carry = 0.0
    for a, z in zip(starts.tolist(), ends.tolist(), strict=True):
        steps = np.arange(1, z - a + 1)
        deviation[a:z] = k**steps * (carry + np.cumsum(shocks[a:z] * k ** (-steps)))
        carry = float(deviation[z - 1])
    close = FIXTURE_BASE[market] * np.exp(deviation)
    open_ = np.r_[FIXTURE_BASE[market], close[:-1]]
    spread = sigma * close * 0.6
    high = np.maximum(open_, close) + wick_up * spread
    low = np.minimum(open_, close) - wick_down * spread

    def snap(x: np.ndarray) -> np.ndarray:
        return np.round(x / tick) * tick

    o, h, lo, c = snap(open_), snap(high), snap(low), snap(close)
    h = np.maximum(h, np.maximum(o, c))
    lo = np.minimum(lo, np.minimum(o, c))
    return Bars(grid, o, h, lo, c, np.round(volume * 100 * profile), 1)
