"""Vendor 1-minute bar files whose clock and roll method are not documented.

Written for the Kaggle NQ file (tgtanalytics/nq-futures-1min-bar-2022-2025) and usable
for any vendor bar file of a CME equity-index future. Every function here either
describes the data or returns an explicit decision that the caller writes to a report;
nothing changes a price, fills a bar or drops a row silently.

* :func:`find_bar_files` decides by content (columns, parseable timestamps, OHLC
  consistency) which file of a download holds the bars, never by its name.
* :func:`column_profile` documents every column, including extras such as VWAP,
  session markers, contract symbols or roll information, before anything is dropped.
* :func:`infer_clock` determines the timezone and the open/close label of the
  timestamps from the CME session structure instead of assuming them. Three independent
  checks must agree: the implied UTC offset of every session start (18:00 New York)
  follows exactly one timezone through both DST regimes; the empty maintenance hour sits
  at 17:00-18:00 New York time; the cash-open volume spike sits at 09:30. Anything less
  than one answer is AMBIGUOUS, and the caller stops.
* :func:`normalise` returns the canonical input frame (UTC bar-open times) and the
  log of every removed row: impossible bars (capped by a share that stops the run) and
  zero-volume bars, which carry no trade (the pipeline's own bars come from trades, and
  a minute without trades has no bar, ``trades.py``).
* :func:`extra_quality` adds the checks the generic report (``quality.check_bars``)
  lacks: weekend and holiday bars, raw order, duplicate rows, repeated bars, missing
  minutes, session starts per DST regime.
* :func:`assess_rolls` classifies the continuous series (contract column, unadjusted
  with quarterly roll gaps, back-adjusted, unclear), identifies the vendor's roll timing
  and whether the runner's frozen roll exclusion (RESEARCH_PROTOCOL.md 13.3) covers it.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dateutil.easter import easter

from ..config import SessionTemplate, minutes_of
from ..sessions import in_time_range, trading_dates
from .loader import CANONICAL, OPTIONAL, _epoch_unit, _key, file_sha256, infer_bar_minutes, normalize_columns, read_table
from .rolls import equity_index_roll_dates, roll_exclusion_dates, third_friday

MONTH_CODES = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M", 7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}
# Zones a vendor clock is compared with. Fixed-offset zones catch files written in
# "EST all year" style; Asian and European zones catch exports made on a local machine.
CANDIDATE_ZONES = (
    "UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "Etc/GMT+4", "Etc/GMT+5", "Etc/GMT+6", "Europe/London", "Europe/Berlin", "Europe/Moscow",
    "Asia/Dubai", "Asia/Singapore", "Asia/Hong_Kong", "Asia/Tokyo", "Australia/Sydney",
)
_OFFSET_RE = r"(?:Z|[+-]\d{2}:?\d{2})$"


class StopCondition(RuntimeError):
    """A finding that must stop the pipeline before any research step."""


def _ns(index: pd.DatetimeIndex) -> np.ndarray:
    """Integer nanoseconds, whatever resolution pandas chose for the index."""
    return pd.DatetimeIndex(index).as_unit("ns").asi8


# =======================================================================================
# 1. which file holds the bars
# =======================================================================================
def sniff(path: str | Path) -> str:
    """zip | parquet | feather | text | binary | empty, from the first bytes."""
    with open(path, "rb") as fh:
        head = fh.read(8192)
    if not head:
        return "empty"
    for magic, kind in ((b"PK\x03\x04", "zip"), (b"PAR1", "parquet"), (b"ARROW1", "feather")):
        if head.startswith(magic):
            return kind
    if b"\x00" in head:
        return "binary"
    text = head.decode("utf-8", errors="replace")
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text) / max(len(text), 1)
    return "text" if printable > 0.97 else "binary"


def count_rows(path: str | Path, chunk: int = 1 << 24) -> int:
    """Line count of a text file (a last line without newline counts)."""
    n, last = 0, b""
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            n += block.count(b"\n")
            last = block[-1:]
    return n + (1 if last and last != b"\n" else 0)


def _ohlc_consistency(df: pd.DataFrame) -> float:
    o, h, lo, c = (pd.to_numeric(df[k], errors="coerce").to_numpy(float) for k in ("open", "high", "low", "close"))
    ok = np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c)
    if not ok.any():
        return 0.0
    good = (h >= np.maximum(o, c)) & (lo <= np.minimum(o, c)) & (h >= lo)
    return float(good[ok].mean())


def _has_time(cols: list[str]) -> list[str]:
    if "ts" in cols:
        return ["ts"]
    if "date" in cols and "time" in cols:
        return ["date", "time"]
    return ["date"] if "date" in cols else []


HEADERLESS_NAMES = {6: ["ts", "open", "high", "low", "close", "volume"],
                    7: ["date", "time", "open", "high", "low", "close", "volume"]}


def read_bar_table(path: str | Path, kind: str | None = None, nrows: int | None = None,
                   headerless: bool = False) -> pd.DataFrame:
    """Raw table as written. ``headerless`` assigns the documented positional names."""
    kind = kind or sniff(path)
    if kind == "parquet":
        df = pd.read_parquet(path)
        return df.head(nrows) if nrows else df
    if kind == "feather":
        df = pd.read_feather(path)
        return df.head(nrows) if nrows else df
    if kind != "text":
        raise ValueError(f"{path}: not a table ({kind})")
    kw: dict[str, Any] = {"nrows": nrows} if nrows else {}
    if headerless:
        first = Path(path).open("rb").readline().decode("utf-8", errors="replace")
        sep = ";" if first.count(";") > first.count(",") else ("\t" if "\t" in first else ",")
        n = len(first.rstrip("\r\n").split(sep))
        return pd.read_csv(path, sep=sep, header=None, names=HEADERLESS_NAMES[n], **kw)
    # read_table detects the separator from the first line; an extension-less upload is fine
    return read_table(path, **kw)


def describe_file(path: str | Path, sample_rows: int = 5000) -> dict[str, Any]:
    """What a file is and whether it holds 1-minute OHLCV bars, judged by content."""
    p = Path(path)
    info: dict[str, Any] = {"path": str(p), "name": p.name, "bytes": p.stat().st_size, "kind": sniff(p),
                            "candidate": False, "header": None, "reason": ""}
    if info["kind"] in ("zip",):
        info["reason"] = "archive (extracted by scripts/kaggle_download.py, never read directly)"
        return info
    if info["kind"] not in ("text", "parquet", "feather"):
        info["reason"] = f"not a table ({info['kind']})"
        return info
    try:
        raw = read_bar_table(p, info["kind"], nrows=sample_rows)
    except Exception as exc:  # described, never fatal here
        info["reason"] = f"unreadable as a table: {type(exc).__name__}: {str(exc)[:120]}"
        return info
    info["header"] = "present"
    try:
        cols = list(normalize_columns(raw).columns)
    except ValueError as exc:
        cols = []
        info["reason"] = str(exc)
    if not {"open", "high", "low", "close"} <= set(cols) and info["kind"] == "text":
        first = p.open("rb").readline().decode("utf-8", errors="replace").rstrip("\r\n")
        sep = ";" if first.count(";") > first.count(",") else ("\t" if "\t" in first else ",")
        fields = first.split(sep)
        if len(fields) in HEADERLESS_NAMES and pd.notna(pd.to_datetime(fields[0] if len(fields) == 6 else f"{fields[0]} {fields[1]}",
                                                                        errors="coerce")):
            raw = read_bar_table(p, "text", nrows=sample_rows, headerless=True)
            cols = list(raw.columns)
            info["header"] = "absent: columns named by position " + ", ".join(cols)
    info["columns_raw"] = [str(c) for c in raw.columns]
    info["columns_canonical"] = cols
    tcols = _has_time(cols)
    info["timestamp_columns"] = tcols
    need = {"open", "high", "low", "close", "volume"}
    if not need <= set(cols):
        info["reason"] = info["reason"] or f"no OHLCV columns (has {cols[:12]})"
        return info
    if not tcols:
        info["reason"] = "no timestamp column"
        return info
    df = raw if info["header"] != "present" else normalize_columns(raw)
    try:
        clock, kind = raw_timestamps(df)
        info["timestamp_kind"] = kind
        info["timestamp_parse_ok_pct"] = float(100 * (1 - clock.isna().mean()))
    except Exception as exc:
        info["reason"] = f"timestamps not parseable: {type(exc).__name__}: {str(exc)[:120]}"
        return info
    info["ohlc_consistency_pct_sample"] = 100 * _ohlc_consistency(df)
    if info["header"] != "present" and info["ohlc_consistency_pct_sample"] < 99.9:
        info["reason"] = "headerless and the positional OHLC order is not consistent"
        return info
    info["rows"] = count_rows(p) - (1 if info["header"] == "present" else 0) if info["kind"] == "text" else None
    info["candidate"] = info["timestamp_parse_ok_pct"] > 99.0
    info["reason"] = "1-minute OHLCV bars" if info["candidate"] else "timestamps mostly unparseable"
    return info


def find_bar_files(root: str | Path, skip_suffixes: tuple[str, ...] = (".json", ".md")) -> tuple[list[Path], list[dict]]:
    """All files under ``root`` described; the bar files among them, largest first."""
    files = []
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file() or p.name.startswith(".") or p.suffix.lower() in skip_suffixes:
            continue
        files.append(describe_file(p))
    cands = sorted((f for f in files if f["candidate"]), key=lambda f: -(f.get("rows") or f["bytes"]))
    return [Path(f["path"]) for f in cands], files


# =======================================================================================
# 2. columns
# =======================================================================================
def extra_role(col: str) -> tuple[str, str]:
    """(role, description) of a column that is not canonical OHLCV."""
    k = _key(col)
    if "vwap" in k:
        if "rth" in k or "regular" in k or "day" in k:
            return "vwap_rth", "vendor VWAP of the regular (cash) session"
        if any(x in k for x in ("eth", "globex", "overnight", "extended")):
            return "vwap_eth", "vendor VWAP of the extended (Globex) session"
        return "vwap", "vendor VWAP, session scope not stated"
    if any(x in k for x in ("session", "markethours", "segment", "isrth", "iseth", "regular", "extended", "pit")) \
            or k in ("rth", "eth"):
        return "session_marker", "vendor session marker"
    if any(x in k for x in ("roll", "adjust", "backadj", "continuous", "front")):
        return "roll_info", "vendor roll / adjustment information"
    if any(x in k for x in ("contract", "symbol", "ticker", "instrument", "expir", "series")):
        return "contract", "contract / symbol"
    if any(x in k for x in ("bid", "ask", "delta", "buy", "sell", "uptick", "downtick")):
        return "order_flow", "vendor order-flow field"
    if k in ("oi", "openinterest"):
        return "open_interest", "open interest"
    if any(x in k for x in ("trades", "ticks", "count")):
        return "n_trades", "number of trades or ticks"
    return "other", "not identified"


CANONICAL_TEXT = {
    "ts": "bar timestamp", "date": "bar date", "time": "bar time of day", "open": "first trade price of the bar",
    "high": "highest trade price", "low": "lowest trade price", "close": "last trade price",
    "volume": "contracts traded in the bar", "contract": "contract symbol", "n_trades": "number of trades",
    "bid_volume": "volume at the bid", "ask_volume": "volume at the ask",
}


MAPPED = (*CANONICAL, *OPTIONAL, "ts", "date", "time")


def column_mapping(raw: pd.DataFrame) -> dict[str, str]:
    """{raw column: canonical name} exactly as ``loader.normalize_columns`` maps it."""
    norm = normalize_columns(raw)
    return {str(a): str(b) for a, b in zip(raw.columns, norm.columns, strict=True) if str(b) in MAPPED}


def column_profile(raw: pd.DataFrame, mapping: dict[str, str], n_examples: int = 3) -> list[dict[str, Any]]:
    """dtype, nulls, range and examples of every raw column, with its role."""
    out = []
    n = len(raw)
    for col in raw.columns:
        s = raw[col]
        canon = mapping.get(str(col))
        if canon in CANONICAL_TEXT:
            role, desc = canon, CANONICAL_TEXT[canon]
        else:
            role, desc = extra_role(str(col))
        nn = s.dropna()
        pos = sorted({0, len(nn) // 2, len(nn) - 1}) if len(nn) else []
        row: dict[str, Any] = {
            "column": str(col), "role": role, "description": desc, "dtype": str(s.dtype),
            "nulls": int(s.isna().sum()), "null_pct": float(100 * s.isna().mean()) if n else 0.0,
            "unique": int(nn.nunique()),
            "examples": [str(nn.iloc[i]) for i in pos][:n_examples],
        }
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            row["min"], row["max"] = (float(nn.min()), float(nn.max())) if len(nn) else (None, None)
            row["negatives"] = int((nn < 0).sum())
            row["zeros"] = int((nn == 0).sum())
        else:
            st = nn.astype(str)
            row["min"], row["max"] = (str(st.min()), str(st.max())) if len(st) else (None, None)
            if row["unique"] <= 20:
                row["values"] = {str(k): int(v) for k, v in st.value_counts().head(20).items()}
        out.append(row)
    return out


def special_columns(profile: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Columns per special role the spec asks about (empty list = not present)."""
    keys = ("vwap", "vwap_rth", "vwap_eth", "session_marker", "contract", "roll_info", "n_trades", "order_flow",
            "open_interest")
    return {k: [r["column"] for r in profile if r["role"] == k] for k in keys}


