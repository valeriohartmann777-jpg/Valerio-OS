"""QuantLab: the handoff's test matrix (Q-001 … Q-022) against its synthetic fixtures.

Expected numbers come from ``docs/quantlab-handoff/fixtures/golden_expected.json``
and from a separate reference SMA written here with exact fractions — never from
the engine under test.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.api.app import create_app
from jarvis.quantlab import engine
from jarvis.quantlab.data import (
    SYNTHETIC_LABEL,
    Bar,
    DataError,
    ImportMeta,
    import_bars,
    read_snapshot,
    snapshot_bytes,
)
from jarvis.quantlab.service import QuantLabError, _checked_id, _downsample
from jarvis.quantlab.spec import SpecError, example_spec, parse_spec
from jarvis.runtime import Runtime
from jarvis.settings import PROJECT_ROOT, Settings
from tests.conftest import Recorder, eventually

FIXTURES = PROJECT_ROOT / "docs" / "quantlab-handoff" / "fixtures"
EXPECTED = json.loads((FIXTURES / "golden_expected.json").read_text(encoding="utf-8"))
META = ImportMeta(symbol="TEST_SYNTHETIC", exchange="TEST_ONLY", currency="USD", frequency="1m")
MINUTE = timedelta(minutes=1)


def fixture_bars(name: str) -> list[Bar]:
    result = import_bars(name, (FIXTURES / name).read_bytes(), META)
    assert result.usable, result.findings
    return result.bars


def config(cash: float, fee: float = 0.5, *, bps: float = 0, slip: float = 0) -> engine.Config:
    return engine.Config(
        interval=MINUTE,
        size_units=1,
        initial_cash=engine.dec(cash),
        fee_fixed=engine.dec(fee),
        fee_bps=engine.dec(bps),
        slippage_bps=engine.dec(slip),
    )


def golden_signals(bars: list[Bar]) -> dict[int, engine.Side]:
    index = {b.ts: i for i, b in enumerate(bars)}
    out: dict[int, engine.Side] = {}
    with open(FIXTURES / "golden_signals.csv", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ts = datetime.fromisoformat(row["signal_bar_start_utc"].replace("Z", "+00:00"))
            out[index[ts]] = row["signal"]  # type: ignore[assignment]
    return out


def iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# Execution accounting (Q-001 … Q-005) ---------------------------------------------------


def test_golden_execution_fixture_reconciles_exactly() -> None:
    """Q-001, Q-002, Q-003: signal at a bar's close fills at the next open, to the cent."""
    bars = fixture_bars("golden_execution_case.csv")
    result = engine.simulate(bars, golden_signals(bars), config(10_000))
    filled = [o for o in result.orders if o.status == "FILLED"]
    assert len(filled) == len(EXPECTED["execution"])
    for order, want in zip(filled, EXPECTED["execution"], strict=True):
        assert order.side == want["side"]
        assert iso(order.signal_bar_start) == want["signal_bar_start_utc"]
        assert iso(order.signal_available_at) == want["signal_available_at_utc"]
        assert iso(order.fill_time) == want["fill_bar_start_utc"]
        assert order.fill_index is not None and order.fill_index > order.signal_index
        assert order.fill_time is not None and order.fill_time >= order.signal_available_at
        assert order.fill_price == Decimal(str(want["fill_price_usd"]))
        assert order.fee == Decimal(str(want["fee_usd"]))
        assert order.cash_after == Decimal(str(want["cash_after_usd"]))
        assert order.position_after == want["position_after_shares"]
    (trade,) = result.trades
    assert trade.gross_pnl == Decimal(str(EXPECTED["round_trip"]["gross_pnl_usd"]))
    assert trade.fees == Decimal(str(EXPECTED["round_trip"]["total_fees_usd"]))
    assert trade.net_pnl == Decimal(str(EXPECTED["round_trip"]["net_pnl_usd"]))
    assert result.cash[-1] == Decimal(str(EXPECTED["end"]["cash_usd"]))
    assert result.final_equity == Decimal(str(EXPECTED["end"]["equity_usd"]))
    assert all(check.passed for check in engine.audit(result))


