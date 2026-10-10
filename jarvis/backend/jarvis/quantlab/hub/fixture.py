"""An offline stand-in for ``databento.Historical`` — for tests and the E2E run only.

It answers the same metadata, symbology and timeseries calls and writes **real
DBN files** (encoded with ``databento_dbn``), so the whole ingestion path is
exercised with the SDK's own decoder. Its data is synthetic: a seeded random
walk on CME Globex hours (exchange_calendars ``CMES``) for NQ, MNQ, ES and MES
quarterly contracts, with a carry premium between contracts so a continuous
symbol jumps at the roll exactly like real unadjusted data does. Its prices
are made up too. Everything it produces is labelled FIXTURE in the hub.

Behaviour switches (by key text, for tests): a key containing ``REVOKED`` is
rejected (401); ``NOLICENSE`` is valid but not licensed for GLBX.MDP3 (403);
``OFFLINE`` behaves like a network failure.
"""

from __future__ import annotations

import hashlib
import io
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np

LABEL = "Offline fixture provider — synthetic data in Databento's format, NOT Databento"
VALID_KEY = "db-FIXTURE0000000000000000000000"

DATASET = "GLBX.MDP3"
UNLICENSED = "XNAS.ITCH"
AVAILABLE_START = date(2025, 1, 2)
AVAILABLE_END = date(2026, 7, 1)  # exclusive
SCHEMAS = ["ohlcv-1m", "ohlcv-1h", "ohlcv-1d", "definition", "trades", "mbp-1", "tbbo"]
GENERATED = {"ohlcv-1m", "definition"}
RECORD_BYTES = {"ohlcv-1m": 56, "definition": 520}
USD_PER_GB = {"ohlcv-1m": 600.0, "definition": 200.0}  # FIXTURE prices, not Databento's
DEGRADED_DAYS = {date(2026, 2, 17)}
NANOS = 1_000_000_000
TICK = 250_000_000  # 0.25 in DBN fixed precision (1e-9)
MONTHS = "HMUZ"


@dataclass(frozen=True)
class Product:
    root: str
    family: str  # NQ/MNQ share one index path, ES/MES another
    multiplier: int
    tick_value_fixed: int
    base: float
    id_base: int
    rth_volume: int


PRODUCTS = {
    "NQ": Product("NQ", "NDX", 20, 5 * NANOS, 21_000.0, 42_000, 600),
    "MNQ": Product("MNQ", "NDX", 2, NANOS // 2, 21_000.0, 52_000, 2_400),
    "ES": Product("ES", "SPX", 50, 12_500_000_000, 6_000.0, 43_000, 1_500),
    "MES": Product("MES", "SPX", 5, 1_250_000_000, 6_000.0, 53_000, 5_000),
}


@dataclass(frozen=True)
class Contract:
    product: Product
    year: int
    month: int  # 3, 6, 9, 12

    @property
    def raw_symbol(self) -> str:
        return f"{self.product.root}{MONTHS[self.month // 3 - 1]}{self.year % 10}"

    @property
    def instrument_id(self) -> int:
        return self.product.id_base + (self.year % 100) * 10 + self.month // 3

    @property
    def expiration(self) -> datetime:
        first = date(self.year, self.month, 1)
        friday = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
        return datetime(friday.year, friday.month, friday.day, 13, 30, tzinfo=UTC)

    @property
    def roll_date(self) -> date:
        """The UTC date from which the volume-ranked front (``v.0``) is the next contract."""
        return self.expiration.date() - timedelta(days=7)

    @property
    def activation(self) -> datetime:
        return self.expiration - timedelta(days=365)


def contracts(product: Product) -> list[Contract]:
    return [Contract(product, y, m) for y in (2024, 2025, 2026, 2027) for m in (3, 6, 9, 12)]


def front(product: Product, day: date, rule: str) -> Contract:
    """Point-in-time front contract on a UTC date (rule c: by expiry, v/n: by roll date)."""
    for contract in contracts(product):
        boundary = (
            contract.expiration.date() + timedelta(days=1) if rule == "c" else (contract.roll_date)
        )
        if day < boundary:
            return contract
    raise ValueError("outside the fixture's contract list")


def _seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8], "little")


