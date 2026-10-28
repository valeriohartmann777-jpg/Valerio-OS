"""Auction and order-flow measures — only from data that can carry them (spec J2-J5).

**Session VWAP** (``session_vwap``). Venue futures bars (CME): the session resets at 17:00
America/Chicago (the Globex trading day, = 18:00 New York in winter and summer alike); the
regular-session anchor is 08:30 Chicago (09:30 New York). Price basis: the typical price
(high + low + close) / 3 of each closed one-minute bar times its **traded** volume — a bar-level
approximation, recorded as such. Broker spot quotes (XAUUSD): there is no traded volume; tick
counts give a quote-activity weighted line, reset 17:00 New York, labelled ``PROXY`` — never a
traded VWAP. No volume at all → ``UNAVAILABLE``.

**Delta / CVD** (``classify``, ``delta_bars``) — only from trade prints, never from candle
colour. Each trade's aggressor side, in order of evidence:

1. the venue's aggressor flag (``B`` buy, ``A`` sell; Databento ``side``) — confidence 1.0;
2. the quote rule against the best bid/offer in force *before* the trade (its own TBBO quote
   or the last quote strictly earlier): at or through the ask = buy, at or through the bid =
   sell (0.95); inside the spread above / below the mid (0.75);
3. at the mid, or with a locked book: the tick rule against the last different trade price
   (0.6).

Unclassified and counted with the reason: no quote yet, a crossed book (bid > ask), a zero tick
with no earlier price change. Per closed bar: buy, sell and unclassified volume, delta, and the
cumulative delta since the session reset — trades at or after ``as_of`` never count.

**Absorption** (``absorption``) needs depth (MBP-10 / MBO) with synchronised trades. APEX has
no such source, and no heuristic is built until one has been tested on it: the answer is always
``ABSORPTION NOT MEASURABLE`` — a green wick is not absorption.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from jarvis.learning.market import Bars

ORDERFLOW_VERSION = "orderflow-1.0.0"
CHICAGO = ZoneInfo("America/Chicago")
NEW_YORK = ZoneInfo("America/New_York")
CONFIDENCE = {"venue_aggressor": 1.0, "at_quote": 0.95, "quote_rule": 0.75, "tick_rule": 0.6}


def session_start(t: int, market_type: str) -> int:
    """The last session reset at or before ``t``: 17:00 Chicago (futures) or 17:00 New York
    (spot metals / FX day) — DST-correct."""
    zone = CHICAGO if market_type == "futures" else NEW_YORK
    local = datetime.fromtimestamp(t, zone)
    reset = local.replace(hour=17, minute=0, second=0, microsecond=0)
    if reset > local:
        reset -= timedelta(days=1)
        reset = reset.replace(hour=17)  # same wall-clock time across a DST change
    return int(reset.timestamp())


def rth_start(t: int) -> int | None:
    """09:30 New York (08:30 Chicago) of ``t``'s day, if ``t`` is at or after it."""
    local = datetime.fromtimestamp(t, NEW_YORK)
    start = local.replace(hour=9, minute=30, second=0, microsecond=0)
    return int(start.timestamp()) if local >= start else None


def session_vwap(
    bars: Bars,
    as_of: int,
    capability: dict[str, Any],
    *,
    anchor: str = "session",
) -> dict[str, Any]:
    """The VWAP of the closed one-minute bars since the anchor, with ±1 / ±2 sd bands."""
    market_type = str(capability.get("market_type"))
    traded = bool(capability.get("has_actual_traded_volume"))
    zone = "America/Chicago" if market_type == "futures" else "America/New_York"
    base = {"version": ORDERFLOW_VERSION, "anchor": anchor, "as_of": as_of,
            "source_id": capability.get("source_id"), "timezone": zone}  # fmt: skip
    if market_type == "generated" or not (traded or market_type == "spot_quote"):
        return {**base, "status": "UNAVAILABLE", "value": None,
                "reason": "no volume in this source — no VWAP"}  # fmt: skip
    start = session_start(as_of, market_type) if anchor == "session" else rth_start(as_of)
    if start is None:
        return {**base, "status": "UNAVAILABLE", "value": None,
                "reason": "the regular session hasn't opened yet"}  # fmt: skip
    closed = bars.between(start, as_of - 59)  # bars that had closed by as_of
    volume = closed.volume.astype(float)
    total = float(volume.sum())
    if len(closed) == 0 or total <= 0:
        return {**base, "status": "UNAVAILABLE", "value": None, "reset_utc": start,
                "reason": "no volume since the reset"}  # fmt: skip
    typical = (closed.high + closed.low + closed.close) / 3.0
    value = float(np.sum(typical * volume) / total)
    sd = float(np.sqrt(np.sum(volume * (typical - value) ** 2) / total))
    status = "CONFIRMED" if traded else "PROXY"
    basis = (
        "typical price x traded volume per one-minute venue bar (bar-level approximation)"
        if traded
        else "typical price x quote activity (tick counts) — PROXY, not a traded VWAP"
    )
    return {
        **base,
        "status": status,
        "value": round(value, 6),
        "bands": {
            "sd": round(sd, 6),
            "upper_1": round(value + sd, 6),
            "lower_1": round(value - sd, 6),
            "upper_2": round(value + 2 * sd, 6),
            "lower_2": round(value - 2 * sd, 6),
        },
        "reset_utc": start,
        "bars": len(closed),
        "volume": total,
        "basis": basis,
    }


@dataclass(frozen=True)
class Trade:
    t: float  # epoch seconds (sub-second allowed)
    price: float
    size: float
    side: str = "N"  # venue aggressor flag: B buy, A sell, N none
    bid: float | None = None  # TBBO: the best bid/offer just before this trade
    ask: float | None = None


