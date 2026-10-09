"""Databento data infrastructure: key handling, cost limit, downloads, contract selection,
trade validation, 1-minute bars, exact volume at price and the no-lookahead guarantees.

SYNTHETIC DATA NOTICE: every trade here comes from tests/dbn_fixtures.py (a seeded random
walk written as DBN files) and exists only to test the software. It is never evidence.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dbn_fixtures import ES_INSTRUMENTS, FakeHistorical, definition_bytes, ns, synthetic_trades, trade_dtype, with_record
from edgelab.config import RESEARCH_ROOT, load_instrument, load_research_config, load_session_template
from edgelab.data import databento_client as dbc
from edgelab.data.contracts import UNDEF_TS, active_contracts, contract_table, read_definitions, roll_schedule, session_starts
from edgelab.data.databento_build import BAR_COLUMNS, build_product, evaluate_session_checks, stage_dir, validate_raw
from edgelab.data.databento_report import render_report
from edgelab.data.loader import file_sha256
from edgelab.data.manifest import DatasetEntry, get_entry, load_processed, prepare, register
from edgelab.data.trades import exact_duplicates, minute_labels, outside_session, process_chunk
from edgelab.features.profile import value_area
from edgelab.features.vap import (composite_levels, developing_levels, label_minutes, levels_from_ticks, session_levels,
                                  value_area_single)
from edgelab.sessions import label_sessions
from edgelab.studies.context import DEFAULTS
from edgelab.studies.runner import roll_exclusions

sys.path.insert(0, str(RESEARCH_ROOT / "scripts"))

START, END = "2024-02-01", "2024-04-01"
TZ = "America/New_York"
TPL = load_session_template("cme_equity_index")
ES = load_instrument("ES")
CUT = ns("2024-03-06 23:00")          # 18:00 ET: start of trading date 2024-03-07, the roll date
SPLIT_TS = "2024-02-29 23:59:59.990"  # last trade of a minute, captured after midnight UTC -> next monthly file
MIN = 60 * 10**9


def install(tmp: Path, arr: np.ndarray, cfg: dict, start: str = START, end: str = END, instruments=ES_INSTRUMENTS) -> Path:
    """Fetch the synthetic files through the real download code and a fake client."""
    raw = tmp / "raw"
    cl = FakeHistorical(arr, instruments=instruments)
    for t in dbc.plan_downloads(cl, cfg, ["ES"], [cfg["definition_schema"], cfg["schema"]], start, end, raw_dir=raw):
        dbc.download_chunk(cl, t)
    return raw


def run_pipeline(tmp: Path, arr: np.ndarray, cfg: dict, start: str = START, end: str = END, dataset_id: str = "ES_TEST"):
    raw = install(tmp, arr, cfg, start, end)
    val = validate_raw(cfg, "ES", start, end, instrument=ES, template=TPL, raw_dir=raw, out_root=tmp / "processed")
    summary = build_product(cfg, "ES", start, end, dataset_id=dataset_id, instrument=ES, template=TPL, raw_dir=raw,
                            out_root=tmp / "processed")
    return SimpleNamespace(tmp=tmp, raw=raw, out=tmp / "processed" / dataset_id, val=val, summary=summary)


def make_trades(rows: list[tuple]) -> np.ndarray:
    """Rows ``(instrument_id, ts_event UTC, price, size[, capture delay ns])`` sorted by capture time."""
    arr = np.zeros(len(rows), dtype=trade_dtype())
    for i, r in enumerate(rows):
        arr[i]["length"] = arr.dtype.itemsize // 4
        arr[i]["publisher_id"] = 1
        arr[i]["instrument_id"] = r[0]
        arr[i]["ts_event"] = ns(r[1])
        arr[i]["price"] = int(round(r[2] * 1e9))
        arr[i]["size"] = r[3]
        arr[i]["action"] = b"T"
        arr[i]["side"] = b"A"
        arr[i]["ts_recv"] = ns(r[1]) + (r[4] if len(r) > 4 else 50_000)
        arr[i]["sequence"] = i + 1
    return arr[np.argsort(arr["ts_recv"], kind="stable")]


def brute_force_trades(trades: np.ndarray, phase: str | None = None) -> pd.DataFrame:
    """Independent oracle: the active contract's trades (frozen rule hard-coded for the fixture)."""
    df = pd.DataFrame({k: trades[k].astype(np.int64) for k in ("instrument_id", "ts_event", "price", "size", "ts_recv")})
    df = df.drop_duplicates()
    et = pd.to_datetime(df["ts_event"], unit="ns", utc=True).dt.tz_convert(TZ)
    wall = et.dt.tz_localize(None)
    df["trading_date"] = (wall + pd.Timedelta(hours=6)).dt.normalize()
    df["minute_of_day"] = wall.dt.hour * 60 + wall.dt.minute
    df = df[df["instrument_id"] == np.where(df["trading_date"] >= pd.Timestamp("2024-03-07"), 102, 101)]
    if phase == "RTH":
        df = df[(df["minute_of_day"] >= 570) & (df["minute_of_day"] < 960)]
    df["minute"] = df["ts_event"] // MIN * MIN
    return df


@pytest.fixture(scope="module")
def cfg():
    return dbc.load_databento_config()


@pytest.fixture(scope="module")
def trades():
    arr = synthetic_trades()
    arr = with_record(arr, instrument_id=101, ts_event=SPLIT_TS, price=5000.0, size=3, recv_delay_ns=20_000_000,
                      sequence=900_000_001)
    # an exact copy of that record, identical in every field: removed and counted
    return with_record(arr, instrument_id=101, ts_event=SPLIT_TS, price=5000.0, size=3, recv_delay_ns=20_000_000,
                       sequence=900_000_001)


