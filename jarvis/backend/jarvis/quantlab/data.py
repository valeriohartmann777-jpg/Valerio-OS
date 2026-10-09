"""Data intelligence: import OHLCV bars, check them, write the Data Passport.

Fail closed. Nothing is silently repaired: duplicates, broken OHLC, unparseable
or non-finite prices and unknown timezones block the dataset; unsorted rows
are sorted and the fix is recorded; gaps, zero volume and big jumps are
reported, never filled. Every bar ends up as a UTC bar-start timestamp.

The normalised bars are hashed in a library-independent text form
(``normalized_sha256``) and frozen as a Parquet snapshot; the engine only ever
reads that snapshot.
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.quantlab.spec import INTERVALS, sha256_bytes, sha256_text, unsupported_reason

MAX_BYTES = 50 * 1024 * 1024
MAX_ROWS = 2_000_000
OUTLIER_MOVE = 0.20  # a bar-to-bar close change above 20% is reported (never changed)
PASSPORT_VERSION = "0.1"

# The handoff's synthetic engineering fixtures, by SHA-256 of the file.
SYNTHETIC_FIXTURES: dict[str, str] = {
    "0e7cd1960c7be5852df9bd45b5c2185e63fcfdf20f8a59d7e502ce7d6c0423e0": "golden_execution",
    "600b91f32a8b893929bf72ace21c22d3b704515f814f8ddf336ebb004f811a77": "ma_crossover",
    "589d9f680002a0a78c14d1559a01b993f636cd6f6c460a1a8a39c1478ae033ff": "invalid_ohlc_duplicate",
}
SYNTHETIC_LABEL = "SYNTHETIC / TEST ONLY"

Status = Literal["ACCEPTED", "WARNING", "REJECTED", "UNSUPPORTED"]
Severity = Literal["block", "warn", "info"]

_TIME_KEYS = (
    "bar_start_utc", "timestamp_utc", "timestamp", "datetime", "date_time", "bar_start",
    "open_time", "time", "date", "ts",
)  # fmt: skip
_PRICE_KEYS: dict[str, tuple[str, ...]] = {
    "open": ("open", "o", "open_price"),
    "high": ("high", "h", "high_price"),
    "low": ("low", "l", "low_price"),
    "close": ("close", "c", "close_price", "last"),
}
_VOLUME_KEYS = ("volume", "vol", "real_volume", "tickvol", "tick_volume", "v")


class DataError(ValueError):
    """The upload can't be read at all (too big, not CSV/Parquet, no header)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Bar:
    ts: datetime  # bar start, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float | None


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str
    count: int = 0
    examples: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
            "examples": list(self.examples),
        }


@dataclass(frozen=True)
class ImportMeta:
    symbol: str
    exchange: str
    currency: str
    asset_class: str = "cash_equity"
    timezone: str | None = None  # how to read timestamps without an offset
    frequency: str | None = None  # 1m / 5m / 1h / 1d; inferred when missing
    provider: str = "user_supplied"
    license: str = "unverified (user's responsibility)"
    adjustment: Literal["adjusted", "unadjusted", "unknown"] = "unknown"
    columns: dict[str, str] = field(default_factory=dict)  # role -> header override


@dataclass
class ImportResult:
    status: Status
    passport: dict[str, Any]
    findings: list[Finding]
    bars: list[Bar]
    preview: dict[str, Any]
    synthetic: bool

    @property
    def usable(self) -> bool:
        return self.status in ("ACCEPTED", "WARNING")


# Reading ---------------------------------------------------------------------------------


def _norm_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().strip("<>").lower()).strip("_")


def _read_table(filename: str, data: bytes) -> tuple[list[str], list[list[Any]], list[str]]:
    """Header, rows, and the normalisations applied while reading."""
    if data[:4] == b"PAR1" or filename.lower().endswith(".parquet"):
        return _read_parquet(data)
    return _read_csv(data)


def _read_parquet(data: bytes) -> tuple[list[str], list[list[Any]], list[str]]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    try:
        table = pq.read_table(pa.BufferReader(data))
    except Exception as exc:  # pyarrow raises several types for corrupt files
        raise DataError("FILE_UNREADABLE", f"This isn't a readable Parquet file ({exc}).") from exc
    header = [str(name) for name in table.column_names]
    columns = [table.column(i).to_pylist() for i in range(table.num_columns)]
    rows = [list(values) for values in zip(*columns, strict=True)] if columns else []
    return header, rows, ["read Parquet"]