def test_signal_on_last_bar_is_not_executed() -> None:
    """Q-004: no fill on a bar that doesn't exist."""
    bars = fixture_bars("golden_execution_case.csv")
    result = engine.simulate(bars, {2: "BUY", len(bars) - 1: "SELL"}, config(10_000))
    last = result.orders[-1]
    assert last.status == "NOT_EXECUTABLE" and last.reason and "NO_NEXT_BAR" in last.reason
    assert last.fill_index is None and last.fill_price is None
    assert result.trades[-1].status == "OPEN"  # marked, not sold
    assert result.signals[-1].disposition == "NO_NEXT_BAR"


def test_buy_without_enough_cash_is_rejected_not_financed() -> None:
    """Q-005: no negative cash, no implicit leverage."""
    bars = fixture_bars("golden_execution_case.csv")
    result = engine.simulate(bars, golden_signals(bars), config(103.4))  # needs 103.50
    buy = result.orders[0]
    assert buy.status == "REJECTED" and buy.reason and buy.reason.startswith("INSUFFICIENT_CASH")
    assert all(c == Decimal("103.4") for c in result.cash)
    assert all(p == 0 for p in result.position)
    assert result.trades == []
    # The SELL signal finds nothing to sell.
    assert result.signals[-1].disposition == "IGNORED_FLAT"
    assert all(check.passed for check in engine.audit(result))


def test_slippage_goes_against_the_trader_and_variable_fees_apply() -> None:
    bars = fixture_bars("golden_execution_case.csv")
    result = engine.simulate(bars, golden_signals(bars), config(10_000, 1, bps=10, slip=50))
    buy, sell = result.orders
    assert buy.fill_price == Decimal("103") * Decimal("1.005")
    assert sell.fill_price == Decimal("104") * Decimal("0.995")
    assert buy.fee == 1 + buy.fill_price * Decimal("0.001")  # type: ignore[operator]
    assert buy.slippage_cost == Decimal("0.515")
    assert all(check.passed for check in engine.audit(result))


def test_audit_catches_a_tampered_ledger() -> None:
    bars = fixture_bars("golden_execution_case.csv")
    result = engine.simulate(bars, golden_signals(bars), config(10_000))
    assert result.orders[0].cash_after is not None
    result.orders[0] = replace(result.orders[0], cash_after=result.orders[0].cash_after + 1)
    failed = {c.id for c in engine.audit(result) if not c.passed}
    assert "accounting.cash_flows" in failed


# MA strategy (Q-006, Q-007) -------------------------------------------------------------


def reference_crossings(
    closes: list[Fraction], fast: int, slow: int
) -> tuple[list[int], list[int]]:
    """Independent SMA crossings with exact fractions (not the engine's code)."""

    def sma(i: int, n: int) -> Fraction | None:
        return None if i + 1 < n else sum(closes[i + 1 - n : i + 1], Fraction(0)) / n

    buys, sells = [], []
    for i in range(1, len(closes)):
        f0, s0, f1, s1 = sma(i - 1, fast), sma(i - 1, slow), sma(i, fast), sma(i, slow)
        if None in (f0, s0, f1, s1):
            continue
        assert f0 is not None and s0 is not None and f1 is not None and s1 is not None
        if f0 <= s0 and f1 > s1:
            buys.append(i)
        if f0 >= s0 and f1 < s1:
            sells.append(i)
    return buys, sells


def test_ma_fixture_matches_the_independent_reference() -> None:
    """Q-006: SMA(2)/SMA(3) on closed bars; signals, fills and final equity as expected."""
    ma = EXPECTED["ma_fixture"]
    bars = fixture_bars("ma_crossover_case.csv")
    closes = [Fraction(str(b.close)) for b in bars]
    buys, sells = reference_crossings(closes, ma["fast_window"], ma["slow_window"])
    assert buys == ma["buy_signal_bar_indices"]
    assert sells == ma["sell_signal_bar_indices"]

    result = engine.run(
        bars,
        fast=ma["fast_window"],
        slow=ma["slow_window"],
        config=config(ma["initial_cash_usd"], ma["fee_usd_per_order"]),
    )
    filled = [o for o in result.orders if o.status == "FILLED"]
    assert [o.fill_index for o in filled if o.side == "BUY"] == ma["executed_buy_indices"]
    assert [o.fill_index for o in filled if o.side == "SELL"] == ma["executed_sell_indices"]
    assert result.position[-1] == ma["final_position_shares"]
    assert result.final_equity == Decimal(str(ma["final_equity_usd"]))
    assert result.orders[-1].status == "NOT_EXECUTABLE"
    assert all(check.passed for check in engine.audit(result))