@pytest.fixture(scope="module")
def built(tmp_path_factory, cfg, trades):
    return run_pipeline(tmp_path_factory.mktemp("databento"), trades, cfg)


@pytest.fixture(scope="module")
def contracts(tmp_path_factory):
    p = tmp_path_factory.mktemp("defs") / "defs.dbn"
    p.write_bytes(definition_bytes(START, END))
    return contract_table(read_definitions([p]), root="ES", months=(3, 6, 9, 12), tick_size=0.25, roll_offset_days=8)


def chunk(arr: np.ndarray, contracts: pd.DataFrame):
    sched = active_contracts(pd.date_range("2024-01-25", "2024-04-08"), contracts, TPL)
    abd = {d: int(i) for d, i in sched["instrument_id"].items() if pd.notna(i)}
    return process_chunk(arr, chunk_index=0, contracts=contracts, tick_size=0.25, request_start_ns=ns(START),
                         request_end_ns=ns(END), template=TPL, active_by_date=abd)


# ---------------------------------------------------------------------------------------
# configuration, API key, cost limit, downloads
# ---------------------------------------------------------------------------------------
def test_config(cfg):
    assert (cfg["dataset"], cfg["schema"], cfg["stype_in"]) == ("GLBX.MDP3", "trades", "parent")
    assert {p["parent"] for p in cfg["products"].values()} == {"ES.FUT", "NQ.FUT"}
    assert float(cfg["safety"]["max_auto_download_cost_usd"]) == 20.0
    assert (cfg["pilot"]["start"], cfg["pilot"]["end"], cfg["pilot"]["purpose"]) == ("2024-01-01", "2024-04-01", "pipeline_check")
    va = DEFAULTS["value_area"]  # the data product uses the studies' value-area definition
    assert (cfg["volume_at_price"]["va_pct"], cfg["volume_at_price"]["va_method"]) == (va["va_pct"], va["va_method"])
    assert not any("key" in k.lower() for k in cfg)


def test_api_key_comes_only_from_the_environment(monkeypatch):
    monkeypatch.delenv(dbc.ENV_KEY, raising=False)
    with pytest.raises(dbc.MissingApiKey) as exc:
        dbc.api_key()
    assert 'export DATABENTO_API_KEY="' in str(exc.value) and '$env:DATABENTO_API_KEY="' in str(exc.value)
    monkeypatch.setenv(dbc.ENV_KEY, "   ")
    with pytest.raises(dbc.MissingApiKey):
        dbc.api_key()
    monkeypatch.setenv(dbc.ENV_KEY, " db-TESTKEY123 ")
    assert dbc.api_key() == "db-TESTKEY123"
    assert dbc.redact("failed for db-TESTKEY123") == "failed for <DATABENTO_API_KEY>"
    assert "db-TESTKEY123" not in dbc.request_error(RuntimeError("401 auth failed for db-TESTKEY123"))
    assert "network policy" in dbc.request_error(RuntimeError("Tunnel connection failed: 403 Forbidden"))


def test_scripts_stop_cleanly_without_a_key(monkeypatch, capsys):
    import databento_cost
    import databento_download

    monkeypatch.delenv(dbc.ENV_KEY, raising=False)
    for mod, argv in ((databento_cost, ["x", "--start", "2024-01-01", "--end", "2024-04-01", "--symbols", "NQ.FUT", "ES.FUT"]),
                      (databento_download, ["x", "--pilot", "--dry-run"])):
        monkeypatch.setattr(sys, "argv", argv)
        assert mod.main() == 2
        assert "DATABENTO_API_KEY is not set" in capsys.readouterr().out


def test_parent_symbols(cfg):
    assert dbc.parse_parent_symbol("ES.FUT") == ("ES", "FUT")
    assert dbc.parse_parent_symbol("NQ.FUT") == ("NQ", "FUT")
    for bad in ("ES", "ESH4", "ES.OPT", "es.fut", "ES.FUT.X"):
        with pytest.raises(ValueError):
            dbc.parse_parent_symbol(bad)
    assert dbc.product_for_parent(cfg, "NQ.FUT") == "NQ"
    with pytest.raises(KeyError):
        dbc.product_for_parent(cfg, "CL.FUT")


def test_requests_and_monthly_chunks():
    assert dbc.month_chunks("2024-01-01", "2024-04-01") == [("2024-01-01", "2024-02-01"), ("2024-02-01", "2024-03-01"),
                                                           ("2024-03-01", "2024-04-01")]
    assert dbc.month_chunks("2024-01-15", "2024-02-10") == [("2024-01-15", "2024-02-01"), ("2024-02-01", "2024-02-10")]
    for a, b in (("2024-04-01", "2024-01-01"), ("2024/01/01", "2024-04-01")):
        with pytest.raises(ValueError):
            dbc.Request("GLBX.MDP3", "trades", ("ES.FUT",), "parent", a, b)


def test_cost_limit_message_and_confirmation():
    dbc.check_cost(20.0, 20.0)
    dbc.check_cost(19.99, 20.0)
    with pytest.raises(dbc.CostLimitExceeded) as exc:
        dbc.check_cost(20.01, 20.0)
    assert str(exc.value) == ("Estimated Databento cost is $20.01, above configured safety limit of $20.00. "
                              "Explicit confirmation/config change required.")
    dbc.check_cost(35.10, 20.0, confirmed=35.10)
    with pytest.raises(dbc.CostLimitExceeded):
        dbc.check_cost(35.10, 20.0, confirmed=30.0)


