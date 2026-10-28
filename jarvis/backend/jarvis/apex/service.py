"""ApexService: picks the data, runs analyses, keeps the candidate journal honest.

Data sources per market come from ``config/apex.yaml`` in order of preference:
``vault:<ROOT>`` (QuantLab Data Vault futures bars, read through checksum-verified dataset
snapshots — never a provider call) and ``dukascopy:<NAME>`` (the learning cache; with
``refresh`` the missing days are fetched — free, no key). The ``fixture`` provider replaces
all of them with generated test data, labelled SYNTHETIC (tests, E2E).

Every analysis is stored; its candidates go into an immutable journal and are resolved later
on the **same source**'s newer bars. Track records run in the background.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from jarvis.apex.audit import compare, outcome_check
from jarvis.apex.data import SYNTHETIC_LABEL, Series, from_table, synthetic_bars
from jarvis.apex.engine import ENGINE_VERSION, MarketContext, analyze
from jarvis.apex.evaluate import summarize, track_record
from jarvis.apex.resolve import resolve
from jarvis.apex.store import ApexStore
from jarvis.briefing.levels import LevelRules
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType
from jarvis.learning.market import DataError, Instrument, MarketData
from jarvis.learning.strategy import clock_minutes
from jarvis.quantlab.hub.service import DataHubService, HubError
from jarvis.quantlab.vault.service import VaultService
from jarvis.settings import Settings
from jarvis.training.dataset import TouchRules

log = logging.getLogger("jarvis.apex")

PROXY_TEXT = {
    "dukascopy:NQ": "Dukascopy USA Tech 100 CFD (bid) — a proxy for the Nasdaq-100, not the "
    "NQ/MNQ futures contract; levels can differ from the futures by the basis.",
}


class ApexError(Exception):
    def __init__(self, code: str, message: str, remedy: str = "", status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remedy = remedy
        self.status = status


def now_minute() -> int:
    return int(datetime.now(UTC).timestamp()) // 60 * 60


def market_contexts(settings: Settings) -> dict[str, MarketContext]:
    """Each configured APEX market with its level rules, session window and touch rules."""
    t = settings.training
    rules = TouchRules(
        timeframe=t.timeframe_minutes,
        horizon_minutes=t.horizon_minutes,
        tolerance=t.tolerance_atr,
        hold=t.hold_atr,
        breach=t.breach_atr,
        history_days=t.history_days,
        round_steps=t.round_steps_each_side,
    )
    out: dict[str, MarketContext] = {}
    for name, spec in settings.apex.markets.items():
        levels = settings.briefing.instruments.get(spec.levels)
        session = settings.training.sessions.get(spec.session)
        learning = settings.learning.instruments.get(spec.levels)
        if levels is None or session is None or learning is None:
            log.warning("APEX market %s skipped: levels, session or instrument missing", name)
            continue
        out[name] = MarketContext(
            name=name,
            spec=spec,
            level_rules=LevelRules(
                round_step=levels.round_step,
                major_step=levels.major_step,
                merge_within=levels.merge_within,
                regular_session=levels.regular_session,
            ),
            levels_market=spec.levels,
            window=(clock_minutes(session.start), clock_minutes(session.end)),
            touch_rules=rules,
            price_range=(float(learning.price_range[0]), float(learning.price_range[1])),
        )
    return out


class ApexService:
    def __init__(
        self,
        *,
        settings: Settings,
        market: MarketData,
        vault: VaultService,
        hub: DataHubService,
        store: ApexStore,
        bus: EventBus,
        now: Callable[[], int] = now_minute,
    ) -> None:
        self._settings = settings
        self._cfg = settings.apex
        self._market = market
        self._vault = vault
        self._hub = hub
        self.store = store
        self._bus = bus
        self._now = now
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._progress: dict[str, float] = {}
        self.contexts = market_contexts(settings)

    @property
    def fixture(self) -> bool:
        return self._cfg.provider == "fixture"

    async def start(self) -> None:
        failed = await self.store.fail_running()
        if failed:
            log.info("APEX: %d interrupted track record run(s) marked as failed", failed)

    async def stop(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()

    def _ctx(self, market: str) -> MarketContext:
        ctx = self.contexts.get(market.upper())
        if ctx is None:
            raise ApexError(
                "UNKNOWN_MARKET", f"APEX covers {', '.join(self.contexts)} — not {market}."
            )
        return ctx

    # Data -----------------------------------------------------------------------------------

    def _span(self, as_of: int, extra_days: int = 0) -> tuple[date, date]:
        days = self._cfg.history_days + self._cfg.level_days + 5 + extra_days
        first = datetime.fromtimestamp(as_of - days * 86400, UTC).date()
        return first, datetime.fromtimestamp(as_of, UTC).date()

    async def series(
        self,
        market: str,
        *,
        as_of: int,
        refresh: bool = False,
        source: str | None = None,
        extra_days: int = 0,
        through_now: bool = False,
    ) -> Series:
        """Minute bars from the first available source (or exactly ``source``). The window
        starts from ``as_of``; ``through_now`` extends it to the newest bars (re-checks)."""
        ctx = self._ctx(market)
        first, last = self._span(as_of, extra_days)
        if through_now:
            last = max(last, datetime.fromtimestamp(self._now(), UTC).date())
        if self.fixture:
            bars = await asyncio.to_thread(synthetic_bars, ctx.name, self._now(), ctx.spec.tick)
            lo = int(datetime(first.year, first.month, first.day, tzinfo=UTC).timestamp())
            return Series(
                ctx.name, "fixture", SYNTHETIC_LABEL, True, bars.between(lo, self._now() + 1),
                provenance={"source_id": "fixture", "generator": "apex.data.synthetic_bars"},
            )  # fmt: skip
        reasons: list[str] = []
        for candidate in [source] if source else ctx.spec.sources:
            kind, _, name = candidate.partition(":")
            try:
                if kind == "vault":
                    found = await self._from_vault(ctx, name, first, last, candidate)
                elif kind == "dukascopy":
                    found = await self._from_dukascopy(ctx, name, first, last, refresh, candidate)
                else:
                    reasons.append(f"{candidate}: unknown source")
                    continue
            except (DataError, HubError, ValueError, OSError) as exc:
                reasons.append(f"{candidate}: {exc}")
                continue
            if found is not None and len(found.bars):
                return found
            reasons.append(f"{candidate}: no bars for {first} → {last}")
        raise ApexError(
            "NO_DATA",
            f"No local minute data for {ctx.name}. " + "; ".join(reasons),
            "Gold: Training Hub → Research loop downloads free Dukascopy data (or press Update "
            "data). NQ/MNQ: QuantLab → Data Vault (import or a one-time Databento purchase).",
            status=409,
        )

    async def _from_dukascopy(
        self, ctx: MarketContext, name: str, first: date, last: date, refresh: bool, source_id: str
    ) -> Series | None:
        spec = self._settings.learning.instruments.get(name)
        if spec is None:
            raise ValueError(f"instrument {name} isn't in config/learning.yaml")
        instrument = Instrument(name, spec.symbol, spec.price_range, spec.code)
        note = None
        if refresh:
            try:
                bars = await self._market.recent(instrument, (last - first).days)
            except DataError as exc:
                note = f"Update failed ({exc}); using the cached days."
                bars = await asyncio.to_thread(self._market.load, instrument, first, last)
        else:
            bars = await asyncio.to_thread(self._market.load, instrument, first, last)
        if len(bars) == 0:
            return None
        label = spec.label or f"Dukascopy {spec.symbol}"
        provenance = {"source_id": source_id, "symbol": spec.symbol, "prices": "bid", "note": note}
        return Series(
            ctx.name, "dukascopy", label, False, bars, PROXY_TEXT.get(source_id), (), provenance
        )

    async def _from_vault(
        self, ctx: MarketContext, root: str, first: date, last: date, source_id: str
    ) -> Series | None:
        ready = await self._vault.readiness(
            root, 1, None, first.isoformat(), (last + timedelta(days=1)).isoformat()
        )
        if not ready["runnable"]:
            raise ValueError(ready["message"])
        described, window = ready["series"], ready["range"]
        provider, dataset, schema, stype_in, symbol = described["key"].split("|")
        built = await self._hub.build_dataset(
            {
                "provider": provider,
                "dataset": dataset,
                "schema": schema,
                "stype_in": stype_in,
                "symbol": symbol,
                "start": window["start"],
                "end": window["end"],
            }
        )
        record, table, _ = await self._hub.load(built["id"])
        bars, breaks, contracts = await asyncio.to_thread(from_table, table)
        fixture = bool(described.get("fixture"))
        proxy = (
            f"{root} prices for {ctx.name} — the same Nasdaq-100 futures; "
            f"{ctx.name} differs only in contract size."
            if root != ctx.name
            else None
        )
        return Series(
            ctx.name,
            "fixture" if fixture else ("databento" if provider == "databento" else provider),
            (SYNTHETIC_LABEL + f" ({symbol})")
            if fixture
            else str(described.get("source") or f"{dataset} {symbol}"),
            fixture,
            bars,
            proxy,
            breaks,
            provenance={
                "source_id": source_id,
                "dataset_id": built["id"],
                "snapshot_sha256": record.get("snapshot_sha256"),
                "symbol": symbol,
                "contracts": contracts,
            },
        )

    # Analysis -------------------------------------------------------------------------------

    async def markets(self) -> list[dict[str, Any]]:
        out = []
        for name, ctx in self.contexts.items():
            latest = await self.store.latest_completed(name)
            out.append(
                {
                    "market": name,
                    "label": ctx.spec.label,
                    "unit": ctx.spec.unit,
                    "tick": ctx.spec.tick,
                    "point_value": ctx.spec.point_value,
                    "sources": ["fixture"] if self.fixture else ctx.spec.sources,
                    "fixture": self.fixture,
                    "session": [ctx.window[0], ctx.window[1]],
                    "track_record": _brief(latest) if latest else None,
                }
            )
        return out

    async def analyze(
        self, market: str, *, as_of: int | None = None, refresh: bool = False
    ) -> dict[str, Any]:
        ctx = self._ctx(market)
        now = self._now()
        when = now if as_of is None else int(as_of)
        if when > now:
            raise ApexError(
                "FUTURE", "An analysis can't be made for a moment that hasn't happened."
            )
        series = await self.series(ctx.name, as_of=when, refresh=refresh and as_of is None)
        latest = await self.store.latest_completed(ctx.name)
        track = _brief(latest) if latest and latest["synthetic"] == series.synthetic else None
        result = await asyncio.to_thread(analyze, series, ctx, self._cfg, when, track_record=track)
        result["replay"] = as_of is not None
        result["data"]["source_id"] = series.provenance.get("source_id")
        analysis_id = (
            "apa-"
            + hashlib.sha256(
                f"{ctx.name}|{when}|{result['data']['sha256']}|{ENGINE_VERSION}".encode()
            ).hexdigest()[:16]
        )
        result["id"] = analysis_id
        await self.store.add_analysis(analysis_id, result)
        recorded = 0
        for c in result["candidates"]:
            c["source_id"] = result["data"]["source_id"]
            c["replay"] = result["replay"]
            recorded += int(await self.store.add_candidate(analysis_id, c))
        await self._bus.emit(
            EventType.APEX_ANALYSIS,
            message=f"APEX {ctx.name}: {len(result['zones'])} zones, "
            f"{len(result['candidates'])} candidate(s)"
            + (" — SYNTHETIC test data" if series.synthetic else ""),
            payload={"market": ctx.name, "analysis_id": analysis_id, "candidates": recorded},
        )
        await self.resolve_open(ctx.name)
        return result

    async def resolve_open(self, market: str | None = None) -> dict[str, Any]:
        """Re-check every unresolved journal candidate on its own source's newer bars."""
        open_ = await self.store.candidates(market, open_only=True, limit=1000)
        by_source: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for c in open_:
            by_source.setdefault((c["market"], str(c.get("source_id") or "")), []).append(c)
        updated = 0
        for (name, source_id), items in by_source.items():
            try:
                series = await self.series(
                    name,
                    as_of=self._now(),
                    source=None if self.fixture or not source_id else source_id,
                )
            except ApexError:
                continue
            if bool(series.synthetic) != bool(items[0].get("synthetic")):
                continue  # never judge a real candidate on test data, or the reverse
            last = int(series.bars.t[-1]) + 60 if len(series.bars) else 0
            ctx = self._ctx(name)
            for c in items:
                outcome = resolve(c, series.bars, cost_points=ctx.spec.cost_points, now=last)
                if outcome["state"] != (c.get("outcome") or {}).get("state") or outcome["final"]:
                    await self.store.set_outcome(c["id"], outcome)
                    updated += 1
                    if outcome["final"]:
                        await self._bus.emit(
                            EventType.APEX_OUTCOME,
                            message=f"APEX {name} {c['direction']} candidate: {outcome['state']}",
                            payload={
                                "candidate_id": c["id"],
                                "state": outcome["state"],
                                "r": outcome["r"],
                            },
                        )
        return {"checked": len(open_), "updated": updated}

    async def journal(self, market: str | None = None, limit: int = 100) -> dict[str, Any]:
        items = await self.store.candidates(market, limit=limit)
        stats: dict[str, Any] = {}
        for synthetic in (False, True):
            done = [
                {**(c["outcome"] or {}), "day": int(c["created_at"]) // 86400}
                for c in items
                if bool(c.get("synthetic")) == synthetic and (c.get("outcome") or {}).get("final")
            ]
            stats["synthetic" if synthetic else "real"] = summarize(done, self._cfg.min_samples)
        return {"candidates": items, "stats": stats}

    # SENTINEL re-check ----------------------------------------------------------------------

    async def audit(self, analysis_id: str) -> dict[str, Any]:
        """Recompute a stored analysis from the data now on disk and re-judge its candidates."""
        stored = await self.store.analysis(analysis_id)
        if stored is None:
            raise ApexError("NOT_FOUND", "No such analysis.", status=404)
        ctx = self._ctx(stored["market"])
        audit_id = "apu-" + secrets.token_hex(8)
        source_id = stored["data"].get("source_id")
        try:
            series = await self.series(
                ctx.name,
                as_of=int(stored["as_of"]),
                source=None if self.fixture or not source_id else str(source_id),
                through_now=True,
            )
        except ApexError as exc:
            row = {"checks": [{"code": "DATA", "passed": False, "detail": exc.message}]}
            return await self._record_audit(audit_id, stored, False, row, "")
        if series.synthetic != stored["data"]["synthetic"]:
            row = {
                "checks": [
                    {
                        "code": "SOURCE",
                        "passed": False,
                        "detail": "Test data and real data can't be compared.",
                    }
                ]
            }
            return await self._record_audit(audit_id, stored, False, row, series.label)
        recomputed = await asyncio.to_thread(analyze, series, ctx, self._cfg, int(stored["as_of"]))
        checks = compare(stored, recomputed)
        recorded = [
            c
            for c in await self.store.candidates(ctx.name, limit=1000)
            if c.get("analysis_id") == analysis_id
        ]
        last = int(series.bars.t[-1]) + 60 if len(series.bars) else 0
        rejudged = {
            c["id"]: resolve(c, series.bars, cost_points=ctx.spec.cost_points, now=last)
            for c in recorded
        }
        checks.append(outcome_check(recorded, rejudged))
        passed = all(c["passed"] for c in checks)
        return await self._record_audit(audit_id, stored, passed, {"checks": checks}, series.label)

    async def _record_audit(
        self,
        audit_id: str,
        stored: dict[str, Any],
        passed: bool,
        result: dict[str, Any],
        label: str,
    ) -> dict[str, Any]:
        result = {**result, "recomputed_at": self._now(), "data_source": label}
        await self.store.add_audit(audit_id, stored["id"], stored["market"], passed, result)
        await self._bus.emit(
            EventType.APEX_AUDIT,
            message=f"SENTINEL re-checked APEX {stored['market']} analysis: "
            + ("all checks passed" if passed else "a check failed"),
            payload={"audit_id": audit_id, "analysis_id": stored["id"], "passed": passed},
        )
        found = await self.store.audit(audit_id)
        assert found is not None
        return found

    async def hq_tasks(self, since: str) -> list[dict[str, Any]]:
        """APEX work for the ULTRON HQ: CIPHER's track records, SENTINEL's re-checks."""
        return await self.store.hq_rows(since)

    # Track record ---------------------------------------------------------------------------

    async def evaluate(self, market: str, *, days: int | None = None) -> dict[str, Any]:
        ctx = self._ctx(market)
        if ctx.name in self._tasks and not self._tasks[ctx.name].done():
            raise ApexError(
                "RUNNING", f"A track record for {ctx.name} is already running.", status=409
            )
        span = min(max(int(days or self._cfg.history_days), 10), 730)
        now = self._now()
        end = datetime.fromtimestamp(now, UTC).date() - timedelta(days=1)
        start = end - timedelta(days=span)
        series = await self.series(
            ctx.name, as_of=now, extra_days=max(0, span - self._cfg.history_days)
        )
        evaluation_id = (
            "ape-"
            + hashlib.sha256(
                f"{ctx.name}|{start}|{end}|{series.digest()}|{now}".encode()
            ).hexdigest()[:16]
        )
        await self.store.add_evaluation(
            {
                "id": evaluation_id,
                "market": ctx.name,
                "template": "zone_reaction",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "synthetic": series.synthetic,
            }
        )
        self._progress[evaluation_id] = 0.0
        self._tasks[ctx.name] = asyncio.create_task(
            self._run(evaluation_id, series, ctx, start, end)
        )
        await self._bus.emit(
            EventType.APEX_EVALUATION,
            message=f"APEX track record started — {ctx.name} {start} → {end}",
            payload={"evaluation_id": evaluation_id, "state": "RUNNING"},
        )
        return await self.evaluation(evaluation_id)

    async def _run(
        self, evaluation_id: str, series: Series, ctx: MarketContext, start: date, end: date
    ) -> None:
        def progress(value: float) -> None:
            self._progress[evaluation_id] = value

        try:
            result = await asyncio.to_thread(
                track_record, series, ctx, self._cfg, start, end, progress=progress
            )
            await self.store.update_evaluation(
                evaluation_id, state="COMPLETED", progress=1.0, result=result
            )
            message = f"APEX track record {ctx.name}: {result['verdict']['text']}"
        except Exception as exc:  # a failed run is reported, never retried silently
            log.exception("APEX evaluation failed")
            await self.store.update_evaluation(evaluation_id, state="FAILED", error=str(exc)[:500])
            message = f"APEX track record {ctx.name} failed: {exc}"
        self._progress.pop(evaluation_id, None)
        await self._bus.emit(
            EventType.APEX_EVALUATION, message=message, payload={"evaluation_id": evaluation_id}
        )

    async def evaluation(self, evaluation_id: str) -> dict[str, Any]:
        row = await self.store.evaluation(evaluation_id)
        if row is None:
            raise ApexError("NOT_FOUND", "No such track record.", status=404)
        if evaluation_id in self._progress:
            row["progress"] = round(self._progress[evaluation_id], 3)
        return row

    async def evaluations(self, market: str | None = None) -> list[dict[str, Any]]:
        rows = await self.store.evaluations(market)
        for row in rows:
            if row["id"] in self._progress:
                row["progress"] = round(self._progress[row["id"]], 3)
        return rows

    async def wait_idle(self, timeout: float = 120.0) -> None:  # noqa: ASYNC109
        tasks = [t for t in self._tasks.values() if not t.done()]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout)


def _brief(evaluation: dict[str, Any]) -> dict[str, Any]:
    """What a candidate shows of its template's track record."""
    result = evaluation.get("result") or {}
    oos = result.get("out_of_sample", {})
    whole = result.get("all", {})
    return {
        "evaluation_id": evaluation["id"],
        "period": [evaluation["start"], evaluation["end"]],
        "synthetic": evaluation["synthetic"],
        "data": (result.get("data") or {}).get("label"),
        "verdict": result.get("verdict"),
        "filled": whole.get("filled"),
        "win_rate": whole.get("win_rate"),
        "expectancy_r": (whole.get("expectancy_r") or {}).get("mean"),
        "oos_filled": oos.get("filled"),
        "oos_expectancy_r": (oos.get("expectancy_r") or {}).get("mean"),
        "oos_ci": (oos.get("expectancy_r") or {}).get("ci"),
    }
