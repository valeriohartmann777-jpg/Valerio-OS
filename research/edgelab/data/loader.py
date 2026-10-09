"""Load vendor OHLCV files into the canonical bar frame.

Canonical frame
---------------
* index ``ts``: tz-aware bar OPEN time, converted to ``tz_out`` (America/New_York)
* float64 columns ``open, high, low, close, volume``
* optional columns kept when present: ``bid_volume, ask_volume, n_trades, contract``
* sorted by time; duplicates are NOT silently dropped (the quality report counts them)

Vendors disagree on whether a bar is labelled by its open or its close time
(NinjaTrader and TradeStation exports label by close, Databento and most
research vendors by open). Getting this wrong shifts every signal by one bar,
which is a lookahead bug, so ``label`` has no default and must be stated.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

CANONICAL = ["open", "high", "low", "close", "volume"]
OPTIONAL = ["bid_volume", "ask_volume", "n_trades", "contract"]

_ALIASES = {
    "o": "open", "open": "open", "openprice": "open",
    "h": "high", "high": "high", "highprice": "high",
    "l": "low", "low": "low", "lowprice": "low",
    "c": "close", "close": "close", "last": "close", "closeprice": "close", "settle": "close",
    "v": "volume", "vol": "volume", "volume": "volume", "totalvolume": "volume",
    "ts": "ts", "timestamp": "ts", "datetime": "ts", "tsevent": "ts", "opentime": "ts",
    "date": "date", "day": "date", "time": "time",
    "bidvolume": "bid_volume", "askvolume": "ask_volume",
    "numberoftrades": "n_trades", "ntrades": "n_trades", "trades": "n_trades",
    "count": "n_trades", "numtrades": "n_trades", "#oftrades": "n_trades",
    "symbol": "contract", "contract": "contract", "ticker": "contract", "rawsymbol": "contract",
}


def file_sha256(path: str | Path, chunk: int = 1 << 20) -> str:
    """SHA-256 of a file, recorded so every experiment can name its exact input."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _key(col: str) -> str:
    return "".join(ch for ch in str(col).strip().lower() if ch not in " _-.")


def read_table(path: str | Path, **read_kwargs: Any) -> pd.DataFrame:
    """Read csv/txt/parquet/feather/arrow into a raw DataFrame."""
    p = Path(path)
    suffix = "".join(p.suffixes[-2:]).lower() if p.suffix.lower() in {".gz", ".zip", ".bz2"} else p.suffix.lower()
    if suffix.startswith(".parquet") or suffix == ".pq":
        return pd.read_parquet(p)
    if suffix in {".feather", ".arrow", ".ipc"}:
        return pd.read_feather(p)
    with open(p, "rb") as fh:
        head = fh.read(4096)
    if p.suffix.lower() in {".gz", ".zip", ".bz2"}:
        sep = read_kwargs.pop("sep", ",")
    else:
        text = head.decode("utf-8", errors="replace")
        first = text.splitlines()[0] if text else ""
        sep = read_kwargs.pop("sep", ";" if first.count(";") > first.count(",") else ("\t" if "\t" in first else ","))
    return pd.read_csv(p, sep=sep, **read_kwargs)


def normalize_columns(df: pd.DataFrame, column_map: dict[str, str] | None = None) -> pd.DataFrame:
    """Rename vendor columns to canonical names (explicit ``column_map`` wins)."""
    rename: dict[str, str] = {}
    for col in df.columns:
        if column_map and col in column_map:
            rename[col] = column_map[col]
            continue
        k = _key(col)
        if k in _ALIASES:
            rename[col] = _ALIASES[k]
    out = df.rename(columns=rename)
    if out.columns.duplicated().any():
        dup = out.columns[out.columns.duplicated()].tolist()
        raise ValueError(f"ambiguous columns after normalisation: {dup}; pass column_map")
    return out


def _epoch_unit(values: np.ndarray) -> str:
    m = float(np.nanmedian(np.abs(values)))
    if m > 1e17:
        return "ns"
    if m > 1e14:
        return "us"
    if m > 1e11:
        return "ms"
    return "s"