def test_download_refuses_above_the_limit_then_fetches_once_after_confirmation(tmp_path, monkeypatch, capsys, cfg):
    import databento_download as dl

    local = {**cfg, "raw_dir": str(tmp_path / "raw")}
    fake = FakeHistorical(cost_per_request=6.0)  # 2 schemas x 2 months x $6 = $24
    monkeypatch.setattr(dl, "load_databento_config", lambda: local)
    monkeypatch.setattr(dl, "make_client", lambda: fake)
    monkeypatch.setattr(dl, "LOG", tmp_path / "log.md")
    monkeypatch.setenv(dbc.ENV_KEY, "db-SECRETKEY")
    argv = ["x", "--start", START, "--end", END, "--products", "ES"]
    monkeypatch.setattr(sys, "argv", argv)
    assert dl.main() == 3
    assert ("Estimated Databento cost is $24.00, above configured safety limit of $20.00. "
            "Explicit confirmation/config change required.") in capsys.readouterr().out
    assert not any(c[0] == "get_range" for c in fake.calls) and not (tmp_path / "raw").exists()

    monkeypatch.setattr(sys, "argv", argv + ["--confirm-cost", "24"])
    assert dl.main() == 0
    assert len(list((tmp_path / "raw").rglob("*.dbn.zst"))) == 4
    n_fetch = sum(c[0] == "get_range" for c in fake.calls)
    monkeypatch.setattr(sys, "argv", argv)
    assert dl.main() == 0  # all files on disk and verified: nothing estimated above the limit, nothing fetched
    assert sum(c[0] == "get_range" for c in fake.calls) == n_fetch == 4
    written = "".join(p.read_text() for p in (tmp_path / "raw").rglob("*.json")) + (tmp_path / "log.md").read_text()
    assert "db-SECRETKEY" not in written and "explicitly confirmed up to $24.00" in written


def test_raw_files_have_metadata_and_are_never_overwritten(built, tmp_path):
    files = sorted(built.raw.rglob("*.dbn.zst"))
    assert len(files) == 4
    for f in files:
        meta = json.loads(dbc.meta_path(f).read_text())
        for k in ("download_utc", "dataset", "schema", "symbols", "stype_in", "start", "end", "request",
                  "library_versions", "records", "file_bytes", "sha256", "cost_estimate_usd"):
            assert k in meta
        assert meta["sha256"] == file_sha256(f) and meta["file_bytes"] == f.stat().st_size and meta["records"] > 0
    task = dbc.ChunkTask("ES", dbc.Request("GLBX.MDP3", "trades", ("ES.FUT",), "parent", "2024-03-01", "2024-04-01"),
                         files[-1], exists=True)
    with pytest.raises(FileExistsError):
        dbc.download_chunk(FakeHistorical(), task)
    copy = tmp_path / files[-1].name
    shutil.copy(files[-1], copy)
    shutil.copy(dbc.meta_path(files[-1]), dbc.meta_path(copy))
    assert dbc.verify_raw(copy)["sha256"] == file_sha256(copy)
    with open(copy, "r+b") as fh:
        fh.seek(-8, 2)
        fh.write(b"\x00" * 8)
    with pytest.raises(RuntimeError, match="SHA-256"):
        dbc.verify_raw(copy)


# ---------------------------------------------------------------------------------------
# contracts and the frozen roll
# ---------------------------------------------------------------------------------------
def _def(iid, sym, cls="F", sec="FUT", asset="ES", exp="2024-03-15 13:30", act="2023-01-01", tick=250_000_000, udi="N", ts=1):
    return {"ts_recv": ts, "instrument_id": iid, "raw_symbol": sym, "instrument_class": cls, "security_type": sec,
            "asset": asset, "expiration": np.uint64(ns(exp)) if exp else UNDEF_TS, "activation": np.uint64(ns(act)),
            "min_price_increment": tick, "user_defined_instrument": udi}


def table(rows):
    return contract_table(pd.DataFrame(rows), root="ES", months=(3, 6, 9, 12), tick_size=0.25, roll_offset_days=8)


def test_outright_filter_keeps_only_quarterly_outrights():
    t = table([
        _def(101, "ESH4"), _def(102, "ESM4", exp="2024-06-21 13:30"),
        _def(201, "ESH4-ESM4", cls="S"), _def(301, "ESH4 C5000", cls="C", sec="OOF"),
        _def(401, "ESF4", exp="2024-01-19 14:30"), _def(402, "MESH4", asset="MES"),
        _def(403, "ESU4", exp="2024-09-20 13:30", udi="Y"), _def(404, "ESZ4", exp="2024-12-20 14:30", tick=500_000_000),
        _def(405, "ESH5", exp=None),
    ])
    assert t.loc[t["is_outright"], "raw_symbol"].tolist() == ["ESH4", "ESM4"]
    why = dict(zip(t["raw_symbol"], t["exclusion"]))
    assert why["ESH4-ESM4"] == "future spread" and why["ESH4 C5000"] == "option"
    assert "outside the expiry cycle" in why["ESF4"] and "asset 'MES'" in why["MESH4"]
    assert why["ESU4"] == "user-defined instrument" and "tick size" in why["ESZ4"]
    assert why["ESH5"] == "no expiration in the definition"
    h4 = t.set_index("raw_symbol").loc["ESH4"]
    assert (str(h4["rule_expiry"]), bool(h4["expiry_matches_rule"]), str(h4["roll_date"])) == ("2024-03-15", True, "2024-03-07")


def test_latest_definition_counts_and_moved_expiries_are_visible():
    t = table([_def(101, "ESH4", ts=1), _def(101, "ESH4", ts=2), _def(102, "ESM4", exp="2024-06-21 13:30", ts=1),
               _def(102, "ESM4", exp="2024-06-28 13:30", ts=2)])
    r = t.set_index("raw_symbol")
    assert r.loc["ESH4", "n_definitions"] == 2 and r.loc["ESH4", "n_expirations"] == 1
    assert r.loc["ESM4", "n_expirations"] == 2 and str(r.loc["ESM4", "expiry_date"]) == "2024-06-28"


