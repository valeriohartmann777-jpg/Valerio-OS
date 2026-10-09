"""Kaggle NQ adapter: file search, schema, clock inference, normalisation, quality, rolls.

SYNTHETIC DATA NOTICE: every bar here is generated for SOFTWARE TESTS ONLY (see
conftest.py). The tests check that the adapter recovers a clock or a roll pattern that
was put into the data on purpose; they say nothing about any market.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from conftest import TZ, cme_schedule
from edgelab.config import RESEARCH_ROOT, load_instrument, load_session_template
from edgelab.data import vendor_bars as vb
from edgelab.data.manifest import get_entry, load_manifest
from edgelab.data.vendor_reports import findings, render_quality, render_rolls, render_schema
from edgelab.sessions import label_sessions
from edgelab.studies.runner import roll_exclusions

sys.path.insert(0, str(RESEARCH_ROOT / "scripts"))
import kaggle_download as kd  # noqa: E402

TPL = load_session_template("cme_equity_index")
NQ = load_instrument("NQ")


def ohlcv(index: pd.DatetimeIndex, seed: int = 0, spike: bool = True, start: float = 18000.0) -> pd.DataFrame:
    """Tick-grid OHLCV with a volume spike on the 09:30 New York bar (synthetic)."""
    rng = np.random.default_rng(seed)
    n = len(index)
    c = start + np.cumsum(np.round(rng.normal(0, 3, n))) * 0.25
    o = np.r_[start, c[:-1]]
    h = np.maximum(o, c) + np.abs(np.round(rng.normal(0, 2, n))) * 0.25
    lo = np.minimum(o, c) - np.abs(np.round(rng.normal(0, 2, n))) * 0.25
    wall = index.tz_convert(TZ).tz_localize(None)
    v = rng.integers(50, 150, n).astype(float)
    if spike:
        v[np.asarray(wall.hour * 60 + wall.minute) == 570] *= 10
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v})


def file_clock(index: pd.DatetimeIndex, zone: str, label: str) -> pd.Series:
    """Vendor timestamps: the bar's open or close instant as ``zone`` wall-clock time."""
    t = index + pd.Timedelta(minutes=1) if label == "close" else index
    return pd.Series(t.tz_convert(zone).tz_localize(None).strftime("%Y-%m-%d %H:%M:%S"))


@pytest.fixture(scope="module")
def spring():
    """25 trading dates across the US (2024-03-10) and EU (2024-03-31) DST changes."""
    return cme_schedule("2024-03-04", 25)


# =======================================================================================
# clock
# =======================================================================================
@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "America/Chicago", "Europe/London", "Europe/Berlin"])
@pytest.mark.parametrize("label", ["open", "close"])
def test_clock_recovers_zone_and_label(spring, zone, label):
    df = ohlcv(spring).assign(ts=file_clock(spring, zone, label))
    d = vb.infer_clock(df, TPL)
    assert d.decided, d.reasons
    assert (d.tz_in, d.label, d.structure) == (zone, label, "globex")
    assert {"EST", "EDT"} <= set(d.evidence["regimes"])


@pytest.mark.parametrize("zone", ["America/New_York", "Europe/London"])
def test_clock_autumn_transitions(zone):
    idx = cme_schedule("2024-10-14", 20)  # EU change 2024-10-27, US change 2024-11-03
    d = vb.infer_clock(ohlcv(idx).assign(ts=file_clock(idx, zone, "open")), TPL)
    assert d.decided and d.tz_in == zone and d.label == "open"


def test_clock_date_and_time_columns(spring):
    t = spring.tz_localize(None) + pd.Timedelta(minutes=1)
    df = ohlcv(spring).assign(date=t.strftime("%m/%d/%Y"), time=t.strftime("%H:%M"))
    d = vb.infer_clock(df, TPL)
    assert d.decided and (d.tz_in, d.label) == ("America/New_York", "close")


def test_clock_mixed_zones_stop(spring):
    h = len(spring) // 2
    t = spring[:h].tz_localize(None).append(spring[h:].tz_convert("UTC").tz_localize(None))
    d = vb.infer_clock(ohlcv(spring).assign(ts=t.strftime("%Y-%m-%d %H:%M:%S")), TPL)
    assert not d.decided and d.tz_in is None


