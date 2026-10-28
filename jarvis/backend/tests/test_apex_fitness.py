"""APEX P1: data fitness names every gap by its reason and every spike by its minute.

Generated test data (``synthetic_bars``) with deliberate damage: a missing day, a long gap,
scattered missing minutes, a bad tick. The view must count each one — and never repair it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from jarvis.apex.data import SYNTHETIC_LABEL, Series, synthetic_bars
from jarvis.apex.execution import execution_for
from jarvis.apex.fitness import fitness
from jarvis.apex.service import market_contexts
from jarvis.learning.market import Bars
from jarvis.settings import load_settings
from tests.conftest import make_settings

SETTINGS = load_settings(environ={})
CFG = SETTINGS.apex
CONTEXTS = market_contexts(SETTINGS)
T = int(datetime(2026, 3, 18, 15, 7, tzinfo=UTC).timestamp())


def damaged() -> tuple[Series, dict[str, Any]]:
    bars = synthetic_bars("XAUUSD", T, CONTEXTS["XAUUSD"].spec.tick).between(T - 30 * 86400, T)
    t = bars.t
    keep = np.ones(len(t), dtype=bool)
    day = int(datetime(2026, 3, 11, 12, tzinfo=UTC).timestamp())  # Wednesday 11 March (NY)
    whole = (t >= day - 12 * 3600 + 4 * 3600) & (t < day + 12 * 3600 + 4 * 3600)
    long_gap = (t >= T - 2 * 86400) & (t < T - 2 * 86400 + 45 * 60)  # 45 minutes
    keep &= ~whole & ~long_gap
    scattered = np.flatnonzero(keep)[1000::3001][:7]  # 7 single missing minutes
    keep[scattered] = False
    close = bars.close.copy()
    high = bars.high.copy()
    spike_at = int(np.flatnonzero(keep)[-3000])
    high[spike_at] = close[spike_at] * 1.05  # a wick 5 % above everything around it
    cut = Bars(t[keep], bars.open[keep], high[keep], bars.low[keep], close[keep],
               bars.volume[keep], 1)  # fmt: skip
    series = Series("XAUUSD", "fixture", SYNTHETIC_LABEL, True, cut)
    return series, {"long": 45, "scattered": 7, "spike": int(t[spike_at])}


def test_gaps_are_counted_by_reason_and_spikes_by_minute() -> None:
    series, damage = damaged()
    ctx = CONTEXTS["XAUUSD"]
    out = fitness(series, ctx, CFG, T, execution_for(series, ctx.spec))
    reasons = {r["reason"]: r for r in out["gaps"]["by_reason"]}
    assert reasons["day_without_bars"]["days"] == 1
    assert reasons["day_without_bars"]["examples"] == ["2026-03-11"]
    assert reasons["long_gap"]["count"] == 1 and reasons["long_gap"]["minutes"] == damage["long"]
    assert reasons["short_gap"]["minutes"] == damage["scattered"]
    total = sum(r["minutes"] for r in reasons.values())
    assert total == out["gaps"]["missing_minutes"]
    cov = out["coverage"]
    assert cov["expected_bars"] - cov["present_bars"] == out["gaps"]["missing_minutes"]
    assert 0.9 < cov["share"] < 1.0
    assert damage["spike"] in {s["t"] for s in out["spikes"]}
    assert out["quote_side"] == "synthetic" and out["granularity"] == "1 minute"
    assert out["last_closed_bar"] == int(series.bars.t[-1]) + 60
    # Nothing was repaired: the bars are exactly the damaged ones.
    assert out["bars"] == len(series.bars)


def test_the_weekend_and_daily_break_are_never_missing() -> None:
    bars = synthetic_bars("XAUUSD", T, CONTEXTS["XAUUSD"].spec.tick).between(T - 10 * 86400, T)
    series = Series("XAUUSD", "fixture", SYNTHETIC_LABEL, True, bars)
    out = fitness(
        series, CONTEXTS["XAUUSD"], CFG, T, execution_for(series, CONTEXTS["XAUUSD"].spec)
    )
    assert out["gaps"]["missing_minutes"] == 0 and out["coverage"]["share"] == 1.0
    assert out["gaps"]["outside_calendar_bars"] == 0


def test_a_cfd_proxy_is_labelled_data_unavailable_for_futures() -> None:
    ctx = CONTEXTS["NQ"]
    bars = synthetic_bars("NQ", T, ctx.spec.tick).between(T - 10 * 86400, T)
    proxy = Series("NQ", "dukascopy", "Dukascopy USA Tech 100", False, bars, "CFD proxy",
                   provenance={"source_id": "dukascopy:NQ", "prices": "bid"})  # fmt: skip
    out = fitness(proxy, ctx, CFG, T, execution_for(proxy, ctx.spec))
    assert out["status"] == "DATA_UNAVAILABLE"
    assert "CFD proxy" in out["contract"] and "Data Vault" in out["hints"][0]


def test_the_fitness_endpoint_serves_the_view(tmp_path: Path) -> None:
    from jarvis.api.app import create_app

    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture", "history_days": 30})
    with TestClient(create_app(settings.model_copy(update={"apex": apex}))) as client:
        client.app.state.runtime.apex._now = lambda: T  # type: ignore[attr-defined]
        body = client.get("/apex/fitness/XAUUSD").json()
        assert body["market"] == "XAUUSD" and body["synthetic"] is True
        assert body["coverage"]["share"] == 1.0 and body["execution"]["quotes"] == "synthetic"
        assert client.get("/apex/fitness/EURUSD").status_code == 422