def test_active_contract_follows_the_frozen_calendar_rule():
    t = table([_def(101, "ESH4"), _def(102, "ESM4", exp="2024-06-21 13:30"), _def(103, "ESU4", exp="2024-09-20 13:30")])
    act = active_contracts(pd.bdate_range("2024-03-04", "2024-03-12"), t, TPL)
    assert act.loc["2024-03-06", "contract"] == "ESH4" and act.loc["2024-03-06", "next_contract"] == "ESM4"
    assert act.loc["2024-03-07", "contract"] == "ESM4" and act.loc["2024-03-06", "days_to_expiry"] == 9
    rs = roll_schedule(act)
    assert [(str(d.date()), a, b) for d, a, b in rs.itertuples(index=False)] == [("2024-03-07", "ESH4", "ESM4")]
    # quarterly roll dates over a year are exactly third Friday minus 8 days
    t2 = table([_def(100 + i, s, exp=e) for i, (s, e) in enumerate(
        [("ESH4", "2024-03-15 13:30"), ("ESM4", "2024-06-21 13:30"), ("ESU4", "2024-09-20 13:30"),
         ("ESZ4", "2024-12-20 14:30"), ("ESH5", "2025-03-21 13:30")])])
    rolls = roll_schedule(active_contracts(pd.bdate_range("2024-01-02", "2024-12-31"), t2, TPL))
    assert [str(d.date()) for d in rolls["trading_date"]] == ["2024-03-07", "2024-06-13", "2024-09-12", "2024-12-12"]


def test_contract_must_be_listed_before_the_session_starts():
    # ESM4 listed at 23:30 UTC on 2024-03-06, after the 2024-03-07 session opened (23:00 UTC)
    t = table([_def(101, "ESH4"), _def(102, "ESM4", exp="2024-06-21 13:30", act="2024-03-06 23:30")])
    act = active_contracts(pd.DatetimeIndex(["2024-03-07", "2024-03-08"]), t, TPL)
    assert pd.isna(act.loc["2024-03-07", "instrument_id"]) and act.loc["2024-03-08", "contract"] == "ESM4"


def test_roll_choice_ignores_later_definitions():
    early = [_def(101, "ESH4"), _def(102, "ESM4", exp="2024-06-21 13:30")]
    later = early + [_def(103, "ESU4", exp="2024-09-20 13:30", act="2024-03-20", ts=5),
                     _def(102, "ESM4", exp="2024-06-21 13:30", ts=5)]
    days = pd.bdate_range("2024-02-01", "2024-03-19")
    a = active_contracts(days, table(early), TPL)
    b = active_contracts(days, table(later), TPL)
    pd.testing.assert_frame_equal(a[["instrument_id", "contract", "roll_date"]], b[["instrument_id", "contract", "roll_date"]])


# ---------------------------------------------------------------------------------------
# trade validation and 1-minute aggregation
# ---------------------------------------------------------------------------------------
def test_minute_bar_uses_exchange_time_order_and_leaves_empty_minutes_empty(contracts):
    arr = make_trades([
        (101, "2024-03-06 15:00:10", 5100.00, 1, 30 * 10**9),  # happened first, captured last
        (101, "2024-03-06 15:00:20", 5102.00, 2),
        (101, "2024-03-06 15:00:50", 5099.00, 3),
        (101, "2024-03-06 15:02:05", 5101.25, 4),               # 15:01 has no trade
    ])
    res = chunk(arr, contracts)
    b = res.bars.set_index("minute")
    m0 = ns("2024-03-06 15:00")
    assert list(b.index) == [m0, ns("2024-03-06 15:02")]
    ohlc = (b.at[m0, "open"], b.at[m0, "high"], b.at[m0, "low"], b.at[m0, "close"])
    assert ohlc == (5100 * 10**9, 5102 * 10**9, 5099 * 10**9, 5099 * 10**9)
    assert (b.at[m0, "volume"], b.at[m0, "n_trades"]) == (6, 3)
    assert res.checks["ts_event_decreases_within_instrument"] == 1
    assert (res.bars["first_ts"] >= res.bars["minute"]).all() and (res.bars["last_ts"] < res.bars["minute"] + MIN).all()


def test_exact_duplicates_only():
    arr = make_trades([(101, "2024-03-06 15:00:10", 5100.0, 1), (101, "2024-03-06 15:00:10", 5100.0, 1)])
    assert not exact_duplicates(arr).any()  # same trade fields, different venue sequence: two trades
    dup = np.concatenate([arr[:1], arr])
    assert exact_duplicates(dup).tolist() == [False, True, False]
    late = arr[:1].copy()
    late["ts_recv"] += 1  # same exchange record captured at another time: kept and visible
    assert not exact_duplicates(np.concatenate([arr[:1], late])).any()


def test_invalid_trades_are_counted(contracts):
    arr = make_trades([
        (101, "2024-03-06 15:00:10", 5100.00, 1), (101, "2024-03-06 15:00:11", 5100.10, 1),   # off the tick grid
        (101, "2024-03-06 15:00:12", -1.0, 1), (101, "2024-03-06 15:00:13", 5100.0, 0),       # price <= 0, size 0
        (101, "2024-03-06 22:30:00", 5100.0, 1),                                                # 17:30 ET break
        (101, "2024-03-09 17:00:00", 5100.0, 1),                                                # Saturday
        (101, "2024-03-15 14:00:00", 5100.0, 1),                                                # after ESH4 expiry
        (999, "2024-03-06 15:00:14", 5100.0, 1), (201, "2024-03-06 15:00:15", -60.0, 1),     # no definition, spread
    ])
    c = chunk(arr, contracts).checks
    assert (c["outright_off_tick_price"], c["outright_nonpositive_price"], c["outright_zero_size"]) == (1, 1, 1)
    assert (c["outright_trades_outside_session_hours"], c["outright_trades_after_expiration"]) == (2, 1)
    assert (c["records_without_definition"], c["records_excluded_non_outright"], c["records_outright"]) == (1, 1, 7)