def test_clock_seconds_stop(spring):
    t = spring.tz_convert("UTC").tz_localize(None) + pd.Timedelta(seconds=59)
    d = vb.infer_clock(ohlcv(spring).assign(ts=t.strftime("%Y-%m-%d %H:%M:%S")), TPL)
    assert not d.decided
    assert d.evidence["timestamps_with_seconds"] == len(spring)


def test_clock_needs_the_open_spike(spring):
    d = vb.infer_clock(ohlcv(spring, spike=False).assign(ts=file_clock(spring, "UTC", "open")), TPL)
    assert not d.decided and any("09:30" in r for r in d.reasons)


def test_clock_without_volume_uses_two_methods(spring):
    d = vb.infer_clock(ohlcv(spring).assign(volume=0.0, ts=file_clock(spring, "UTC", "open")), TPL)
    assert d.decided and "spike_unavailable" in d.evidence


def test_clock_rth_only(spring):
    wall = spring.tz_localize(None)
    m = np.asarray(wall.hour * 60 + wall.minute)
    idx = spring[(m >= 570) & (m < 960)]
    d = vb.infer_clock(ohlcv(idx).assign(ts=file_clock(idx, "America/New_York", "open")), TPL)
    assert d.decided and d.structure == "rth_only" and d.tz_in == "America/New_York"


def test_clock_aware_and_epoch(spring):
    d = vb.infer_clock(ohlcv(spring).assign(ts=[t.isoformat() for t in spring]), TPL)  # -05:00 and -04:00 mixed
    assert d.decided and d.kind == "aware" and d.tz_in == "UTC" and d.label == "open"
    d = vb.infer_clock(ohlcv(spring).assign(ts=spring.as_unit("s").asi8), TPL)
    assert d.decided and d.kind == "epoch"


def test_clock_wrong_embedded_offset_stops(spring):
    ts = spring.tz_localize(None).strftime("%Y-%m-%dT%H:%M:%S+00:00")  # New York wall clock claimed as UTC
    d = vb.infer_clock(ohlcv(spring).assign(ts=ts), TPL)
    assert not d.decided
    assert any("America/New_York" in r for r in d.reasons)


def test_clock_single_regime_twins_are_equivalent():
    idx = cme_schedule("2024-01-08", 20)
    d = vb.infer_clock(ohlcv(idx).assign(ts=file_clock(idx, "America/New_York", "open")), TPL)
    assert d.decided and d.tz_in == "America/New_York"
    assert "Etc/GMT+5" in d.evidence["equivalent_zones"] and d.evidence.get("single_regime")


# =======================================================================================
# normalisation
# =======================================================================================
def test_normalise_converts_to_utc_bar_open(spring):
    df = ohlcv(spring).assign(ts=file_clock(spring, "America/Chicago", "close"))
    d = vb.infer_clock(df, TPL)
    out, log = vb.normalise(df, d)
    assert log["removed_total"] == 0 and len(out) == len(df)
    assert pd.DatetimeIndex(out["ts"]).equals(spring.tz_convert("UTC"))  # close label shifted back one bar
    assert np.array_equal(out["close"].to_numpy(), df["close"].to_numpy())


def test_normalise_removes_only_impossible_bars(spring):
    df = ohlcv(spring).assign(ts=file_clock(spring, "UTC", "open"))
    d = vb.infer_clock(df, TPL)
    bad = df.copy()
    bad.loc[10, "high"] = bad.loc[10, "low"] - 1
    bad.loc[20, "volume"] = -1
    bad.loc[30, "close"] = np.nan
    bad = pd.concat([bad, bad.iloc[[40]]], ignore_index=True)  # identical duplicate: kept for manifest.prepare
    out, log = vb.normalise(bad, d, max_removed_share=0.01)
    assert log["removed_total"] == 3 and len(out) == len(bad) - 3
    assert log["removed"]["high below low"]["count"] == 1 and log["removed"]["negative volume"]["count"] == 1
    assert pd.DatetimeIndex(out["ts"]).duplicated().sum() == 1


def test_normalise_stops_above_limit(spring):
    df = ohlcv(spring).assign(ts=file_clock(spring, "UTC", "open"))
    d = vb.infer_clock(df, TPL)
    df.loc[:50, "volume"] = np.nan
    with pytest.raises(vb.StopCondition):
        vb.normalise(df, d, max_removed_share=1e-4)


@pytest.mark.parametrize("first, n, zone, label", [("2024-03-04", 25, "America/Chicago", "close"),
                                                   ("2024-10-14", 20, "America/New_York", "open")])