def vwap_scope(bars_ny: pd.DataFrame, col: str, template: SessionTemplate) -> dict[str, Any]:
    """Whether a vendor VWAP column changes during the session (causal running value) or is
    constant per trading date (a full-session value: future information at every bar).

    Diagnostic only: the frozen studies compute their own causal VWAP from OHLCV and never
    read a vendor column."""
    v = pd.to_numeric(bars_ny[col], errors="coerce")
    td = trading_dates(bars_ny.index, template)
    g = v.groupby(td.to_numpy())
    n_unique = g.nunique()
    days = int((g.count() > 10).sum())
    const = int(((n_unique <= 1) & (g.count() > 10)).sum())
    return {"column": col, "days": days, "days_constant": const,
            "verdict": ("constant within the session: a full-session value, never usable intraday" if days and const / days > 0.9
                        else "changes within the session: consistent with a running value")}


# =======================================================================================
# 3. clock: timezone and open/close label
# =======================================================================================
def raw_timestamps(df: pd.DataFrame, ts_format: str | None = None) -> tuple[pd.DatetimeIndex, str]:
    """Timestamps as written in the file and their kind.

    naive  wall-clock values without a zone (returned naive)
    aware  values carrying a UTC offset or 'Z' (returned as naive UTC instants)
    epoch  numbers (returned as naive UTC instants; unit from magnitude)
    """
    if "ts" in df.columns:
        raw = df["ts"]
    elif "date" in df.columns and "time" in df.columns:
        raw = df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip()
    elif "date" in df.columns:
        raw = df["date"]
    else:
        raise ValueError("no timestamp column (expected ts/timestamp/datetime or date+time)")
    if pd.api.types.is_datetime64_any_dtype(raw):
        idx = pd.DatetimeIndex(raw)
        return (idx.tz_convert("UTC").tz_localize(None), "aware") if idx.tz is not None else (idx, "naive")
    if pd.api.types.is_numeric_dtype(raw):
        vals = raw.to_numpy(dtype="float64")
        idx = pd.DatetimeIndex(pd.to_datetime(raw.to_numpy(dtype="int64"), unit=_epoch_unit(vals), utc=True))
        return idx.tz_localize(None), "epoch"
    s = raw.astype(str).str.strip()
    sample = s.iloc[: min(len(s), 2000)]
    if len(sample) and sample.str.contains(_OFFSET_RE, regex=True).mean() > 0.5:
        return pd.DatetimeIndex(pd.to_datetime(s, utc=True, format=ts_format)).tz_localize(None), "aware"
    return pd.DatetimeIndex(pd.to_datetime(s, format=ts_format)), "naive"


