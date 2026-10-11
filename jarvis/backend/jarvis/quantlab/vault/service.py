"""The QuantLab Data Vault: local historical data as a first-class, permanent asset.

Everything here works without a network and without any API key:

* **Catalog and coverage** — every locally held series (Databento downloads, Databento DBN
  files you supplied, imported CSV/Parquet) with its exact days, holes against the exchange
  calendar, contract mapping and integrity state. Coverage is ``[start, end)`` in UTC days;
  a range is never taken as proof of complete bars — holes are listed per trading day.
* **Readiness** — for a product × timeframe (× strategy): ``READY``, ``PARTIAL``,
  ``WRONG_GRANULARITY``, ``MISSING_MAPPINGS``, ``INVALID``, ``NOT_AVAILABLE`` or
  ``INCOMPATIBLE``. Only the exact product counts: no ES for NQ, no index for futures.
* **Imports** — staged (preview, guided mapping) then committed: the original file is kept
  immutable under ``raw/imported``, its bars go into the same per-day cache as downloads.
* **Derived timeframes** — N-minute bars built from the immutable snapshot, cached with a
  content hash, verified on every use and rebuilt locally when deleted or damaged.
* **Integrity audit and repair**, **backup / restore** with checksums, storage usage.
* **Offline mode** — a hard switch: the Databento adapter refuses every request.

The backtest path (``lab.py``) reads only from here; acquisition (``acquire.py``) is the
one place that may spend money, behind its own approval gate.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.quantlab.futures import sessions as cal
from jarvis.quantlab.futures import timeframes as tfs
from jarvis.quantlab.futures.resample import AGGREGATOR, resample, table_sha256
from jarvis.quantlab.futures.sessions import Window
from jarvis.quantlab.futures.spec import FuturesSpec, clock
from jarvis.quantlab.hub import cache
from jarvis.quantlab.hub.provider import DatabentoAdapter
from jarvis.quantlab.hub.service import PROVIDER, DataHubService, HubError
from jarvis.quantlab.hub.store import HubStore, now
from jarvis.quantlab.spec import sha256_text
from jarvis.quantlab.vault import importer
from jarvis.quantlab.vault.instruments import markets
from jarvis.quantlab.vault.store import VaultStore
from jarvis.util import new_id

log = logging.getLogger("jarvis.quantlab.vault")

MAX_UPLOAD = 20 * 1024**3
SOURCE_LABEL = {
    PROVIDER: "Databento download",
    "databento-file": "Databento DBN file (supplied by you)",
    "import": "Imported file (yours — not exchange-verified)",
}
PREFERENCE = {PROVIDER: 0, "databento-file": 1, "import": 2}
READINESS = {
    "READY": "Ready locally",
    "PARTIAL": "Partial coverage",
    "WRONG_GRANULARITY": "Wrong granularity",
    "MISSING_MAPPINGS": "Missing contract mappings",
    "INVALID": "Invalid data",
    "NOT_AVAILABLE": "Not available",
    "INCOMPATIBLE": "Not compatible with this strategy",
}
OFFLINE_REASON = "Offline mode is on — no market-data request leaves this computer."
IMPORT_LICENSE = (
    "User-supplied file — private, local research use; provenance as you declared it at "
    "import. Not exchange-verified."
)
DBN_FILE_LICENSE = (
    "Databento data you supplied as a DBN file — licensed to your Databento account; local "
    "research use only, no redistribution."
)


class VaultError(Exception):
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


def root_of(symbol: str) -> str | None:
    return importer._root_of(symbol)


def _rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def _unfreeze(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, 0o644)


def _expand(text: str) -> Path:
    return Path(os.path.expanduser(str(text)))


def _file_size(path: Path) -> int | None:
    return path.stat().st_size if path.is_absolute() and path.is_file() else None


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _profile_sha(windows: list[Window], source: int) -> str:
    digest = hashlib.sha256(f"src={source}".encode())
    for w in windows:
        digest.update(f"{w.label.isoformat()}:{w.start_ns}:{w.end_ns};".encode())
    return digest.hexdigest()


def _windows_from(rows: list[list[Any]]) -> list[Window]:
    """Bucket anchors stored with a derived dataset (only start and end matter for them)."""
    if not rows:
        raise ValueError("no stored windows for this derived dataset")
    return [
        Window(
            label=date.fromisoformat(str(label)),
            start_ns=int(start),
            end_ns=int(end),
            flatten_ns=int(end),
            entry_cutoff_ns=int(end),
            session_close_ns=int(end),
            early_close=False,
        )
        for label, start, end in rows
    ]


class VaultService:
    def __init__(
        self,
        *,
        store: VaultStore,
        hub: DataHubService,
        hub_store: HubStore,
        bus: EventBus,
        adapter: DatabentoAdapter,
    ) -> None:
        self._store = store
        self._hub = hub
        self._hub_store = hub_store
        self._bus = bus
        self._adapter = adapter
        self.root = hub.root
        self._offline = False
        self._lock = asyncio.Lock()

    # Lifecycle ----------------------------------------------------------------------------

    async def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._offline = bool(await self._store.setting("offline", False))
        self._adapter.guard = self.block_reason
        shutil.rmtree(self.root / "staging-imports", ignore_errors=True)
        for row in await self._store.imports(500):
            if row["status"] == "STAGED":
                await self._store.update_import(
                    row["id"], status="EXPIRED", error="JARVIS restarted before the import was "
                    "confirmed — upload the file again.",
                )  # fmt: skip

    async def _emit(self, message: str, payload: dict[str, Any], warn: bool = False) -> None:
        await self._bus.emit(
            EventType.QUANTLAB_VAULT,
            message=message,
            source="quantlab",
            severity=Severity.WARNING if warn else Severity.INFO,
            payload=payload,
        )

    # Offline mode -------------------------------------------------------------------------

    def block_reason(self) -> str | None:
        return OFFLINE_REASON if self._offline else None

    @property
    def offline(self) -> bool:
        return self._offline

    async def set_offline(self, enabled: bool) -> dict[str, Any]:
        self._offline = bool(enabled)
        await self._store.save_setting("offline", self._offline)
        await self._emit(
            "Offline mode on — market-data requests are blocked"
            if self._offline
            else "Offline mode off — quotes and approved acquisitions may use the network",
            {"offline": self._offline},
        )
        return {"offline": self._offline}

    # Imports ------------------------------------------------------------------------------

    async def stage_upload(self, filename: str, body: AsyncIterator[bytes]) -> dict[str, Any]:
        import_id = "imp-" + new_id()[:16]
        folder = self.root / "staging-imports" / import_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / cache.safe(filename or "upload")
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("wb") as handle:
                async for chunk in body:
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise VaultError(
                            "TOO_LARGE",
                            "The file is larger than 20 GB.",
                            "Import it by path, or split it by year.",
                            status=413,
                        )
                    digest.update(chunk)
                    handle.write(chunk)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        if size == 0:
            shutil.rmtree(folder, ignore_errors=True)
            raise VaultError("EMPTY", "The upload was empty.")
        return await self._stage(import_id, path, filename, digest.hexdigest(), size)

    async def stage_path(self, text: str) -> dict[str, Any]:
        source = _expand(text)
        size_now = await asyncio.to_thread(_file_size, source)
        if size_now is None:
            raise VaultError("NOT_FOUND", f"No file at {text}.", status=404)
        if size_now > MAX_UPLOAD:
            raise VaultError("TOO_LARGE", "The file is larger than 20 GB.", status=413)
        import_id = "imp-" + new_id()[:16]
        folder = self.root / "staging-imports" / import_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / cache.safe(source.name)

        def copy() -> tuple[str, int]:
            digest = hashlib.sha256()
            size = 0
            with source.open("rb") as src, path.open("wb") as dst:
                for block in iter(lambda: src.read(1 << 20), b""):
                    digest.update(block)
                    dst.write(block)
                    size += len(block)
            return digest.hexdigest(), size

        try:
            sha, size = await asyncio.to_thread(copy)
        except OSError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise VaultError("READ_FAILED", f"Couldn't read the file: {exc}") from None
        return await self._stage(import_id, path, source.name, sha, size)

    async def _stage(
        self, import_id: str, path: Path, filename: str, sha: str, size: int
    ) -> dict[str, Any]:
        duplicate = await self._store.import_by_sha(sha)
        if duplicate is not None:
            shutil.rmtree(path.parent, ignore_errors=True)
            return {**duplicate, "duplicate": True}
        try:
            preview = await asyncio.to_thread(importer.preview, path)
        except importer.ImportError_ as exc:
            shutil.rmtree(path.parent, ignore_errors=True)
            raise VaultError(exc.code, exc.message) from None
        except Exception as exc:
            shutil.rmtree(path.parent, ignore_errors=True)
            raise VaultError("UNREADABLE", f"The file couldn't be read: {exc}") from None
        record = {
            "id": import_id,
            "status": "STAGED",
            "format": preview["format"],
            "filename": filename,
            "staged_path": _rel(path, self.root),
            "raw_sha256": sha,
            "bytes": size,
            "preview": preview,
        }
        await self._store.add_import(record)
        stored = await self._store.import_(import_id)
        assert stored is not None
        return {**stored, "duplicate": False}

    async def commit_import(self, import_id: str, mapping: dict[str, Any]) -> dict[str, Any]:
        record = await self._store.import_(import_id)
        if record is None:
            raise VaultError("NOT_FOUND", "No such import.", status=404)
        if record["status"] != "STAGED":
            raise VaultError("NOT_STAGED", f"The import is {record['status'].lower()}.", status=409)
        path = self.root / str(record["staged_path"])
        try:
            parsed = await asyncio.to_thread(importer.parse, path, mapping)
        except importer.ImportError_ as exc:
            findings = [*exc.findings, importer.finding("BLOCK", exc.code, exc.message)]
            await self._store.update_import(
                import_id, status="REJECTED", findings=findings, error=exc.message, mapping=mapping
            )
            raise VaultError(exc.code, exc.message, findings=findings) from None
        async with self._lock:
            raw_rel = Path("raw", "imported", import_id, cache.safe(record["filename"]))
            raw_abs = self.root / raw_rel
            raw_abs.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, raw_abs)
            cache.freeze(raw_abs)
            shutil.rmtree(path.parent, ignore_errors=True)
            provider = "databento-file" if parsed.format == "dbn" else "import"
            added, kept = await self._ingest(
                parsed, provider, raw_rel, record["raw_sha256"], import_id
            )
        ts = parsed.table.column("ts_event").to_numpy() if parsed.table.num_rows else None
        findings = list(parsed.findings)
        if kept:
            findings.append(
                importer.finding(
                    "INFO",
                    "DAYS_KEPT",
                    f"{kept} day(s) were already in the vault for this series — the existing "
                    "data was kept (nothing is overwritten).",
                    kept,
                )
            )
        await self._store.update_import(
            import_id,
            status="COMMITTED",
            staged_path=None,
            raw_path=str(raw_rel),
            mapping={**mapping, "contracts": {str(k): v for k, v in parsed.contracts.items()},
                     "mapped": parsed.mapped},
            provider=provider,
            dataset=parsed.dataset,
            schema=parsed.schema,
            stype_in=parsed.stype_in,
            symbol=parsed.symbol,
            root=parsed.root,
            source_minutes=parsed.source_minutes,
            rows_in=parsed.rows_in,
            rows_ok=parsed.table.num_rows,
            rows_rejected=parsed.rows_rejected,
            first_ts=int(ts.min()) if ts is not None and ts.size else None,
            last_ts=int(ts.max()) if ts is not None and ts.size else None,
            days_added=added,
            days_kept=kept,
            findings=findings,
            authoritative=False,
            committed_at=now(),
        )  # fmt: skip
        await self._emit(
            f"Imported {record['filename']}: {parsed.table.num_rows:,} bars of {parsed.symbol} "
            f"({added} new day(s))",
            {"import_id": import_id},
        )
        stored = await self._store.import_(import_id)
        assert stored is not None
        return stored

    async def _ingest(
        self,
        parsed: importer.Parsed,
        provider: str,
        raw_rel: Path,
        raw_sha: str,
        job_id: str,
    ) -> tuple[int, int]:
        table = parsed.table
        if not table.num_rows:
            return 0, 0
        stamp = "ts_recv" if parsed.schema == "definition" else "ts_event"
        stamps = table.column(stamp).to_numpy()
        day_of = (stamps // cache.DAY_NS).astype("int64")
        epoch = date(1970, 1, 1)
        groups: dict[date, list[int]] = {}
        for i, d in enumerate(day_of.tolist()):
            groups.setdefault(epoch + timedelta(days=int(d)), []).append(i)
        first, last = min(groups), max(groups) + timedelta(days=1)
        parts = (
            provider,
            cache.safe(parsed.dataset),
            cache.safe(parsed.schema),
            cache.safe(parsed.stype_in),
            cache.safe(parsed.symbol),
        )
        key = cache.cache_key(
            provider, parsed.dataset, parsed.schema, parsed.stype_in, parsed.symbol
        )
        held = {
            r["day"]
            for r in await self._hub_store.cached_days(key, first.isoformat(), last.isoformat())
        }
        trading = {s.label for s in cal.sessions("XNYS", first, last)}
        rows, added, kept = [], 0, 0
        for day in cache.days(first, last):
            if day.isoformat() in held:
                kept += 1
                continue
            idx = groups.get(day)
            canonical_rel: str | None = None
            sha: str | None = None
            records = 0
            ids: list[int] = []
            if idx:
                part = table.take(pa.array(idx))
                rel = Path("canonical", *parts, f"{day.isoformat()}.parquet")
                sha = await asyncio.to_thread(cache.write_parquet, self.root / rel, part)
                canonical_rel, records = str(rel), part.num_rows
                ids = sorted({int(x) for x in part.column("instrument_id").to_pylist()})
            if parsed.mappings:
                ids = cache.ids_on(parsed.mappings, parsed.symbol, day) or ids
            condition = "available"
            if not records and parsed.schema != "definition" and day in trading:
                condition = "missing"  # a trading day inside the file's range without bars
            rows.append(
                {
                    "cache_key": key,
                    "day": day.isoformat(),
                    "provider": provider,
                    "dataset": parsed.dataset,
                    "schema": parsed.schema,
                    "stype_in": parsed.stype_in,
                    "symbol": parsed.symbol,
                    "records": records,
                    "canonical_path": canonical_rel,
                    "sha256": sha,
                    "raw_path": str(raw_rel),
                    "raw_sha256": raw_sha,
                    "condition": condition,
                    "instrument_ids": ids,
                    "job_id": job_id,
                    "transform_version": cache.TRANSFORM_VERSION,
                    "fixture": False,
                }
            )
            added += 1
        if rows:
            await self._hub_store.add_cache_days(rows)
        return added, kept

    async def imports(self) -> list[dict[str, Any]]:
        return await self._store.imports()

    # Catalog and coverage ----------------------------------------------------------------

    async def series(self) -> list[dict[str, Any]]:
        rows = await self._hub_store.cache_rows_all()
        by_key: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_key.setdefault(row["cache_key"], []).append(row)
        imports = [i for i in await self._store.imports(1000) if i["status"] == "COMMITTED"]
        issues = await self._store.open_issues()
        out = []
        for key, days in by_key.items():
            head = days[0]
            if head["schema"] == "definition":
                continue
            out.append(self._describe(key, days, imports, issues))
        out.sort(key=lambda s: (s["root"] or "~", PREFERENCE.get(s["provider"], 9), s["symbol"]))
        return out

    def _describe(
        self,
        key: str,
        days: list[dict[str, Any]],
        imports: list[dict[str, Any]],
        issues: list[dict[str, Any]],
    ) -> dict[str, Any]:
        head = days[0]
        first = date.fromisoformat(days[0]["day"])
        last = date.fromisoformat(days[-1]["day"]) + timedelta(days=1)
        with_bars = {d["day"] for d in days if d["records"]}
        sessions = [s.label for s in cal.sessions("XNYS", first, last)]
        holes = [s for s in sessions if s.isoformat() not in with_bars]
        segments: list[dict[str, Any]] = []
        for s in sessions:
            present = s.isoformat() in with_bars
            if present and segments and segments[-1]["open"]:
                segments[-1]["end"] = s + timedelta(days=1)
                segments[-1]["sessions"] += 1
            elif present:
                segments.append(
                    {"start": s, "end": s + timedelta(days=1), "sessions": 1, "open": True}
                )
            elif segments:
                segments[-1]["open"] = False
        hole_ranges = [
            {"start": a.isoformat(), "end": b.isoformat()}
            for a, b in cache.ranges(holes)
        ]  # fmt: skip
        mine = [
            i for i in imports
            if cache.cache_key(i["provider"] or "", i["dataset"] or "", i["schema"] or "",
                               i["stype_in"] or "", i["symbol"] or "") == key
        ]  # fmt: skip
        mapped = head["provider"] == PROVIDER or all(
            bool((i.get("mapping") or {}).get("mapped", True)) for i in mine
        )
        if head["stype_in"] == "parent":
            mapped = False
        multi = sum(1 for d in days if len(d["instrument_ids"] or []) > 1)
        corrupt = [i for i in issues if i["subject"] == key and i["kind"].startswith("CORRUPT")]
        flagged = sum(1 for d in days if d["condition"] not in (None, "available", "missing"))
        source_minutes = 1
        if head["schema"].startswith("ohlcv-"):
            unit = head["schema"].removeprefix("ohlcv-")
            source_minutes = int(unit[:-1]) * (60 if unit.endswith("h") else 1)
        grade = "A"
        if flagged:
            grade = "B"
        if holes:
            grade = "C"
        if corrupt or not mapped:
            grade = "F"
        fixture = any(bool(d["fixture"]) for d in days)
        return {
            "key": key,
            "provider": head["provider"],
            "source": "Offline fixture (SYNTHETIC — not market data)"
            if fixture
            else SOURCE_LABEL.get(head["provider"], head["provider"]),
            "fixture": fixture,
            "authoritative": head["provider"] == PROVIDER and not fixture,
            "dataset": head["dataset"],
            "schema": head["schema"],
            "stype_in": head["stype_in"],
            "symbol": head["symbol"],
            "root": root_of(head["symbol"]),
            "source_minutes": source_minutes,
            "start": first.isoformat(),
            "end": last.isoformat(),
            "days": len(days),
            "sessions": len(sessions),
            "sessions_with_bars": len(sessions) - len(holes),
            "holes": hole_ranges,
            "segments": [
                {
                    "start": g["start"].isoformat(),
                    "end": g["end"].isoformat(),
                    "sessions": g["sessions"],
                }
                for g in segments
            ],
            "records": sum(int(d["records"]) for d in days),
            "flagged_days": flagged,
            "multi_contract_days": multi,
            "mapped": mapped,
            "corrupt": [i["detail"] for i in corrupt],
            "grade": grade,
            "imports": [i["id"] for i in mine],
            "jobs": sorted({d["job_id"] for d in days}),
            "derived": [],
        }

    async def coverage(
        self,
        symbol: str,
        schema: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        root = root_of(symbol) or symbol
        rows = [s for s in await self.series() if s["root"] == root]
        if schema:
            rows = [s for s in rows if s["schema"] == schema]
        out = []
        for s in rows:
            entry = dict(s)
            if start and end:
                lo, hi = date.fromisoformat(start), date.fromisoformat(end)
                have = date.fromisoformat(s["start"]), date.fromisoformat(s["end"])
                need = [
                    sess.label
                    for sess in cal.sessions("XNYS", lo, hi)
                    if not (have[0] <= sess.label < have[1])
                ]
                entry["requested"] = {"start": start, "end": end}
                entry["missing"] = [
                    {"start": a.isoformat(), "end": b.isoformat()} for a, b in cache.ranges(need)
                ] + [h for h in s["holes"] if lo.isoformat() <= h["start"] < hi.isoformat()]
            out.append(entry)
        return {"symbol": root, "series": out}

    # Readiness -----------------------------------------------------------------------------

    async def readiness(
        self,
        product: str,
        minutes: int,
        spec: FuturesSpec | None = None,
        start: str | None = None,
        end: str | None = None,
        all_series: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        series = all_series if all_series is not None else await self.series()

        def result(status: str, message: str, **extra: Any) -> dict[str, Any]:
            return {
                "status": status,
                "label": READINESS[status],
                "product": product,
                "minutes": minutes,
                "timeframe": tfs.label(minutes),
                "message": message,
                "runnable": status in ("READY", "PARTIAL"),
                **extra,
            }

        mine = [s for s in series if s["root"] == product and s["schema"].startswith("ohlcv")]
        if not mine:
            others = sorted({s["root"] for s in series if s["root"] and s["root"] != product})
            note = (
                f" {', '.join(others)} data is here — it is never used in place of {product}."
                if others
                else ""
            )
            return result(
                "NOT_AVAILABLE",
                f"No {product} history in the vault.{note}",
                missing=[{"symbol": product, "schema": "ohlcv-1m", "start": start, "end": end}],
                action="Open Data Vault to import a file or acquire it once.",
            )
        usable = sorted(
            mine,
            key=lambda s: (
                bool(s["corrupt"]),
                not s["mapped"],
                minutes % s["source_minutes"] != 0,
                PREFERENCE.get(s["provider"], 9),
                -s["sessions_with_bars"],
            ),
        )
        chosen = usable[0]
        info = {
            "series": {
                k: chosen[k]
                for k in ("key", "source", "provider", "symbol", "schema", "start", "end",
                          "source_minutes", "fixture", "grade", "authoritative")
            }
        }  # fmt: skip
        if chosen["corrupt"]:
            return result(
                "INVALID",
                f"The local {chosen['symbol']} data failed its integrity check — it won't be "
                "used until it is repaired or re-imported.",
                problems=chosen["corrupt"],
                action="Open Data Vault → Audit / Repair.",
                **info,
            )
        if minutes % chosen["source_minutes"]:
            return result(
                "WRONG_GRANULARITY",
                f"The local {product} data is {tfs.label(chosen['source_minutes'])} bars; "
                f"{tfs.label(minutes)} would need finer data, which is never invented.",
                action=f"Import or acquire {product} ohlcv-1m data for {tfs.label(minutes)}.",
                **info,
            )
        if not chosen["mapped"]:
            return result(
                "MISSING_MAPPINGS",
                "The data doesn't say which contract month each bar belongs to, so rolls "
                "can't be handled.",
                action="Re-import with a contract column, or acquire a continuous series.",
                **info,
            )
        lo = date.fromisoformat(start) if start else date.fromisoformat(chosen["start"])
        hi = date.fromisoformat(end) if end else date.fromisoformat(chosen["end"])
        if hi <= lo:
            return result("PARTIAL", "The end date must be after the start date.", **info)
        have_lo, have_hi = date.fromisoformat(chosen["start"]), date.fromisoformat(chosen["end"])
        outside = [
            s.label for s in cal.sessions("XNYS", lo, hi) if not (have_lo <= s.label < have_hi)
        ]
        holes = [h for h in chosen["holes"] if lo.isoformat() <= h["start"] < hi.isoformat()]
        compat = None
        if spec is not None:
            effective, _ = tfs.effective_spec(
                spec, product=product, symbol=f"{product}.local", minutes=minutes
            )
            compat = tfs.compatibility(effective, minutes, chosen["source_minutes"])
        extra = {
            **info,
            "range": {"start": lo.isoformat(), "end": hi.isoformat()},
            "compatibility": compat,
        }
        if outside:
            return result(
                "PARTIAL",
                f"{len(outside)} trading day(s) in the chosen range aren't in the vault.",
                missing=[
                    {
                        "symbol": product,
                        "schema": "ohlcv-1m",
                        "start": a.isoformat(),
                        "end": b.isoformat(),
                    }
                    for a, b in cache.ranges(outside)
                ],
                runnable=False,
                action="Shorten the range, or open Data Vault to acquire exactly the missing days.",
                **extra,
            )
        if spec is not None:
            from jarvis.quantlab.futures import validation

            s = spec.session
            windows = cal.windows(
                s.calendar, s.timezone, clock(s.start), clock(s.end), clock(s.flatten_at),
                clock(s.entry_cutoff) if s.entry_cutoff else None, lo, hi, tuple(s.weekdays),
            )  # fmt: skip
            have = {
                w.label for w in windows if not any(
                    h["start"] <= w.label.isoformat() < h["end"] for h in holes
                )
            }  # fmt: skip
            with_data = [w for w in windows if w.label in have]
            split = validation.split(with_data, spec) if with_data else None
            insample = len(split.insample) if split else 0
            if insample < validation.MIN_IS_SESSIONS:
                return {
                    **result(
                        "PARTIAL",
                        f"Only {len(with_data)} session(s) with data in this range — this "
                        f"strategy's split leaves {insample} in-sample, at least "
                        f"{validation.MIN_IS_SESSIONS} are needed.",
                        action="Import or acquire a longer history (Data Vault).",
                        **extra,
                    ),
                    "runnable": False,
                }
        if compat is not None and not compat["compatible"]:
            return {
                **result(
                    "INCOMPATIBLE",
                    " ".join(i["message"] for i in compat["issues"]),
                    **extra,
                ),
                "runnable": False,
            }
        if holes:
            days = sum(
                (date.fromisoformat(h["end"]) - date.fromisoformat(h["start"])).days for h in holes
            )
            return result(
                "PARTIAL",
                f"Runs on the local data; {len(holes)} gap(s) (≈{days} day(s) without bars) are "
                "skipped and listed in the report.",
                holes=holes,
                **extra,
            )
        return result(
            "READY", f"{chosen['symbol']} {chosen['start']} → {chosen['end']} is local.", **extra
        )

    # Derived timeframes ---------------------------------------------------------------------

    async def derived_bars(
        self,
        record: dict[str, Any],
        table: pa.Table,
        windows: list[Window],
        minutes: int,
        source: int,
    ) -> tuple[pa.Table, dict[str, Any]]:
        """N-minute bars for a run: from the cache when intact, else rebuilt locally."""
        profile = _profile_sha(windows, source)
        existing = await self._store.derived(record["id"], minutes, profile, AGGREGATOR)
        rebuilt_from: str | None = None
        if existing is not None:
            path = self.root / existing["path"]
            if path.exists():
                actual = await asyncio.to_thread(cache.sha256_file, path)
                if actual == existing["file_sha256"]:
                    bars = await asyncio.to_thread(pq.read_table, path)
                    return bars, {
                        **{k: v for k, v in existing["stats"].items() if k != "windows"},
                        "sha256": existing["data_sha256"],
                        "cached": True,
                        "path": existing["path"],
                    }
                rebuilt_from = "damaged"
                await self._store.add_issue(
                    "DERIVED_DAMAGED", existing["path"], {"path": existing["path"]}
                )
                _unfreeze(path)
                path.unlink()
            else:
                rebuilt_from = "missing"
        out = await asyncio.to_thread(resample, table, windows, minutes, source_minutes=source)
        data_sha = await asyncio.to_thread(table_sha256, out.table)
        if existing is not None and data_sha != existing["data_sha256"]:
            await self._store.add_issue(
                "DERIVED_MISMATCH",
                existing["path"],
                {"expected": existing["data_sha256"], "got": data_sha},
            )
        folder = Path("derived", record["id"], tfs.label(minutes), profile[:16], AGGREGATOR)
        bars_rel = folder / "bars.parquet"
        file_sha = await asyncio.to_thread(cache.write_parquet, self.root / bars_rel, out.table)
        series = cache.cache_key(
            record["provider"], record["dataset"], record["schema"], record["stype_in"],
            record["symbol"],
        )  # fmt: skip
        stats = {
            **out.stats,
            "profile_sha": profile,
            "series": series,
            # The exact bucket anchors, so a rebuild needs nothing but the snapshot.
            "windows": [[w.label.isoformat(), w.start_ns, w.end_ns] for w in windows],
        }
        await self._store.save_derived(
            {
                "id": f"dv-{sha256_text(record['id'] + str(minutes) + profile + AGGREGATOR)[:16]}",
                "dataset_id": record["id"],
                "minutes": minutes,
                "profile_sha": profile,
                "aggregator": AGGREGATOR,
                "path": str(bars_rel),
                "file_sha256": file_sha,
                "data_sha256": data_sha,
                "bars": out.table.num_rows,
                "stats": stats,
            }
        )
        if rebuilt_from:
            await self._store.resolve_issues("DERIVED_DAMAGED", str(bars_rel))
        public = {k: v for k, v in stats.items() if k != "windows"}
        return out.table, {
            **public,
            "sha256": data_sha,
            "cached": False,
            "rebuilt": rebuilt_from,
            "path": str(bars_rel),
        }

    async def rebuild_derived(self, dataset_id: str | None = None) -> dict[str, Any]:
        """Delete and regenerate cached timeframes from the immutable snapshots (offline)."""
        rebuilt, failed = [], []
        for row in await self._store.derived_all():
            if dataset_id and row["dataset_id"] != dataset_id:
                continue
            try:
                windows = _windows_from(row["stats"].get("windows") or [])
                record, table, _ = await self._hub.load(row["dataset_id"])
            except (OSError, HubError, ValueError) as exc:
                failed.append({"path": row["path"], "error": str(getattr(exc, "message", exc))})
                continue
            bars_path = self.root / row["path"]
            if bars_path.exists():
                _unfreeze(bars_path)
                bars_path.unlink()
            source = int(row["stats"].get("source_minutes", 1))
            _, info = await self.derived_bars(record, table, windows, int(row["minutes"]), source)
            rebuilt.append(
                {
                    "path": row["path"],
                    "identical": info["sha256"] == row["data_sha256"],
                    "bars": info["bars_out"],
                }
            )
        await self._emit(
            f"Derived timeframes rebuilt locally: {len(rebuilt)} ({len(failed)} failed)",
            {"rebuilt": len(rebuilt), "failed": len(failed)},
            warn=bool(failed),
        )
        return {"rebuilt": rebuilt, "failed": failed}

    # Integrity ---------------------------------------------------------------------------

    async def audit(self, repair: bool = False) -> dict[str, Any]:
        """Re-hash every raw, canonical, snapshot and derived file the catalog knows."""
        checked = 0
        problems: list[dict[str, Any]] = []
        repaired: list[dict[str, Any]] = []
        rows = await self._hub_store.cache_rows_all()
        raw_seen: dict[str, bool] = {}
        by_key: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_key.setdefault(row["cache_key"], []).append(row)
        for key, days in by_key.items():
            bad: list[str] = []
            for row in days:
                raw = row["raw_path"]
                if raw and raw not in raw_seen:
                    raw_ok = await self._file_ok(raw, row["raw_sha256"])
                    raw_seen[raw] = raw_ok
                    checked += 1
                    if not raw_ok:
                        problems.append({"kind": "RAW", "path": raw, "series": key})
                if row["canonical_path"]:
                    checked += 1
                    if not await self._file_ok(row["canonical_path"], row["sha256"]):
                        fixed = False
                        if repair and raw_seen.get(raw):
                            fixed = await self._repair_day(row)
                        if fixed:
                            repaired.append({"kind": "CANONICAL", "path": row["canonical_path"]})
                        else:
                            bad.append(row["day"])
                            problems.append(
                                {"kind": "CANONICAL", "path": row["canonical_path"], "series": key}
                            )
            if bad:
                await self._store.add_issue(
                    "CORRUPT_DAYS",
                    key,
                    {
                        "days": bad[:50],
                        "count": len(bad),
                        "message": f"{len(bad)} day file(s) are missing or changed on disk.",
                    },
                )
            else:
                await self._store.resolve_issues("CORRUPT_DAYS", key)
        for record in await self._hub_store.datasets():
            checked += 1
            if not await self._file_ok(record["snapshot_path"], record["snapshot_sha256"]):
                problems.append(
                    {"kind": "SNAPSHOT", "path": record["snapshot_path"], "dataset": record["id"]}
                )
        for row in await self._store.derived_all():
            checked += 1
            if not await self._file_ok(row["path"], row["file_sha256"]):
                problems.append({"kind": "DERIVED", "path": row["path"], "rebuildable": True})
        for imp in await self._store.imports(1000):
            if imp["status"] == "COMMITTED" and imp["raw_path"]:
                if imp["raw_path"] in raw_seen:
                    continue
                checked += 1
                if not await self._file_ok(imp["raw_path"], imp["raw_sha256"]):
                    problems.append({"kind": "RAW", "path": imp["raw_path"], "import": imp["id"]})
        report = {
            "checked": checked,
            "problems": problems,
            "repaired": repaired,
            "ok": not problems,
            "at": now(),
        }
        await self._store.save_setting("last_audit", report | {"problems": problems[:100]})
        await self._emit(
            f"Integrity audit: {checked} files checked, {len(problems)} problem(s)"
            + (f", {len(repaired)} repaired" if repaired else ""),
            {"ok": not problems},
            warn=bool(problems),
        )
        return report

    async def flag_corrupt(self, key: str, message: str) -> None:
        """A checksum failure found outside the audit (e.g. while building a snapshot)."""
        await self._store.add_issue(
            "CORRUPT_DAYS", key, {"message": message, "count": 1, "days": []}
        )
        await self._emit(f"Integrity problem in {key}: {message}", {"series": key}, warn=True)

    async def _file_ok(self, rel: str, sha: str | None) -> bool:
        path = self.root / rel
        if not path.exists():
            return False
        if not sha:
            return True
        return await asyncio.to_thread(cache.sha256_file, path) == sha

    async def _repair_day(self, row: dict[str, Any]) -> bool:
        """Rebuild one canonical day from its intact raw DBN file (Databento sources only)."""
        if row["provider"] not in (PROVIDER, "databento-file"):
            return False  # CSV/Parquet days are re-imported from their kept original instead
        try:
            decoded = await asyncio.to_thread(
                cache.decode, self.root / row["raw_path"], row["schema"]
            )
        except Exception:
            return False
        part = decoded.by_day.get(date.fromisoformat(row["day"]))
        if part is None or part.num_rows != int(row["records"]):
            return False
        path = self.root / row["canonical_path"]
        if path.exists():
            _unfreeze(path)
            path.unlink()
        sha = await asyncio.to_thread(cache.write_parquet, path, part)
        await self._hub_store.update_cache_file(
            row["cache_key"], row["day"], row["canonical_path"], sha
        )
        return True

    # Backup / restore ----------------------------------------------------------------------

    async def backup(self, location: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise VaultError(
                "CONFIRM_REQUIRED",
                "A backup copies your market data to another folder — confirm the location.",
            )
        target = _expand(location)
        if not target.is_absolute() or not await asyncio.to_thread(target.is_dir):
            raise VaultError("NOT_FOUND", f"{location} isn't an existing folder.", status=404)
        if await asyncio.to_thread(_inside, target, self.root):
            raise VaultError("INSIDE_VAULT", "Choose a folder outside the vault itself.")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        dest = target / "JARVIS-QuantLab-Backup" / stamp
        catalog = {
            "cache": await self._hub_store.cache_rows_all(),
            "datasets": await self._hub_store.datasets(),
            "imports": [i for i in await self._store.imports(1000) if i["status"] == "COMMITTED"],
        }

        def run() -> dict[str, Any]:
            dest.mkdir(parents=True, exist_ok=False)
            entries = []
            total = 0
            for sub in ("raw", "canonical", "snapshots"):
                base = self.root / sub
                if not base.exists():
                    continue
                for path in sorted(p for p in base.rglob("*") if p.is_file()):
                    if path.name.endswith(".partial"):
                        continue
                    rel = path.relative_to(self.root)
                    copy = dest / "files" / rel
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, copy)
                    sha = cache.sha256_file(path)
                    if cache.sha256_file(copy) != sha:
                        raise OSError(f"copy of {rel} doesn't match the original")
                    size = path.stat().st_size
                    total += size
                    entries.append({"path": str(rel), "sha256": sha, "bytes": size})
            (dest / "catalog.json").write_text(json.dumps(catalog, default=str))
            manifest = {
                "format": "jarvis-quantlab-backup-1",
                "created_at": now(),
                "files": entries,
                "catalog_sha256": cache.sha256_file(dest / "catalog.json"),
                "note": "Private copy of your local market data. Licensed data stays under its "
                "provider's terms — don't share this folder.",
            }
            text = json.dumps(manifest, indent=1)
            (dest / "manifest.json").write_text(text)
            return {
                "files": len(entries),
                "bytes": total,
                "manifest_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }

        try:
            info = await asyncio.to_thread(run)
        except OSError as exc:
            raise VaultError("BACKUP_FAILED", f"Backup failed: {exc}") from None
        backup_id = "bk-" + new_id()[:16]
        await self._store.add_backup(
            {
                "id": backup_id,
                "location": str(dest),
                "files": info["files"],
                "bytes": info["bytes"],
                "manifest_sha256": info["manifest_sha256"],
                "status": "VERIFIED",
                "verified_at": now(),
                "detail": {"excluded": "derived timeframes (rebuildable), staging"},
            }
        )
        await self._emit(
            f"Backup verified: {info['files']} files ({info['bytes'] / 1e6:,.1f} MB) → {dest}",
            {"backup_id": backup_id},
        )
        return {"id": backup_id, "location": str(dest), **info, "status": "VERIFIED"}

    async def restore(self, location: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise VaultError(
                "CONFIRM_REQUIRED", "Confirm the restore — nothing intact is overwritten."
            )
        source = _expand(location)
        manifest_path = source / "manifest.json"
        if not await asyncio.to_thread(manifest_path.is_file):
            raise VaultError(
                "NOT_FOUND", "No JARVIS backup (manifest.json) in that folder.", status=404
            )
        manifest = json.loads(manifest_path.read_text())
        catalog = json.loads((source / "catalog.json").read_text())

        def run() -> dict[str, Any]:
            restored, intact, damaged = 0, 0, []
            for entry in manifest["files"]:
                copy = source / "files" / entry["path"]
                if not copy.exists() or cache.sha256_file(copy) != entry["sha256"]:
                    damaged.append(entry["path"])
                    continue
                local = self.root / entry["path"]
                if local.exists() and cache.sha256_file(local) == entry["sha256"]:
                    intact += 1
                    continue
                local.parent.mkdir(parents=True, exist_ok=True)
                if local.exists():
                    _unfreeze(local)
                shutil.copyfile(copy, local)
                cache.freeze(local)
                restored += 1
            return {"restored": restored, "intact": intact, "damaged_in_backup": damaged}

        result = await asyncio.to_thread(run)
        rows_added = await self._hub_store.restore_cache_rows(catalog.get("cache", []))
        datasets_added = 0
        for record in catalog.get("datasets", []):
            if await self._hub_store.restore_dataset(record):
                datasets_added += 1
        imports_added = 0
        for imp in catalog.get("imports", []):
            if await self._store.import_(imp["id"]) is None:
                fields = {k: v for k, v in imp.items() if k not in ("duplicate",)}
                await self._store.add_import(fields)
                imports_added += 1
        await self._emit(
            f"Restore: {result['restored']} file(s) restored, {result['intact']} already intact",
            {"restored": result["restored"]},
            warn=bool(result["damaged_in_backup"]),
        )
        return {
            **result,
            "catalog_rows_added": rows_added,
            "datasets_added": datasets_added,
            "imports_added": imports_added,
        }

    # Storage and overview ------------------------------------------------------------------

    def _usage(self) -> dict[str, Any]:
        parts = {}
        for sub in ("raw", "canonical", "snapshots", "derived", "staging-imports"):
            base = self.root / sub
            size, files = 0, 0
            if base.exists():
                for path in base.rglob("*"):
                    if path.is_file():
                        size += path.stat().st_size
                        files += 1
            parts[sub] = {"bytes": size, "files": files}
        disk = shutil.disk_usage(self.root)
        return {
            "parts": parts,
            "total_bytes": sum(p["bytes"] for p in parts.values()),
            "disk_free_bytes": disk.free,
            "disk_total_bytes": disk.total,
            "location": str(self.root),
        }

    async def overview(self) -> dict[str, Any]:
        series = await self.series()
        derived = await self._store.derived_all()
        for s in series:
            s["derived"] = sorted(
                {tfs.label(d["minutes"]) for d in derived if d["stats"].get("series") == s["key"]}
            )
        backups = await self._store.backups()
        usage = await asyncio.to_thread(self._usage)
        status = await self._hub.status()
        latest = backups[0] if backups else None
        return {
            "offline": self._offline,
            # Market-data provider requests since JARVIS started (quotes, metadata, purchases).
            "provider_requests": self._adapter.calls,
            "series": series,
            "markets": markets(),
            "storage": usage,
            "derived": [
                {
                    "dataset_id": d["dataset_id"],
                    "timeframe": tfs.label(d["minutes"]),
                    "bars": d["bars"],
                    "aggregator": d["aggregator"],
                    "created_at": d["created_at"],
                }
                for d in derived[:50]
            ],
            "imports": (await self._store.imports(20)),
            "issues": await self._store.open_issues(),
            "last_audit": await self._store.setting("last_audit"),
            "backups": backups,
            "backup_warning": None
            if latest and latest["status"] == "VERIFIED"
            else "No verified backup: this computer holds the only copy of your market data.",
            "provider": {
                "databento": {
                    "status": status["status"],
                    "fixture_label": status["fixture_label"],
                    "key_needed_for_backtests": False,
                }
            },
            "alternatives": ALTERNATIVES,
            "license": "Licensed data stays private and local under its provider's terms. Your "
            "imported files keep the provenance you declared; they are never labelled "
            "exchange-authoritative.",
        }


ALTERNATIVES = {
    "criteria": [
        "Exact CME contract identity (instrument ids, roll mapping)",
        "Venue and dataset (GLBX.MDP3 = CME Globex MDP 3.0)",
        "Granularity (tick, 1s, 1m) and history depth",
        "Cost structure (one-time vs subscription, per-byte metering)",
        "License (personal / internal research, storage, redistribution)",
        "Reliability and an official API",
    ],
    "providers": [
        {
            "name": "Databento",
            "status": "Integrated (official SDK)",
            "identity": "Instrument ids, definitions and symbology from the exchange feed",
            "granularity": "Trades / quotes / 1s / 1m / 1h / 1d OHLCV",
            "cost": "Metered per byte of uncompressed data; quotes are free — JARVIS shows "
            "the live quote, never an assumed price",
            "license": "Your Databento account terms (check storage and non-display use)",
        },
        {
            "name": "Your own files (CSV / Parquet / DBN)",
            "status": "Integrated (import)",
            "identity": "Only what the file states (contract column or declared contract)",
            "granularity": "As delivered (≥ 1 minute for CSV/Parquet)",
            "cost": "No new cost",
            "license": "As you obtained them",
        },
        {
            "name": "Other vendors",
            "status": "Not evaluated — no adapter yet",
            "identity": "—",
            "granularity": "—",
            "cost": "Not known to JARVIS (no invented prices)",
            "license": "—",
        },
    ],
    "never": "CFD, spot, cash-index or ETF series are never used in place of CME futures.",
}
