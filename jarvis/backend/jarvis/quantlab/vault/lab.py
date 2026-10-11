"""The Backtest Lab: Strategy × Symbol × Timeframe → a fully local, deterministic run.

The owner picks a saved, versioned strategy, a market (NQ, MNQ, ES, MES) and a timeframe
(1m … 1h) and presses Backtest. This module:

1. asks the Data Vault whether exactly that market is ready locally at that timeframe
   (and whether the strategy can be measured on it);
2. builds — or reuses — an immutable snapshot of the local data (no provider call: the
   hub's dataset builder only reads the local cache and verifies every checksum);
3. queues the existing research run with the timeframe, the symbol binding and the cost
   profile as part of the run's identity (same inputs → the same run, never a second one).

Nothing in this module can reach a market-data provider; acquisition lives in
``acquire.py`` behind its own approval gate. Changing the timeframe never triggers a
purchase — missing data is reported with the exact symbols, ranges and schema instead.
"""

from __future__ import annotations

from typing import Any

from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.quantlab.futures import timeframes as tfs
from jarvis.quantlab.futures.service import ResearchError, ResearchService
from jarvis.quantlab.futures.spec import FuturesSpecError, describe, parse
from jarvis.quantlab.futures.store import ResearchStore
from jarvis.quantlab.hub import cache
from jarvis.quantlab.hub.service import PROVIDER, DataHubService, HubError
from jarvis.quantlab.vault.instruments import is_market, markets
from jarvis.quantlab.vault.service import (
    DBN_FILE_LICENSE,
    IMPORT_LICENSE,
    VaultError,
    VaultService,
)

ADVANCED_KEYS = {
    "contracts",
    "account_capital",
    "direction",
    "commission_per_contract_side",
    "exchange_fees_per_contract_side",
    "slippage_ticks",
    "limit_fill",
}


class LabError(Exception):
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