def session_starts(clock: pd.DatetimeIndex, min_gap_minutes: int = 45) -> pd.DatetimeIndex:
    """First timestamp after every gap of at least ``min_gap_minutes`` (file clock)."""
    u = pd.DatetimeIndex(clock.dropna().unique()).sort_values()
    if len(u) < 2:
        return u[:0]
    gap = (u[1:] - u[:-1]) >= pd.Timedelta(minutes=min_gap_minutes)
    return u[1:][np.asarray(gap)]


def implied_offsets(starts: pd.DatetimeIndex, anchor: dt.time, tz: str) -> np.ndarray:
    """Minutes by which the file clock is ahead of UTC at each start, if that start is the
    ``anchor`` wall-clock time in ``tz`` on the matching date. One value in [-720, 720)."""
    out = np.full(len(starts), np.nan)
    if not len(starts):
        return out
    base = starts.normalize()
    for shift in (-1, 0, 1):
        wall = base + pd.Timedelta(days=shift) + pd.Timedelta(minutes=minutes_of(anchor))
        inst = wall.tz_localize(tz, ambiguous="NaT", nonexistent="NaT").tz_convert("UTC").tz_localize(None)
        k = np.asarray((starts - inst) / pd.Timedelta(minutes=1), dtype=float)
        ok = np.isfinite(k) & (k >= -720) & (k < 720) & np.isnan(out)
        out[ok] = k[ok]
    return out


def zone_offsets(utc_naive: pd.DatetimeIndex, zone: str) -> np.ndarray:
    """UTC offset of ``zone`` in minutes at each instant."""
    aware = utc_naive.tz_localize("UTC").tz_convert(zone)
    return np.asarray((aware.tz_localize(None) - utc_naive) / pd.Timedelta(minutes=1), dtype=float)


def convert_clock(clock: pd.DatetimeIndex, kind: str, zone: str, label: str, bar_minutes: int) -> tuple[pd.DatetimeIndex, int]:
    """UTC bar-open instants for a (zone, label) reading of the file clock, and the number
    of wall-clock values that do not exist or repeat in ``zone`` (returned as NaT)."""
    if kind == "naive":
        aware = clock.tz_localize(zone, ambiguous="NaT", nonexistent="NaT")
    else:
        aware = clock.tz_localize("UTC")
    n_nat = int(aware.isna().sum() - clock.isna().sum())
    if label == "close":
        aware = aware - pd.Timedelta(minutes=bar_minutes)
    return aware.tz_convert("UTC"), n_nat


def minute_presence(utc_index: pd.DatetimeIndex, template: SessionTemplate) -> dict[str, dict[str, Any]]:
    """Per DST regime of the template zone: share of weekday trading dates with a bar at
    each wall-clock minute (0..1439)."""
    idx = pd.DatetimeIndex(utc_index.dropna())
    local = idx.tz_convert(template.timezone)
    wall = local.tz_localize(None)
    off = np.asarray((wall - idx.tz_convert("UTC").tz_localize(None)) / pd.Timedelta(minutes=1), dtype=float)
    td = trading_dates(local, template)
    df = pd.DataFrame({"td": td.to_numpy(), "m": (wall.hour * 60 + wall.minute).to_numpy(), "off": off})
    df = df[td.weekday.to_numpy() < 5].drop_duplicates(["td", "m"])
    out = {}
    for o, g in df.groupby("off"):
        n = int(g["td"].nunique())
        share = g.groupby("m").size().reindex(range(1440), fill_value=0).to_numpy() / max(n, 1)
        name = {-300.0: "EST", -240.0: "EDT"}.get(float(o), f"UTC{float(o) / 60:+g}h")
        out[name] = {"n_dates": n, "share": share}
    return out


def template_presence(structure: str, template: SessionTemplate) -> np.ndarray:
    m = np.arange(1440)
    if structure == "rth_only":
        return in_time_range(m, template.rth_start, template.rth_end)
    if template.maintenance_break is None:
        return np.ones(1440, dtype=bool)
    return ~in_time_range(m, *template.maintenance_break)


def presence_mismatches(share: np.ndarray, expected: np.ndarray, absent: float = 0.05, present: float = 0.5) -> dict[str, Any]:
    """Minutes that contradict the session template: expected but (almost) never present,
    or not expected but present on most dates. Minutes in between are counted, not judged."""
    bad = (expected & (share <= absent)) | (~expected & (share >= present))
    unsure = (share > absent) & (share < present)
    fmt = [f"{m // 60:02d}:{m % 60:02d}" for m in np.flatnonzero(bad)]
    return {"mismatches": int(bad.sum()), "mismatch_minutes": fmt[:20], "uncertain_minutes": int(unsure.sum())}


