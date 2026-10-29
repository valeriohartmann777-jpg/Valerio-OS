"""The whole APEX pipeline on the real code path: Dukascopy download → decode → cache → quality
gate → structure → zones → rates → candidates → chart → data status → repair.

Dukascopy is replaced by a stand-in that serves **generated** gold minute bars
(``apex.data.synthetic_bars``) in Dukascopy's own delta-encoded format, so every byte goes
through the real decoder and cache. The prices are test data, not market results; the tests
check mechanics only. The owner's case — a cache that stopped four weeks ago with the days
since stored as "fetched" — is rebuilt and repaired here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import numpy as np
import pytest

from jarvis.apex.data import synthetic_bars
from jarvis.learning.market import MarketData
from jarvis.runtime import Runtime
from tests.conftest import make_settings
from tests.learning_data import candles_json

NOW = datetime.now(UTC)
TODAY = NOW.date()
_BARS = synthetic_bars("XAUUSD", int(NOW.timestamp()) // 60 * 60, 0.01)
_DAY = _BARS.t // 86400


class Dukascopy:
    """Generated gold bars served as Dukascopy's API does (bid; ask = bid + 0.30)."""

    def __init__(self) -> None:
        self.down = False
        self.requests: list[str] = []

    def rows(self, day: date, lift: float, upto: int | None = None) -> dict[str, Any]:
        k = (day - date(1970, 1, 1)).days
        mask = _DAY == k
        if upto is not None:
            mask &= _BARS.t + 60 <= upto
        t = _BARS.t[mask]
        minute = ((t - k * 86400) // 60).tolist()
        cols = [
            np.round(x[mask] + lift, 2).tolist()
            for x in (_BARS.open, _BARS.high, _BARS.low, _BARS.close)
        ]
        volume = np.maximum(_BARS.volume[mask], 1.0).tolist()
        rows = list(zip(minute, *cols, volume, strict=True))
        return candles_json(day, rows, multiplier=0.01)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        self.requests.append(path)
        if path == "/v1/instruments":
            return httpx2.Response(200, json=[{"code": "XAU-USD"}, {"code": "USATECH.IDX-USD"}])
        if self.down:
            return httpx2.Response(503)
        parts = path.split("/")
        if parts[4] != "XAU-USD":
            return httpx2.Response(404)
        lift = 0.3 if parts[5] == "ASK" else 0.0
        if len(parts) == 6:  # today so far
            return httpx2.Response(200, json=self.rows(TODAY, lift, int(NOW.timestamp())))
        day = date(int(parts[6]), int(parts[7]), int(parts[8]))
        return httpx2.Response(200, json=self.rows(day, lift))


@pytest.fixture
async def gold(tmp_path: Path) -> AsyncIterator[tuple[Runtime, Dukascopy]]:
    feed = Dukascopy()
    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"history_days": 25, "spread_days": 5})
    market = MarketData(
        tmp_path / "market",
        transport=httpx2.MockTransport(feed),
        retry_delays=(),
        requests_per_second=0,
    )
    runtime = Runtime(settings.model_copy(update={"apex": apex}), market=market)
    await runtime.start()
    try:
        yield runtime, feed
    finally:
        await runtime.stop()


async def test_from_download_to_chart_on_the_real_code_path(
    gold: tuple[Runtime, Dukascopy],
) -> None:
    rt, feed = gold
    out = await rt.apex.analyze("XAUUSD", refresh=True)
    update = out["data"]["update"]
    assert update["requested"] and update["ok"] and update["error"] is None
    assert update["report"]["with_data"] > 20 and update["report"]["answers"] == {
        "200": update["report"]["asked"]
    }
    assert not out["blocked"] and not out["quality"]["stale"]
    codes = {f["code"] for f in out["quality"]["findings"]}
    assert not codes & {"GAPS", "STALE", "SPIKES", "NO_RECENT_DATA"}, codes
    assert out["data"]["provenance"]["prices"] == "bid"
    spread = out["data"]["spread"]
    assert spread["measured"] and spread["median"] == pytest.approx(0.3)
    assert not spread["exceeds_costs"]  # 0.30 spread < 0.40 configured costs
    assert out["zones"] and out["structure"]["15"]["bars"] > 0
    assert out["scenarios"] and (out["candidates"] or out["rejected"])
    story = out["narrative"]  # JARVIS's reading: only the analysis' own numbers
    assert story["headline"].startswith("XAUUSD ") and "trend" in story["headline"]
    assert any(p.startswith("Data: ") and "updated now" in p for p in story["points"])
    assert any(p.startswith(("Key support KS1", "Key resistance KR1")) for p in story["points"])
    assert any("not probabilities" in c for c in story["caveats"])  # no track record yet

    # the chart in every timeframe, only bars known at the moment, the same data
    for minutes in (1, 5, 15, 60, 240):
        chart = await rt.apex.chart(out["id"], minutes, 200)
        assert chart["same_data"] and chart["prices"] == "bid"
        rows = chart["bars"]
        assert 0 < len(rows) <= 200 and all(r[0] < out["as_of"] for r in rows)
        assert all(r[3] <= min(r[1], r[4]) and r[2] >= max(r[1], r[4]) for r in rows)
        assert {int(b[0]) % (60 * minutes) for b in rows} == {0}

    # a second analysis reuses the local history: only today is asked again
    before = len([r for r in feed.requests if "/BID/" in r])
    await rt.apex.analyze("XAUUSD", refresh=True)
    assert len([r for r in feed.requests if "/BID/" in r]) == before

    status = await rt.apex.data_status("XAUUSD")
    source = status["sources"][0]
    assert source["state"] == "ok", source
    # complete through yesterday (or Friday/Sunday when yesterday had no trading)
    assert (TODAY - timedelta(days=2)).isoformat() <= source["coverage"]["last"] < TODAY.isoformat()
    assert status["causes"][0]["code"] == "OK"