def test_session_boundaries_in_new_york_time():
    # Wed 16:59 / 17:00 / 17:59 / 18:00 EST, then Fri 17:30, Sat, Sun 17:30 / 18:00 EDT
    at = ["2024-03-06 21:59", "2024-03-06 22:00", "2024-03-06 22:59", "2024-03-06 23:00",
          "2024-03-08 22:30", "2024-03-09 17:00", "2024-03-10 21:30", "2024-03-10 22:00"]
    lab = minute_labels(np.array([ns(t) for t in at], dtype=np.int64), TPL)
    assert outside_session(lab).tolist() == [False, True, True, False, True, True, True, False]
    assert [str(d.date()) for d in lab["trading_date"]] == ["2024-03-06", "2024-03-06", "2024-03-06", "2024-03-07",
                                                            "2024-03-08", "2024-03-09", "2024-03-10", "2024-03-11"]


def test_session_starts_at_1800_new_york_in_winter_summer_and_both_dst_changes():
    d = pd.DatetimeIndex(["2024-03-08", "2024-03-11", "2024-07-10", "2024-11-01", "2024-11-04"])
    s = session_starts(d, TPL)
    assert [t.strftime("%H:%M") for t in s] == ["18:00"] * 5
    assert [t.tz_convert("UTC").strftime("%Y-%m-%d %H:%M") for t in s] == [
        "2024-03-07 23:00", "2024-03-10 22:00", "2024-07-09 22:00", "2024-10-31 22:00", "2024-11-03 23:00"]


def test_time_zone_error_is_fatal():
    ok = {"sessions": 5, "first_bar_ny": {"18:00": 5}, "first_bar_utc": {"23:00": 5}}
    bad = {"sessions": 5, "first_bar_ny": {"19:00": 4, "18:00": 1}, "first_bar_utc": {"23:00": 5}}
    assert evaluate_session_checks({"session_open_by_regime": {"EST": ok}})[0] == []
    fatal, _ = evaluate_session_checks({"session_open_by_regime": {"EST": ok, "EDT": bad}})
    assert len(fatal) == 1 and fatal[0].startswith("EDT") and "time-zone" in fatal[0]


# ---------------------------------------------------------------------------------------
# end to end: synthetic DBN -> validation -> build -> manifest -> runner inputs
# ---------------------------------------------------------------------------------------
def test_validation_counts_and_contracts(built, trades):
    v, c = built.val, built.val["trade_checks"]
    n_spread = int((trades["instrument_id"] == 201).sum())
    assert v["passed"] and not v["fatal"] and any("exact duplicate" in r for r in v["review"])
    assert c["records"] == len(trades) and c["exact_duplicates_removed"] == 1
    assert c["records_outright"] == len(trades) - n_spread and c["records_excluded_non_outright"] == n_spread
    for k in ("records_without_definition", "outright_nonpositive_price", "outright_zero_size", "outright_off_tick_price",
              "outright_trades_outside_session_hours", "outside_request_window", "ts_recv_decreases", "negative_latency"):
        assert c[k] == 0, k
    assert v["contracts"]["excluded_by_reason"] == {"future spread": 1}
    assert v["contracts"]["outright_symbols"] == ["ESH4", "ESM4", "ESU4"]
    assert v["active_contract_by_trading_date"]["2024-03-06"] == "ESH4"
    assert v["active_contract_by_trading_date"]["2024-03-07"] == "ESM4"


def test_bars_equal_an_independent_aggregation_of_the_trades(built, trades):
    bars = pd.read_parquet(built.out / "bars_1m.parquet")
    assert list(bars.columns) == BAR_COLUMNS and str(bars["ts"].dt.tz) == "UTC" and not bars.isna().any().any()
    df = brute_force_trades(trades).sort_values(["minute", "ts_event", "ts_recv"])
    g = df.groupby("minute")
    exp = pd.DataFrame({"open": g["price"].first() / 1e9, "high": g["price"].max() / 1e9, "low": g["price"].min() / 1e9,
                        "close": g["price"].last() / 1e9, "volume": g["size"].sum().astype(float), "n_trades": g.size()})
    got = bars.set_index(bars["ts"].astype("int64").to_numpy())[["open", "high", "low", "close", "volume", "n_trades"]]
    assert len(got) == len(exp)  # one bar per minute with a trade, none for minutes without
    pd.testing.assert_frame_equal(got.sort_index(), exp.sort_index(), check_names=False, check_dtype=False)
    split = got.loc[ns("2024-02-29 23:59")]  # minute split over two monthly files: last trade sits in the March file
    assert split["close"] == 5000.0 and split["low"] == 5000.0
    assert set(bars.loc[bars["ts"] < pd.Timestamp(CUT, tz="UTC"), "contract"]) == {"ESH4"}
    assert set(bars.loc[bars["ts"] >= pd.Timestamp(CUT, tz="UTC"), "contract"]) == {"ESM4"}


def test_stage_bars_contain_only_trades_of_their_minute(built, cfg):
    stage = stage_dir(cfg, "ES", START, END, built.tmp / "processed")
    st = pd.concat([pd.read_parquet(p) for p in sorted(stage.glob("bars_*.parquet"))])
    assert (st["first_ts"] >= st["minute"]).all() and (st["last_ts"] < st["minute"] + MIN).all()