def test_future_prices_cannot_change_past_decisions() -> None:
    """Q-007: rewrite the last bars wildly; every earlier signal and fill stays the same."""
    bars = fixture_bars("ma_crossover_case.csv")
    cfg = config(100)
    base = engine.run(bars, fast=2, slow=3, config=cfg)
    cut = 8
    shocked = bars[:cut] + [
        replace(b, open=b.open * 50, high=b.high * 50, low=b.low * 50, close=b.close * 50)
        for b in bars[cut:]
    ]
    moved = engine.run(shocked, fast=2, slow=3, config=cfg)
    before = [s for s in base.signals if s.index < cut]
    assert before == [s for s in moved.signals if s.index < cut]
    filled_before = [o for o in base.orders if o.fill_index is not None and o.fill_index < cut]
    assert filled_before == [
        o for o in moved.orders if o.fill_index is not None and o.fill_index < cut
    ]
    assert filled_before  # the window really contains decisions
    assert base.equity[:cut] == moved.equity[:cut]


def test_exact_ties_are_not_turned_into_crosses_by_float_rounding() -> None:
    closes = [engine.dec(101.3)] * 10  # (3 x 101.3) / 3 != 101.3 in binary floats
    assert engine.sma_crossings(closes, 2, 3) == []


def test_zero_trades_give_not_available_metrics_not_nan() -> None:
    bars = fixture_bars("golden_execution_case.csv")
    result = engine.simulate(bars, {}, config(10_000))
    metrics = engine.full_metrics(result)
    assert metrics["trades_closed"] == 0
    assert metrics["win_rate"] is None and metrics["profit_factor"] is None
    assert metrics["sharpe"]["status"] == "NOT_RUN"
    json.dumps(metrics, allow_nan=False)  # no NaN or Infinity anywhere


# Data import (Q-008 … Q-010) -------------------------------------------------------------


def test_invalid_fixture_is_rejected_with_both_reasons() -> None:
    """Q-008, Q-009: broken OHLC and a duplicate timestamp, both named."""
    name = "invalid_ohlc_duplicate.csv"
    result = import_bars(name, (FIXTURES / name).read_bytes(), META)
    assert result.status == "REJECTED" and not result.usable and result.bars == []
    codes = {f.code for f in result.findings if f.severity == "block"}
    assert {"OHLC_INVALID", "DUPLICATE_TIMESTAMP"} <= codes


def csv_bytes(rows: list[str], header: str = "time,open,high,low,close,volume") -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


@pytest.mark.parametrize(
    ("row", "code"),
    [
        ("2026-01-05T10:01:00Z,10,9,11,10,1", "OHLC_INVALID"),  # low > high
        ("2026-01-05T10:01:00Z,0,1,0,1,1", "PRICE_NONPOSITIVE"),
        ("2026-01-05T10:01:00Z,nan,11,9,10,1", "PRICE_NOT_FINITE"),
        ("2026-01-05T10:01:00Z,abc,11,9,10,1", "PRICE_UNPARSEABLE"),
        ("31/01/2026 10:01,10,11,9,10,1", "TIMESTAMP_UNPARSEABLE"),
        ("2026-01-05T10:01:00Z,10,11,9,10,-5", "VOLUME_NEGATIVE"),
    ],
)
def test_bad_rows_block_the_dataset(row: str, code: str) -> None:
    data = csv_bytes(
        ["2026-01-05T10:00:00Z,10,11,9,10,1", row, "2026-01-05T10:02:00Z,10,11,9,10,1"]
    )
    result = import_bars("x.csv", data, replace(META, symbol="ACME", exchange="XNYS"))
    assert result.status == "REJECTED"
    assert code in {f.code for f in result.findings}


def test_timezones_unknown_converted_and_dst_ambiguity() -> None:
    """Q-010: naive stamps need a timezone; local time becomes UTC; DST gaps are refused."""
    rows = ["2026-01-05 09:30,10,11,9,10,1", "2026-01-05 09:31,10,11,9,10,1"]
    meta = ImportMeta(symbol="ACME", exchange="XNYS", currency="USD", frequency="1m")
    unknown = import_bars("x.csv", csv_bytes(rows), meta)
    assert unknown.status == "REJECTED"
    assert "TIMEZONE_UNKNOWN" in {f.code for f in unknown.findings}

    ny = import_bars("x.csv", csv_bytes(rows), replace(meta, timezone="America/New_York"))
    assert ny.usable
    assert ny.bars[0].ts == datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    assert ny.passport["normalized_timezone"] == "UTC"
    assert ny.passport["timestamp_semantics"] == "bar_start"

    spring = ["2026-03-08 01:59,10,11,9,10,1", "2026-03-08 02:30,10,11,9,10,1"]
    gap = import_bars("x.csv", csv_bytes(spring), replace(meta, timezone="America/New_York"))
    assert "TIMESTAMP_DST_AMBIGUOUS" in {f.code for f in gap.findings}


