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
                f"The newest bar is {behind} trading minutes old — no trade candidates on "
                "stale data. Update the data first.",
                behind,
            )
        )

    window_start = as_of - window_days * 86400
    expected = trading_minutes(window_start, min(as_of, last_close))
    have = np.isin(expected, t[t >= window_start])
    missing = int(len(expected) - np.sum(have))
    share = missing / len(expected) if len(expected) else 1.0
    if share > 0.5:
        findings.append(
            _finding("BLOCK", "GAPS", f"{share:.0%} of trading minutes are missing.", missing)
        )
    elif share > 0.02:
        findings.append(
            _finding(
                "WARN",
                "GAPS",
                f"{share:.1%} of trading minutes in the last {window_days} days are missing "
                "(holidays and thin hours included).",
                missing,
            )
        )

    if len(c) > 100:
        r = np.diff(np.log(c[finite]))
        if len(r) > 10:
            mad = float(np.median(np.abs(r - np.median(r)))) or 1e-9
            spikes = int(np.sum(np.abs(r) > 15 * 1.4826 * mad))
            if spikes:
                findings.append(
                    _finding(
                        "WARN",
                        "SPIKES",
                        "Unusually large one-minute moves — check for bad ticks.",
                        spikes,
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
        "missing_share": round(share, 4),
        "days": covered,
    }
    return _verdict(findings, stale=stale, summary=summary)


def _verdict(
    findings: list[dict[str, Any]], *, stale: bool, summary: dict[str, Any]
) -> dict[str, Any]:
    levels = {f["level"] for f in findings}
    status = "BLOCK" if "BLOCK" in levels else "WARN" if "WARN" in levels else "OK"
    return {"status": status, "stale": stale, "findings": findings, "summary": summary}


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