def test_exact_volume_at_price_and_vwap(built, trades):
    vap = pd.read_parquet(built.out / "vap_RTH.parquet")
    df = brute_force_trades(trades, phase="RTH")
    exp = df.groupby(["trading_date", "price"])["size"].sum()
    got = vap.set_index(["trading_date", (vap["tick"] * 250_000_000)])["volume"]
    assert got.to_dict() == {(d, p): v for (d, p), v in exp.items()}
    lv = pd.read_parquet(built.out / "levels_RTH.parquet")
    vw = df.assign(pv=df["price"] / 1e9 * df["size"]).groupby("trading_date").apply(lambda g: g["pv"].sum() / g["size"].sum())
    np.testing.assert_allclose(lv["vwap"].to_numpy(), vw.reindex(lv.index).to_numpy(), rtol=1e-12)
    dev = pd.read_parquet(built.out / "developing_RTH.parquet")
    last = dev.groupby("trading_date").tail(1).set_index("trading_date")
    np.testing.assert_allclose(last["dev_vwap"].to_numpy(), lv["vwap"].reindex(last.index).to_numpy(), rtol=1e-12)
    np.testing.assert_allclose(last["dev_poc"].to_numpy(), lv["poc"].reindex(last.index).to_numpy())
    np.testing.assert_allclose(last["dev_vah"].to_numpy(), lv["vah"].reindex(last.index).to_numpy())
    np.testing.assert_allclose(last["dev_val"].to_numpy(), lv["val"].reindex(last.index).to_numpy())


def test_session_levels_are_available_only_at_the_scheduled_end(built):
    rth = pd.read_parquet(built.out / "levels_RTH.parquet")
    allp = pd.read_parquet(built.out / "levels_ALL.parquet")
    for lv, hhmm in ((rth, "16:00"), (allp, "18:00")):
        t = pd.DatetimeIndex(lv["available_at"]).tz_convert(TZ)
        assert (t.strftime("%H:%M") == hhmm).all()
        assert (t.tz_localize(None).normalize() == lv.index).all()
    assert rth["complete"].all() and set(rth["contract"]) == {"ESH4", "ESM4"}


def test_build_summary_and_dst_checks(built):
    s = built.summary
    assert s["passed"] and s["fatal"] == [] and s["contracts_used"] == ["ESH4", "ESM4"]
    assert s["rolls"] == [{"trading_date": "2024-03-07", "from": "ESH4", "to": "ESM4"}]
    reg = s["session_checks"]["session_open_by_regime"]
    assert reg["EST"]["first_bar_ny"] == {"18:00": 6} and reg["EST"]["first_bar_utc"] == {"23:00": 6}
    assert reg["EDT"]["first_bar_ny"] == {"18:00": 2} and reg["EDT"]["first_bar_utc"] == {"22:00": 2}
    sc = s["session_checks"]
    assert sc["sessions_full_rth"] == 8 and sc["bars_in_maintenance_break"] == 0 and sc["bars_on_weekend_trade_dates"] == 0
    assert sc["roll_gaps"][0]["roll_trading_date"] == "2024-03-07"


def test_dataset_goes_through_the_unchanged_manifest_and_runner_inputs(built, tmp_path):
    manifest = tmp_path / "datasets.yaml"
    shutil.copy(RESEARCH_ROOT / "configs" / "datasets.yaml", manifest)
    row = {"id": "ES_TEST", "instrument": "ES", "path": str(built.out / "bars_1m.parquet"),
           "source": "Databento GLBX.MDP3 trades fixture (software test)", "tz_in": "UTC", "label": "open",
           "bar_minutes": 1, "adjustment": "unadjusted", "on_conflict": "abort", "purpose": "pipeline_check"}
    assert register(row, manifest) and not register(row, manifest)
    with pytest.raises(ValueError):
        register({**row, "adjustment": "difference"}, manifest)
    entry = get_entry("ES_TEST", manifest)
    _, meta = prepare(entry, out_dir=tmp_path / "processed")
    bars, meta_loaded = load_processed("ES_TEST", out_dir=tmp_path / "processed")
    assert meta["purpose"] == "pipeline_check" and meta["bar_minutes_inferred"] == 1 and meta_loaded == meta
    assert str(bars.index.tz) == TZ and list(bars.columns) == ["open", "high", "low", "close", "volume", "n_trades", "contract"]
    assert pd.Timestamp("2024-03-08 09:30", tz=TZ) in bars.index and pd.Timestamp("2024-03-08 14:30", tz="UTC") in bars.index
    assert pd.Timestamp("2024-03-11 09:30", tz=TZ) == pd.Timestamp("2024-03-11 13:30", tz="UTC")
    assert pd.Timestamp("2024-03-11 09:30", tz=TZ) in bars.index
    lab = label_sessions(bars.index, TPL)
    rth = pd.DatetimeIndex(sorted(pd.unique(lab.loc[lab["is_rth"].to_numpy(), "trading_date"])))
    assert roll_exclusions(bars, lab, entry.adjustment, ES.roll, rth) == {pd.Timestamp("2024-03-07")}


def test_research_runner_refuses_pipeline_check_datasets(monkeypatch, capsys):
    import run_event_studies

    entry = DatasetEntry(id="ES_DB_PILOT", instrument="ES", path="x.parquet", source="Databento trades", tz_in="UTC",
                         label="open", bar_minutes=1, purpose="pipeline_check")
    monkeypatch.setattr(run_event_studies, "get_entry", lambda _id: entry)
    monkeypatch.setattr(sys, "argv", ["x", "--dataset", "ES_DB_PILOT"])
    assert run_event_studies.main() == 1
    assert "pipeline_check dataset" in capsys.readouterr().out
    with pytest.raises(ValueError):
        DatasetEntry(id="x", instrument="ES", path="x", source="s", tz_in="UTC", label="open", bar_minutes=1, purpose="other")