def _read_csv(data: bytes) -> tuple[list[str], list[list[Any]], list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataError(
            "FILE_UNREADABLE", "The file isn't UTF-8 text. Export it as CSV (UTF-8) or Parquet."
        ) from exc
    try:
        dialect: Any = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in row)]
    if not rows:
        raise DataError("FILE_EMPTY", "The file has no rows.")
    notes = [f"read CSV (delimiter {dialect.delimiter!r})"]
    body: list[list[Any]] = [list(r) for r in rows[1:]]
    if dialect.delimiter == ";" and any("," in c and "." not in c for r in body[:50] for c in r):
        body = [[c.replace(",", ".") for c in r] for r in body]
        notes.append("decimal commas read as decimal points")
    return [c.strip() for c in rows[0]], body, notes


def _map_columns(header: list[str], overrides: dict[str, str]) -> tuple[dict[str, int], str]:
    """Role -> column index. Missing roles are absent. Also returns the time mode."""
    normalized = [_norm_header(h) for h in header]
    roles: dict[str, int] = {}
    for role, name in overrides.items():
        if name and name in header:
            roles[role] = header.index(name)
        elif name and _norm_header(name) in normalized:
            roles[role] = normalized.index(_norm_header(name))

    def find(keys: Iterable[str]) -> int | None:
        for key in keys:
            if key in normalized:
                return normalized.index(key)
        return None

    for role, keys in _PRICE_KEYS.items():
        if role not in roles and (index := find(keys)) is not None:
            roles[role] = index
    if "volume" not in roles and (index := find(_VOLUME_KEYS)) is not None:
        roles["volume"] = index
    mode = "single"
    if "time" not in roles:
        # MetaTrader-style exports split the stamp over <DATE> and <TIME>.
        if "date" in normalized and "time" in normalized:
            roles["time"] = normalized.index("date")
            roles["time_of_day"] = normalized.index("time")
            mode = "date+time"
        elif (index := find(_TIME_KEYS)) is not None:
            roles["time"] = index
    return roles, mode


# Timestamps ------------------------------------------------------------------------------

_DOTTED_DATE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})")
_DATE_ONLY = re.compile(r"^\d{4}[-.]\d{2}[-.]\d{2}$|^\d{8}$")


def _to_datetime(value: Any) -> datetime | None:
    """A datetime (naive or aware) from a cell, or None if it isn't one."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, int | float) and not isinstance(value, bool):
        if not math.isfinite(value):
            return None
        seconds = value / 1000 if abs(value) >= 1e11 else value  # epoch ms or s
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d{9,13}(\.\d+)?", text):
        return _to_datetime(float(text))
    text = _DOTTED_DATE.sub(r"\1-\2-\3", text)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


class _Clock:
    """Turns cells into UTC bar starts, counting what couldn't be read."""

    def __init__(self, timezone: ZoneInfo | None) -> None:
        self.timezone = timezone
        self.unparseable: list[str] = []
        self.naive_without_tz: list[str] = []
        self.dst_ambiguous: list[str] = []
        self.aware = 0
        self.naive = 0
        self.dates = 0

    def utc(self, value: Any) -> datetime | None:
        if (type(value) is date) or (isinstance(value, str) and _DATE_ONLY.match(value.strip())):
            # A trading date (daily bars): stamped 00:00 UTC of that date, no timezone needed.
            day = _to_datetime(value)
            if day is None:
                self.unparseable.append(str(value)[:40])
                return None
            self.dates += 1
            return day.replace(tzinfo=UTC)
        moment = _to_datetime(value)
        if moment is None:
            self.unparseable.append(str(value)[:40])
            return None
        if moment.tzinfo is not None:
            self.aware += 1
            return moment.astimezone(UTC)
        self.naive += 1
        if self.timezone is None:
            self.naive_without_tz.append(str(value)[:40])
            return None
        early = moment.replace(tzinfo=self.timezone, fold=0)
        late = moment.replace(tzinfo=self.timezone, fold=1)
        if early.utcoffset() != late.utcoffset():
            # Repeated (autumn) or skipped (spring) local time: refuse to guess.
            self.dst_ambiguous.append(str(value)[:40])
            return None
        return early.astimezone(UTC)