def open_spike(utc_index: pd.DatetimeIndex, volume: np.ndarray, template: SessionTemplate) -> dict[str, dict[str, Any]]:
    """Per DST regime: median volume by wall-clock minute around the cash open. The
    09:30 bar must be the largest of 09:20-09:40 and at least twice the 09:20-09:29 level."""
    v = np.asarray(volume, float)
    if not np.isfinite(v).any() or np.nansum(np.abs(v)) == 0:
        return {}
    idx = pd.DatetimeIndex(utc_index)
    local = idx.tz_convert(template.timezone)
    wall = local.tz_localize(None)
    off = np.asarray((wall - idx.tz_convert("UTC").tz_localize(None)) / pd.Timedelta(minutes=1), dtype=float)
    m = (wall.hour * 60 + wall.minute).to_numpy()
    a = minutes_of(template.rth_start)
    keep = (m >= a - 10) & (m <= a + 10) & (local.weekday < 5) & np.isfinite(v)
    df = pd.DataFrame({"m": m[keep], "v": v[keep], "off": off[keep]})
    out = {}
    for o, g in df.groupby("off"):
        med = g.groupby("m")["v"].median().reindex(range(a - 10, a + 11))
        base = float(np.nanmedian(med.loc[a - 10: a - 1].to_numpy()))
        top = int(med.idxmax()) if med.notna().any() else -1
        ratio = float(med.loc[a] / base) if base > 0 and np.isfinite(med.loc[a]) else float("nan")
        name = {-300.0: "EST", -240.0: "EDT"}.get(float(o), f"UTC{float(o) / 60:+g}h")
        out[name] = {"argmax": f"{top // 60:02d}:{top % 60:02d}" if top >= 0 else "n/a", "ratio_0930": ratio,
                     "ok": bool(top == a and np.isfinite(ratio) and ratio >= 2.0), "n_bars": int(len(g))}
    return out


@dataclass
class ClockDecision:
    status: str                      # DECIDED | AMBIGUOUS | NO_MATCH
    tz_in: str | None
    label: str | None
    kind: str                        # naive | aware | epoch
    structure: str | None            # globex (session starts 18:00 New York) | rth_only (09:30)
    bar_minutes: int
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def decided(self) -> bool:
        return self.status == "DECIDED"


def infer_clock(df: pd.DataFrame, template: SessionTemplate, *, ts_format: str | None = None,
                zones: tuple[str, ...] = CANDIDATE_ZONES, min_match: float = 0.98, min_label_share: float = 0.90,
                min_regime_dates: int = 5) -> ClockDecision:
    """Timezone and open/close label of a bar file, from its session structure.

    ``df`` has canonical column names (``loader.normalize_columns``). Method 1 decides,
    methods 2 and 3 must confirm (see the module docstring)."""
    clock, kind = raw_timestamps(df, ts_format)
    ev: dict[str, Any] = {"kind": kind, "rows": int(len(clock)), "unparseable": int(clock.isna().sum())}
    reasons: list[str] = []
    valid = clock[~clock.isna()]
    sec = (valid.second != 0) | (valid.microsecond != 0) | (valid.nanosecond != 0)
    ev["timestamps_with_seconds"] = int(np.asarray(sec).sum())
    vol = pd.to_numeric(df["volume"], errors="coerce").to_numpy(float) if "volume" in df.columns else np.full(len(df), np.nan)
    traded = np.isfinite(vol) & (vol > 0)
    if traded.any():  # a vendor that fills empty minutes with zero-volume bars must not hide the session structure
        ev["rows_without_volume_ignored"] = int((~traded).sum())
        clock, vol = clock[traded], vol[traded]
        valid = clock[~clock.isna()]
    uniq = pd.DatetimeIndex(valid.unique()).sort_values()
    bar = infer_bar_minutes(uniq)
    ev["modal_spacing_minutes"] = int(bar)
    ev["first_raw"], ev["last_raw"] = (str(uniq.min()), str(uniq.max())) if len(uniq) else ("", "")
    starts = session_starts(uniq)
    ev["session_starts"] = int(len(starts))

    def fail(status: str, why: str) -> ClockDecision:
        return ClockDecision(status, None, None, kind, ev.get("structure"), bar, reasons + [why], ev)

    if ev["unparseable"]:
        reasons.append(f"{ev['unparseable']} timestamps could not be parsed")
    if ev["timestamps_with_seconds"]:
        reasons.append(f"{ev['timestamps_with_seconds']} timestamps are not on whole minutes: the bar convention is unclear")
    if len(starts) < 10:
        return fail("NO_MATCH", f"only {len(starts)} session starts (gaps >= 45 min): too little structure to decide")

    # ---- method 1: implied UTC offset of every session start ------------------------------
    anchors = {}
    for name, anchor in (("globex", template.trading_day_start), ("rth_only", template.rth_start)):
        k = implied_offsets(starts, anchor, template.timezone)
        r = np.mod(k, 60)
        aligned = np.isfinite(k) & np.isin(r, (0, bar % 60))
        anchors[name] = (k, r, aligned)
    structure = max(anchors, key=lambda n: anchors[n][2].mean())
    k, r, aligned = anchors[structure]
    ev["structure"] = structure
    ev["anchor_alignment"] = {n: float(a[2].mean()) for n, a in anchors.items()}
    if aligned.mean() < 0.9:
        return fail("NO_MATCH", "session starts are not at 18:00 or 09:30 New York time under any whole-hour offset "
                                f"(best alignment {aligned.mean():.1%} for {structure})")
    ev["unaligned_starts"] = [str(t) for t in starts[~aligned][:10]]
    rr, kk = r[aligned], k[aligned]
    shares = {"open": float(np.mean(rr == 0)), "close": float(np.mean(rr == bar % 60))}
    label = max(shares, key=shares.get)
    ev["label_shares"] = shares
    if shares[label] < min_label_share:
        reasons.append(f"session starts split between open and close labels {shares}")
    off = kk - rr
    ev["implied_offsets_minutes"] = {f"{o:+.0f}": int(c) for o, c in zip(*np.unique(off, return_counts=True), strict=True)}
    inst = starts[aligned] - pd.to_timedelta(kk, unit="min")
    zone_list = zones if kind == "naive" else ("UTC",)
    rows = []
    for z in zone_list:
        zo = zone_offsets(pd.DatetimeIndex(inst), z)
        rows.append({"zone": z, "match": float(np.mean(zo == off)), "_vec": zo})
    if kind != "naive":  # explain a conflict: which wall clock the 'instants' really follow
        for z in zones:
            zo = zone_offsets(pd.DatetimeIndex(inst), z)
            if z != "UTC" and np.mean(zo == off) >= min_match:
                reasons.append(f"embedded offsets contradict the session structure; the values follow {z} wall-clock time")
    rows.sort(key=lambda x: -x["match"])
    best = rows[0]
    equiv = [x["zone"] for x in rows if np.array_equal(x["_vec"], best["_vec"])]
    chosen = next(z for z in zone_list if z in equiv)
    others = [x for x in rows if x["zone"] not in equiv]
    runner_up = others[0]["match"] if others else 0.0
    ev["zones"] = [{"zone": x["zone"], "match": x["match"], "equivalent_to_best": x["zone"] in equiv} for x in rows]
    if len(equiv) > 1:
        ev["equivalent_zones"] = equiv
    if best["match"] < min_match:
        reasons.append(f"no timezone explains the session starts (best {best['zone']} {best['match']:.1%})")
    elif best["match"] - runner_up < 0.05:
        reasons.append(f"{chosen} and {others[0]['zone']} explain the session starts almost equally well")

    # ---- methods 2 and 3 for the chosen reading and its nearest rivals ----------------------
    expected = template_presence(structure, template)
    checks = []
    rivals = [(chosen, label), (chosen, "close" if label == "open" else "open")]
    if others and kind == "naive":
        rivals.append((others[0]["zone"], label))
    for z, lab in rivals:
        utc, n_nat = convert_clock(clock, kind, z, lab, bar)
        pres = minute_presence(utc, template)
        mism = {reg: presence_mismatches(p["share"], expected) | {"n_dates": p["n_dates"]} for reg, p in pres.items()}
        spike = open_spike(utc, vol, template) if structure == "globex" else {}
        checks.append({"zone": z, "label": lab, "nonexistent_or_repeated": n_nat, "presence": mism, "spike": spike})
    ev["checks"] = checks
    mine = checks[0]
    for reg, m in mine["presence"].items():
        if m["n_dates"] >= min_regime_dates and m["mismatches"]:
            reasons.append(f"{reg}: {m['mismatches']} minutes contradict the session template ({m['mismatch_minutes'][:6]})")
    regimes = [reg for reg, m in mine["presence"].items() if m["n_dates"] >= min_regime_dates]
    ev["regimes"] = regimes
    if structure == "globex" and kind == "naive" and not ({"EST", "EDT"} <= set(regimes)):
        ev["single_regime"] = True  # cannot separate a DST zone from its fixed-offset twin; noted in the report
    for reg, s in mine["spike"].items():
        if mine["presence"].get(reg, {}).get("n_dates", 0) >= min_regime_dates and not s["ok"]:
            reasons.append(f"{reg}: no cash-open volume spike at 09:30 (largest at {s['argmax']}, ratio {s['ratio_0930']:.2f})")
    if not mine["spike"] and structure == "globex":
        ev["spike_unavailable"] = "volume missing or zero: method 3 not available"
    if mine["nonexistent_or_repeated"]:
        reasons.append(f"{mine['nonexistent_or_repeated']} timestamps do not exist or repeat in {chosen}")
    if bar != 1:
        reasons.append(f"bars are {bar} minutes apart, not 1 minute")
    status = "DECIDED" if not reasons else "AMBIGUOUS"
    return ClockDecision(status, chosen if status == "DECIDED" else None, label if status == "DECIDED" else None, kind,
                         structure, bar, reasons, ev)


