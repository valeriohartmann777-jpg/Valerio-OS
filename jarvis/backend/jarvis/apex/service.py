"""ApexService: picks the data, runs analyses, keeps the candidate journal honest.

Data sources per market come from ``config/apex.yaml`` in order of preference:
``vault:<ROOT>`` (QuantLab Data Vault futures bars, read through checksum-verified dataset
snapshots — never a provider call) and ``dukascopy:<NAME>`` (the learning cache; with
``refresh`` the missing days are fetched — free, no key). The ``fixture`` provider replaces
all of them with generated test data, labelled SYNTHETIC (tests, E2E).

Every analysis is stored; its candidates go into an immutable journal and are resolved later
on the **same source**'s newer bars. Track records run in the background.

Data status and repair (``docs/apex/APEX_DATA_DIAGNOSIS.md``): every update's outcome is shown
with the analysis; ``data_status`` lists the cache's coverage, suspicious days, the latest
update reports and concrete causes with repair actions; ``repair`` re-checks every trading day
without candles and downloads what is missing (free Dukascopy data only).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np

from jarvis.apex.audit import compare, outcome_check
from jarvis.apex.data import (
    SYNTHETIC_LABEL,
    Series,
    from_table,
    measure_spread,
    synthetic_bars,
    trading,
    trading_minutes,
)
from jarvis.apex.engine import ENGINE_VERSION, STRATEGIES, MarketContext, analyze
from jarvis.apex.evaluate import Cancelled, summarize, track_record
from jarvis.apex.execution import Execution, execution_for, fill_model
from jarvis.apex.narrative import narrate
from jarvis.apex.resolve import resolve
from jarvis.apex.signal import ValidationState, from_analysis
from jarvis.apex.store import ApexStore
from jarvis.apex.validation import (
    describe_split,
    develop,
    holdout_test,
    plan_split,
    trading_days,
    verdict,
)
from jarvis.briefing.levels import LevelRules
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType
from jarvis.learning.market import Bars, DataError, Instrument, MarketData, resample
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
        self._repair_tasks: dict[str, asyncio.Task[None]] = {}
        self._repairs: dict[str, dict[str, Any]] = {}
        self._validations: dict[str, asyncio.Task[None]] = {}  # market → running validation
        self._cancel: dict[str, threading.Event] = {}  # validation id → its cancel flag
        # The series the latest analyses saw (for their charts): analysis id → bars until then.
        self._seen: dict[str, Series] = {}
        self.contexts = market_contexts(settings)

    @property
    def fixture(self) -> bool:
        return self._cfg.provider == "fixture"

    async def start(self) -> None:
        failed = await self.store.fail_running()
        if failed:
            log.info("APEX: %d interrupted track record run(s) marked as failed", failed)
        failed = await self.store.fail_running_validations()
        if failed:
            log.info("APEX: %d interrupted validation run(s) marked as failed", failed)

    async def stop(self) -> None:
        for flag in self._cancel.values():
            flag.set()
        for task in [
            *self._tasks.values(),
            *self._repair_tasks.values(),
            *self._validations.values(),
        ]:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._repair_tasks.clear()
        self._validations.clear()

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
                    found = await self._from_dukascopy(
                        ctx, name, first, last, refresh, candidate, as_of
                    )
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

    def _instrument(self, name: str) -> Instrument:
        spec = self._settings.learning.instruments.get(name)
        if spec is None:
            raise ValueError(f"instrument {name} isn't in config/learning.yaml")
        return Instrument(name, spec.symbol, spec.price_range, spec.code)

    async def _from_dukascopy(
        self,
        ctx: MarketContext,
        name: str,
        first: date,
        last: date,
        refresh: bool,
        source_id: str,
        as_of: int,
    ) -> Series | None:
        instrument = self._instrument(name)
        update: dict[str, Any] = {"requested": refresh, "ok": None, "error": None}
        if refresh:
            try:
                bars = await self._market.recent(instrument, (last - first).days)
                update["ok"] = True
            except DataError as exc:
                update["ok"], update["error"] = False, str(exc)
                bars = await asyncio.to_thread(self._market.load, instrument, first, last)
        else:
            bars = await asyncio.to_thread(self._market.load, instrument, first, last)
        reports = [r for r in self._market.reports(instrument) if r.get("kind") == "sync"]
        update["report"] = reports[-1] if reports else None
        if len(bars) == 0:
            if update["error"]:
                raise DataError(f"the update failed ({update['error']}) and nothing is cached")
            return None
        spread = await self._spread(instrument, bars, ctx, refresh, as_of)
        spec = self._settings.learning.instruments[name]
        label = spec.label or f"Dukascopy {spec.symbol}"
        provenance = {
            "source_id": source_id,
            "symbol": spec.symbol,
            "prices": "bid",
            "cache": str(self._market.path(instrument)),
            "update": update,
            "spread": spread,
        }
        return Series(
            ctx.name, "dukascopy", label, False, bars, PROXY_TEXT.get(source_id), (), provenance
        )

    async def _spread(
        self, instrument: Instrument, bid: Bars, ctx: MarketContext, refresh: bool, as_of: int
    ) -> dict[str, Any]:
        """The measured bid/ask spread of the recent days (Dukascopy also has ask candles)."""
        ask = instrument.with_side("ASK")
        days = self._cfg.spread_days
        end = datetime.fromtimestamp(as_of, UTC).date()
        error = None
        if refresh:
            try:
                ask_bars = await self._market.recent(ask, days)
            except DataError as exc:
                error = str(exc)
                ask_bars = await asyncio.to_thread(
                    self._market.load, ask, end - timedelta(days=days), end
                )
        else:
            ask_bars = await asyncio.to_thread(
                self._market.load, ask, end - timedelta(days=days), end
            )
        out = await asyncio.to_thread(
            measure_spread,
            bid,
            ask_bars,
            start=as_of - days * 86400,
            end=as_of,
            window=ctx.window,
            cost_points=ctx.spec.cost_points,
        )
        return {**out, "days": days, "error": error}

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
        runs: dict[str, dict[str, Any]] = {}
        for name in STRATEGIES:
            found = await self.store.latest_completed(ctx.name, name)
            if found and found["synthetic"] == series.synthetic:
                runs[name] = found
        tracks = {name: _brief(run) for name, run in runs.items()}
        result = await asyncio.to_thread(
            lambda: analyze(series, ctx, self._cfg, when, track_records=tracks)
        )
        result["replay"] = as_of is not None
        result["data"]["source_id"] = series.provenance.get("source_id")
        provenance = result["data"]["provenance"]
        result["data"]["update"] = provenance.get("update")
        result["data"]["spread"] = provenance.get("spread")
        result["data"]["provenance"] = {
            k: v for k, v in provenance.items() if k not in ("update", "spread")
        }
        analysis_id = (
            "apa-"
            + hashlib.sha256(
                f"{ctx.name}|{when}|{result['data']['sha256']}|{ENGINE_VERSION}".encode()
            ).hexdigest()[:16]
        )
        result["id"] = analysis_id
        calibration = None
        if "zone_reaction" in runs:  # the zone rates' calibration is measured with that run
            checked = (runs["zone_reaction"].get("result") or {}).get("calibration") or {}
            calibration = (checked.get("verdict") or {}).get("code")
        result["narrative"] = narrate(result, calibration)
        states, evidence = await self._validation_states(ctx.name, series.synthetic)
        result["validation_ids"] = evidence
        for c in result["candidates"]:
            c["source_id"] = result["data"]["source_id"]
            c["replay"] = result["replay"]
        signals = from_analysis(result, states, created_at=datetime.fromtimestamp(now, UTC))
        result["signals"] = [sig.as_record() for sig in signals]
        self._remember(analysis_id, series.until(when))
        await self.store.add_analysis(analysis_id, result)
        recorded = 0
        for c in result["candidates"]:
            recorded += int(await self.store.add_candidate(analysis_id, c))
        for record in result["signals"]:
            await self.store.add_signal(
                analysis_id, record, synthetic=series.synthetic, replay=result["replay"]
            )
        await self._bus.emit(
            EventType.APEX_ANALYSIS,
            message=f"APEX {ctx.name}: {len(result['zones'])} zones, "
            f"{len(result['candidates'])} candidate(s)"
            + (" — SYNTHETIC test data" if series.synthetic else ""),
            payload={"market": ctx.name, "analysis_id": analysis_id, "candidates": recorded},
        )
        await self.resolve_open(ctx.name)
        return result

    async def _validation_states(
        self, market: str, synthetic: bool
    ) -> tuple[dict[str, ValidationState], dict[str, str]]:
        """Each strategy's validation state for its current version (UNVALIDATED until a
        validation run of this version on this kind of data finished)."""
        verdicts = await self.store.verdicts(market, synthetic)
        states: dict[str, ValidationState] = {}
        evidence: dict[str, str] = {}
        for name, version in STRATEGIES.items():
            row = verdicts.get(f"{name}@{version}")
            if row is None:
                continue
            state = str(((row.get("result") or {}).get("verdict") or {}).get("validation_state"))
            if state in ValidationState.__members__:
                states[name] = ValidationState(state)
                evidence[name] = row["id"]
        return states, evidence

    async def signals(
        self, market: str | None = None, *, strategy: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await self.store.signals(
            self._ctx(market).name if market else None, strategy=strategy, limit=limit
        )

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
                costs, spread = fill_model(c, ctx.spec.cost_points)
                outcome = resolve(c, series.bars, cost_points=costs, now=last, spread=spread)
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

    # Chart ----------------------------------------------------------------------------------

    def _remember(self, analysis_id: str, known: Series) -> None:
        self._seen[analysis_id] = known
        while len(self._seen) > 6:  # a few markets' latest analyses
            self._seen.pop(next(iter(self._seen)))

    async def chart(self, analysis_id: str, minutes: int, limit: int = 300) -> dict[str, Any]:
        """Bars of an analysis at another timeframe — only bars known at its moment. A last
        bar still forming then is marked ``forming``; ``same_data`` says whether the bars are
        the ones the analysis saw (the cache may have healed since)."""
        if minutes not in CHART_MINUTES:
            raise ApexError(
                "TIMEFRAME", f"Charts come in {', '.join(map(str, CHART_MINUTES))} minutes."
            )
        stored = await self.store.analysis(analysis_id)
        if stored is None:
            raise ApexError("NOT_FOUND", "No such analysis.", status=404)
        as_of = int(stored["as_of"])
        known = self._seen.get(analysis_id)
        if known is None:
            source_id = stored["data"].get("source_id")
            series = await self.series(
                stored["market"],
                as_of=as_of,
                source=None if self.fixture or not source_id else str(source_id),
            )
            known = series.until(as_of)
            self._remember(analysis_id, known)
        limit = max(20, min(int(limit), 1000))
        # one bar more than shown, so a first bucket cut in half falls off
        span = min(len(known.bars), (limit + 1) * minutes)
        tail = known.bars.between(int(known.bars.t[-span]), as_of + 1) if span else known.bars
        bars = await asyncio.to_thread(resample, tail, minutes) if minutes > 1 and span else tail
        forming = bool(len(bars)) and int(bars.t[-1]) + 60 * minutes > as_of
        cut = slice(max(0, len(bars) - limit), len(bars))
        rows = [
            [int(t), float(o), float(h), float(lo), float(c), float(v)]
            for t, o, h, lo, c, v in zip(
                bars.t[cut], bars.open[cut], bars.high[cut], bars.low[cut], bars.close[cut],
                bars.volume[cut], strict=True,
            )
        ]  # fmt: skip
        return {
            "analysis_id": analysis_id,
            "market": stored["market"],
            "minutes": minutes,
            "as_of": as_of,
            "label": known.label,
            "synthetic": known.synthetic,
            "prices": known.provenance.get("prices") or "trades",
            "same_data": known.digest() == stored["data"]["sha256"],
            "forming": forming,
            "bars": rows,
        }

    # Data status and repair -----------------------------------------------------------------

    async def data_status(self, market: str) -> dict[str, Any]:
        """Where each source's data stands, why an analysis may be blocked, and what fixes it."""
        ctx = self._ctx(market)
        now = self._now()
        first, last = self._span(now)
        out: dict[str, Any] = {
            "market": ctx.name,
            "checked_at": now,
            "fixture": self.fixture,
            "span": [first.isoformat(), last.isoformat()],
            "market_open": bool(trading(np.array([now - 60]))[0]),
            "sources": [],
            "causes": [],
            "repair": self._repairs.get(ctx.name),
        }
        if self.fixture:
            series = await self.series(ctx.name, as_of=now)
            newest = int(series.bars.t[-1]) + 60 if len(series.bars) else None
            out["sources"].append(
                {
                    "id": "fixture",
                    "kind": "fixture",
                    "label": SYNTHETIC_LABEL,
                    "state": "ok",
                    "bars": len(series.bars),
                    "newest_bar": newest,
                }
            )
            out["causes"].append(
                _cause(
                    "SYNTHETIC",
                    "INFO",
                    "Generated test data (tests and demos) — nothing to repair, and nothing it "
                    "shows says anything about the market.",
                )
            )
            return out
        for source_id in ctx.spec.sources:
            kind, _, name = source_id.partition(":")
            try:
                if kind == "dukascopy":
                    found = await self._dukascopy_status(ctx, name, source_id, first, last, now)
                elif kind == "vault":
                    found = await self._vault_status(name, source_id, first, last)
                else:
                    continue
            except (DataError, HubError, ValueError, OSError) as exc:
                found = {"id": source_id, "kind": kind, "state": "error", "message": str(exc)}
            out["sources"].append(found)
        out["active"] = next(
            (s["id"] for s in out["sources"] if s["state"] in ("ok", "warn", "error")), None
        )
        out["causes"] = _causes(ctx, out["sources"], out["active"])
        return out

    async def _dukascopy_status(
        self, ctx: MarketContext, name: str, source_id: str, first: date, last: date, now: int
    ) -> dict[str, Any]:
        instrument = self._instrument(name)
        yesterday = last - timedelta(days=1)
        coverage = await asyncio.to_thread(self._market.coverage, instrument)
        days = await asyncio.to_thread(self._market.days, instrument, first, yesterday)
        newest = None
        behind = None
        if coverage:
            tail = await asyncio.to_thread(self._market.load, instrument, coverage[1], coverage[1])
            if len(tail):
                newest = int(tail.t[-1]) + 60
                today_start = int(datetime(last.year, last.month, last.day, tzinfo=UTC).timestamp())
                behind = len(trading_minutes(newest, today_start))
        counts: dict[str, int] = {}
        for d in days:
            counts[d["state"]] = counts.get(d["state"], 0) + 1
        suspicious = _ranges([d for d in days if d["state"] in SUSPICIOUS])
        reports = self._market.reports(instrument)
        syncs = [r for r in reports if r.get("kind") == "sync"]
        bid = (
            await asyncio.to_thread(
                self._market.load, instrument, last - timedelta(days=self._cfg.spread_days), last
            )
            if coverage
            else None
        )
        spread = None
        if bid is not None and len(bid):
            ask = await asyncio.to_thread(
                self._market.load,
                instrument.with_side("ASK"),
                last - timedelta(days=self._cfg.spread_days),
                last,
            )
            spread = measure_spread(
                bid, ask, start=now - self._cfg.spread_days * 86400, end=now,
                window=ctx.window, cost_points=ctx.spec.cost_points,
            )  # fmt: skip
        last_report = reports[-1] if reports else None
        if coverage is None:
            state = "none"
        elif (last_report and last_report.get("error")) or (behind or 0) > 1380:
            state = "error"
        elif suspicious or (spread and spread.get("exceeds_costs")):
            state = "warn"
        else:
            state = "ok"
        spec = self._settings.learning.instruments[name]
        return {
            "id": source_id,
            "kind": "dukascopy",
            "label": spec.label or f"Dukascopy {spec.symbol}",
            "prices": "bid",
            "proxy": PROXY_TEXT.get(source_id),
            "state": state,
            "cache": str(self._market.path(instrument)),
            "coverage": {
                "first": coverage[0].isoformat(),
                "last": coverage[1].isoformat(),
                "bars": coverage[2],
            }
            if coverage
            else None,
            "newest_bar": newest,
            "behind_minutes": behind,
            "days": counts,
            "suspicious": suspicious,
            "last_update": last_report,
            "last_sync": syncs[-1] if syncs else None,
            "updates": reports[-8:],
            "spread": spread,
        }

    async def _vault_status(
        self, root: str, source_id: str, first: date, last: date
    ) -> dict[str, Any]:
        ready = await self._vault.readiness(
            root, 1, None, first.isoformat(), (last + timedelta(days=1)).isoformat()
        )
        described = ready.get("series") or {}
        return {
            "id": source_id,
            "kind": "vault",
            "label": described.get("source") or f"Data Vault {root}",
            "prices": "trades",
            "state": "ok" if ready["runnable"] else "none",
            "readiness": ready["status"],
            "message": ready["message"],
            "action": ready.get("action"),
            "coverage": {"first": described.get("start"), "last": described.get("end")}
            if described
            else None,
        }

    async def repair(self, market: str) -> dict[str, Any]:
        """Re-check every trading day without candles in the analysis span and download what is
        missing (bid, and ask for the spread), in the background. Free Dukascopy data only —
        Data Vault sources are never bought from here."""
        ctx = self._ctx(market)
        if self.fixture:
            return {
                "state": "COMPLETED",
                "fixture": True,
                "steps": [],
                "message": "Generated test data needs no repair.",
            }
        task = self._repair_tasks.get(ctx.name)
        if task and not task.done():
            return self._repairs[ctx.name]
        self._repairs[ctx.name] = {
            "state": "RUNNING",
            "started": self._now(),
            "finished": None,
            "steps": [],
            "message": None,
        }
        self._repair_tasks[ctx.name] = asyncio.create_task(self._repair(ctx))
        return self._repairs[ctx.name]

    async def _repair(self, ctx: MarketContext) -> None:
        state = self._repairs[ctx.name]
        first, last = self._span(self._now())
        yesterday = last - timedelta(days=1)
        try:
            for source_id in ctx.spec.sources:
                kind, _, name = source_id.partition(":")
                if kind != "dukascopy":
                    state["steps"].append(
                        {
                            "source": source_id,
                            "ok": None,
                            "skipped": "Data Vault data is imported or bought in QuantLab → Data "
                            "Vault; APEX never downloads or buys it.",
                        }
                    )
                    continue
                instrument = self._instrument(name)
                spread_from = yesterday - timedelta(days=self._cfg.spread_days)
                for item, start in (
                    (instrument, first),
                    (instrument.with_side("ASK"), spread_from),
                ):
                    step: dict[str, Any] = {
                        "source": source_id,
                        "side": item.side,
                        "range": [start.isoformat(), last.isoformat()],
                        "ok": None,
                        "error": None,
                    }
                    try:
                        await self._market.sync(item, start, yesterday, recheck=True)
                        await self._market.today(item)
                        step["ok"] = True
                    except DataError as exc:
                        step["ok"], step["error"] = False, str(exc)
                    syncs = [r for r in self._market.reports(item) if r.get("kind") == "sync"]
                    step["report"] = syncs[-1] if syncs else None
                    state["steps"].append(step)
            failed = [s for s in state["steps"] if s.get("ok") is False]
            state["state"] = "FAILED" if failed else "COMPLETED"
            state["message"] = (
                failed[0]["error"]
                if failed
                else "Every trading day without candles was asked again; the cache is up to date."
            )
        except Exception as exc:  # reported, never retried silently
            log.exception("APEX data repair failed")
            state["state"], state["message"] = "FAILED", str(exc)[:500]
        state["finished"] = self._now()
        await self._bus.emit(
            EventType.APEX_DATA,
            message=f"APEX {ctx.name} data repair: {state['state'].lower()} — {state['message']}",
            payload={"market": ctx.name, "state": state["state"]},
        )

    async def wait_repair(self, market: str, timeout: float = 60.0) -> None:  # noqa: ASYNC109
        task = self._repair_tasks.get(market.upper())
        if task and not task.done():
            await asyncio.wait_for(asyncio.shield(task), timeout)

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
        # The execution model is an input of the analysis (like the settings): the stored one.
        recorded_execution = stored.get("execution")
        recomputed = await asyncio.to_thread(
            analyze,
            series,
            ctx,
            self._cfg,
            int(stored["as_of"]),
            execution=Execution.from_dict(recorded_execution) if recorded_execution else None,
        )
        checks = compare(stored, recomputed)
        recorded = [
            c
            for c in await self.store.candidates(ctx.name, limit=1000)
            if c.get("analysis_id") == analysis_id
        ]
        last = int(series.bars.t[-1]) + 60 if len(series.bars) else 0
        rejudged = {}
        for c in recorded:
            costs, spread = fill_model(c, ctx.spec.cost_points)
            rejudged[c["id"]] = resolve(c, series.bars, cost_points=costs, now=last, spread=spread)
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

    async def evaluate(
        self, market: str, *, days: int | None = None, strategy: str = "zone_reaction"
    ) -> dict[str, Any]:
        ctx = self._ctx(market)
        if strategy not in STRATEGIES:
            raise ApexError("UNKNOWN_STRATEGY", f"APEX strategies: {', '.join(STRATEGIES)}.")
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
        execution = execution_for(series, ctx.spec)
        if not execution.tradable:
            raise ApexError("DATA_UNAVAILABLE", execution.reason, status=409)
        evaluation_id = (
            "ape-"
            + hashlib.sha256(
                f"{ctx.name}|{strategy}|{start}|{end}|{series.digest()}|{now}".encode()
            ).hexdigest()[:16]
        )
        await self.store.add_evaluation(
            {
                "id": evaluation_id,
                "market": ctx.name,
                "template": strategy,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "synthetic": series.synthetic,
            }
        )
        self._progress[evaluation_id] = 0.0
        self._tasks[ctx.name] = asyncio.create_task(
            self._run(evaluation_id, series, ctx, start, end, strategy, execution)
        )
        await self._bus.emit(
            EventType.APEX_EVALUATION,
            message=f"APEX track record started — {ctx.name} {start} → {end}",
            payload={"evaluation_id": evaluation_id, "state": "RUNNING"},
        )
        return await self.evaluation(evaluation_id)

    async def _run(
        self,
        evaluation_id: str,
        series: Series,
        ctx: MarketContext,
        start: date,
        end: date,
        strategy: str,
        execution: Execution,
    ) -> None:
        def progress(value: float) -> None:
            self._progress[evaluation_id] = value

        try:
            result = await asyncio.to_thread(
                lambda: track_record(
                    series, ctx, self._cfg, start, end,
                    strategy=strategy, execution=execution, progress=progress,
                )
            )  # fmt: skip
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

    # Validation (docs/APEX_VALIDATION_PROTOCOL.md) ------------------------------------------

    async def validate(self, market: str, strategy: str) -> dict[str, Any]:
        """Start the pre-registered validation of ``strategy`` on ``market`` (background)."""
        ctx = self._ctx(market)
        if strategy not in STRATEGIES:
            raise ApexError("UNKNOWN_STRATEGY", f"APEX strategies: {', '.join(STRATEGIES)}.")
        running = self._validations.get(ctx.name)
        if running and not running.done():
            raise ApexError(
                "RUNNING", f"A validation for {ctx.name} is already running.", status=409
            )
        protocol = self._settings.apex_validation
        now = self._now()
        series = await self.series(ctx.name, as_of=now)
        execution = execution_for(series, ctx.spec)
        if not execution.tradable:
            raise ApexError("DATA_UNAVAILABLE", execution.reason, status=409)
        source_id = str(series.provenance.get("source_id") or series.source)
        locked = await self.store.holdout(ctx.name, source_id, series.synthetic)
        try:
            split = plan_split(
                trading_days(series), protocol, (locked["start"], locked["end"]) if locked else None
            )
        except ValueError as exc:
            raise ApexError("TOO_LITTLE_DATA", str(exc), status=409) from None
        if locked is None:  # the first validation on this data freezes the holdout
            await self.store.lock_holdout(ctx.name, source_id, series.synthetic, *split["holdout"])
        version = STRATEGIES[strategy]
        validation_id = "apv-" + secrets.token_hex(8)  # every run is its own trial
        await self.store.add_validation(
            {
                "id": validation_id,
                "market": ctx.name,
                "strategy": strategy,
                "version": version,
                "protocol_version": protocol.protocol_version,
                "source_id": source_id,
                "synthetic": series.synthetic,
                "data_sha256": series.digest(),
            }
        )
        self._progress[validation_id] = 0.0
        self._cancel[validation_id] = threading.Event()
        self._validations[ctx.name] = asyncio.create_task(
            self._validate(validation_id, series, ctx, strategy, execution, split, source_id)
        )
        await self._bus.emit(
            EventType.APEX_VALIDATION,
            message=f"APEX validation started — {strategy} {version} on {ctx.name}",
            payload={"validation_id": validation_id, "state": "RUNNING"},
        )
        return await self.validation(validation_id)

    async def _validate(
        self,
        validation_id: str,
        series: Series,
        ctx: MarketContext,
        strategy: str,
        execution: Execution,
        split: dict[str, Any],
        source_id: str,
    ) -> None:
        protocol = self._settings.apex_validation
        version = STRATEGIES[strategy]
        family = f"{ctx.name}:{strategy}"
        seed = int(series.digest()[:8], 16)
        flag = self._cancel[validation_id]

        def progress(value: float) -> None:
            self._progress[validation_id] = round(0.95 * value, 3)

        try:
            prior = len(await self.store.trials(family, series.synthetic))
            dev = await asyncio.to_thread(
                lambda: develop(
                    series, ctx, self._cfg, protocol, strategy, execution, split,
                    prior_trials=prior, seed=seed, progress=progress, cancelled=flag.is_set,
                )
            )  # fmt: skip
            registered = _parameters(self._cfg)
            for variant in dev["variants"]:
                row = next((n for n in dev["neighbours"] if n["change"] == variant), dev["stats"])
                await self.store.add_trial(
                    {
                        "family": family,
                        "strategy": strategy,
                        "version": version,
                        "kind": "neighbour" if variant else "registered",
                        "params": {**registered, **variant},
                        "validation_id": validation_id,
                        "data_sha256": series.digest(),
                        "synthetic": series.synthetic,
                        "summary": {"filled": row["filled"], "mean_r": row["mean_r"]},
                    }
                )
            held: dict[str, Any] | None = None
            if dev["passed"]:
                opened = await self.store.holdout_opened(
                    ctx.name, source_id, series.synthetic, strategy, version
                )
                if opened:  # opened once per version: the first result stands
                    held = {**opened["result"], "opened_by": opened["validation_id"]}
                else:
                    held = await asyncio.to_thread(
                        lambda: holdout_test(
                            series, ctx, self._cfg, protocol, strategy, execution, split,
                            seed=seed, cancelled=flag.is_set,
                        )
                    )  # fmt: skip
                    await self.store.open_holdout(
                        {
                            "market": ctx.name,
                            "source_id": source_id,
                            "synthetic": series.synthetic,
                            "strategy": strategy,
                            "version": version,
                            "validation_id": validation_id,
                            "result": held,
                        }
                    )
                    held = {**held, "opened_by": validation_id}
            judged = verdict(dev, held, synthetic=series.synthetic)
            result = {
                "protocol_version": protocol.protocol_version,
                "strategy": strategy,
                "version": version,
                "engine": ENGINE_VERSION,
                "market": ctx.name,
                "data": {
                    "source_id": source_id,
                    "label": series.label,
                    "synthetic": series.synthetic,
                    "sha256": series.digest(),
                },
                "execution": execution.as_dict(),
                "parameters": registered,
                "split": describe_split(split),
                "development": dev,
                "holdout": held,
                "verdict": judged,
            }
            await self.store.update_validation(
                validation_id, state="COMPLETED", progress=1.0, result=result
            )
            message = f"APEX validation {strategy} on {ctx.name}: {judged['validation_state']}"
        except Cancelled:
            await self.store.update_validation(validation_id, state="CANCELLED", error="Cancelled.")
            message = f"APEX validation {strategy} on {ctx.name} cancelled"
        except Exception as exc:  # reported, never retried silently
            log.exception("APEX validation failed")
            await self.store.update_validation(validation_id, state="FAILED", error=str(exc)[:500])
            message = f"APEX validation {strategy} on {ctx.name} failed: {exc}"
        self._progress.pop(validation_id, None)
        self._cancel.pop(validation_id, None)
        await self._bus.emit(
            EventType.APEX_VALIDATION, message=message, payload={"validation_id": validation_id}
        )

    async def cancel_validation(self, validation_id: str) -> dict[str, Any]:
        flag = self._cancel.get(validation_id)
        if flag is None:
            raise ApexError("NOT_RUNNING", "This validation isn't running.", status=409)
        flag.set()
        return await self.validation(validation_id)

    async def validation(self, validation_id: str) -> dict[str, Any]:
        row = await self.store.validation(validation_id)
        if row is None:
            raise ApexError("NOT_FOUND", "No such validation.", status=404)
        if validation_id in self._progress:
            row["progress"] = self._progress[validation_id]
        return row

    async def validations(self, market: str | None = None) -> list[dict[str, Any]]:
        rows = await self.store.validations(self._ctx(market).name if market else None)
        out = []
        for row in rows:
            if row["id"] in self._progress:
                row["progress"] = self._progress[row["id"]]
            result = row.get("result") or {}
            dev = result.get("development") or {}
            row["result"] = (
                {k: v for k, v in result.items() if k != "development"}
                | {"development": {k: v for k, v in dev.items() if k != "ledger"}}
                if result
                else None
            )
            out.append(row)
        return out

    async def trials(self, market: str, strategy: str) -> dict[str, Any]:
        ctx = self._ctx(market)
        family = f"{ctx.name}:{strategy}"
        return {
            "family": family,
            "real": await self.store.trials(family, False),
            "synthetic": await self.store.trials(family, True),
        }

    async def wait_validation(self, market: str, timeout: float = 300.0) -> None:  # noqa: ASYNC109
        task = self._validations.get(market.upper())
        if task and not task.done():
            await asyncio.wait_for(asyncio.shield(task), timeout)

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


