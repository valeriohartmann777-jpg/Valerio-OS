"""Raw provider files and canonical per-day Parquet, both immutable.

Layout under the hub root::

    raw/<provider>/<dataset>/<schema>/<stype_in>/<symbol>/<start>_<end>__<job>-<n>.dbn
    canonical/<provider>/<dataset>/<schema>/<stype_in>/<symbol>/<YYYY-MM-DD>.parquet
    snapshots/<dataset_id>/bars.parquet, definitions.parquet, manifest.json

A raw file is exactly what the provider delivered (DBN, zstd or not). The
canonical files keep the provider's integer prices (fixed precision, 1e-9) —
no float conversion, no adjustment — split by UTC day so the cache knows which
days it already holds and never buys them twice.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

TRANSFORM_VERSION = "canon-1"
DAY_NS = 86_400 * 1_000_000_000

OHLCV_COLUMNS = ("ts_event", "instrument_id", "open", "high", "low", "close", "volume")
DEFINITION_COLUMNS = (
    "ts_recv",
    "ts_event",
    "instrument_id",
    "raw_symbol",
    "security_type",
    "instrument_class",
    "asset",
    "exchange",
    "currency",
    "min_price_increment",
    "display_factor",
    "unit_of_measure",
    "unit_of_measure_qty",
    "min_price_increment_amount",
    "expiration",
    "activation",
)
_STRING_DEFINITION = {
    "raw_symbol",
    "security_type",
    "instrument_class",
    "asset",
    "exchange",
    "currency",
    "unit_of_measure",
}


def safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", part)[:80] or "_"


def cache_key(provider: str, dataset: str, schema: str, stype_in: str, symbol: str) -> str:
    return "|".join((provider, dataset, schema, stype_in, symbol))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def day_ns(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) * 1_000_000_000


def days(start: date, end: date) -> list[date]:
    out, day = [], start
    while day < end:
        out.append(day)
        day += timedelta(days=1)
    return out


def ranges(missing: list[date]) -> list[tuple[date, date]]:
    """Contiguous [start, end) runs of days."""
    out: list[tuple[date, date]] = []
    for day in sorted(missing):
        if out and out[-1][1] == day:
            out[-1] = (out[-1][0], day + timedelta(days=1))
        else:
            out.append((day, day + timedelta(days=1)))
    return out


def freeze(path: Path) -> None:
    os.chmod(path, 0o444)


def write_parquet(path: Path, table: pa.Table) -> str:
    """Staged write + rename; returns the file's SHA-256. Existing files are kept."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return sha256_file(path)
    staging = path.with_name(path.name + ".partial")
    pq.write_table(table, staging, compression="zstd")
    os.replace(staging, path)
    freeze(path)
    return sha256_file(path)


@dataclass
class Decoded:
    schema: str
    table: pa.Table
    mappings: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    by_day: dict[date, pa.Table] = field(default_factory=dict)


def decode(path: Path, schema: str) -> Decoded:
    """DBN → canonical Arrow table, split by UTC day (bars by ts_event, definitions by ts_recv)."""
    import databento

    store = databento.DBNStore.from_file(str(path))
    frame = store.to_df(price_type="fixed", pretty_ts=False, map_symbols=False)
    frame = frame.reset_index()
    mappings = {
        str(symbol): [
            {
                "start": str(i.get("start_date")),
                "end": str(i.get("end_date")),
                "instrument_id": str(i.get("symbol")),
            }
            for i in intervals
        ]
        for symbol, intervals in (store.metadata.mappings or {}).items()
    }
    columns: tuple[str, ...]
    if schema.startswith("ohlcv"):
        columns = OHLCV_COLUMNS
        stamp = "ts_event"
    elif schema == "definition":
        columns = DEFINITION_COLUMNS
        stamp = "ts_recv"
    else:
        raise ValueError(f"schema {schema} isn't ingestible yet")
    arrays: dict[str, pa.Array] = {}
    for name in columns:
        if name in frame.columns:
            values = frame[name]
            if name in _STRING_DEFINITION:
                arrays[name] = pa.array(values.astype(str).tolist(), type=pa.string())
            else:
                arrays[name] = pa.array(values.astype("int64").tolist(), type=pa.int64())
        else:
            kind = pa.string() if name in _STRING_DEFINITION else pa.int64()
            arrays[name] = pa.nulls(len(frame), type=kind)
    table = pa.table(arrays)
    if table.num_rows:
        table = table.sort_by([(stamp, "ascending"), ("instrument_id", "ascending")])
    by_day: dict[date, pa.Table] = {}
    if table.num_rows:
        stamps = table.column(stamp).to_pylist()
        groups: dict[date, list[int]] = {}
        for i, ts in enumerate(stamps):
            groups.setdefault(datetime.fromtimestamp(ts // 1_000_000_000, UTC).date(), []).append(i)
        for day, idx in groups.items():
            by_day[day] = table.take(pa.array(idx))
    return Decoded(schema=schema, table=table, mappings=mappings, by_day=by_day)


def ids_on(mappings: dict[str, list[dict[str, Any]]], symbol: str, day: date) -> list[int]:
    """Instrument ids the provider mapped ``symbol`` to on ``day`` (from the DBN metadata)."""
    out: list[int] = []
    for interval in mappings.get(symbol, []):
        try:
            start, end = date.fromisoformat(interval["start"]), date.fromisoformat(interval["end"])
        except ValueError:
            continue
        if start <= day < end:
            for part in str(interval["instrument_id"]).split(","):
                if part.strip().isdigit():
                    out.append(int(part))
    return out


def read_days(root: Path, paths: list[str]) -> pa.Table | None:
    tables = [pq.read_table(root / p) for p in paths if p]
    tables = [t for t in tables if t.num_rows]
    if not tables:
        return None
    return pa.concat_tables(tables, promote_options="default")
