"""Synthetic Databento DBN files and a fake Historical client.

SYNTHETIC DATA NOTICE: every trade written here comes from a seeded random walk and
exists only to exercise parsing, validation, contract selection and aggregation code
(SOFTWARE TESTS ONLY). It carries no information about any market and must never be
used or reported as strategy evidence.

The files mimic a parent request ``ES.FUT`` on ``GLBX.MDP3``: outright futures of three
expiries, a calendar spread, records sorted by capture time ``ts_recv`` and split into
one file per calendar month.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

TZ = "America/New_York"
FIXED = 1_000_000_000
NS_SEC = 1_000_000_000
NS_MIN = 60 * NS_SEC

# instrument_id: (raw symbol, expiration UTC, activation UTC, instrument class)
ES_INSTRUMENTS = {
    101: ("ESH4", "2024-03-15 13:30", "2022-12-16 23:00", "F"),
    102: ("ESM4", "2024-06-21 13:30", "2023-03-17 22:00", "F"),
    103: ("ESU4", "2024-09-20 13:30", "2023-06-16 22:00", "F"),
    201: ("ESH4-ESM4", "2024-03-15 13:30", "2023-03-17 22:00", "S"),
}
# trading dates around the March 2024 roll (frozen rule: 2024-03-15 - 8 days = 2024-03-07)
# and the start of daylight saving time (Sunday 2024-03-10)
DEFAULT_DATES = ("2024-02-28", "2024-02-29", "2024-03-01", "2024-03-06", "2024-03-07", "2024-03-08",
                 "2024-03-11", "2024-03-12")


def ns(ts: str, tz: str = "UTC") -> int:
    return int(pd.Timestamp(ts, tz=tz).value)


def _metadata(schema: str, start: str, end: str, symbol: str = "ES.FUT"):
    import databento_dbn as dbn

    sch = {"trades": dbn.Schema.TRADES, "definition": dbn.Schema.DEFINITION}[schema]
    return dbn.Metadata(dataset="GLBX.MDP3", start=ns(start), end=ns(end), stype_in=dbn.SType.PARENT,
                        stype_out=dbn.SType.INSTRUMENT_ID, schema=sch, symbols=[symbol])


def trade_dtype() -> np.dtype:
    """The record layout ``DBNStore.to_ndarray`` returns for trades (taken from the library)."""
    import databento as db
    import databento_dbn as dbn

    msg = dbn.TradeMsg(publisher_id=1, instrument_id=1, ts_event=1, price=1, size=1, action=dbn.Action.TRADE,
                       side=dbn.Side.ASK, depth=0, ts_recv=2, flags=0, ts_in_delta=0, sequence=0)
    md = _metadata("trades", "2024-01-01", "2024-01-02")
    return db.DBNStore.from_bytes(md.encode() + bytes(msg)).to_ndarray().dtype


def definition_bytes(start: str, end: str, instruments: dict[int, tuple] = ES_INSTRUMENTS, asset: str = "ES",
                     tick_fixed: int = 250_000_000) -> bytes:
    """One definition per instrument at the start of the file's window.

    ``instruments`` values are ``(raw symbol, expiration, activation, class)`` with an
    optional fifth element of overrides: ``asset``, ``tick_fixed``, ``user_defined``,
    ``security_type``.
    """
    import databento_dbn as dbn

    classes = {"F": dbn.InstrumentClass.FUTURE, "S": dbn.InstrumentClass.FUTURE_SPREAD,
               "C": dbn.InstrumentClass.CALL, "P": dbn.InstrumentClass.PUT}
    out = [_metadata("definition", start, end).encode()]
    t0 = ns(start) + 60 * NS_SEC
    for iid, spec in sorted(instruments.items()):
        sym, exp, act, cls = spec[:4]
        opt = spec[4] if len(spec) > 4 else {}
        out.append(bytes(dbn.InstrumentDefMsg(
            publisher_id=1, instrument_id=iid, ts_event=t0, ts_recv=t0,
            min_price_increment=opt.get("tick_fixed", tick_fixed), display_factor=FIXED, raw_symbol=sym,
            asset=opt.get("asset", asset), security_type=opt.get("security_type", "OOF" if cls in "CP" else "FUT"),
            instrument_class=classes[cls], security_update_action=dbn.SecurityUpdateAction.ADD,
            expiration=ns(exp), activation=ns(act),
            user_defined_instrument=dbn.UserDefinedInstrument.YES if opt.get("user_defined") else dbn.UserDefinedInstrument.NO)))
    return b"".join(out)


def session_minutes(trading_date: str) -> pd.DatetimeIndex:
    """Minute opens of one CME trading date in New York time: 18:00 (prior day) to 16:59."""
    d = pd.Timestamp(trading_date)
    wall = pd.date_range(d - pd.Timedelta(hours=6), d + pd.Timedelta(hours=17), freq="1min", inclusive="left")
    return wall.tz_localize(TZ, nonexistent="shift_forward", ambiguous="NaT")


def synthetic_trades(dates: tuple[str, ...] = DEFAULT_DATES, seed: int = 7, *, front: int = 101, back: int = 102,
                     spread: int = 201, roll_date: str = "2024-03-07", base_price: float = 5100.0,
                     roll_spread: float = 60.0, tick: float = 0.25) -> np.ndarray:
    """Trades of every minute of each trading date (random walk on the tick grid).

    The contract the frozen rule makes active trades every minute (two trades); the other
    quarterly contract every 5th minute; the spread every 30th minute.
    """
    rng = np.random.default_rng(seed)
    dt_ = trade_dtype()
    rows = []
    price = base_price
    seq = 0
    for d in dates:
        active = back if pd.Timestamp(d) >= pd.Timestamp(roll_date) else front
        other = front if active == back else back
        for k, m in enumerate(session_minutes(d)):
            t_min = int(m.value)
            price = round((price + rng.normal(0, 1.0) * tick) / tick) * tick
            for j in range(2):
                seq += 1
                te = t_min + int((10 + 20 * j) * NS_SEC + rng.integers(0, NS_SEC))
                p = price + (roll_spread if active == back else 0.0) + tick * j
                rows.append((active, te, p, int(rng.integers(1, 20)), seq))
            if k % 5 == 0:
                seq += 1
                p = price + (roll_spread if other == back else 0.0)
                rows.append((other, t_min + 45 * NS_SEC, p, int(rng.integers(1, 5)), seq))
            if k % 30 == 0:
                seq += 1
                rows.append((spread, t_min + 50 * NS_SEC, -roll_spread, 1, seq))
    arr = np.zeros(len(rows), dtype=dt_)
    arr["length"] = dt_.itemsize // 4
    arr["rtype"] = 0
    arr["publisher_id"] = 1
    arr["instrument_id"] = [r[0] for r in rows]
    arr["ts_event"] = [r[1] for r in rows]
    arr["price"] = [int(round(r[2] * FIXED)) for r in rows]
    arr["size"] = [r[3] for r in rows]
    arr["action"] = b"T"
    arr["side"] = [b"A" if i % 2 else b"B" for i in range(len(rows))]
    arr["ts_recv"] = arr["ts_event"] + 50_000
    arr["ts_in_delta"] = 20_000
    arr["sequence"] = [r[4] for r in rows]
    return arr[np.argsort(arr["ts_recv"], kind="stable")]


def trades_bytes(arr: np.ndarray, start: str, end: str) -> bytes:
    """Records with ``start <= ts_recv < end``, as one DBN file."""
    sel = arr[(arr["ts_recv"] >= ns(start)) & (arr["ts_recv"] < ns(end))]
    return _metadata("trades", start, end).encode() + np.ascontiguousarray(sel).tobytes()


class FakeHistorical:
    """Stands in for ``databento.Historical``: serves prepared bytes and records every call."""

    def __init__(self, trades: np.ndarray | None = None, *, instruments: dict[int, tuple] = ES_INSTRUMENTS,
                 cost_per_request: float = 0.25, bytes_per_request: int = 1_000_000,
                 dataset_range: tuple[str, str] = ("2010-06-06", "2026-10-08")):
        self.trades = trades if trades is not None else synthetic_trades()
        self.instruments = instruments
        self.cost_per_request = cost_per_request
        self.bytes_per_request = bytes_per_request
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.metadata = SimpleNamespace(
            get_cost=self._cost, get_billable_size=self._size, get_record_count=self._count,
            get_dataset_range=lambda **kw: {"start": dataset_range[0] + "T00:00:00Z", "end": dataset_range[1] + "T00:00:00Z"})
        self.timeseries = SimpleNamespace(get_range=self._get_range)

    def _cost(self, **kw: Any) -> float:
        self.calls.append(("get_cost", kw))
        return self.cost_per_request

    def _size(self, **kw: Any) -> int:
        self.calls.append(("get_billable_size", kw))
        return self.bytes_per_request

    def _count(self, **kw: Any) -> int:
        self.calls.append(("get_record_count", kw))
        return 1000

    def _get_range(self, *, dataset: str, schema: str, symbols: list[str], stype_in: str, start: str, end: str,
                   path: str | Path) -> None:
        self.calls.append(("get_range", {"dataset": dataset, "schema": schema, "symbols": symbols,
                                         "stype_in": stype_in, "start": start, "end": end}))
        data = definition_bytes(start, end, self.instruments) if schema == "definition" else trades_bytes(self.trades, start, end)
        Path(path).write_bytes(data)


def with_record(arr: np.ndarray, *, instrument_id: int, ts_event: str, price: float, size: int = 1,
                recv_delay_ns: int = 50_000, sequence: int = 10**9) -> np.ndarray:
    """``arr`` plus one trade (``ts_event`` in UTC), re-sorted by capture time."""
    rec = np.zeros(1, dtype=arr.dtype)
    rec["length"] = arr.dtype.itemsize // 4
    rec["publisher_id"] = 1
    rec["instrument_id"] = instrument_id
    rec["ts_event"] = ns(ts_event)
    rec["price"] = int(round(price * FIXED))
    rec["size"] = size
    rec["action"] = b"T"
    rec["side"] = b"A"
    rec["ts_recv"] = rec["ts_event"] + recv_delay_ns
    rec["sequence"] = sequence
    out = np.concatenate([arr, rec])
    return out[np.argsort(out["ts_recv"], kind="stable")]
