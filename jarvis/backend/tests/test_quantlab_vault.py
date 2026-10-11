"""QuantLab — local data first: the 20 acceptance tests of the brief (section K) and more.

Market data here is SYNTHETIC: the offline fixture provider writes real DBN files with
made-up prices (labelled FIXTURE everywhere), and the toy bars below are hand-built. Every
expected number for the toy cases is worked out by hand or by an independent formula —
never by the code under test. Nothing here proves a strategy works; it proves that the
backtest path is local, correct about time and money, and never buys data on its own.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import socket
import stat
import time as clock
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from jarvis.api.app import create_app
from jarvis.quantlab.futures import contracts as ct
from jarvis.quantlab.futures import sessions as cal
from jarvis.quantlab.futures import timeframes as tfs
from jarvis.quantlab.futures.audit import audit, passed
from jarvis.quantlab.futures.engine import MINUTE, Bars, simulate
from jarvis.quantlab.futures.resample import resample
from jarvis.quantlab.futures.sessions import Window
from jarvis.quantlab.futures.spec import parse, template
from jarvis.quantlab.hub import fixture
from jarvis.quantlab.vault import acquire as acquire_mod
from jarvis.quantlab.vault.lab import LabError
from jarvis.quantlab.vault.service import VaultError
from jarvis.runtime import Runtime
from tests.conftest import make_settings
from tests.test_quantlab_research import small_spec

NS = 1_000_000_000
NY = ZoneInfo("America/New_York")
START, END = "2025-11-03", "2026-03-03"
ACQ = {"symbols": ["NQ.v.0"], "start": START, "end": END, "schema": "ohlcv-1m"}


# -- helpers ---------------------------------------------------------------------------------


@cache
def _dbn(symbol: str, schema: str, start: str = START, end: str = END) -> bytes:
    """A real DBN file from the offline fixture (synthetic prices, labelled FIXTURE)."""
    return fixture.FixtureHistorical(fixture.VALID_KEY).encode(
        dataset="GLBX.MDP3", schema=schema, stype_in="continuous", symbols=[symbol],
        start=start, end=end,
    )  # fmt: skip


def dbn_file(folder: Path, symbol: str = "NQ.v.0", schema: str = "ohlcv-1m") -> Path:
    path = folder / f"{symbol}-{schema}.dbn"
    path.write_bytes(_dbn(symbol, schema))
    return path


async def import_dbn(rt: Runtime, folder: Path, symbol: str = "NQ.v.0") -> None:
    root = symbol.split(".")[0]
    for schema in ("ohlcv-1m", "definition"):
        staged = await rt.vault.stage_path(str(dbn_file(folder, symbol, schema)))
        done = await rt.vault.commit_import(staged["id"], {"root": root})
        assert done["status"] == "COMMITTED", done


async def strategy(rt: Runtime, **rule: Any) -> str:
    created = await rt.research.create_strategy(small_spec(**rule))
    return str(created["versions"][0]["id"])


async def backtest(rt: Runtime, version: str, timeframe: str, symbol: str = "NQ") -> dict[str, Any]:
    out = await rt.backtests.launch(
        {"version_id": version, "symbol": symbol, "timeframe": timeframe}
    )
    await rt.research.wait_idle(300)
    run = await rt.research.run(out["run"]["id"])
    assert run["status"] == "COMPLETED", run["error"]
    return {**out, "run": run}


@contextmanager
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Outbound network refused at the socket layer; any attempt is recorded."""
    attempts: list[str] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        attempts.append(repr(args[:2]))
        raise OSError("network blocked by the test")

    import databento

    def no_client(*args: Any, **kwargs: Any) -> Any:
        attempts.append("databento.Historical")
        raise AssertionError("a Databento client was created during a local backtest")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(databento, "Historical", no_client)
    yield attempts


@pytest.fixture
async def rt(tmp_path: Path) -> Any:
    settings = make_settings(tmp_path)
    settings = settings.model_copy(
        update={
            "quantlab": settings.quantlab.model_copy(
                update={"provider": "fixture", "keystore": "memory"}
            )
        }
    )
    runtime = Runtime(settings)
    await runtime.start()
    runtime.acquisitions.poll_seconds = 0.0
    runtime.acquisitions.retry_seconds = 0.0
    try:
        yield runtime
    finally:
        await runtime.stop()


# Toy bars: one RTH session. Prices in points, on the 0.25 grid.
BASE = int(datetime(2026, 3, 2, 14, 30, tzinfo=UTC).timestamp()) * NS  # 09:30 New York (EST)
NQ = ct.ContractSpec(42261, "NQH6", "NQ", 250_000_000, Decimal(5), Decimal(20), "USD", None,
                     "definition", None)  # fmt: skip
MNQ = ct.ContractSpec(42261, "MNQH6", "MNQ", 250_000_000, Decimal("0.5"), Decimal(2), "USD",
                      None, "definition", None)  # fmt: skip


def fixed(points: float) -> int:
    return round(points * 4) * 250_000_000


def toy_bars(
    rows: list[tuple[int, float, float, float, float]], minutes: int, iid: int = 42261
) -> Bars:
    return Bars(
        ts=np.array([BASE + m * MINUTE for m, *_ in rows], dtype=np.int64),
        iid=np.full(len(rows), iid, dtype=np.int64),
        open=np.array([fixed(r[1]) for r in rows], dtype=np.int64),
        high=np.array([fixed(r[2]) for r in rows], dtype=np.int64),
        low=np.array([fixed(r[3]) for r in rows], dtype=np.int64),
        close=np.array([fixed(r[4]) for r in rows], dtype=np.int64),
    )


