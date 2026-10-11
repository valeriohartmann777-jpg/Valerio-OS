"""N-minute bars from finer bars: deterministic, session-anchored, one contract per bar.

Rules (each is tested against independently computed fixtures):

* Buckets are anchored at the strategy window's start (e.g. 09:30 New York, converted
  per date, so daylight saving never shifts them) — never at UTC midnight. Bucket ``k`` of
  a window covers ``[start + k·N, start + (k+1)·N)``; the last one is cut at the window end
  and flagged ``partial``. Nothing outside a window is aggregated, so no bar straddles a
  session boundary, a maintenance break or an early close.
* ``open`` = first actual open, ``high`` = max, ``low`` = min, ``close`` = last actual close,
  ``volume`` = sum — of the source bars that exist. Minutes without trades have no source
  bar (Databento OHLCV) and are never filled in; ``source_bars`` says how many there were.
* Bars of different contracts (``instrument_id``) are never mixed: a bucket holding two
  contracts yields two bars with the same timestamp (the engine then skips the session as a
  roll). ``stats["split_by_contract"]`` counts them.
* ``ts_event`` is the bucket's START; its values are known at ``ts_event + N minutes``.
* Source bars must sit on the bucket grid (an imported 5-minute file whose bars start at
  :02 can't be cut into session-anchored 15-minute bars) — otherwise ``ResampleError``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa

from jarvis.quantlab.futures.sessions import Window

AGGREGATOR = "ql-resample-1"
NS = 1_000_000_000
MINUTE = 60 * NS
COLUMNS = ("ts_event", "instrument_id", "open", "high", "low", "close", "volume")


class ResampleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Resampled:
    table: pa.Table
    minutes: int
    source_minutes: int
    stats: dict[str, Any] = field(default_factory=dict)


def _column(table: pa.Table, name: str) -> np.ndarray:
    if name not in table.column_names:
        return np.zeros(table.num_rows, dtype=np.int64)
    return np.asarray(table.column(name).to_numpy(zero_copy_only=False), dtype=np.int64)


def resample(
    table: pa.Table, windows: list[Window], minutes: int, *, source_minutes: int = 1
) -> Resampled:
    """Aggregate ``source_minutes`` bars into ``minutes`` bars inside ``windows``."""
    if minutes < 1 or source_minutes < 1:
        raise ResampleError("INVALID", "Bar lengths must be at least one minute.")
    if minutes % source_minutes:
        raise ResampleError(
            "WRONG_GRANULARITY",
            f"{minutes}-minute bars can't be built from {source_minutes}-minute bars "
            "(they would have to be split, which would invent data).",
        )
    ts = _column(table, "ts_event")
    if ts.size and np.any(np.diff(ts) < 0):
        order = np.lexsort((_column(table, "instrument_id"), ts))
        table = table.take(pa.array(order))
        ts = ts[order]
    iid = _column(table, "instrument_id")
    o, h, lo, c = (_column(table, n) for n in ("open", "high", "low", "close"))
    v = _column(table, "volume")
    span = minutes * MINUTE
    src = source_minutes * MINUTE

    win_parts: list[np.ndarray] = []
    bucket_parts: list[np.ndarray] = []
    index_parts: list[np.ndarray] = []
    for number, w in enumerate(windows):
        a, b = (int(x) for x in np.searchsorted(ts, [w.start_ns, w.end_ns]))
        if b <= a:
            continue
        offset = ts[a:b] - w.start_ns
        if source_minutes > 1 and np.any(offset % src):
            raise ResampleError(
                "MISALIGNED_SOURCE",
                f"{source_minutes}-minute source bars on {w.label} don't start on the "
                "session grid; they can't be aggregated without splitting a bar.",
            )
        win_parts.append(np.full(b - a, number, dtype=np.int64))
        bucket_parts.append(offset // span)
        index_parts.append(np.arange(a, b, dtype=np.int64))
    stats: dict[str, Any] = {
        "aggregator": AGGREGATOR,
        "minutes": minutes,
        "source_minutes": source_minutes,
        "source_bars_in": int(ts.size),
        "source_bars_used": 0,
        "bars_out": 0,
        "partial": 0,
        "split_by_contract": 0,
        "windows": len(windows),
    }
    empty = pa.table(
        {
            **{name: pa.array([], type=pa.int64()) for name in COLUMNS},
            "source_bars": pa.array([], type=pa.int64()),
            "span_minutes": pa.array([], type=pa.int64()),
            "partial": pa.array([], type=pa.bool_()),
        }
    )
    if not index_parts:
        return Resampled(empty, minutes, source_minutes, stats)
    win = np.concatenate(win_parts)
    bucket = np.concatenate(bucket_parts)
    idx = np.concatenate(index_parts)
    gid = iid[idx]
    # Stable sort by (window, bucket, contract): time order is kept inside every group.
    order = np.lexsort((gid, bucket, win))
    win, bucket, idx, gid = win[order], bucket[order], idx[order], gid[order]
    change = np.ones(idx.size, dtype=bool)
    change[1:] = (win[1:] != win[:-1]) | (bucket[1:] != bucket[:-1]) | (gid[1:] != gid[:-1])
    first = np.flatnonzero(change)
    last = np.append(first[1:], idx.size) - 1
    w_start = np.asarray([windows[int(n)].start_ns for n in win[first]], dtype=np.int64)
    w_end = np.asarray([windows[int(n)].end_ns for n in win[first]], dtype=np.int64)
    bar_ts = w_start + bucket[first] * span
    span_ns = np.minimum(bar_ts + span, w_end) - bar_ts
    out = {
        "ts_event": bar_ts,
        "instrument_id": gid[first],
        "open": o[idx[first]],
        "high": np.maximum.reduceat(h[idx], first),
        "low": np.minimum.reduceat(lo[idx], first),
        "close": c[idx[last]],
        "volume": np.add.reduceat(v[idx], first),
        "source_bars": last - first + 1,
        "span_minutes": span_ns // MINUTE,
    }
    partial = span_ns < span
    # Group boundaries by (window, bucket) only: two contracts in one bucket.
    pair = np.ones(first.size, dtype=bool)
    pair[1:] = (win[first][1:] != win[first][:-1]) | (bucket[first][1:] != bucket[first][:-1])
    final = np.lexsort((out["instrument_id"], out["ts_event"]))
    result = pa.table(
        {
            **{name: pa.array(out[name][final], type=pa.int64()) for name in out},
            "partial": pa.array(partial[final], type=pa.bool_()),
        }
    )
    stats.update(
        source_bars_used=int(idx.size),
        bars_out=int(first.size),
        partial=int(partial.sum()),
        split_by_contract=int(first.size - pair.sum()),
    )
    return Resampled(result, minutes, source_minutes, stats)


def detect_minutes(table: pa.Table) -> int | None:
    """The source resolution: the smallest gap between consecutive bars of one contract."""
    ts = _column(table, "ts_event")
    iid = _column(table, "instrument_id")
    if ts.size < 2:
        return None
    order = np.lexsort((ts, iid))
    ts, iid = ts[order], iid[order]
    gaps = np.diff(ts)[iid[1:] == iid[:-1]]
    gaps = gaps[gaps > 0]
    if not gaps.size:
        return None
    smallest = int(gaps.min())
    if smallest % MINUTE:
        return None
    return smallest // MINUTE


def table_sha256(table: pa.Table) -> str:
    """A content hash of a bar table (column names, types and values, in order)."""
    digest = hashlib.sha256()
    for name in table.column_names:
        column = table.column(name).combine_chunks()
        digest.update(name.encode())
        digest.update(str(column.type).encode())
        values = column.to_numpy(zero_copy_only=False)
        digest.update(np.ascontiguousarray(values).tobytes())
    return digest.hexdigest()
