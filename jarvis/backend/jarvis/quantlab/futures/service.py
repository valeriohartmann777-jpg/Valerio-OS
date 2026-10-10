"""Futures research runs: versioned strategies × verified datasets → ledgers, validation, verdicts.

A run is identified by the SHA-256 of its manifest (spec, dataset snapshot, engine
version, code revision, calendar, environment), so asking for the same run twice
returns the same run. Runs are persistent jobs: queued, run one at a time in a
worker thread, cancellable, marked INTERRUPTED by a restart. Only deterministic
code computes results; nothing here asks a language model for a number.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import platform
import shutil
import threading
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.quantlab.futures import contracts as ct
from jarvis.quantlab.futures import engine, metrics, validation
from jarvis.quantlab.futures import sessions as cal
from jarvis.quantlab.futures.audit import audit, passed
from jarvis.quantlab.futures.engine import MINUTE, Bars, Result
from jarvis.quantlab.futures.sessions import Window
from jarvis.quantlab.futures.spec import (
    FuturesSpec,
    FuturesSpecError,
    OpeningRangeBreakout,
    clock,
    describe,
    parse,
    template,
)
from jarvis.quantlab.futures.store import ResearchStore, now
from jarvis.quantlab.hub.service import DataHubService, HubError
from jarvis.quantlab.spec import canonical_json, sha256_text
from jarvis.util import new_id

log = logging.getLogger("jarvis.quantlab.research")

NS = 1_000_000_000
KINDS = ("backtest", "validation")
LIMITATIONS = [
    "1-minute OHLCV bars: the order of prices within a minute is unknown (ambiguous bars are "
    "resolved conservatively; the optimistic case is reported as a bound).",
    "No bid/ask: the spread is part of the slippage assumption; no partial fills, no queue "
    "position, no market impact.",
    "Fixed contract count; margin is not modelled (only an optional initial-margin check).",
    "Positions are flat at every session end; overnight holding and roll trades aren't "
    "supported yet.",
    "Commission and fee schedules are your stated assumptions, not historical broker rates.",
]


class ResearchError(Exception):
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


def _pkg(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:  # pragma: no cover
        return None


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / NS, UTC).isoformat().replace("+00:00", "Z")


def _price(ticks: int | None, tick_fixed: int) -> float | None:
    if ticks is None:
        return None
    return float(Decimal(ticks * tick_fixed) / Decimal(NS))


class ResearchService:
    def __init__(
        self,
        *,
        store: ResearchStore,
        hub: DataHubService,
        bus: EventBus,
        root: Path,
        code_revision: str,
    ) -> None:
        self._store = store
        self._hub = hub
        self._bus = bus
        self.root = root
        self._revision = code_revision
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._cancel: dict[str, threading.Event] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # Lifecycle --------------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.root.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(self.root / "staging", ignore_errors=True)
        for run in await self._store.runs_with_status("QUEUED", "RUNNING"):
            await self._store.update_run(
                run["id"],
                status="INTERRUPTED",
                stage=None,
                finished_at=now(),
                error_code="INTERRUPTED",
                error="JARVIS stopped during the run. Start it again — the result is identical "
                "when nothing changed.",
            )
        self._worker = asyncio.create_task(self._work(), name="quantlab-research")

    async def stop(self) -> None:
        for event in self._cancel.values():
            event.set()
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    async def _emit(self, message: str, payload: dict[str, Any], warn: bool = False) -> None:
        await self._bus.emit(
            EventType.QUANTLAB_RESEARCH_RUN,
            message=message,
            source="quantlab",
            severity=Severity.WARNING if warn else Severity.INFO,
            payload=payload,
        )

    # Strategies -------------------------------------------------------------------------

    @staticmethod
    def templates() -> list[dict[str, Any]]:
        return [
            {
                "kind": "opening_range_breakout",
                "title": "Opening range breakout",
                "spec": template("opening_range_breakout", "NQ"),
            },
            {
                "kind": "ma_crossover",
                "title": "Intraday moving-average crossover",
                "spec": template("ma_crossover", "NQ"),
            },
        ]

    def check(self, raw: Any) -> dict[str, Any]:
        try:
            spec = parse(raw)
        except FuturesSpecError as exc:
            return {
                "valid": False,
                "errors": exc.details or [{"field": "(spec)", "problem": exc.message}],
            }
        return {
            "valid": True,
            "errors": [],
            "description": describe(spec),
            "sha256": spec.sha256(),
            "state": "DRAFT" if spec.unresolved else "READY",
            "unresolved": [a.model_dump() for a in spec.unresolved],
            "assumptions": [a.model_dump() for a in spec.assumptions],
            "warnings": _warnings(spec),
        }

    def _parse(self, raw: Any) -> FuturesSpec:
        try:
            return parse(raw)
        except FuturesSpecError as exc:
            raise ResearchError(exc.code, exc.message, details=exc.details) from None

    async def create_strategy(
        self, raw: Any, *, origin: str = "user", note: str | None = None
    ) -> dict[str, Any]:
        spec = self._parse(raw)
        strategy_id = "st-" + new_id()[:12]
        await self._store.add_strategy(
            strategy_id, spec.name, spec.hypothesis, spec.instrument.product
        )
        await self._add_version(strategy_id, spec, origin, note, None)
        await self._emit(f"Strategy created: {spec.name}", {"strategy_id": strategy_id})
        return await self.strategy(strategy_id)

    async def add_version(
        self,
        strategy_id: str,
        raw: Any,
        *,
        origin: str = "user",
        note: str | None = None,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        if await self._store.strategy(strategy_id) is None:
            raise ResearchError("NOT_FOUND", "No such strategy.", status=404)
        spec = self._parse(raw)
        version_id = await self._add_version(strategy_id, spec, origin, note, parent_id)
        version_row = await self._store.version(version_id)
        assert version_row is not None
        await self._emit(
            f"Strategy version {version_row['number']} saved ({origin})",
            {"strategy_id": strategy_id, "version_id": version_id},
        )
        return await self.strategy(strategy_id)

    async def _add_version(
        self,
        strategy_id: str,
        spec: FuturesSpec,
        origin: str,
        note: str | None,
        parent_id: str | None,
    ) -> str:
        existing = await self._store.version_by_sha(strategy_id, spec.sha256())
        if existing is not None:
            return str(existing["id"])  # the same spec is the same version
        version_id = "sv-" + new_id()[:12]
        await self._store.add_version(
            version_id, strategy_id, spec.canonical(), spec.sha256(), origin, note, parent_id
        )
        return version_id

    async def version_owner(self, version_id: str) -> str:
        row = await self._store.version(version_id)
        if row is None:
            raise ResearchError("NOT_FOUND", "No such strategy version.", status=404)
        return str(row["strategy_id"])

    async def strategies(self) -> list[dict[str, Any]]:
        out = []
        runs = await self._store.runs()
        for row in await self._store.strategies():
            versions = await self._store.versions(row["id"])
            mine = [r for r in runs if r["strategy_id"] == row["id"]]
            latest = mine[0] if mine else None
            out.append(
                {
                    **row,
                    "versions": len(versions),
                    "latest_version_id": versions[-1]["id"] if versions else None,
                    "latest_run": _run_row(latest) if latest else None,
                }
            )
        return out

    async def strategy(self, strategy_id: str) -> dict[str, Any]:
        row = await self._store.strategy(strategy_id)
        if row is None:
            raise ResearchError("NOT_FOUND", "No such strategy.", status=404)
        versions = []
        for v in await self._store.versions(strategy_id):
            spec = parse(v["spec"])
            versions.append(
                {
                    "id": v["id"],
                    "number": v["number"],
                    "parent_id": v["parent_id"],
                    "origin": v["origin"],
                    "note": v["note"],
                    "created_at": v["created_at"],
                    "spec": v["spec"],
                    "spec_sha256": v["spec_sha256"],
                    "description": describe(spec),
                    "state": "DRAFT" if spec.unresolved else "READY",
                    "assumptions": [a.model_dump() for a in spec.assumptions],
                }
            )
        runs = [r for r in await self._store.runs() if r["strategy_id"] == strategy_id]
        trials, _ = await self._store.trial_stats(strategy_id)
        return {
            **row,
            "versions": versions,
            "runs": [_run_row(r) for r in runs],
            "trials": {"variants": trials, "recent": (await self._store.trials(strategy_id, 50))},
            "holdouts": await self._store.holdouts(strategy_id),
            "notes": await self._store.notes(strategy_id),
        }

    async def add_note(
        self, strategy_id: str, kind: str, text: str, run_id: str | None
    ) -> dict[str, Any]:
        if await self._store.strategy(strategy_id) is None:
            raise ResearchError("NOT_FOUND", "No such strategy.", status=404)
        if kind not in ("note", "decision"):
            raise ResearchError("INVALID_KIND", "A note is 'note' or 'decision'.")
        await self._store.add_note(strategy_id, run_id, kind, text.strip()[:4000])
        return await self.strategy(strategy_id)

    # Runs -------------------------------------------------------------------------------

    async def start_run(
        self,
        version_id: str,
        dataset_id: str,
        *,
        kind: str = "backtest",
        include_holdout: bool = False,
        confirm_holdout: bool = False,
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise ResearchError("INVALID_KIND", f"Run kind must be one of {', '.join(KINDS)}.")
        version_row = await self._store.version(version_id)
        if version_row is None:
            raise ResearchError("NOT_FOUND", "No such strategy version.", status=404)
        spec = parse(version_row["spec"])
        if spec.unresolved:
            raise ResearchError(
                "REQUIRES_CLARIFICATION",
                "Some rule details are still unknown: "
                + ", ".join(a.field for a in spec.unresolved),
                "Resolve them in the Strategy Studio (confirm or set each value), then run.",
            )
        if include_holdout and not confirm_holdout:
            raise ResearchError(
                "HOLDOUT_NOT_CONFIRMED",
                "Evaluating the sealed holdout needs an explicit confirmation.",
                "It can be looked at independently only once — confirm when the spec is final.",
            )
        if include_holdout and kind != "validation":
            raise ResearchError("INVALID_KIND", "The holdout is evaluated in a validation run.")
        try:
            dataset = await self._hub.dataset(dataset_id)
        except HubError as exc:
            raise ResearchError(exc.code, exc.message, status=exc.status) from None
        core = {
            "manifest_version": "qr-run-1",
            "kind": kind,
            "include_holdout": include_holdout,
            "strategy_id": version_row["strategy_id"],
            "version_id": version_id,
            "version_number": version_row["number"],
            "spec_sha256": version_row["spec_sha256"],
            "spec": version_row["spec"],
            "dataset": {
                "id": dataset_id,
                "snapshot_sha256": dataset["snapshot_sha256"],
                "provider": dataset["provider"],
                "dataset": dataset["dataset"],
                "symbol": dataset["symbol"],
                "stype_in": dataset["stype_in"],
                "start": dataset["start"],
                "end": dataset["end"],
                "fixture": dataset["fixture"],
            },
            "engine": {
                "name": engine.ENGINE_NAME,
                "version": engine.ENGINE_VERSION,
                "code_revision": self._revision,
            },
            "calendar": {"library": cal.LIBRARY, "version": cal.library_version(), "venue": "XNYS"},
            "environment": {
                "python": platform.python_version(),
                "numpy": _pkg("numpy"),
                "pyarrow": _pkg("pyarrow"),
                "databento": _pkg("databento"),
            },
            "seed": spec.validation.bootstrap.seed,
            "live_trading": False,
        }
        manifest_sha = sha256_text(canonical_json(core))
        run_id = "run-" + manifest_sha[:16]
        existing = await self._store.run(run_id)
        if existing is not None:
            if existing["status"] in ("QUEUED", "RUNNING", "COMPLETED"):
                return await self.run(run_id)
            requeued = await self._store.transition(
                run_id,
                ("FAILED", "CANCELED", "INTERRUPTED"),
                status="QUEUED",
                stage=None,
                progress=0,
                error_code=None,
                error=None,
                started_at=None,
                finished_at=None,
            )
            if not requeued:
                return await self.run(run_id)
        else:
            await self._store.add_run(
                {
                    "id": run_id,
                    "strategy_id": version_row["strategy_id"],
                    "version_id": version_id,
                    "dataset_id": dataset_id,
                    "kind": kind,
                    "include_holdout": include_holdout,
                    "manifest": {**core, "created_at": now()},
                    "manifest_sha256": manifest_sha,
                    "fixture": dataset["fixture"],
                }
            )
        await self._emit(
            f"{kind.title()} queued — {spec.name} v{version_row['number']} on {dataset['symbol']}",
            {"run_id": run_id, "status": "QUEUED"},
        )
        await self._queue.put(run_id)
        return await self.run(run_id)

    async def run(self, run_id: str) -> dict[str, Any]:
        row = await self._store.run(run_id)
        if row is None:
            raise ResearchError("NOT_FOUND", "No such run.", status=404)
        return row

    async def runs(self) -> list[dict[str, Any]]:
        return [_run_row(r) for r in await self._store.runs()]

    async def cancel(self, run_id: str) -> dict[str, Any]:
        row = await self.run(run_id)
        if row["status"] not in ("QUEUED", "RUNNING"):
            raise ResearchError("NOT_RUNNING", f"The run is {row['status'].lower()}.", status=409)
        if await self._store.transition(run_id, ("QUEUED",), status="CANCELED", finished_at=now()):
            await self._emit("Run cancelled", {"run_id": run_id, "status": "CANCELED"})
        else:
            event = self._cancel.get(run_id)
            if event is not None:
                event.set()  # the worker stops at its next check
        return await self.run(run_id)

    async def stop_all(self) -> dict[str, Any]:
        stopped = []
        for row in await self._store.runs_with_status("QUEUED", "RUNNING"):
            with contextlib.suppress(ResearchError):
                await self.cancel(row["id"])
                stopped.append(row["id"])
        return {"runs": stopped}

    async def wait_idle(self, timeout: float = 120.0) -> None:  # noqa: ASYNC109
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if not await self._store.runs_with_status("QUEUED", "RUNNING"):
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("research still busy")

    async def _work(self) -> None:
        while True:
            run_id = await self._queue.get()
            try:
                await self._execute(run_id)
            except asyncio.CancelledError:
                raise
            except engine.Cancelled:
                if await self._store.transition(
                    run_id, ("RUNNING",), status="CANCELED", stage=None, finished_at=now()
                ):
                    await self._emit("Run cancelled", {"run_id": run_id, "status": "CANCELED"})
            except (ResearchError, HubError) as exc:
                await self._fail(run_id, exc.code, exc.message)
            except Exception as exc:  # the worker must survive anything
                log.exception("research run crashed")
                await self._fail(run_id, "INTERNAL", f"{type(exc).__name__}: {exc}")
            finally:
                self._cancel.pop(run_id, None)

    async def _fail(self, run_id: str, code: str, message: str) -> None:
        if not await self._store.transition(
            run_id,
            ("RUNNING",),
            status="FAILED",
            stage=None,
            finished_at=now(),
            error_code=code,
            error=message,
        ):
            return
        await self._emit(f"Run failed: {message}", {"run_id": run_id, "status": "FAILED"}, True)

    async def _stage(self, run_id: str, stage: str, progress: float) -> None:
        await self._store.update_run(run_id, stage=stage, progress=round(progress, 3))
        await self._emit(
            f"Research run: {stage}",
            {"run_id": run_id, "status": "RUNNING", "stage": stage, "progress": progress},
        )

    def _threadsafe_stage(self, run_id: str) -> Any:
        loop = self._loop

        def report(stage: str, fraction: float) -> None:
            if loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._stage(run_id, stage, 0.2 + 0.7 * fraction), loop
                )

        return report

    async def _execute(self, run_id: str) -> None:
        stop = threading.Event()
        self._cancel[run_id] = stop
        if not await self._store.transition(
            run_id, ("QUEUED",), status="RUNNING", started_at=now(), progress=0
        ):
            return  # cancelled (or already handled) before the worker got to it
        row = await self._store.run(run_id)
        assert row is not None
        await self._stage(run_id, "loading verified dataset", 0.02)
        manifest = row["manifest"]
        spec = parse(manifest["spec"])
        record, table, definitions = await self._hub.load(row["dataset_id"])
        bars = await asyncio.to_thread(Bars.from_table, table)
        first_ts: dict[int, int] = {}
        for iid, ts in zip(bars.iid.tolist(), bars.ts.tolist(), strict=True):
            first_ts.setdefault(iid, ts)
        contract_map, contract_findings = ct.build(spec.instrument.product, first_ts, definitions)
        windows = _windows(spec, record["start"], record["end"])
        if not windows:
            raise ResearchError("NO_SESSIONS", "The dataset contains no trading session.")
        sp = validation.split(windows, spec)
        eligible = sp.insample + sp.oos
        if len(sp.insample) < validation.MIN_IS_SESSIONS:
            raise ResearchError(
                "TOO_SHORT",
                f"Only {len(sp.insample)} in-sample sessions after the split — at least "
                f"{validation.MIN_IS_SESSIONS} are needed.",
                "Build a longer dataset in the Data Hub.",
            )

        def sim(variant: FuturesSpec, ws: list[Window], **kw: Any) -> Result:
            source = kw.pop("bars", bars)
            return engine.simulate(variant, source, contract_map, ws, cancelled=stop.is_set, **kw)

        await self._stage(run_id, "simulating (conservative fills)", 0.08)
        base = await asyncio.to_thread(sim, spec, eligible)
        await self._stage(run_id, "simulating (optimistic bound)", 0.14)
        optimistic = await asyncio.to_thread(
            sim, spec, eligible, mode="optimistic", record_equity=False
        )
        checks = await asyncio.to_thread(audit, base, bars, eligible, spec)
        checks_opt = await asyncio.to_thread(audit, optimistic, bars, eligible, spec)
        audit_ok = passed(checks) and passed(checks_opt)
        counts: dict[str, int] = {}
        for s in base.sessions:
            counts[s.status] = counts.get(s.status, 0) + 1
        observed = sum(v for k, v in counts.items() if k in metrics.OBSERVED)
        fit = validation.fitness(
            record["quality"], contract_findings, spec, record["symbol"], observed, counts
        )
        is_l = sp.labels("insample")
        trial_rows: list[dict[str, Any]] = []
        trials = dict(await self._store.trial_keys(row["strategy_id"]))

        def record_trial(
            params: dict[str, float], segment_name: str, result: Result, labels: list[str]
        ) -> None:
            chosen = set(labels)
            daily = [n / float(result.capital) for _, n in metrics.daily_series(result, labels)]
            sr = validation.sharpe(daily)
            trial_rows.append(
                {
                    "strategy_id": row["strategy_id"],
                    "run_id": run_id,
                    "version_id": row["version_id"],
                    "params": params,
                    "segment": segment_name,
                    "trades": sum(1 for t in result.trades if t.session in chosen),
                    "net": round(
                        float(sum((t.net for t in result.trades if t.session in chosen), 0)), 2
                    ),
                    "sharpe_daily": sr,
                }
            )
            if segment_name in ("IS", "BASE"):
                trials.setdefault((row["version_id"], json.dumps(params, sort_keys=True)), sr)

        def trials_now() -> tuple[int, list[float]]:
            return len(trials), [v for v in trials.values() if v is not None]

        record_trial(spec.current_params(), "BASE", base, is_l)
        holdout_info: dict[str, Any] | None = None
        holdout_result: Result | None = None
        if row["include_holdout"] and sp.holdout:
            await self._stage(run_id, "evaluating the sealed holdout (once)", 0.18)
            holdout_result = await asyncio.to_thread(sim, spec, sp.holdout)
            looks = await self._store.holdout_looks(
                row["strategy_id"],
                sp.holdout[0].label.isoformat(),
                sp.holdout[-1].label.isoformat(),
            )
            if not passed(await asyncio.to_thread(audit, holdout_result, bars, sp.holdout, spec)):
                audit_ok = False
            holdout_info = {
                "evaluated": True,
                "independent": looks == 0,
                "previous": looks,
                "result": holdout_result,
            }
        tests: list[dict[str, Any]] = []
        verdict: dict[str, Any] | None = None
        if row["kind"] == "validation":
            ctx = validation.Context(
                spec=spec,
                bars=bars,
                windows=windows,
                split=sp,
                simulate=sim,
                fitness=fit,
                audit_ok=audit_ok,
                fixture=bool(record["fixture"]),
                trials_now=trials_now,
                holdout=holdout_info,
                progress=self._threadsafe_stage(run_id),
                record_trial=record_trial,
            )

            tests = await asyncio.to_thread(validation.run_suite, ctx, base, optimistic)
            verdict = validation.verdict(tests, fixture=bool(record["fixture"]))
        await self._stage(run_id, "writing ledgers and report", 0.92)
        await self._store.add_trials(trial_rows)
        summary = self._summary(
            spec,
            record,
            base,
            optimistic,
            holdout_result,
            sp,
            fit,
            checks,
            checks_opt,
            contract_map,
            contract_findings,
            tests,
            verdict,
            counts,
        )
        results_sha = await asyncio.to_thread(
            self._write_artifacts, run_id, manifest, base, sp, summary, contract_map, holdout_result
        )
        if holdout_result is not None and sp.holdout:
            await self._store.add_holdout(
                row["strategy_id"],
                spec.instrument.product,
                sp.holdout[0].label.isoformat(),
                sp.holdout[-1].label.isoformat(),
                run_id,
            )
        if stop.is_set():
            raise engine.Cancelled
        await self._store.transition(
            run_id,
            ("RUNNING",),
            status="COMPLETED",
            stage=None,
            progress=1.0,
            finished_at=now(),
            verdict=verdict["verdict"] if verdict else None,
            summary=summary,
            results_sha256=results_sha,
        )
        text = (
            f"{row['kind'].title()} complete — verdict {verdict['verdict']}"
            if verdict
            else f"Backtest complete — {len(base.trades)} trades, net "
            f"${float(base.final_equity - base.capital):,.2f} (conservative fills)"
        )
        await self._emit(text, {"run_id": run_id, "status": "COMPLETED"})

    def _summary(
        self,
        spec: FuturesSpec,
        record: dict[str, Any],
        base: Result,
        optimistic: Result,
        holdout: Result | None,
        sp: validation.Split,
        fit: dict[str, Any],
        checks: list[dict[str, Any]],
        checks_opt: list[dict[str, Any]],
        contract_map: dict[int, ct.ContractSpec],
        contract_findings: list[dict[str, Any]],
        tests: list[dict[str, Any]],
        verdict: dict[str, Any] | None,
        counts: dict[str, int],
    ) -> dict[str, Any]:
        is_l, oos_l = sp.labels("insample"), sp.labels("oos")
        segments = {
            "insample": metrics.segment(base, is_l),
            "oos": metrics.segment(base, oos_l),
        }
        if holdout is not None:
            segments["holdout"] = metrics.segment(holdout, sp.labels("holdout"))
        notional = []
        for t in base.trades:
            price = _price(t.entry_ticks, t.tick_size_fixed) or 0.0
            contract = contract_map.get(t.instrument_id)
            if contract is not None:
                notional.append(price * float(contract.multiplier) * t.contracts)
        capital = float(base.capital)
        margin = spec.sizing.initial_margin_per_contract
        return {
            "name": spec.name,
            "product": spec.instrument.product,
            "fixture": bool(record["fixture"]),
            "description": describe(spec),
            "metrics": metrics.full(base),
            "segments": segments,
            "optimistic": {
                "net_pnl": round(float(optimistic.final_equity - optimistic.capital), 2),
                "oos_net": metrics.segment(optimistic, oos_l)["net_pnl"],
            },
            "split": sp.as_dict(),
            "fitness": fit,
            "audit": {"conservative": checks, "optimistic": checks_opt},
            "contracts": [c.as_dict() for c in contract_map.values()],
            "contract_findings": contract_findings,
            "session_status": counts,
            "tests": tests,
            "verdict": verdict,
            "risk": {
                "max_notional": round(max(notional), 2) if notional else None,
                "avg_notional": round(sum(notional) / len(notional), 2) if notional else None,
                "max_leverage": round(max(notional) / capital, 3) if notional else None,
                "margin_required": (margin * spec.sizing.contracts) if margin else None,
                "margin_ok": (margin * spec.sizing.contracts <= capital) if margin else None,
                "fee_per_side": float(base.fee_per_side),
                "slippage_ticks": base.slippage_ticks,
            },
            "dataset": {
                "id": record["id"],
                "symbol": record["symbol"],
                "start": record["start"],
                "end": record["end"],
                "quality": record["quality"]["status"],
                "license": record["manifest"].get("license"),
            },
            "limitations": LIMITATIONS,
            "assumptions": [a.model_dump() for a in spec.assumptions],
        }

    def _write_artifacts(
        self,
        run_id: str,
        manifest: dict[str, Any],
        base: Result,
        sp: validation.Split,
        summary: dict[str, Any],
        contract_map: dict[int, ct.ContractSpec],
        holdout: Result | None,
    ) -> str:
        staging = self.root / "staging" / run_id
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        segment_of = {label: "IS" for label in sp.labels("insample")}
        segment_of.update({label: "OOS" for label in sp.labels("oos")})
        segment_of.update({label: "HOLDOUT" for label in sp.labels("holdout")})
        trades = [_trade_row(t, segment_of) for t in base.trades]
        if holdout is not None:
            trades += [_trade_row(t, segment_of) for t in holdout.trades]
        fills = [
            {
                "order_id": f.order_id,
                "session": f.session,
                "bar_time": _iso(f.bar_ts),
                "known_at": _iso(f.known_at_ns),
                "instrument_id": f.instrument_id,
                "side": "BUY" if f.side > 0 else "SELL",
                "contracts": f.contracts,
                "price": _price(f.ticks, contract_map[f.instrument_id].tick_size_fixed),
                "reference_price": _price(
                    f.reference_ticks, contract_map[f.instrument_id].tick_size_fixed
                ),
                "slippage_ticks": f.slippage_ticks,
                "fee": float(f.fee),
                "reason": f.reason,
                "ambiguous": f.ambiguous,
            }
            for f in base.fills
        ]
        orders = [
            {
                "id": o.id,
                "session": o.session,
                "created": _iso(o.created_ns),
                "kind": o.kind,
                "side": "BUY" if o.side > 0 else "SELL",
                "price_ticks": o.price_ticks,
                "status": o.status,
                "closed": _iso(o.closed_ns) if o.closed_ns else None,
            }
            for o in base.orders
        ]
        sessions = [
            {
                "session": s.label,
                "status": s.status,
                "instrument_id": s.instrument_id,
                "raw_symbol": s.raw_symbol,
                "trades": s.trades,
                "net": float(s.net),
                "segment": segment_of.get(s.label, "EMBARGO"),
                "early_close": s.early_close,
                "note": s.note,
            }
            for s in base.sessions
        ]
        tables = {
            "trades": trades,
            "fills": fills,
            "orders": orders,
            "sessions": sessions,
            "equity": [
                {"ts": _iso(ts), "equity": float(e)}
                for ts, e in zip(base.equity_ts, base.equity, strict=True)
            ],
        }
        for name, rows in tables.items():
            if rows:
                pq.write_table(pa.Table.from_pylist(rows), staging / f"{name}.parquet")
            else:
                (staging / f"{name}.parquet.empty").write_text("")
        results = {
            "trades": trades,
            "metrics": summary["metrics"],
            "segments": summary["segments"],
            "tests": [
                {"id": t["id"], "status": t["status"], "metric": t["metric"]}
                for t in summary["tests"]
            ],
            "verdict": summary["verdict"],
        }
        results_sha = sha256_text(canonical_json(json.loads(json.dumps(results, default=str))))
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
        (staging / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
        (staging / "report.md").write_text(report_markdown(run_id, manifest, summary, results_sha))
        final = self.root / run_id
        if final.exists():
            shutil.rmtree(final)
        os.replace(staging, final)
        return results_sha

    # Results ----------------------------------------------------------------------------

    async def _completed(self, run_id: str) -> dict[str, Any]:
        row = await self.run(run_id)
        if row["status"] != "COMPLETED":
            raise ResearchError("NOT_COMPLETED", f"The run is {row['status'].lower()}.", status=409)
        return row

    async def trades(
        self, run_id: str, offset: int = 0, limit: int = 200, segment: str | None = None
    ) -> dict[str, Any]:
        await self._completed(run_id)
        path = self.root / run_id / "trades.parquet"
        rows = pq.read_table(path).to_pylist() if path.exists() else []
        if segment:
            rows = [r for r in rows if r["segment"] == segment]
        return {"total": len(rows), "offset": offset, "rows": rows[offset : offset + limit]}

    async def trade(self, run_id: str, number: int, segment: str | None = None) -> dict[str, Any]:
        row = await self._completed(run_id)
        path = self.root / run_id / "trades.parquet"
        trades = pq.read_table(path).to_pylist() if path.exists() else []
        match = [
            t
            for t in trades
            if t["number"] == number and (segment is None or t["segment"] == segment)
        ]
        if not match:
            raise ResearchError("NOT_FOUND", "No such trade.", status=404)
        trade = match[0]
        _, table, _ = await self._hub.load(row["dataset_id"])
        spec = parse(row["manifest"]["spec"])
        entry_ns = _parse_iso(trade["entry_time"])
        exit_ns = _parse_iso(trade["exit_time"])
        window = next(
            (
                w
                for w in _windows(
                    spec, row["manifest"]["dataset"]["start"], row["manifest"]["dataset"]["end"]
                )
                if w.label.isoformat() == trade["session"]
            ),
            None,
        )
        start = window.start_ns if window else entry_ns - 30 * MINUTE
        end = window.end_ns if window else exit_ns + 30 * MINUTE
        lo_ns, hi_ns = max(start, entry_ns - 45 * MINUTE), min(end, exit_ns + 30 * MINUTE)
        ts = table.column("ts_event").to_pylist()
        import bisect

        i, j = bisect.bisect_left(ts, lo_ns), bisect.bisect_left(ts, hi_ns)
        cols = {
            k: table.column(k).to_pylist()[i:j]
            for k in ("ts_event", "open", "high", "low", "close", "volume")
        }
        bars_out = [
            {
                "t": _iso(cols["ts_event"][k]),
                "o": cols["open"][k] / NS,
                "h": cols["high"][k] / NS,
                "l": cols["low"][k] / NS,
                "c": cols["close"][k] / NS,
                "v": cols["volume"][k],
            }
            for k in range(len(cols["ts_event"]))
        ]
        fills = pq.read_table(self.root / run_id / "fills.parquet").to_pylist()
        mine = [f for f in fills if f["session"] == trade["session"]]
        sessions = pq.read_table(self.root / run_id / "sessions.parquet").to_pylist()
        session = next((s for s in sessions if s["session"] == trade["session"]), None)
        commission = spec.costs.commission_per_contract_side * trade["contracts"] * 2
        exchange = spec.costs.exchange_fees_per_contract_side * trade["contracts"] * 2
        story = _story(trade, spec)
        return {
            "trade": trade,
            "bars": bars_out,
            "fills": mine,
            "session": session,
            "range": _range_levels(spec, cols, window),
            "costs": {
                "commission": round(commission, 2),
                "exchange_fees": round(exchange, 2),
                "slippage": trade["slippage_cost"],
                "total": round(commission + exchange + trade["slippage_cost"], 2),
            },
            "story": story,
            "timezone": "Times in UTC; session rules are New York time.",
        }

    async def chart(self, run_id: str, points: int = 900) -> dict[str, Any]:
        row = await self._completed(run_id)
        equity_path = self.root / run_id / "equity.parquet"
        equity = pq.read_table(equity_path).to_pylist() if equity_path.exists() else []
        picked = _downsample([e["equity"] for e in equity], points)
        capital = float(row["manifest"]["spec"]["sizing"]["account_capital"])
        peak, curve = capital, []
        for i in picked:
            value = equity[i]["equity"]
            peak = max(peak, value)
            curve.append(
                {
                    "t": equity[i]["ts"],
                    "equity": round(value, 2),
                    "drawdown": round(value - peak, 2),
                }
            )
        _, table, _ = await self._hub.load(row["dataset_id"])
        spec = parse(row["manifest"]["spec"])
        windows = _windows(
            spec, row["manifest"]["dataset"]["start"], row["manifest"]["dataset"]["end"]
        )
        ts = table.column("ts_event").to_numpy()
        o, h, lo, c = (table.column(k).to_numpy() for k in ("open", "high", "low", "close"))
        import numpy as np

        candles = []
        for w in windows:
            a, b = (int(x) for x in np.searchsorted(ts, [w.start_ns, w.end_ns]))
            if b > a:
                candles.append(
                    {
                        "session": w.label.isoformat(),
                        "o": int(o[a]) / NS,
                        "h": int(h[a:b].max()) / NS,
                        "l": int(lo[a:b].min()) / NS,
                        "c": int(c[b - 1]) / NS,
                    }
                )
        trades = (
            pq.read_table(self.root / run_id / "trades.parquet").to_pylist()
            if (self.root / run_id / "trades.parquet").exists()
            else []
        )
        markers = [
            {
                "session": t["session"],
                "number": t["number"],
                "direction": t["direction"],
                "entry": t["entry_price"],
                "exit": t["exit_price"],
                "net": t["net"],
                "segment": t["segment"],
            }
            for t in trades
        ]
        return {
            "equity": curve,
            "candles": candles,
            "markers": markers,
            "split": row["summary"]["split"] if row["summary"] else None,
            "capital": capital,
        }

    async def report(self, run_id: str) -> str:
        await self._completed(run_id)
        return (self.root / run_id / "report.md").read_text(encoding="utf-8")

    async def reproduce(self, run_id: str) -> dict[str, Any]:
        """Recompute from the same manifest and compare result hashes (artifacts untouched)."""
        row = await self._completed(run_id)
        manifest = row["manifest"]
        spec = parse(manifest["spec"])
        record, table, definitions = await self._hub.load(row["dataset_id"])
        bars = Bars.from_table(table)
        first_ts: dict[int, int] = {}
        for iid, ts in zip(bars.iid.tolist(), bars.ts.tolist(), strict=True):
            first_ts.setdefault(iid, ts)
        contract_map, _ = ct.build(spec.instrument.product, first_ts, definitions)
        windows = _windows(spec, record["start"], record["end"])
        sp = validation.split(windows, spec)
        again = await asyncio.to_thread(
            engine.simulate, spec, bars, contract_map, sp.insample + sp.oos
        )
        segment_of = {label: "IS" for label in sp.labels("insample")}
        segment_of.update({label: "OOS" for label in sp.labels("oos")})
        rows = [_trade_row(t, segment_of) for t in again.trades]
        stored = (await self.trades(run_id, 0, 10**9))["rows"]
        stored = [r for r in stored if r["segment"] != "HOLDOUT"]
        identical = canonical_json(rows) == canonical_json(stored)
        return {
            "identical": identical,
            "trades": len(rows),
            "results_sha256": row["results_sha256"],
        }

    async def compare(self, run_ids: list[str]) -> dict[str, Any]:
        rows = []
        specs = []
        for run_id in run_ids[:6]:
            row = await self._completed(run_id)
            summary = row["summary"] or {}
            oos = (summary.get("segments") or {}).get("oos", {})
            rows.append(
                {
                    "id": run_id,
                    "name": summary.get("name"),
                    "kind": row["kind"],
                    "version": row["manifest"]["version_number"],
                    "dataset": row["dataset_id"],
                    "verdict": row["verdict"],
                    "net": summary.get("metrics", {}).get("net_pnl"),
                    "oos_net": oos.get("net_pnl"),
                    "oos_trades": oos.get("trades"),
                    "oos_sharpe": oos.get("sharpe"),
                    "max_drawdown": summary.get("metrics", {}).get("max_drawdown"),
                    "ambiguous_trades": summary.get("metrics", {}).get("ambiguous_trades"),
                    "fixture": row["fixture"],
                }
            )
            specs.append(_flatten(row["manifest"]["spec"]))
        keys = sorted({k for s in specs for k in s})
        diff = [
            {"field": k, "values": [s.get(k) for s in specs]}
            for k in keys
            if len({json.dumps(s.get(k)) for s in specs}) > 1
        ]
        return {"runs": rows, "spec_differences": diff}

    async def overview(self) -> dict[str, Any]:
        runs = await self._store.runs(20)
        return {
            "strategies": len(await self._store.strategies()),
            "runs": [_run_row(r) for r in runs[:8]],
            "active": [r["id"] for r in runs if r["status"] in ("QUEUED", "RUNNING")],
        }

    async def report_text(self) -> str:
        """For JARVIS: a factual digest of the latest research (numbers from the engine)."""
        runs = [r for r in await self._store.runs(10) if r["status"] == "COMPLETED"]
        if not runs:
            return "No completed QuantLab futures research runs yet."
        lines = []
        for r in runs[:5]:
            s = r["summary"] or {}
            oos = (s.get("segments") or {}).get("oos", {})
            verdict = (s.get("verdict") or {}).get("verdict", "no verdict (backtest only)")
            lines.append(
                f"- {s.get('name')} on {s.get('dataset', {}).get('symbol')} "
                f"({'SYNTHETIC fixture' if r['fixture'] else 'licensed data'}): {r['kind']}, "
                f"verdict {verdict}; OOS {oos.get('trades')} trades, net {oos.get('net_pnl')}, "
                f"Sharpe {oos.get('sharpe')}; fitness {s.get('fitness', {}).get('status')}."
            )
        return "Latest QuantLab futures runs (deterministic engine results):\n" + "\n".join(lines)


# helpers --------------------------------------------------------------------------------------


def _windows(spec: FuturesSpec, start: str, end: str) -> list[Window]:
    s = spec.session
    first, last = datetime.fromisoformat(start).date(), datetime.fromisoformat(end).date()
    return cal.windows(
        s.calendar,
        s.timezone,
        clock(s.start),
        clock(s.end),
        clock(s.flatten_at),
        clock(s.entry_cutoff) if s.entry_cutoff else None,
        first,
        last,
        tuple(s.weekdays),
    )


def _warnings(spec: FuturesSpec) -> list[str]:
    notes = []
    if spec.execution.slippage_ticks == 0:
        notes.append("Zero slippage is optimistic for stop and market orders.")
    if spec.costs.commission_per_contract_side + spec.costs.exchange_fees_per_contract_side == 0:
        notes.append("No costs: real trading pays commission and exchange fees.")
    if not spec.validation.parameter_grid:
        notes.append("No parameter grid: sensitivity and walk-forward can't test robustness.")
    if spec.validation.holdout_fraction == 0:
        notes.append("No sealed holdout: the strategy can never reach the top verdict.")
    return notes


def _trade_row(t: engine.Trade, segment_of: dict[str, str]) -> dict[str, Any]:
    tick = t.tick_size_fixed
    return {
        "number": t.number,
        "session": t.session,
        "segment": segment_of.get(t.session, "EMBARGO"),
        "direction": "LONG" if t.direction > 0 else "SHORT",
        "contracts": t.contracts,
        "instrument_id": t.instrument_id,
        "raw_symbol": t.raw_symbol,
        "entry_time": _iso(t.entry_bar_ts),
        "entry_price": _price(t.entry_ticks, tick),
        "exit_time": _iso(t.exit_bar_ts),
        "exit_price": _price(t.exit_ticks, tick),
        "exit_reason": t.exit_reason,
        "stop_price": _price(t.stop_ticks, tick),
        "target_price": _price(t.target_ticks, tick),
        "gross": float(t.gross),
        "fees": float(t.fees),
        "net": float(t.net),
        "slippage_cost": float(t.slippage_cost),
        "mae_ticks": t.mae_ticks,
        "mfe_ticks": t.mfe_ticks,
        "mae_usd": float(t.tick_value * t.mae_ticks * t.contracts),
        "mfe_usd": float(t.tick_value * t.mfe_ticks * t.contracts),
        "r_multiple": float(t.r_multiple) if t.r_multiple is not None else None,
        "bars_held": t.bars_held,
        "signal_known_at": _iso(t.signal_known_at),
        "ambiguous": t.ambiguous,
    }


def _run_row(r: dict[str, Any]) -> dict[str, Any]:
    summary = r.get("summary") or {}
    return {
        "id": r["id"],
        "strategy_id": r["strategy_id"],
        "version_id": r["version_id"],
        "version_number": r["manifest"].get("version_number"),
        "dataset_id": r["dataset_id"],
        "kind": r["kind"],
        "include_holdout": r["include_holdout"],
        "status": r["status"],
        "stage": r["stage"],
        "progress": r["progress"],
        "verdict": r["verdict"],
        "fixture": r["fixture"],
        "name": summary.get("name") or r["manifest"]["spec"].get("name"),
        "symbol": r["manifest"]["dataset"]["symbol"],
        "net_pnl": (summary.get("metrics") or {}).get("net_pnl"),
        "oos_net": ((summary.get("segments") or {}).get("oos") or {}).get("net_pnl"),
        "trades": (summary.get("metrics") or {}).get("trades"),
        "error_code": r["error_code"],
        "error": r["error"],
        "created_at": r["created_at"],
        "finished_at": r["finished_at"],
        "results_sha256": r["results_sha256"],
    }


def _parse_iso(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()) * NS


def _downsample(values: list[float], limit: int) -> list[int]:
    n = len(values)
    if n <= limit:
        return list(range(n))
    step = n / (limit / 2)
    picked: set[int] = {0, n - 1}
    start = 0.0
    while start < n:
        seg = range(int(start), min(n, int(start + step)))
        if seg:
            picked.add(max(seg, key=lambda i: values[i]))
            picked.add(min(seg, key=lambda i: values[i]))
        start += step
    return sorted(picked)


def _range_levels(
    spec: FuturesSpec, cols: dict[str, list[Any]], window: Window | None
) -> dict[str, Any] | None:
    rule = spec.rule
    if not isinstance(rule, OpeningRangeBreakout) or window is None:
        return None
    end = window.start_ns + rule.range_minutes * MINUTE
    picked = [k for k, t in enumerate(cols["ts_event"]) if window.start_ns <= t < end]
    if not picked:
        return None
    return {
        "high": max(cols["high"][k] for k in picked) / NS,
        "low": min(cols["low"][k] for k in picked) / NS,
        "known_at": _iso(end),
    }


def _story(trade: dict[str, Any], spec: FuturesSpec) -> list[str]:
    side = "Bought" if trade["direction"] == "LONG" else "Sold short"
    lines = [
        f"Signal information available at {trade['signal_known_at'][11:16]} UTC.",
        f"{side} {trade['contracts']} × {trade['raw_symbol']} at {trade['entry_price']:,.2f} "
        f"in the bar starting {trade['entry_time'][11:16]} UTC.",
    ]
    if trade["stop_price"] is not None:
        lines.append(f"Protective stop at {trade['stop_price']:,.2f}.")
    if trade["target_price"] is not None:
        lines.append(f"Target at {trade['target_price']:,.2f}.")
    reason = {
        "TARGET": "the target was reached",
        "STOP": "the stop was hit",
        "TIME_EXIT": f"the flatten time ({spec.session.flatten_at} New York) was reached",
        "LAST_BAR_CLOSE": "no later bar existed — closed at the last bar's close",
        "SIGNAL_EXIT": "an opposite signal",
    }.get(trade["exit_reason"], trade["exit_reason"])
    lines.append(f"Exited at {trade['exit_price']:,.2f} because {reason}.")
    if trade["ambiguous"]:
        lines.append(
            "Ambiguous: stop and target (or entry and stop) were inside the same 1-minute bar. "
            "The conservative assumption was used; the real order is unknown."
        )
    lines.append(
        f"Net {trade['net']:+,.2f} USD after {trade['fees']:.2f} fees; "
        f"{trade['slippage_cost']:.2f} of the result is modelled slippage."
    )
    return lines


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "assumptions":
                continue
            out.update(_flatten(v, f"{prefix}{k}."))
    else:
        out[prefix.rstrip(".")] = value
    return out


def report_markdown(
    run_id: str, manifest: dict[str, Any], s: dict[str, Any], results_sha: str
) -> str:
    v = s.get("verdict") or {}
    ds = s["dataset"]
    lines = [
        f"# {s['name']} — {manifest['kind']} report",
        "",
        f"Run `{run_id}` · strategy version {manifest['version_number']} · dataset `{ds['id']}` "
        f"({ds['symbol']}, {ds['start']} → {ds['end']})",
        "",
    ]
    if s.get("fixture"):
        lines += [
            "> **SYNTHETIC FIXTURE DATA** — an engineering check of the pipeline, not market "
            "evidence.",
            "",
        ]
    lines += ["## Verdict", ""]
    if v:
        lines.append(
            f"**{v['verdict'].replace('_', ' ')}**"
            + (
                f" (the gates alone would say {v['would_be'].replace('_', ' ')})"
                if v.get("would_be")
                else ""
            )
        )
        lines += [""] + [f"- {r}" for r in v.get("reasons", [])]
        if v.get("next_steps"):
            lines += ["", "Next steps:"] + [f"- {n}" for n in v["next_steps"]]
        lines += ["", f"_{v.get('never', '')}_", ""]
    else:
        lines += ["No verdict: this was a backtest. Run validation for gate-by-gate evidence.", ""]
    lines += ["## Rules as run", ""] + [f"- {d}" for d in s["description"]] + [""]
    lines += [
        "## Data",
        "",
        f"- Quality: {ds['quality']}; fitness for this strategy: {s['fitness']['status']}",
    ]
    lines += [f"  - {r['level']}: {r['message']}" for r in s["fitness"]["reasons"]]
    for c in s["contracts"]:
        lines.append(
            f"- {c['raw_symbol']}: tick {c['tick_size']} = ${c['tick_value']}, ×{c['multiplier']} "
            f"({c['provenance']})"
        )
    if ds.get("license"):
        lines.append(f"- License: {ds['license']}")
    lines += [
        "",
        "## Results (conservative fills)",
        "",
        "| | In-sample | Out-of-sample | Holdout |",
        "|---|---|---|---|",
    ]
    seg = s["segments"]
    for key, label in (
        ("trades", "Trades"),
        ("net_pnl", "Net P&L ($)"),
        ("expectancy", "Expectancy ($/trade)"),
        ("win_rate", "Win rate"),
        ("profit_factor", "Profit factor"),
        ("sharpe", "Sharpe (daily, √252)"),
        ("max_drawdown", "Max drawdown ($)"),
        ("ambiguous_trades", "Ambiguous trades"),
    ):
        cells = [
            str(seg.get(part, {}).get(key, "—")) if part in seg else "sealed"
            for part in ("insample", "oos", "holdout")
        ]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += [
        "",
        f"Optimistic bound (ambiguous bars resolved favourably): net "
        f"{s['optimistic']['net_pnl']} overall, {s['optimistic']['oos_net']} out-of-sample.",
        "",
    ]
    if s["tests"]:
        lines += ["## Validation gates", "", "| Test | Status | Reading |", "|---|---|---|"]
        for t in s["tests"]:
            lines.append(f"| {t['name']} | {t['status']} | {t['interpretation']} |")
        lines.append("")
    lines += (
        ["## Assumptions", ""]
        + [f"- {a['field']}: {a['state']} — {a['note']}" for a in s["assumptions"]]
        + ["", "## Limitations", ""]
        + [f"- {x}" for x in s["limitations"]]
    )
    lines += [
        "",
        "## Reproducibility",
        "",
        f"- Spec SHA-256: `{manifest['spec_sha256']}`",
        f"- Dataset snapshot SHA-256: `{manifest['dataset']['snapshot_sha256']}`",
        f"- Engine: {manifest['engine']['name']} {manifest['engine']['version']} "
        f"(code {manifest['engine']['code_revision']})",
        f"- Results SHA-256: `{results_sha}`",
        f"- Bootstrap seed: {manifest['seed']}",
        "",
        "Research only — QuantLab has no broker connection and places no orders.",
        "",
    ]
    return "\n".join(lines)
