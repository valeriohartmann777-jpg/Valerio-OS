"""One-time Databento batch acquisition — the Data Vault's only paid path.

Order of operations (each step is tested):

1. **Prepare** (free): the request is normalized and diffed against what the vault already
   holds from Databento (set subtraction per symbol, schema and UTC day); only the missing
   ranges are priced with ``metadata.get_cost`` / ``get_billable_size`` /
   ``get_record_count`` — metadata calls that cost nothing. The result is an acquisition
   with an exact request hash, a quote in USD (the currency Databento bills in) and a
   15-minute expiry. The same request asked again returns the same acquisition.
2. **Approve** (owner, in the UI): ``confirm: true``, the request hash the owner saw and a
   maximum amount ≥ the quote, within the hub's per-request and monthly caps. Recorded in
   an append-only approval log.
3. **Execute**: re-priced (free) against the approval's maximum, then a compare-and-set
   APPROVED → SUBMITTING lets exactly one caller submit. Each batch job is persisted as
   SUBMITTING before ``batch.submit_job`` and with its provider job id right after. A
   submission whose outcome is unknown (crash, network loss mid-call) is never repeated
   automatically: after a restart the vault looks the job up with ``batch.list_jobs`` and
   adopts it, otherwise it stops and asks the owner to check the Databento portal.
4. **Download** with ``batch.download`` per file into staging, resumed from partial files,
   verified against the SHA-256 Databento lists, then moved into the immutable raw archive.
5. **Ingest** into the per-day cache (the same one downloads and imports use). Days already
   held are kept; nothing is bought twice.

Offline mode blocks every step that touches the network. The backtest path never imports
this module.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa

from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.quantlab.hub import cache
from jarvis.quantlab.hub.provider import STYPES, DatabentoAdapter, ProviderError, check_symbols
from jarvis.quantlab.hub.service import (
    ESTIMATE_CAVEAT,
    PROVIDER,
    QUOTE_TTL,
    DataHubService,
    HubError,
)
from jarvis.quantlab.hub.store import HubStore, now
from jarvis.quantlab.spec import canonical_json, sha256_text
from jarvis.quantlab.vault.service import VaultError, VaultService
from jarvis.quantlab.vault.store import VaultStore
from jarvis.util import new_id, utcnow

log = logging.getLogger("jarvis.quantlab.vault.acquire")

SCHEMAS = ("ohlcv-1m", "ohlcv-1h", "definition")
MAX_YEARS = 10
APPROVAL_TTL = timedelta(minutes=30)
ACTIVE = ("APPROVED", "SUBMITTING", "SUBMITTED", "DOWNLOADING", "INGESTING")
OPEN = ("QUOTED", *ACTIVE, "COMPLETED", "NOTHING_TO_BUY", "ATTENTION")


def _day(text: str) -> date:
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        raise VaultError("INVALID_DATE", f"'{text}' isn't a date (YYYY-MM-DD).") from None


class Acquisitions:
    def __init__(
        self,
        *,
        store: VaultStore,
        hub: DataHubService,
        hub_store: HubStore,
        vault: VaultService,
        adapter: DatabentoAdapter,
        bus: EventBus,
    ) -> None:
        self._store = store
        self._hub = hub
        self._hub_store = hub_store
        self._vault = vault
        self._adapter = adapter
        self._bus = bus
        self.root = hub.root
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self.poll_seconds = 20.0  # tests shrink it
        self.retry_seconds = 5.0

    async def start(self) -> None:
        for row in await self._store.acquisitions_with_status(*ACTIVE[1:]):
            # Restarted mid-acquisition: continue from the persisted state, never resubmit.
            self._spawn(row["id"])

    async def stop(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()

    async def wait_idle(self, timeout: float = 60.0) -> None:  # noqa: ASYNC109
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if not any(not t.done() for t in self._tasks.values()):
                return
            await asyncio.sleep(0.02)
        raise TimeoutError("acquisition still running")

    async def _emit(self, message: str, payload: dict[str, Any], warn: bool = False) -> None:
        await self._bus.emit(
            EventType.QUANTLAB_VAULT,
            message=message,
            source="quantlab",
            severity=Severity.WARNING if warn else Severity.INFO,
            payload=payload,
        )

    def _require_online(self) -> None:
        reason = self._vault.block_reason()
        if reason:
            raise VaultError("OFFLINE_MODE", reason, "Turn offline mode off in the Data Vault.")

    def _key(self) -> str:
        try:
            return self._hub.api_key()
        except HubError as exc:
            raise VaultError(exc.code, exc.message, exc.remedy, status=exc.status) from None

    # Prepare (free quote) --------------------------------------------------------------------

    async def prepare(self, request: dict[str, Any]) -> dict[str, Any]:
        self._require_online()
        dataset = str(request.get("dataset") or "GLBX.MDP3").strip()
        schema = str(request.get("schema") or "ohlcv-1m").strip()
        stype_in = str(request.get("stype_in") or "continuous").strip()
        include_definitions = bool(request.get("include_definitions", True))
        try:
            symbols = check_symbols([str(s) for s in request.get("symbols") or []])
        except ProviderError as exc:
            raise VaultError(exc.code, exc.detail or exc.message) from None
        if schema not in SCHEMAS:
            raise VaultError("SCHEMA", f"Batch acquisition supports {', '.join(SCHEMAS)}.")
        if stype_in not in STYPES:
            raise VaultError("INVALID_STYPE", f"Symbology must be one of {', '.join(STYPES)}.")
        first, last = _day(request.get("start", "")), _day(request.get("end", ""))
        if last <= first:
            raise VaultError("INVALID_RANGE", "The end date must be after the start date.")
        if (last - first).days > 366 * MAX_YEARS:
            raise VaultError("RANGE_TOO_LONG", f"At most {MAX_YEARS} years per acquisition.")
        key = self._key()
        try:
            info = await self._hub.dataset_info(dataset)
        except HubError as exc:
            raise VaultError(exc.code, exc.message, exc.remedy, status=exc.status) from None
        if schema not in info["schemas"]:
            raise VaultError("UNSUPPORTED_SCHEMA", f"{dataset} doesn't offer {schema}.")
        start_ok = date.fromisoformat(info["available_start"])
        end_ok = date.fromisoformat(info["available_end"])
        if first < start_ok or last > end_ok:
            raise VaultError(
                "RANGE_UNAVAILABLE",
                f"{dataset} has complete days from {start_ok} to {end_ok - timedelta(days=1)} "
                "(UTC). The current day is never bought while incomplete.",
            )
        schemas = [schema] + (
            ["definition"] if include_definitions and schema != "definition" else []
        )
        items: list[dict[str, Any]] = []
        held_days = 0
        for item_schema in schemas:
            for symbol in symbols:
                missing, have = await self._hub.missing_days(
                    dataset, item_schema, stype_in, symbol, first, last
                )
                held_days += len(have)
                for a, b in cache.ranges(missing):
                    items.append(
                        {
                            "symbol": symbol,
                            "schema": item_schema,
                            "stype_in": stype_in,
                            "dataset": dataset,
                            "start": a.isoformat(),
                            "end": b.isoformat(),
                        }
                    )
        normalized = {
            "provider": PROVIDER,
            "dataset": dataset,
            "schema": schema,
            "stype_in": stype_in,
            "symbols": sorted(symbols),
            "start": first.isoformat(),
            "end": last.isoformat(),
            "include_definitions": include_definitions,
            "missing": items,
        }
        request_hash = sha256_text(canonical_json(normalized))
        existing = await self._store.acquisition_by_hash(request_hash)
        if existing is not None and not self._expired(existing):
            return existing
        if not items:
            record: dict[str, Any] = {
                "id": "acq-" + new_id()[:16],
                "request_hash": request_hash,
                "request": normalized,
                "items": [],
                "status": "NOTHING_TO_BUY",
                "quote": {
                    "currency": "USD",
                    "cost_usd": 0.0,
                    "held_days": held_days,
                    "message": "Everything requested is already in the vault from Databento.",
                },
                "fixture": self._hub.fixture,
            }
            await self._store.add_acquisition(record)
            return await self._get(str(record["id"]))
        jobs: list[dict[str, Any]] = []
        total_cost, total_bytes, total_records = 0.0, 0, 0
        warnings: list[str] = []
        try:
            conditions = await self._adapter.condition(
                key, dataset, first, last - timedelta(days=1)
            )
            for item in items:
                est = await self._adapter.estimate(
                    key,
                    dataset=dataset,
                    schema=item["schema"],
                    stype_in=stype_in,
                    symbols=[item["symbol"]],
                    start=date.fromisoformat(item["start"]),
                    end=date.fromisoformat(item["end"]),
                )
                warnings.extend(est.warnings)
                total_cost += est.cost_usd
                total_bytes += est.billable_bytes
                total_records += est.records
                jobs.append(
                    {
                        **item,
                        "estimated_usd": round(est.cost_usd, 6),
                        "billable_bytes": est.billable_bytes,
                        "records": est.records,
                        "state": "PENDING",
                        "provider_job": None,
                        "files": [],
                    }
                )
        except ProviderError as exc:
            err = self._hub.provider_error(exc)
            raise VaultError(err.code, err.message, err.remedy, status=err.status) from None
        expires = utcnow() + QUOTE_TTL
        record = {
            "id": "acq-" + new_id()[:16],
            "request_hash": request_hash,
            "request": normalized,
            "items": items,
            "status": "QUOTED",
            "quote": {
                "currency": "USD",
                "cost_usd": round(total_cost, 6),
                "billable_bytes": total_bytes,
                "records": total_records,
                "held_days_deducted": held_days,
                "jobs": len(jobs),
                "source": "Databento metadata.get_cost (free) for exactly the missing ranges",
                "caveat": ESTIMATE_CAVEAT,
                "warnings": sorted(set(warnings))[:10],
                "conditions": {
                    c["date"]: c["condition"] for c in conditions if c["condition"] != "available"
                },
                "quoted_at": now(),
                "fixture_note": self._adapter.fixture_label,
            },
            "expires_at": expires.isoformat(),
            "job": {"jobs": jobs},
            "fixture": self._hub.fixture,
        }
        await self._store.add_acquisition(record)
        await self._emit(
            f"Databento quote: ${total_cost:,.2f} for {len(jobs)} missing range(s) — "
            "waiting for your approval",
            {"acquisition_id": record["id"], "status": "QUOTED"},
        )
        return await self._get(str(record["id"]))

    def _expired(self, row: dict[str, Any]) -> bool:
        if row["status"] != "QUOTED" or not row.get("expires_at"):
            return False
        return bool(row["expires_at"] < utcnow().isoformat())

    async def _get(self, acquisition_id: str) -> dict[str, Any]:
        row = await self._store.acquisition(acquisition_id)
        if row is None:
            raise VaultError("NOT_FOUND", "No such acquisition.", status=404)
        if self._expired(row):
            await self._store.transition_acquisition(acquisition_id, ("QUOTED",), status="EXPIRED")
            row = await self._store.acquisition(acquisition_id)
            assert row is not None
        row["approvals"] = await self._store.approvals(acquisition_id)
        return row

    async def get(self, acquisition_id: str) -> dict[str, Any]:
        return await self._get(acquisition_id)

    async def acquisitions(self) -> list[dict[str, Any]]:
        return await self._store.acquisitions()

    # Approve ----------------------------------------------------------------------------------

    async def approve(
        self, acquisition_id: str, request_hash: str, max_usd: float, confirm: bool
    ) -> dict[str, Any]:
        if confirm is not True:
            raise VaultError("CONFIRM_REQUIRED", "Approving a purchase needs your confirmation.")
        row = await self._get(acquisition_id)
        if row["status"] == "EXPIRED":
            raise VaultError(
                "QUOTE_EXPIRED", "The quote expired — prepare a fresh one.", status=409
            )
        if row["status"] != "QUOTED":
            raise VaultError(
                "NOT_QUOTED", f"The acquisition is {row['status'].lower()}.", status=409
            )
        if request_hash != row["request_hash"]:
            raise VaultError(
                "REQUEST_MISMATCH",
                "The approval doesn't name this exact request (it changed since you saw it).",
                status=409,
            )
        cost = float(row["quote"]["cost_usd"])
        if max_usd < cost:
            raise VaultError(
                "BELOW_QUOTE", f"The maximum (${max_usd:.2f}) is below the quote (${cost:.2f})."
            )
        caps = await self._hub.caps()
        if max_usd > caps["per_request_usd"]:
            raise VaultError(
                "OVER_CAP",
                f"${max_usd:.2f} is above your per-request cap of ${caps['per_request_usd']:.2f}.",
                "Raise the cap in the Data Hub settings first if you really want this.",
            )
        spent = await self._hub.month_spent() + await self._approved_this_month()
        if spent + max_usd > caps["per_month_usd"]:
            raise VaultError(
                "OVER_MONTHLY_CAP",
                f"This would bring this month's approved data spend to ${spent + max_usd:.2f}, "
                f"above your cap of ${caps['per_month_usd']:.2f}.",
            )
        if not await self._store.transition_acquisition(
            acquisition_id, ("QUOTED",), status="APPROVED", approved_at=now(), max_usd=max_usd
        ):
            raise VaultError("NOT_QUOTED", "The acquisition changed meanwhile.", status=409)
        await self._store.add_approval(
            {
                "acquisition_id": acquisition_id,
                "request_hash": request_hash,
                "quoted_usd": cost,
                "max_usd": max_usd,
                "detail": {"request": row["request"], "jobs": len(row["items"])},
            }
        )
        await self._emit(
            f"Data purchase approved: up to ${max_usd:,.2f} (quote ${cost:,.2f})",
            {"acquisition_id": acquisition_id, "status": "APPROVED"},
        )
        return await self._get(acquisition_id)

    async def _approved_this_month(self) -> float:
        month = utcnow().strftime("%Y-%m")
        return sum(
            float(a["quoted_usd"])
            for a in await self._store.approvals()
            if str(a["at"]).startswith(month)
        )

    # Execute ----------------------------------------------------------------------------------

    async def execute(self, acquisition_id: str) -> dict[str, Any]:
        self._require_online()
        row = await self._get(acquisition_id)
        if row["status"] in ACTIVE[1:] or row["status"] == "COMPLETED":
            return row  # already running or done: never a second submission
        if row["status"] != "APPROVED":
            raise VaultError(
                "NOT_APPROVED",
                "This purchase hasn't been approved (or was already used).",
                status=409,
            )
        approvals = [a for a in row["approvals"] if a["request_hash"] == row["request_hash"]]
        if not approvals:
            raise VaultError("NOT_APPROVED", "No approval for this exact request.", status=409)
        approved_at = approvals[-1]["at"]
        if (utcnow() - _parse(approved_at)) > APPROVAL_TTL:
            await self._store.transition_acquisition(
                acquisition_id, ("APPROVED",), status="EXPIRED",
                error="The approval is older than 30 minutes — prepare and approve again.",
            )  # fmt: skip
            raise VaultError("APPROVAL_EXPIRED", "The approval expired.", status=409)
        key = self._key()
        total = 0.0
        try:
            for job in row["job"]["jobs"]:
                est = await self._adapter.estimate(
                    key,
                    dataset=job["dataset"],
                    schema=job["schema"],
                    stype_in=job["stype_in"],
                    symbols=[job["symbol"]],
                    start=date.fromisoformat(job["start"]),
                    end=date.fromisoformat(job["end"]),
                )
                total += est.cost_usd
        except ProviderError as exc:
            err = self._hub.provider_error(exc)
            raise VaultError(err.code, err.message, err.remedy, status=err.status) from None
        if total > float(row["max_usd"]) + 1e-9:
            await self._store.transition_acquisition(
                acquisition_id, ("APPROVED",), status="FAILED", error_code="PRICE_CHANGED",
                error=f"Re-priced at ${total:.2f}, above the approved ${row['max_usd']:.2f}. "
                "Nothing was submitted.",
            )  # fmt: skip
            raise VaultError("PRICE_CHANGED", "The price rose above your approval.", status=409)
        if not await self._store.transition_acquisition(
            acquisition_id, ("APPROVED",), status="SUBMITTING"
        ):
            return await self._get(acquisition_id)  # someone else won the race
        self._spawn(acquisition_id)
        return await self._get(acquisition_id)

    def _spawn(self, acquisition_id: str) -> None:
        task = self._tasks.get(acquisition_id)
        if task is not None and not task.done():
            return
        self._tasks[acquisition_id] = asyncio.create_task(
            self._run(acquisition_id), name=f"acquisition-{acquisition_id}"
        )

    async def resume(self, acquisition_id: str) -> dict[str, Any]:
        """After ATTENTION: look the uncertain job up again (never a new submission)."""
        self._require_online()
        if not await self._store.transition_acquisition(
            acquisition_id, ("ATTENTION",), status="SUBMITTING", error_code=None, error=None
        ):
            raise VaultError("NOT_WAITING", "Nothing to check for this acquisition.", status=409)
        self._spawn(acquisition_id)
        return await self._get(acquisition_id)

    async def abandon(self, acquisition_id: str) -> dict[str, Any]:
        """The owner closes an acquisition (e.g. after checking the Databento portal)."""
        if not await self._store.transition_acquisition(
            acquisition_id, ("QUOTED", "APPROVED", "ATTENTION"), status="CANCELED"
        ):
            raise VaultError("NOT_OPEN", "This acquisition can't be closed now.", status=409)
        return await self._get(acquisition_id)

    # Worker ------------------------------------------------------------------------------------

    async def _save_jobs(
        self, acquisition_id: str, jobs: list[dict[str, Any]], **fields: Any
    ) -> None:
        done = sum(1 for j in jobs if j["state"] == "INGESTED")
        await self._store.update_acquisition(
            acquisition_id,
            job={"jobs": jobs},
            progress=round(done / max(1, len(jobs)), 3),
            **fields,
        )

    async def _run(self, acquisition_id: str) -> None:
        try:
            await self._work(acquisition_id)
        except asyncio.CancelledError:
            raise
        except VaultError as exc:
            await self._store.update_acquisition(
                acquisition_id, status="FAILED", error_code=exc.code, error=exc.message
            )
            await self._emit(
                f"Acquisition failed: {exc.message}", {"acquisition_id": acquisition_id}, True
            )
        except Exception as exc:  # the worker must not die silently
            log.exception("acquisition crashed")
            await self._store.update_acquisition(
                acquisition_id,
                status="FAILED",
                error_code="INTERNAL",
                error=f"{type(exc).__name__}: {exc}",
            )

    async def _work(self, acquisition_id: str) -> None:
        row = await self._get(acquisition_id)
        jobs: list[dict[str, Any]] = row["job"]["jobs"]
        key = self._key()
        for job in jobs:
            if job["state"] == "INGESTED":
                continue
            if job["provider_job"] is None:
                if job["state"] == "SUBMITTING":
                    # The process stopped during submit_job: look before doing anything.
                    found = await self._reconcile(key, job, row)
                    if found is None:
                        job["state"] = "UNCERTAIN"
                        await self._save_jobs(
                            acquisition_id, jobs, status="ATTENTION", error_code="SUBMIT_UNCERTAIN",
                            error="JARVIS can't confirm whether Databento received a batch job "
                            f"for {job['symbol']} {job['schema']} {job['start']} → {job['end']}. "
                            "It won't submit again on its own — check your Databento portal "
                            "(Batch jobs), then prepare a new acquisition if nothing is there.",
                        )  # fmt: skip
                        await self._emit(
                            "Acquisition needs your attention (submission uncertain)",
                            {"acquisition_id": acquisition_id}, True,
                        )  # fmt: skip
                        return
                    job["provider_job"], job["state"] = found, "SUBMITTED"
                    await self._save_jobs(acquisition_id, jobs)
                else:
                    self._require_online()
                    job["state"] = "SUBMITTING"
                    await self._save_jobs(acquisition_id, jobs, status="SUBMITTING")
                    try:
                        details = await self._adapter.submit_batch(
                            key,
                            dataset=job["dataset"],
                            schema=job["schema"],
                            stype_in=job["stype_in"],
                            symbols=[job["symbol"]],
                            start=date.fromisoformat(job["start"]),
                            end=date.fromisoformat(job["end"]),
                        )
                    except ProviderError as exc:
                        if exc.code in ("NETWORK", "PROVIDER_ERROR", "RATE_LIMITED"):
                            # Did the request reach Databento? Unknown — never guess.
                            await self._save_jobs(
                                acquisition_id, jobs, status="ATTENTION",
                                error_code="SUBMIT_UNCERTAIN",
                                error=f"The submission for {job['symbol']} {job['schema']} "
                                f"failed with {exc.code}. JARVIS checks your batch jobs before "
                                "anything else happens.",
                            )  # fmt: skip
                            return
                        job["state"] = "REJECTED"
                        await self._save_jobs(acquisition_id, jobs)
                        raise VaultError(exc.code, exc.message) from None
                    job["provider_job"] = str(details.get("id"))
                    job["submitted"] = {
                        k: details.get(k) for k in ("id", "cost_usd", "state", "ts_received")
                    }
                    job["state"] = "SUBMITTED"
                    await self._save_jobs(acquisition_id, jobs, status="SUBMITTED")
                    await self._emit(
                        f"Batch job {job['provider_job']} submitted "
                        f"({job['symbol']} {job['schema']})",
                        {"acquisition_id": acquisition_id},
                    )
            await self._finish_job(key, acquisition_id, row, jobs, job)
        await self._save_jobs(acquisition_id, jobs, status="COMPLETED", error_code=None, error=None)
        await self._emit(
            f"Acquisition complete — {len(jobs)} batch job(s) stored locally; backtests now run "
            "offline on this data",
            {"acquisition_id": acquisition_id, "status": "COMPLETED"},
        )

    async def _reconcile(self, key: str, job: dict[str, Any], row: dict[str, Any]) -> str | None:
        try:
            listed = await self._adapter.batch_jobs(key, since=row.get("approved_at"))
        except ProviderError:
            return None
        for item in listed:
            symbols = item.get("symbols")
            names = symbols if isinstance(symbols, list) else str(symbols or "").split(",")
            if (
                item.get("dataset") == job["dataset"]
                and item.get("schema") == job["schema"]
                and [str(s).strip() for s in names] == [job["symbol"]]
                and str(item.get("start", ""))[:10] == job["start"]
                and str(item.get("end", ""))[:10] == job["end"]
            ):
                return str(item.get("id"))
        return None

    async def _finish_job(
        self,
        key: str,
        acquisition_id: str,
        row: dict[str, Any],
        jobs: list[dict[str, Any]],
        job: dict[str, Any],
    ) -> None:
        provider_job = str(job["provider_job"])
        while True:
            try:
                listed = await self._adapter.batch_jobs(key, since=row.get("approved_at"))
            except ProviderError as exc:
                if exc.code in ("NETWORK", "PROVIDER_ERROR", "RATE_LIMITED"):
                    await asyncio.sleep(self.retry_seconds)
                    continue
                raise VaultError(exc.code, exc.message) from None
            state = next(
                (str(j.get("state")) for j in listed if str(j.get("id")) == provider_job), None
            )
            if state == "done":
                break
            if state == "expired":
                raise VaultError(
                    "JOB_EXPIRED",
                    f"Batch job {provider_job} expired before its files were downloaded.",
                )
            job["state"] = "PROCESSING"
            await self._save_jobs(acquisition_id, jobs, status="SUBMITTED")
            await asyncio.sleep(self.poll_seconds)
        try:
            files = await self._adapter.batch_files(key, provider_job)
        except ProviderError as exc:
            raise VaultError(exc.code, exc.message) from None
        staging = self.root / "staging-acquisitions" / acquisition_id
        staging.mkdir(parents=True, exist_ok=True)
        stored: list[dict[str, Any]] = list(job.get("files") or [])
        known = {f["filename"] for f in stored}
        job["state"] = "DOWNLOADING"
        await self._save_jobs(acquisition_id, jobs, status="DOWNLOADING")
        for entry in files:
            name = str(entry["filename"])
            if name in known:
                continue
            final = await self._download(key, provider_job, entry, staging, job)
            stored.append(final)
            job["files"] = stored
            await self._save_jobs(acquisition_id, jobs, status="DOWNLOADING")
        job["state"] = "INGESTING"
        await self._save_jobs(acquisition_id, jobs, status="INGESTING")
        await self._ingest(job, stored, row["quote"].get("conditions") or {})
        job["state"] = "INGESTED"
        await self._save_jobs(acquisition_id, jobs)
        shutil.rmtree(staging / provider_job, ignore_errors=True)

    async def _download(
        self,
        key: str,
        provider_job: str,
        entry: dict[str, Any],
        staging: Path,
        job: dict[str, Any],
    ) -> dict[str, Any]:
        name = str(entry["filename"])
        algo, _, expected = str(entry.get("hash", "")).partition(":")
        path = staging / provider_job / name
        for attempt in range(4):
            try:
                await self._adapter.batch_download(key, provider_job, name, staging)
            except ProviderError as exc:
                if exc.code in ("NETWORK", "PROVIDER_ERROR", "RATE_LIMITED") and attempt < 3:
                    await asyncio.sleep(self.retry_seconds)
                    continue  # the partial file stays: the next attempt resumes it
                raise VaultError(exc.code, exc.message) from None
            if not path.exists():
                raise VaultError("DOWNLOAD_MISSING", f"{name} wasn't written.")
            actual = await asyncio.to_thread(cache.sha256_file, path)
            if algo != "sha256" or actual == expected:
                break
            path.unlink()  # corrupt: start this file again from zero
            if attempt == 3:
                raise VaultError("CHECKSUM", f"{name} failed its SHA-256 check four times.")
        else:  # pragma: no cover - loop always breaks or raises
            raise VaultError("DOWNLOAD_FAILED", f"{name} couldn't be downloaded.")
        parts = (
            PROVIDER,
            cache.safe(job["dataset"]),
            cache.safe(job["schema"]),
            cache.safe(job["stype_in"]),
            cache.safe(job["symbol"]),
        )
        rel = Path("raw", *parts, "batch", provider_job, cache.safe(name))
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, target)
        cache.freeze(target)
        return {
            "filename": name,
            "path": str(rel),
            "sha256": actual,
            "provider_hash": entry.get("hash"),
            "bytes": int(entry.get("size") or target.stat().st_size),
            "verified": algo == "sha256",
            "downloaded_at": now(),
        }

    async def _ingest(
        self, job: dict[str, Any], files: list[dict[str, Any]], conditions: dict[str, str]
    ) -> None:
        first, last = date.fromisoformat(job["start"]), date.fromisoformat(job["end"])
        key = cache.cache_key(
            PROVIDER, job["dataset"], job["schema"], job["stype_in"], job["symbol"]
        )
        held = {
            r["day"]
            for r in await self._hub_store.cached_days(key, first.isoformat(), last.isoformat())
        }
        by_day: dict[date, tuple[pa.Table, dict[str, Any]]] = {}
        mappings: dict[str, list[dict[str, Any]]] = {}
        for entry in files:
            decoded = await asyncio.to_thread(
                cache.decode, self.root / entry["path"], job["schema"]
            )
            for symbol, intervals in decoded.mappings.items():
                mappings.setdefault(symbol, []).extend(intervals)
            for day, table in decoded.by_day.items():
                by_day[day] = (table, entry)
        parts = (
            PROVIDER,
            cache.safe(job["dataset"]),
            cache.safe(job["schema"]),
            cache.safe(job["stype_in"]),
            cache.safe(job["symbol"]),
        )
        rows = []
        for day in cache.days(first, last):
            if day.isoformat() in held:
                continue  # already in the vault: kept, never replaced
            got = by_day.get(day)
            canonical_rel: str | None = None
            sha: str | None = None
            records = 0
            entry = got[1] if got else files[0]
            if got is not None and got[0].num_rows:
                rel = Path("canonical", *parts, f"{day.isoformat()}.parquet")
                sha = await asyncio.to_thread(cache.write_parquet, self.root / rel, got[0])
                canonical_rel, records = str(rel), got[0].num_rows
            ids = cache.ids_on(mappings, job["symbol"], day)
            if not ids and got is not None:
                ids = sorted({int(x) for x in got[0].column("instrument_id").to_pylist()})
            rows.append(
                {
                    "cache_key": key,
                    "day": day.isoformat(),
                    "provider": PROVIDER,
                    "dataset": job["dataset"],
                    "schema": job["schema"],
                    "stype_in": job["stype_in"],
                    "symbol": job["symbol"],
                    "records": records,
                    "canonical_path": canonical_rel,
                    "sha256": sha,
                    "raw_path": entry["path"],
                    "raw_sha256": entry["sha256"],
                    "condition": conditions.get(day.isoformat(), "available"),
                    "instrument_ids": ids,
                    "job_id": str(job["provider_job"]),
                    "transform_version": cache.TRANSFORM_VERSION,
                    "fixture": self._hub.fixture,
                }
            )
        if rows:
            await self._hub_store.add_cache_days(rows)


def _parse(text: str) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(text)