def _parameters(cfg: Any) -> dict[str, Any]:
    """The registered strategy parameters a validation judged (from config/apex.yaml)."""
    keys = (
        "reach_atr", "stop_buffer_atr", "min_rr", "max_stop_atr", "valid_minutes",
        "trigger_valid_minutes", "max_hold_minutes", "zone_width_atr", "eval_step_minutes",
    )  # fmt: skip
    return {k: getattr(cfg, k) for k in keys}


def _brief(evaluation: dict[str, Any]) -> dict[str, Any]:
    """What a candidate shows of its template's track record."""
    result = evaluation.get("result") or {}
    oos = result.get("out_of_sample", {})
    whole = result.get("all", {})
    calibration = result.get("calibration")
    return {
        "evaluation_id": evaluation["id"],
        "template": evaluation.get("template"),
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
        "calibration": {
            "code": calibration["verdict"]["code"],
            "skill": calibration.get("skill"),
            "touches": calibration.get("touches", 0),
        }
        if calibration
        else None,
    }


CHART_MINUTES = (1, 5, 15, 60, 240)
# Day states in the cache that mean "a trading day without candles".
SUSPICIOUS = ("empty", "missing", "pending", "stored_empty", "not_fetched")


def _cause(code: str, level: str, text: str, actions: list[str] | None = None) -> dict[str, Any]:
    return {"code": code, "level": level, "text": text, "actions": actions or []}


