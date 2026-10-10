"""Databento behind a narrow, typed adapter.

Only metadata calls are used to connect and to quote; ``download`` is the one
billable call and the hub only reaches it with an approved quote. The SDK is
synchronous, so every call runs in a worker thread, one at a time (provider
rate limits, and the SDK's warnings are captured per call).

Signatures were taken from the installed SDK (``databento`` 0.87.0):
``metadata.list_datasets/list_schemas/get_dataset_range/get_dataset_condition/
get_cost/get_billable_size/get_record_count``, ``symbology.resolve``,
``timeseries.get_range(..., path=...)``.
"""

from __future__ import annotations

import asyncio
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from jarvis.observability.logging import redact

T = TypeVar("T")

KEY_PATTERN = re.compile(r"^db-[A-Za-z0-9]{10,64}$")
SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-_ /:]{0,39}$")
STYPES = ("continuous", "raw_symbol", "parent", "instrument_id")
MAX_SYMBOLS = 5

# code -> (what happened, what to do)
ERRORS: dict[str, tuple[str, str]] = {
    "AUTH_INVALID": (
        "Databento rejected the API key.",
        "Copy the key again from the Databento portal (API keys) and paste it here.",
    ),
    "NO_ENTITLEMENT": (
        "The key works, but this account isn't licensed for that dataset or schema.",
        "Check your Databento subscriptions or pick a dataset the account can access.",
    ),
    "UNSUPPORTED_SCHEMA": (
        "That schema isn't available for this dataset.",
        "Pick one of the schemas listed for the dataset.",
    ),
    "RANGE_UNAVAILABLE": (
        "The requested dates are outside the dataset's available range.",
        "Stay within the start and end dates shown for the dataset.",
    ),
    "SYMBOL_NOT_FOUND": (
        "Databento couldn't resolve that symbol for these dates.",
        "Check the symbol and symbology type (e.g. NQ.v.0 is continuous, NQM6 raw).",
    ),
    "INVALID_REQUEST": ("Databento refused the request.", "Check the parameters and try again."),
    "RATE_LIMITED": ("Databento is rate-limiting requests.", "Wait a minute and try again."),
    "QUOTA_EXCEEDED": (
        "A Databento usage limit or budget was reached.",
        "Check the limits on your Databento account.",
    ),
    "PROVIDER_ERROR": (
        "Databento had a server problem.",
        "Try again later; check status.databento.com.",
    ),
    "NETWORK": (
        "Databento can't be reached from this computer.",
        "Check the internet connection, VPN or firewall, then try again.",
    ),
    "SDK_MISSING": (
        "The Databento SDK isn't installed.",
        "Update JARVIS (the update installs it) and restart.",
    ),
}


class ProviderError(Exception):
    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        status: int | None = None,
        case: str | None = None,
    ) -> None:
        message, remedy = ERRORS.get(code, ERRORS["INVALID_REQUEST"])
        super().__init__(message)
        self.code = code
        self.message = message
        self.remedy = remedy
        self.detail = redact(detail)[:500]
        self.status = status
        self.case = case

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "remedy": self.remedy,
            "detail": self.detail,
            "http_status": self.status,
        }


def classify(exc: BaseException, secret: str = "") -> ProviderError:
    """Map SDK, HTTP and network failures onto the hub's error codes."""

    def clean(text: str) -> str:
        return redact(text.replace(secret, "[REDACTED]") if secret else text)

    status = getattr(exc, "http_status", None)
    if isinstance(status, int):
        body = getattr(exc, "json_body", None)
        detail = body.get("detail") if isinstance(body, dict) else None
        case = str(detail.get("case", "")) if isinstance(detail, dict) else ""
        text = clean(str(getattr(exc, "message", None) or exc))
        hint = f"{case} {text}".lower()
        if status == 401:
            code = "AUTH_INVALID"
        elif status in (402,) or any(w in hint for w in ("budget", "quota", "limit_exceeded")):
            code = "QUOTA_EXCEEDED"
        elif status == 403:
            code = "NO_ENTITLEMENT"
        elif status == 408:
            code = "NETWORK"
        elif status == 429:
            code = "RATE_LIMITED"
        elif status >= 500:
            code = "PROVIDER_ERROR"
        elif "schema" in hint:
            code = "UNSUPPORTED_SCHEMA"
        elif "symbol" in hint:
            code = "SYMBOL_NOT_FOUND"
        elif any(w in hint for w in ("start", "end", "range", "available", "date")):
            code = "RANGE_UNAVAILABLE"
        else:
            code = "INVALID_REQUEST"
        return ProviderError(code, text, status=status, case=case or None)
    name = type(exc).__name__
    module = type(exc).__module__
    if isinstance(exc, (ConnectionError, TimeoutError)) or module.startswith(
        ("requests", "aiohttp", "urllib3", "socket")
    ):
        return ProviderError("NETWORK", clean(f"{name}: {exc}"))
    if isinstance(exc, ValueError):
        return ProviderError("INVALID_REQUEST", clean(str(exc)))
    return ProviderError("PROVIDER_ERROR", clean(f"{name}: {exc}"))


@dataclass(frozen=True)
class Estimate:
    cost_usd: float
    billable_bytes: int
    records: int
    warnings: tuple[str, ...]