def toy_window() -> list[Window]:
    return [
        Window(
            date(2026, 3, 2),
            BASE,
            BASE + 390 * MINUTE,
            BASE + 385 * MINUTE,
            BASE + 150 * MINUTE,
            BASE + 390 * MINUTE,
            False,
        )
    ]


def toy_spec(**changes: Any) -> Any:
    raw = template("opening_range_breakout", "NQ")
    raw["rule"].update(
        range_minutes=10, entry="close_beyond_range", direction="long", buffer_ticks=0
    )
    raw["exits"] = {
        "stop": {"type": "range_opposite"},
        "target": {"type": "r_multiple", "value": 1},
    }
    raw["execution"]["slippage_ticks"] = 0
    raw["costs"].update(commission_per_contract_side=2.0, exchange_fees_per_contract_side=1.0)
    raw["validation"]["parameter_grid"] = {}
    for path, value in changes.items():
        node = raw
        keys = path.split("__")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return parse(raw)


# 5-minute toy session. Range (first 10 minutes = bars at 0 and 5): high 101.50, low 99.50.
# Bar at 10 closes at 102.00 > 101.50 → signal known at 09:45 → market entry at the 09:45 open.
FIVE = [
    (0, 100.00, 101.00, 99.50, 100.50),
    (5, 100.50, 101.50, 100.00, 101.00),
    (10, 101.00, 102.25, 100.75, 102.00),
    (15, 102.25, 102.50, 102.00, 102.25),
]


def table_of(rows: list[dict[str, int]]) -> pa.Table:
    names = ("ts_event", "instrument_id", "open", "high", "low", "close", "volume")
    return pa.table({n: pa.array([r[n] for r in rows], type=pa.int64()) for n in names})


def linear_minutes(
    start_ns: int, minutes: range, iid: int = 7, skip: set[int] | None = None
) -> list[dict[str, int]]:
    """Minute m: open 100+0.25m, high +0.5, low −0.25, close +0.25, volume m+1."""
    out = []
    for m in minutes:
        if skip and m in skip:
            continue
        o = 100 + 0.25 * m
        out.append(
            {
                "ts_event": start_ns + m * MINUTE,
                "instrument_id": iid,
                "open": fixed(o),
                "high": fixed(o + 0.5),
                "low": fixed(o - 0.25),
                "close": fixed(o + 0.25),
                "volume": m + 1,
            }
        )
    return out


def rth(first: date, last: date) -> list[Window]:
    return cal.windows(
        "XNYS", "America/New_York", time(9, 30), time(16, 0), time(15, 55), None, first, last
    )


# -- 1 · a pre-imported NQ 1-minute backtest with the network blocked --------------------------


