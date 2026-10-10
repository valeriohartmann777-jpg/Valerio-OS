"""The QuantLab Data Hub: provider connection, catalog, cost-first acquisition, cache.

Flow for market data:

1. **Connect** — the pasted key is checked with a metadata request
   (``list_datasets``), then stored in the OS keystore. Nothing is stored when
   the check fails; the key never reaches SQLite, logs, events or the API.
2. **Quote** — for a request (dataset, schema, symbology, symbols, UTC days) the
   hub subtracts the days already cached and asks the provider what the rest
   costs (``get_cost``, ``get_billable_size``, ``get_record_count``). The quote
   is stored with a signature over its exact parameters and expires after
   ``QUOTE_TTL``.
3. **Approve** — only an explicit user action through the API. The approval
   names a maximum budget, must be within the per-request and monthly caps,
   is re-priced against the provider right before it is used, and can be used
   exactly once (compare-and-set). JARVIS and ULTRON have no tool that reaches it.
4. **Download** — a persistent job, one symbol × ≤31 days per provider request,
   progress events, cancellable between chunks (charges already incurred stay),
   interrupted by a restart rather than silently resumed.
5. **Cache** — raw DBN kept as delivered; canonical per-day Parquet (integer
   prices); a day is never bought twice.
6. **Dataset** — an immutable snapshot of cached days with a manifest
   (provenance, mapping, conditions, checksums, acquisition) and a quality report.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.quantlab.futures import sessions as cal
from jarvis.quantlab.hub import cache
from jarvis.quantlab.hub.provider import (
    KEY_PATTERN,
    STYPES,
    DatabentoAdapter,
    ProviderError,
    check_symbols,
    sdk_version,
)
from jarvis.quantlab.hub.quality import assess_bars
from jarvis.quantlab.hub.store import HubStore, now
from jarvis.quantlab.hub.vault import CredentialVault, VaultError
from jarvis.quantlab.spec import canonical_json, sha256_text
from jarvis.util import new_id, utcnow

log = logging.getLogger("jarvis.quantlab.hub")

PROVIDER = "databento"
ACCOUNT = "databento-historical"
QUOTE_TTL = timedelta(minutes=15)
CHUNK_DAYS = 31
INGESTIBLE = ("ohlcv-1m", "definition")
DEFAULT_PER_REQUEST = 25.0
DEFAULT_PER_MONTH = 100.0
PRICE_TOLERANCE = 0.01  # re-priced estimate may differ by 1 % (or 1 cent) before re-approval
MAX_DAYS = {"ohlcv-1m": 800, "definition": 800}
CATALOG_TTL = timedelta(minutes=10)
LICENSE_NOTE = (
    "Market data licensed to your Databento account. Local research use only — "
    "raw and derived data must not be redistributed or shared."
)
ESTIMATE_CAVEAT = (
    "Estimate from Databento's metadata.get_cost for exactly these parameters, not a guaranteed "
    "price. Databento notes estimates can be higher than the final charge for ranges not aligned "
    "to 10 minutes, and definition data is priced in whole days. Charges for data already "
    "delivered can't be undone by cancelling."
)


class HubError(Exception):
    def __init__(
        self, code: str, message: str, remedy: str = "", status: int = 400, **extra: Any
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remedy = remedy
        self.status = status
        self.extra = extra

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "remedy": self.remedy, **self.extra}


def normalize_key(raw: str) -> str:
    key = raw.strip().removeprefix("export ").strip()
    name, sep, rest = key.partition("=")
    if sep and name.strip().upper() in ("DATABENTO_API_KEY", "DB_API_KEY"):
        key = rest
    return key.strip().strip("\"'“”‘’").strip()


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise HubError("INVALID_DATE", f"'{value}' isn't a date (YYYY-MM-DD).") from None


def _available_end(range_info: dict[str, Any], schema: str) -> date:
    """Exclusive end in whole UTC days: the day holding the provider's end isn't complete
    (or, at exactly midnight, isn't there at all) — either way it's never bought."""
    text = range_info.get("schemas", {}).get(schema, {}).get("end") or range_info.get("end", "")
    if len(text) < 10:
        raise HubError("RANGE_UNKNOWN", "The provider didn't report the dataset's end date.")
    return date.fromisoformat(text[:10])


def _available_start(range_info: dict[str, Any], schema: str) -> date:
    text = range_info.get("schemas", {}).get(schema, {}).get("start") or range_info.get("start", "")
    return date.fromisoformat(text[:10]) if text else date(1970, 1, 1)


class DataHubService:
    def __init__(
        self,
        *,
        store: HubStore,
        bus: EventBus,
        root: Path,
        vault: CredentialVault,
        adapter: DatabentoAdapter,
    ) -> None:
        self._store = store
        self._bus = bus
        self.root = root
        self._vault = vault
        self._adapter = adapter
        self._catalog: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._cancel: set[str] = set()
        self._dataset_lock = asyncio.Lock()

    @property
    def fixture(self) -> bool:
        return self._adapter.fixture_label is not None

    # Lifecycle --------------------------------------------------------------------------

    async def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(self.root / "staging", ignore_errors=True)
        for job in await self._store.jobs_with_status("QUEUED", "RUNNING"):
            await self._store.update_job(
                job["id"],
                status="INTERRUPTED",
                finished_at=now(),
                error_code="INTERRUPTED",
                error="JARVIS stopped during the download. Days already saved stay cached; "
                "request a new quote to fetch the rest (cached days are free).",
            )
            await self._store.audit(PROVIDER, "job.interrupted", {"job_id": job["id"]})
        self._worker = asyncio.create_task(self._work(), name="quantlab-hub")

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    async def _emit(self, kind: EventType, message: str, payload: dict[str, Any]) -> None:
        severity = Severity.WARNING if "fail" in message.lower() else Severity.INFO
        await self._bus.emit(
            kind, message=message, source="quantlab", severity=severity, payload=payload
        )

    # Credentials ------------------------------------------------------------------------

    def _key(self) -> str:
        try:
            key = self._vault.get(ACCOUNT)
        except VaultError as exc:
            raise HubError(exc.code, exc.message, exc.remedy, status=409) from None
        if not key:
            raise HubError(
                "NOT_CONNECTED",
                "Databento isn't connected.",
                "Paste your API key in QuantLab → Data Hub and choose Connect & Verify.",
                status=409,
            )
        return key

    def _provider_error(self, exc: ProviderError) -> HubError:
        status = 502 if exc.code in ("PROVIDER_ERROR", "NETWORK") else 400
        return HubError(exc.code, exc.message, exc.remedy, status=status, detail=exc.detail)

    async def status(self) -> dict[str, Any]:
        vault = self._vault.status()
        connection = await self._store.connection(PROVIDER) or {"status": "NOT_CONNECTED"}
        caps = await self.caps()
        month = await self._store.approved_since(_month_start())
        return {
            "provider": PROVIDER,
            "status": connection.get("status", "NOT_CONNECTED"),
            "key_hint": connection.get("key_hint"),
            "connected_at": connection.get("connected_at"),
            "verified_at": connection.get("verified_at"),
            "datasets": connection.get("datasets") or [],
            "error_code": connection.get("error_code"),
            "error": connection.get("error"),
            "keystore": vault.as_dict(),
            "fixture": self.fixture,
            "fixture_label": self._adapter.fixture_label,
            "sdk_version": sdk_version(),
            "caps": caps,
            "approved_this_month_usd": round(month, 4),
            "license_note": LICENSE_NOTE,
        }

    async def connect(self, raw_key: str) -> dict[str, Any]:
        key = normalize_key(raw_key)
        if not KEY_PATTERN.match(key):
            raise HubError(
                "INVALID_FORMAT",
                "That doesn't look like a Databento API key.",
                "Keys start with db- followed by letters and digits. Copy it again from the "
                "Databento portal → API keys.",
            )
        status = self._vault.status()
        if not status.available:
            raise HubError("NO_SECURE_KEYSTORE", status.reason or "No secure keystore.", status=409)
        try:
            datasets = await self._adapter.verify(key)
        except ProviderError as exc:
            await self._store.audit(PROVIDER, "connect.failed", {"code": exc.code})
            raise self._provider_error(exc) from None
        try:
            self._vault.put(ACCOUNT, key)
        except VaultError as exc:
            raise HubError(exc.code, exc.message, exc.remedy, status=409) from None
        stamp = now()
        await self._store.save_connection(
            PROVIDER,
            status="CONNECTED",
            key_hint=f"…{key[-4:]}",
            connected_at=stamp,
            verified_at=stamp,
            datasets=datasets,
            error_code=None,
            error=None,
            fixture=self.fixture,
        )
        self._catalog.clear()
        await self._store.audit(
            PROVIDER, "connect", {"datasets": len(datasets), "keystore": status.backend}
        )
        await self._emit(
            EventType.QUANTLAB_HUB_CHANGED,
            f"Databento connected — {len(datasets)} datasets visible",
            {"status": "CONNECTED"},
        )
        return await self.status()

    async def test(self) -> dict[str, Any]:
        key = self._key()
        try:
            datasets = await self._adapter.verify(key)
        except ProviderError as exc:
            state = "REJECTED" if exc.code == "AUTH_INVALID" else "CONNECTED"
            await self._store.save_connection(
                PROVIDER, status=state, error_code=exc.code, error=exc.message
            )
            await self._store.audit(PROVIDER, "test.failed", {"code": exc.code})
            await self._emit(
                EventType.QUANTLAB_HUB_CHANGED, f"Databento check failed: {exc.message}",
                {"status": state},
            )  # fmt: skip
            raise self._provider_error(exc) from None
        await self._store.save_connection(
            PROVIDER,
            status="CONNECTED",
            verified_at=now(),
            datasets=datasets,
            error_code=None,
            error=None,
        )
        await self._store.audit(PROVIDER, "test", {"datasets": len(datasets)})
        await self._emit(
            EventType.QUANTLAB_HUB_CHANGED, "Databento connection verified", {"status": "CONNECTED"}
        )
        return await self.status()

    async def disconnect(self) -> dict[str, Any]:
        try:
            removed = self._vault.delete(ACCOUNT)
        except VaultError as exc:
            raise HubError(exc.code, exc.message, exc.remedy, status=409) from None
        await self._store.save_connection(
            PROVIDER,
            status="NOT_CONNECTED",
            key_hint=None,
            verified_at=None,
            datasets=[],
            error_code=None,
            error=None,
        )
        self._catalog.clear()
        await self._store.audit(PROVIDER, "disconnect", {"removed": removed})
        await self._emit(
            EventType.QUANTLAB_HUB_CHANGED,
            "Databento disconnected — the key was removed; cached data stays",
            {"status": "NOT_CONNECTED"},
        )
        return await self.status()

    # Spend caps -------------------------------------------------------------------------

    async def caps(self) -> dict[str, float]:
        row = await self._store.settings(PROVIDER)
        if row is None:
            return {"per_request_usd": DEFAULT_PER_REQUEST, "per_month_usd": DEFAULT_PER_MONTH}
        return {
            "per_request_usd": float(row["max_usd_per_request"]),
            "per_month_usd": float(row["max_usd_per_month"]),
        }

    async def set_caps(self, per_request: float, per_month: float) -> dict[str, float]:
        if not (0 <= per_request <= 10_000 and 0 <= per_month <= 100_000):
            raise HubError("INVALID_CAP", "Caps must be between $0 and $10,000 / $100,000.")
        if per_request > per_month:
            raise HubError("INVALID_CAP", "The per-request cap can't exceed the monthly cap.")
        before = await self.caps()
        await self._store.save_settings(PROVIDER, per_request, per_month)
        await self._store.audit(
            PROVIDER,
            "caps.changed",
            {"before": before, "after": {"per_request": per_request, "per_month": per_month}},
        )
        return await self.caps()

    # Catalog ----------------------------------------------------------------------------

    async def dataset_info(self, dataset: str) -> dict[str, Any]:
        cached = self._catalog.get(dataset)
        if cached and utcnow() - cached[0] < CATALOG_TTL:
            return cached[1]
        key = self._key()
        try:
            schemas = await self._adapter.schemas(key, dataset)
            range_info = await self._adapter.dataset_range(key, dataset)
            start = _available_start(range_info, "ohlcv-1m")
            end = _available_end(range_info, "ohlcv-1m")
            recent = await self._adapter.condition(
                key, dataset, max(start, end - timedelta(days=30)), end - timedelta(days=1)
            )
        except ProviderError as exc:
            raise self._provider_error(exc) from None
        counts: dict[str, int] = {}
        for row in recent:
            counts[row["condition"]] = counts.get(row["condition"], 0) + 1
        info = {
            "dataset": dataset,
            "schemas": schemas,
            "ingestible": [s for s in schemas if s in INGESTIBLE],
            "range": range_info,
            "available_start": start.isoformat(),
            "available_end": end.isoformat(),
            "recent_conditions": counts,
            "recent_flagged": [r for r in recent if r["condition"] != "available"],
            "fixture": self.fixture,
        }
        self._catalog[dataset] = (utcnow(), info)
        return info

    async def resolve(
        self, dataset: str, symbols: list[str], stype_in: str, start: str, end: str
    ) -> dict[str, Any]:
        if stype_in not in STYPES:
            raise HubError("INVALID_STYPE", f"Symbology must be one of {', '.join(STYPES)}.")
        first, last = _day(start), _day(end)
        if last <= first:
            raise HubError("INVALID_RANGE", "The end date must be after the start date.")
        key = self._key()
        try:
            return await self._adapter.resolve(key, dataset, symbols, stype_in, first, last)
        except ProviderError as exc:
            raise self._provider_error(exc) from None

    # Quotes -----------------------------------------------------------------------------

    def _signature(self, quote: dict[str, Any]) -> str:
        core = {
            "provider": quote["provider"],
            "request": quote["request"],
            "items": [
                {k: item[k] for k in ("schema", "symbol", "stype_in", "dataset", "ranges")}
                for item in quote["items"]
            ],
            "cost_usd": round(float(quote["cost_usd"]), 6),
            "expires_at": quote["expires_at"],
        }
        return sha256_text(canonical_json(core))

    async def _missing(
        self, dataset: str, schema: str, stype_in: str, symbol: str, first: date, last: date
    ) -> tuple[list[date], list[dict[str, Any]]]:
        key = cache.cache_key(PROVIDER, dataset, schema, stype_in, symbol)
        have = await self._store.cached_days(key, first.isoformat(), last.isoformat())
        held = {row["day"] for row in have}
        return [d for d in cache.days(first, last) if d.isoformat() not in held], have

    async def quote(self, request: dict[str, Any]) -> dict[str, Any]:
        dataset = str(request.get("dataset", "")).strip()
        schema = str(request.get("schema", "")).strip()
        stype_in = str(request.get("stype_in", "")).strip()
        include_definitions = bool(request.get("include_definitions", True))
        try:
            symbols = check_symbols([str(s) for s in request.get("symbols") or []])
        except ProviderError as exc:
            raise HubError(exc.code, exc.detail or exc.message) from None
        first, last = _day(str(request.get("start", ""))), _day(str(request.get("end", "")))
        if stype_in not in STYPES:
            raise HubError("INVALID_STYPE", f"Symbology must be one of {', '.join(STYPES)}.")
        if last <= first:
            raise HubError("INVALID_RANGE", "The end date must be after the start date.")
        if schema not in INGESTIBLE:
            raise HubError(
                "SCHEMA_NOT_INGESTIBLE",
                f"QuantLab can't ingest {schema or 'that schema'} yet — only "
                f"{', '.join(INGESTIBLE)}.",
                "Trades, quotes and order-book schemas are planned (R7).",
            )
        info = await self.dataset_info(dataset)
        if schema not in info["schemas"]:
            raise HubError(
                "UNSUPPORTED_SCHEMA",
                f"{dataset} doesn't offer {schema}.",
                f"Available: {', '.join(info['schemas'])}.",
            )
        start_ok, end_ok = (
            date.fromisoformat(info["available_start"]),
            date.fromisoformat(info["available_end"]),
        )
        if first < start_ok or last > end_ok:
            raise HubError(
                "RANGE_UNAVAILABLE",
                f"{dataset} has complete days from {start_ok} to "
                f"{end_ok - timedelta(days=1)} (UTC).",
                "Pick dates inside that range; the current day is never bought while incomplete.",
            )
        if (last - first).days > MAX_DAYS[schema]:
            raise HubError(
                "RANGE_TOO_LONG", f"At most {MAX_DAYS[schema]} days per request for {schema}."
            )
        key = self._key()
        schemas = [schema] + (
            ["definition"] if include_definitions and schema != "definition" else []
        )
        try:
            conditions = await self._adapter.condition(
                key, dataset, first, last - timedelta(days=1)
            )
        except ProviderError as exc:
            raise self._provider_error(exc) from None
        condition_map = {row["date"]: row["condition"] for row in conditions}
        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        total_cost, total_bytes, total_records, cached = 0.0, 0, 0, 0
        for item_schema in schemas:
            for symbol in symbols:
                missing, have = await self._missing(
                    dataset, item_schema, stype_in, symbol, first, last
                )
                cached += len(have)
                ranges = []
                for r_start, r_end in cache.ranges(missing):
                    try:
                        est = await self._adapter.estimate(
                            key,
                            dataset=dataset,
                            schema=item_schema,
                            stype_in=stype_in,
                            symbols=[symbol],
                            start=r_start,
                            end=r_end,
                        )
                    except ProviderError as exc:
                        raise self._provider_error(exc) from None
                    warnings.extend(est.warnings)
                    ranges.append(
                        {
                            "start": r_start.isoformat(),
                            "end": r_end.isoformat(),
                            "cost_usd": est.cost_usd,
                            "billable_bytes": est.billable_bytes,
                            "records": est.records,
                        }
                    )
                    total_cost += est.cost_usd
                    total_bytes += est.billable_bytes
                    total_records += est.records
                items.append(
                    {
                        "dataset": dataset,
                        "schema": item_schema,
                        "symbol": symbol,
                        "stype_in": stype_in,
                        "ranges": ranges,
                        "cached_days": len(have),
                        "missing_days": len(missing),
                    }
                )
        created = utcnow()
        quote: dict[str, Any] = {
            "id": "q-" + new_id()[:16],
            "provider": PROVIDER,
            "created_at": created.isoformat(),
            "expires_at": (created + QUOTE_TTL).isoformat(),
            "request": {
                "dataset": dataset,
                "schema": schema,
                "stype_in": stype_in,
                "symbols": symbols,
                "start": first.isoformat(),
                "end": last.isoformat(),
                "include_definitions": include_definitions,
            },
            "items": items,
            "cost_usd": round(total_cost, 6),
            "billable_bytes": total_bytes,
            "records": total_records,
            "cached_days": cached,
            "conditions": {d: c for d, c in condition_map.items() if c != "available"},
            "warnings": sorted(set(warnings)),
            "fixture": self.fixture,
        }
        quote["signature"] = self._signature(quote)
        await self._store.add_quote(quote)
        await self._store.audit(
            PROVIDER,
            "quote",
            {"quote_id": quote["id"], "cost_usd": quote["cost_usd"], "request": quote["request"]},
        )
        await self._emit(
            EventType.QUANTLAB_HUB_CHANGED,
            f"Data quote {quote['id']}: estimated ${quote['cost_usd']:.2f} — waiting for approval"
            if quote["cost_usd"] > 0
            else f"Data quote {quote['id']}: everything requested is already cached",
            {"quote_id": quote["id"]},
        )
        return await self.quote_view(quote["id"])

    async def quote_view(self, quote_id: str) -> dict[str, Any]:
        await self._store.expire_quotes(now())
        quote = await self._store.quote(quote_id)
        if quote is None:
            raise HubError("NOT_FOUND", "No such quote.", status=404)
        caps = await self.caps()
        month = await self._store.approved_since(_month_start())
        quote["caveat"] = ESTIMATE_CAVEAT
        quote["caps"] = caps
        quote["approved_this_month_usd"] = round(month, 4)
        quote["within_caps"] = (
            quote["cost_usd"] <= caps["per_request_usd"]
            and month + quote["cost_usd"] <= caps["per_month_usd"]
        )
        quote["signature_valid"] = quote["signature"] == self._signature(quote)
        return quote

    async def quotes(self) -> list[dict[str, Any]]:
        await self._store.expire_quotes(now())
        return await self._store.quotes()

    async def reject(self, quote_id: str) -> dict[str, Any]:
        if not await self._store.close_quote(quote_id, "REJECTED", "Declined by the user."):
            raise HubError("NOT_OPEN", "That quote is no longer open.", status=409)
        await self._store.audit(PROVIDER, "quote.rejected", {"quote_id": quote_id})
        await self._emit(
            EventType.QUANTLAB_HUB_CHANGED,
            f"Data quote {quote_id} declined",
            {"quote_id": quote_id},
        )
        return await self.quote_view(quote_id)

    async def approve(self, quote_id: str, max_budget_usd: float) -> dict[str, Any]:
        """The only path to a billable download. Bound to the exact quote, once."""
        quote = await self.quote_view(quote_id)
        if quote["status"] != "OPEN":
            raise HubError(
                "NOT_OPEN",
                f"That quote is {quote['status'].lower()} — it can't be approved.",
                "Request a new quote.",
                status=409,
            )
        if not quote["signature_valid"]:
            await self._store.close_quote(quote_id, "REJECTED", "Signature mismatch.")
            await self._store.audit(PROVIDER, "approve.tampered", {"quote_id": quote_id})
            raise HubError("TAMPERED", "The quote changed after it was issued.", status=409)
        cost = float(quote["cost_usd"])
        if max_budget_usd < cost:
            raise HubError(
                "BUDGET_BELOW_ESTIMATE",
                f"The approved budget ${max_budget_usd:.2f} is below the estimate ${cost:.2f}.",
            )
        caps = quote["caps"]
        if cost > caps["per_request_usd"]:
            raise HubError(
                "OVER_REQUEST_CAP",
                f"${cost:.2f} exceeds your per-request cap of ${caps['per_request_usd']:.2f}.",
                "Narrow the request, or raise the cap in Data Hub settings first.",
            )
        if quote["approved_this_month_usd"] + cost > caps["per_month_usd"]:
            raise HubError(
                "OVER_MONTH_CAP",
                f"This would bring approved spend this month to "
                f"${quote['approved_this_month_usd'] + cost:.2f}, over the "
                f"${caps['per_month_usd']:.2f} monthly cap.",
                "Raise the monthly cap in Data Hub settings first, or wait for next month.",
            )
        chunks = _chunks(quote["items"])
        if cost > 0 or chunks:
            key = self._key()
            fresh = 0.0
            for item in quote["items"]:
                for r in item["ranges"]:
                    try:
                        est = await self._adapter.estimate(
                            key,
                            dataset=item["dataset"],
                            schema=item["schema"],
                            stype_in=item["stype_in"],
                            symbols=[item["symbol"]],
                            start=date.fromisoformat(r["start"]),
                            end=date.fromisoformat(r["end"]),
                        )
                    except ProviderError as exc:
                        raise self._provider_error(exc) from None
                    fresh += est.cost_usd
            if fresh > max_budget_usd or abs(fresh - cost) > max(PRICE_TOLERANCE * cost, 0.01):
                await self._store.close_quote(
                    quote_id, "SUPERSEDED", f"Re-priced at ${fresh:.4f} (quoted ${cost:.4f})."
                )
                await self._store.audit(
                    PROVIDER, "approve.repriced", {"quote_id": quote_id, "fresh": fresh}
                )
                raise HubError(
                    "PRICE_CHANGED",
                    f"The provider's estimate changed to ${fresh:.2f} (quoted ${cost:.2f}).",
                    "Review the new quote and approve again.",
                    status=409,
                )
        stamp = utcnow().isoformat()
        if not await self._store.claim_quote(quote_id, max_budget_usd, stamp):
            raise HubError(
                "NOT_OPEN", "That quote expired or was already used.", "Request a new quote.", 409
            )
        job_id = "job-" + new_id()[:12]
        await self._store.add_job(
            {
                "id": job_id,
                "quote_id": quote_id,
                "approved_budget_usd": max_budget_usd,
                "estimated_cost_usd": cost,
                "fixture": self.fixture,
            },
            chunks,
        )
        await self._store.link_quote(quote_id, job_id)
        await self._store.audit(
            PROVIDER,
            "approve",
            {"quote_id": quote_id, "job_id": job_id, "cost_usd": cost, "budget": max_budget_usd},
        )
        await self._emit(
            EventType.QUANTLAB_HUB_JOB,
            f"Download approved (${cost:.2f} estimated) — {len(chunks)} request(s) queued",
            {"job_id": job_id, "status": "QUEUED"},
        )
        await self._queue.put(job_id)
        return await self.job(job_id)

    # Jobs -------------------------------------------------------------------------------

    async def job(self, job_id: str) -> dict[str, Any]:
        job = await self._store.job(job_id)
        if job is None:
            raise HubError("NOT_FOUND", "No such job.", status=404)
        job["chunks"] = await self._store.chunks(job_id)
        return job

    async def jobs(self) -> list[dict[str, Any]]:
        return await self._store.jobs()

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = await self.job(job_id)
        if job["status"] not in ("QUEUED", "RUNNING"):
            raise HubError("NOT_RUNNING", f"The job is {job['status'].lower()}.", status=409)
        self._cancel.add(job_id)
        if await self._store.transition_job(
            job_id, ("QUEUED",), status="CANCELED", finished_at=now(),
            error="Cancelled before it started.",
        ):  # fmt: skip
            await self._store.audit(PROVIDER, "job.canceled", {"job_id": job_id})
            await self._emit(
                EventType.QUANTLAB_HUB_JOB, "Download cancelled before it started",
                {"job_id": job_id, "status": "CANCELED"},
            )  # fmt: skip
        await self._store.audit(PROVIDER, "job.cancel", {"job_id": job_id})
        return await self.job(job_id)

    async def wait_idle(self, timeout: float = 30.0) -> None:  # noqa: ASYNC109
        """For tests: until no job is queued or running."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if not await self._store.jobs_with_status("QUEUED", "RUNNING"):
                return
            await asyncio.sleep(0.02)
        raise TimeoutError("hub still busy")

    async def _work(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._run(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let the worker die
                log.exception("download job crashed")
                await self._finish_job(job_id, "FAILED", "INTERNAL", f"{type(exc).__name__}: {exc}")

    async def _finish_job(
        self, job_id: str, status: str, code: str | None, error: str | None
    ) -> None:
        if not await self._store.transition_job(
            job_id, ("RUNNING",), status=status, finished_at=now(), error_code=code, error=error
        ):
            return
        job = await self._store.job(job_id)
        await self._store.audit(PROVIDER, f"job.{status.lower()}", {"job_id": job_id, "code": code})
        await self._emit(
            EventType.QUANTLAB_HUB_JOB,
            f"Download {status.lower()}"
            + (f": {error}" if error else f" — {job['records'] if job else 0:,} records"),
            {"job_id": job_id, "status": status},
        )

    async def _run(self, job_id: str) -> None:
        if not await self._store.transition_job(
            job_id, ("QUEUED",), status="RUNNING", started_at=now()
        ):
            return  # cancelled before the worker got to it
        job = await self._store.job(job_id)
        assert job is not None
        await self._emit(
            EventType.QUANTLAB_HUB_JOB, "Download started", {"job_id": job_id, "status": "RUNNING"}
        )
        quote = await self._store.quote(job["quote_id"])
        conditions = (quote or {}).get("conditions", {})
        warnings: list[str] = []
        staging = self.root / "staging" / job_id
        staging.mkdir(parents=True, exist_ok=True)
        try:
            key = self._key()
        except HubError as exc:
            await self._finish_job(job_id, "FAILED", exc.code, exc.message)
            return
        for chunk in await self._store.chunks(job_id):
            if job_id in self._cancel:
                await self._finish_job(
                    job_id, "CANCELED", None,
                    "Cancelled. Days already delivered are cached (and were charged).",
                )  # fmt: skip
                return
            if chunk["status"] == "DONE":
                continue
            try:
                result = await self._fetch_chunk(key, job_id, chunk, staging, conditions)
            except ProviderError as exc:
                await self._store.update_chunk(
                    job_id, chunk["idx"], status="FAILED", error=exc.code
                )
                await self._finish_job(job_id, "FAILED", exc.code, exc.message)
                return
            except Exception as exc:
                await self._store.update_chunk(
                    job_id, chunk["idx"], status="FAILED", error="DECODE"
                )
                await self._finish_job(
                    job_id, "FAILED", "DECODE_FAILED", f"The delivered file couldn't be read: {exc}"
                )
                return
            warnings.extend(result["warnings"])
            current = await self._store.job(job_id)
            assert current is not None
            await self._store.update_job(
                job_id,
                chunks_done=current["chunks_done"] + 1,
                bytes=current["bytes"] + result["bytes"],
                records=current["records"] + result["records"],
                warnings=sorted(set(warnings)),
            )
            await self._emit(
                EventType.QUANTLAB_HUB_JOB,
                f"Downloaded {chunk['symbol']} {chunk['schema']} {chunk['start']} → {chunk['end']}",
                {
                    "job_id": job_id,
                    "status": "RUNNING",
                    "chunks_done": current["chunks_done"] + 1,
                    "chunks_total": current["chunks_total"],
                },
            )
        shutil.rmtree(staging, ignore_errors=True)
        await self._finish_job(job_id, "COMPLETED", None, None)

    async def _fetch_chunk(
        self,
        key: str,
        job_id: str,
        chunk: dict[str, Any],
        staging: Path,
        conditions: dict[str, str],
    ) -> dict[str, Any]:
        start, end = date.fromisoformat(chunk["start"]), date.fromisoformat(chunk["end"])
        tmp = staging / f"{chunk['idx']}.dbn"
        warnings = await self._download_with_retry(key, chunk, start, end, tmp)
        decoded = await asyncio.to_thread(cache.decode, tmp, chunk["schema"])
        parts = (
            PROVIDER,
            cache.safe(chunk["dataset"]),
            cache.safe(chunk["schema"]),
            cache.safe(chunk["stype_in"]),
            cache.safe(chunk["symbol"]),
        )
        raw_rel = Path("raw", *parts, f"{start}_{end}__{job_id}-{chunk['idx']}.dbn")
        raw_abs = self.root / raw_rel
        raw_abs.parent.mkdir(parents=True, exist_ok=True)
        raw_sha = await asyncio.to_thread(cache.sha256_file, tmp)
        size = tmp.stat().st_size
        os.replace(tmp, raw_abs)
        cache.freeze(raw_abs)
        rows = []
        key_ = cache.cache_key(
            PROVIDER, chunk["dataset"], chunk["schema"], chunk["stype_in"], chunk["symbol"]
        )
        for day in cache.days(start, end):
            table = decoded.by_day.get(day)
            canonical_rel: str | None = None
            sha: str | None = None
            if table is not None and table.num_rows:
                rel = Path("canonical", *parts, f"{day.isoformat()}.parquet")
                sha = await asyncio.to_thread(cache.write_parquet, self.root / rel, table)
                canonical_rel = str(rel)
            ids = cache.ids_on(decoded.mappings, chunk["symbol"], day)
            if not ids and table is not None:
                ids = sorted({int(x) for x in table.column("instrument_id").to_pylist()})
            rows.append(
                {
                    "cache_key": key_,
                    "day": day.isoformat(),
                    "provider": PROVIDER,
                    "dataset": chunk["dataset"],
                    "schema": chunk["schema"],
                    "stype_in": chunk["stype_in"],
                    "symbol": chunk["symbol"],
                    "records": table.num_rows if table is not None else 0,
                    "canonical_path": canonical_rel,
                    "sha256": sha,
                    "raw_path": str(raw_rel),
                    "raw_sha256": raw_sha,
                    "condition": conditions.get(day.isoformat(), "available"),
                    "instrument_ids": ids,
                    "job_id": job_id,
                    "transform_version": cache.TRANSFORM_VERSION,
                    "fixture": self.fixture,
                }
            )
        await self._store.add_cache_days(rows)
        await self._store.update_chunk(
            job_id,
            chunk["idx"],
            status="DONE",
            raw_path=str(raw_rel),
            raw_sha256=raw_sha,
            bytes=size,
            records=decoded.table.num_rows,
        )
        return {"bytes": size, "records": decoded.table.num_rows, "warnings": warnings}

    async def _download_with_retry(
        self, key: str, chunk: dict[str, Any], start: date, end: date, path: Path
    ) -> list[str]:
        delays = (2.0, 8.0)
        for attempt in range(len(delays) + 1):
            try:
                return await self._adapter.download(
                    key,
                    dataset=chunk["dataset"],
                    schema=chunk["schema"],
                    stype_in=chunk["stype_in"],
                    symbols=[chunk["symbol"]],
                    start=start,
                    end=end,
                    path=path,
                )
            except ProviderError as exc:
                transient = exc.code in ("RATE_LIMITED", "PROVIDER_ERROR", "NETWORK")
                if not transient or attempt == len(delays):
                    raise
                await self._store.audit(
                    PROVIDER,
                    "download.retry",
                    {"chunk": chunk["idx"], "code": exc.code, "attempt": attempt + 1},
                )
                await asyncio.sleep(delays[attempt] * self.retry_scale)
        raise AssertionError("unreachable")

    retry_scale = 1.0  # tests shrink the backoff

    # Cache and datasets -----------------------------------------------------------------

    async def cache_summary(self) -> list[dict[str, Any]]:
        return await self._store.cache_summary()

    async def build_dataset(self, request: dict[str, Any]) -> dict[str, Any]:
        dataset = str(request.get("dataset", "")).strip()
        schema = str(request.get("schema", "ohlcv-1m"))
        stype_in = str(request.get("stype_in", "")).strip()
        symbol = str(request.get("symbol", "")).strip()
        first, last = _day(str(request.get("start", ""))), _day(str(request.get("end", "")))
        if schema != "ohlcv-1m":
            raise HubError("SCHEMA_NOT_SUPPORTED", "Datasets are built from ohlcv-1m bars for now.")
        if last <= first:
            raise HubError("INVALID_RANGE", "The end date must be after the start date.")
        async with self._dataset_lock:
            missing, have = await self._missing(dataset, schema, stype_in, symbol, first, last)
            if missing:
                spans = [f"{a} → {b}" for a, b in cache.ranges(missing)]
                raise HubError(
                    "NOT_CACHED",
                    f"{len(missing)} day(s) aren't in the local cache: {', '.join(spans[:4])}.",
                    "Request a quote for them in the Data Hub (cached days cost nothing).",
                    missing=[d.isoformat() for d in missing],
                )
            _, def_have = await self._missing(dataset, "definition", stype_in, symbol, first, last)
            return await self._build(dataset, schema, stype_in, symbol, first, last, have, def_have)

    async def _build(
        self,
        dataset: str,
        schema: str,
        stype_in: str,
        symbol: str,
        first: date,
        last: date,
        have: list[dict[str, Any]],
        def_have: list[dict[str, Any]],
    ) -> dict[str, Any]:
        for row in have + def_have:
            path = row.get("canonical_path")
            if path:
                actual = await asyncio.to_thread(cache.sha256_file, self.root / path)
                if actual != row["sha256"]:
                    raise HubError(
                        "CACHE_TAMPERED",
                        f"Cached file for {row['day']} changed on disk (checksum mismatch).",
                        "Delete the cache folder for this symbol and download again.",
                        status=409,
                    )
        bars = await asyncio.to_thread(
            cache.read_days, self.root, [r["canonical_path"] for r in have]
        )
        definitions = await asyncio.to_thread(
            cache.read_days, self.root, [r["canonical_path"] for r in def_have]
        )
        if bars is None:
            raise HubError("EMPTY", "No bars in that range (all days were empty).")
        conditions = {r["day"]: r["condition"] for r in have}
        quality = await asyncio.to_thread(
            assess_bars,
            bars,
            start=first,
            end=last,
            conditions=conditions,
            definitions=definitions,
        )
        instruments = _instruments(bars, definitions)
        mapping: list[dict[str, Any]] = []
        for row in have:
            ids = row["instrument_ids"]
            if (
                mapping
                and mapping[-1]["instrument_ids"] == ids
                and mapping[-1]["end"] == row["day"]
            ):
                mapping[-1]["end"] = (
                    date.fromisoformat(row["day"]) + timedelta(days=1)
                ).isoformat()
            else:
                mapping.append(
                    {
                        "start": row["day"],
                        "end": (date.fromisoformat(row["day"]) + timedelta(days=1)).isoformat(),
                        "instrument_ids": ids,
                    }
                )
        jobs = sorted({r["job_id"] for r in have + def_have})
        acquisition = []
        for job_id in jobs:
            job = await self._store.job(job_id)
            if job:
                acquisition.append(
                    {
                        "job_id": job_id,
                        "quote_id": job["quote_id"],
                        "estimated_cost_usd": job["estimated_cost_usd"],
                        "approved_budget_usd": job["approved_budget_usd"],
                        "approved_at": job["created_at"],
                    }
                )
        fixture = any(bool(r["fixture"]) for r in have)
        core = {
            "manifest_version": "qh-dataset-1",
            "provider": PROVIDER,
            "fixture": fixture,
            "dataset": dataset,
            "schema": schema,
            "stype_in": stype_in,
            "symbol": symbol,
            "window": {"start": first.isoformat(), "end": last.isoformat(), "timezone": "UTC"},
            "time_convention": (
                "ts_event is the START of each 1-minute bar (Databento OHLCV). A bar's values are "
                "known only at ts_event + 1 minute. Bars exist only for minutes with trades."
            ),
            "prices": "Integer fixed precision (1e-9), unadjusted, as delivered.",
            "records": bars.num_rows,
            "instruments": instruments,
            "mapping": mapping,
            "conditions": {d: c for d, c in conditions.items() if c != "available"},
            "files": {
                "raw": sorted({(r["raw_path"], r["raw_sha256"]) for r in have + def_have}),
                "canonical": [
                    {"day": r["day"], "path": r["canonical_path"], "sha256": r["sha256"]}
                    for r in have + def_have
                    if r["canonical_path"]
                ],
            },
            "definitions": {
                "days_cached": len(def_have),
                "records": definitions.num_rows if definitions is not None else 0,
            },
            "transform_version": cache.TRANSFORM_VERSION,
            "calendar": {"library": cal.LIBRARY, "version": cal.library_version()},
            "sdk_version": sdk_version(),
            "acquisition": acquisition,
            "license": LICENSE_NOTE,
        }
        dataset_id = "dh-" + sha256_text(canonical_json(core))[:16]
        existing = await self._store.dataset(dataset_id)
        if existing:
            return self._dataset_view(existing)
        folder = self.root / "snapshots" / dataset_id
        folder.mkdir(parents=True, exist_ok=True)
        snap_sha = await asyncio.to_thread(cache.write_parquet, folder / "bars.parquet", bars)
        def_sha = None
        if definitions is not None:
            def_sha = await asyncio.to_thread(
                cache.write_parquet, folder / "definitions.parquet", definitions
            )
        manifest = {
            **core,
            "id": dataset_id,
            "snapshot": {"path": f"snapshots/{dataset_id}/bars.parquet", "sha256": snap_sha},
            "definitions_snapshot": (
                {"path": f"snapshots/{dataset_id}/definitions.parquet", "sha256": def_sha}
                if def_sha
                else None
            ),
            "quality_status": quality["status"],
            "created_at": now(),
        }
        manifest_path = folder / "manifest.json"
        if not manifest_path.exists():
            manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
            cache.freeze(manifest_path)
        record = {
            "id": dataset_id,
            "provider": PROVIDER,
            "dataset": dataset,
            "schema": schema,
            "stype_in": stype_in,
            "symbol": symbol,
            "start": first.isoformat(),
            "end": last.isoformat(),
            "records": bars.num_rows,
            "snapshot_path": manifest["snapshot"]["path"],
            "snapshot_sha256": snap_sha,
            "manifest": manifest,
            "quality": quality,
            "fixture": fixture,
        }
        await self._store.add_dataset(record)
        await self._store.audit(PROVIDER, "dataset.built", {"dataset_id": dataset_id})
        await self._emit(
            EventType.QUANTLAB_HUB_DATASET,
            f"Dataset {dataset_id} built — {bars.num_rows:,} bars, quality {quality['status']}",
            {"dataset_id": dataset_id},
        )
        stored = await self._store.dataset(dataset_id)
        assert stored is not None
        return self._dataset_view(stored)

    @staticmethod
    def _dataset_view(record: dict[str, Any]) -> dict[str, Any]:
        return record

    async def datasets(self) -> list[dict[str, Any]]:
        rows = await self._store.datasets()
        for row in rows:
            row["quality"] = {
                "status": row["quality"]["status"],
                "summary": row["quality"].get("summary", {}),
                "findings": row["quality"].get("findings", []),
            }
        return rows

    async def dataset(self, dataset_id: str) -> dict[str, Any]:
        record = await self._store.dataset(dataset_id)
        if record is None:
            raise HubError("NOT_FOUND", "No such dataset.", status=404)
        return record

    async def load(self, dataset_id: str) -> tuple[dict[str, Any], pa.Table, pa.Table | None]:
        """Snapshot bars + definitions, checksum-verified. Raises on any mismatch."""
        record = await self.dataset(dataset_id)
        path = self.root / record["snapshot_path"]
        actual = await asyncio.to_thread(cache.sha256_file, path)
        if actual != record["snapshot_sha256"]:
            raise HubError(
                "SNAPSHOT_TAMPERED",
                "The dataset snapshot changed on disk (checksum mismatch); it won't be used.",
                status=409,
            )
        bars = await asyncio.to_thread(pq.read_table, path)
        definitions = None
        snap = record["manifest"].get("definitions_snapshot")
        if snap:
            def_path = self.root / snap["path"]
            if await asyncio.to_thread(cache.sha256_file, def_path) != snap["sha256"]:
                raise HubError(
                    "SNAPSHOT_TAMPERED", "The definitions snapshot changed on disk.", status=409
                )
            definitions = await asyncio.to_thread(pq.read_table, def_path)
        return record, bars, definitions

    async def preview(self, dataset_id: str, points: int = 600) -> dict[str, Any]:
        """A downsampled close series for the chart (min/max kept per bucket)."""
        _, bars, _ = await self.load(dataset_id)
        ts = bars.column("ts_event").to_pylist()
        close = bars.column("close").to_pylist()
        iid = bars.column("instrument_id").to_pylist()
        n = len(ts)
        if n == 0:
            return {"points": []}
        step = max(1, n // points)
        out = []
        for start in range(0, n, step):
            seg = range(start, min(n, start + step))
            hi = max(seg, key=lambda i: close[i])
            lo = min(seg, key=lambda i: close[i])
            for i in sorted({hi, lo}):
                out.append({"ts": ts[i], "close": close[i] / 1e9, "instrument_id": iid[i]})
        return {"points": out, "records": n}

    async def audit_log(self) -> list[dict[str, Any]]:
        return await self._store.audit_log()

    async def overview(self) -> dict[str, Any]:
        status = await self.status()
        jobs = await self._store.jobs(5)
        return {
            "status": status["status"],
            "fixture": status["fixture"],
            "datasets": len(await self._store.datasets()),
            "cache": await self._store.cache_summary(),
            "jobs_running": sum(1 for j in jobs if j["status"] in ("QUEUED", "RUNNING")),
            "approved_this_month_usd": status["approved_this_month_usd"],
        }


def _instruments(bars: pa.Table, definitions: pa.Table | None) -> list[dict[str, Any]]:
    ids = bars.column("instrument_id").to_pylist()
    ts = bars.column("ts_event").to_pylist()
    seen: dict[int, dict[str, Any]] = {}
    for i, instrument in enumerate(ids):
        entry = seen.setdefault(
            instrument, {"instrument_id": instrument, "first_ts": ts[i], "bars": 0}
        )
        entry["last_ts"] = ts[i]
        entry["bars"] += 1
    if definitions is not None:
        for row in definitions.to_pylist():
            known = seen.get(int(row["instrument_id"]))
            if known is not None:
                known.update(
                    raw_symbol=row.get("raw_symbol"),
                    min_price_increment_fixed=row.get("min_price_increment"),
                    unit_of_measure_qty_fixed=row.get("unit_of_measure_qty"),
                    min_price_increment_amount_fixed=row.get("min_price_increment_amount"),
                    expiration_ns=row.get("expiration"),
                    currency=row.get("currency"),
                )
    return list(seen.values())


def _chunks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        for r in item["ranges"]:
            start, end = date.fromisoformat(r["start"]), date.fromisoformat(r["end"])
            while start < end:
                stop = min(end, start + timedelta(days=CHUNK_DAYS))
                out.append(
                    {
                        "dataset": item["dataset"],
                        "schema": item["schema"],
                        "stype_in": item["stype_in"],
                        "symbol": item["symbol"],
                        "start": start.isoformat(),
                        "end": stop.isoformat(),
                    }
                )
                start = stop
    return out


def _month_start() -> str:
    today = utcnow()
    return datetime(today.year, today.month, 1, tzinfo=UTC).isoformat()