def test_unsorted_rows_are_sorted_visibly_and_gaps_reported() -> None:
    rows = [
        "2026-01-05T10:02:00Z,10,11,9,10,1",
        "2026-01-05T10:00:00Z,10,11,9,10,0",
        "2026-01-05T10:01:00Z,10,11,9,10,1",
        "2026-01-05T10:09:00Z,10,11,9,10,1",
    ]
    result = import_bars("x.csv", csv_bytes(rows), replace(META, symbol="ACME"))
    assert result.status == "WARNING"
    codes = {f.code for f in result.findings}
    assert {"SORT_FIX", "GAPS", "ZERO_VOLUME"} <= codes
    assert [b.ts.minute for b in result.bars] == [0, 1, 2, 9]
    assert result.passport["sort_fixes"] == 1
    assert result.passport["missing_periods"]["estimated_missing_bars"] == 6


def test_fixtures_are_recognised_as_synthetic_and_snapshot_roundtrips(tmp_path: Path) -> None:
    """Q-016 (data side): the fixtures are labelled wherever they go."""
    result = import_bars("renamed.csv", (FIXTURES / "ma_crossover_case.csv").read_bytes(), META)
    assert result.synthetic and result.passport["label"] == SYNTHETIC_LABEL
    assert result.passport["original_sha256"].startswith("600b91f3")
    assert any("SYNTHETIC" in line for line in result.passport["limitations"])
    path = tmp_path / "snapshot.parquet"
    path.write_bytes(snapshot_bytes(result.bars))
    assert read_snapshot(path) == result.bars


def test_parquet_and_metatrader_style_csv_import() -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "timestamp": pa.array(
                [datetime(2026, 1, 5, 10, m, tzinfo=UTC) for m in range(3)],
                pa.timestamp("s", "UTC"),
            ),
            "open": [10.0, 10.5, 11.0],
            "high": [11.0, 11.5, 12.0],
            "low": [9.5, 10.0, 10.5],
            "close": [10.5, 11.0, 11.5],
            "volume": [100, 200, 300],
        }
    )
    sink = io.BytesIO()
    pq.write_table(table, sink)
    parquet = import_bars("bars.parquet", sink.getvalue(), replace(META, symbol="ACME"))
    assert parquet.usable and len(parquet.bars) == 3

    mt5 = csv_bytes(
        ["2026.01.05\t10:00\t10\t11\t9\t10\t5", "2026.01.05\t10:01\t10\t11\t9\t10\t5"],
        header="<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>",
    )
    result = import_bars("x.csv", mt5, replace(META, symbol="ACME", timezone="Etc/UTC"))
    assert result.usable and result.bars[1].ts == datetime(2026, 1, 5, 10, 1, tzinfo=UTC)
    assert "TICK_VOLUME" in {f.code for f in result.findings}


def test_futures_data_is_unsupported_and_huge_files_refused() -> None:
    data = (FIXTURES / "ma_crossover_case.csv").read_bytes()
    nq = import_bars("nq.csv", data, ImportMeta(symbol="NQZ6", exchange="CME", currency="USD"))
    assert nq.status == "UNSUPPORTED" and not nq.usable
    with pytest.raises(DataError):
        import_bars("x.csv", b"x" * (60 * 1024 * 1024), META)


# Spec contract (Q-015, Q-017, Q-018, Q-019) ----------------------------------------------


def spec(**changes: Any) -> dict[str, Any]:
    out = example_spec()
    for path, value in changes.items():
        section, key = path.split("__")
        out[section] = {**out[section], key: value}
    return out


def test_spec_hash_is_canonical() -> None:
    a = parse_spec(spec())
    reordered = json.loads(json.dumps(spec(), sort_keys=True))
    assert parse_spec(reordered).sha256() == a.sha256()
    assert parse_spec(spec(position__initial_cash=100.0)).sha256() == a.sha256()
    assert parse_spec(spec(execution__slippage_bps=5)).sha256() != a.sha256()