async def test_the_owners_case_is_explained_and_repaired(
    gold: tuple[Runtime, Dukascopy], tmp_path: Path
) -> None:
    """Cache stopped four weeks ago; the days since were stored as fetched-but-empty (the old
    behaviour after an outage). Before: STALE + "GAPS 100 %" (blocked), no reason given.
    Now: the honest block, the concrete cause, one repair, a working analysis."""
    rt, feed = gold
    await rt.apex.analyze("XAUUSD", refresh=True)  # fill the cache
    cut = TODAY - timedelta(days=27)
    _poison_since(tmp_path / "market" / "XAUUSD", cut)

    stale = await rt.apex.analyze("XAUUSD")  # without an update: what the cache holds
    found = {f["code"]: f for f in stale["quality"]["findings"]}
    assert stale["blocked"] and "NO_RECENT_DATA" in found and "STALE" in found
    assert "GAPS" not in found  # was "100 % missing" with zero missing minutes
    assert "no analysis" in stale["narrative"]["headline"]

    status = await rt.apex.data_status("XAUUSD")
    causes = {c["code"]: c for c in status["causes"]}
    assert (
        causes["CACHE_BEHIND"]["level"] == "BLOCK" and "repair" in causes["CACHE_BEHIND"]["actions"]
    )
    assert causes["MISSING_DAYS"]["actions"] == ["repair"]
    hole = status["sources"][0]["suspicious"][-1]
    assert hole["from"] <= (cut + timedelta(days=2)).isoformat() and hole["states"] == {
        "stored_empty": hole["days"]
    }

    feed.down = True  # an update while Dukascopy is down: the reason is shown
    down = await rt.apex.analyze("XAUUSD", refresh=True)
    assert down["data"]["update"]["ok"] is False
    assert "can't reach" in down["data"]["update"]["error"]
    causes = {c["code"]: c for c in (await rt.apex.data_status("XAUUSD"))["causes"]}
    assert "No connection to Dukascopy" in causes["UPDATE_FAILED"]["text"]

    feed.down = False
    started = await rt.apex.repair("XAUUSD")
    assert started["state"] == "RUNNING"
    await rt.apex.wait_repair("XAUUSD")
    status = await rt.apex.data_status("XAUUSD")
    assert status["repair"]["state"] == "COMPLETED", status["repair"]
    assert [s["side"] for s in status["repair"]["steps"]] == ["BID", "ASK"]
    assert status["sources"][0]["suspicious"] == [] and status["causes"][0]["code"] == "OK"

    healed = await rt.apex.analyze("XAUUSD", refresh=True)
    assert not healed["blocked"] and not healed["quality"]["stale"]
    assert {f["code"] for f in healed["quality"]["findings"]}.isdisjoint({"GAPS", "SPIKES"})


def _poison_since(folder: Path, cut: date) -> None:
    """Drop the bars from ``cut`` on but keep those days listed as fetched."""
    lo = int(datetime(cut.year, cut.month, cut.day, tzinfo=UTC).timestamp())
    for path in folder.glob("*.npz"):
        with np.load(path) as f:
            stored = {k: f[k] for k in f.files}
        keep = stored["t"] < lo
        for k in ("t", "o", "h", "l", "c", "v"):
            stored[k] = stored[k][keep]
        np.savez_compressed(path, **stored)


def test_data_status_repair_and_chart_over_http(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from jarvis.api.app import create_app
    from jarvis.tools.apex import brief

    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture", "history_days": 30})
    with TestClient(create_app(settings.model_copy(update={"apex": apex}))) as client:
        status = client.get("/apex/data/XAUUSD").json()
        assert status["fixture"] and status["causes"][0]["code"] == "SYNTHETIC"
        assert client.get("/apex/data/ES").json()["detail"]["code"] == "UNKNOWN_MARKET"
        repair = client.post("/apex/data/XAUUSD/repair").json()
        assert repair["state"] == "COMPLETED" and repair["fixture"]
        analysis = client.post("/apex/analyze", json={"market": "XAUUSD"}).json()
        chart = client.get(
            f"/apex/analyses/{analysis['id']}/chart", params={"minutes": 60, "limit": 50}
        ).json()
        assert chart["synthetic"] and chart["same_data"] and len(chart["bars"]) == 50
        wrong = client.get(f"/apex/analyses/{analysis['id']}/chart", params={"minutes": 7})
        assert wrong.json()["detail"]["code"] == "TIMEFRAME"
        missing = client.get("/apex/analyses/apa-nothing/chart")
        assert missing.status_code == 404

    blocked = {
        "market": "XAUUSD",
        "blocked": True,
        "data": {"synthetic": False, "label": "Dukascopy", "update": {"error": "HTTP 503"}},
        "quality": {"findings": [{"level": "BLOCK", "code": "NO_RECENT_DATA", "message": "x"}]},
    }
    line = brief(blocked)
    assert "NO_RECENT_DATA" in line and "HTTP 503" in line and "APEX → Data" in line