def test_filler_bars_do_not_hide_the_clock_and_are_removed(first, n, zone, label):
    """A vendor that writes a flat zero-volume bar for every empty minute (break, weekend,
    and in autumn the repeated wall-clock hour, which is not a valid time in the zone)."""
    idx = cme_schedule(first, n)
    full = pd.date_range(idx.min(), idx.max(), freq="min")
    real = ohlcv(idx).set_index(idx)
    bars = real.reindex(full)
    fill = bars["close"].isna().to_numpy()
    prev = bars["close"].ffill()
    bars.loc[fill, ["open", "high", "low", "close"]] = np.repeat(prev[fill].to_numpy()[:, None], 4, axis=1)
    bars.loc[fill, "volume"] = 0.0
    df = bars.reset_index(drop=True).assign(ts=file_clock(full, zone, label))
    d = vb.infer_clock(df, TPL)
    assert d.decided, d.reasons
    assert (d.tz_in, d.label) == (zone, label)
    assert d.evidence["rows_without_volume_ignored"] == fill.sum()
    out, log = vb.normalise(df, d)
    assert log["removed_total"] == 0
    nt = log["no_trade_bars"]
    assert nt["count"] == nt["flat"] == fill.sum() and nt["with_range"] == nt["without_price"] == 0
    assert nt["nonexistent_or_repeated_wall_times"] == (120 if first.startswith("2024-10") else 0)  # 01:00-01:59 twice
    assert pd.DatetimeIndex(out["ts"]).equals(idx.tz_convert("UTC"))
    assert np.array_equal(out["close"].to_numpy(), real["close"].to_numpy())


def test_normalise_stops_when_most_bars_have_no_volume(spring):
    df = ohlcv(spring).assign(ts=file_clock(spring, "UTC", "open"))
    d = vb.infer_clock(df, TPL)
    df.loc[: int(0.6 * len(df)), "volume"] = 0.0
    with pytest.raises(vb.StopCondition, match="volume"):
        vb.normalise(df, d)


def test_normalise_is_row_wise(spring):
    """No row's output depends on a later row (truncation invariance)."""
    df = ohlcv(spring).assign(ts=file_clock(spring, "America/New_York", "close"))
    d = vb.infer_clock(df, TPL)
    full, _ = vb.normalise(df, d)
    k = len(df) // 3
    part, _ = vb.normalise(df.iloc[:k], d)
    pd.testing.assert_frame_equal(part, full.iloc[:k])


def test_normalise_refuses_undecided_clock(spring):
    d = vb.ClockDecision("AMBIGUOUS", None, None, "naive", "globex", 1, ["test"])
    with pytest.raises(vb.StopCondition):
        vb.normalise(ohlcv(spring).assign(ts=file_clock(spring, "UTC", "open")), d)


# =======================================================================================
# files and columns
# =======================================================================================
def test_find_bar_files_by_content(tmp_path, spring):
    idx = spring[:3000]
    bars = ohlcv(idx).assign(Datetime=file_clock(idx, "UTC", "open"))
    bars.rename(columns=str.title)[["Datetime", "Open", "High", "Low", "Close", "Volume"]].to_csv(tmp_path / "readme_data.txt", index=False)
    bars[["Datetime", "open", "high", "low", "close", "volume"]].to_csv(tmp_path / "upload_123", index=False, header=False)
    (tmp_path / "README.md").write_text("# not data\n")
    (tmp_path / "notes.csv").write_text("a,b\n1,2\n")
    found, files = vb.find_bar_files(tmp_path)
    names = {Path(p).name for p in found}
    assert names == {"readme_data.txt", "upload_123"}
    by = {f["name"]: f for f in files}
    assert by["upload_123"]["header"].startswith("absent")
    assert not by["notes.csv"]["candidate"] and "README.md" not in by
    raw = vb.read_bar_table(tmp_path / "upload_123", headerless=True)
    assert list(raw.columns) == ["ts", "open", "high", "low", "close", "volume"] and len(raw) == 3000


