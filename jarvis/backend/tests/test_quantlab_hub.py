"""QuantLab Data Hub: credentials, metadata-only connection, cost-first acquisition, cache.

The provider is the offline fixture (``jarvis.quantlab.hub.fixture``): it raises the
real SDK exception types and writes real DBN files, so the adapter's error mapping
and the ingestion path run exactly as they would against Databento. No test here
talks to Databento, and none pretends to.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from keyring.backends import fail

from jarvis.events.bus import EventBus
from jarvis.observability.logging import RedactingFilter, redact
from jarvis.quantlab.hub import fixture
from jarvis.quantlab.hub.provider import DatabentoAdapter, ProviderError, classify
from jarvis.quantlab.hub.service import DataHubService, HubError
from jarvis.quantlab.hub.store import HubStore
from jarvis.quantlab.hub.vault import CredentialVault, MemoryKeyring
from jarvis.storage.database import Database
from tests.conftest import Recorder

KEY = fixture.VALID_KEY
NQ = {
    "dataset": "GLBX.MDP3",
    "schema": "ohlcv-1m",
    "stype_in": "continuous",
    "symbols": ["NQ.v.0"],
    "start": "2026-03-09",
    "end": "2026-03-17",
}


class Counting:
    """Wraps the fixture factory and counts billable downloads."""

    def __init__(self) -> None:
        self.downloads = 0
        self.factory_calls = 0

    def __call__(self, key: str) -> Any:
        self.factory_calls += 1
        client = fixture.FixtureHistorical(key)
        original = client.timeseries.get_range

        def counted(**kw: Any) -> Any:
            self.downloads += 1
            return original(**kw)

        client.timeseries.get_range = counted  # type: ignore[method-assign]
        return client


class Hub:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.db = Database(tmp / "jarvis.db")
        self.bus = EventBus()
        self.events = Recorder(self.bus)
        self.keyring = MemoryKeyring()
        self.counter = Counting()
        self.service = self.make()

    def make(self, vault: CredentialVault | None = None) -> DataHubService:
        service = DataHubService(
            store=HubStore(self.db),
            bus=self.bus,
            root=self.tmp / "hub",
            vault=vault or CredentialVault(self.keyring),
            adapter=DatabentoAdapter(self.counter, fixture_label=fixture.LABEL),
        )
        service.retry_scale = 0.001
        return service


@pytest.fixture
async def hub(tmp_path: Path) -> AsyncIterator[Hub]:
    h = Hub(tmp_path)
    await h.db.connect()
    await h.service.start()
    try:
        yield h
    finally:
        await h.service.stop()
        await h.db.close()


async def connected(h: Hub) -> DataHubService:
    await h.service.connect(KEY)
    return h.service


async def download(h: Hub, request: dict[str, Any] | None = None) -> dict[str, Any]:
    service = h.service
    if (await service.status())["status"] != "CONNECTED":
        await service.connect(KEY)
    quote = await service.quote(request or NQ)
    job = await service.approve(quote["id"], quote["cost_usd"] + 1)
    await service.wait_idle()
    return await service.job(job["id"])


def all_bytes(root: Path) -> bytes:
    out = b""
    for path in root.rglob("*"):
        if path.is_file():
            out += path.read_bytes()
    return out


# -- connection and credentials -----------------------------------------------------------


async def test_connect_checks_metadata_and_keeps_the_key_only_in_the_keystore(hub: Hub) -> None:
    status = await hub.service.connect(f'DATABENTO_API_KEY="{KEY}"')
    assert status["status"] == "CONNECTED"
    assert status["key_hint"] == "…" + KEY[-4:]
    assert "GLBX.MDP3" in status["datasets"]
    assert status["fixture"] is True and "NOT Databento" in status["fixture_label"]
    assert status["keystore"]["test_only"] is True
    assert hub.keyring.get_password("JARVIS QuantLab", "databento-historical") == KEY
    assert hub.counter.downloads == 0  # connecting never downloads
    # The key appears nowhere else: database, files, events, the status payload.
    await hub.db.close()
    assert KEY.encode() not in (hub.tmp / "jarvis.db").read_bytes()
    assert KEY.encode() not in all_bytes(hub.tmp)
    await hub.db.connect()
    assert KEY not in repr(status)
    assert all(KEY not in repr(e.model_dump()) for e in hub.events.events)


async def test_rejected_or_malformed_keys_store_nothing(hub: Hub) -> None:
    with pytest.raises(HubError) as bad:
        await hub.service.connect("not-a-key")
    assert bad.value.code == "INVALID_FORMAT"
    assert hub.counter.factory_calls == 0  # never sent anywhere

    with pytest.raises(HubError) as rejected:
        await hub.service.connect("db-REVOKED000000000000000000000")
    assert rejected.value.code == "AUTH_INVALID"
    assert "portal" in rejected.value.remedy

    with pytest.raises(HubError) as offline:
        await hub.service.connect("db-OFFLINE000000000000000000000")
    assert offline.value.code == "NETWORK"

    assert hub.keyring.get_password("JARVIS QuantLab", "databento-historical") is None
    assert (await hub.service.status())["status"] == "NOT_CONNECTED"


async def test_without_a_secure_keystore_nothing_is_stored_or_sent(hub: Hub) -> None:
    service = hub.make(CredentialVault(fail.Keyring()))
    status = await service.status()
    assert status["keystore"]["available"] is False
    assert "plain text" in status["keystore"]["reason"]
    with pytest.raises(HubError) as refused:
        await service.connect(KEY)
    assert refused.value.code == "NO_SECURE_KEYSTORE"
    assert hub.counter.factory_calls == 0


async def test_test_and_disconnect_keep_the_cache(hub: Hub) -> None:
    job = await download(hub)
    assert job["status"] == "COMPLETED"
    status = await hub.service.test()
    assert status["verified_at"]
    status = await hub.service.disconnect()
    assert status["status"] == "NOT_CONNECTED"
    assert hub.keyring.get_password("JARVIS QuantLab", "databento-historical") is None
    assert await hub.service.cache_summary()  # licensed data already bought stays local
    with pytest.raises(HubError) as missing:
        await hub.service.quote(NQ)
    assert missing.value.code == "NOT_CONNECTED"


# -- catalog --------------------------------------------------------------------------------


async def test_catalog_comes_from_metadata(hub: Hub) -> None:
    service = await connected(hub)
    info = await service.dataset_info("GLBX.MDP3")
    assert "ohlcv-1m" in info["schemas"] and "mbp-1" in info["schemas"]
    assert info["ingestible"] == ["ohlcv-1m", "definition"]
    assert info["available_start"] == "2025-01-02"
    assert info["available_end"] == "2026-07-01"
    resolved = await service.resolve("GLBX.MDP3", ["NQ.v.0", "ZZ.v.0"], "continuous",
                                     "2026-03-01", "2026-03-20")  # fmt: skip
    ids = [m["id"] for m in resolved["mappings"]["NQ.v.0"]]
    assert len(ids) == 2 and ids[0] != ids[1]  # the roll from NQH6 to NQM6 is visible
    assert resolved["not_found"] == ["ZZ.v.0"]
    assert hub.counter.downloads == 0


async def test_unlicensed_dataset_is_reported_as_entitlement(hub: Hub) -> None:
    await hub.service.connect("db-NOLICENSE0000000000000000000")
    with pytest.raises(HubError) as denied:
        await hub.service.quote(NQ)
    assert denied.value.code == "NO_ENTITLEMENT"


# -- quotes and approvals -------------------------------------------------------------------


async def test_quote_rules_and_no_download_without_approval(hub: Hub) -> None:
    service = await connected(hub)
    for change, code in (
        ({"symbols": []}, "INVALID_REQUEST"),
        ({"symbols": ["ALL_SYMBOLS"]}, "INVALID_REQUEST"),
        ({"start": "2024-06-01"}, "RANGE_UNAVAILABLE"),
        ({"end": "2026-07-02"}, "RANGE_UNAVAILABLE"),
        ({"schema": "trades"}, "SCHEMA_NOT_INGESTIBLE"),
        ({"end": "2026-03-09"}, "INVALID_RANGE"),
        ({"stype_in": "isin"}, "INVALID_STYPE"),
    ):
        with pytest.raises(HubError) as refused:
            await service.quote({**NQ, **change})
        assert refused.value.code == code, change
    quote = await service.quote(NQ)
    assert quote["status"] == "OPEN"
    assert quote["cost_usd"] > 0 and quote["records"] > 0
    assert {i["schema"] for i in quote["items"]} == {"ohlcv-1m", "definition"}
    assert "not a guaranteed price" in quote["caveat"]
    assert quote["signature_valid"] is True
    assert hub.counter.downloads == 0


async def test_download_caches_days_and_never_buys_them_twice(hub: Hub) -> None:
    job = await download(hub)
    assert job["status"] == "COMPLETED"
    assert job["chunks_done"] == job["chunks_total"] == 2  # bars + definitions
    first_downloads = hub.counter.downloads
    raw = [hub.service.root / c["raw_path"] for c in job["chunks"]]
    assert all(p.exists() and p.stat().st_mode & 0o777 == 0o444 for p in raw)  # read-only
    summary = await hub.service.cache_summary()
    bars = next(s for s in summary if s["schema"] == "ohlcv-1m")
    assert bars["days"] == 8 and bars["records"] > 5_000

    again = await hub.service.quote(NQ)
    assert again["cost_usd"] == 0 and again["cached_days"] == 16
    assert all(not item["ranges"] for item in again["items"])

    wider = await hub.service.quote({**NQ, "end": "2026-03-20"})
    ranges = [r for item in wider["items"] for r in item["ranges"]]
    assert {(r["start"], r["end"]) for r in ranges} == {("2026-03-17", "2026-03-20")}
    assert hub.counter.downloads == first_downloads


async def test_approval_is_bound_capped_and_single_use(hub: Hub) -> None:
    service = await connected(hub)
    quote = await service.quote(NQ)
    with pytest.raises(HubError) as low:
        await service.approve(quote["id"], quote["cost_usd"] / 2)
    assert low.value.code == "BUDGET_BELOW_ESTIMATE"

    await service.set_caps(per_request=0.01, per_month=100)
    with pytest.raises(HubError) as capped:
        await service.approve(quote["id"], 100)
    assert capped.value.code == "OVER_REQUEST_CAP"
    cost = quote["cost_usd"]
    await service.set_caps(per_request=cost * 1.5, per_month=cost * 1.5)
    job = await service.approve(quote["id"], cost + 0.5)
    with pytest.raises(HubError) as replay:
        await service.approve(quote["id"], cost + 0.5)
    assert replay.value.code == "NOT_OPEN"
    second = await service.quote({**NQ, "start": "2026-04-06", "end": "2026-04-14"})
    with pytest.raises(HubError) as month:
        await service.approve(second["id"], 100)
    assert month.value.code == "OVER_MONTH_CAP"  # the first approval counts this month
    audit = [a["action"] for a in await service.audit_log()]
    assert audit.count("caps.changed") == 2
    await service.wait_idle()
    assert (await service.job(job["id"]))["status"] == "COMPLETED"


async def test_expired_tampered_and_repriced_quotes_are_refused(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = await connected(hub)
    expired = await service.quote(NQ)
    await hub.db.execute(
        "UPDATE qh_quotes SET expires_at = ? WHERE id = ?", ("2000-01-01T00:00:00", expired["id"])
    )
    with pytest.raises(HubError) as old:
        await service.approve(expired["id"], 100)
    assert old.value.code == "NOT_OPEN"

    tampered = await service.quote(NQ)
    await hub.db.execute("UPDATE qh_quotes SET cost_usd = 0.0 WHERE id = ?", (tampered["id"],))
    with pytest.raises(HubError) as edited:
        await service.approve(tampered["id"], 100)
    assert edited.value.code == "TAMPERED"

    repriced = await service.quote(NQ)
    monkeypatch.setitem(fixture.USD_PER_GB, "ohlcv-1m", fixture.USD_PER_GB["ohlcv-1m"] * 3)
    with pytest.raises(HubError) as moved:
        await service.approve(repriced["id"], 100)
    assert moved.value.code == "PRICE_CHANGED"
    assert (await service.quote_view(repriced["id"]))["status"] == "SUPERSEDED"
    assert hub.counter.downloads == 0


async def test_declined_quote_cannot_be_approved(hub: Hub) -> None:
    service = await connected(hub)
    quote = await service.quote(NQ)
    await service.reject(quote["id"])
    with pytest.raises(HubError):
        await service.approve(quote["id"], 100)


async def test_cancel_and_restart(hub: Hub) -> None:
    service = await connected(hub)
    quote = await service.quote({**NQ, "start": "2025-06-02", "end": "2025-12-01"})
    await service.set_caps(per_request=100, per_month=500)
    job = await service.approve(quote["id"], quote["cost_usd"] + 1)
    await service.cancel(job["id"])
    await service.wait_idle(60)
    done = await service.job(job["id"])
    assert done["status"] == "CANCELED"
    assert done["chunks_done"] < done["chunks_total"]

    # A download in flight when JARVIS stops is marked interrupted on the next start.
    await hub.db.execute("UPDATE qh_jobs SET status = 'RUNNING' WHERE id = ?", (job["id"],))
    await service.stop()
    restarted = hub.make()
    await restarted.start()
    after = await restarted.job(job["id"])
    assert after["status"] == "INTERRUPTED" and "cached" in after["error"]
    await restarted.stop()
    await service.start()


# -- datasets -------------------------------------------------------------------------------


async def test_dataset_snapshot_manifest_quality_and_tamper_check(hub: Hub) -> None:
    await download(hub, {**NQ, "start": "2026-02-16", "end": "2026-03-17"})
    with pytest.raises(HubError) as gap:
        await hub.service.build_dataset({**NQ, "symbol": "NQ.v.0", "start": "2026-02-01"})
    assert gap.value.code == "NOT_CACHED"
    record = await hub.service.build_dataset({**NQ, "symbol": "NQ.v.0", "start": "2026-02-16"})
    manifest = record["manifest"]
    assert manifest["fixture"] is True
    assert "START of each 1-minute bar" in manifest["time_convention"]
    assert manifest["conditions"] == {"2026-02-17": "degraded"}
    assert len({i for m in manifest["mapping"] for i in m["instrument_ids"]}) == 2
    assert {i.get("raw_symbol") for i in manifest["instruments"]} == {"NQH6", "NQM6"}
    assert "redistribut" in manifest["license"]
    quality = record["quality"]
    codes = {f["code"] for f in quality["findings"]}
    assert "ROLLS" in codes and "PROVIDER_CONDITION" in codes
    assert "NO_DEFINITION" not in codes and quality["status"] == "WARN"
    roll = quality["rolls"][0]
    assert roll["jump_points"] > 100  # the next contract trades at a premium — not P&L
    assert quality["summary"]["outside_session_bars"] == 0

    same = await hub.service.build_dataset({**NQ, "symbol": "NQ.v.0", "start": "2026-02-16"})
    assert same["id"] == record["id"]  # deterministic id, no duplicate snapshot

    _, bars, definitions = await hub.service.load(record["id"])
    assert bars.num_rows == record["records"] and definitions is not None
    snapshot = hub.service.root / record["snapshot_path"]
    os.chmod(snapshot, 0o644)
    snapshot.write_bytes(snapshot.read_bytes() + b"x")
    with pytest.raises(HubError) as tampered:
        await hub.service.load(record["id"])
    assert tampered.value.code == "SNAPSHOT_TAMPERED"
    with pytest.raises(sqlite3.IntegrityError):
        await hub.db.execute("UPDATE qh_datasets SET records = 1 WHERE id = ?", (record["id"],))


# -- errors and redaction -------------------------------------------------------------------


def test_provider_errors_are_classified_and_redacted() -> None:
    import databento

    def client(status: int, case: str = "", message: str = "") -> Exception:
        return databento.BentoClientError(
            http_status=status,
            json_body={"detail": {"case": case, "message": message, "docs": None}},
            message=message,
        )

    cases = [
        (client(401, "auth_authentication_failed"), "AUTH_INVALID"),
        (client(403, "license_dataset_not_subscribed"), "NO_ENTITLEMENT"),
        (client(422, "schema_invalid", "bad schema"), "UNSUPPORTED_SCHEMA"),
        (client(422, "data_end_after_available_end"), "RANGE_UNAVAILABLE"),
        (client(422, "symbology_invalid_symbol"), "SYMBOL_NOT_FOUND"),
        (client(429), "RATE_LIMITED"),
        (client(402), "QUOTA_EXCEEDED"),
        (databento.BentoServerError(http_status=503, message="down"), "PROVIDER_ERROR"),
        (ConnectionError("reset"), "NETWORK"),
        (ValueError("bad value"), "INVALID_REQUEST"),
    ]
    for exc, code in cases:
        assert classify(exc).code == code, exc
    leaked = classify(RuntimeError(f"boom with {KEY} inside"), KEY)
    assert KEY not in leaked.detail
    assert isinstance(leaked, ProviderError)


def test_logs_never_contain_keys() -> None:
    record = logging.LogRecord("jarvis", logging.INFO, "", 0, "key=%s", (KEY,), None)
    record.extra_key = f"Bearer {KEY}"
    RedactingFilter().filter(record)
    assert KEY not in record.getMessage() and KEY not in record.extra_key
    assert (
        redact("sk-ant-api03-abcdefghijkl and db-ABCDEFGHIJKLMNOP") == "[REDACTED] and [REDACTED]"
    )


def test_fixture_contracts_and_roll_dates() -> None:
    nq = fixture.PRODUCTS["NQ"]
    assert fixture.front(nq, date(2026, 3, 12), "v").raw_symbol == "NQH6"
    assert fixture.front(nq, date(2026, 3, 13), "v").raw_symbol == "NQM6"
    assert fixture.front(nq, date(2026, 3, 20), "c").raw_symbol == "NQH6"
    assert fixture.front(nq, date(2026, 3, 21), "c").raw_symbol == "NQM6"
    expiry = fixture.Contract(nq, 2026, 3).expiration
    assert expiry.weekday() == 4 and 15 <= expiry.day <= 21
    assert fixture.Contract(nq, 2026, 3).roll_date == expiry.date() - timedelta(days=7)


# -- HTTP API -------------------------------------------------------------------------------


@pytest.fixture
def client(settings: Any) -> Any:
    from fastapi.testclient import TestClient

    from jarvis.api.app import create_app

    lab = settings.quantlab.model_copy(update={"provider": "fixture", "keystore": "memory"})
    with TestClient(create_app(settings.model_copy(update={"quantlab": lab}))) as test_client:
        yield test_client


def test_api_connect_quote_approve_dataset(client: Any) -> None:
    status = client.get("/quantlab/connections/databento/status").json()
    assert status["status"] == "NOT_CONNECTED" and status["fixture"] is True

    long_key = "db-" + "A" * 5000
    rejected = client.post("/quantlab/connections/databento", json={"api_key": long_key})
    assert rejected.status_code == 400 and long_key not in rejected.text
    assert client.post("/quantlab/connections/databento", json={"api_key": 12}).status_code == 422

    ok = client.post("/quantlab/connections/databento", json={"api_key": KEY})
    assert ok.status_code == 200 and KEY not in ok.text and ok.json()["status"] == "CONNECTED"
    assert KEY not in client.get("/quantlab/connections/databento/status").text

    catalog = client.get("/quantlab/data/catalog", params={"dataset": "GLBX.MDP3"}).json()
    assert catalog["info"]["available_end"] == "2026-07-01"
    assert any(p["root"] == "NQ" for p in catalog["products"])

    quote = client.post("/quantlab/data/quote", json=NQ).json()
    assert quote["status"] == "OPEN"
    no_confirm = {"quote_id": quote["id"], "max_budget_usd": 10}
    assert client.post("/quantlab/data/requests", json=no_confirm).status_code == 422
    foreign = client.post(
        "/quantlab/data/requests",
        json={**no_confirm, "confirm": True},
        headers={"Origin": "https://evil.example"},
    )
    assert foreign.status_code == 403  # a web page can't approve spending
    job = client.post("/quantlab/data/requests", json={**no_confirm, "confirm": True}).json()
    for _ in range(400):
        job = client.get(f"/quantlab/data/jobs/{job['id']}").json()
        if job["status"] not in ("QUEUED", "RUNNING"):
            break
        import time

        time.sleep(0.02)
    assert job["status"] == "COMPLETED"
    built = client.post(
        "/quantlab/data/datasets",
        json={**{k: NQ[k] for k in ("dataset", "schema", "stype_in", "start", "end")},
              "symbol": "NQ.v.0"},
    )  # fmt: skip
    assert built.status_code == 200, built.text
    preview = client.get(f"/quantlab/data/datasets/{built.json()['id']}/preview").json()
    assert preview["points"] and preview["records"] == built.json()["records"]
    audit = client.get("/quantlab/data/audit").json()
    assert {"connect", "quote", "approve", "dataset.built"} <= {a["action"] for a in audit}
    assert KEY not in repr(audit)
    assert client.delete("/quantlab/connections/databento").json()["status"] == "NOT_CONNECTED"
