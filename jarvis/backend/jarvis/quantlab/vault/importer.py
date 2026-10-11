"""File imports for the Data Vault: CSV, Parquet and Databento DBN (also .dbn.zst).

Two steps, both local:

1. ``preview`` — format by magic bytes, columns, sample rows, a guessed column mapping and
   what's still needed (e.g. the timezone of naive timestamps). Nothing is stored.
2. ``parse`` — with the owner's confirmed mapping: timestamps → UTC bar starts, prices →
   integer 1e-9 units (exact, never float-rounded), then strict checks. A file whose prices
   are off the contract's tick grid (a back- or ratio-adjusted series), whose timestamps
   can't be placed in time, or whose bars aren't regular is refused — never "repaired".

User files are never labelled exchange-authoritative. A DBN file keeps the provider's own
instrument ids and symbology; a CSV/Parquet file gets contract identities only from a
contract column or the owner's declaration of a single contract — otherwise the series is
marked as having no contract mapping (and can't be backtested across rolls).
"""

from __future__ import annotations

import csv
import io
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pyarrow as pa

from jarvis.quantlab.futures.contracts import REFERENCE
from jarvis.quantlab.futures.resample import MINUTE, detect_minutes
from jarvis.quantlab.futures.timeframes import TIMEFRAMES

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
SAMPLE_ROWS = 8
MAX_REJECTED_SHARE = 0.01
SYNTHETIC_ID_BASE = 1 << 40  # above every exchange instrument id
CONTRACT = re.compile(r"^(MNQ|MES|NQ|ES)([FGHJKMNQUVXZ])(\d{1,4})$")
_CANDIDATES = {
    "timestamp": ("ts_event", "timestamp", "datetime", "date_time", "time", "ts", "date"),
    "date": ("date", "day"),
    "time": ("time", "clock"),
    "open": ("open", "o", "open_price"),
    "high": ("high", "h", "high_price"),
    "low": ("low", "l", "low_price"),
    "close": ("close", "c", "close_price", "last"),
    "volume": ("volume", "vol", "v", "size"),
    "contract": ("contract", "raw_symbol", "instrument", "symbol", "ticker"),
}