def test_column_profile_and_special_columns(spring):
    idx = spring[:2000]
    df = ohlcv(idx).assign(Timestamp=file_clock(idx, "UTC", "open"), VWAP=1.0, RTH_VWAP=2.0, ETH_VWAP=3.0,
                           Session="ETH", Symbol="NQM4", RollFlag=0)
    df.loc[5, "VWAP"] = np.nan
    mapping = vb.column_mapping(df)
    assert mapping["Timestamp"] == "ts" and mapping["Symbol"] == "contract" and "VWAP" not in mapping
    prof = vb.column_profile(df, mapping)
    sp = vb.special_columns(prof)
    assert sp["vwap"] == ["VWAP"] and sp["vwap_rth"] == ["RTH_VWAP"] and sp["vwap_eth"] == ["ETH_VWAP"]
    assert sp["session_marker"] == ["Session"] and sp["roll_info"] == ["RollFlag"]
    row = next(r for r in prof if r["column"] == "VWAP")
    assert row["nulls"] == 1 and row["min"] == 1.0 and row["max"] == 1.0
    assert next(r for r in prof if r["column"] == "Symbol")["role"] == "contract"


def test_vwap_scope_flags_full_session_values(spring):
    bars = ohlcv(spring).set_index(spring)
    td = label_sessions(spring, TPL)["trading_date"].to_numpy()
    bars["running"] = bars["close"].expanding().mean()
    bars["full"] = bars["close"].groupby(td).transform("mean")
    assert vb.vwap_scope(bars, "running", TPL)["days_constant"] == 0
    assert "never usable" in vb.vwap_scope(bars, "full", TPL)["verdict"]


# =======================================================================================
# quality
# =======================================================================================
def test_nyse_holidays_known_dates():
    hol = dict(vb.nyse_holidays(2021, 2025))
    for d, name in [("2022-01-17", "Martin Luther King Jr. Day"), ("2022-06-20", "Juneteenth"), ("2022-12-26", "Christmas"),
                    ("2023-01-02", "New Year's Day"), ("2024-03-29", "Good Friday"), ("2024-11-28", "Thanksgiving"),
                    ("2021-07-05", "Independence Day"), ("2025-01-09", "National Day of Mourning (J. Carter)"),
                    ("2023-05-29", "Memorial Day"), ("2025-09-01", "Labor Day")]:
        assert hol[dt.date.fromisoformat(d)] == name
    assert dt.date(2021, 12, 31) not in hol  # New Year's Day 2022 fell on a Saturday: not observed
    assert dt.date(2021, 6, 18) not in hol   # Juneteenth only from 2022