class BacktestLab:
    def __init__(
        self,
        *,
        vault: VaultService,
        hub: DataHubService,
        research: ResearchService,
        store: ResearchStore,
        bus: EventBus,
    ) -> None:
        self._vault = vault
        self._hub = hub
        self._research = research
        self._store = store
        self._bus = bus

    async def _emit(self, message: str, payload: dict[str, Any]) -> None:
        await self._bus.emit(
            EventType.QUANTLAB_BACKTEST,
            message=message,
            source="quantlab",
            severity=Severity.INFO,
            payload=payload,
        )

    # Options ------------------------------------------------------------------------------

    async def options(self) -> dict[str, Any]:
        strategies = []
        for row in await self._research.strategies():
            version_id = row.get("latest_version_id")
            version = await self._store.version(version_id) if version_id else None
            if version is None:
                continue
            try:
                spec = parse(version["spec"])
            except FuturesSpecError:
                continue
            latest = row.get("latest_run") or {}
            strategies.append(
                {
                    "id": row["id"],
                    "name": spec.name,
                    "product": spec.instrument.product,
                    "rule": spec.rule.type,
                    "session": f"{spec.session.start}–{spec.session.end} New York",
                    "versions": row["versions"],
                    "version_id": version_id,
                    "version_number": version["number"],
                    "spec_sha256": version["spec_sha256"],
                    "state": "DRAFT" if spec.unresolved else "READY",
                    "summary": describe(spec)[:3],
                    "last_verdict": latest.get("verdict"),
                    "last_run": latest or None,
                    "created_at": row.get("created_at"),
                }
            )
        series = await self._vault.series()
        offered = []
        for market in markets():
            mine = [s for s in series if s["root"] == market["symbol"]]
            offered.append(
                {
                    **market,
                    "local": [
                        {
                            k: s[k]
                            for k in (
                                "source",
                                "symbol",
                                "start",
                                "end",
                                "schema",
                                "grade",
                                "fixture",
                                "sessions_with_bars",
                            )
                        }
                        for s in mine
                    ],
                }
            )
        return {
            "strategies": strategies,
            "markets": offered,
            "timeframes": [{"minutes": m, "label": tfs.label(m)} for m in tfs.TIMEFRAMES],
            "cost_profiles": [
                {"id": key, "label": p["label"], "note": p["note"], "products": p["products"]}
                for key, p in tfs.COST_PROFILES.items()
            ],
            "offline": self._vault.offline,
            "network": "Backtests read only local data — no provider request, no data fee.",
        }

    # Readiness ------------------------------------------------------------------------------

    async def readiness(
        self,
        product: str,
        version_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        if not is_market(product):
            raise LabError("UNKNOWN_MARKET", f"{product} isn't in the market catalogue.")
        spec = None
        if version_id:
            version = await self._store.version(version_id)
            if version is None:
                raise LabError("NOT_FOUND", "No such strategy version.", status=404)
            spec = parse(version["spec"])
        series = await self._vault.series()
        rows = [
            await self._vault.readiness(product, m, spec, start, end, all_series=series)
            for m in tfs.TIMEFRAMES
        ]
        return {"product": product, "version_id": version_id, "timeframes": rows}

    # Launch -------------------------------------------------------------------------------

    async def launch(self, body: dict[str, Any]) -> dict[str, Any]:
        version_id = body.get("version_id")
        if not version_id and body.get("strategy_id"):
            strategy = await self._research.strategy(str(body["strategy_id"]))
            versions = strategy.get("versions") or []
            version_id = versions[-1]["id"] if versions else None
        if not version_id:
            raise LabError("STRATEGY_REQUIRED", "Choose a saved strategy.")
        version = await self._store.version(str(version_id))
        if version is None:
            raise LabError("NOT_FOUND", "No such strategy version.", status=404)
        spec = parse(version["spec"])
        product = str(body.get("symbol") or "")
        if not is_market(product):
            raise LabError("UNKNOWN_MARKET", "Choose a market: NQ, MNQ, ES or MES.")
        try:
            minutes = tfs.parse_timeframe(body.get("timeframe", "1m"))
        except ValueError as exc:
            raise LabError("TIMEFRAME", str(exc)) from None
        advanced = dict(body.get("advanced") or {})
        start, end = advanced.pop("start", None), advanced.pop("end", None)
        cost_profile = advanced.pop("cost_profile", None)
        kind = str(advanced.pop("kind", None) or body.get("kind") or "backtest")
        overrides = {k: v for k, v in advanced.items() if v not in (None, "")}
        unknown = set(overrides) - ADVANCED_KEYS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise LabError("ADVANCED", f"Unknown advanced setting(s): {names}.")
        ready = await self._vault.readiness(product, minutes, spec, start, end)
        if not ready["runnable"]:
            raise LabError(
                ready["status"],
                ready["message"],
                ready.get("action", ""),
                status=409,
                readiness=ready,
            )
        series, window = ready["series"], ready["range"]
        key = series["key"]
        provider, dataset, schema, stype_in, symbol = key.split("|")
        request = {
            "provider": provider,
            "dataset": dataset,
            "schema": schema,
            "stype_in": stype_in,
            "symbol": symbol,
            "start": window["start"],
            "end": window["end"],
        }
        try:
            ds = await self._hub.build_dataset(request, await self._provenance(key, provider))
        except HubError as exc:
            if exc.code in ("CACHE_TAMPERED", "SNAPSHOT_TAMPERED"):
                await self._vault.flag_corrupt(key, exc.message)
                raise LabError(
                    "INVALID",
                    "The local data failed its checksum check and won't be used.",
                    "Open Data Vault → Audit / Repair.",
                    status=409,
                ) from None
            raise LabError(exc.code, exc.message, exc.remedy, status=exc.status) from None
        try:
            run = await self._research.start_run(
                str(version_id),
                ds["id"],
                kind=kind,
                timeframe=minutes,
                binding={
                    "product": product,
                    "cost_profile": cost_profile,
                    "overrides": overrides,
                },
            )
        except ResearchError as exc:
            raise LabError(exc.code, exc.message, exc.remedy, status=exc.status) from None
        await self._emit(
            f"Backtest started — {spec.name} on {product} · {tfs.label(minutes)} (local data)",
            {"run_id": run["id"], "dataset_id": ds["id"]},
        )
        return {
            "run": run,
            "readiness": ready,
            "dataset": {
                "id": ds["id"],
                "provider": ds["provider"],
                "symbol": ds["symbol"],
                "start": ds["start"],
                "end": ds["end"],
                "fixture": ds["fixture"],
                "quality": ds["quality"]["status"],
            },
            "network": "none — local data only",
        }

    async def _provenance(self, key: str, provider: str) -> dict[str, Any] | None:
        if provider == PROVIDER:
            return None  # Databento downloads carry their own acquisition record
        imports = [
            i
            for i in await self._vault.imports()
            if i["status"] == "COMMITTED"
            and cache.cache_key(
                i["provider"] or "", i["dataset"] or "", i["schema"] or "", i["stype_in"] or "",
                i["symbol"] or "",
            ) == key
        ]  # fmt: skip
        names: dict[str, str] = {}
        for i in imports:
            names.update((i.get("mapping") or {}).get("contracts") or {})
        return {
            "source": {
                "kind": provider,
                "authoritative": False,
                "imports": [
                    {
                        "id": i["id"],
                        "filename": i["filename"],
                        "sha256": i["raw_sha256"],
                        "format": i["format"],
                        "committed_at": i["committed_at"],
                    }
                    for i in imports
                ],
            },
            "contract_names": names,
            "license": DBN_FILE_LICENSE if provider == "databento-file" else IMPORT_LICENSE,
        }


__all__ = ["BacktestLab", "LabError", "VaultError"]