def check_symbols(symbols: list[str]) -> list[str]:
    """Explicit symbols only: the SDK treats an empty list or ALL_SYMBOLS as *everything*."""
    clean = [s.strip() for s in symbols if s and s.strip()]
    if not clean:
        raise ProviderError("INVALID_REQUEST", "At least one symbol is required.")
    if len(clean) > MAX_SYMBOLS:
        raise ProviderError("INVALID_REQUEST", f"At most {MAX_SYMBOLS} symbols per request.")
    for symbol in clean:
        if symbol.upper() == "ALL_SYMBOLS" or not SYMBOL_PATTERN.match(symbol):
            raise ProviderError("INVALID_REQUEST", f"Symbol '{symbol[:40]}' isn't allowed.")
    return clean


def _historical(key: str) -> Any:
    try:
        import databento
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ProviderError("SDK_MISSING", str(exc)) from None
    return databento.Historical(key=key)


def sdk_version() -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("databento")
    except PackageNotFoundError:  # pragma: no cover
        return None


class DatabentoAdapter:
    provider = "databento"

    def __init__(
        self, factory: Callable[[str], Any] | None = None, *, fixture_label: str | None = None
    ) -> None:
        self._factory = factory or _historical
        self.fixture_label = fixture_label  # set → an offline fixture, never real data
        self._lock = asyncio.Lock()

    async def _call(self, key: str, fn: Callable[[Any], T]) -> tuple[T, list[str]]:
        def work() -> tuple[T, list[str]]:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                client = self._factory(key)
                value = fn(client)
            return value, [redact(str(w.message))[:300] for w in caught]

        async with self._lock:
            try:
                return await asyncio.to_thread(work)
            except ProviderError:
                raise
            except Exception as exc:
                raise classify(exc, key) from None

    async def verify(self, key: str) -> list[str]:
        """An authenticated metadata request; never a billable download."""
        datasets, _ = await self._call(key, lambda c: c.metadata.list_datasets())
        return sorted(str(d) for d in datasets)

    async def schemas(self, key: str, dataset: str) -> list[str]:
        value, _ = await self._call(key, lambda c: c.metadata.list_schemas(dataset=dataset))
        return [str(s) for s in value]

    async def dataset_range(self, key: str, dataset: str) -> dict[str, Any]:
        raw, _ = await self._call(key, lambda c: c.metadata.get_dataset_range(dataset=dataset))
        out: dict[str, Any] = {
            "start": str(raw.get("start", "")),
            "end": str(raw.get("end", "")),
            "schemas": {},
        }
        per_schema = raw.get("schema")
        if isinstance(per_schema, dict):
            out["schemas"] = {
                str(k): {"start": str(v.get("start", "")), "end": str(v.get("end", ""))}
                for k, v in per_schema.items()
                if isinstance(v, dict)
            }
        return out

    async def condition(
        self, key: str, dataset: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        rows, _ = await self._call(
            key,
            lambda c: c.metadata.get_dataset_condition(
                dataset=dataset, start_date=start.isoformat(), end_date=end.isoformat()
            ),
        )
        return [
            {
                "date": str(r.get("date")),
                "condition": str(r.get("condition")),
                "last_modified_date": r.get("last_modified_date"),
            }
            for r in rows
        ]

    async def resolve(
        self, key: str, dataset: str, symbols: list[str], stype_in: str, start: date, end: date
    ) -> dict[str, Any]:
        symbols = check_symbols(symbols)
        raw, warns = await self._call(
            key,
            lambda c: c.symbology.resolve(
                dataset=dataset,
                symbols=symbols,
                stype_in=stype_in,
                stype_out="instrument_id",
                start_date=start.isoformat(),
                end_date=end.isoformat(),
            ),
        )
        result = raw.get("result") or {}
        return {
            "mappings": {
                str(sym): [
                    {"start": str(m.get("d0")), "end": str(m.get("d1")), "id": str(m.get("s"))}
                    for m in intervals
                ]
                for sym, intervals in result.items()
            },
            "not_found": [str(s) for s in raw.get("not_found") or []],
            "partial": [str(s) for s in raw.get("partial") or []],
            "warnings": warns,
        }

    async def estimate(
        self,
        key: str,
        *,
        dataset: str,
        schema: str,
        stype_in: str,
        symbols: list[str],
        start: date,
        end: date,
    ) -> Estimate:
        symbols = check_symbols(symbols)
        args = {
            "dataset": dataset,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "symbols": symbols,
            "schema": schema,
            "stype_in": stype_in,
        }

        def query(c: Any) -> tuple[float, int, int]:
            cost = float(c.metadata.get_cost(**args))
            size = int(c.metadata.get_billable_size(**args))
            records = int(c.metadata.get_record_count(**args))
            return cost, size, records

        (cost, size, records), warns = await self._call(key, query)
        return Estimate(cost, size, records, tuple(warns))

    async def download(
        self,
        key: str,
        *,
        dataset: str,
        schema: str,
        stype_in: str,
        symbols: list[str],
        start: date,
        end: date,
        path: Path,
    ) -> list[str]:
        """The billable call. Writes DBN to ``path``; returns provider warnings."""
        symbols = check_symbols(symbols)
        _, warns = await self._call(
            key,
            lambda c: c.timeseries.get_range(
                dataset=dataset,
                start=start.isoformat(),
                end=end.isoformat(),
                symbols=symbols,
                schema=schema,
                stype_in=stype_in,
                stype_out="instrument_id",
                path=str(path),
            ),
        )
        return warns