def _ranges(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Consecutive suspicious days (Saturdays don't break a range) → from, to, count, states."""
    out: list[dict[str, Any]] = []
    for d in days:
        day = date.fromisoformat(d["day"])
        if out:
            prev = date.fromisoformat(out[-1]["to"])
            gap = (day - prev).days
            if gap == 1 or (gap == 2 and (prev + timedelta(days=1)).weekday() == 5):
                out[-1]["to"] = d["day"]
                out[-1]["days"] += 1
                out[-1]["states"][d["state"]] = out[-1]["states"].get(d["state"], 0) + 1
                continue
        out.append({"from": d["day"], "to": d["day"], "days": 1, "states": {d["state"]: 1}})
    return out


def _remedy(error: str) -> str:
    """What the owner can do about a failed update, by its reason."""
    text = error.lower()
    if "refused" in text:
        return (
            "Dukascopy refuses requests from this computer (HTTP 401/403). A VPN, proxy or "
            "firewall can cause it — check the connection, then press Repair."
        )
    if "can't reach" in text:
        return (
            "No connection to Dukascopy's data API (jetta.dukascopy.com). Check the internet "
            "connection, then press Repair."
        )
    if "in a row" in text:
        return (
            "Dukascopy answered without candles for several trading days. A short outage "
            "usually: Repair asks again. If it stays, the API changed — tell JARVIS."
        )
    if "minute candles" in text or "api may have changed" in text:
        return (
            "Dukascopy's answer format changed. The decoder refuses it, so no wrong prices get "
            "in — this needs a code update."
        )
    if "expected range" in text:
        return "Prices outside price_range in config/learning.yaml — check the configured range."
    return "Press Repair to try again; the reason above says what went wrong."


def _causes(
    ctx: MarketContext, sources: list[dict[str, Any]], active: str | None
) -> list[dict[str, Any]]:
    """Concrete causes, most important first, each with the actions that fix it."""
    causes: list[dict[str, Any]] = []
    if active is None:
        gold = ctx.name == "XAUUSD"
        causes.append(
            _cause(
                "NO_LOCAL_DATA",
                "BLOCK",
                f"No local minute data for {ctx.name}. "
                + (
                    "Repair downloads the free Dukascopy history (about two minutes)."
                    if gold
                    else "Import NQ/MNQ futures in the Data Vault, or let Repair download the "
                    "free Dukascopy Nasdaq-100 CFD as a labelled proxy."
                ),
                ["repair"] if gold else ["vault", "repair"],
            )
        )
        return causes
    source = next(s for s in sources if s["id"] == active)
    if source["kind"] == "dukascopy":
        update = source.get("last_update") or {}
        if update.get("error"):
            causes.append(
                _cause(
                    "UPDATE_FAILED",
                    "BLOCK" if (source.get("behind_minutes") or 0) > 1380 else "WARN",
                    f"The last update failed: {update['error']} {_remedy(update['error'])}",
                    ["repair"],
                )
            )
        behind = source.get("behind_minutes") or 0
        if behind > 1380:
            causes.append(
                _cause(
                    "CACHE_BEHIND",
                    "BLOCK",
                    f"The cache ends {source['coverage']['last']} — {behind // 1380} trading "
                    "day(s) before today are missing, so the newest bars are old. Repair asks "
                    "Dukascopy for every missing day.",
                    ["repair"],
                )
            )
        holes = source.get("suspicious") or []
        if holes:
            days = sum(h["days"] for h in holes)
            listed = ", ".join(
                h["from"] if h["from"] == h["to"] else f"{h['from']} to {h['to']}"
                for h in holes[:4]
            )
            causes.append(
                _cause(
                    "MISSING_DAYS",
                    "WARN",
                    f"{days} trading day(s) without candles in the analysis span ({listed}"
                    f"{' …' if len(holes) > 4 else ''}). Holidays look like this too; Repair "
                    "asks for every one again.",
                    ["repair"],
                )
            )
        spread = source.get("spread") or {}
        if spread.get("measured") and spread.get("exceeds_costs"):
            causes.append(
                _cause(
                    "SPREAD",
                    "WARN",
                    f"The measured bid/ask spread in the session (median "
                    f"{spread['session_median']:g}) is above the configured round-trip costs "
                    f"({spread['cost_points']:g}) — results look better than they are. Raise "
                    "cost_points in config/apex.yaml.",
                )
            )
        elif not spread.get("measured"):
            causes.append(
                _cause(
                    "SPREAD_UNMEASURED",
                    "INFO",
                    "The bid/ask spread isn't measured: "
                    + str(spread.get("reason") or "there are no recent bid candles.")
                    + " Repair downloads the missing bid and ask candles.",
                    ["repair"],
                )
            )
        if source.get("proxy"):
            causes.append(_cause("PROXY", "WARN", source["proxy"], ["vault"]))
    if not [c for c in causes if c["level"] in ("BLOCK", "WARN")]:
        newest = next((s.get("coverage") for s in sources if s["id"] == active), None) or {}
        causes.insert(
            0,
            _cause(
                "OK",
                "OK",
                f"Data complete through {newest.get('last', '—')}; today's bars are fetched "
                "with each analysis.",
            ),
        )
    return causes