def test_pipeline_check_admission_and_causal_study_list():
    import pipeline_check
    from test_studies import CAUSAL_STUDIES

    assert pipeline_check.CAUSAL_STUDIES == CAUSAL_STUDIES
    rcfg = load_research_config()
    base = {"path": "x", "source": "Databento trades", "tz_in": "UTC", "label": "open", "bar_minutes": 1}
    assert pipeline_check.admission(DatasetEntry(id="a", instrument="ES", purpose="pipeline_check", **base), rcfg) is None
    assert "purpose" in pipeline_check.admission(DatasetEntry(id="b", instrument="ES", purpose="research", **base), rcfg)
    assert "embargoed" in pipeline_check.admission(DatasetEntry(id="c", instrument="NQ", purpose="pipeline_check", **base), rcfg)


def test_fatal_validation_stops_the_build(tmp_path, cfg):
    arr = with_record(synthetic_trades(dates=("2024-03-06",)), instrument_id=101, ts_event="2024-03-06 15:00:00", price=5100.10)
    raw = install(tmp_path, arr, cfg)
    val = validate_raw(cfg, "ES", START, END, instrument=ES, template=TPL, raw_dir=raw, out_root=tmp_path / "p")
    assert not val["passed"] and any("off the tick grid" in f for f in val["fatal"])
    with pytest.raises(RuntimeError, match="validation failed"):
        build_product(cfg, "ES", START, END, dataset_id="X", instrument=ES, template=TPL, raw_dir=raw, out_root=tmp_path / "p")


def test_incomplete_session_at_the_end_of_the_request(tmp_path, cfg, trades):
    b = run_pipeline(tmp_path, trades, cfg, START, "2024-03-12")
    assert b.summary["incomplete_sessions_by_request_window"] == ["2024-03-12"]
    allp = pd.read_parquet(b.out / "levels_ALL.parquet")
    assert not allp.loc["2024-03-12", "complete"] and allp.drop(pd.Timestamp("2024-03-12"))["complete"].all()
    t = pd.Timestamp(allp.loc["2024-03-12", "available_at"]).tz_convert(TZ)
    assert t == pd.Timestamp("2024-03-12 18:00", tz=TZ)  # scheduled end, not the last trade in the file
    assert pd.Timestamp("2024-03-12") not in pd.read_parquet(b.out / "levels_RTH.parquet").index


# ---------------------------------------------------------------------------------------
# no lookahead
# ---------------------------------------------------------------------------------------
def test_later_trades_change_nothing_before_them(built, trades, tmp_path, cfg):
    """A different future (other prices, a far contract suddenly most liquid) leaves every
    bar, contract choice, session level, developing value and composite before it unchanged."""
    fut = trades["ts_event"] >= CUT
    alt = trades.copy()
    alt["price"][fut] += 120 * 10**9
    alt["size"][fut] *= 7
    extra = alt[fut & (alt["instrument_id"] == 102)].copy()
    extra["instrument_id"] = 103
    extra["size"] *= 100
    extra["sequence"] += 3_000_000
    alt = np.concatenate([alt, extra])
    alt = alt[np.argsort(alt["ts_recv"], kind="stable")]
    b2 = run_pipeline(tmp_path, alt, cfg, dataset_id="ES_ALT")

    a_bars, b_bars = (pd.read_parquet(d / "bars_1m.parquet") for d in (built.out, b2.out))
    cut = pd.Timestamp(CUT, tz="UTC")
    pd.testing.assert_frame_equal(a_bars[a_bars["ts"] < cut].reset_index(drop=True),
                                  b_bars[b_bars["ts"] < cut].reset_index(drop=True))
    assert not a_bars[a_bars["ts"] >= cut].reset_index(drop=True).equals(b_bars[b_bars["ts"] >= cut].reset_index(drop=True))
    early = pd.Timestamp("2024-03-07")
    a_act, b_act = (pd.read_csv(d / "active_contracts.csv", index_col=0, parse_dates=True) for d in (built.out, b2.out))
    pd.testing.assert_frame_equal(a_act[a_act.index < early], b_act[b_act.index < early])
    for name in ("levels_RTH", "levels_ALL", "composite5_RTH", "composite5_ALL"):
        a, b = (pd.read_parquet(d / f"{name}.parquet") for d in (built.out, b2.out))
        pd.testing.assert_frame_equal(a[a.index < early], b[b.index < early], obj=name)
    for ph in ("RTH", "ALL"):
        a, b = (pd.read_parquet(d / f"developing_{ph}.parquet") for d in (built.out, b2.out))
        ka, kb = a[a["known_at"] <= cut].reset_index(drop=True), b[b["known_at"] <= cut].reset_index(drop=True)
        assert len(ka) > 0
        pd.testing.assert_frame_equal(ka, kb, obj=f"developing_{ph}")


def _mpv(rows: list[tuple[str, float, int]]) -> pd.DataFrame:
    df = pd.DataFrame({"minute": [ns(m) for m, _, _ in rows], "price": [int(round(p * 1e9)) for _, p, _ in rows],
                       "volume": [v for _, _, v in rows], "n_trades": 1})
    return label_minutes(df.groupby(["minute", "price"], as_index=False)[["volume", "n_trades"]].sum(), TPL)


