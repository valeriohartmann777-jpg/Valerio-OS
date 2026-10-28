"""APEX Ultimate U5: auction and order flow only from data that can carry them.

Acceptance tests 17-19 and scenario F: the VWAP resets at the exchange's session time (17:00
Chicago, winter and summer), a spot-quote VWAP is relabelled PROXY, delta comes from trade
prints with their quotes (fixtures of trades and BBO), never from candle colour, and bid/ask
candles can't produce "buyers", a delta number or "absorption confirmed" anywhere.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from jarvis.apex.capability import capability_for, factor_matrix
from jarvis.apex.data import Series, synthetic_bars
from jarvis.apex.engine import analyze
from jarvis.apex.narrative import narrate
from jarvis.apex.orderflow import (
    Trade,
    absorption,
    classify,
    delta_bars,
    orderflow_view,
    rth_start,
    session_start,
    session_vwap,
)
from jarvis.apex.service import market_contexts
from jarvis.learning.market import Bars
from jarvis.settings import load_settings

SETTINGS = load_settings(environ={})
CFG = SETTINGS.apex
CONTEXTS = market_contexts(SETTINGS)
CHI = ZoneInfo("America/Chicago")
FUTURES = {"market_type": "futures", "has_actual_traded_volume": True, "source_id": "vault:NQ"}
SPOT = {"market_type": "spot_quote", "has_actual_traded_volume": False,
        "source_id": "dukascopy:XAUUSD"}  # fmt: skip


def utc(y: int, m: int, d: int, hh: int, mm: int) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=UTC).timestamp())


def minute_bars(start: int, rows: list[tuple[float, float, float, float]]) -> Bars:
    t = start + 60 * np.arange(len(rows), dtype=np.int64)
    h, lo, c, v = (np.array([r[k] for r in rows], dtype=float) for k in range(4))
    return Bars(t, c, h, lo, c, v, 1)


# -- 18 · VWAP resets at the exchange's time, DST-correct ------------------------------------------


@pytest.mark.parametrize(
    ("when", "reset"),
    [
        (utc(2026, 1, 14, 23, 30), utc(2026, 1, 14, 23, 0)),  # CST: 17:00 Chicago = 23:00 UTC
        (utc(2026, 1, 14, 22, 30), utc(2026, 1, 13, 23, 0)),  # before today's reset
        (utc(2026, 7, 14, 22, 30), utc(2026, 7, 14, 22, 0)),  # CDT: 17:00 Chicago = 22:00 UTC
        (utc(2026, 3, 9, 1, 0), utc(2026, 3, 8, 22, 0)),  # the first evening after the change
    ],
)
def test_the_futures_session_resets_at_five_pm_chicago(when: int, reset: int) -> None:
    assert session_start(when, "futures") == reset
    assert datetime.fromtimestamp(reset, CHI).hour == 17


def test_spot_resets_at_five_pm_new_york_and_rth_at_nine_thirty() -> None:
    assert session_start(utc(2026, 1, 14, 23, 30), "spot_quote") == utc(2026, 1, 14, 22, 0)
    assert session_start(utc(2026, 7, 14, 21, 30), "spot_quote") == utc(2026, 7, 14, 21, 0)
    assert rth_start(utc(2026, 1, 14, 15, 0)) == utc(2026, 1, 14, 14, 30)  # 09:30 EST
    assert rth_start(utc(2026, 7, 14, 14, 0)) == utc(2026, 7, 14, 13, 30)  # 09:30 EDT
    assert rth_start(utc(2026, 7, 14, 13, 0)) is None  # not open yet


def test_the_vwap_uses_only_closed_bars_since_the_reset() -> None:
    reset = utc(2026, 1, 14, 23, 0)
    # high, low, close, volume — typical prices 100, 103, 106
    before = minute_bars(reset - 120, [(200.0, 200.0, 200.0, 50.0)] * 2)
    after = minute_bars(reset, [(101.0, 99.0, 100.0, 10.0), (104.0, 102.0, 103.0, 20.0),
                                (107.0, 105.0, 106.0, 30.0)])  # fmt: skip
    t, o, h, lo, c, v = (np.r_[getattr(before, k), getattr(after, k)]
                         for k in ("t", "open", "high", "low", "close", "volume"))  # fmt: skip
    bars = Bars(t, o, h, lo, c, v, 1)
    out = session_vwap(bars, reset + 180, FUTURES)
    assert out["status"] == "CONFIRMED" and out["reset_utc"] == reset and out["bars"] == 3
    assert out["value"] == pytest.approx((100 * 10 + 103 * 20 + 106 * 30) / 60)
    assert "traded volume" in out["basis"] and out["timezone"] == "America/Chicago"
    early = session_vwap(bars, reset + 150, FUTURES)  # the third bar hasn't closed yet
    assert early["bars"] == 2 and early["value"] == pytest.approx((1000 + 2060) / 30)


def test_a_spot_quote_vwap_is_a_proxy_and_generated_data_has_none() -> None:
    bars = minute_bars(utc(2026, 1, 14, 22, 0), [(101.0, 99.0, 100.0, 5.0)] * 5)
    proxy = session_vwap(bars, utc(2026, 1, 14, 22, 5), SPOT)
    assert proxy["status"] == "PROXY" and "not a traded VWAP" in proxy["basis"]
    none = session_vwap(bars, utc(2026, 1, 14, 22, 5), {"market_type": "generated"})
    assert none["status"] == "UNAVAILABLE" and none["value"] is None


# -- 19 · delta from trades and quotes, with the method and its confidence ----------------------


QUOTES = [(10.0, 99.75, 100.25), (20.0, 100.0, 100.5), (30.0, 101.0, 100.75)]  # last: crossed


def test_trades_are_signed_by_flag_quote_rule_and_tick_rule_with_confidence() -> None:
    trades = [
        Trade(5.0, 100.0, 1),  # before any quote → unclassified
        Trade(11.0, 100.25, 2),  # at the ask → buy (0.95)
        Trade(12.0, 99.75, 3),  # at the bid → sell
        Trade(13.0, 100.1, 1),  # inside, above the mid 100.0 → buy (0.75)
        Trade(14.0, 100.0, 4),  # at the mid: tick rule vs 100.1 → down → sell (0.6)
        Trade(20.0, 100.5, 5),  # quote at 20.0 is not "before": prevailing 99.75/100.25 → buy
        Trade(21.0, 100.0, 1, side="A"),  # venue flag: sell (1.0)
        Trade(31.0, 100.9, 2),  # crossed book → unclassified
        Trade(32.0, 100.9, 1, bid=100.5, ask=101.0),  # its own TBBO quote: above mid → buy
    ]
    signed, report = classify(trades, QUOTES)
    assert [x["sign"] for x in signed] == [0, 1, -1, 1, -1, 1, -1, 0, 1]
    assert [x["method"] for x in signed][1:5] == ["at_quote", "at_quote", "quote_rule",
                                                  "tick_rule"]  # fmt: skip
    assert signed[6]["confidence"] == 1.0 and signed[4]["confidence"] == 0.6
    assert report["unclassified"] == {"no_quote": 1, "crossed": 1, "zero_tick": 0}
    assert report["classified"] == 7 and report["trades"] == 9


def test_delta_and_cvd_count_only_closed_bars_before_the_moment() -> None:
    reset = 0
    signed = [
        {"t": 10.0, "size": 5.0, "sign": 1}, {"t": 200.0, "size": 2.0, "sign": -1},
        {"t": 310.0, "size": 4.0, "sign": -1}, {"t": 320.0, "size": 1.0, "sign": 0},
        {"t": 650.0, "size": 9.0, "sign": 1},  # the bar 600-900 hasn't closed at 700
    ]  # fmt: skip
    out = delta_bars(signed, 5, 700, session_reset=reset)
    assert [(b["bar_utc"], b["delta"], b["cvd"]) for b in out] == [(0, 3.0, 3.0), (300, -4.0, -1.0)]
    assert out[1]["unclassified"] == 1.0


def test_bars_never_give_delta_and_absorption_is_not_measurable() -> None:
    bars = minute_bars(utc(2026, 1, 14, 23, 0), [(101.0, 99.0, 100.5, 10.0)] * 30)
    view = orderflow_view(bars, utc(2026, 1, 14, 23, 30), FUTURES)
    assert (
        view["delta"]["status"] == "UNAVAILABLE"
        and "bars never give delta" in view["delta"]["reason"]
    )
    assert view["absorption"]["label"] == "ABSORPTION NOT MEASURABLE"
    assert absorption(depth=object())["status"] == "UNAVAILABLE"
    with_prints = orderflow_view(bars, utc(2026, 1, 14, 23, 30), FUTURES,
                                 trades=[Trade(utc(2026, 1, 14, 23, 1) + 0.5, 100.0, 3,
                                               side="B")])  # fmt: skip
    assert with_prints["delta"]["status"] == "CONFIRMED"
    assert with_prints["delta"]["bars"][0]["delta"] == 3.0


# -- 17 / F · bid/ask candles can't claim volume profile, delta or absorption ---------------------


FORBIDDEN = re.compile(
    r"\bbuyers?\b|\bsellers? absorbed\b|absorption (?:confirmed|detected)|"
    r"delta\s*[:=]?\s*[+-]?\d{2,}",
    re.IGNORECASE,
)


def _walk(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _walk(v)]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _walk(v)]
    return []


def test_bid_ask_candles_show_unavailable_never_buyers_or_absorption() -> None:
    ctx = CONTEXTS["XAUUSD"]
    t = utc(2026, 3, 18, 15, 0)
    bars = synthetic_bars("XAUUSD", t, ctx.spec.tick).between(t - 60 * 86400, t)
    series = Series("XAUUSD", "dukascopy", "Dukascopy bid candles (test fixture)", False, bars,
                    provenance={"source_id": "dukascopy:XAUUSD",
                                "spread": {"measured": True, "session_median": 0.3}})  # fmt: skip
    out = analyze(series, ctx, CFG, t)
    factors = {f["factor"]: f for f in out["factors"]}
    assert factors["delta"]["status"] == "UNAVAILABLE"
    assert factors["absorption"]["status"] == "UNAVAILABLE"
    assert "ABSORPTION NOT MEASURABLE" in factors["absorption"]["detail"]
    assert factors["volume_profile"]["status"] == "PROXY"  # never a precise POC from quotes
    assert factors["options"]["status"] == "UNAVAILABLE"
    assert out["auction"]["delta"]["status"] == "UNAVAILABLE"
    assert out["auction"]["vwap"]["status"] in ("PROXY", "UNAVAILABLE")
    text = [*_walk(out), json.dumps(narrate(out, None))]
    hits = [s for s in text if FORBIDDEN.search(s)]
    assert not hits, hits[:3]


def test_futures_ohlcv_has_a_vwap_but_still_no_delta_or_precise_poc() -> None:
    ctx = CONTEXTS["NQ"]
    t = utc(2026, 3, 18, 15, 0)
    bars = synthetic_bars("NQ", t, ctx.spec.tick).between(t - 10 * 86400, t)
    series = Series("NQ", "vault", "NQ venue bars (test fixture)", False, bars,
                    provenance={"source_id": "vault:NQ", "schema": "ohlcv-1m"})  # fmt: skip
    cap = capability_for(series, ctx.spec)
    rows = {f["factor"]: f["status"] for f in factor_matrix(cap, stale=False)}
    assert rows["vwap"] == "CONFIRMED" and rows["delta"] == "UNAVAILABLE"
    assert rows["volume_profile"] == "PROXY" and rows["absorption"] == "UNAVAILABLE"
    view = orderflow_view(bars, t, cap.as_dict())
    assert view["vwap"]["status"] == "CONFIRMED" and view["delta"]["status"] == "UNAVAILABLE"