def parse_timestamps(
    df: pd.DataFrame,
    tz_in: str,
    ambiguous: str = "infer",
    nonexistent: str = "raise",
    ts_format: str | None = None,
) -> pd.DatetimeIndex:
    """Build a tz-aware DatetimeIndex from ``ts`` or ``date`` + ``time`` columns.

    Naive timestamps are localised to ``tz_in``. During the autumn DST change the
    01:00-02:00 hour repeats; ``ambiguous='infer'`` resolves it from ordering.
    Numeric epochs are interpreted as UTC with the unit inferred from magnitude.
    """
    if "ts" in df.columns:
        raw = df["ts"]
    elif "date" in df.columns and "time" in df.columns:
        raw = df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip()
    elif "date" in df.columns:
        raw = df["date"]
    else:
        raise ValueError("no timestamp column found (expected ts/timestamp/datetime or date+time)")

    if pd.api.types.is_numeric_dtype(raw):
        vals = raw.to_numpy(dtype="float64")
        idx = pd.DatetimeIndex(pd.to_datetime(raw.to_numpy(dtype="int64"), unit=_epoch_unit(vals), utc=True))
    else:
        idx = pd.DatetimeIndex(pd.to_datetime(raw, format=ts_format))
    if idx.tz is None:
        idx = idx.tz_localize(tz_in, ambiguous=ambiguous, nonexistent=nonexistent)
    return idx


def load_bars(
    path: str | Path,
    *,
    tz_in: str,
    label: Literal["open", "close"],
    bar_minutes: int | None = None,
    column_map: dict[str, str] | None = None,
    tz_out: str = "America/New_York",
    ambiguous: str = "infer",
    nonexistent: str = "raise",
    price_scale: float = 1.0,
    ts_format: str | None = None,
    read_kwargs: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load a vendor bar file into the canonical frame.

    Parameters
    ----------
    tz_in
        Timezone of naive timestamps in the file (ignored for tz-aware or epoch data).
    label
        Whether the vendor timestamp marks the bar ``open`` or ``close``. Close-labelled
        bars are shifted back by one bar length so the index is always the open time.
    bar_minutes
        Bar length; inferred from the modal spacing when omitted.
    price_scale
        Multiplier for fixed-point vendor prices (e.g. 1e-9 for raw Databento).

    Returns
    -------
    (bars, meta) where ``meta`` documents every normalisation decision.
    """
    raw = read_table(path, **(read_kwargs or {}))
    df = normalize_columns(raw, column_map)
    missing = [c for c in CANONICAL if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns {missing}; got {list(df.columns)}")
    idx = parse_timestamps(df, tz_in=tz_in, ambiguous=ambiguous, nonexistent=nonexistent, ts_format=ts_format)

    bars = pd.DataFrame(index=idx)
    for c in CANONICAL:
        bars[c] = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype="float64")
    for c in ("open", "high", "low", "close"):
        bars[c] = bars[c] * price_scale
    for c in OPTIONAL:
        if c in df.columns:
            bars[c] = df[c].to_numpy() if c == "contract" else pd.to_numeric(df[c], errors="coerce").to_numpy()

    order_was_sorted = bool(bars.index.is_monotonic_increasing)
    bars = bars.sort_index(kind="stable")
    inferred = infer_bar_minutes(bars.index)
    if bar_minutes is None:
        bar_minutes = inferred
    if label == "close":
        bars.index = bars.index - pd.Timedelta(minutes=bar_minutes)
    elif label != "open":
        raise ValueError("label must be 'open' or 'close'")
    bars.index = bars.index.tz_convert(tz_out)
    bars.index.name = "ts"

    meta = {
        "source_file": str(path),
        "sha256": file_sha256(path),
        "rows_read": int(len(raw)),
        "columns_raw": [str(c) for c in raw.columns],
        "tz_in": tz_in,
        "tz_out": tz_out,
        "label_convention": label,
        "bar_minutes": int(bar_minutes),
        "bar_minutes_inferred": int(inferred),
        "input_was_sorted": order_was_sorted,
        "price_scale": price_scale,
        "has_contract_column": "contract" in bars.columns,
        "has_bid_ask_volume": {"bid_volume", "ask_volume"}.issubset(bars.columns),
    }
    return bars, meta


def infer_bar_minutes(index: pd.DatetimeIndex) -> int:
    """Modal spacing between consecutive timestamps, in whole minutes (>= 1)."""
    if len(index) < 2:
        return 1
    diffs = pd.Series(index[1:] - index[:-1])
    diffs = diffs[diffs > pd.Timedelta(0)]
    if diffs.empty:
        return 1
    mode = diffs.mode().iloc[0]
    return max(1, int(round(mode.total_seconds() / 60.0)))


def drop_duplicate_bars(bars: pd.DataFrame, keep: str = "first") -> tuple[pd.DataFrame, int]:
    """Remove exact-timestamp duplicates; returns (frame, number removed)."""
    dup = bars.index.duplicated(keep=keep)
    return bars[~dup], int(dup.sum())