async def test_01_cached_backtest_runs_with_the_network_blocked(
    rt: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await import_dbn(rt, tmp_path)  # Databento DBN files you already have; no key connected
    version = await strategy(rt)
    assert (await rt.hub.status())["status"] != "CONNECTED"
    calls = rt.hub.adapter.calls
    with no_network(monkeypatch) as attempts:
        out = await backtest(rt, version, "1m")
    assert attempts == [] and rt.hub.adapter.calls == calls == 0
    summary = out["run"]["summary"]
    assert summary["metrics"]["trades"] > 0
    assert out["dataset"]["provider"] == "databento-file"
    assert all(c["result"] == "PASS" for c in summary["audit"]["conservative"])
    assert {c["provenance"] for c in summary["contracts"]} == {"definition"}


# -- 2 · 1m → 5m → 15m on one snapshot: no acquisition, correct bars, provenance ---------------


async def test_02_timeframes_share_one_snapshot_and_derive_correct_bars(
    rt: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await import_dbn(rt, tmp_path)
    version = await strategy(rt)
    with no_network(monkeypatch) as attempts:
        runs = {tf: await backtest(rt, version, tf) for tf in ("1m", "5m", "15m")}
    assert attempts == [] and rt.hub.adapter.calls == 0
    assert len({r["dataset"]["id"] for r in runs.values()}) == 1  # one snapshot
    assert await rt.acquisitions.acquisitions() == []
    assert await rt.hub.jobs() == []
    for tf in ("5m", "15m"):
        tf_info = runs[tf]["run"]["summary"]["timeframe"]
        assert tf_info["label"] == tf and tf_info["aggregator"] == "ql-resample-1"
        assert tf_info["derived"]["sha256"] and tf_info["source_minutes"] == 1
    # Independent check of the 5-minute bars: plain Python grouping in New York time.
    _, table, _ = await rt.hub.load(runs["5m"]["dataset"]["id"])
    derived = await rt.vault._store.derived_all()
    five = next(d for d in derived if d["minutes"] == 5)
    got = pq.read_table(rt.vault.root / five["path"]).to_pylist()
    expected: dict[tuple[int, int], dict[str, int]] = {}
    for row in table.to_pylist():
        local = datetime.fromtimestamp(row["ts_event"] / NS, NY)
        minute = local.hour * 60 + local.minute - (9 * 60 + 30)
        if not 0 <= minute < 390 or local.weekday() > 4:
            continue
        start = datetime.combine(local.date(), time(9, 30), NY) + timedelta(minutes=minute // 5 * 5)
        k = (int(start.timestamp()) * NS, row["instrument_id"])
        bar = expected.get(k)
        if bar is None:
            expected[k] = {"open": row["open"], "high": row["high"], "low": row["low"],
                           "close": row["close"], "volume": row["volume"]}  # fmt: skip
        else:
            bar.update(high=max(bar["high"], row["high"]), low=min(bar["low"], row["low"]),
                       close=row["close"], volume=bar["volume"] + row["volume"])  # fmt: skip
    sessions = {s.label for s in cal.sessions("XNYS", date(2025, 11, 3), date(2026, 3, 3))}
    expected = {
        k: v for k, v in expected.items()
        if datetime.fromtimestamp(k[0] / NS, NY).date() in sessions
    }  # fmt: skip
    early = {
        s.label: s
        for s in cal.sessions("XNYS", date(2025, 11, 3), date(2026, 3, 3))
        if s.early_close
    }

    def kept(ts: int) -> bool:
        day = datetime.fromtimestamp(ts / NS, NY).date()
        return day not in early or ts < early[day].close_ns

    expected = {k: v for k, v in expected.items() if kept(k[0])}
    assert len(got) == len(expected)
    for bar in got:
        want = expected[(bar["ts_event"], bar["instrument_id"])]
        assert {k: bar[k] for k in want} == want


# -- 3 · 2m, 3m, 10m, 30m against formula fixtures ---------------------------------------------


@pytest.mark.parametrize("minutes", [2, 3, 10, 30])
def test_03_derived_bars_match_independent_fixtures(minutes: int) -> None:
    (window,) = rth(date(2025, 3, 7), date(2025, 3, 8))
    out = resample(table_of(linear_minutes(window.start_ns, range(390))), [window], minutes)
    rows = out.table.to_pylist()
    assert len(rows) == 390 // minutes and out.stats["partial"] == 0
    for k in (0, 1, len(rows) - 1):
        bar = rows[k]
        first, last = k * minutes, (k + 1) * minutes - 1
        local = datetime.fromtimestamp(bar["ts_event"] / NS, NY)
        assert (local.hour, local.minute) == divmod(9 * 60 + 30 + first, 60)
        assert bar["open"] == fixed(100 + 0.25 * first)
        assert bar["high"] == fixed(100 + 0.25 * last + 0.5)
        assert bar["low"] == fixed(100 + 0.25 * first - 0.25)
        assert bar["close"] == fixed(100 + 0.25 * last + 0.25)
        assert bar["volume"] == sum(m + 1 for m in range(first, last + 1))
        assert bar["source_bars"] == minutes and not bar["partial"]


# -- 4 · DST, early closes, missing bars, no-trade intervals -----------------------------------


def test_04_sessions_dst_early_close_and_missing_bars() -> None:
    before, after = rth(date(2025, 3, 7), date(2025, 3, 11))  # Fri EST, Mon EDT
    assert datetime.fromtimestamp(before.start_ns / NS, UTC).hour == 14  # 09:30 EST = 14:30 UTC
    assert datetime.fromtimestamp(after.start_ns / NS, UTC).hour == 13  # 09:30 EDT = 13:30 UTC
    rows = linear_minutes(before.start_ns, range(390)) + linear_minutes(after.start_ns, range(390))
    out = resample(table_of(rows), [before, after], 30)
    starts = sorted(
        {
            datetime.fromtimestamp(r["ts_event"] / NS, NY).strftime("%H:%M")
            for r in out.table.to_pylist()
        }
    )
    assert starts[0] == "09:30" and starts[-1] == "15:30"  # anchored in New York, not UTC
    # Early close (day after Thanksgiving, 13:00): the 1h bars stop there; the last is partial.
    (short,) = rth(date(2025, 11, 28), date(2025, 11, 29))
    assert short.end_ns - short.start_ns == 210 * MINUTE
    hourly = resample(
        table_of(linear_minutes(short.start_ns, range(210))), [short], 60
    ).table.to_pylist()
    assert [r["span_minutes"] for r in hourly] == [60, 60, 60, 30]
    assert [r["partial"] for r in hourly] == [False, False, False, True]
    # Missing minutes are never filled: 2 of 5 absent → 3 source bars; an empty bucket → no bar.
    (day,) = rth(date(2025, 3, 7), date(2025, 3, 8))
    holes = linear_minutes(day.start_ns, range(30), skip={6, 7, 10, 11, 12, 13, 14})
    five = resample(table_of(holes), [day], 5).table.to_pylist()
    assert [r["source_bars"] for r in five] == [5, 3, 5, 5, 5]
    assert day.start_ns + 10 * MINUTE not in {r["ts_event"] for r in five}
    assert five[1]["open"] == fixed(100 + 0.25 * 5) and five[1]["close"] == fixed(
        100 + 0.25 * 9 + 0.25
    )


# -- 5 · no bar mixes contracts across a roll ----------------------------------------------------


def test_05_no_bar_mixes_instrument_ids() -> None:
    (day,) = rth(date(2025, 3, 7), date(2025, 3, 8))
    rows = linear_minutes(day.start_ns, range(3), iid=1) + linear_minutes(
        day.start_ns, range(3, 10), iid=2
    )
    out = resample(table_of(rows), [day], 5)
    bars = out.table.to_pylist()
    assert out.stats["split_by_contract"] == 1
    first = [b for b in bars if b["ts_event"] == day.start_ns]
    assert sorted(b["instrument_id"] for b in first) == [1, 2]
    one = next(b for b in first if b["instrument_id"] == 1)
    two = next(b for b in first if b["instrument_id"] == 2)
    assert (one["open"], one["close"], one["source_bars"]) == (fixed(100), fixed(100.75), 3)
    assert (two["open"], two["close"], two["source_bars"]) == (fixed(100.75), fixed(101.25), 2)
    contracts = {1: NQ, 2: NQ}
    result = simulate(toy_spec(), Bars.from_table(out.table), contracts, [day], bar_minutes=5)
    assert [s.status for s in result.sessions] == ["SKIPPED_ROLL"] and not result.trades


# -- 6 · NQ missing, ES present → blocked, never substituted -------------------------------------


async def test_06_missing_nq_is_never_replaced_by_es(rt: Runtime, tmp_path: Path) -> None:
    await import_dbn(rt, tmp_path, "ES.v.0")
    version = await strategy(rt)
    ready = await rt.vault.readiness("NQ", 5)
    assert ready["status"] == "NOT_AVAILABLE" and "never used in place of NQ" in ready["message"]
    with pytest.raises(LabError) as blocked:
        await rt.backtests.launch({"version_id": version, "symbol": "NQ", "timeframe": "5m"})
    assert blocked.value.code == "NOT_AVAILABLE"
    assert (await rt.vault.readiness("ES", 5))["status"] == "READY"


# -- 7 · only 5-minute data → a 1-minute test is blocked --------------------------------------


async def test_07_five_minute_source_blocks_one_minute(rt: Runtime, tmp_path: Path) -> None:
    path = tmp_path / "nq-5m.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "open", "high", "low", "close", "volume", "contract"])
        for w in rth(date(2025, 3, 3), date(2025, 3, 8)):
            for k in range(78):
                o = 18000 + 0.25 * k
                stamp = datetime.fromtimestamp((w.start_ns + k * 5 * MINUTE) / NS, UTC).isoformat()
                writer.writerow([stamp, o, o + 1, o - 0.5, o + 0.5, 10, "NQH5"])
    staged = await rt.vault.stage_path(str(path))
    mapping = {
        **{k: v for k, v in staged["preview"]["mapping"].items() if v},
        "root": "NQ",
        "contract_mode": "column",
    }
    done = await rt.vault.commit_import(staged["id"], mapping)
    assert (done["schema"], done["source_minutes"], done["authoritative"]) == ("ohlcv-5m", 5, False)
    assert (await rt.vault.readiness("NQ", 1))["status"] == "WRONG_GRANULARITY"
    assert (await rt.vault.readiness("NQ", 5))["status"] == "READY"
    assert (await rt.vault.readiness("NQ", 15))["status"] == "READY"
    version = await strategy(rt)
    with pytest.raises(LabError) as blocked:
        await rt.backtests.launch({"version_id": version, "symbol": "NQ", "timeframe": "1m"})
    assert blocked.value.code == "WRONG_GRANULARITY"


# -- 8 · the quote is free metadata; paid work needs an exact, unexpired approval -----------


async def test_08_quote_is_free_and_purchase_needs_exact_approval(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(VaultError):  # no key: nothing to quote with
        await rt.acquisitions.prepare(ACQ)
    await rt.hub.connect(fixture.VALID_KEY)
    jobs_before = len(fixture.BATCH_JOBS)

    async def no_submit(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("submit_job during a quote or an approval")

    monkeypatch.setattr(rt.hub.adapter, "submit_batch", no_submit)
    quote = await rt.acquisitions.prepare(ACQ)
    assert quote["status"] == "QUOTED" and quote["quote"]["currency"] == "USD"
    assert quote["quote"]["cost_usd"] > 0 and "get_cost" in quote["quote"]["source"]
    assert len(fixture.BATCH_JOBS) == jobs_before  # no job, no charge
    with pytest.raises(VaultError) as err:
        await rt.acquisitions.execute(quote["id"])
    assert err.value.code == "NOT_APPROVED"
    for args, code in (
        ((quote["request_hash"], quote["quote"]["cost_usd"] + 1, False), "CONFIRM_REQUIRED"),
        (("f" * 64, quote["quote"]["cost_usd"] + 1, True), "REQUEST_MISMATCH"),
        ((quote["request_hash"], quote["quote"]["cost_usd"] / 2, True), "BELOW_QUOTE"),
        ((quote["request_hash"], 10_000.0, True), "OVER_CAP"),
    ):
        with pytest.raises(VaultError) as err:
            await rt.acquisitions.approve(quote["id"], *args)
        assert err.value.code == code
    await rt.hub.set_caps(100, 500)
    approved = await rt.acquisitions.approve(
        quote["id"], quote["request_hash"], round(quote["quote"]["cost_usd"] + 0.5, 2), True
    )
    assert approved["status"] == "APPROVED" and len(approved["approvals"]) == 1
    later = datetime.now(UTC) + timedelta(minutes=31)
    monkeypatch.setattr(acquire_mod, "utcnow", lambda: later)
    with pytest.raises(VaultError) as err:
        await rt.acquisitions.execute(quote["id"])
    assert err.value.code == "APPROVAL_EXPIRED"
    assert len(fixture.BATCH_JOBS) == jobs_before
    await rt.vault.set_offline(True)
    with pytest.raises(VaultError) as err:
        await rt.acquisitions.prepare({**ACQ, "end": "2026-03-04"})
    assert err.value.code == "OFFLINE_MODE"


async def approved(rt: Runtime, request: dict[str, Any] = ACQ) -> dict[str, Any]:
    await rt.hub.connect(fixture.VALID_KEY)
    await rt.hub.set_caps(100, 500)
    quote = await rt.acquisitions.prepare(request)
    return await rt.acquisitions.approve(
        quote["id"], quote["request_hash"], round(quote["quote"]["cost_usd"] + 0.5, 2), True
    )


# -- 9 · no silent resubmission, no repeated charges --------------------------------------------


async def test_09_duplicates_never_charge_twice(rt: Runtime, tmp_path: Path) -> None:
    first = await approved(rt)
    again = await rt.acquisitions.prepare(ACQ)
    assert again["id"] == first["id"]  # the same request is the same acquisition
    before = len(fixture.BATCH_JOBS)
    await asyncio.gather(rt.acquisitions.execute(first["id"]), rt.acquisitions.execute(first["id"]))
    await rt.acquisitions.wait_idle(120)
    done = await rt.acquisitions.get(first["id"])
    assert done["status"] == "COMPLETED"
    assert len(fixture.BATCH_JOBS) - before == 2  # ohlcv-1m + definitions, once each
    after = await rt.acquisitions.prepare(ACQ)
    assert after["status"] == "NOTHING_TO_BUY" and after["quote"]["cost_usd"] == 0
    # A file imported twice is recognised; nothing is stored again.
    path = dbn_file(tmp_path, "MNQ.v.0")
    one = await rt.vault.stage_path(str(path))
    await rt.vault.commit_import(one["id"], {"root": "MNQ"})
    two = await rt.vault.stage_path(str(path))
    assert two["duplicate"] is True and two["id"] == one["id"]


async def test_09b_an_uncertain_submission_is_looked_up_never_repeated(rt: Runtime) -> None:
    acq = await approved(rt, {**ACQ, "include_definitions": False})
    # The process "died" right after Databento accepted the job: the job exists at the
    # provider, but the vault only knows it was SUBMITTING.
    job = acq["job"]["jobs"][0]
    client = fixture.FixtureHistorical(fixture.VALID_KEY)
    submitted = client.batch.submit_job(
        dataset=job["dataset"], symbols=[job["symbol"]], schema=job["schema"], start=job["start"],
        end=job["end"], stype_in=job["stype_in"], split_duration="month", compression="zstd",
    )  # fmt: skip
    job["state"] = "SUBMITTING"
    await rt.vault._store.update_acquisition(acq["id"], status="SUBMITTING", job={"jobs": [job]})
    count = len(fixture.BATCH_JOBS)
    await rt.acquisitions.start()  # restart: resume from the stored state
    await rt.acquisitions.wait_idle(120)
    done = await rt.acquisitions.get(acq["id"])
    assert done["status"] == "COMPLETED"
    assert done["job"]["jobs"][0]["provider_job"] == submitted["id"]
    assert len(fixture.BATCH_JOBS) == count  # adopted, not submitted again


async def test_09c_network_loss_during_submit_stops_and_asks(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    acq = await approved(rt, {**ACQ, "include_definitions": False})
    from jarvis.quantlab.hub.provider import ProviderError

    calls: list[int] = []

    async def lost(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        raise ProviderError("NETWORK", "connection reset")

    monkeypatch.setattr(rt.hub.adapter, "submit_batch", lost)
    await rt.acquisitions.execute(acq["id"])
    await rt.acquisitions.wait_idle(60)
    row = await rt.acquisitions.get(acq["id"])
    assert row["status"] == "ATTENTION" and row["error_code"] == "SUBMIT_UNCERTAIN"
    assert calls == [1]  # tried once; never retried blindly
    resumed = await rt.acquisitions.resume(acq["id"])  # owner: "check again" → lookup only
    await rt.acquisitions.wait_idle(60)
    assert (await rt.acquisitions.get(resumed["id"]))["status"] == "ATTENTION"
    assert calls == [1]


# -- 10 · an interrupted download resumes; the raw archive stays valid -----------------------


async def test_10_interrupted_download_resumes_and_verifies(rt: Runtime) -> None:
    acq = await approved(rt, {**ACQ, "include_definitions": False})
    name = "glbx.mdp3-20251103-20251201.ohlcv-1m.dbn.zst"
    fixture.FAIL_ONCE.add(name)
    await rt.acquisitions.execute(acq["id"])
    await rt.acquisitions.wait_idle(120)
    done = await rt.acquisitions.get(acq["id"])
    assert done["status"] == "COMPLETED", done["error"]
    assert name not in fixture.FAIL_ONCE  # the cut really happened
    files = done["job"]["jobs"][0]["files"]
    assert files and all(f["verified"] for f in files)
    for f in files:
        path = rt.vault.root / f["path"]
        assert f["provider_hash"] == f"sha256:{f['sha256']}"
        assert stat.S_IMODE(path.stat().st_mode) == 0o444  # immutable original (read-only)
    staging = rt.vault.root / "staging-acquisitions" / acq["id"]
    assert not any(staging.rglob("*.zst"))  # no partial file left behind
    assert (await rt.vault.audit())["ok"] is True


# -- 11 · derived bars rebuilt from the immutable originals, offline ------------------------


async def test_11_rebuild_derived_offline_after_deletion(
    rt: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await import_dbn(rt, tmp_path)
    version = await strategy(rt)
    first = await backtest(rt, version, "5m")
    trades = (await rt.research.trades(first["run"]["id"], 0, 10_000))["rows"]
    derived = rt.vault.root / "derived"
    for path in derived.rglob("*"):
        if path.is_file():
            os.chmod(path, 0o644)
    import shutil

    shutil.rmtree(derived)
    with no_network(monkeypatch) as attempts:
        rebuilt = await rt.vault.rebuild_derived()
        assert rebuilt["failed"] == [] and rebuilt["rebuilt"]
        assert all(r["identical"] for r in rebuilt["rebuilt"])  # bit-identical content
        again = await rt.research.reproduce(first["run"]["id"])
    assert attempts == [] and again["identical"] is True
    stored = (await rt.research.trades(first["run"]["id"], 0, 10_000))["rows"]
    assert stored == trades


# -- 12 · bar timestamps are starts; a close signal never fills before the close ----------


def test_12_close_signal_fills_only_after_the_bar_completed() -> None:
    rows = [*FIVE, (20, 102.50, 105.25, 102.25, 105.00)]
    spec = toy_spec()
    result = simulate(spec, toy_bars(rows, 5), {NQ.instrument_id: NQ}, toy_window(), bar_minutes=5)
    (trade,) = result.trades
    assert trade.signal_known_at == BASE + 15 * MINUTE  # the 09:40 bar is known at 09:45
    assert trade.entry_bar_ts == BASE + 15 * MINUTE  # filled at the 09:45 bar's open, not before
    assert (trade.entry_ticks, trade.exit_ticks, trade.exit_reason) == (409, 420, "TARGET")
    checks = audit(result, toy_bars(rows, 5), toy_window(), spec)
    assert passed(checks)
    # The same prices stamped as 1-minute bars would be known 4 minutes earlier: the
    # duration, not just the timestamp, decides when information exists.
    assert result.bar_minutes == 5


# -- 13 · broker-grade ledger: NQ vs MNQ ----------------------------------------------------------


def test_13_ledger_nq_vs_mnq() -> None:
    rows = [*FIVE, (20, 102.50, 105.25, 102.25, 105.00)]
    spec = toy_spec()
    nq = simulate(spec, toy_bars(rows, 5), {NQ.instrument_id: NQ}, toy_window(), bar_minutes=5)
    mnq_spec, notes = tfs.effective_spec(
        spec, product="MNQ", symbol="MNQ.v.0", minutes=5, overrides={"slippage_ticks": 0}
    )
    mnq = simulate(
        mnq_spec, toy_bars(rows, 5), {MNQ.instrument_id: MNQ}, toy_window(), bar_minutes=5
    )
    (a,), (b,) = nq.trades, mnq.trades
    # 11 ticks: NQ 11 × $5 = $55 gross, fees 2 × ($2 + $1) = $6; MNQ 11 × $0.50 = $5.50,
    # fees from the stated MNQ profile 2 × ($0.50 + $0.37) = $1.74.
    assert (a.gross, a.fees, a.net) == (Decimal(55), Decimal(6), Decimal(49))
    assert (b.gross, b.fees, b.net) == (Decimal("5.50"), Decimal("1.74"), Decimal("3.76"))
    assert a.gross / b.gross == 10
    assert any("tick value and multiplier come from the MNQ contract master" in n for n in notes)


# -- 14 · stop and target inside one bar: conservative, never magically optimal ---------------


def test_14_stop_and_target_in_one_bar_is_resolved_conservatively() -> None:
    rows = [*FIVE, (20, 102.50, 105.25, 99.25, 100.00)]  # target 105 and stop 99.50 both inside
    spec = toy_spec()
    bars = toy_bars(rows, 5)
    low = simulate(spec, bars, {NQ.instrument_id: NQ}, toy_window(), bar_minutes=5)
    high = simulate(
        spec, bars, {NQ.instrument_id: NQ}, toy_window(), bar_minutes=5, mode="optimistic"
    )
    (worst,), (best,) = low.trades, high.trades
    assert (worst.exit_reason, worst.exit_ticks, worst.ambiguous) == ("STOP", 398, True)
    assert (best.exit_reason, best.exit_ticks, best.ambiguous) == ("TARGET", 420, True)
    assert worst.net == Decimal(-61) and best.net == Decimal(49)  # ∓11 ticks × $5 − $6 fees


# -- 15 · same inputs → same run, reproducible -------------------------------------------------


async def test_15_runs_are_reproducible_and_identified_by_their_inputs(
    rt: Runtime, tmp_path: Path
) -> None:
    await import_dbn(rt, tmp_path)
    version = await strategy(rt)
    one = await backtest(rt, version, "5m")
    two = await backtest(rt, version, "5m")
    assert one["run"]["id"] == two["run"]["id"]
    manifest = one["run"]["manifest"]
    assert manifest["timeframe"]["minutes"] == 5 and manifest["engine"]["version"] == "1.1.0"
    assert manifest["effective_spec_sha256"] and manifest["dataset"]["snapshot_sha256"]
    assert (await rt.research.reproduce(one["run"]["id"]))["identical"] is True
    other = await backtest(rt, version, "15m")
    assert other["run"]["id"] != one["run"]["id"]  # the timeframe is part of the identity


# -- 16 · the UI sees true coverage and timeframe compatibility ---------------------------------


async def test_16_options_and_readiness_show_true_coverage(rt: Runtime, tmp_path: Path) -> None:
    empty = await rt.backtests.options()
    assert all(m["local"] == [] for m in empty["markets"])
    await import_dbn(rt, tmp_path)
    version = await strategy(rt)  # 15-minute opening range
    options = await rt.backtests.options()
    nq = next(m for m in options["markets"] if m["symbol"] == "NQ")
    assert nq["local"][0]["symbol"] == "NQ.v.0" and nq["local"][0]["start"] == START
    assert (
        nq["tick_value"] == 5.0
        and next(m for m in options["markets"] if m["symbol"] == "MNQ")["tick_value"] == 0.5
    )
    rows = {r["timeframe"]: r for r in (await rt.backtests.readiness("NQ", version))["timeframes"]}
    assert rows["5m"]["status"] == "READY" and rows["15m"]["status"] == "READY"
    assert rows["10m"]["status"] == "INCOMPATIBLE" and "opening range" in rows["10m"]["message"]
    assert rows["5m"]["series"]["source"] == "Databento DBN file (supplied by you)"
    assert any("Opening range" in n for n in rows["5m"]["compatibility"]["notes"])
    assert (await rt.backtests.readiness("MNQ", version))["timeframes"][0][
        "status"
    ] == "NOT_AVAILABLE"


# -- 17 · 100 re-runs: zero provider requests, zero charges -----------------------------------


async def test_17_a_hundred_reruns_cost_nothing(
    rt: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await import_dbn(rt, tmp_path)
    version = await strategy(rt)
    with no_network(monkeypatch) as attempts:
        ids = set()
        for i in range(100):
            out = await rt.backtests.launch(
                {"version_id": version, "symbol": "NQ", "timeframe": ("1m", "5m", "15m")[i % 3]}
            )
            ids.add(out["run"]["id"])
        await rt.research.wait_idle(600)
    assert attempts == [] and rt.hub.adapter.calls == 0
    assert len(ids) == 3  # one run per distinct input set
    assert await rt.acquisitions.acquisitions() == [] and await rt.hub.jobs() == []


# -- 18 · damaged local data is detected and never called offline-ready ----------------------


async def test_18_corruption_is_detected_and_repaired_from_the_original(
    rt: Runtime, tmp_path: Path
) -> None:
    await import_dbn(rt, tmp_path)
    version = await strategy(rt)
    day = next(
        p for p in (rt.vault.root / "canonical").rglob("2026-01-1*.parquet") if "ohlcv-1m" in str(p)
    )
    os.chmod(day, 0o644)
    day.write_bytes(day.read_bytes()[:-10] + b"0123456789")  # silent bit rot
    report = await rt.vault.audit()
    assert not report["ok"] and any(p["kind"] == "CANONICAL" for p in report["problems"])
    ready = await rt.vault.readiness("NQ", 5)
    assert ready["status"] == "INVALID" and not ready["runnable"]
    with pytest.raises(LabError) as blocked:
        await rt.backtests.launch({"version_id": version, "symbol": "NQ", "timeframe": "5m"})
    assert blocked.value.code == "INVALID"
    fixed_report = await rt.vault.audit(repair=True)  # re-derived from the intact raw DBN
    assert fixed_report["ok"] and fixed_report["repaired"]
    assert (await rt.vault.readiness("NQ", 5))["status"] == "READY"


# -- 19 · secrets never leave the keystore ---------------------------------------------------------


async def test_19_secrets_are_not_exposed(
    rt: Runtime, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    key = fixture.VALID_KEY
    acq = await approved(rt, {**ACQ, "include_definitions": False})
    await rt.acquisitions.execute(acq["id"])
    await rt.acquisitions.wait_idle(120)
    blobs = [
        json.dumps(await rt.vault.overview(), default=str),
        json.dumps(await rt.acquisitions.acquisitions(), default=str),
        json.dumps(await rt.hub.status(), default=str),
        caplog.text,
    ]
    for table in (
        "qv_acquisitions",
        "qv_approvals",
        "qh_cache",
        "qh_connections",
        "qh_audit",
        "events",
    ):
        rows = await rt.db.fetch_all(f"SELECT * FROM {table}")
        blobs.append(json.dumps([dict(r) for r in rows], default=str))
    for blob in blobs:
        assert key not in blob and key[3:] not in blob


# -- 20 · the UI path end to end: strategy → NQ → 5m → Backtest → chart, ledger, validation -----


def test_20_ui_flow_over_the_api_with_the_network_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    settings = settings.model_copy(
        update={
            "quantlab": settings.quantlab.model_copy(
                update={"provider": "fixture", "keystore": "memory"}
            )
        }
    )
    runtime = Runtime(settings)
    with TestClient(create_app(runtime=runtime)) as client:
        for schema in ("ohlcv-1m", "definition"):
            path = dbn_file(tmp_path, "NQ.v.0", schema)
            with path.open("rb") as handle:
                staged = client.post(
                    "/quantlab/data/imports",
                    content=handle.read(),
                    headers={"x-filename": path.name},
                ).json()
            assert (
                client.post(
                    f"/quantlab/data/imports/{staged['id']}/commit", json={"root": "NQ"}
                ).status_code
                == 200
            )
        version = client.post("/quantlab/research/strategies", json={"spec": small_spec()}).json()
        options = client.get("/quantlab/backtest/options").json()
        chosen = next(s for s in options["strategies"] if s["id"] == version["id"])
        with no_network(monkeypatch) as attempts:
            ready = client.get(
                "/quantlab/backtest/readiness",
                params={"symbol": "NQ", "version_id": chosen["version_id"]},
            ).json()
            five = next(r for r in ready["timeframes"] if r["timeframe"] == "5m")
            assert five["status"] == "READY"
            started = client.post(
                "/quantlab/backtests",
                json={"version_id": chosen["version_id"], "symbol": "NQ", "timeframe": "5m",
                      "advanced": {"kind": "validation"}},
            )  # fmt: skip
            assert started.status_code == 200, started.text
            run_id = started.json()["run"]["id"]
            for _ in range(600):
                run = client.get(f"/quantlab/backtests/{run_id}").json()
                if run["status"] in ("COMPLETED", "FAILED"):
                    break
                clock.sleep(0.2)
            assert run["status"] == "COMPLETED", run["error"]
            chart = client.get(f"/quantlab/research/runs/{run_id}/chart").json()
            trades = client.get(f"/quantlab/backtests/{run_id}/trades").json()
            checks = client.get(f"/quantlab/backtests/{run_id}/validation").json()
        assert attempts == []
        assert chart["candles"] and chart["equity"] and chart["markers"]
        assert trades["total"] > 0 and trades["rows"][0]["entry_time"]
        # The imported DBN here is synthetic fixture data in Databento's format; the vault
        # can't tell (a DBN file looks like a DBN file), so no fixture cap applies — the E2E
        # uses the labelled fixture provider instead. Here: a verdict from the fixed policy.
        assert checks["verdict"]["verdict"] in {
            "INVALID_DATA_OR_METHOD", "INSUFFICIENT_EVIDENCE", "REJECTED_HYPOTHESIS",
            "PROMISING_RESEARCH_CANDIDATE", "FORWARD_VALIDATION_REQUIRED",
            "ROBUST_UNDER_TESTED_ASSUMPTIONS",
        }  # fmt: skip
        assert checks["timeframe"]["label"] == "5m" and checks["tests"]
        assert (
            client.post(
                "/quantlab/backtests",
                json={"version_id": chosen["version_id"], "symbol": "ES", "timeframe": "5m"},
            ).status_code
            == 409
        )


# -- more: import validation, backup / restore, offline switch ------------------------------


async def test_csv_import_checks_refuse_what_they_cannot_place(rt: Runtime, tmp_path: Path) -> None:
    def write(name: str, rows: list[list[Any]]) -> str:
        path = tmp_path / name
        with path.open("w", newline="") as handle:
            csv.writer(handle).writerows(
                [["time", "open", "high", "low", "close", "volume", "contract"], *rows]
            )
        return str(path)

    def two(t0: str, t1: str, first_open: float = 100, contract: str = "NQH5") -> list[list[Any]]:
        return [
            [t0, first_open, 101, 99.5, 100.5, 3, contract],
            [t1, 100.5, 101, 100, 100.75, 3, contract],
        ]

    naive = await rt.vault.stage_path(
        write("naive.csv", two("2025-03-07 09:30", "2025-03-07 09:31"))
    )
    assert "timezone" in naive["preview"]["needs"]
    base = {
        "root": "NQ", "timestamp": "time", "open": "open", "high": "high", "low": "low",
        "close": "close", "volume": "volume", "contract": "contract", "contract_mode": "column",
    }  # fmt: skip
    with pytest.raises(VaultError) as err:
        await rt.vault.commit_import(naive["id"], base)
    assert err.value.code == "TIMEZONE_REQUIRED"
    adjusted = await rt.vault.stage_path(
        write("adjusted.csv", two("2025-03-07T14:30:00Z", "2025-03-07T14:31:00Z", 100.1))
    )
    with pytest.raises(VaultError) as err:
        await rt.vault.commit_import(adjusted["id"], base)
    assert err.value.code == "OFF_TICK_GRID"
    unmapped = await rt.vault.stage_path(
        write("unmapped.csv", two("2025-03-07T14:30:00Z", "2025-03-07T14:31:00Z", contract="x"))
    )
    done = await rt.vault.commit_import(unmapped["id"], {**base, "contract_mode": "none"})
    assert any(f["code"] == "NO_CONTRACT_MAPPING" for f in done["findings"])
    assert (await rt.vault.readiness("NQ", 1))["status"] == "MISSING_MAPPINGS"


async def test_backup_and_restore_round_trip(rt: Runtime, tmp_path: Path) -> None:
    await import_dbn(rt, tmp_path)
    target = tmp_path / "external"
    target.mkdir()
    with pytest.raises(VaultError):
        await rt.vault.backup(str(target), confirm=False)
    backup = await rt.vault.backup(str(target), confirm=True)
    assert backup["status"] == "VERIFIED" and backup["files"] > 10
    assert (await rt.vault.overview())["backup_warning"] is None
    victim = next((rt.vault.root / "canonical").rglob("*.parquet"))
    os.chmod(victim, 0o644)
    victim.unlink()
    assert not (await rt.vault.audit())["ok"]
    restored = await rt.vault.restore(backup["location"], confirm=True)
    assert restored["restored"] == 1 and restored["damaged_in_backup"] == []
    assert (await rt.vault.audit())["ok"]


async def test_offline_mode_blocks_every_market_data_request(rt: Runtime) -> None:
    await rt.hub.connect(fixture.VALID_KEY)
    await rt.vault.set_offline(True)
    from jarvis.quantlab.hub.service import HubError

    with pytest.raises(HubError) as err:
        await rt.hub.quote({**ACQ, "dataset": "GLBX.MDP3", "stype_in": "continuous"})
    assert err.value.code == "OFFLINE_MODE"
    with pytest.raises(VaultError) as verr:
        await rt.acquisitions.prepare(ACQ)
    assert verr.value.code == "OFFLINE_MODE"
    await rt.vault.set_offline(False)
    assert (await rt.acquisitions.prepare(ACQ))["status"] == "QUOTED"