# Prices ----------------------------------------------------------------------------------


def _number(value: Any) -> float | str | None:
    """A float, None for an empty cell, or the string 'bad' if it isn't a number."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "bad"
    if isinstance(value, int | float | Decimal):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return "bad"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _duration(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


# The import ------------------------------------------------------------------------------


def import_bars(filename: str, data: bytes, meta: ImportMeta) -> ImportResult:
    """Read, check and normalise. Never raises for bad *content* — that's a REJECTED result."""
    if len(data) > MAX_BYTES:
        raise DataError(
            "FILE_TOO_LARGE",
            f"The file is {len(data) / 1e6:.1f} MB; the limit is {MAX_BYTES / 1e6:.0f} MB.",
        )
    if not data:
        raise DataError("FILE_EMPTY", "The file is empty.")
    original_sha = sha256_bytes(data)
    synthetic = original_sha in SYNTHETIC_FIXTURES
    header, rows, notes = _read_table(filename, data)
    if len(rows) > MAX_ROWS:
        raise DataError(
            "TOO_MANY_ROWS", f"{len(rows):,} rows; the limit is {MAX_ROWS:,}. Split the file."
        )
    findings: list[Finding] = []

    def block(code: str, message: str, count: int = 0, examples: Iterable[str] = ()) -> None:
        findings.append(Finding(code, "block", message, count, tuple(examples)[:5]))

    def warn(code: str, message: str, count: int = 0, examples: Iterable[str] = ()) -> None:
        findings.append(Finding(code, "warn", message, count, tuple(examples)[:5]))

    def info(code: str, message: str) -> None:
        findings.append(Finding(code, "info", message))

    roles, time_mode = _map_columns(header, meta.columns)
    missing = [r for r in ("time", "open", "high", "low", "close") if r not in roles]
    if missing:
        block(
            "COLUMNS_MISSING",
            f"Couldn't find column(s) for {', '.join(missing)}. Found: {', '.join(header)}. "
            "Name them in the column mapping.",
        )

    timezone: ZoneInfo | None = None
    if meta.timezone:
        try:
            timezone = ZoneInfo(meta.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            block("TIMEZONE_UNKNOWN", f"'{meta.timezone}' isn't an IANA timezone name.")

    unsupported = unsupported_reason(meta.asset_class, meta.symbol, meta.exchange)
    if not re.fullmatch(r"[A-Z]{3}", meta.currency):
        block("CURRENCY_INVALID", f"Currency '{meta.currency}' isn't a 3-letter ISO code.")
    if not meta.symbol.strip():
        block("SYMBOL_MISSING", "The instrument symbol is required.")

    clock = _Clock(timezone)
    stamps: list[datetime] = []  # every readable bar start, valid prices or not
    parsed: list[Bar] = []
    bad_numbers: list[str] = []
    missing_prices: list[str] = []
    non_finite: list[str] = []
    non_positive: list[str] = []
    bad_ohlc: list[str] = []
    negative_volume: list[str] = []
    empty_volume = 0
    zero_volume = 0
    if not missing:
        for line, row in enumerate(rows, start=2):
            if len(row) < len(header):
                row = [*row, *([None] * (len(header) - len(row)))]
            stamp: Any = row[roles["time"]]
            if time_mode == "date+time":
                stamp = f"{str(stamp).strip()} {str(row[roles['time_of_day']]).strip()}"
            ts = clock.utc(stamp)
            if ts is not None:
                stamps.append(ts)
            values = [_number(row[roles[r]]) for r in ("open", "high", "low", "close")]
            where = f"row {line}"
            if any(v == "bad" for v in values):
                bad_numbers.append(where)
                continue
            if any(v is None for v in values):
                missing_prices.append(where)
                continue
            o, h, low, c = (float(v) for v in values if isinstance(v, float))
            if not all(math.isfinite(v) for v in (o, h, low, c)):
                non_finite.append(where)
                continue
            if min(o, h, low, c) <= 0:
                non_positive.append(where)
                continue
            if not (low <= min(o, c) and max(o, c) <= h and low <= h):
                bad_ohlc.append(f"{where}: O{o:g} H{h:g} L{low:g} C{c:g}")
                continue
            volume: float | None = None
            if "volume" in roles:
                v = _number(row[roles["volume"]])
                if v == "bad" or (isinstance(v, float) and not math.isfinite(v)):
                    bad_numbers.append(f"{where} (volume)")
                    continue
                if v is None:
                    empty_volume += 1
                elif isinstance(v, float):
                    if v < 0:
                        negative_volume.append(where)
                        continue
                    zero_volume += v == 0
                    volume = v
            if ts is not None:
                parsed.append(Bar(ts, o, h, low, c, volume))

    if clock.dates:
        notes.append("date-only stamps read as 00:00 UTC of the trading date")
    if clock.unparseable:
        block(
            "TIMESTAMP_UNPARSEABLE",
            "Some timestamps can't be read. Use ISO 8601 (2024-01-31T14:30:00Z), "
            "YYYY.MM.DD HH:MM or epoch seconds — ambiguous day/month formats aren't guessed.",
            len(clock.unparseable),
            clock.unparseable,
        )
    if clock.naive_without_tz:
        block(
            "TIMEZONE_UNKNOWN",
            "Timestamps carry no UTC offset and no timezone was given. Choose the timezone "
            "the file was exported in (e.g. America/New_York, or your broker's server time).",
            len(clock.naive_without_tz),
            clock.naive_without_tz,
        )
    if clock.dst_ambiguous:
        block(
            "TIMESTAMP_DST_AMBIGUOUS",
            "Some local times are repeated or skipped by a daylight-saving change, so the real "
            "moment is ambiguous. Export with UTC offsets.",
            len(clock.dst_ambiguous),
            clock.dst_ambiguous,
        )
    if bad_numbers:
        block("PRICE_UNPARSEABLE", "Non-numeric prices or volume.", len(bad_numbers), bad_numbers)
    if missing_prices:
        block("PRICE_MISSING", "Rows with empty OHLC fields.", len(missing_prices), missing_prices)
    if non_finite:
        block("PRICE_NOT_FINITE", "NaN or infinite prices.", len(non_finite), non_finite)
    if non_positive:
        block("PRICE_NONPOSITIVE", "Prices at or below zero.", len(non_positive), non_positive)
    if bad_ohlc:
        block(
            "OHLC_INVALID",
            "Bars break low ≤ min(open, close) ≤ max(open, close) ≤ high.",
            len(bad_ohlc),
            bad_ohlc,
        )
    if negative_volume:
        block("VOLUME_NEGATIVE", "Negative volume.", len(negative_volume), negative_volume)

    # Order, duplicates, spacing -------------------------------------------------------
    counts = Counter(stamps)
    duplicates = sorted(ts for ts, n in counts.items() if n > 1)
    if duplicates:
        block(
            "DUPLICATE_TIMESTAMP",
            "The same bar start appears more than once. Rows are never merged or dropped "
            "implicitly — fix the export.",
            sum(counts[ts] - 1 for ts in duplicates),
            [_iso(ts) for ts in duplicates],
        )
    inversions = sum(1 for a, b in pairwise(parsed) if b.ts < a.ts)
    if inversions:
        parsed.sort(key=lambda bar: bar.ts)
        notes.append(f"sorted by time ({inversions} out-of-order rows)")
        warn(
            "SORT_FIX",
            "Rows weren't in time order; they were sorted (nothing else changed).",
            inversions,
        )

    frequency = meta.frequency
    frequency_source = "declared"
    gaps: list[tuple[datetime, timedelta]] = []
    missing_bars = 0
    deltas = [b.ts - a.ts for a, b in pairwise(parsed) if b.ts > a.ts]
    if len(parsed) < 2:
        if not any(f.severity == "block" for f in findings):
            block("TOO_SHORT", "At least two valid bars are needed.", len(parsed))
    elif deltas:
        modal = Counter(deltas).most_common(1)[0][0]
        if frequency is None:
            inferred = next((k for k, v in INTERVALS.items() if v == modal), None)
            if inferred is None:
                block(
                    "FREQUENCY_UNKNOWN",
                    f"Bars are mostly {_duration(modal)} apart, which isn't a supported interval "
                    "(1m, 5m, 1h, 1d).",
                )
            else:
                frequency, frequency_source = inferred, "inferred"
                info("FREQUENCY_INFERRED", f"Bar interval inferred from the data: {inferred}.")
        if frequency is not None and frequency not in INTERVALS:
            block("FREQUENCY_UNKNOWN", f"Unsupported bar interval '{frequency}'.")
        elif frequency is not None:
            step = INTERVALS[frequency]
            if modal != step:
                block(
                    "FREQUENCY_MISMATCH",
                    f"Declared {frequency} bars, but most bars are {_duration(modal)} apart.",
                )
            overlapping = [
                _iso(b.ts) for a, b in pairwise(parsed) if timedelta(0) < b.ts - a.ts < step
            ]
            if overlapping:
                block(
                    "OVERLAPPING_BARS",
                    f"Bars start less than one {frequency} interval after the previous one.",
                    len(overlapping),
                    overlapping,
                )
            irregular = [
                _iso(b.ts)
                for a, b in pairwise(parsed)
                if (b.ts - a.ts) > step and (b.ts - a.ts) % step
            ]
            if irregular:
                warn(
                    "IRREGULAR_SPACING",
                    "Some bars aren't aligned to the interval grid.",
                    len(irregular),
                    irregular,
                )
            for a, b in pairwise(parsed):
                delta = b.ts - a.ts
                if delta <= step:
                    continue
                weekend = frequency == "1d" and a.ts.weekday() == 4 and delta == timedelta(days=3)
                if not weekend:
                    gaps.append((a.ts, delta))
                    missing_bars += max(0, int(delta / step) - 1)
            if gaps:
                largest = max(gaps, key=lambda g: g[1])
                warn(
                    "GAPS",
                    "Time gaps between bars (not filled). No exchange calendar is applied, so "
                    "overnight and holiday breaks count too. Largest: "
                    f"{_duration(largest[1])} after {_iso(largest[0])}.",
                    len(gaps),
                    [f"{_iso(ts)} +{_duration(d)}" for ts, d in gaps],
                )

    outliers = [
        f"{_iso(b.ts)}: {b.close / a.close - 1:+.1%}"
        for a, b in pairwise(parsed)
        if abs(b.close / a.close - 1) > OUTLIER_MOVE
    ]
    if outliers:
        warn(
            "OUTLIER_MOVES",
            f"Close-to-close moves above {OUTLIER_MOVE:.0%} (left unchanged). Check for "
            "unadjusted splits or bad ticks.",
            len(outliers),
            outliers,
        )
    if "volume" not in roles:
        warn("VOLUME_MISSING", "No volume column; liquidity can't be checked.")
    else:
        if empty_volume:
            warn("VOLUME_EMPTY", "Rows without volume.", empty_volume)
        if zero_volume:
            warn("ZERO_VOLUME", "Bars with zero volume (illiquid or no trades).", zero_volume)
        if _norm_header(header[roles["volume"]]) in ("tickvol", "tick_volume"):
            info("TICK_VOLUME", "Volume is tick volume (number of price changes), not shares.")
    if not synthetic and meta.adjustment != "adjusted":
        warn(
            "CORPORATE_ACTIONS_UNCLEAR",
            "Splits and dividends: the series isn't declared as adjusted. Unadjusted splits "
            "create fake jumps; dividends aren't credited in R1.",
        )
    if unsupported:
        findings.insert(0, Finding("UNSUPPORTED_INSTRUMENT", "block", unsupported))

    blocked = any(f.severity == "block" for f in findings)
    status: Status
    if unsupported:
        status = "UNSUPPORTED"
    elif blocked:
        status = "REJECTED"
    elif any(f.severity == "warn" for f in findings):
        status = "WARNING"
    else:
        status = "ACCEPTED"
    bars = [] if blocked else parsed

    normalized_sha = sha256_text(normalized_text(bars)) if bars else None
    limitations = [
        "No exchange calendar: sessions, holidays and early closes are not modelled.",
        "Bars are OHLCV only: intrabar order of high and low is unknown.",
    ]
    if synthetic:
        limitations[:0] = [
            f"{SYNTHETIC_LABEL}: fabricated numbers for engineering tests, not real prices.",
            "Not suitable for statistical or out-of-sample validation.",
        ]
    elif meta.license.startswith("unverified"):
        limitations.append("Licence not verified: you're responsible for the right to use it.")
    if unsupported:
        limitations.append(unsupported)

    if clock.dates and not (clock.aware or clock.naive):
        tz_original = "trading dates (no time of day)"
    elif clock.aware and not clock.naive:
        tz_original = "UTC offsets in the data"
    elif meta.timezone:
        tz_original = meta.timezone + (" (+ offsets in the data)" if clock.aware else "")
    else:
        tz_original = "unknown"
    if synthetic:
        adjustment_text = "not applicable (synthetic fixture)"
    elif meta.adjustment == "adjusted":
        adjustment_text = "declared adjusted by the provider (not verified)"
    else:
        adjustment_text = f"{meta.adjustment}: no split/dividend handling in R1"

    passport: dict[str, Any] = {
        "schema_version": PASSPORT_VERSION,
        "source_type": "synthetic_fixture" if synthetic else "user_supplied",
        "dataset_kind": "synthetic_engineering_fixture" if synthetic else "user_market_data",
        "synthetic": synthetic,
        "label": SYNTHETIC_LABEL if synthetic else None,
        "provider": "synthetic_test_fixture" if synthetic else meta.provider,
        "license": "internal_test_only" if synthetic else meta.license,
        "original_filename": Path(filename).name[:200],
        "original_sha256": original_sha,
        "normalized_sha256": normalized_sha,
        "instrument": {
            "asset_class": meta.asset_class,
            "symbol": meta.symbol,
            "exchange": meta.exchange,
            "currency": meta.currency,
        },
        "venue": meta.exchange,
        "currency": meta.currency,
        "asset_class": meta.asset_class,
        "timezone_original": tz_original,
        "normalized_timezone": "UTC",
        "timestamp_semantics": "bar_start",
        "frequency": frequency,
        "frequency_source": frequency_source if frequency else None,
        "coverage_start_utc": _iso(bars[0].ts) if bars else None,
        "coverage_end_utc": _iso(bars[-1].ts) if bars else None,
        "row_count": len(bars),
        "rows_in_file": len(rows),
        "missing_periods": {"gaps": len(gaps), "estimated_missing_bars": missing_bars},
        "duplicates": sum(counts[ts] - 1 for ts in duplicates),
        "sort_fixes": inversions,
        "invalid_ohlc": len(bad_ohlc),
        "outliers": len(outliers),
        "zero_volume": zero_volume,
        "corporate_actions_or_roll_policy": adjustment_text,
        "column_mapping": {
            role: header[index] for role, index in roles.items() if index < len(header)
        },
        "normalizations": notes,
        "quality_status": status,
        "quality_findings": [f.as_dict() for f in findings],
        "limitations": limitations,
    }
    preview = {
        "header": header,
        "raw_rows": [[_cell(c) for c in r] for r in rows[:5]],
        "normalized_rows": [_bar_row(b) for b in bars[:8]],
    }
    return ImportResult(status, passport, findings, bars, preview, synthetic)


def _cell(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)[:60]


def _bar_row(bar: Bar) -> dict[str, Any]:
    return {
        "ts": _iso(bar.ts),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def normalized_text(bars: Sequence[Bar]) -> str:
    """The library-independent form ``normalized_sha256`` is taken over."""
    lines = ["ts,open,high,low,close,volume"]
    for b in bars:
        volume = "" if b.volume is None else repr(b.volume)
        lines.append(f"{_iso(b.ts)},{b.open!r},{b.high!r},{b.low!r},{b.close!r},{volume}")
    return "\n".join(lines) + "\n"


# Snapshots -------------------------------------------------------------------------------


def snapshot_bytes(bars: Sequence[Bar]) -> bytes:
    """The frozen Parquet snapshot of normalised bars."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "ts": pa.array([b.ts for b in bars], type=pa.timestamp("us", tz="UTC")),
            "open": pa.array([b.open for b in bars], type=pa.float64()),
            "high": pa.array([b.high for b in bars], type=pa.float64()),
            "low": pa.array([b.low for b in bars], type=pa.float64()),
            "close": pa.array([b.close for b in bars], type=pa.float64()),
            "volume": pa.array([b.volume for b in bars], type=pa.float64()),
        }
    )
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd")
    return sink.getvalue()


def read_snapshot(path: Path) -> list[Bar]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    cols = {name: table.column(name).to_pylist() for name in table.column_names}
    return [
        Bar(ts.astimezone(UTC), o, h, low, c, v)
        for ts, o, h, low, c, v in zip(
            cols["ts"],
            cols["open"],
            cols["high"],
            cols["low"],
            cols["close"],
            cols["volume"],
            strict=True,
        )
    ]
