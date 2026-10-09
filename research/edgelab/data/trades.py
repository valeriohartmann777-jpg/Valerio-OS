"""Trade records from Databento DBN files: validation counters and 1-minute aggregates.

Prices stay in DBN's fixed-point integers (1e-9 units), so tick-grid checks and the
volume-at-price keys are exact. Times are the exchange timestamp ``ts_event`` in UTC
nanoseconds; session logic converts through the tz database (``sessions.py``), never
through fixed offsets.

Nothing is dropped silently. Exact duplicate records (identical in every field,
including the venue sequence number and the capture time) are the only rows removed,
and they are counted. Spreads and other non-outright instruments stay in the raw files
and are counted; they never enter bars or profiles. A minute without trades has no bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import SessionTemplate
from ..sessions import label_sessions, trading_dates

FIXED = 1_000_000_000          # DBN fixed-point scale
NS_MIN = 60 * 1_000_000_000
EXAMPLES = 5


def read_trade_array(path: Path) -> np.ndarray:
    """All records of a trades DBN file as a structured array (fields as in ``TradeMsg``)."""
    import databento as db

    return db.DBNStore.from_file(path).to_ndarray()


def _examples(ts_ns: np.ndarray, mask: np.ndarray, n: int = EXAMPLES) -> list[str]:
    return [str(pd.Timestamp(int(t), tz="UTC")) for t in ts_ns[mask][:n]]


def exact_duplicates(arr: np.ndarray) -> np.ndarray:
    """Mask of records identical in every byte to an earlier record (the first copy stays).

    The packed DBN record is compared as 8-byte words, so two trades that differ in any
    field (price, size, side, venue sequence, capture time, ...) are never duplicates.
    """
    n = len(arr)
    dup = np.zeros(n, dtype=bool)
    if n < 2:
        return dup
    rec = np.ascontiguousarray(arr)
    if rec.dtype.itemsize % 8:
        raise ValueError(f"record size {rec.dtype.itemsize} is not a multiple of 8 bytes")
    words = rec.view(np.uint64).reshape(n, rec.dtype.itemsize // 8)
    order = np.lexsort(words.T[::-1])
    w = words[order]
    same = (w[1:] == w[:-1]).all(axis=1)
    dup[order[1:][same]] = True
    return dup


@dataclass
class ChunkResult:
    bars: pd.DataFrame            # every outright: one row per (instrument_id, minute)
    mpv: pd.DataFrame             # active contract only: one row per (minute, price)
    checks: dict[str, Any] = field(default_factory=dict)


def minute_labels(minutes_ns: np.ndarray, template: SessionTemplate) -> pd.DataFrame:
    """Session labels of unique UTC minute starts (trading date, phase, weekday, wall clock)."""
    idx = pd.DatetimeIndex(pd.to_datetime(minutes_ns, unit="ns", utc=True))
    lab = label_sessions(idx, template)
    lab.index = pd.Index(minutes_ns, name="minute")
    return lab


def outside_session(lab: pd.DataFrame) -> np.ndarray:
    """Minutes in which a CME equity-index future cannot trade: the 17:00-18:00 ET break
    and the weekend (Friday 17:00 to Sunday 18:00 ET belongs to Saturday/Sunday trade
    dates, which do not exist)."""
    in_break = (lab["phase"].astype(str) == "BREAK").to_numpy()
    weekend = lab["weekday"].to_numpy() >= 5
    return in_break | weekend


def process_chunk(
    arr: np.ndarray,
    *,
    chunk_index: int,
    contracts: pd.DataFrame,
    tick_size: float,
    request_start_ns: int,
    request_end_ns: int,
    template: SessionTemplate,
    active_by_date: dict[pd.Timestamp, int],
) -> ChunkResult:
    """Validate one file's trades and aggregate them to 1-minute bars and minute-price volume."""
    n = len(arr)
    chk: dict[str, Any] = {"records": int(n)}
    if n == 0:
        return ChunkResult(pd.DataFrame(), pd.DataFrame(), chk)
    ts_recv = arr["ts_recv"].astype(np.int64)
    ts_event = arr["ts_event"].astype(np.int64)
    iid = arr["instrument_id"].astype(np.int64)
    price = arr["price"].astype(np.int64)
    size = arr["size"].astype(np.int64)
    pos = np.arange(n, dtype=np.int64)

    act, cnt = np.unique(arr["action"], return_counts=True)
    chk["actions"] = {a.decode() if isinstance(a, bytes) else str(a): int(c) for a, c in zip(act, cnt)}
    chk["ts_recv_decreases"] = int((np.diff(ts_recv) < 0).sum())
    chk["outside_request_window"] = int(((ts_recv < request_start_ns) | (ts_recv >= request_end_ns)).sum())
    lat = ts_recv - ts_event
    chk["negative_latency"] = int((lat < 0).sum())
    chk["latency_ms"] = {"median": float(np.median(lat) / 1e6), "p99": float(np.percentile(lat, 99) / 1e6),
                         "max": float(lat.max() / 1e6)}

    dup = exact_duplicates(arr)
    chk["exact_duplicates_removed"] = int(dup.sum())

    # instrument classes
    c = contracts.set_index("instrument_id")
    known = np.isin(iid, c.index.to_numpy())
    out_ids = c.index[c["is_outright"]].to_numpy()
    is_out = np.isin(iid, out_ids)
    cls = c["instrument_class"].reindex(iid[known]).to_numpy()
    by_class = pd.Series(cls).value_counts().to_dict()
    chk["records_by_instrument_class"] = {str(k): int(v) for k, v in by_class.items()}
    chk["records_without_definition"] = int((~known).sum())
    chk["records_outright"] = int(is_out.sum())
    chk["records_excluded_non_outright"] = int((known & ~is_out).sum())

    keep = is_out & ~dup
    if not keep.any():
        return ChunkResult(pd.DataFrame(), pd.DataFrame(), chk)
    ts_o, iid_o, px_o, sz_o, pos_o = ts_event[keep], iid[keep], price[keep], size[keep], pos[keep]
    tick_fixed = int(round(tick_size * FIXED))
    chk["outright_nonpositive_price"] = int((px_o <= 0).sum())
    chk["outright_zero_size"] = int((sz_o <= 0).sum())
    off = (px_o % tick_fixed) != 0
    chk["outright_off_tick_price"] = int(off.sum())
    chk["outright_off_tick_examples"] = _examples(ts_o, off)
    # exchange time running backwards within one instrument, in file (capture) order
    o2 = np.lexsort((pos_o, iid_o))
    back = (np.diff(ts_o[o2]) < 0) & (iid_o[o2][1:] == iid_o[o2][:-1])
    chk["ts_event_decreases_within_instrument"] = int(back.sum())
    ids_sorted = np.sort(out_ids.astype(np.int64))
    exp_sorted = np.array([np.iinfo(np.int64).max if pd.isna(e) else pd.Timestamp(e).value
                           for e in c["expiration"].reindex(ids_sorted)], dtype=np.int64)
    after = ts_o > exp_sorted[np.searchsorted(ids_sorted, iid_o)]
    chk["outright_trades_after_expiration"] = int(after.sum())

    minute = (ts_o // NS_MIN) * NS_MIN
    um, inv = np.unique(minute, return_inverse=True)
    lab = minute_labels(um, template)
    bad_min = outside_session(lab)
    bad = bad_min[inv]
    chk["outright_trades_outside_session_hours"] = int(bad.sum())
    chk["outright_outside_session_examples"] = _examples(ts_o, bad)

    # 1-minute bars of every outright (exchange time order, capture order breaks ties)
    o3 = np.lexsort((pos_o, ts_o, iid_o))
    i3, m3, p3, s3, t3 = iid_o[o3], minute[o3], px_o[o3], sz_o[o3], ts_o[o3]
    brk = np.flatnonzero((i3[1:] != i3[:-1]) | (m3[1:] != m3[:-1])) + 1
    st = np.r_[0, brk]
    en = np.r_[brk, len(i3)]
    bars = pd.DataFrame({
        "instrument_id": i3[st], "minute": m3[st], "first_ts": t3[st], "last_ts": t3[en - 1],
        "open": p3[st], "high": np.maximum.reduceat(p3, st), "low": np.minimum.reduceat(p3, st), "close": p3[en - 1],
        "volume": np.add.reduceat(s3, st), "n_trades": (en - st).astype(np.int64), "chunk": chunk_index,
    })

    # minute-price volume of the active contract of each trading date
    td_min = lab["trading_date"].to_numpy()
    act_min = np.array([active_by_date.get(pd.Timestamp(d), -1) for d in td_min], dtype=np.int64)
    is_act = iid_o == act_min[inv]
    chk["outright_records_active_contract"] = int(is_act.sum())
    m4, p4, s4 = minute[is_act], px_o[is_act], sz_o[is_act]
    o4 = np.lexsort((p4, m4))
    m4, p4, s4 = m4[o4], p4[o4], s4[o4]
    if len(m4):
        brk4 = np.flatnonzero((m4[1:] != m4[:-1]) | (p4[1:] != p4[:-1])) + 1
        st4 = np.r_[0, brk4]
        en4 = np.r_[brk4, len(m4)]
        mpv = pd.DataFrame({"minute": m4[st4], "price": p4[st4], "volume": np.add.reduceat(s4, st4),
                            "n_trades": (en4 - st4).astype(np.int64)})
    else:
        mpv = pd.DataFrame({"minute": np.array([], np.int64), "price": np.array([], np.int64),
                            "volume": np.array([], np.int64), "n_trades": np.array([], np.int64)})
    return ChunkResult(bars, mpv, chk)


def merge_bars(parts: list[pd.DataFrame]) -> pd.DataFrame:
    """Combine per-file bars. A minute split across two files (a late capture lands in the
    next file) is merged: open from the earliest trade, close from the latest."""
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame(columns=["instrument_id", "minute", "first_ts", "last_ts", "open", "high", "low", "close",
                                     "volume", "n_trades"])
    df = pd.concat(parts, ignore_index=True)
    key = ["instrument_id", "minute"]
    dup = df.duplicated(key, keep=False).to_numpy()
    single = df[~dup]
    if dup.any():
        d = df[dup]
        first = d.sort_values(["first_ts", "chunk"], kind="stable").groupby(key, sort=False).head(1).set_index(key)
        last = d.sort_values(["last_ts", "chunk"], kind="stable").groupby(key, sort=False).tail(1).set_index(key)
        agg = d.groupby(key).agg(high=("high", "max"), low=("low", "min"), volume=("volume", "sum"), n_trades=("n_trades", "sum"))
        agg["open"] = first["open"]
        agg["first_ts"] = first["first_ts"]
        agg["close"] = last["close"]
        agg["last_ts"] = last["last_ts"]
        agg["chunk"] = -1
        df = pd.concat([single, agg.reset_index()[single.columns]], ignore_index=True)
    return df.sort_values(key, kind="stable").reset_index(drop=True).drop(columns=["chunk"])


def merge_mpv(parts: list[pd.DataFrame]) -> pd.DataFrame:
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame(columns=["minute", "price", "volume", "n_trades"])
    df = pd.concat(parts, ignore_index=True)
    return df.groupby(["minute", "price"], as_index=False, sort=True)[["volume", "n_trades"]].sum()


def bars_to_frame(bars: pd.DataFrame, contracts: pd.DataFrame) -> pd.DataFrame:
    """Fixed-point aggregates -> float OHLCV with a tz-aware UTC ``ts`` (minute open)."""
    sym = contracts.set_index("instrument_id")["raw_symbol"]
    out = pd.DataFrame({
        "ts": pd.to_datetime(bars["minute"].to_numpy(np.int64), unit="ns", utc=True),
        "instrument_id": bars["instrument_id"].to_numpy(np.int64),
        "contract": sym.reindex(bars["instrument_id"]).to_numpy(),
    })
    for k in ("open", "high", "low", "close"):
        out[k] = bars[k].to_numpy(np.int64) / FIXED
    out["volume"] = bars["volume"].to_numpy(np.int64).astype(float)
    out["n_trades"] = bars["n_trades"].to_numpy(np.int64)
    return out


def active_bars(bars: pd.DataFrame, active: pd.DataFrame, template: SessionTemplate) -> pd.DataFrame:
    """Bars of the active contract of each bar's trading date (``bars`` from :func:`bars_to_frame`)."""
    td = trading_dates(pd.DatetimeIndex(bars["ts"]), template)
    act = active["instrument_id"].reindex(td).to_numpy()
    keep = bars["instrument_id"].to_numpy(np.float64) == act
    out = bars[keep].copy()
    return out.sort_values("ts", kind="stable").reset_index(drop=True)


def sum_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Add up per-file counters (examples are concatenated, latency takes the worst file)."""
    out: dict[str, Any] = {}
    for c in checks:
        for k, v in c.items():
            if isinstance(v, bool):
                out[k] = out.get(k, False) or v
            elif isinstance(v, (int, np.integer)):
                out[k] = int(out.get(k, 0)) + int(v)
            elif isinstance(v, list):
                out[k] = (out.get(k, []) + v)[:EXAMPLES]
            elif k == "latency_ms":
                prev = out.get(k, {"median": 0.0, "p99": 0.0, "max": 0.0})
                out[k] = {s: max(prev[s], v[s]) for s in ("median", "p99", "max")}
            elif isinstance(v, dict):
                prev = out.get(k, {})
                summed = {kk: int(prev.get(kk, 0)) + int(vv) for kk, vv in v.items()}
                out[k] = summed | {kk: vv for kk, vv in prev.items() if kk not in v}
            else:
                out[k] = v
    return out