class ImportError_(ValueError):
    def __init__(self, code: str, message: str, findings: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.findings = findings or []


def finding(level: str, code: str, message: str, count: int = 1) -> dict[str, Any]:
    return {"level": level, "code": code, "message": message, "count": count}


# -- format ------------------------------------------------------------------------------------


def sniff(path: Path) -> str:
    head = path.read_bytes()[:8] if path.stat().st_size < 64 else _head(path)
    if head.startswith(b"DBN"):
        return "dbn"
    if head.startswith(ZSTD_MAGIC):
        import zstandard

        with path.open("rb") as handle:
            reader = zstandard.ZstdDecompressor().stream_reader(handle)
            inner = reader.read(4)
        if inner.startswith(b"DBN"):
            return "dbn"
        raise ImportError_("UNSUPPORTED", "A zstd file that isn't DBN — decompress it first.")
    if head.startswith(b"PAR1"):
        return "parquet"
    try:
        _head(path, 4096).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportError_(
            "UNSUPPORTED", "Unknown file format — expected CSV, Parquet or DBN."
        ) from None
    return "csv"


def _head(path: Path, size: int = 8) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", name.strip().lower()).strip("_")


def guess_mapping(columns: list[str]) -> dict[str, str | None]:
    lookup = {_norm(c): c for c in columns}
    out: dict[str, str | None] = {}
    for key in ("open", "high", "low", "close", "volume", "contract"):
        out[key] = next((lookup[n] for n in _CANDIDATES[key] if n in lookup), None)
    stamp = next(
        (lookup[n] for n in _CANDIDATES["timestamp"] if n in lookup and n not in ("date", "time")),
        None,
    )
    date_col = next((lookup[n] for n in _CANDIDATES["date"] if n in lookup), None)
    time_col = next((lookup[n] for n in _CANDIDATES["time"] if n in lookup), None)
    if stamp is None and date_col and time_col:
        out.update(timestamp=None, date=date_col, time=time_col)
    else:
        out.update(timestamp=stamp or time_col or date_col, date=None, time=None)
    return out


def _csv_dialect(path: Path) -> tuple[str, bool]:
    sample = _head(path, 16384).decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    try:
        header = csv.Sniffer().has_header(sample)
    except csv.Error:
        header = True
    return delimiter, header


def _read_tabular(path: Path, fmt: str, nrows: int | None = None) -> Any:
    import pandas as pd

    if fmt == "parquet":
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        if nrows is not None:
            table = table.slice(0, nrows)
        return table.to_pandas()
    delimiter, header = _csv_dialect(path)
    return pd.read_csv(
        path,
        sep=delimiter,
        header=0 if header else None,
        dtype=str,
        nrows=nrows,
        skipinitialspace=True,
        encoding="utf-8-sig",
    ).rename(columns=lambda c: str(c).strip())


def _stamp_kind(values: list[str]) -> str:
    sample = [v for v in values if v not in ("", "nan", "None")][:20]
    if not sample:
        return "unknown"
    if all(re.fullmatch(r"-?\d+(\.\d+)?", v) for v in sample):
        magnitude = max(abs(float(v)) for v in sample)
        if magnitude > 1e17:
            return "epoch_ns"
        if magnitude > 1e14:
            return "epoch_us"
        if magnitude > 1e11:
            return "epoch_ms"
        return "epoch_s"
    if all(re.search(r"(Z|[+-]\d\d:?\d\d)$", v.strip()) for v in sample):
        return "iso_with_offset"
    return "naive"


def preview(path: Path) -> dict[str, Any]:
    fmt = sniff(path)
    if fmt == "dbn":
        return _preview_dbn(path)
    frame = _read_tabular(path, fmt, nrows=200)
    columns = [str(c) for c in frame.columns]
    mapping = guess_mapping(columns)
    stamp_col = mapping.get("timestamp") or mapping.get("date")
    kind = (
        _stamp_kind([str(v) for v in frame[stamp_col].tolist()])
        if stamp_col in frame.columns
        else "unknown"
    )
    if mapping.get("date") and mapping.get("time"):
        kind = "naive"
    needs = []
    if kind == "naive":
        needs.append("timezone")
    if not all(mapping.get(k) for k in ("open", "high", "low", "close")) or kind == "unknown":
        needs.append("columns")
    roots = _roots_in(frame, mapping.get("contract"))
    return {
        "format": fmt,
        "columns": columns,
        "sample": [
            {c: _cell(v) for c, v in row.items()}
            for row in frame.head(SAMPLE_ROWS).to_dict("records")
        ],
        "mapping": mapping,
        "timestamp_kind": kind,
        "contract_roots": roots,
        "needs": needs,
        "authoritative": False,
        "note": "User file — imported as provided, never labelled exchange-authoritative.",
    }


def _cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _roots_in(frame: Any, column: str | None) -> list[str]:
    if not column or column not in frame.columns:
        return []
    roots = set()
    for value in frame[column].astype(str).tolist():
        match = CONTRACT.match(_contract_text(value))
        if match:
            roots.add(match.group(1))
    return sorted(roots)


def _preview_dbn(path: Path) -> dict[str, Any]:
    import databento

    store = databento.DBNStore.from_file(str(path))
    meta = store.metadata
    symbols = [str(s) for s in (meta.symbols or [])]
    roots = sorted({r for s in symbols if (r := _root_of(s))})
    return {
        "format": "dbn",
        "columns": [],
        "sample": [],
        "mapping": {},
        "timestamp_kind": "epoch_ns",
        "dbn": {
            "dataset": str(meta.dataset),
            "schema": str(meta.schema) if meta.schema is not None else None,
            "stype_in": str(meta.stype_in) if meta.stype_in is not None else None,
            "symbols": symbols[:20],
            "start": int(meta.start),
            "end": int(meta.end) if meta.end else None,
        },
        "contract_roots": roots,
        "needs": [] if len(roots) == 1 else ["root"],
        "authoritative": False,
        "note": "Databento DBN file supplied by you — instrument ids and symbology are the "
        "provider's; provenance as stated in the file's metadata.",
    }


def _root_of(symbol: str) -> str | None:
    match = re.match(r"^(MNQ|MES|NQ|ES)(?=[.FGHJKMNQUVXZ]|$)", symbol)
    return match.group(1) if match else None


def _contract_text(value: str) -> str:
    return re.sub(r"[\s\-_/']", "", str(value).upper())


def synthetic_id(contract: str) -> int:
    """A stable id for a contract named in a user file (never an exchange id)."""
    return SYNTHETIC_ID_BASE + zlib.crc32(contract.encode())


# -- parse --------------------------------------------------------------------------------------


@dataclass
class Parsed:
    format: str
    table: pa.Table  # ts_event, instrument_id, open, high, low, close, volume (int64)
    schema: str
    source_minutes: int
    root: str
    symbol: str  # the series symbol: NQ.local, NQZ5, or the DBN's own symbol
    stype_in: str
    dataset: str
    contracts: dict[int, str]  # instrument id → raw symbol (user files)
    mapped: bool  # contract identity known for every bar
    rows_in: int
    rows_rejected: int
    findings: list[dict[str, Any]] = field(default_factory=list)
    mappings: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    definitions: pa.Table | None = None


def parse(path: Path, mapping: dict[str, Any]) -> Parsed:
    fmt = sniff(path)
    if fmt == "dbn":
        return _parse_dbn(path, mapping)
    return _parse_tabular(path, fmt, mapping)


def _parse_dbn(path: Path, mapping: dict[str, Any]) -> Parsed:
    import databento

    from jarvis.quantlab.hub import cache

    store = databento.DBNStore.from_file(str(path))
    meta = store.metadata
    schema = str(meta.schema) if meta.schema is not None else ""
    if schema not in ("ohlcv-1m", "ohlcv-1h", "definition"):
        raise ImportError_(
            "SCHEMA_NOT_SUPPORTED",
            f"DBN schema {schema or 'mixed'} isn't importable yet "
            "(ohlcv-1m, ohlcv-1h, definition).",
        )
    symbols = [str(s) for s in (meta.symbols or [])]
    roots = sorted({r for s in symbols if (r := _root_of(s))})
    root = str(mapping.get("root") or (roots[0] if len(roots) == 1 else ""))
    if root not in REFERENCE:
        raise ImportError_("ROOT_UNKNOWN", "Choose the product (NQ, MNQ, ES or MES) of this file.")
    if len(symbols) != 1:
        raise ImportError_(
            "MULTI_SYMBOL",
            "The DBN file holds several requested symbols; import one series per file.",
        )
    decoded = cache.decode(path, schema)
    table = decoded.table
    findings = [
        finding("INFO", "DBN", f"Databento {meta.dataset} {schema}, {meta.stype_in} {symbols[0]}.")
    ]
    stype_in = str(meta.stype_in)
    if stype_in == "parent" and schema != "definition":
        findings.append(
            finding(
                "BLOCK",
                "NO_CONTINUOUS_MAPPING",
                "Parent symbology holds every contract month at once; a backtest needs one "
                "contract per day (continuous symbology such as NQ.v.0, or one contract).",
            )
        )
    minutes = 60 if schema == "ohlcv-1h" else 1
    return Parsed(
        format="dbn",
        table=table,
        schema=schema,
        source_minutes=minutes,
        root=root,
        symbol=symbols[0],
        stype_in=stype_in,
        dataset=str(meta.dataset),
        contracts={},
        mapped=stype_in != "parent",
        rows_in=table.num_rows,
        rows_rejected=0,
        findings=findings,
        mappings=decoded.mappings,
    )


def _zone(name: str | None) -> ZoneInfo:
    if not name:
        raise ImportError_(
            "TIMEZONE_REQUIRED",
            "The timestamps have no timezone. Choose the zone they were written in "
            "(e.g. America/New_York, America/Chicago or UTC).",
        )
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError):
        raise ImportError_("TIMEZONE_UNKNOWN", f"Unknown timezone {name!r}.") from None