@pytest.mark.parametrize(
    "bad",
    [
        {**example_spec(), "stop_loss_pct": 1},  # Q-018: stops aren't supported — refused
        spec(position__size_units="1"),
        spec(analysis__chronological_oos_fraction=0.5),
        spec(analysis__allow_parameter_search_on_oos=True),  # Q-015
        spec(position__leverage=2),
        spec(instrument__timezone="Mars/Olympus"),
        {k: v for k, v in example_spec().items() if k != "costs"},
    ],
)
def test_invalid_specs_are_refused_with_details(bad: dict[str, Any]) -> None:
    """Q-019."""
    with pytest.raises(SpecError) as exc:
        parse_spec(bad)
    assert exc.value.code == "SPEC_INVALID" and exc.value.details


@pytest.mark.parametrize(
    "instrument",
    [
        {"asset_class": "futures", "symbol": "NQ", "exchange": "CME"},
        {"symbol": "NQZ6", "exchange": "CME"},
        {"symbol": "MNQ", "exchange": "NASDAQ"},
        {"symbol": "XAUUSD", "exchange": "OANDA"},
    ],
)
def test_futures_and_fx_specs_are_unsupported(instrument: dict[str, str]) -> None:
    """Q-017: no share accounting for contracts."""
    raw = example_spec()
    raw["instrument"] = {**raw["instrument"], **instrument}
    with pytest.raises(SpecError) as exc:
        parse_spec(raw)
    assert exc.value.code == "UNSUPPORTED_INSTRUMENT"


def test_ids_cannot_traverse_paths() -> None:
    for bad in ("../etc", "ds_../../x", "exp_ABC", ""):
        with pytest.raises(QuantLabError):
            _checked_id(bad)


def test_downsampling_keeps_peaks_and_troughs() -> None:
    values = [float(i % 97) for i in range(10_000)]
    values[5_000] = -50.0
    keep = _downsample(values, 300)
    assert len(keep) < 450 and 5_000 in keep and keep[0] == 0 and keep[-1] == 9_999


# The service: registry, runs, artifacts (Q-011 … Q-014, Q-016, Q-020) --------------------


