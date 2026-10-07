"""Metrics, bootstrap, Monte Carlo, multiple testing, splits, guards, registry, journal."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from edgelab import journal
from edgelab.events.controls import time_matched_controls
from edgelab.events.study import event_study, first_passage, forward_outcomes
from edgelab.experiments import ExperimentSpec, load_registry, next_experiment_id, param_hash, save_run
from edgelab.metrics import max_drawdown, max_streaks, trade_metrics
from edgelab.stats.bootstrap import UNCERTAIN, bootstrap_ci, expectancy_verdict
from edgelab.stats.montecarlo import mc_bootstrap, mc_missing_trades, mc_reshuffle, risk_of_ruin
from edgelab.stats.multiple_testing import TestRegistry, benjamini_hochberg
from edgelab.stats.randomization import permutation_pvalue
from edgelab.validation.perturbation import grid, grid_neighbors, perturbations, plateau_table
from edgelab.validation.splits import SplitLedger, TestSetGuard, chronological_split, walk_forward_windows


def toy_trades(r, days=None):
    n = len(r)
    days = days or list(pd.bdate_range("2024-01-02", periods=n))
    return pd.DataFrame({"R_net": r, "trading_date": days[:n], "direction": [1, -1] * (n // 2) + [1] * (n % 2), "duration_min": 10.0})


def test_trade_metrics_known_values():
    r = [2.0, -1.0, -1.0, 3.0, -1.0]
    m = trade_metrics(toy_trades(r))
    assert m["trades"] == 5 and m["wins"] == 2 and m["win_rate"] == pytest.approx(0.4)
    assert m["expectancy_R"] == pytest.approx(0.4)
    assert m["profit_factor"] == pytest.approx(5 / 3)
    assert m["max_drawdown_R"] == pytest.approx(2.0)
    assert m["max_consec_losses"] == 2
    assert max_drawdown(np.cumsum(r))["max_dd"] == 2.0
    assert max_streaks(np.array(r)) == (1, 2)


def test_daily_sharpe_counts_days_without_trades():
    t = toy_trades([1.0, 1.0, -0.5])
    all_days = pd.bdate_range("2024-01-02", periods=30)
    m_all = trade_metrics(t, trading_days=all_days)
    m_traded = trade_metrics(t)
    assert m_all["trading_days"] == 30
    assert m_all["sharpe_daily_ann"] < m_traded["sharpe_daily_ann"]


def test_bootstrap_ci_and_verdict():
    rng = np.random.default_rng(0)
    x = rng.normal(0.02, 1.0, 400)
    ci = bootstrap_ci(x, "mean", n_boot=4000, seed=1)
    assert ci["lo"] < x.mean() < ci["hi"]
    assert expectancy_verdict(ci) == UNCERTAIN
    clustered = bootstrap_ci(x, "mean", n_boot=500, seed=1, cluster=np.repeat(np.arange(40), 10))
    assert clustered["hi"] - clustered["lo"] > 0
    pf = bootstrap_ci(x, "pf", n_boot=1000, seed=2)
    assert pf["lo"] < pf["point"] < pf["hi"]


def test_benjamini_hochberg():
    adj, rej = benjamini_hochberg(np.array([0.001, 0.2, 0.3]), q=0.05)
    assert np.allclose(adj, [0.003, 0.3, 0.3]) and rej.tolist() == [True, False, False]
    adj2, rej2 = benjamini_hochberg(np.array([0.01, 0.02, 0.03, 0.04, 0.05]), q=0.05)
    assert rej2.all()


def test_monte_carlo_and_ruin():
    rng = np.random.default_rng(3)
    r = rng.choice([2.0, -1.0], size=300, p=[0.4, 0.6])
    rs = mc_reshuffle(r, n_sims=500, seed=1)
    assert rs["max_dd_R"]["p95"] >= rs["max_dd_R"]["median"] > 0
    bs = mc_bootstrap(r, n_sims=500, seed=1)
    assert bs["final_R"]["p5"] < bs["final_R"]["median"]
    miss = mc_missing_trades(r, 0.2, n_sims=500, seed=1)
    assert abs(miss["expectancy_R"]["median"] - r.mean()) < 0.1
    lo = risk_of_ruin(r, 0.0025, 250, n_sims=500, seed=1)
    hi = risk_of_ruin(r, 0.02, 250, n_sims=500, seed=1)
    assert hi["p_dd_ge_20pct"] >= lo["p_dd_ge_20pct"]


def test_permutation_pvalue():
    null = np.linspace(-1, 1, 99)
    assert permutation_pvalue(2.0, null) == pytest.approx(1 / 100)
    assert permutation_pvalue(-2.0, null) == pytest.approx(1.0)


def test_chronological_split_with_embargo():
    days = pd.bdate_range("2020-01-01", periods=200)
    s = chronological_split(days, embargo_days=5)
    assert s["DEV"][0] == str(days[0].date()) and s["DEV"][1] == str(days[99].date())
    assert s["VAL"][0] == str(days[105].date()) and s["TEST"][0] == str(days[155].date())
    assert s["TEST"][1] == str(days[-1].date())


def test_split_ledger_freezes(tmp_path):
    led = SplitLedger(tmp_path / "split.json")
    s = {"DEV": ("2020-01-01", "2020-06-30"), "VAL": ("2020-07-08", "2020-09-30"), "TEST": ("2020-10-08", "2020-12-31")}
    led.register("ES", "abc" * 10, s)
    with pytest.raises(RuntimeError):
        led.register("ES", "abc" * 10, {**s, "TEST": ("2020-11-01", "2020-12-31")})


def test_test_set_guard_one_shot(tmp_path):
    g = TestSetGuard(tmp_path / "guard.json")
    with pytest.raises(PermissionError):
        g.request(candidate="A_val_rotation", experiment_id="A010", status="VALIDATION", params_hash="x", instrument="ES")
    g.request(candidate="A_val_rotation", experiment_id="A010", status="FROZEN", params_hash="x", instrument="ES")
    with pytest.raises(PermissionError):
        g.request(candidate="A_val_rotation", experiment_id="A011", status="FROZEN", params_hash="y", instrument="ES")


def test_walk_forward_windows():
    days = pd.bdate_range("2020-01-01", "2022-12-31")
    w = walk_forward_windows(days, 12, 3)
    assert w[0]["train_start"] == pd.Timestamp("2020-01-01") and w[0]["test_start"] == pd.Timestamp("2021-01-01")
    assert all(x["test_start"] > x["train_end"] for x in w)
    assert len(w) == 8


def test_grid_neighbors_and_perturbations():
    space = {"a": [1, 2, 3], "b": [0.1, 0.2]}
    g = grid(space)
    nb = grid_neighbors(space)
    assert len(g) == 6 and sorted(nb(0)) == [1, 2]
    res = pd.DataFrame(g)
    res["score"] = [0, 0, 0, 5, 0, 0]
    pt = plateau_table(res, space, "score")
    assert pt["score_isolation"].iloc[3] > 0
    ps = perturbations({"a": 10, "b": 0.5}, ["a"], integer_keys=("a",))
    assert [p[2]["a"] for p in ps] == [8, 9, 11, 12]


def test_registry_and_run_dir(tmp_path):
    spec = ExperimentSpec("A001", "test_candidate", "v0", "ES", "5m", {"x": 1}, "ES:BASE:slip1x", notes="unit test")
    trades = toy_trades([1.0, -1.0, 0.5])
    trades["exit_ts"] = pd.date_range("2024-01-02", periods=3, tz="America/New_York")
    d = save_run(spec, trades, trade_metrics(trades), results_root=tmp_path)
    for f in ("config.yaml", "metrics.json", "trades.parquet", "equity.csv", "notes.md"):
        assert (d / f).exists()
    reg = load_registry(tmp_path)
    assert reg["experiment_id"].tolist() == ["A001"] and reg["parameter_hash"].iloc[0] == param_hash({"x": 1})
    assert next_experiment_id("A", tmp_path) == "A002"
    assert json.loads((d / "metrics.json").read_text())["trades"] == 3


def test_journal_requires_preregistration(tmp_path):
    p = tmp_path / "J.md"
    with pytest.raises(PermissionError):
        journal.record_result("A001", actual="x", interpretation="y", decision="KEEP", path=p)
    journal.preregister("A001", "t", hypothesis="h", reason="r", rules="ru", parameters="p", dataset="d", expected="e", path=p)
    with pytest.raises(ValueError):
        journal.preregister("A001", "t", hypothesis="h", reason="r", rules="ru", parameters="p", dataset="d", expected="e", path=p)
    journal.record_result("A001", actual="x", interpretation="y", decision="REJECT", path=p)
    text = p.read_text()
    assert text.index("Expected outcome BEFORE running") < text.index("Actual result")
    assert journal.entries(p) == ["A001"]


def test_test_registry_bh(tmp_path):
    reg = TestRegistry(tmp_path / "t.csv")
    for p, fam in ((0.001, "auction"), (0.04, "auction"), (0.5, "ict")):
        reg.register(experiment_id="A001", family=fam, metric="mean", p_value=p)
    adj = reg.adjusted(q=0.10)
    assert adj["n_tests_total"].iloc[0] == 3 and bool(adj["sig_bh_global"].iloc[0])


def test_event_study_and_first_passage(week_bars, week_labels):
    from edgelab.features.volatility import atr

    a = atr(week_bars, 14)
    rth = np.flatnonzero(week_labels["is_rth"].to_numpy())
    ev = pd.DataFrame({"pos": rth[100:2000:97], "direction": 1})
    ctl = time_matched_controls(week_labels, ev, n_per_event=3, eligible=week_labels["is_rth"].to_numpy(), seed=1)
    assert len(ctl) == 3 * len(ev)
    assert (week_labels["minute_of_day"].to_numpy()[ctl["pos"]] == np.repeat(week_labels["minute_of_day"].to_numpy()[ev["pos"]], 3)).all()
    sid = pd.factorize(week_labels["trading_date"])[0]
    res = event_study("unit", week_bars, ev, a, horizons=(1, 5), session_id=sid, controls=ctl, cluster=sid, n_boot=200)
    assert {"ev_mean", "ct_mean", "diff_lo", "diff_hi"}.issubset(res.table.columns)
    fo = forward_outcomes(week_bars, ev["pos"].to_numpy(), ev["direction"].to_numpy(), a.to_numpy()[ev["pos"]], (5,), sid)
    p0 = ev["pos"].iloc[0]
    o = week_bars["open"].to_numpy()
    c = week_bars["close"].to_numpy()
    assert fo["ret_5"].iloc[0] == pytest.approx(c[p0 + 5] - o[p0 + 1])
    fp = first_passage(week_bars, ev["pos"].to_numpy(), ev["direction"].to_numpy(), o[ev["pos"] + 1] + 2.0, o[ev["pos"] + 1] - 2.0, 60, sid)
    assert set(np.unique(fp["outcome"])).issubset({-1, 0, 1})