def _timestamps(frame: Any, mapping: dict[str, Any]) -> tuple[np.ndarray, int]:
    """UTC ns per row; NaT rows → -1 (rejected). Returns (stamps, ambiguous count)."""
    import pandas as pd

    if mapping.get("date") and mapping.get("time"):
        text = frame[mapping["date"]].astype(str) + " " + frame[mapping["time"]].astype(str)
        kind = "naive"
    else:
        column = mapping.get("timestamp")
        if not column or column not in frame.columns:
            raise ImportError_("COLUMNS", "Choose the timestamp column.")
        text = frame[column].astype(str).str.strip()
        kind = _stamp_kind(text.tolist())
    ambiguous = 0
    if kind.startswith("epoch"):
        unit = kind.split("_")[1]
        numbers = pd.to_numeric(text, errors="coerce")
        stamps = pd.to_datetime(numbers, unit=unit, utc=True, errors="coerce")
    elif kind == "iso_with_offset":
        stamps = pd.to_datetime(text, utc=True, errors="coerce", format="mixed")
    else:
        zone = _zone(mapping.get("timezone"))
        local = pd.to_datetime(text, errors="coerce", format="mixed")
        if getattr(local.dt, "tz", None) is not None:
            stamps = local.dt.tz_convert("UTC")
        else:
            located = local.dt.tz_localize(zone, ambiguous="NaT", nonexistent="NaT")
            ambiguous = int((located.isna() & local.notna()).sum())
            stamps = located.dt.tz_convert("UTC")
    # pandas 3 infers µs or s resolution from strings: normalise to ns before reading.
    values = stamps.dt.as_unit("ns").astype("int64").to_numpy(dtype=np.int64, copy=True)
    values[stamps.isna().to_numpy()] = -1
    return values, ambiguous


