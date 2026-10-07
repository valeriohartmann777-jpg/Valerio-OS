"""Execution engine: fills, stops, targets, ambiguity, costs, timing (synthetic bars)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import bars_from_rows
from edgelab.config import load_cost_model, load_instrument, load_session_template
from edgelab.execution.engine import ExecConfig, simulate
from edgelab.execution.sizing import account_summary, fixed_fractional
from edgelab.sessions import label_sessions

TZ = "America/New_York"
ES = load_instrument("ES")
BASE = load_cost_model("ES", "BASE")
TPL = load_session_template("cme_equity_index")


def run(rows, signals, start="2024-03-05 10:00", cfg=ExecConfig(), cost=BASE):
    b = bars_from_rows(rows, start=start)
    lab = label_sessions(b.index, TPL)
    sig = pd.DataFrame(signals)
    sig["decision_ts"] = [b.index[k] + pd.Timedelta(minutes=1) for k in sig.pop("bar")]
    return simulate(b, lab, sig, ES, cost, cfg), b


def sig(bar, d, etype="market", stop=np.nan, target=np.nan, entry=np.nan, **kw):
    return {"bar": bar, "direction": d, "entry_type": etype, "stop_price": stop, "target_price": target, "entry_price": entry, **kw}


def test_market_entry_is_next_bar_open_and_costs_are_exact():
    rows = [(100, 101, 99, 100), (100.25, 100.5, 98.5, 99), (99, 99, 98, 98.5)]
    res, b = run(rows, [sig(0, 1, stop=99.0)])
    t = res.trades.iloc[0]
    assert t["entry_ts"] == b.index[1] and t["entry_raw"] == 100.25
    assert t["entry_price"] == 100.5                     # +1 tick slippage
    assert t["exit_reason"] == "stop" and t["exit_raw"] == 99.0 and t["exit_price"] == 98.75
    assert t["R_gross"] == pytest.approx(-1.0)
    fees = 4.50 / 50
    assert t["fees_pts"] == pytest.approx(fees)
    assert t["net_pts"] == pytest.approx(-1.25 - 0.5 - fees)
    assert t["R_net"] == pytest.approx((-1.25 - 0.5 - fees) / 1.25)


def test_same_bar_stop_and_target_is_conservative_by_default():
    rows = [(100, 100, 100, 100), (100, 101.5, 98.5, 100), (100, 100, 100, 100)]
    s = [sig(0, 1, stop=99.0, target=101.0)]
    cons, _ = run(rows, s)
    opt, _ = run(rows, s, cfg=ExecConfig(ambiguity="optimistic"))
    assert cons.trades.iloc[0]["exit_reason"] == "stop" and bool(cons.trades.iloc[0]["ambiguous"])
    assert opt.trades.iloc[0]["exit_reason"] == "target"


def test_limit_needs_trade_through_and_gap_fills_at_open():
    rows = [(100, 100, 100, 100), (100, 100.25, 99.5, 99.75), (99.75, 100, 99.25, 99.5), (99.5, 99.5, 99.5, 99.5)]
    res, b = run(rows, [sig(0, 1, "limit", stop=98.0, entry=99.5, ttl_bars=5)])
    t = res.trades.iloc[0]
    assert t["entry_ts"] == b.index[2] and t["entry_raw"] == 99.5 and t["entry_price"] == 99.5  # no slippage on limit
    gap_rows = [(100, 100, 100, 100), (99.0, 99.25, 98.75, 99.0), (99, 99, 99, 99)]
    res2, _ = run(gap_rows, [sig(0, 1, "limit", stop=97.0, entry=99.5)])
    assert res2.trades.iloc[0]["entry_raw"] == 99.0


def test_limit_fill_bar_target_not_credited_when_conservative():
    rows = [(100, 100, 100, 100), (100, 100.75, 99.25, 100), (100, 100.25, 99.75, 100), (100, 100, 100, 100)]
    s = [sig(0, 1, "limit", stop=98.0, entry=99.5, target=100.5, ttl_bars=3)]
    cons, _ = run(rows, s)
    opt, _ = run(rows, s, cfg=ExecConfig(ambiguity="optimistic"))
    assert cons.trades.iloc[0]["exit_reason"] == "session_end"
    assert opt.trades.iloc[0]["exit_reason"] == "target"


def test_stop_entry_gap_and_invalidation_before_entry():
    rows = [(100, 100, 100, 100), (101.5, 102, 101.25, 101.75), (101.75, 101.75, 101.75, 101.75)]
    res, _ = run(rows, [sig(0, 1, "stop", stop=99.0, entry=101.0)])
    t = res.trades.iloc[0]
    assert t["entry_raw"] == 101.5 and t["entry_price"] == 101.75
    rows2 = [(100, 100, 100, 100), (100, 100.5, 98.75, 99), (99, 101.5, 99, 101.25)]
    res2, _ = run(rows2, [sig(0, 1, "stop", stop=99.0, entry=101.0)])
    assert res2.trades.empty and res2.cancelled.iloc[0]["reason"] == "invalidated_before_entry"


def test_target_before_limit_fill_cancels():
    rows = [(100, 100, 100, 100), (100, 101.5, 99.75, 101), (101, 101, 99, 99)]
    res, _ = run(rows, [sig(0, 1, "limit", stop=98.0, entry=99.5, target=101.0)])
    assert res.trades.empty and res.cancelled.iloc[0]["reason"] == "target_before_entry"


def test_flat_time_exit_and_no_entries_after_it():
    rows = [(100, 100.25, 99.75, 100)] * 10
    res, b = run(rows, [sig(1, 1, stop=99.0), sig(6, 1, stop=99.0)], start="2024-03-05 15:50", cfg=ExecConfig(flat_time="15:55"))
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "flat_time" and t["exit_ts"] == pd.Timestamp("2024-03-05 15:55", tz=TZ)
    assert "after_flat_time" in res.cancelled["reason"].tolist()


def test_session_end_exit_and_boundary():
    idx = list(pd.date_range("2024-03-05 16:55", periods=5, freq="1min", tz=TZ)) + list(pd.date_range("2024-03-05 18:00", periods=3, freq="1min", tz=TZ))
    b = pd.DataFrame({"open": 100.0, "high": 100.25, "low": 99.75, "close": 100.0, "volume": 10.0}, index=pd.DatetimeIndex(idx, name="ts"))
    lab = label_sessions(b.index, TPL)
    s = pd.DataFrame([sig(1, 1, stop=99.0), sig(4, 1, stop=99.0)])
    s["decision_ts"] = [b.index[k] + pd.Timedelta(minutes=1) for k in s.pop("bar")]
    res = simulate(b, lab, s, ES, BASE, ExecConfig())
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "session_end" and t["exit_ts"] == b.index[4] and t["exit_raw"] == 100.0
    assert res.cancelled.iloc[0]["reason"] in ("session_boundary", "overlap")


def test_overlap_and_min_risk():
    rows = [(100, 100.25, 99.75, 100)] * 6
    res, _ = run(rows, [sig(0, 1, stop=99.0), sig(1, 1, stop=99.0), sig(2, -1, stop=100.5)])
    assert len(res.trades) == 1 and set(res.cancelled["reason"]) == {"overlap"}
    res2, _ = run(rows, [sig(0, 1, stop=99.75)])  # 2 ticks < 4 tick minimum
    assert res2.cancelled.iloc[0]["reason"] == "risk_too_small"


def test_short_target_and_entry_delay():
    rows = [(100, 100, 100, 100), (100, 100.25, 99.5, 99.75), (99.75, 99.75, 98.0, 98.25), (98.25, 98.5, 98, 98.25)]
    res, b = run(rows, [sig(0, -1, stop=101.0, target=98.5)])
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "target" and t["R_gross"] > 0 and t["direction"] == -1
    res2, b2 = run(rows, [sig(0, -1, stop=101.0, target=98.5)], cfg=ExecConfig(entry_delay_bars=1))
    assert res2.trades.iloc[0]["entry_ts"] == b2.index[2]


def test_eth_slippage_multiplier():
    rows = [(100, 100.25, 99.75, 100)] * 4
    res, _ = run(rows, [sig(0, 1, stop=99.0)], start="2024-03-05 20:00")
    t = res.trades.iloc[0]
    assert t["entry_price"] - t["entry_raw"] == pytest.approx(0.25 * BASE.eth_slippage_mult)
    stress = BASE.with_slippage_mult(2.0)
    res2, _ = run(rows, [sig(0, 1, stop=99.0)], cost=stress)
    assert res2.trades.iloc[0]["entry_price"] - res2.trades.iloc[0]["entry_raw"] == pytest.approx(0.25 * 2.0)


def test_fixed_fractional_integer_contracts_skip_wide_stops():
    trades = pd.DataFrame({"exit_ts": [1, 2], "R_net": [1.0, -1.0], "risk_pts": [4.0, 40.0], "net_pts": [4.0, -40.0]})
    path = fixed_fractional(trades, 0.005, 100_000, inst=ES, integer_contracts=True)
    assert path["contracts"].tolist() == [2, 0] and path["skipped"].tolist() == [False, True]
    s = account_summary(path, 100_000, years=1.0)
    assert s["final_equity"] == pytest.approx(100_000 + 2 * 4 * 50)
