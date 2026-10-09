"""End-to-end smoke test: vendor file -> prepared bars -> quality report -> features ->
event study -> signals -> execution -> metrics -> bootstrap -> registry + journal.

SYNTHETIC DATA: this proves the software chain works. The numbers mean nothing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

from conftest import cme_schedule, random_walk_bars
from edgelab import journal
from edgelab.config import load_cost_model, load_instrument, load_session_template
from edgelab.data.manifest import load_manifest, load_processed, prepare
from edgelab.data.quality import check_bars, render_markdown
from edgelab.events.controls import time_matched_controls
from edgelab.events.study import event_study
from edgelab.execution.engine import ExecConfig, simulate
from edgelab.experiments import ExperimentSpec, load_registry, save_run
from edgelab.features.levels import session_levels
from edgelab.features.sr import level_touches
from edgelab.features.volatility import atr
from edgelab.metrics import trade_metrics
from edgelab.resample import resample_bars
from edgelab.sessions import label_sessions
from edgelab.stats.bootstrap import bootstrap_ci

TPL = load_session_template("cme_equity_index")
ES = load_instrument("ES")


def _vendor_file(tmp_path, bars):
    """NinjaTrader-style export: close-labelled, Chicago clock, semicolons, one repeated row."""
    close_ts = (bars.index + pd.Timedelta(minutes=1)).tz_convert("America/Chicago").tz_localize(None)
    raw = pd.DataFrame({
        "Time": close_ts.strftime("%Y%m%d %H%M%S"),
        "Open": bars["open"].to_numpy(), "High": bars["high"].to_numpy(),
        "Low": bars["low"].to_numpy(), "Close": bars["close"].to_numpy(), "Volume": bars["volume"].to_numpy(),
    })
    raw = pd.concat([raw.iloc[:500], raw.iloc[[499]], raw.iloc[500:]], ignore_index=True)
    path = tmp_path / "ES_export.txt"
    raw.to_csv(path, sep=";", index=False)
    return path


def test_end_to_end_pipeline(tmp_path):
    truth = random_walk_bars(cme_schedule("2024-03-04", 15), seed=11)
    raw_path = _vendor_file(tmp_path, truth)
    manifest = tmp_path / "datasets.yaml"
    manifest.write_text(yaml.safe_dump({"datasets": [{
        "id": "ES_test", "instrument": "ES", "path": str(raw_path), "source": "synthetic unit-test file",
        "tz_in": "America/Chicago", "label": "close", "bar_minutes": 1, "adjustment": "unadjusted",
        "column_map": {"Time": "ts"}, "ts_format": "%Y%m%d %H%M%S",
    }]}))

    # prepare: close labels shifted to open times, NY timezone, duplicate dropped and counted
    (entry,) = load_manifest(manifest)
    prepare(entry, out_dir=tmp_path / "processed")
    bars, meta = load_processed("ES_test", out_dir=tmp_path / "processed")
    assert meta["identical_duplicates_dropped"] == 1
    assert bars.index.equals(truth.index)
    assert np.allclose(bars["close"].to_numpy(), truth["close"].to_numpy())

    # quality report
    rep = check_bars(bars, instrument="ES", source="test", template=TPL, bar_minutes=1, tick_size=ES.tick_size, roll_cfg=ES.roll, meta=meta)
    assert rep["duplicate_timestamps"] == 0 and sum(rep["invalid_ohlc"].values()) == 0
    assert rep["tick_grid_conformity_pct"] == 100.0
    assert "## ES" in render_markdown([rep])

    # features on 5-minute bars, event = confirmed touch of the prior RTH low
    lab1 = label_sessions(bars.index, TPL)
    b5 = resample_bars(bars, 5)
    lab5 = label_sessions(b5.index, TPL)
    a5 = atr(b5, 14)
    pdl = session_levels(b5, lab5, 5, TPL, phase="RTH", which="previous")["pd_rth_low"].to_numpy()
    touches = level_touches(b5, pdl, a5, side="support")
    ev = touches[touches["event"] == "touch"]
    assert len(ev) > 0
    events = pd.DataFrame({"pos": ev["confirm_pos"].to_numpy(), "direction": 1})
    sid = pd.factorize(lab5["trading_date"])[0]
    ctl = time_matched_controls(lab5, events, n_per_event=3, bar_minutes=5, seed=1)
    res = event_study("pdl_touch", b5, events, a5, horizons=(1, 3, 6), session_id=sid, controls=ctl, cluster=sid, n_boot=200)
    assert len(res.table) == 3 and "diff_mean" in res.table

    # signals at the 5m close, executed on 1m bars
    p = events["pos"].to_numpy()
    lvl = ev["level"].to_numpy()
    stop = np.floor((lvl - 0.25 * a5.to_numpy()[p]) / ES.tick_size) * ES.tick_size
    ref = b5["close"].to_numpy()[p]
    target = np.ceil((ref + 2 * (ref - stop)) / ES.tick_size) * ES.tick_size
    sig = pd.DataFrame({"decision_ts": b5.index[p] + pd.Timedelta(minutes=5), "direction": 1, "entry_type": "market",
                        "stop_price": stop, "target_price": target})
    sim = simulate(bars, lab1, sig, ES, load_cost_model("ES", "BASE"), ExecConfig(flat_time="15:55"))
    trades = sim.trades
    assert len(trades) > 0 and len(trades) + len(sim.cancelled) == len(sig)
    assert (trades["entry_ts"] >= trades["decision_ts"]).all()
    m = trade_metrics(trades)
    ci = bootstrap_ci(trades["R_net"].to_numpy(), "mean", n_boot=500, seed=3)
    assert m["trades"] == len(trades) and np.isfinite(ci["point"])

    # registry and journal: expectation first, then result
    jp = tmp_path / "J.md"
    journal.preregister("A999", "smoke", hypothesis="h", reason="r", rules="x", parameters="p", dataset="ES_test", expected="nothing", path=jp)
    spec = ExperimentSpec("A999", "smoke_pdl_touch", "V0", "ES", "5m", {"stop_atr": 0.25, "target_R": 2}, "ES:BASE:slip1x", data_sha256=meta["processed_sha256"])
    save_run(spec, trades, m, results_root=tmp_path / "results")
    journal.record_result("A999", actual=f"{m['trades']} trades", interpretation="software check", decision="REJECT", path=jp)
    reg = load_registry(tmp_path / "results")
    assert reg["experiment_id"].tolist() == ["A999"] and reg["evidence_class"].iloc[0] == "CME"