# =======================================================================================
# 4. canonical input frame
# =======================================================================================
def normalise(df: pd.DataFrame, clock: ClockDecision, *, max_removed_share: float = 1e-4,
              ts_format: str | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Canonical input rows: ``ts`` (UTC bar open), OHLCV and the optional columns.

    Rows stay in file order; duplicates stay (``manifest.prepare`` counts and resolves
    them). Impossible bars are removed, and each removal is counted: NaN or non-positive
    prices, high below low, open or close outside [low, high], NaN or negative volume.
    Above ``max_removed_share`` of all rows a ``StopCondition`` is raised instead.
    Zero-volume bars are removed and counted separately (flat fillers and bars with a
    range): a bar without a trade carries no trade price, and the pipeline's own bars
    exist only for minutes with trades. If most bars have no volume, the volume-based
    studies cannot run and a ``StopCondition`` is raised. A removed bar becomes a missing
    minute; nothing is filled."""
    if not clock.decided:
        raise StopCondition(f"clock not decided ({clock.status}): {clock.reasons}")
    raw_clock, kind = raw_timestamps(df, ts_format)
    utc, n_nat = convert_clock(raw_clock, kind, clock.tz_in or "UTC", clock.label or "open", clock.bar_minutes)
    out = pd.DataFrame({"ts": utc})
    for c in CANONICAL:
        out[c] = pd.to_numeric(df[c], errors="coerce").to_numpy(float)
    for c in ("bid_volume", "ask_volume", "n_trades"):
        if c in df.columns:
            out[c] = pd.to_numeric(df[c], errors="coerce").to_numpy(float)
    if "contract" in df.columns:
        out["contract"] = df["contract"].astype(str).to_numpy()
    o, h, lo, c, v = (out[k].to_numpy(float) for k in CANONICAL)
    raw_nat = np.asarray(raw_clock.isna(), bool)
    with np.errstate(invalid="ignore"):
        priced = np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c)
        # A bar with zero volume had no trade: a vendor's filler for an empty minute, not a
        # trade price. It is removed and counted on its own (no error in the data, so no
        # limit), whatever its prices or wall-clock time; an unparseable timestamp stays an error.
        zero = np.isfinite(v) & (v == 0) & ~raw_nat
        masks = {
            "unparseable timestamp": raw_nat,
            "wall-clock time that does not exist or repeats in the decided zone": out["ts"].isna().to_numpy() & ~raw_nat & ~zero,
            "NaN price": ~priced & ~zero,
            "non-positive price": ((o <= 0) | (h <= 0) | (lo <= 0) | (c <= 0)) & ~zero,
            "high below low": (h < lo) & ~zero,
            "open outside [low, high]": ((o > h) | (o < lo)) & ~zero,
            "close outside [low, high]": ((c > h) | (c < lo)) & ~zero,
            "NaN volume": ~np.isfinite(v),
            "negative volume": v < 0,
        }
    drop = np.zeros(len(out), dtype=bool)
    log: dict[str, Any] = {"rows_in": int(len(out)), "removed": {}, "nonexistent_or_repeated_wall_times": n_nat}
    for why, m in masks.items():
        m = np.asarray(m, bool)
        log["removed"][why] = {"count": int(m.sum()), "examples": [str(x) for x in df.index[m][:5]]}
        drop |= m
    log["removed_total"] = int(drop.sum())
    log["removed_share"] = float(drop.mean()) if len(drop) else 0.0
    if log["removed_share"] > max_removed_share:
        counts = ", ".join(f"{k}: {x['count']}" for k, x in log["removed"].items() if x["count"])
        raise StopCondition(f"{log['removed_total']} impossible bars ({log['removed_share']:.4%}) exceed the limit "
                            f"{max_removed_share:.4%} ({counts})")
    if len(v) and np.mean(np.isfinite(v) & (v > 0)) < 0.5:
        raise StopCondition("volume is zero or missing for most bars: the volume-based studies cannot run on this file")
    zero &= ~drop
    with np.errstate(invalid="ignore"):
        flat = zero & priced & (o == h) & (h == lo) & (lo == c)
    ranged = zero & priced & ~flat
    log["no_trade_bars"] = {"count": int(zero.sum()), "share": float(zero.mean()) if len(zero) else 0.0,
                            "flat": int(flat.sum()), "with_range": int(ranged.sum()),
                            "without_price": int((zero & ~priced).sum()),
                            "nonexistent_or_repeated_wall_times": int((zero & out["ts"].isna().to_numpy()).sum()),
                            "examples": [str(x) for x in df.index[zero][:5]],
                            "examples_with_range": [str(x) for x in df.index[ranged][:5]]}
    drop |= zero
    out = out[~drop].reset_index(drop=True)
    log["rows_out"] = int(len(out))
    return out, log


def to_ny_bars(frame: pd.DataFrame, tz: str = "America/New_York") -> pd.DataFrame:
    """The canonical frame indexed by bar-open time in ``tz`` (stable sort, duplicates kept)."""
    bars = frame.set_index(pd.DatetimeIndex(frame["ts"]).tz_convert(tz)).drop(columns=["ts"])
    bars.index.name = "ts"
    return bars.sort_index(kind="stable")


# =======================================================================================
# 5. extra quality checks
# =======================================================================================
def _nth_weekday(y: int, m: int, weekday: int, n: int) -> dt.date:
    d = dt.date(y, m, 1)
    return d + dt.timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(y: int, m: int, weekday: int) -> dt.date:
    d = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=1) if d.weekday() == 5 else (d + dt.timedelta(days=1) if d.weekday() == 6 else d)


SPECIAL_CLOSURES = {dt.date(2018, 12, 5): "National Day of Mourning (G. H. W. Bush)",
                    dt.date(2025, 1, 9): "National Day of Mourning (J. Carter)"}


def nyse_holidays(first_year: int, last_year: int) -> list[tuple[dt.date, str]]:
    """NYSE full-day holidays (observed dates). CME equity-index futures trade an
    abbreviated Globex session on most of them; the bars show which."""
    out = []
    for y in range(first_year, last_year + 1):
        ny = dt.date(y, 1, 1)
        if ny.weekday() != 5:  # a Saturday New Year's Day is not observed on the Friday before
            out.append((_observed(ny), "New Year's Day"))
        out += [(_nth_weekday(y, 1, 0, 3), "Martin Luther King Jr. Day"), (_nth_weekday(y, 2, 0, 3), "Washington's Birthday"),
                (easter(y) - dt.timedelta(days=2), "Good Friday"), (_last_weekday(y, 5, 0), "Memorial Day")]
        if y >= 2022:
            out.append((_observed(dt.date(y, 6, 19)), "Juneteenth"))
        out += [(_observed(dt.date(y, 7, 4)), "Independence Day"), (_nth_weekday(y, 9, 0, 1), "Labor Day"),
                (_nth_weekday(y, 11, 3, 4), "Thanksgiving"), (_observed(dt.date(y, 12, 25)), "Christmas")]
    out += [(d, n) for d, n in SPECIAL_CLOSURES.items() if first_year <= d.year <= last_year]
    return sorted(out)


def _hhmm(t: pd.Timestamp) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def extra_quality(frame: pd.DataFrame, bars: pd.DataFrame, template: SessionTemplate, *, bar_minutes: int = 1,
                  raw_order: pd.DatetimeIndex | None = None) -> dict[str, Any]:
    """Checks beyond ``quality.check_bars``. ``frame`` is the canonical input (UTC ``ts``
    in file order), ``bars`` the same rows indexed in New York time and sorted."""
    rep: dict[str, Any] = {}
    ts = pd.DatetimeIndex(frame["ts"])
    rep["raw_backward_steps"] = int((np.diff(_ns(ts)) < 0).sum()) if len(ts) > 1 else 0
    cols = [c for c in CANONICAL if c in frame.columns]
    dup_ts = ts.duplicated(keep=False)
    same = frame.duplicated(subset=["ts", *cols], keep="first").to_numpy()
    rep["duplicate_timestamps"] = int(ts.duplicated().sum())
    rep["identical_duplicate_rows"] = int(same.sum())
    conflicting = frame.loc[dup_ts & ~frame.duplicated(subset=["ts", *cols], keep=False).to_numpy()]
    rep["conflicting_duplicate_timestamps"] = int(pd.DatetimeIndex(conflicting["ts"]).nunique())
    rep["conflicting_examples"] = [str(t) for t in pd.DatetimeIndex(conflicting["ts"]).unique()[:5]]
    if "contract" in frame.columns:  # several contracts per minute = a multi-contract file, not one series
        per_ts = frame.groupby("ts")["contract"].nunique()
        rep["contracts"] = int(frame["contract"].nunique())
        rep["minutes_with_several_contracts"] = int((per_ts > 1).sum())
        rep["contract_counts"] = {str(k): int(v) for k, v in frame["contract"].value_counts().head(12).items()}

    idx = bars.index
    wall = idx.tz_localize(None)
    td = trading_dates(idx, template)
    mins = (wall.hour * 60 + wall.minute).to_numpy()
    wd = td.weekday.to_numpy()
    weekend = wd >= 5
    rep["weekend_bars"] = int(weekend.sum())
    rep["weekend_examples"] = [str(t) for t in idx[weekend][:5]]
    brk = in_time_range(mins, *template.maintenance_break) if template.maintenance_break else np.zeros(len(idx), bool)
    rep["break_bars"] = int(brk.sum())
    rep["break_examples"] = [str(t) for t in idx[brk][:5]]

    o, h, lo, c, v = (bars[k].to_numpy(float) for k in CANONICAL)
    same_prev = np.r_[False, (o[1:] == o[:-1]) & (h[1:] == h[:-1]) & (lo[1:] == lo[:-1]) & (c[1:] == c[:-1]) & (v[1:] == v[:-1])]
    rep["identical_consecutive_bars"] = int(same_prev.sum())
    run = pd.Series(same_prev).groupby((~pd.Series(same_prev)).cumsum()).cumsum()
    rep["longest_identical_run"] = int(run.max()) if len(run) else 0

    # session starts (first bar after >= 45 min) by DST regime, in New York wall-clock time
    step = np.r_[np.inf, np.diff(_ns(idx)) / 6e10]
    first = step >= 45
    off = np.asarray((wall - idx.tz_convert("UTC").tz_localize(None)) / pd.Timedelta(minutes=1), dtype=float)
    starts = pd.DataFrame({"t": [_hhmm(t) for t in wall[first]], "off": off[first]})
    start_rows = []
    for o_, g in starts.groupby("off"):
        vc = g["t"].value_counts()
        start_rows.append({"regime": {-300.0: "EST", -240.0: "EDT"}.get(float(o_), str(o_)), "starts": int(len(g)),
                           "at_18:00": int((g["t"] == _hhmm(pd.Timestamp("2000-01-01 18:00"))).sum()),
                           "other_times": {str(a): int(b) for a, b in vc.drop("18:00", errors="ignore").head(6).items()}})
    rep["session_starts_by_regime"] = start_rows

    # missing minutes on regular dates (full RTH, no early close)
    rth = in_time_range(mins, template.rth_start, template.rth_end)
    per = pd.DataFrame({"td": td.to_numpy(), "rth": rth, "m": mins, "t": idx}).drop_duplicates(["td", "t"])
    g = per.groupby("td")
    cal = pd.DataFrame({"n": g.size(), "n_rth": g["rth"].sum()})
    rth_last = per[per["rth"]].groupby("td")["m"].max().reindex(cal.index)
    rth_first = per[per["rth"]].groupby("td")["m"].min().reindex(cal.index)
    exp_rth = (minutes_of(template.rth_end) - minutes_of(template.rth_start)) // bar_minutes
    full = (rth_first == minutes_of(template.rth_start)) & (rth_last >= minutes_of(template.rth_end) - bar_minutes)
    exp_day = (24 * 60 - (60 if template.maintenance_break else 0)) // bar_minutes
    reg = cal[full.fillna(False).to_numpy() & (pd.DatetimeIndex(cal.index).weekday < 5)]
    miss = (exp_day - reg["n"]).clip(lower=0)
    miss_rth = (exp_rth - reg["n_rth"]).clip(lower=0)
    rep["regular_dates"] = int(len(reg))
    rep["missing_minutes_regular_dates"] = int(miss.sum())
    rep["missing_minutes_per_date"] = {"median": float(miss.median()) if len(miss) else 0.0,
                                       "p95": float(miss.quantile(0.95)) if len(miss) else 0.0,
                                       "max": int(miss.max()) if len(miss) else 0}
    rep["missing_rth_minutes_regular_dates"] = int(miss_rth.sum())
    rep["dates_missing_over_30_minutes"] = [str(pd.Timestamp(d).date()) for d in miss[miss > 30].index[:30]]

    # holidays
    tds = pd.DatetimeIndex(sorted(set(td)))
    hol = []
    if len(tds):
        for d, name in nyse_holidays(tds.min().year, tds.max().year):
            if not (tds.min().date() <= d <= tds.max().date()):
                continue
            sel = td == pd.Timestamp(d)
            n = int(sel.sum())
            row = {"date": str(d), "holiday": name, "bars": n, "rth_bars": int((sel & rth).sum())}
            if n:
                row["first_bar"], row["last_bar"] = str(idx[sel].min()), str(idx[sel].max())
                row["status"] = "full RTH" if row["rth_bars"] >= exp_rth * 0.95 else "abbreviated session"
            else:
                row["status"] = "no bars (closed)"
            hol.append(row)
    rep["holidays"] = hol
    return rep


# =======================================================================================
# 6. rolls
# =======================================================================================
def quarterly_expiries(start: dt.date, end: dt.date, months: tuple[int, ...] = (3, 6, 9, 12)) -> list[dt.date]:
    return sorted(third_friday(y, m) for y in range(start.year - 1, end.year + 2) for m in months
                  if start <= third_friday(y, m) <= end)


def contract_code(root: str, expiry: dt.date) -> str:
    return f"{root}{MONTH_CODES[expiry.month]}{expiry.year % 100:02d}"


def bar_gaps(bars: pd.DataFrame, template: SessionTemplate, bar_minutes: int = 1) -> pd.DataFrame:
    """Price gap ``open[i] - close[i-1]`` between consecutive bars, with its kind:
    bar (adjacent bars), gap (missing minutes inside a session), open (first bar after the
    daily break), open_long (first bar after a weekend or closure)."""
    idx = bars.index
    td = trading_dates(idx, template).to_numpy()
    o, c = bars["open"].to_numpy(float), bars["close"].to_numpy(float)
    dmin = np.r_[np.nan, np.diff(_ns(idx)) / 6e10]
    same = np.r_[False, td[1:] == td[:-1]]
    kind = np.where(same & (dmin <= bar_minutes), "bar", np.where(same, "gap", np.where(dmin >= 24 * 60, "open_long", "open")))
    g = np.r_[np.nan, o[1:] - c[:-1]]
    return pd.DataFrame({"td": td, "kind": kind, "gap": g, "minutes": dmin}, index=idx)


def _busday_offset(td: pd.Timestamp, expiry: dt.date) -> int:
    """Business days from expiry to ``td`` (negative before expiry)."""
    return int(np.busday_count(np.datetime64(expiry, "D"), np.datetime64(pd.Timestamp(td).date(), "D")))


def assess_rolls(bars: pd.DataFrame, template: SessionTemplate, roll_cfg: dict[str, Any], tick_size: float, *,
                 bar_minutes: int = 1, root: str = "NQ", window_days: tuple[int, int] = (16, 3), clear_z: float = 8.0,
                 dominance: float = 3.0) -> dict[str, Any]:
    """Series type, vendor roll timing and coverage by the frozen roll exclusion.

    The vendor's roll shows as a price gap of one calendar spread between the last bar
    of the old and the first bar of the new contract. Per quarterly expiry E, the window
    [E - window_days[0], E + window_days[1]] is searched for the largest gap relative
    to a robust scale of its kind measured outside all windows. A gap is CLEAR when it is
    at least ``clear_z`` scales and ``dominance`` times the next-largest in the window."""
    rep: dict[str, Any] = {"root": root, "params": {"window_days": list(window_days), "clear_z": clear_z, "dominance": dominance}}
    px = np.concatenate([bars[k].to_numpy(float) for k in ("open", "high", "low", "close")])
    px = px[np.isfinite(px)]
    q = px / tick_size
    rep["tick_grid_conformity_pct"] = float(100 * np.mean(np.abs(q - np.round(q)) < 1e-6)) if len(px) else float("nan")
    rep["min_price"] = float(px.min()) if len(px) else float("nan")
    gaps = bar_gaps(bars, template, bar_minutes)
    tds = pd.DatetimeIndex(sorted(set(gaps["td"])))
    if not len(tds):
        rep.update(classification="unclear", conclusion="no data")
        return rep
    months = tuple(roll_cfg.get("months", (3, 6, 9, 12)))
    pre, post = window_days
    exps = quarterly_expiries((tds.min() - pd.Timedelta(days=post)).date(), (tds.max() + pd.Timedelta(days=pre)).date(), months)
    in_win = np.zeros(len(gaps), dtype=bool)
    tdv = pd.DatetimeIndex(gaps["td"])
    for e in exps:
        in_win |= np.asarray((tdv >= pd.Timestamp(e) - pd.Timedelta(days=pre)) & (tdv <= pd.Timestamp(e) + pd.Timedelta(days=post)))
    absg = gaps["gap"].abs().to_numpy()
    scales = {}
    for kind, qq in (("bar", 0.999), ("gap", 0.99), ("open", 0.99), ("open_long", 0.99)):
        sel = (gaps["kind"].to_numpy() == kind) & ~in_win & np.isfinite(absg)
        vals = absg[sel]
        scales[kind] = {"n": int(sel.sum()), "median": float(np.median(vals)) if len(vals) else float("nan"),
                        "quantile": qq, "scale": float(max(np.quantile(vals, qq), tick_size)) if len(vals) else float("nan")}
    rep["gap_scales_outside_windows"] = scales
    sc = gaps["kind"].map({k: v["scale"] for k, v in scales.items()}).to_numpy(float)
    z = absg / sc
    gaps = gaps.assign(z=z)

    # daily RTH volume for the liquidity diagnostic
    wall = bars.index.tz_localize(None)
    mins = (wall.hour * 60 + wall.minute).to_numpy()
    rth = in_time_range(mins, template.rth_start, template.rth_end)
    dvol = pd.Series(bars["volume"].to_numpy(float)[rth]).groupby(tdv[rth]).sum()

    windows = []
    for e in exps:
        a, b = pd.Timestamp(e) - pd.Timedelta(days=pre), pd.Timestamp(e) + pd.Timedelta(days=post)
        w = gaps[(tdv >= a) & (tdv <= b) & np.isfinite(gaps["z"].to_numpy())]
        row: dict[str, Any] = {"expiry": str(e), "from": contract_code(root, e),
                               "covered": bool(tds.min() <= a and tds.max() >= b), "clear": False}
        if len(w):
            top = w.sort_values("z", ascending=False).head(3)
            t1 = top.iloc[0]
            z2 = float(top["z"].iloc[1]) if len(top) > 1 else 0.0
            row.update({
                "jump_ts": str(top.index[0]), "jump_td": str(pd.Timestamp(t1["td"]).date()), "jump_kind": str(t1["kind"]),
                "jump_points": float(t1["gap"]), "z": float(t1["z"]), "z_next": z2,
                "offset_bd": _busday_offset(t1["td"], e), "jump_time_ny": _hhmm(top.index[0]),
                "clear": bool(t1["z"] >= clear_z and t1["z"] >= dominance * z2),
                "top3": [{"ts": str(i), "kind": str(r_["kind"]), "points": float(r_["gap"]), "z": float(r_["z"])} for i, r_ in top.iterrows()],
            })
        bd = dvol.index
        pre_v = dvol[(bd < pd.Timestamp(e)) & (bd >= pd.Timestamp(e) - pd.tseries.offsets.BDay(5))]
        base_v = dvol[(bd <= pd.Timestamp(e) - pd.tseries.offsets.BDay(10)) & (bd >= pd.Timestamp(e) - pd.tseries.offsets.BDay(25))]
        row["volume_ratio_last5_vs_base"] = (float(pre_v.median() / base_v.median())
                                             if len(pre_v) and len(base_v) and base_v.median() > 0 else float("nan"))
        windows.append(row)
    rep["windows"] = windows

    assess = [w for w in windows if w["covered"]]
    clear = [w for w in assess if w["clear"]]
    rep["windows_assessable"] = len(assess)
    rep["windows_clear"] = len(clear)
    share = len(clear) / len(assess) if assess else 0.0
    rep["clear_share"] = share
    if "contract" in bars.columns:
        rep["classification"] = "contract_column"
    elif rep["tick_grid_conformity_pct"] < 99.0:
        rep["classification"] = "ratio_adjusted"
    elif not assess:
        rep["classification"] = "unclear"
    elif share >= 0.5:
        rep["classification"] = "unadjusted"
    elif share <= 0.1:
        rep["classification"] = "adjusted_or_gapless"
    else:
        rep["classification"] = "unclear"

    if rep["classification"] == "unadjusted":
        offs = pd.Series([w["offset_bd"] for w in clear])
        kinds = pd.Series([w["jump_kind"] for w in clear])
        mode_off = int(offs.mode().iloc[0])
        rep["rule"] = {"offset_bd_mode": mode_off, "offset_bd_share": float((offs == mode_off).mean()),
                       "offsets": {str(k): int(v) for k, v in offs.value_counts().items()},
                       "kind_mode": str(kinds.mode().iloc[0]), "kind_share": float((kinds == kinds.mode().iloc[0]).mean()),
                       "positive_share": float(np.mean([w["jump_points"] > 0 for w in clear]))}
        rep["rule"]["consistent"] = bool(rep["rule"]["offset_bd_share"] >= 0.75)
        # conventional exclusion of the runner (protocol 13.3, no contract column)
        rth_dates = pd.DatetimeIndex(sorted(set(tdv[rth])))
        rd = equity_index_roll_dates(tds.min().date(), tds.max().date(), months, int(roll_cfg.get("roll_offset_days", 8)))
        excl = roll_exclusion_dates(rd, rth_dates, sessions_after=1)
        rolls = []
        for w in assess:
            e = dt.date.fromisoformat(w["expiry"])
            if w["clear"]:
                pos, method = pd.Timestamp(w["jump_ts"]), "detected"
            elif rep["rule"]["consistent"]:
                d = pd.Timestamp(np.busday_offset(np.datetime64(e, "D"), mode_off, roll="forward"))
                first = gaps.index[(tdv == d)]
                pos, method = (first.min(), "rule") if len(first) else (None, "unresolved")
            else:
                pos, method = None, "unresolved"
            r_td = trading_dates(pd.DatetimeIndex([pos]), template)[0] if pos is not None else None
            rolls.append({"expiry": w["expiry"], "from": contract_code(root, e), "first_bar_new": str(pos) if pos is not None else None,
                          "trading_date": str(r_td.date()) if r_td is not None else None, "method": method,
                          "covered_by_frozen_exclusion": bool(r_td is not None and r_td in excl)})
        rep["rolls"] = rolls
        rep["frozen_exclusion_dates"] = sorted(str(pd.Timestamp(d).date()) for d in excl)
        rep["all_rolls_resolved"] = all(r["method"] != "unresolved" for r in rolls)
        rep["all_rolls_covered"] = rep["all_rolls_resolved"] and all(r["covered_by_frozen_exclusion"] for r in rolls)
    rep["conclusion"] = roll_conclusion(rep)
    return rep


def roll_conclusion(rep: dict[str, Any]) -> dict[str, Any]:
    """What the classification means for the frozen pipeline: ok, adjustment, stop reason."""
    cls = rep.get("classification")
    if cls == "contract_column":
        return {"ok": True, "adjustment": "unadjusted", "runner_path": "contract column: the trading date of every change is excluded"}
    if cls == "unadjusted":
        if rep.get("all_rolls_covered"):
            return {"ok": True, "adjustment": "unadjusted",
                    "runner_path": "no contract column: the frozen conventional exclusion (roll date and next session) covers every vendor roll"}
        if not rep.get("all_rolls_resolved"):
            return {"ok": False, "adjustment": "unadjusted",
                    "stop": "the timing of some vendor rolls cannot be determined, so their gap sessions cannot be excluded"}
        return {"ok": False, "adjustment": "unadjusted",
                "stop": "the vendor rolls on other dates than the frozen conventional rule: the runner would treat roll-gap "
                        "sessions as event days (protocol 2.3). Excluding the vendor's roll dates instead needs a decision"}
    if cls == "adjusted_or_gapless":
        return {"ok": False, "adjustment": "difference",
                "stop": "no roll gaps although the calendar spread is large: the series is back-adjusted, so its prices are not "
                        "the traded historical levels (round-number studies C014/C015 and all absolute levels are affected)"}
    if cls == "ratio_adjusted":
        return {"ok": False, "adjustment": "ratio",
                "stop": "prices are off the tick grid: ratio back-adjusted; absolute levels and 1-tick profile bins are invalid"}
    return {"ok": False, "adjustment": "unknown", "stop": "the series type cannot be determined from the data"}


def contract_labels(index: pd.DatetimeIndex, rolls: list[dict[str, Any]], root: str = "NQ",
                    months: tuple[int, ...] = (3, 6, 9, 12)) -> np.ndarray:
    """Contract code per bar from roll positions (first bar of each new contract). Bars
    before a roll carry the contract that expires at that roll's expiry. Only used to let
    the runner exclude the vendor's roll sessions (its contract-column path)."""
    rs = sorted((pd.Timestamp(r["first_bar_new"]), dt.date.fromisoformat(r["expiry"])) for r in rolls if r.get("first_bar_new"))
    if not rs:
        raise ValueError("no roll positions")
    exps = [e for _, e in rs]
    nxt = quarterly_expiries(exps[-1] + dt.timedelta(days=1), exps[-1] + dt.timedelta(days=200), months)[0]
    codes = np.array([contract_code(root, e) for e in [*exps, nxt]])
    pos = np.searchsorted(np.array([t.as_unit("ns").value for t, _ in rs]), _ns(index), side="right")
    return codes[pos]


# =======================================================================================
# identity
# =======================================================================================
def file_identity(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    return {"name": p.name, "path": str(p), "bytes": p.stat().st_size, "sha256": file_sha256(p), "kind": sniff(p)}


_SAFE_ID = re.compile(r"^[A-Za-z0-9 _.()+,-]+$")


def safe_name(name: str) -> bool:
    """A plain file name: letters, digits, space and ``_.()+,-``; no separators, no ``..``."""
    return bool(_SAFE_ID.match(name)) and name.strip(" .") != "" and ".." not in name