@cache
def _sessions() -> list[tuple[date, int, int]]:
    """CME Globex equity sessions as (label, open ns, close ns), UTC."""
    import exchange_calendars as xc

    cal = xc.get_calendar(
        "CMES",
        start=(AVAILABLE_START - timedelta(days=10)).isoformat(),
        end=(AVAILABLE_END + timedelta(days=10)).isoformat(),
    )
    out = []
    for label, row in cal.schedule.iterrows():
        out.append((label.date(), int(row["open"].value), int(row["close"].value)))
    return out


@cache
def _day_levels(family: str) -> dict[date, float]:
    """Daily opening index level per session: a seeded random walk."""
    base = {"NDX": 21_000.0, "SPX": 6_000.0}[family]
    rng = np.random.default_rng(_seed("levels", family))
    level, out = base, {}
    for label, _, _ in _sessions():
        out[label] = level
        level *= float(np.exp(rng.normal(0.0003, 0.011)))
    return out


@cache
def _session_path(family: str, label: date) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-minute (ts ns, index close, has-trades) for one session."""
    session = next(s for s in _sessions() if s[0] == label)
    _, open_ns, close_ns = session
    minutes = np.arange(open_ns, close_ns, 60 * NANOS, dtype=np.int64)
    rng = np.random.default_rng(_seed("path", family, label))
    et_minute = ((minutes // NANOS // 60) - 4 * 60) % (24 * 60)  # rough ET clock for volatility
    rth = (et_minute >= 9 * 60 + 30) & (et_minute < 16 * 60)
    sigma = np.where(rth, 0.00055, 0.00022)
    returns = rng.normal(0.0, 1.0, minutes.size) * sigma
    level = _day_levels(family)[label] * np.exp(np.cumsum(returns))
    trades = rth | (rng.random(minutes.size) > 0.12)
    return minutes, level, trades


def _premium(contract: Contract, ts_ns: np.ndarray) -> np.ndarray:
    days = (int(contract.expiration.timestamp() * NANOS) - ts_ns) / (86_400 * NANOS)
    return 1.0 + 0.045 * np.maximum(days, 0.0) / 365.0


def _bars(
    contract: Contract, start_ns: int, end_ns: int
) -> list[tuple[int, int, int, int, int, int]]:
    """(ts, open, high, low, close, volume) in DBN fixed precision for one contract."""
    product = contract.product
    rows: list[tuple[int, int, int, int, int, int]] = []
    for label, open_ns, close_ns in _sessions():
        if close_ns <= start_ns or open_ns >= end_ns:
            continue
        minutes, level, trades = _session_path(product.family, label)
        price = level * _premium(contract, minutes)
        ticks = np.round(price / 0.25).astype(np.int64)
        prev = np.concatenate(([ticks[0]], ticks[:-1]))
        rng = np.random.default_rng(_seed("wick", product.root, contract.raw_symbol, label))
        up = np.abs(rng.normal(0, 3.0, ticks.size)).astype(np.int64)
        down = np.abs(rng.normal(0, 3.0, ticks.size)).astype(np.int64)
        high = np.maximum(prev, ticks) + up
        low = np.minimum(prev, ticks) - down
        et_minute = ((minutes // NANOS // 60) - 4 * 60) % (24 * 60)
        rth = (et_minute >= 9 * 60 + 30) & (et_minute < 16 * 60)
        volume = np.where(
            rth,
            rng.integers(product.rth_volume // 3, product.rth_volume * 2, ticks.size),
            rng.integers(1, max(2, product.rth_volume // 10), ticks.size),
        )
        keep = trades & (minutes >= start_ns) & (minutes < end_ns)
        for i in np.flatnonzero(keep):
            rows.append(
                (
                    int(minutes[i]),
                    int(prev[i]) * TICK,
                    int(high[i]) * TICK,
                    int(low[i]) * TICK,
                    int(ticks[i]) * TICK,
                    int(volume[i]),
                )
            )
    return rows


def _ns(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) * NANOS


def _date(value: Any) -> date:
    text = str(value)[:10]
    return date.fromisoformat(text)


def _client_error(status: int, case: str, message: str) -> Exception:
    import databento

    return databento.BentoClientError(
        http_status=status,
        json_body={"detail": {"case": case, "message": message, "docs": None}},
        message=message,
    )


class _Resolver:
    """Symbol → per-UTC-date contracts for the three supported symbology types."""

    @staticmethod
    def parse(symbol: str, stype_in: str) -> tuple[Product, str] | None:
        if stype_in == "continuous":
            parts = symbol.split(".")
            if len(parts) == 3 and parts[0] in PRODUCTS and parts[1] in "cvn" and parts[2] == "0":
                return PRODUCTS[parts[0]], parts[1]
            return None
        if stype_in == "parent":
            root, _, kind = symbol.partition(".")
            return (PRODUCTS[root], "parent") if root in PRODUCTS and kind == "FUT" else None
        if stype_in == "raw_symbol":
            for product in PRODUCTS.values():
                for contract in contracts(product):
                    if contract.raw_symbol == symbol:
                        return product, symbol
        return None

    @staticmethod
    def on(product: Product, rule: str, day: date) -> list[Contract]:
        if rule == "parent":
            return [
                c for c in contracts(product)
                if c.activation.date() <= day <= c.expiration.date()
            ]  # fmt: skip
        if rule in ("c", "v", "n"):
            return [front(product, day, "c" if rule == "c" else "v")]
        return [c for c in contracts(product) if c.raw_symbol == rule]


def _days(start: date, end: date) -> Iterable[date]:
    day = start
    while day < end:
        yield day
        day += timedelta(days=1)


class _Metadata:
    def __init__(self, owner: FixtureHistorical) -> None:
        self._o = owner

    def list_datasets(self, start_date: Any = None, end_date: Any = None) -> list[str]:
        self._o.check()
        return [DATASET, UNLICENSED]

    def list_schemas(self, dataset: str) -> list[str]:
        self._o.check(dataset, licensed=False)
        return list(SCHEMAS)

    def get_dataset_range(self, dataset: str) -> dict[str, Any]:
        self._o.check(dataset, licensed=False)
        start = f"{AVAILABLE_START.isoformat()}T00:00:00.000000000Z"
        end = f"{AVAILABLE_END.isoformat()}T00:00:00.000000000Z"
        return {
            "start": start,
            "end": end,
            "schema": {s: {"start": start, "end": end} for s in SCHEMAS},
        }

    def get_dataset_condition(
        self, dataset: str, start_date: Any = None, end_date: Any = None
    ) -> list[dict[str, Any]]:
        self._o.check(dataset, licensed=False)
        first = _date(start_date) if start_date else AVAILABLE_START
        last = _date(end_date) if end_date else AVAILABLE_END - timedelta(days=1)
        return [
            {
                "date": d.isoformat(),
                "condition": "degraded" if d in DEGRADED_DAYS else "available",
                "last_modified_date": d.isoformat(),
            }
            for d in _days(first, last + timedelta(days=1))
            if d < AVAILABLE_END
        ]

    def list_unit_prices(self, dataset: str) -> list[dict[str, Any]]:
        self._o.check(dataset)
        return [{"mode": "historical-streaming", "unit_prices": dict(USD_PER_GB)}]

    def _records(self, **kw: Any) -> int:
        return self._o.count(**kw)

    def get_record_count(self, **kw: Any) -> int:
        return self._records(**kw)

    def get_billable_size(self, **kw: Any) -> int:
        return self._records(**kw) * RECORD_BYTES.get(str(kw.get("schema")), 64)

    def get_cost(self, **kw: Any) -> float:
        kw.pop("mode", None)
        size = self.get_billable_size(**kw)
        return round(size / 1e9 * USD_PER_GB.get(str(kw.get("schema")), 100.0), 6)


class _Timeseries:
    def __init__(self, owner: FixtureHistorical) -> None:
        self._o = owner

    def get_range(self, **kw: Any) -> Any:
        import databento

        data = self._o.encode(**kw)
        path = kw.get("path")
        if path:
            Path(path).write_bytes(data)
            return databento.DBNStore.from_file(path)
        return databento.DBNStore.from_bytes(io.BytesIO(data))


class _Symbology:
    def __init__(self, owner: FixtureHistorical) -> None:
        self._o = owner

    def resolve(
        self,
        dataset: str,
        symbols: list[str],
        stype_in: str,
        stype_out: str,
        start_date: Any,
        end_date: Any = None,
    ) -> dict[str, Any]:
        self._o.check(dataset)
        first = _date(start_date)
        last = _date(end_date) if end_date else first + timedelta(days=1)
        result: dict[str, list[dict[str, str]]] = {}
        not_found: list[str] = []
        for symbol in symbols:
            parsed = _Resolver.parse(symbol, stype_in)
            if parsed is None:
                not_found.append(symbol)
                continue
            result[symbol] = self._o.intervals(parsed[0], parsed[1], first, last)
        return {
            "result": result,
            "symbols": symbols,
            "stype_in": stype_in,
            "stype_out": stype_out,
            "start_date": first.isoformat(),
            "end_date": last.isoformat(),
            "partial": [],
            "not_found": not_found,
            "message": "OK" if not not_found else "Not found",
            "status": 0 if not not_found else 2,
        }


class FixtureHistorical:
    """Same call surface as ``databento.Historical`` for what the hub uses."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.metadata = _Metadata(self)
        self.timeseries = _Timeseries(self)
        self.symbology = _Symbology(self)

    # -- behaviour -----------------------------------------------------------------------

    def check(self, dataset: str | None = None, *, licensed: bool = True) -> None:
        if "OFFLINE" in self.key:
            raise ConnectionError("fixture: network unreachable")
        if not self.key.startswith("db-") or "REVOKED" in self.key or len(self.key) < 20:
            raise _client_error(401, "auth_authentication_failed", "Authentication failed.")
        if dataset is not None and dataset not in (DATASET, UNLICENSED):
            raise _client_error(422, "dataset_invalid", f"Unknown dataset {dataset}.")
        if licensed and (dataset == UNLICENSED or (dataset == DATASET and "NOLICENSE" in self.key)):
            raise _client_error(403, "license_dataset_not_subscribed", "No license for dataset.")

    def _window(self, kw: dict[str, Any]) -> tuple[str, str, str, list[str], date, date]:
        dataset = str(kw["dataset"])
        self.check(dataset)
        schema = str(kw.get("schema", "trades"))
        stype_in = str(kw.get("stype_in", "raw_symbol"))
        if schema not in SCHEMAS:
            raise _client_error(422, "schema_invalid", f"Invalid schema {schema}.")
        start = _date(kw["start"])
        end = _date(kw["end"]) if kw.get("end") else start + timedelta(days=1)
        if start < AVAILABLE_START or end > AVAILABLE_END:
            raise _client_error(
                422, "data_end_after_available_end", "Range outside the available data."
            )
        symbols = kw.get("symbols")
        if not symbols or symbols == "ALL_SYMBOLS":
            raise _client_error(422, "symbology_invalid_request", "Fixture needs symbols.")
        return dataset, schema, stype_in, list(symbols), start, end

    def intervals(
        self, product: Product, rule: str, first: date, last: date
    ) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for day in _days(first, last):
            ids = ",".join(str(c.instrument_id) for c in _Resolver.on(product, rule, day))
            if out and out[-1]["s"] == ids and out[-1]["d1"] == day.isoformat():
                out[-1]["d1"] = (day + timedelta(days=1)).isoformat()
            else:
                out.append(
                    {"d0": day.isoformat(), "d1": (day + timedelta(days=1)).isoformat(), "s": ids}
                )
        return out

    def _plan(
        self, kw: dict[str, Any]
    ) -> tuple[str, str, str, list[tuple[str, Product, str]], list[str], date, date]:
        dataset, schema, stype_in, symbols, start, end = self._window(kw)
        known, missing = [], []
        for symbol in symbols:
            parsed = _Resolver.parse(str(symbol), stype_in)
            if parsed is None:
                missing.append(str(symbol))
            else:
                known.append((str(symbol), *parsed))
        if missing:
            warnings.warn(f"symbols not found: {', '.join(missing)}", stacklevel=2)
        if not known:
            raise _client_error(422, "symbology_invalid_symbol", "None of the symbols resolved.")
        return dataset, schema, stype_in, known, missing, start, end

    def count(self, **kw: Any) -> int:
        _, schema, _, known, _, start, end = self._plan(kw)
        return len(self._records(schema, known, start, end))

    def _records(
        self, schema: str, known: list[tuple[str, Product, str]], start: date, end: date
    ) -> list[Any]:
        import databento_dbn as dbn

        if schema not in GENERATED:
            raise _client_error(422, "schema_not_generated", f"The fixture can't produce {schema}.")
        records: list[Any] = []
        for _, product, rule in known:
            for day in _days(start, end):
                day_start, day_end = _ns(day), _ns(day + timedelta(days=1))
                for contract in _Resolver.on(product, rule, day):
                    if schema == "definition":
                        if day.weekday() == 5:  # no session starts on Saturday
                            continue
                        records.append(self._definition(dbn, contract, day_start))
                        continue
                    for ts, o, h, lo, c, v in _bars(contract, day_start, day_end):
                        records.append(
                            dbn.OHLCVMsg(
                                rtype=dbn.RType.OHLCV_1M,
                                publisher_id=1,
                                instrument_id=contract.instrument_id,
                                ts_event=ts,
                                open=o,
                                high=h,
                                low=lo,
                                close=c,
                                volume=v,
                            )
                        )
        records.sort(key=lambda r: (r.ts_event, r.instrument_id))
        return records

    @staticmethod
    def _definition(dbn: Any, contract: Contract, ts: int) -> Any:
        product = contract.product
        return dbn.InstrumentDefMsg(
            publisher_id=1,
            instrument_id=contract.instrument_id,
            ts_event=ts,
            ts_recv=ts,
            min_price_increment=TICK,
            display_factor=NANOS,
            raw_symbol=contract.raw_symbol,
            asset=product.root,
            security_type="FUT",
            instrument_class=dbn.InstrumentClass.FUTURE,
            security_update_action=dbn.SecurityUpdateAction.ADD,
            expiration=int(contract.expiration.timestamp()) * NANOS,
            activation=int(contract.activation.timestamp()) * NANOS,
            unit_of_measure_qty=product.multiplier * NANOS,
            min_price_increment_amount=product.tick_value_fixed,
            currency="USD",
            exchange="XCME",
            group=product.root,
            unit_of_measure="IPNT",
            cfi="FFIXSX",
        )

    def encode(self, **kw: Any) -> bytes:
        import databento_dbn as dbn

        dataset, schema, stype_in, known, missing, start, end = self._plan(kw)
        records = self._records(schema, known, start, end)
        mappings = []
        for symbol, product, rule in known:
            if rule == "parent":
                continue  # one symbol → many contracts; the definitions carry the ids
            intervals = [
                _Interval(_date(i["d0"]), _date(i["d1"]), i["s"])
                for i in self.intervals(product, rule, start, end)
            ]
            mappings.append(_Mapping(symbol, intervals))
        fields: dict[str, Any] = {
            "dataset": dataset,
            "start": _ns(start),
            "stype_in": dbn.SType.from_str(stype_in),
            "stype_out": dbn.SType.INSTRUMENT_ID,
            "schema": dbn.Schema.from_str(schema),
            "symbols": [s for s, _, _ in known] + missing,
            "partial": [],
            "not_found": missing,
            "mappings": mappings,
            "end": _ns(end),
        }
        metadata = dbn.Metadata(**fields)
        return bytes(metadata.encode()) + b"".join(bytes(r) for r in records)


@dataclass
class _Interval:
    start_date: date
    end_date: date
    symbol: str


@dataclass
class _Mapping:
    raw_symbol: str
    intervals: list[_Interval]