async def run_fixture(rt: Runtime, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    dataset = await rt.quantlab.import_fixture("ma_crossover_case.csv")
    strategy = await rt.quantlab.create_strategy(raw or example_spec())
    return await run(rt, strategy["versions"][-1]["id"], dataset["id"])


async def run(rt: Runtime, version_id: str, dataset_id: str) -> dict[str, Any]:
    exp = await rt.quantlab.create_experiment(version_id, dataset_id)
    done: dict[str, Any] = {}

    async def finished() -> bool:
        done.update(await rt.quantlab.experiment(exp["id"]))
        return done["status"] in ("completed", "failed", "cancelled")

    async with asyncio.timeout(10):
        while not await finished():  # noqa: ASYNC110 - polling the service in a test
            await asyncio.sleep(0.02)
    return done


async def test_fixture_run_end_to_end(runtime: Runtime, recorder: Recorder) -> None:
    exp = await run_fixture(runtime)
    assert exp["status"] == "completed", exp["error"]
    summary = exp["summary"]
    assert exp["verdict"] == "INCONCLUSIVE"  # never more in R1, and certainly not on fixtures
    assert summary["synthetic"] and exp["dataset"]["label"] == SYNTHETIC_LABEL  # Q-016
    assert summary["metrics"]["full"]["final_equity"] == 92.5
    assert "SYNTHETIC" in summary["assessment"]["limitations"][0]
    assert summary["assessment"]["next_test"]
    manifest = exp["manifest"]
    assert manifest["engine_version"] == engine.ENGINE_VERSION
    assert manifest["code_revision"] == runtime.build
    assert manifest["validation"]["holdout_touched_for_optimization"] is False
    assert manifest["live_trading"] is False

    # Q-014: chronological, untouched OOS.
    split = summary["metrics"]["split"]
    assert split["train_end_utc"] < split["oos_start_utc"]
    assert (split["train_bars"], split["oos_bars"]) == (9, 3)
    assert summary["metrics"]["oos"]["start_equity"] == 96.0  # equity at the last train close
    checks = {c["id"]: c for c in summary["checks"]}
    assert checks["C1.chronological_split"]["result"] == "PASS"
    assert checks["C3.oos_net_after_costs"]["result"] == "NOT_RUN"  # too few OOS trades
    assert set(checks["A4.causality"]) >= {
        "id", "method_version", "result", "assumptions", "metric", "observations",
        "evidence_artifact",
    }  # fmt: skip

    # Artifacts on disk match their recorded checksums.
    folder = runtime.settings.data_dir / "quantlab" / "experiments" / exp["id"]
    kinds = {a["kind"] for a in exp["artifacts"]}
    assert kinds == {"manifest", "data_qa", "metrics", "trades", "orders", "signals", "equity",
                     "audit"}  # fmt: skip
    import hashlib

    for artifact in exp["artifacts"]:
        data = (folder / artifact["relative_path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
    assert not list(folder.parent.glob(".staging-*"))

    ledger = await runtime.quantlab.ledger(exp["id"])
    assert [t["status"] for t in ledger["trades"]] == ["CLOSED", "OPEN"]
    equity = await runtime.quantlab.equity(exp["id"])
    assert equity["total_points"] == 12 and equity["oos_start"] == split["oos_start_utc"]

    # Q-020: the UI can follow the run from its events.
    await eventually(lambda: recorder.of("quantlab.validation.completed"))
    types = [t for t in recorder.types() if t.startswith("quantlab.experiment")]
    assert types[0] == "quantlab.experiment.created"
    assert "quantlab.experiment.running" in types
    assert types[-1] == "quantlab.experiment.completed"
    assert recorder.of("quantlab.experiment.completed")[0].payload["entity_id"] == exp["id"]


async def test_same_inputs_same_experiment_and_reproducible(runtime: Runtime) -> None:
    """Q-011, Q-012: identical inputs reproduce byte-for-byte; any change is a new run."""
    exp = await run_fixture(runtime)
    again = await runtime.quantlab.create_experiment(
        exp["manifest"]["strategy_version_id"], exp["manifest"]["dataset_id"]
    )
    assert again["id"] == exp["id"] and again["status"] == "completed"
    assert len(await runtime.quantlab.experiments()) == 1
    check = await runtime.quantlab.reproduce(exp["id"])
    assert check["identical"] and check["rerun_results_sha256"] == exp["results_sha256"]

    strategy_id = exp["manifest"]["strategy_id"]
    changed = await runtime.quantlab.add_version(strategy_id, spec(execution__slippage_bps=5))
    assert [v["number"] for v in changed["versions"]] == [1, 2]
    other = await run(runtime, changed["versions"][-1]["id"], exp["manifest"]["dataset_id"])
    assert other["id"] != exp["id"]
    assert other["manifest_sha256"] != exp["manifest_sha256"]
    checks = {c["id"]: c for c in other["summary"]["checks"]}
    assert checks["C4.variants_on_dataset"]["result"] == "WARN"  # 2 variants on this data


async def test_versions_and_snapshots_are_immutable(runtime: Runtime) -> None:
    """Q-013."""
    import sqlite3

    exp = await run_fixture(runtime)
    original = exp["manifest"]["strategy_spec_sha256"]
    for sql in (
        "UPDATE ql_strategy_versions SET spec_json = '{}'",
        "UPDATE ql_datasets SET status = 'ACCEPTED'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            await runtime.db.execute(sql)
    await runtime.quantlab.add_version(exp["manifest"]["strategy_id"], spec(signal__slow_window=4))
    reread = await runtime.quantlab.experiment(exp["id"])
    assert reread["manifest"]["strategy_spec_sha256"] == original
    # The same spec saved again is not a new version.
    same = await runtime.quantlab.add_version(exp["manifest"]["strategy_id"], example_spec())
    assert len(same["versions"]) == 2


async def test_rejected_data_never_runs(runtime: Runtime, recorder: Recorder) -> None:
    dataset = await runtime.quantlab.import_fixture("invalid_ohlc_duplicate.csv")
    assert dataset["status"] == "REJECTED" and not dataset["usable"]
    assert recorder.of("quantlab.dataset.rejected")
    strategy = await runtime.quantlab.create_strategy(example_spec())
    with pytest.raises(QuantLabError) as exc:
        await runtime.quantlab.create_experiment(strategy["versions"][0]["id"], dataset["id"])
    assert exc.value.code == "DATA_INVALID"
    assert await runtime.quantlab.experiments() == []


async def test_mismatch_and_insufficient_data_are_refused(runtime: Runtime) -> None:
    dataset = await runtime.quantlab.import_fixture("ma_crossover_case.csv")
    wrong = await runtime.quantlab.create_strategy(spec(instrument__symbol="ACME"))
    with pytest.raises(QuantLabError) as exc:
        await runtime.quantlab.create_experiment(wrong["versions"][0]["id"], dataset["id"])
    assert exc.value.code == "SPEC_DATA_MISMATCH"
    slow = await runtime.quantlab.create_strategy(spec(signal__slow_window=20))
    with pytest.raises(QuantLabError) as exc:
        await runtime.quantlab.create_experiment(slow["versions"][0]["id"], dataset["id"])
    assert exc.value.code == "INSUFFICIENT_DATA"


async def test_cancel_before_the_run_starts(runtime: Runtime, recorder: Recorder) -> None:
    dataset = await runtime.quantlab.import_fixture("ma_crossover_case.csv")
    strategy = await runtime.quantlab.create_strategy(example_spec())
    lab = runtime.quantlab
    async with lab._lock:  # another run is busy
        exp = await lab.create_experiment(strategy["versions"][0]["id"], dataset["id"])
        await lab.cancel(exp["id"])
    await eventually(lambda: recorder.of("quantlab.experiment.cancelled"))
    final = await lab.experiment(exp["id"])
    assert final["status"] == "cancelled" and final["artifacts"] == []
    assert not (runtime.settings.data_dir / "quantlab" / "experiments" / exp["id"]).exists()
    # Asking again after a cancel runs it for real.
    rerun = await run(runtime, strategy["versions"][0]["id"], dataset["id"])
    assert rerun["id"] == exp["id"] and rerun["status"] == "completed"


async def test_tampered_snapshot_fails_the_run(runtime: Runtime) -> None:
    dataset = await runtime.quantlab.import_fixture("ma_crossover_case.csv")
    strategy = await runtime.quantlab.create_strategy(example_spec())
    snapshot = (
        runtime.settings.data_dir / "quantlab" / "datasets" / dataset["id"] / "snapshot.parquet"
    )
    snapshot.write_bytes(snapshot.read_bytes() + b"\0")
    with pytest.raises(QuantLabError) as exc:
        await runtime.quantlab.create_experiment(strategy["versions"][0]["id"], dataset["id"])
    assert exc.value.code == "SNAPSHOT_TAMPERED"


async def test_interrupted_runs_are_marked_failed_on_start(runtime: Runtime) -> None:
    exp = await run_fixture(runtime)
    await runtime.db.execute(
        "UPDATE ql_experiments SET status = 'running' WHERE id = ?", (exp["id"],)
    )
    await runtime.quantlab.start()
    after = await runtime.quantlab.experiment(exp["id"])
    assert after["status"] == "failed" and after["error_code"] == "INTERRUPTED"


def sawtooth_csv(bars: int) -> bytes:
    """Test data (fabricated): closes alternate 10/12, so SMA(1)/SMA(2) cross every bar."""
    start = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
    rows = []
    for i in range(bars):
        price = 12 if i % 2 else 10
        rows.append(f"{iso(start + i * MINUTE)},{price},{price},{price},{price},100")
    return csv_bytes(rows)


async def import_sawtooth(rt: Runtime) -> dict[str, Any]:
    meta = ImportMeta(
        symbol="ACME", exchange="XNYS", currency="USD", frequency="1m", adjustment="adjusted"
    )
    return await rt.quantlab.import_dataset("sawtooth.csv", sawtooth_csv(400), meta)


def sawtooth_spec(fee: float) -> dict[str, Any]:
    raw = spec(
        signal__fast_window=1,
        signal__slow_window=2,
        costs__fee_fixed_per_order=fee,
        position__initial_cash=10_000,
    )
    raw["instrument"] = {**raw["instrument"], "symbol": "ACME", "exchange": "XNYS"}
    return raw


async def test_preregistered_oos_failure_gives_failed(runtime: Runtime) -> None:
    dataset = await import_sawtooth(runtime)
    assert dataset["usable"] and not dataset["synthetic"]
    strategy = await runtime.quantlab.create_strategy(sawtooth_spec(fee=2))  # +2 gross, -4 fees
    exp = await run(runtime, strategy["versions"][0]["id"], dataset["id"])
    assert exp["status"] == "completed", exp["error"]
    oos = exp["summary"]["metrics"]["oos"]
    assert oos["trades_closed"] >= 30 and oos["net_pnl"] < 0
    assert exp["verdict"] == "FAILED"


async def test_positive_oos_is_still_only_inconclusive(runtime: Runtime) -> None:
    dataset = await import_sawtooth(runtime)
    strategy = await runtime.quantlab.create_strategy(sawtooth_spec(fee=0))
    exp = await run(runtime, strategy["versions"][0]["id"], dataset["id"])
    checks = {c["id"]: c["result"] for c in exp["summary"]["checks"]}
    assert checks["C3.oos_net_after_costs"] == "PASS"
    assert exp["verdict"] == "INCONCLUSIVE"
    text = json.dumps(exp["summary"]).upper()
    assert "VALIDATED EDGE" not in text and "PROMISING_RESEARCH_CANDIDATE" not in text


async def test_brain_report_tool(runtime: Runtime) -> None:
    from jarvis.core.trace import TraceContext

    await run_fixture(runtime)
    result = await runtime.executor.run(
        "quantlab_report", {}, ctx=TraceContext.new(), reason="test"
    )
    assert result.success and "SYNTHETIC / TEST ONLY" in result.data["report"]
    assert "INCONCLUSIVE" in result.data["report"]


# HTTP API (Q-019, Q-021, Q-022) ----------------------------------------------------------


@pytest.fixture
def client(settings: Settings) -> Any:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_api_flow_and_errors(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"  # Q-021: JARVIS still works
    assert client.get("/quantlab/health").json()["live_trading"] is False

    bad = client.post("/quantlab/strategies", json={"spec": spec(position__size_units=0)})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "SPEC_INVALID"
    assert bad.json()["detail"]["details"][0]["field"] == "position.size_units"
    futures = spec(instrument__asset_class="futures")
    assert (
        client.post("/quantlab/strategies", json={"spec": futures}).json()["detail"]["code"]
        == "UNSUPPORTED_INSTRUMENT"
    )
    check = client.post("/quantlab/strategies/validate", json={"spec": example_spec()}).json()
    assert check["valid"] and check["summary"].startswith("Long 1")

    upload = {
        "filename": "ma.csv",
        "content_base64": base64.b64encode(
            (FIXTURES / "ma_crossover_case.csv").read_bytes()
        ).decode(),
        "symbol": "TEST_SYNTHETIC",
        "exchange": "TEST_ONLY",
        "currency": "USD",
        "frequency": "1m",
    }
    dataset = client.post("/quantlab/datasets/import", json=upload).json()
    assert dataset["synthetic"] and dataset["label"] == SYNTHETIC_LABEL
    strategy = client.post("/quantlab/strategies", json={"spec": example_spec()}).json()
    body = {"strategy_version_id": strategy["versions"][0]["id"], "dataset_id": dataset["id"]}
    exp = client.post("/quantlab/experiments", json=body).json()
    for _ in range(500):
        exp = client.get(f"/quantlab/experiments/{exp['id']}").json()
        if exp["status"] == "completed":
            break
    assert exp["status"] == "completed"
    trades = client.get(f"/quantlab/experiments/{exp['id']}/trades").json()
    assert len(trades["orders"]) == 4
    assert client.get(f"/quantlab/experiments/{exp['id']}/equity").json()["total_points"] == 12
    assert client.post(f"/quantlab/experiments/{exp['id']}/reproduce").json()["identical"]
    assert client.get("/quantlab/overview").json()["latest"]["id"] == exp["id"]
    assert client.get("/quantlab/datasets/..%2F..%2Fetc").status_code == 404


def test_no_trading_or_code_execution_surface(client: TestClient) -> None:
    """Q-022: no broker, order or live endpoint; QuantLab imports no network or exec."""
    paths = list(client.get("/openapi.json").json()["paths"])
    quantlab = [p for p in paths if p.startswith("/quantlab")]
    assert quantlab
    assert not [p for p in quantlab if re.search(r"order|broker|live|execut|deploy", p)]
    package = Path(__file__).resolve().parents[1] / "jarvis" / "quantlab"
    for source in package.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        for forbidden in (r"import (httpx|requests|socket|subprocess|urllib)", r"\beval\(",
                          r"\bexec\(", r"import MetaTrader5"):  # fmt: skip
            assert not re.search(forbidden, text), (source.name, forbidden)