def _prices(frame: Any, column: str | None, scale: str) -> np.ndarray:
    import pandas as pd

    if not column or column not in frame.columns:
        raise ImportError_("COLUMNS", "Choose the open, high, low and close columns.")
    numbers = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=np.float64)
    if scale == "fixed_1e9":
        out = np.where(np.isfinite(numbers), np.rint(numbers), -1).astype(np.int64)
    else:
        out = np.where(np.isfinite(numbers), np.rint(numbers * 1e9), -1).astype(np.int64)
    return out


def _parse_tabular(path: Path, fmt: str, mapping: dict[str, Any]) -> Parsed:
    import pandas as pd

    root = str(mapping.get("root") or "")
    if root not in REFERENCE:
        raise ImportError_("ROOT_UNKNOWN", "Choose the product of this file (NQ, MNQ, ES, MES).")
    frame = _read_tabular(path, fmt)
    rows_in = len(frame)
    if not rows_in:
        raise ImportError_("EMPTY", "The file has no rows.")
    findings: list[dict[str, Any]] = []
    stamps, ambiguous = _timestamps(frame, mapping)
    if ambiguous:
        findings.append(
            finding(
                "WARN",
                "DST_AMBIGUOUS",
                f"{ambiguous} local time(s) fall in a daylight-saving change and can't be "
                "placed in time — rejected. Files with UTC timestamps avoid this.",
                ambiguous,
            )
        )
    scale = str(mapping.get("price_scale") or "decimal")
    o, h, lo, c = (_prices(frame, mapping.get(k), scale) for k in ("open", "high", "low", "close"))
    vol_col = mapping.get("volume")
    if vol_col and vol_col in frame.columns:
        volume = (
            pd.to_numeric(frame[vol_col], errors="coerce").fillna(0).to_numpy().astype(np.int64)
        )
    else:
        volume = np.zeros(rows_in, dtype=np.int64)
        findings.append(finding("INFO", "NO_VOLUME", "No volume column — volume recorded as 0."))
    mode = str(mapping.get("contract_mode") or ("column" if mapping.get("contract") else "none"))
    contracts: dict[int, str] = {}
    ok = stamps >= 0
    if mode == "column":
        column = mapping.get("contract")
        if not column or column not in frame.columns:
            raise ImportError_("COLUMNS", "Choose the contract column (e.g. NQZ5 per row).")
        names = [_contract_text(v) for v in frame[column].astype(str).tolist()]
        iid = np.empty(rows_in, dtype=np.int64)
        other = 0
        for i, name in enumerate(names):
            match = CONTRACT.match(name)
            if match is None or match.group(1) != root:
                iid[i] = -1
                other += 1
                continue
            ident = synthetic_id(name)
            contracts.setdefault(ident, name)
            iid[i] = ident
        if other:
            findings.append(
                finding(
                    "WARN",
                    "OTHER_CONTRACTS",
                    f"{other} row(s) name no {root} contract (another product or no month "
                    "code) — rejected, never substituted.",
                    other,
                )
            )
        ok &= iid >= 0
        symbol, stype_in, mapped = f"{root}.local", "continuous", True
    elif mode == "single":
        name = _contract_text(str(mapping.get("single_contract") or ""))
        match = CONTRACT.match(name)
        if match is None or match.group(1) != root:
            raise ImportError_(
                "CONTRACT_INVALID", f"'{name}' isn't a {root} contract like {root}Z5."
            )
        ident = synthetic_id(name)
        contracts[ident] = name
        iid = np.full(rows_in, ident, dtype=np.int64)
        symbol = name if re.fullmatch(r"(MNQ|MES|NQ|ES)[HMUZ]\d{1,2}", name) else f"{root}.local"
        stype_in = "raw_symbol" if symbol == name else "continuous"
        mapped = True
    else:
        name = f"{root}?unmapped"
        ident = synthetic_id(name)
        contracts[ident] = name
        iid = np.full(rows_in, ident, dtype=np.int64)
        symbol, stype_in, mapped = f"{root}.local", "continuous", False
        findings.append(
            finding(
                "BLOCK",
                "NO_CONTRACT_MAPPING",
                "No contract per bar: rolls between contract months can't be seen, so a "
                "backtest would mix contracts. Re-import with a contract column, or declare "
                "the single contract the file holds.",
            )
        )
    bad_price = (o <= 0) | (h <= 0) | (lo <= 0) | (c <= 0)
    inconsistent = (lo > h) | (o > h) | (o < lo) | (c > h) | (c < lo)
    reject = ~ok | bad_price | inconsistent
    if int((bad_price & ok).sum()):
        findings.append(
            finding(
                "WARN",
                "BAD_PRICE",
                "Missing or non-positive prices — rejected.",
                int(bad_price.sum()),
            )
        )
    if int((inconsistent & ~bad_price & ok).sum()):
        findings.append(
            finding(
                "WARN",
                "OHLC_INCONSISTENT",
                "Bars whose open/close lie outside high–low — rejected.",
                int((inconsistent & ~bad_price).sum()),
            )
        )
    if int((stamps < 0).sum()) - ambiguous > 0:
        findings.append(
            finding(
                "WARN",
                "BAD_TIMESTAMP",
                "Rows whose timestamp couldn't be read — rejected.",
                int((stamps < 0).sum()) - ambiguous,
            )
        )
    keep = ~reject
    tick = REFERENCE[root].tick_size_fixed
    on_grid = ((o % tick) == 0) & ((h % tick) == 0) & ((lo % tick) == 0) & ((c % tick) == 0)
    off = int((keep & ~on_grid).sum())
    if off:
        raise ImportError_(
            "OFF_TICK_GRID",
            f"{off} bar(s) have prices off the {root} tick grid ({tick / 1e9:g}). That's typical "
            "of back- or ratio-adjusted continuous series, which can't produce real fills or "
            "P&L — import unadjusted prices instead.",
            findings,
        )
    table = pa.table(
        {
            "ts_event": pa.array(stamps[keep], type=pa.int64()),
            "instrument_id": pa.array(iid[keep], type=pa.int64()),
            "open": pa.array(o[keep], type=pa.int64()),
            "high": pa.array(h[keep], type=pa.int64()),
            "low": pa.array(lo[keep], type=pa.int64()),
            "close": pa.array(c[keep], type=pa.int64()),
            "volume": pa.array(volume[keep], type=pa.int64()),
        }
    ).sort_by([("ts_event", "ascending"), ("instrument_id", "ascending")])
    rejected = int(reject.sum())
    # Duplicate bars (same start, same contract): the first is kept.
    ts_arr = table.column("ts_event").to_numpy()
    id_arr = table.column("instrument_id").to_numpy()
    dup = np.zeros(table.num_rows, dtype=bool)
    if table.num_rows > 1:
        dup[1:] = (ts_arr[1:] == ts_arr[:-1]) & (id_arr[1:] == id_arr[:-1])
    if dup.any():
        findings.append(
            finding(
                "WARN",
                "DUPLICATES",
                "Repeated bars (same start, same contract) — the first is kept.",
                int(dup.sum()),
            )
        )
        table = table.filter(pa.array(~dup))
        rejected += int(dup.sum())
    if rows_in and rejected / rows_in > MAX_REJECTED_SHARE:
        raise ImportError_(
            "TOO_MANY_REJECTED",
            f"{rejected} of {rows_in} rows failed the checks (more than "
            f"{MAX_REJECTED_SHARE:.0%}) — the mapping or the file is likely wrong.",
            findings,
        )
    if not table.num_rows:
        raise ImportError_("EMPTY", "No valid rows after the checks.", findings)
    minutes = detect_minutes(table)
    if minutes is None or minutes not in TIMEFRAMES:
        raise ImportError_(
            "IRREGULAR_BARS",
            "The bars aren't on a regular 1/2/3/5/10/15/30/60-minute grid "
            f"(smallest step: {minutes or 'none'}).",
            findings,
        )
    if str(mapping.get("timestamp_marks") or "start") == "end":
        shifted = table.column("ts_event").to_numpy() - minutes * MINUTE
        table = table.set_column(0, "ts_event", pa.array(shifted, type=pa.int64()))
        findings.append(
            finding(
                "INFO",
                "END_STAMPED",
                f"Timestamps marked bar ends: moved back {minutes} min to bar starts.",
            )
        )
    return Parsed(
        format=fmt,
        table=table,
        schema=f"ohlcv-{minutes}m",
        source_minutes=minutes,
        root=root,
        symbol=symbol,
        stype_in=stype_in,
        dataset="LOCAL",
        contracts=contracts,
        mapped=mapped,
        rows_in=rows_in,
        rows_rejected=rejected,
        findings=findings,
    )


def write_csv(rows: list[dict[str, Any]]) -> bytes:
    """Tests and the E2E: a CSV from dict rows (keeps column order)."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()