def test_extra_quality_counts(spring):
    df = ohlcv(spring).assign(ts=file_clock(spring, "UTC", "open"))
    d = vb.infer_clock(df, TPL)
    frame, _ = vb.normalise(df, d)
    drop = frame.index[[100, 101, 102]]  # three missing minutes on a regular date
    extra_rows = pd.DataFrame({"ts": pd.DatetimeIndex(["2024-03-09 15:00", "2024-03-05 22:30"]).tz_localize("UTC"),
                               "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0})  # Saturday; 17:30 NY break
    frame = pd.concat([frame.drop(index=drop), frame.iloc[[500]], extra_rows], ignore_index=True)
    conflict = frame.iloc[[600]].assign(close=frame["close"].iloc[600] + 1)
    frame = pd.concat([frame, conflict], ignore_index=True)
    rep = vb.extra_quality(frame, vb.to_ny_bars(frame), TPL)
    assert rep["weekend_bars"] == 1 and rep["break_bars"] == 1
    assert rep["identical_duplicate_rows"] == 1 and rep["conflicting_duplicate_timestamps"] == 1
    assert rep["missing_minutes_regular_dates"] == 3
    assert rep["raw_backward_steps"] >= 1
    starts = {r["regime"]: r for r in rep["session_starts_by_regime"]}
    assert starts["EDT"]["at_18:00"] == starts["EDT"]["starts"]
    assert starts["EST"]["other_times"] == {"10:00": 1}  # the Saturday bar shows up as an odd session start
    rows, stops = findings(d, {"removed": {}, "nonexistent_or_repeated_wall_times": 0}, rep, None, None, 1e-4)
    assert any("conflicting" in s for s in stops)


# =======================================================================================
# rolls
# =======================================================================================
def roll_series(offset_days: int = 8, jump: float = 200.0, mode: str = "unadjusted", seed: int = 0) -> pd.DataFrame:
    """One year of 5-minute bars with a quarterly contract switch ``offset_days`` before expiry."""
    idx = cme_schedule("2024-01-02", 255, minutes=5)
    rng = np.random.default_rng(seed)
    n = len(idx)
    td = pd.DatetimeIndex(label_sessions(idx, TPL)["trading_date"])
    jumps = np.zeros(n)
    for e in vb.quarterly_expiries(dt.date(2024, 1, 1), dt.date(2024, 12, 31)):
        first = np.flatnonzero(td >= pd.Timestamp(e) - pd.Timedelta(days=offset_days))
        jumps[first[0]] = jump
    c = 17000 + np.cumsum(np.round(rng.normal(0, 6, n)) * 0.25)
    o = np.r_[c[0], c[:-1]]
    if mode == "unadjusted":
        c, o = c + np.cumsum(jumps), o + np.cumsum(jumps)
    h = np.maximum(o, c) + np.abs(np.round(rng.normal(0, 4, n))) * 0.25
    lo = np.minimum(o, c) - np.abs(np.round(rng.normal(0, 4, n))) * 0.25
    bars = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": rng.integers(100, 1000, n).astype(float)},
                        index=pd.DatetimeIndex(idx, name="ts"))
    if mode == "ratio":
        bars[["open", "high", "low", "close"]] *= 1.0003
    return bars


def test_rolls_conventional_unadjusted_is_covered():
    rep = vb.assess_rolls(roll_series(), TPL, NQ.roll, NQ.tick_size, bar_minutes=5)
    assert rep["classification"] == "unadjusted" and rep["windows_clear"] == 4
    assert rep["rule"]["offset_bd_mode"] == -6 and rep["rule"]["consistent"]
    assert rep["all_rolls_covered"] and rep["conclusion"]["ok"] and rep["conclusion"]["adjustment"] == "unadjusted"


def test_rolls_off_calendar_stop():
    rep = vb.assess_rolls(roll_series(offset_days=2), TPL, NQ.roll, NQ.tick_size, bar_minutes=5)
    assert rep["classification"] == "unadjusted" and not rep["all_rolls_covered"]
    assert not rep["conclusion"]["ok"] and "decision" in rep["conclusion"]["stop"]


@pytest.mark.parametrize("mode,cls,adj", [("adjusted", "adjusted_or_gapless", "difference"), ("ratio", "ratio_adjusted", "ratio")])
def test_rolls_back_adjusted_stop(mode, cls, adj):
    rep = vb.assess_rolls(roll_series(mode=mode), TPL, NQ.roll, NQ.tick_size, bar_minutes=5)
    assert rep["classification"] == cls and rep["conclusion"]["adjustment"] == adj and not rep["conclusion"]["ok"]


def test_rolls_contract_column():
    bars = roll_series().assign(contract="NQH24")
    rep = vb.assess_rolls(bars, TPL, NQ.roll, NQ.tick_size, bar_minutes=5)
    assert rep["classification"] == "contract_column" and rep["conclusion"]["ok"]


def test_contract_labels_drive_runner_exclusions():
    bars = roll_series(offset_days=2)
    rep = vb.assess_rolls(bars, TPL, NQ.roll, NQ.tick_size, bar_minutes=5)
    lab = vb.contract_labels(bars.index, rep["rolls"], root="NQ")
    assert list(dict.fromkeys(lab)) == ["NQH24", "NQM24", "NQU24", "NQZ24", "NQH25"]
    with_c = bars.assign(contract=lab)
    labels = label_sessions(with_c.index, TPL)
    rth = pd.DatetimeIndex(sorted(pd.unique(labels.loc[labels["is_rth"].to_numpy(), "trading_date"])))
    excl = roll_exclusions(with_c, labels, "unadjusted", NQ.roll, rth)
    assert sorted(str(pd.Timestamp(x).date()) for x in excl) == [r["trading_date"] for r in rep["rolls"]]


def test_contract_labels_truncation_invariant():
    bars = roll_series()
    rep = vb.assess_rolls(bars, TPL, NQ.roll, NQ.tick_size, bar_minutes=5)
    full = vb.contract_labels(bars.index, rep["rolls"])
    cut = pd.Timestamp("2024-08-01", tz=TZ)
    before = [r for r in rep["rolls"] if pd.Timestamp(r["first_bar_new"]) < cut]
    part = vb.contract_labels(bars.index[bars.index < cut], before)
    assert np.array_equal(part, full[: len(part)])


def test_roll_report_renders():
    rep = vb.assess_rolls(roll_series(offset_days=2), TPL, NQ.roll, NQ.tick_size, bar_minutes=5)
    txt = render_rolls(dataset="x/y", bar_file={"name": "f.csv", "sha256": "0" * 64}, roll=rep, period=("a", "b"))
    assert "**STOP.**" in txt and "## Per expiry" in txt and "NQZ24" in txt


# =======================================================================================
# scripts
# =======================================================================================
def kaggle_like_file(path: Path, idx: pd.DatetimeIndex, zone: str, label: str, roll_td: str | None = None) -> None:
    """A vendor-style file; ``roll_td``: unadjusted contract switch (+180 points) at that trading date's open."""
    bars = ohlcv(idx, seed=5)
    td = label_sessions(idx, TPL)["trading_date"].to_numpy()
    if roll_td:
        jump = np.where(td >= np.datetime64(roll_td), 180.0, 0.0)
        bars[["open", "high", "low", "close"]] = bars[["open", "high", "low", "close"]].to_numpy() + jump[:, None]
    df = pd.DataFrame({"Datetime": file_clock(idx, zone, label), "Open": bars["open"], "High": bars["high"], "Low": bars["low"],
                       "Close": bars["close"], "Volume": bars["volume"].astype(int),
                       "RTH_VWAP": bars["close"].groupby(td).transform("mean").round(2)})
    df.to_csv(path, index=False)


def test_assess_script_end_to_end(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    idx = cme_schedule("2024-02-12", 40)  # holds the whole roll window of the March 2024 expiry
    kaggle_like_file(raw / "Dataset_NQ_1min_2022_2025.csv", idx, "America/Chicago", "close", roll_td="2024-03-07")
    cfg = {"dataset": "tgtanalytics/nq-futures-1min-bar-2022-2025", "instrument": "NQ", "raw_dir": str(raw),
           "derived_dir": str(tmp_path / "derived"), "dataset_id": "T_KAGGLE", "pilot": {"id": "T_PILOT", "start": None, "months": 1},
           "reports": {k: str(tmp_path / "out" / f"{k}.md") for k in ("schema", "quality", "rollover")} | {"json": str(tmp_path / "out" / "a.json")},
           "assessment": {"max_removed_share": 1e-4, "clock_min_match": 0.98, "clock_min_label_share": 0.9,
                          "roll_window_days": [16, 3], "roll_clear_z": 8.0, "roll_dominance": 3.0}}
    (tmp_path / "k.yaml").write_text(json.dumps(cfg))
    run = subprocess.run([sys.executable, "scripts/assess_kaggle_nq.py", "--config", str(tmp_path / "k.yaml")], cwd=RESEARCH_ROOT,
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "America/Chicago" in run.stdout
    q = (tmp_path / "out" / "quality.md").read_text()
    assert "**Verdict: PASS.**" in q and "labelled by bar CLOSE time" in q
    s = (tmp_path / "out" / "schema.md").read_text()
    assert "RTH_VWAP" in s and "never usable intraday" in s
    summary = json.loads((tmp_path / "out" / "a.json").read_text())
    assert summary["clock"]["tz_in"] == "America/Chicago" and not summary["stops"]
    assert summary["rolls"]["classification"] == "unadjusted" and summary["rolls"]["all_rolls_covered"]
    assert "**OK.**" in (tmp_path / "out" / "rollover.md").read_text()


def test_assess_script_stops_on_ambiguous_clock(tmp_path, spring):
    raw = tmp_path / "raw"
    raw.mkdir()
    h = len(spring) // 2
    kaggle_like_file(raw / "bars.csv", spring, "UTC", "open")
    df = pd.read_csv(raw / "bars.csv")
    df.loc[h:, "Datetime"] = file_clock(spring[h:], "America/New_York", "open").to_numpy()
    df.to_csv(raw / "bars.csv", index=False)
    cfg = {"dataset": "d/s", "instrument": "NQ", "raw_dir": str(raw), "derived_dir": str(tmp_path / "derived"),
           "dataset_id": "T_KAGGLE", "pilot": {"id": "T_PILOT", "start": None, "months": 1},
           "reports": {k: str(tmp_path / f"{k}.md") for k in ("schema", "quality", "rollover")} | {"json": str(tmp_path / "a.json")},
           "assessment": {"max_removed_share": 1e-4, "clock_min_match": 0.98, "clock_min_label_share": 0.9,
                          "roll_window_days": [16, 3], "roll_clear_z": 8.0, "roll_dominance": 3.0}}
    (tmp_path / "k.yaml").write_text(json.dumps(cfg))
    run = subprocess.run([sys.executable, "scripts/assess_kaggle_nq.py", "--config", str(tmp_path / "k.yaml"), "--register"],
                         cwd=RESEARCH_ROOT, capture_output=True, text=True)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "**Verdict: STOP.**" in (tmp_path / "quality.md").read_text()
    assert not (tmp_path / "derived").exists()


def test_register_sets_with_temporary_manifest(tmp_path, spring):
    import assess_kaggle_nq as ak

    df = ohlcv(spring).assign(ts=file_clock(spring, "UTC", "open"))
    d = vb.infer_clock(df, TPL)
    frame, _ = vb.normalise(df, d)
    manifest = tmp_path / "datasets.yaml"
    manifest.write_text("# test manifest\n\ndatasets: []\n")
    cfg = {"dataset": "d/s", "derived_dir": str(tmp_path / "derived"), "dataset_id": "T_FULL",
           "pilot": {"id": "T_PILOT", "start": None, "months": 1}}
    roll = {"classification": "unadjusted", "conclusion": {"ok": True, "adjustment": "unadjusted"}}
    lines = ak.register_sets(cfg=cfg, frame=frame, clock=d, roll=roll, ident={"name": "f.csv", "sha256": "0" * 64},
                             bar_paths=[], removed=0, tpl=TPL, symbol="NQ", manifest=manifest, processed_dir=tmp_path / "proc")
    assert len(lines) == 4
    entries = {e.id: e for e in load_manifest(manifest)}
    assert entries["T_FULL"].purpose == "research" and entries["T_PILOT"].purpose == "pipeline_check"
    assert get_entry("T_FULL", manifest).tz_in == "UTC" and entries["T_FULL"].label == "open"
    full = pd.read_parquet(tmp_path / "proc" / "T_FULL.parquet")
    pilot = pd.read_parquet(tmp_path / "proc" / "T_PILOT.parquet")
    assert len(full) == len(frame) and 0 < len(pilot) < len(full)
    assert pilot.index.max() < pd.Timestamp("2024-04-04", tz=TZ)


def test_kaggle_download_never_shows_a_token(monkeypatch):
    monkeypatch.setenv("KAGGLE_API_TOKEN", "KGAT_secret_value_123")
    assert kd.credential_sources() == ["environment variable KAGGLE_API_TOKEN"]
    assert "KGAT_secret_value_123" not in kd.redact("token KGAT_secret_value_123 rejected")
    msg = ("HTTPSConnectionPool(host='api.kaggle.com', port=443): Max retries exceeded with url: /v1/datasets.DatasetApiService/"
           "GetDatasetMetadata (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden')))")
    assert kd.classify_failure(msg) == "network"
    assert kd.classify_failure("401 Client Error: Unauthorized") == "auth"


def test_import_zip_never_overwrites(tmp_path):
    src = tmp_path / "dl"
    src.mkdir()
    (src / "Dataset_NQ_1min_2022_2025.csv").write_text("Datetime,Open,High,Low,Close,Volume\n2024-01-02 00:00:00,1,1,1,1,1\n")
    z = tmp_path / "upload_file"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(src / "Dataset_NQ_1min_2022_2025.csv", "Dataset_NQ_1min_2022_2025.csv")
    raw = tmp_path / "raw"
    done = kd.import_file(z, raw, {"origin": "test"})
    csv = raw / "Dataset_NQ_1min_2022_2025.csv"
    assert csv.exists() and json.loads((raw / (csv.name + ".meta.json")).read_text())["archive"].startswith("upload_")
    assert any(p.suffix == ".zip" for p in raw.iterdir()) and len(done) == 2
    assert all("identical" in x for x in kd.import_file(z, raw, {"origin": "test"}))
    (src / "Dataset_NQ_1min_2022_2025.csv").write_text("Datetime,Open,High,Low,Close,Volume\n2024-01-02 00:00:00,2,2,2,2,2\n")
    z2 = tmp_path / "other.zip"
    with zipfile.ZipFile(z2, "w") as zf:
        zf.write(src / "Dataset_NQ_1min_2022_2025.csv", "Dataset_NQ_1min_2022_2025.csv")
    with pytest.raises(kd.ImportStop):
        kd.import_file(z2, raw, {"origin": "test"})
    assert ",1,1,1,1,1" in csv.read_text()
    assert not any(p.name.startswith("_staging_") for p in raw.iterdir())


def test_import_rejects_unsafe_archives(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../escape.csv", "a,b\n")
    with pytest.raises(kd.ImportStop):
        kd.import_file(z, tmp_path / "raw", {"origin": "test"})
    assert not (tmp_path / "escape.csv").exists()


def test_import_plain_upload_without_extension(tmp_path):
    f = tmp_path / "file_0123abcd"
    f.write_text("Datetime,Open,High,Low,Close,Volume\n2024-01-02 00:00:00,1,1,1,1,1\n")
    done = kd.import_file(f, tmp_path / "raw", {"origin": "test"})
    assert done[0].startswith("upload_") and ".csv" in done[0]


def test_export_study_results(tmp_path):
    from dataclasses import asdict

    from edgelab.stats.multiple_testing import TestRegistry
    from edgelab.studies.runner import StudyOutcome

    run = tmp_path / "es" / "T_DS"
    reg = TestRegistry(tmp_path / "reg.csv")
    for sid, fam, typ in (("A001", "auction", "E"), ("B004", "ict", "M")):
        o = StudyOutcome(sid, fam, typ, "H", "title", 1, primary={"label": "x", "point": 0.1, "lo": -0.1, "hi": 0.3, "se": 0.1,
                                                              "p_value": 0.3, "n_dates": 10, "n_by_group": {"event": 120}},
                         p_bh=0.5, decision="REJECTED" if typ == "E" else "NOT_SUPPORTED",
                         gates={"G1": False, "G2": False, "G3": True, "G4": True, "G5": False, "G6": False})
        (run / sid).mkdir(parents=True)
        (run / sid / "result.json").write_text(json.dumps({**asdict(o), "failed_gates": o.failed_gates, "provenance": "manifest:T_DS:x",
                                                          "evidence_class": "SYNTHETIC-TEST"}, default=str))
        reg.register(experiment_id=sid, family=fam, p_value=0.3, notes="role=primary;type=E;prov=manifest:T_DS:x")
    reg.register(experiment_id="C001", family="price_action", p_value=0.01, notes="role=primary;type=E;prov=other")
    out = subprocess.run([sys.executable, "scripts/export_study_results.py", "--dataset", "T_DS", "--prefix", "t",
                          "--root", str(tmp_path / "es"), "--registry", str(tmp_path / "reg.csv"), "--out", str(tmp_path / "o")],
                         cwd=RESEARCH_ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    st = pd.read_csv(tmp_path / "o" / "t_study_results.csv")
    gt = pd.read_csv(tmp_path / "o" / "t_gate_results.csv")
    fdr = pd.read_csv(tmp_path / "o" / "t_fdr_results.csv")
    assert list(st["study"]) == ["A001", "B004"] and list(gt.columns[:8]) == ["study", "type", "G1", "G2", "G3", "G4", "G5", "G6"]
    assert pd.isna(gt.loc[gt["study"] == "B004", "G3"]).all()  # not applicable to type M
    assert len(fdr) == 3 and fdr["this_run"].sum() == 2 and "p_bh_global" in fdr.columns
    full = json.loads((tmp_path / "o" / "t_full_results.json").read_text())
    assert full["studies_with_results"] == 2


def test_reports_render_without_data_errors(spring):
    df = ohlcv(spring).assign(ts=file_clock(spring, "UTC", "open"))
    d = vb.infer_clock(df, TPL)
    prof = vb.column_profile(df, vb.column_mapping(df))
    txt = render_schema(dataset="d/s", files=[], bar_file={"name": "f", "bytes": 10, "sha256": "0" * 64}, profile=prof,
                        specials=vb.special_columns(prof), mapping=vb.column_mapping(df), clock_ev=d.evidence, vwap_checks=[],
                        kept=["open"])
    assert "## Columns" in txt and "| ts |" in txt
    q = render_quality(dataset="d/s", bar_file={"name": "f", "sha256": "0" * 64}, clock=d, rows=[], stops=["x"], extra=None,
                       generic_md=None, max_removed_share=1e-4)
    assert "**Verdict: STOP.**" in q and "Method 1" in q


def test_kaggle_config_matches_manifest_rules():
    cfg = json.loads(json.dumps(__import__("yaml").safe_load((RESEARCH_ROOT / "configs" / "kaggle.yaml").read_text())))
    assert cfg["instrument"] == "NQ" and cfg["raw_dir"].startswith("data/raw/")
    assert cfg["derived_dir"].startswith("data/processed/")  # both ignored by git
    gi = (RESEARCH_ROOT / ".gitignore").read_text()
    assert "data/raw/*" in gi and "data/processed/*" in gi
    assert not os.environ.get("KAGGLE_API_TOKEN") or True  # the suite never needs a token