def classify(
    trades: list[Trade], quotes: list[tuple[float, float, float]] | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(each trade with sign, method and confidence; the classification report).

    ``quotes``: (time, bid, ask) BBO updates in time order, used when a trade carries none."""
    q = sorted(quotes or [])
    q_t = np.array([x[0] for x in q], dtype=float)
    out: list[dict[str, Any]] = []
    methods: dict[str, int] = dict.fromkeys(CONFIDENCE, 0)
    unclassified: dict[str, int] = {"no_quote": 0, "crossed": 0, "zero_tick": 0}
    last_price: float | None = None
    last_diff = 0  # sign of the last non-zero price change
    for tr in sorted(trades, key=lambda x: x.t):
        if last_price is not None and tr.price != last_price:
            last_diff = 1 if tr.price > last_price else -1
        sign, method, reason = 0, None, None
        if tr.side in ("B", "A"):
            sign, method = (1 if tr.side == "B" else -1), "venue_aggressor"
        else:
            bid, ask = tr.bid, tr.ask
            if bid is None or ask is None:
                k = int(np.searchsorted(q_t, tr.t, side="left")) - 1  # strictly earlier quote
                if k >= 0:
                    bid, ask = q[k][1], q[k][2]
            if bid is None or ask is None:
                reason = "no_quote"
            elif bid > ask:
                reason = "crossed"
            else:
                mid = (bid + ask) / 2
                if bid < ask and tr.price >= ask:
                    sign, method = 1, "at_quote"
                elif bid < ask and tr.price <= bid:
                    sign, method = -1, "at_quote"
                elif tr.price > mid:
                    sign, method = 1, "quote_rule"
                elif tr.price < mid:
                    sign, method = -1, "quote_rule"
                elif last_diff != 0:
                    sign, method = last_diff, "tick_rule"
                else:
                    reason = "zero_tick"
        if method:
            methods[method] += 1
        else:
            unclassified[str(reason)] += 1
        out.append({"t": tr.t, "price": tr.price, "size": tr.size, "sign": sign,
                    "method": method, "reason": reason,
                    "confidence": CONFIDENCE.get(method or "", 0.0)})  # fmt: skip
        last_price = tr.price
    n = len(out)
    done = n - sum(unclassified.values())
    report = {
        "trades": n,
        "classified": done,
        "classified_share": round(done / n, 4) if n else None,
        "methods": methods,
        "unclassified": unclassified,
        "mean_confidence": round(float(np.mean([x["confidence"] for x in out if x["method"]])), 4)
        if done else None,
        "version": ORDERFLOW_VERSION,
    }  # fmt: skip
    return out, report


def delta_bars(
    classified: list[dict[str, Any]], minutes: int, as_of: int, *, session_reset: int
) -> list[dict[str, Any]]:
    """Per closed ``minutes``-bar since ``session_reset``: buy / sell / unclassified volume,
    delta and cumulative delta. Trades at or after ``as_of`` and unfinished bars never count."""
    size = 60 * minutes
    bars: dict[int, dict[str, float]] = {}
    for x in classified:
        t = float(x["t"])
        if t < session_reset or t >= as_of:
            continue
        start = int(t // size) * size
        if start + size > as_of:
            continue  # the bar hasn't closed yet
        row = bars.setdefault(start, {"buy": 0.0, "sell": 0.0, "unclassified": 0.0})
        if x["sign"] > 0:
            row["buy"] += x["size"]
        elif x["sign"] < 0:
            row["sell"] += x["size"]
        else:
            row["unclassified"] += x["size"]
    out: list[dict[str, Any]] = []
    cvd = 0.0
    for start in sorted(bars):
        row = bars[start]
        delta = row["buy"] - row["sell"]
        cvd += delta
        out.append({"bar_utc": start, "closed_utc": start + size, **row, "delta": delta,
                    "cvd": cvd})  # fmt: skip
    return out


def absorption(depth: Any = None, trades: Any = None) -> dict[str, Any]:
    """Always not measurable here: no depth source, and no untested heuristic."""
    if depth is None:
        reason = "needs MBP-10 / MBO depth with synchronised trades — this source has none"
    else:
        reason = "a depth-based heuristic is not built: no depth data has been available to test it"
    return {"status": "UNAVAILABLE", "label": "ABSORPTION NOT MEASURABLE", "reason": reason,
            "version": ORDERFLOW_VERSION}  # fmt: skip


def orderflow_view(
    bars: Bars, as_of: int, capability: dict[str, Any], trades: list[Trade] | None = None
) -> dict[str, Any]:
    """What an analysis shows under auction / order flow for its source."""
    vwap = session_vwap(bars, as_of, capability)
    rth = session_vwap(bars, as_of, capability, anchor="rth")
    if trades:
        signed, report = classify(trades)
        reset = session_start(as_of, str(capability.get("market_type")))
        per_bar = delta_bars(signed, 5, as_of, session_reset=reset)[-48:]
        delta: dict[str, Any] = {"status": "CONFIRMED", "classification": report,
                                 "bars": per_bar}  # fmt: skip
    else:
        delta = {"status": "UNAVAILABLE", "label": "DELTA UNAVAILABLE",
                 "reason": "needs trade prints with an aggressor flag or trades + quotes "
                           "(TBBO); bars never give delta"}  # fmt: skip
    return {"version": ORDERFLOW_VERSION, "vwap": vwap, "vwap_rth": rth, "delta": delta,
            "absorption": absorption()}  # fmt: skip
