"""QuantLab application service: registry, data import, experiment runs.

Experiments run as background jobs in the backend (one at a time), report
their real stages as ``quantlab.*`` events and can be cancelled. Artifacts are
written into a staging folder and only renamed into place once everything
succeeded, so a failed or cancelled run never looks complete. Identical inputs
(spec, data snapshot, engine, code revision) give the identical experiment id;
asking again returns that experiment instead of a duplicate.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import platform
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from jarvis.events.bus import EventBus
from jarvis.events.types import Event, EventType, Severity
from jarvis.quantlab import engine
from jarvis.quantlab.data import (
    SYNTHETIC_LABEL,
    DataError,
    ImportMeta,
    import_bars,
    read_snapshot,
    snapshot_bytes,
)
from jarvis.quantlab.spec import (
    SpecError,
    canonical_json,
    describe,
    parse_spec,
    sha256_bytes,
    sha256_text,
    spec_warnings,
)
from jarvis.quantlab.store import QuantLabStore
from jarvis.quantlab.validation import MEANINGS, VERDICT_POLICY, evaluate
from jarvis.util import new_id, utcnow

log = logging.getLogger("jarvis.quantlab")

FIXTURES = ("ma_crossover_case.csv", "golden_execution_case.csv", "invalid_ohlc_duplicate.csv")
_ID = re.compile(r"^[a-z]{2,3}_[0-9a-f]{12,64}$")
_DONE = ("completed", "failed", "cancelled")


class QuantLabError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 422,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or []


def _checked_id(value: str) -> str:
    """IDs become folder names: only our own shape is allowed (no ../ traversal)."""
    if not _ID.match(value):
        raise QuantLabError("NOT_FOUND", "Unknown id.", status=404)
    return value


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{new_id()[:8]}.tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class QuantLabService:
    def __init__(
        self,
        *,
        store: QuantLabStore,
        bus: EventBus,
        root: Path,
        fixtures: Path,
        code_revision: str,
    ) -> None:
        self._store = store
        self._bus = bus
        self._root = root
        self._fixtures = fixtures
        self._code_revision = code_revision
        self._lock = asyncio.Lock()  # one simulation at a time
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel: dict[str, threading.Event] = {}

    # Lifecycle --------------------------------------------------------------------------

    async def start(self) -> None:
        # A run that was in flight when the backend stopped didn't finish: say so.
        for exp in await self._store.experiments_with_status("queued", "running"):
            await self._store.set_finished(
                exp["id"],
                "failed",
                error_code="INTERRUPTED",
                error="JARVIS stopped while this run was in progress. Run it again.",
            )
        self._clean_staging()

    async def stop(self) -> None:
        for flag in self._cancel.values():
            flag.set()
        for task in list(self._tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _clean_staging(self) -> None:
        for folder in ("datasets", "experiments"):
            base = self._root / folder
            if base.is_dir():
                for stale in base.glob(".staging-*"):
                    shutil.rmtree(stale, ignore_errors=True)

    async def _emit(
        self,
        type_: EventType,
        message: str,
        entity_id: str,
        payload: dict[str, Any] | None = None,
        severity: Severity = Severity.INFO,
        trace_id: str | None = None,
    ) -> None:
        await self._bus.publish(
            Event(
                type=type_,
                source="quantlab",
                message=message,
                severity=severity,
                trace_id=trace_id,
                payload={"entity_id": entity_id, **(payload or {})},
            )
        )

    # Strategies -------------------------------------------------------------------------

    def validate(self, raw: Any) -> dict[str, Any]:
        """For the Architect: is this spec runnable, and what should the user double-check?"""
        try:
            spec = parse_spec(raw)
        except SpecError as exc:
            return {
                "valid": False,
                "code": exc.code,
                "message": exc.message,
                "errors": exc.details,
                "warnings": [],
                "summary": None,
                "spec_sha256": None,
            }
        return {
            "valid": True,
            "code": None,
            "message": None,
            "errors": [],
            "warnings": spec_warnings(spec),
            "summary": describe(spec),
            "spec_sha256": spec.sha256(),
        }

    def _parse(self, raw: Any) -> Any:
        try:
            return parse_spec(raw)
        except SpecError as exc:
            raise QuantLabError(exc.code, exc.message, details=exc.details) from exc

    async def create_strategy(self, raw: Any) -> dict[str, Any]:
        spec = self._parse(raw)
        strategy_id = f"st_{new_id()[:16]}"
        await self._store.add_strategy(strategy_id, spec.name, spec.hypothesis)
        await self._store.add_version(
            f"sv_{new_id()[:16]}", strategy_id, spec.canonical(), spec.sha256()
        )
        await self._emit(
            EventType.QUANTLAB_STRATEGY_CREATED,
            f"QuantLab strategy created: {spec.name}",
            strategy_id,
            {"name": spec.name, "spec_sha256": spec.sha256()},
        )
        return await self.strategy(strategy_id)

    async def add_version(self, strategy_id: str, raw: Any) -> dict[str, Any]:
        _checked_id(strategy_id)
        if await self._store.strategy(strategy_id) is None:
            raise QuantLabError("NOT_FOUND", "No such strategy.", status=404)
        spec = self._parse(raw)
        # The same spec twice is the same version: versions are identified by content.
        if await self._store.version_by_sha(strategy_id, spec.sha256()) is None:
            await self._store.add_version(
                f"sv_{new_id()[:16]}", strategy_id, spec.canonical(), spec.sha256()
            )
            await self._emit(
                EventType.QUANTLAB_STRATEGY_CREATED,
                f"QuantLab strategy version saved: {spec.name}",
                strategy_id,
                {"name": spec.name, "spec_sha256": spec.sha256()},
            )
        return await self.strategy(strategy_id)

    async def strategy(self, strategy_id: str) -> dict[str, Any]:
        _checked_id(strategy_id)
        record = await self._store.strategy(strategy_id)
        if record is None:
            raise QuantLabError("NOT_FOUND", "No such strategy.", status=404)
        versions = []
        for v in await self._store.versions(strategy_id):
            spec = parse_spec(v["spec"])
            versions.append(
                {
                    "id": v["id"],
                    "number": v["number"],
                    "spec": v["spec"],
                    "spec_sha256": v["spec_sha256"],
                    "created_at": v["created_at"],
                    "summary": describe(spec),
                    "warnings": spec_warnings(spec),
                }
            )
        return {**record, "versions": versions}

    async def strategies(self) -> list[dict[str, Any]]:
        return [await self.strategy(s["id"]) for s in await self._store.strategies()]

    # Datasets ---------------------------------------------------------------------------

    async def import_dataset(self, filename: str, data: bytes, meta: ImportMeta) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(import_bars, filename, data, meta)
        except DataError as exc:
            raise QuantLabError(exc.code, exc.message) from exc
        passport = result.passport
        identity = canonical_json(
            {"original_sha256": passport["original_sha256"], "meta": asdict(meta)}
        )
        dataset_id = f"ds_{sha256_text(identity)[:20]}"
        if (existing := await self._store.dataset(dataset_id)) is not None:
            return self._dataset_view(existing)
        passport = {**passport, "dataset_id": dataset_id}
        snapshot_sha = None
        if result.usable:
            snapshot = await asyncio.to_thread(snapshot_bytes, result.bars)
            snapshot_sha = sha256_bytes(snapshot)
            passport["snapshot_file_sha256"] = snapshot_sha
            await asyncio.to_thread(
                self._freeze_dataset, dataset_id, filename, data, snapshot, passport
            )
        record = {
            "id": dataset_id,
            "status": result.status,
            "synthetic": result.synthetic,
            "symbol": meta.symbol,
            "frequency": passport["frequency"],
            "filename": passport["original_filename"],
            "original_sha256": passport["original_sha256"],
            "normalized_sha256": passport["normalized_sha256"],
            "snapshot_sha256": snapshot_sha,
            "rows": passport["row_count"],
            "passport": passport,
            "preview": result.preview,
        }
        await self._store.add_dataset(record)
        stored = await self._store.dataset(dataset_id)
        assert stored is not None
        if result.usable:
            await self._emit(
                EventType.QUANTLAB_DATASET_IMPORTED,
                f"QuantLab dataset imported: {meta.symbol} ({result.status})",
                dataset_id,
                {"status": result.status, "synthetic": result.synthetic, "rows": len(result.bars)},
            )
        else:
            codes = [f.code for f in result.findings if f.severity == "block"]
            await self._emit(
                EventType.QUANTLAB_DATASET_REJECTED,
                f"QuantLab dataset {result.status.lower()}: {', '.join(codes[:3])}",
                dataset_id,
                {"status": result.status, "codes": codes, "synthetic": result.synthetic},
                Severity.WARNING,
            )
        return self._dataset_view(stored)

    def _freeze_dataset(
        self,
        dataset_id: str,
        filename: str,
        original: bytes,
        snapshot: bytes,
        passport: dict[str, Any],
    ) -> None:
        base = self._root / "datasets"
        base.mkdir(parents=True, exist_ok=True)
        staging = base / f".staging-{dataset_id}-{new_id()[:8]}"
        staging.mkdir()
        try:
            suffix = ".parquet" if original[:4] == b"PAR1" else ".csv"
            _write_atomic(staging / f"original{suffix}", original)
            _write_atomic(staging / "snapshot.parquet", snapshot)
            _write_atomic(staging / "passport.json", json.dumps(passport, indent=2).encode())
            final = base / dataset_id
            if final.exists():  # left over from an import that crashed before recording it
                shutil.rmtree(final)
            os.replace(staging, final)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    async def import_fixture(self, name: str) -> dict[str, Any]:
        if name not in FIXTURES:
            raise QuantLabError("NOT_FOUND", f"No fixture called {name}.", status=404)
        path = self._fixtures / name
        if not path.is_file():
            raise QuantLabError(
                "FIXTURE_MISSING", f"The synthetic fixtures aren't installed ({path})."
            )
        meta = ImportMeta(
            symbol="TEST_SYNTHETIC",
            exchange="TEST_ONLY",
            currency="USD",
            timezone="Etc/UTC",
            frequency="1m",
            provider="synthetic_test_fixture",
            license="internal_test_only",
        )
        return await self.import_dataset(name, path.read_bytes(), meta)

    async def datasets(self) -> list[dict[str, Any]]:
        return [self._dataset_view(d) for d in await self._store.datasets()]

    async def dataset(self, dataset_id: str) -> dict[str, Any]:
        _checked_id(dataset_id)
        record = await self._store.dataset(dataset_id)
        if record is None:
            raise QuantLabError("NOT_FOUND", "No such dataset.", status=404)
        return self._dataset_view(record)

    @staticmethod
    def _dataset_view(record: dict[str, Any]) -> dict[str, Any]:
        return {
            **record,
            "label": SYNTHETIC_LABEL if record["synthetic"] else None,
            "usable": record["status"] in ("ACCEPTED", "WARNING"),
        }

    def _snapshot(self, dataset: dict[str, Any]) -> list[Any]:
        path = self._root / "datasets" / _checked_id(dataset["id"]) / "snapshot.parquet"
        if not path.is_file():
            raise QuantLabError("SNAPSHOT_MISSING", "The dataset's frozen snapshot is missing.")
        if sha256_bytes(path.read_bytes()) != dataset["snapshot_sha256"]:
            raise QuantLabError(
                "SNAPSHOT_TAMPERED", "The snapshot no longer matches its recorded checksum."
            )
        return read_snapshot(path)

    # Experiments ------------------------------------------------------------------------

    async def create_experiment(self, version_id: str, dataset_id: str) -> dict[str, Any]:
        _checked_id(version_id)
        _checked_id(dataset_id)
        version = await self._store.version(version_id)
        if version is None:
            raise QuantLabError("NOT_FOUND", "No such strategy version.", status=404)
        dataset = await self._store.dataset(dataset_id)
        if dataset is None:
            raise QuantLabError("NOT_FOUND", "No such dataset.", status=404)
        if dataset["status"] not in ("ACCEPTED", "WARNING"):
            raise QuantLabError(
                "DATA_INVALID",
                f"The dataset is {dataset['status']}; it can't be used for a run. "
                "See its Data Passport.",
            )
        spec = self._parse(version["spec"])
        passport = dataset["passport"]
        problems = []
        if spec.instrument.symbol.upper() != dataset["symbol"].upper():
            problems.append(
                f"the spec trades {spec.instrument.symbol}, the data is {dataset['symbol']}"
            )
        if spec.instrument.currency != passport["currency"]:
            problems.append(
                f"the spec is in {spec.instrument.currency}, the data in {passport['currency']}"
            )
        if spec.costs.currency != spec.instrument.currency:
            problems.append("fees and instrument are in different currencies (no FX in R1)")
        if spec.timeframe.bar_interval != dataset["frequency"]:
            problems.append(
                f"the spec uses {spec.timeframe.bar_interval} bars, the data is "
                f"{dataset['frequency']}"
            )
        if problems:
            raise QuantLabError("SPEC_DATA_MISMATCH", "Can't run: " + "; ".join(problems) + ".")
        rows = int(dataset["rows"])
        cutoff = engine.split_index(rows, spec.analysis.chronological_oos_fraction)
        needed = spec.signal.slow_window + 1
        if cutoff < needed or rows - cutoff < 2:
            raise QuantLabError(
                "INSUFFICIENT_DATA",
                f"{rows} bars give {cutoff} train and {rows - cutoff} OOS bars; the spec needs "
                f"at least {needed} train bars (slow window + 1) and 2 OOS bars.",
            )
        manifest = await self._manifest(version, dataset, spec, cutoff)
        manifest_sha = sha256_text(canonical_json(manifest))
        experiment_id = f"exp_{manifest_sha[:20]}"
        existing = await self._store.experiment(experiment_id)
        if existing is not None and existing["status"] not in ("failed", "cancelled"):
            return await self.experiment(experiment_id)  # same inputs: same experiment
        if existing is None:
            await self._store.add_experiment(
                experiment_id, version_id, dataset_id, canonical_json(manifest), manifest_sha
            )
        else:
            await self._store.set_queued(experiment_id)
        await self._emit(
            EventType.QUANTLAB_EXPERIMENT_CREATED,
            f"QuantLab experiment queued: {spec.name} on {dataset['symbol']}",
            experiment_id,
            {"status": "queued", "synthetic": dataset["synthetic"]},
            trace_id=experiment_id,
        )
        self._schedule(experiment_id)
        return await self.experiment(experiment_id)

    async def _manifest(
        self, version: dict[str, Any], dataset: dict[str, Any], spec: Any, cutoff: int
    ) -> dict[str, Any]:
        import pyarrow

        passport = dataset["passport"]
        bars = await asyncio.to_thread(self._snapshot, dataset)
        rows = len(bars)
        iso = engine._iso
        return {
            "schema_version": "0.1",
            "environment": "synthetic_fixture_test"
            if dataset["synthetic"]
            else "historical_research",
            "strategy_id": version["strategy_id"],
            "strategy_version_id": version["id"],
            "strategy_version": version["number"],
            "strategy_spec_sha256": version["spec_sha256"],
            "dataset_id": dataset["id"],
            "dataset_synthetic": dataset["synthetic"],
            "dataset_original_sha256": dataset["original_sha256"],
            "dataset_snapshot_sha256": dataset["normalized_sha256"],
            "dataset_snapshot_file_sha256": dataset["snapshot_sha256"],
            "engine_name": engine.ENGINE_NAME,
            "engine_version": engine.ENGINE_VERSION,
            "code_revision": self._code_revision,
            "simulation": {
                "timestamps": "bar_start_utc",
                "bar_interval": spec.timeframe.bar_interval,
                "signal_availability": "bar_close",
                "fill": "next_bar_open",
                "latency": "zero (idealised)",
                "slippage_bps": spec.execution.slippage_bps,
                "fee_fixed_per_order": spec.costs.fee_fixed_per_order,
                "fee_variable_bps": spec.costs.fee_variable_bps,
                "currency": spec.costs.currency,
                "size_units": spec.position.size_units,
                "initial_cash": spec.position.initial_cash,
                "end_of_data": spec.execution.end_of_data,
                "timezone": passport["normalized_timezone"],
            },
            "validation": {
                "method": "chronological_split",
                "oos_fraction": spec.analysis.chronological_oos_fraction,
                "train_fraction": 1 - spec.analysis.chronological_oos_fraction,
                "cutoff_index": cutoff,
                "train_bars": cutoff,
                "oos_bars": rows - cutoff,
                "train_start_utc": iso(bars[0].ts),
                "train_end_utc": iso(bars[cutoff - 1].ts),
                "oos_start_utc": iso(bars[cutoff].ts),
                "oos_end_utc": iso(bars[-1].ts),
                "holdout_touched_for_optimization": False,
                "parameter_search": "none",
                "verdict_policy": VERDICT_POLICY,
            },
            "seed": None,
            "randomness": "none: the simulation is deterministic",
            "runtime": {"python": platform.python_version(), "pyarrow": pyarrow.__version__},
            "live_trading": False,
        }

    def _schedule(self, experiment_id: str) -> None:
        self._cancel[experiment_id] = threading.Event()
        task = asyncio.create_task(self._run(experiment_id), name=f"quantlab-{experiment_id}")
        self._tasks[experiment_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(experiment_id, None))

    async def cancel(self, experiment_id: str) -> dict[str, Any]:
        _checked_id(experiment_id)
        exp = await self._store.experiment(experiment_id)
        if exp is None:
            raise QuantLabError("NOT_FOUND", "No such experiment.", status=404)
        if exp["status"] in _DONE:
            raise QuantLabError("CONFLICT", f"The run is already {exp['status']}.", status=409)
        if flag := self._cancel.get(experiment_id):
            flag.set()
        return await self.experiment(experiment_id)

    async def _stage(self, experiment_id: str, stage: str) -> None:
        await self._store.set_running(experiment_id, stage)
        await self._emit(
            EventType.QUANTLAB_EXPERIMENT_RUNNING,
            f"QuantLab run: {stage.replace('_', ' ')}",
            experiment_id,
            {"status": "running", "stage": stage},
            Severity.DEBUG,
            trace_id=experiment_id,
        )

    async def _run(self, experiment_id: str) -> None:
        flag = self._cancel[experiment_id]
        staging: Path | None = None
        try:
            async with self._lock:
                if flag.is_set():
                    raise engine.Cancelled
                exp = await self._store.experiment(experiment_id)
                assert exp is not None
                manifest = exp["manifest"]
                version = await self._store.version(manifest["strategy_version_id"])
                dataset = await self._store.dataset(manifest["dataset_id"])
                assert version is not None and dataset is not None
                await self._stage(experiment_id, "loading_snapshot")
                bars = await asyncio.to_thread(self._snapshot, dataset)
                await self._stage(experiment_id, "causal_simulation")
                outcome = await asyncio.to_thread(
                    self._compute,
                    bars,
                    version["spec"],
                    manifest,
                    dataset,
                    await self._store.variants_on_dataset(dataset["id"]),
                    flag.is_set,
                )
                await self._stage(experiment_id, "writing_artifacts")
                written, artifacts = await asyncio.to_thread(
                    self._write_artifacts, experiment_id, manifest, outcome, dataset["passport"]
                )
                staging = written
                if flag.is_set():
                    raise engine.Cancelled
                staging = None  # from here the folder is the experiment's
                await asyncio.to_thread(self._commit, experiment_id, written)
                report = outcome["report"]
                await self._store.add_artifacts(experiment_id, artifacts)
                await self._store.add_validations(experiment_id, report["checks"])
                await self._store.set_finished(
                    experiment_id,
                    "completed",
                    verdict=report["verdict"],
                    results_sha256=outcome["results_sha256"],
                    summary=outcome["summary"],
                )
            await self._emit(
                EventType.QUANTLAB_EXPERIMENT_COMPLETED,
                f"QuantLab run complete: {report['verdict']}",
                experiment_id,
                {
                    "status": "completed",
                    "verdict": report["verdict"],
                    "synthetic": dataset["synthetic"],
                },
                Severity.IMPORTANT,
                trace_id=experiment_id,
            )
            await self._emit(
                EventType.QUANTLAB_VALIDATION_COMPLETED,
                f"QuantLab evidence gates: {report['verdict']}",
                experiment_id,
                {
                    "verdict": report["verdict"],
                    "missing_gates": len(report["missing_gates"]),
                },
                Severity.DEBUG,
                trace_id=experiment_id,
            )
        except (engine.Cancelled, asyncio.CancelledError) as exc:
            await self._store.set_finished(
                experiment_id, "cancelled", error_code="CANCELLED", error="Cancelled."
            )
            await self._emit(
                EventType.QUANTLAB_EXPERIMENT_CANCELLED,
                "QuantLab run cancelled",
                experiment_id,
                {"status": "cancelled"},
                trace_id=experiment_id,
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
        except Exception as exc:
            code = exc.code if isinstance(exc, QuantLabError) else "RUN_ERROR"
            message = exc.message if isinstance(exc, QuantLabError) else str(exc)
            log.exception("quantlab run %s failed", experiment_id)
            await self._store.set_finished(
                experiment_id, "failed", error_code=code, error=message[:500]
            )
            await self._emit(
                EventType.QUANTLAB_EXPERIMENT_FAILED,
                f"QuantLab run failed: {message[:120]}",
                experiment_id,
                {"status": "failed", "error_code": code},
                Severity.WARNING,
                trace_id=experiment_id,
            )
        finally:
            self._cancel.pop(experiment_id, None)
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)

    def _compute(
        self,
        bars: list[Any],
        spec_raw: dict[str, Any],
        manifest: dict[str, Any],
        dataset: dict[str, Any],
        variants: int,
        cancelled: Callable[[], bool],
    ) -> dict[str, Any]:
        spec = parse_spec(spec_raw)
        config = engine.Config(
            interval=spec.interval,
            size_units=spec.position.size_units,
            initial_cash=engine.dec(spec.position.initial_cash),
            fee_fixed=engine.dec(spec.costs.fee_fixed_per_order),
            fee_bps=engine.dec(spec.costs.fee_variable_bps),
            slippage_bps=engine.dec(spec.execution.slippage_bps),
        )
        result = engine.run(
            bars,
            fast=spec.signal.fast_window,
            slow=spec.signal.slow_window,
            config=config,
            cancelled=cancelled,
        )
        cutoff = int(manifest["validation"]["cutoff_index"])
        if cutoff != engine.split_index(len(bars), spec.analysis.chronological_oos_fraction):
            raise QuantLabError("SPLIT_MISMATCH", "The snapshot doesn't match the manifest.")
        checks = engine.audit(result)
        full = engine.full_metrics(result)
        train = engine.segment_metrics(result, 0, cutoff - 1)
        oos = engine.segment_metrics(result, cutoff, len(bars) - 1)
        split = {
            k: manifest["validation"][k]
            for k in (
                "cutoff_index",
                "train_bars",
                "oos_bars",
                "train_start_utc",
                "train_end_utc",
                "oos_start_utc",
                "oos_end_utc",
            )
        }
        report = evaluate(
            passport=dataset["passport"],
            audit=checks,
            full=full,
            train=train,
            oos=oos,
            split=split,
            spec=spec.model_dump(mode="json"),
            spec_sha256=manifest["strategy_spec_sha256"],
            variants_on_dataset=max(1, variants),
        ).as_dict()
        orders = engine.order_rows(result)
        trades = engine.trade_rows(result)
        signals = engine.signal_rows(result)
        equity = engine.equity_rows(result, cutoff)
        metrics = {"full": full, "train": train, "oos": oos, "split": split}
        results_sha = sha256_text(
            canonical_json(
                {
                    "orders": orders,
                    "trades": trades,
                    "signals": signals,
                    "equity": equity,
                    "metrics": metrics,
                }
            )
        )
        summary = {
            "name": spec.name,
            "symbol": spec.instrument.symbol,
            "currency": spec.instrument.currency,
            "synthetic": dataset["synthetic"],
            "verdict": report["verdict"],
            "meaning": MEANINGS[report["verdict"]],
            "reason": report["reason"],
            "headline": report["assessment"]["headline"],
            "metrics": metrics,
            "missing_gates": report["missing_gates"],
            "assessment": report["assessment"],
            "checks": report["checks"],
            "audit": [asdict(c) for c in checks],
        }
        return {
            "orders": orders,
            "trades": trades,
            "signals": signals,
            "equity": equity,
            "metrics": metrics,
            "report": report,
            "audit": checks,
            "results_sha256": results_sha,
            "summary": summary,
        }

    def _write_artifacts(
        self,
        experiment_id: str,
        manifest: dict[str, Any],
        outcome: dict[str, Any],
        passport: dict[str, Any],
    ) -> tuple[Path, list[tuple[str, str, str, int]]]:
        base = self._root / "experiments"
        base.mkdir(parents=True, exist_ok=True)
        staging = base / f".staging-{experiment_id}-{new_id()[:8]}"
        staging.mkdir()
        files: dict[str, bytes] = {
            "manifest.json": json.dumps(manifest, indent=2, sort_keys=True).encode(),
            "data_qa.json": json.dumps(passport, indent=2, sort_keys=True).encode(),
            "metrics.json": json.dumps(
                {
                    "results_sha256": outcome["results_sha256"],
                    **outcome["metrics"],
                    "validation": outcome["report"],
                },
                indent=2,
                sort_keys=True,
            ).encode(),
            "trades.parquet": _parquet(outcome["trades"], _TRADE_NUMBERS),
            "orders.parquet": _parquet(outcome["orders"], _ORDER_NUMBERS),
            "signals.parquet": _parquet(outcome["signals"], ("sma_fast", "sma_slow")),
            "equity.parquet": _parquet_columns(outcome["equity"]),
        }
        audit_lines = [
            {"step": "snapshot_verified", "sha256": manifest["dataset_snapshot_file_sha256"]},
            *(
                {"check": c.id, "passed": c.passed, "detail": c.detail, "checked": c.checked}
                for c in outcome["audit"]
            ),
            {"step": "verdict", "verdict": outcome["report"]["verdict"]},
            {
                "step": "results",
                "results_sha256": outcome["results_sha256"],
                "at": utcnow().isoformat(),
            },
        ]
        files["audit.jsonl"] = (
            "\n".join(json.dumps(line, sort_keys=True) for line in audit_lines) + "\n"
        ).encode()
        rows = []
        for name, data in files.items():
            _write_atomic(staging / name, data)
            rows.append((name.split(".")[0], name, sha256_bytes(data), len(data)))
        return staging, rows

    def _commit(self, experiment_id: str, staging: Path) -> None:
        final = self._root / "experiments" / experiment_id
        if final.exists():  # an earlier failed/cancelled attempt never gets here; a crash might
            shutil.rmtree(final)
        os.replace(staging, final)

    async def experiment(self, experiment_id: str) -> dict[str, Any]:
        _checked_id(experiment_id)
        exp = await self._store.experiment(experiment_id)
        if exp is None:
            raise QuantLabError("NOT_FOUND", "No such experiment.", status=404)
        manifest = exp["manifest"]
        version = await self._store.version(manifest["strategy_version_id"])
        dataset = await self._store.dataset(manifest["dataset_id"])
        return {
            "id": exp["id"],
            "status": exp["status"],
            "stage": exp["stage"],
            "created_at": exp["created_at"],
            "started_at": exp["started_at"],
            "finished_at": exp["finished_at"],
            "attempts": exp["attempts"],
            "error_code": exp["error_code"],
            "error": exp["error"],
            "verdict": exp["verdict"],
            "results_sha256": exp["results_sha256"],
            "manifest": manifest,
            "manifest_sha256": exp["manifest_sha256"],
            "strategy_name": version["spec"]["name"] if version else None,
            "strategy_version": version["number"] if version else None,
            "dataset": self._dataset_view(dataset) if dataset else None,
            "summary": exp["summary"],
            "artifacts": await self._store.artifacts(experiment_id),
        }

    async def experiments(self) -> list[dict[str, Any]]:
        out = []
        for exp in await self._store.experiments():
            summary = exp["summary"] or {}
            manifest = exp["manifest"]
            out.append(
                {
                    "id": exp["id"],
                    "status": exp["status"],
                    "stage": exp["stage"],
                    "created_at": exp["created_at"],
                    "finished_at": exp["finished_at"],
                    "verdict": exp["verdict"],
                    "error_code": exp["error_code"],
                    "error": exp["error"],
                    "strategy_version_id": manifest["strategy_version_id"],
                    "strategy_version": manifest["strategy_version"],
                    "dataset_id": manifest["dataset_id"],
                    "synthetic": manifest["dataset_synthetic"],
                    "name": summary.get("name"),
                    "symbol": summary.get("symbol"),
                    "headline": summary.get("headline"),
                    "net_pnl": (summary.get("metrics") or {}).get("full", {}).get("net_pnl"),
                    "oos_net_pnl": (summary.get("metrics") or {}).get("oos", {}).get("net_pnl"),
                    "max_drawdown_pct": (summary.get("metrics") or {})
                    .get("full", {})
                    .get("max_drawdown_pct"),
                    "currency": summary.get("currency"),
                }
            )
        return out

    def _artifact(self, experiment_id: str, name: str) -> Path:
        path = self._root / "experiments" / _checked_id(experiment_id) / name
        if not path.is_file():
            raise QuantLabError("NOT_FOUND", "The run has no results yet.", status=404)
        return path

    async def _completed(self, experiment_id: str) -> dict[str, Any]:
        exp = await self.experiment(experiment_id)
        if exp["status"] != "completed":
            raise QuantLabError("NOT_READY", f"The run is {exp['status']}; no results.", status=409)
        return exp

    async def ledger(self, experiment_id: str) -> dict[str, Any]:
        exp = await self._completed(experiment_id)
        recorded = {a["kind"]: a["sha256"] for a in exp["artifacts"]}

        def read(name: str) -> list[dict[str, Any]]:
            import pyarrow.parquet as pq

            path = self._artifact(experiment_id, f"{name}.parquet")
            if sha256_bytes(path.read_bytes()) != recorded.get(name):
                raise QuantLabError("ARTIFACT_TAMPERED", f"{name}.parquet fails its checksum.")
            rows: list[dict[str, Any]] = pq.read_table(path).to_pylist()
            return rows

        return await asyncio.to_thread(
            lambda: {"trades": read("trades"), "orders": read("orders"), "signals": read("signals")}
        )

    async def equity(self, experiment_id: str, points: int = 800) -> dict[str, Any]:
        await self._completed(experiment_id)
        path = self._artifact(experiment_id, "equity.parquet")

        def load() -> dict[str, Any]:
            import pyarrow.parquet as pq

            table = pq.read_table(path, columns=["ts_utc", "equity", "drawdown", "segment"])
            ts = table.column("ts_utc").to_pylist()
            eq = table.column("equity").to_pylist()
            dd = table.column("drawdown").to_pylist()
            seg = table.column("segment").to_pylist()
            keep = _downsample(eq, max(50, min(points, 5000)))
            return {
                "total_points": len(ts),
                "points": [
                    {"ts": ts[i], "equity": eq[i], "drawdown": dd[i], "segment": seg[i]}
                    for i in keep
                ],
                "oos_start": next((ts[i] for i, s in enumerate(seg) if s == "oos"), None),
            }

        return await asyncio.to_thread(load)

    async def reproduce(self, experiment_id: str) -> dict[str, Any]:
        """Run the same manifest again from the frozen snapshot; compare the results hash."""
        exp = await self._completed(experiment_id)
        manifest = exp["manifest"]
        version = await self._store.version(manifest["strategy_version_id"])
        dataset = await self._store.dataset(manifest["dataset_id"])
        assert version is not None and dataset is not None
        variants = await self._store.variants_on_dataset(dataset["id"])

        def again() -> str:
            bars = self._snapshot(dataset)
            outcome = self._compute(
                bars, version["spec"], manifest, dataset, variants, lambda: False
            )
            return str(outcome["results_sha256"])

        rerun = await asyncio.to_thread(again)
        return {
            "experiment_id": experiment_id,
            "recorded_results_sha256": exp["results_sha256"],
            "rerun_results_sha256": rerun,
            "identical": rerun == exp["results_sha256"],
            "same_code_revision": manifest["code_revision"] == self._code_revision,
        }

    async def overview(self) -> dict[str, Any]:
        experiments = await self.experiments()
        datasets = await self.datasets()
        strategies = await self._store.strategies()
        latest = next((e for e in experiments if e["status"] == "completed"), None)
        return {
            "scope": "R1 · cash equity · long-only · SMA crossover · research only",
            "live_trading": False,
            "engine": {"name": engine.ENGINE_NAME, "version": engine.ENGINE_VERSION},
            "counts": {
                "strategies": len(strategies),
                "datasets": len(datasets),
                "experiments": len(experiments),
                "running": sum(1 for e in experiments if e["status"] in ("queued", "running")),
            },
            "latest": await self.experiment(latest["id"]) if latest else None,
            "experiments": experiments[:8],
            "fixtures_available": all((self._fixtures / f).is_file() for f in FIXTURES),
        }

    async def report_text(self) -> str:
        """For the brain: the latest runs in a few lines, critical by design."""
        experiments = [e for e in await self.experiments() if e["status"] == "completed"][:3]
        if not experiments:
            return "No QuantLab experiment has completed yet."
        lines = []
        for e in experiments:
            exp = await self.experiment(e["id"])
            s = exp["summary"] or {}
            a = s.get("assessment", {})
            label = " [SYNTHETIC / TEST ONLY]" if e["synthetic"] else ""
            lines.append(
                f"{s.get('name')} v{e['strategy_version']} on {s.get('symbol')}{label}: "
                f"{e['verdict']} — {a.get('headline', '')}\n"
                f"  Observed: {' '.join(a.get('observed', [])[:2])}\n"
                f"  Can't conclude: {' '.join(a.get('cannot_conclude', [])[:2])}\n"
                f"  Next pre-registered test: {a.get('next_test', '')}"
            )
        return "\n".join(lines)


_ORDER_NUMBERS = (
    "reference_open", "fill_price", "notional", "fee", "slippage_cost", "cash_before", "cash_after",
)  # fmt: skip
_TRADE_NUMBERS = (
    "entry_price", "entry_fee", "exit_price", "exit_fee", "gross_pnl", "fees", "net_pnl",
    "return_pct",
)  # fmt: skip


def _num(value: Any) -> float | None:
    return None if value is None else float(Decimal(value))


def _parquet(rows: list[dict[str, Any]], numeric: tuple[str, ...]) -> bytes:
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    converted = [{k: (_num(v) if k in numeric else v) for k, v in r.items()} for r in rows]
    table = (
        pa.Table.from_pylist(converted)
        if converted
        else pa.table({"empty": pa.array([], pa.int8())})
    )
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def _parquet_columns(columns: dict[str, list[Any]]) -> bytes:
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    numeric = {"close", "cash", "equity", "drawdown"}
    table = pa.table(
        {k: ([_num(v) for v in vals] if k in numeric else vals) for k, vals in columns.items()}
    )
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def _downsample(values: list[Any], limit: int) -> list[int]:
    """Indices to draw: per bucket the first, the lowest and the highest point (keeps peaks
    and drawdown troughs), plus the last point."""
    n = len(values)
    if n <= limit:
        return list(range(n))
    buckets = max(1, limit // 3)
    size = n / buckets
    keep: set[int] = {0, n - 1}
    for b in range(buckets):
        lo, hi = int(b * size), min(n, int((b + 1) * size))
        if lo >= hi:
            continue
        chunk = range(lo, hi)
        keep.add(lo)
        keep.add(min(chunk, key=lambda i: values[i] if values[i] is not None else 0))
        keep.add(max(chunk, key=lambda i: values[i] if values[i] is not None else 0))
    return sorted(keep)