def test_developing_value_area_uses_only_trades_up_to_each_minute():
    rng = np.random.default_rng(3)
    minutes = pd.date_range("2024-03-06 14:30", periods=40, freq="1min", tz="UTC").strftime("%Y-%m-%d %H:%M")
    rows = [(m, 5100 + 0.25 * int(rng.integers(-12, 13)), int(rng.integers(1, 30))) for m in minutes for _ in range(4)]
    k = 25
    # a different future that also widens the session's range in both directions
    alt = rows[: 4 * (k + 1)] + [(m, p, 500) for m in minutes[k + 1:] for p in (5150.0, 5050.0, 5100.25, 5099.75)]
    a = developing_levels(_mpv(rows), phase="RTH", tick_size=0.25)
    b = developing_levels(_mpv(alt), phase="RTH", tick_size=0.25)
    pd.testing.assert_frame_equal(a.iloc[: k + 1], b.iloc[: k + 1])
    assert not a.iloc[k + 1:].reset_index(drop=True).equals(b.iloc[k + 1:].reset_index(drop=True))
    assert ((a["known_at"] - a["ts"]) == pd.Timedelta(minutes=1)).all()
    full = _mpv(rows)
    for r in range(len(a)):  # every row = the completed profile of the trades up to that minute's close
        sub = full[full["minute"] <= ns(minutes[r])]
        exp = levels_from_ticks(sub["price"].to_numpy() // 250_000_000, sub["volume"].to_numpy(), 0.25)
        assert (a.at[r, "dev_poc"], a.at[r, "dev_val"], a.at[r, "dev_vah"]) == (exp["poc"], exp["val"], exp["vah"]), r
        assert a.at[r, "dev_vwap"] == pytest.approx(exp["vwap"])


def test_value_area_single_matches_the_reference_algorithm():
    rng = np.random.default_rng(0)
    for _ in range(400):
        n = int(rng.integers(1, 60))
        v = rng.integers(0, 50, n).astype(float)
        if rng.random() < 0.3:
            v[int(rng.integers(0, n))] = v.max()  # ties for the POC
        if rng.random() < 0.2:
            v[:] = np.round(v / 10) * 10          # ties during the expansion
        assert value_area_single(v, 0.70) == value_area(v, 0.70, "single")


def test_levels_from_ticks_hand_example():
    ticks = np.arange(400, 407)
    vols = np.array([5, 10, 30, 20, 25, 5, 5])
    lv = levels_from_ticks(ticks, vols, 0.25)
    # POC 402; expansion: 403 (20) beats 401 (10) -> 50; 404 (25) beats 401 -> 75 >= 70 -> stop
    assert (lv["poc"], lv["val"], lv["vah"]) == (100.5, 100.5, 101.0)
    assert (lv["prof_low"], lv["prof_high"], lv["prof_volume"]) == (100.0, 101.5, 100.0)
    assert lv["vwap"] == pytest.approx(40285 / 100 * 0.25)


def test_composite_uses_only_completed_sessions_of_one_contract():
    dates = pd.DatetimeIndex(["2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08"], name="trading_date")
    rng = np.random.default_rng(5)
    vap = pd.DataFrame([{"trading_date": d, "tick": 20000 + t, "volume": int(rng.integers(1, 100))}
                        for d in dates for t in range(12)])
    vap["price"] = vap["tick"] * 0.25
    lv = session_levels(vap, tick_size=0.25, template=TPL, phase="RTH")
    contract = pd.Series(["ESH4", "ESH4", "ESH4", "ESM4", "ESM4"], index=dates)
    complete = pd.Series(True, index=dates)
    comp = composite_levels(vap, lv, contract, complete, n_sessions=2, tick_size=0.25)
    assert comp["poc"].notna().tolist() == [False, True, True, False, True]  # first: one session only; 03-07 crosses the roll
    both = vap[vap["trading_date"].isin(dates[1:3])].groupby("tick")["volume"].sum()
    exp = levels_from_ticks(both.index.to_numpy(), both.to_numpy(), 0.25)
    assert (comp.at[dates[2], "poc"], comp.at[dates[2], "val"], comp.at[dates[2], "vah"]) == (exp["poc"], exp["val"], exp["vah"])
    assert (comp["available_at"] == lv["available_at"]).all()
    complete[dates[1]] = False
    comp2 = composite_levels(vap, lv, contract, complete, n_sessions=2, tick_size=0.25)
    assert comp2["poc"].notna().tolist() == [False, False, False, False, True]


# ---------------------------------------------------------------------------------------
# reports and charts
# ---------------------------------------------------------------------------------------
def test_reports_render_and_keep_price_differences_out_of_git(built):
    import build_market_data

    pub = build_market_data.public_build(built.summary)
    assert all("spread_points" not in g and "atr_d14" not in g for g in pub["session_checks"]["roll_gaps"])
    text = render_report([{k: v for k, v in built.val.items() if k != "stage_files"}], [pub])
    assert "Result: **PASS**" in text and "ESH4: 2024-02-28 .. 2024-03-06 (4 dates)" in text
    assert "Dataset ES_TEST" in text and "EST" in text and "EDT" in text


def test_debug_charts_pick_sessions_by_rule(built):
    import databento_debug_charts as charts

    sessions = pd.read_csv(built.out / "sessions.csv", index_col="trading_date", parse_dates=["trading_date"])
    rolls = pd.read_csv(built.out / "rolls.csv")
    picks = charts.pick_sessions(sessions, rolls, seed=1)
    assert picks == charts.pick_sessions(sessions, rolls, seed=1)
    cats = {c: str(d.date()) for c, d, _ in picks}
    assert {"roll", "dst", "high_volume", "normal", "overnight"} <= set(cats)
    assert (cats["roll"], cats["dst"]) == ("2024-03-07", "2024-03-11")
    normals = [str(d.date()) for c, d, _ in picks if c == "normal"]
    assert not {"2024-03-07", "2024-03-08", "2024-03-11", cats["high_volume"]} & set(normals)
    paths = charts.make_charts(built.out, "ES_TEST", 0.25, seed=1)
    assert len(paths) == len(picks) and all(p.stat().st_size > 10_000 for p in paths)
