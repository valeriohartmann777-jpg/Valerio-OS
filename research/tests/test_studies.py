"""Software tests of the pre-registered event-study machinery.

SYNTHETIC DATA: every price series here is a random walk built for SOFTWARE TESTS ONLY.
The numbers mean nothing about any market and are never evidence. The provenance guard
tested below is what keeps such numbers out of the repository's results.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import pytest

from conftest import cme_schedule, random_walk_bars
from edgelab import journal
from edgelab.config import RESEARCH_ROOT, load_instrument, load_research_config, load_session_template
from edgelab.features.sr import pivot_zones
from edgelab.sessions import label_sessions
from edgelab.stats.bootstrap import date_bootstrap
from edgelab.studies import runner
from edgelab.studies.catalog import HORIZONS, STUDIES, concat
from edgelab.studies.context import DEFAULTS, StudyContext
from edgelab.studies.controls import partner_days, shifted_draws, time_matched
from edgelab.studies.detectors import breakouts, sweep_reclaim, zone_entries
from edgelab.studies.inference import TestInput, evaluate, half_split, point_estimate
from edgelab.studies.runner import StudyOutcome, apply_gates, decide, load_config, ranking_score, select_candidates

TPL = load_session_template("cme_equity_index")
ES = load_instrument("ES")
N_DAYS = 60


def make_ctx(bars: pd.DataFrame, cfg: dict) -> StudyContext:
    return StudyContext(bars, ES, TPL, "synthetic-test", params=runner.context_params(cfg))


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def bars():
    return random_walk_bars(cme_schedule("2024-01-02", N_DAYS), seed=21)


@pytest.fixture(scope="module")
def ctx(bars, cfg):
    return make_ctx(bars, cfg)


@pytest.fixture(scope="module")
def catalog_run(ctx, cfg):
    return {sid: STUDIES[sid](ctx, runner.study_spec(cfg, sid)) for sid in cfg["studies"]}


# ---------------------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------------------
def test_config_matches_catalog_defaults_and_docs(cfg):
    assert set(cfg["studies"]) == set(STUDIES)
    assert runner.context_params(cfg) == DEFAULTS  # the YAML is binding; code defaults may not drift
    assert list(HORIZONS) == load_research_config()["event_study"]["horizons_bars"]
    docs = "".join((RESEARCH_ROOT / f).read_text(encoding="utf-8") for f in runner.HYPOTHESIS_FILES.values())
    for sid, s in cfg["studies"].items():
        assert s["type"] in runner.TYPE_GATES
        assert re.search(rf"^## {s['hypothesis']} ", docs, flags=re.M), f"{sid}: {s['hypothesis']} not in the hypothesis files"
        assert s["twin"] is None or isinstance(s["twin"], str)
        assert str(s["expected"]).strip()


# ---------------------------------------------------------------------------------------
# inference
# ---------------------------------------------------------------------------------------
def _clustered(seed: int = 0, n_dates: int = 80):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_dates).to_numpy()
    rows_d, rows_v, rows_g = [], [], []
    for d in dates:
        shock = rng.normal(0, 1.0)  # common daily shock: rows of a date are dependent
        for g, n, mu in (("ev", rng.integers(1, 4), 0.3), ("ct", rng.integers(3, 9), 0.0)):
            rows_d += [d] * n
            rows_v += list(mu + shock + rng.normal(0, 0.5, n))
            rows_g += [g] * n
    return np.array(rows_d), np.array(rows_v), np.array(rows_g)


def test_date_bootstrap_matches_naive_cluster_resampling():
    d, v, g = _clustered()
    ti = TestInput("diff", d, v, g)
    res = evaluate(ti, n_boot=4000, seed=1)
    assert res["point"] == pytest.approx(v[g == "ev"].mean() - v[g == "ct"].mean())
    rng = np.random.default_rng(2)
    uniq = np.unique(d)
    by = {x: np.flatnonzero(d == x) for x in uniq}
    naive = []
    for _ in range(2000):
        idx = np.concatenate([by[x] for x in rng.choice(uniq, size=len(uniq))])
        naive.append(v[idx][g[idx] == "ev"].mean() - v[idx][g[idx] == "ct"].mean())
    assert res["se"] == pytest.approx(np.std(naive, ddof=1), rel=0.12)
    flipped = evaluate(TestInput("diff", d, v, g, sign=-1.0), n_boot=4000, seed=1)
    assert flipped["point"] == pytest.approx(-res["point"])
    assert flipped["lo"] == pytest.approx(-res["hi"]) and flipped["hi"] == pytest.approx(-res["lo"])


def test_date_bootstrap_p_value_is_calibrated_under_the_null():
    rng = np.random.default_rng(5)
    pvals = []
    for rep in range(60):
        dates = np.repeat(pd.bdate_range("2023-01-02", periods=60).to_numpy(), 4)
        v = rng.normal(size=len(dates)) + np.repeat(rng.normal(size=60), 4)
        g = np.tile(["ev", "ct", "ct", "ev"], 60)
        pvals.append(evaluate(TestInput("diff", dates, v, g), n_boot=400, seed=rep)["p_value"])
    pvals = np.array(pvals)
    assert 0.0 <= (pvals < 0.05).mean() <= 0.15 and 0.3 < np.median(pvals) < 0.7


def test_point_estimates_of_every_kind():
    rng = np.random.default_rng(3)
    n = 400
    d = np.repeat(pd.bdate_range("2023-01-02", periods=100).to_numpy(), 4)
    y, x1, x2 = rng.normal(size=n), rng.normal(size=n), rng.normal(size=n)
    s = rng.choice(["0", "1", "2"], size=n)
    g4 = rng.choice(["a1", "b1", "a2", "b2"], size=n)
    g2 = rng.choice(["a", "b"], size=n)
    m = lambda grp, val=y: val[g4 == grp].mean()  # noqa: E731
    assert point_estimate(TestInput("dod", d, y, g4, ("a1", "b1", "a2", "b2"))) == pytest.approx((m("a1") - m("b1")) - (m("a2") - m("b2")))
    num = den = 0.0
    for st in "012":
        a, b = y[(s == st) & (g2 == "a")], y[(s == st) & (g2 == "b")]
        w = len(a) * len(b) / (len(a) + len(b))
        num, den = num + w * (a.mean() - b.mean()), den + w
    assert point_estimate(TestInput("strat_diff", d, y, g2, ("a", "b"), stratum=s)) == pytest.approx(num / den)
    sxy = sxx = 0.0
    for st in "012":
        k = s == st
        sxy += np.sum((x1[k] - x1[k].mean()) * (y[k] - y[k].mean()))
        sxx += np.sum((x1[k] - x1[k].mean()) ** 2)
    assert point_estimate(TestInput("slope", d, y, np.full(n, "all"), ("all",), stratum=s, x=x1)) == pytest.approx(sxy / sxx)
    coef = np.linalg.lstsq(np.column_stack([np.ones(n), x1, x2]), y, rcond=None)[0][1]
    assert point_estimate(TestInput("ols", d, y, np.full(n, "all"), ("all",), x=x1, x2=x2)) == pytest.approx(coef)
    med = np.median(y[g2 == "a"]) - np.median(y[g2 == "b"])
    assert point_estimate(TestInput("median_diff", d, y, g2, ("a", "b"))) == pytest.approx(med)
    assert np.isnan(point_estimate(TestInput("diff", d, y, np.full(n, "ev"))))  # empty control group -> nan, no exception


def test_half_split_and_date_bootstrap_moments():
    d, v, g = _clustered(seed=4)
    ti = TestInput("diff", d, v, g)
    h1, h2 = half_split(ti)
    uniq = np.sort(np.unique(d))
    first = d < uniq[len(uniq) // 2]
    assert h1 == pytest.approx(point_estimate(ti.subset(first)))
    assert h2 == pytest.approx(point_estimate(ti.subset(~first)))
    r = date_bootstrap(d, {"s": v, "n": np.ones(len(v))}, lambda T: T["s"] / T["n"], n_boot=500, seed=0)
    assert r["point"] == pytest.approx(v.mean()) and r["n_dates"] == len(uniq)


# ---------------------------------------------------------------------------------------
# detectors: definitions hold on every emitted event
# ---------------------------------------------------------------------------------------
def test_sweep_reclaim_events_satisfy_their_definition(ctx):
    lv = ctx.per_bar(ctx.prev(ctx.rth_high))
    for mode in ("pen", "wick", "close"):
        ev = sweep_reclaim(ctx, lv, +1, mode=mode, pen_atr=0.10, n_bars=3, close_thr_atr=0.0, window=(585, 900))
        for r in ev.itertuples(index=False):
            t0, p, L = int(r.t0), int(r.pos), float(r.level)
            assert r.direction == -1 and 585 <= ctx.close_min5[p] <= 900
            assert ctx.c[p] < L  # closed back inside
            if mode in ("pen", "wick"):
                assert ctx.h[t0] - L > 0.10 * ctx.atr5[t0] and 0 <= p - t0 <= 3
            else:
                assert ctx.c[t0] > L and 1 <= p - t0 <= 3
            if mode == "wick":
                assert p == t0
            assert L > ctx.rth_open[int(r.day)]  # near side of the open
        assert not ev.duplicated(["day", "level"]).any()  # one trigger per level value and session


def test_zone_entries_and_breakouts_satisfy_their_definition(ctx):
    lo = ctx.per_bar(ctx.prev(ctx.rth_low))
    z = zone_entries(ctx, lo, -1, zone_atr=0.10, break_atr=0.25, rearm_atr=0.50, window=(585, 900))
    for r in z.itertuples(index=False):
        p, L, A = int(r.pos), float(r.level), ctx.atr5[int(r.pos)]
        assert r.direction == +1 and ctx.l[p] <= L + 0.10 * A and ctx.c[p] - L >= -0.25 * A
    hi = ctx.per_bar(ctx.prev(ctx.rth_high))
    b = breakouts(ctx, hi, +1, variant="atr", window=(585, 900))
    for r in b.itertuples(index=False):
        p = int(r.pos)
        assert r.direction == +1 and ctx.c[p] - r.level > 0.25 * ctx.atr5[p]
    assert not b.duplicated(["day", "level"]).any()


def _perturbed_after(bars: pd.DataFrame, cut: pd.Timestamp, seed: int = 99) -> pd.DataFrame:
    """Same history up to ``cut``, a different random-walk future after it."""
    keep = bars[bars.index < cut]
    fut = random_walk_bars(bars.index[bars.index >= cut], seed=seed, start=float(keep["close"].iloc[-1]))
    return pd.concat([keep, fut])


CAUSAL_STUDIES = ("A001", "A002", "A003", "A005", "A006", "A009", "A010", "A011", "A012", "B002", "B003", "B004", "B005",
                  "B006", "B008", "B010", "B011", "B013", "B014", "C001", "C004", "C005", "C006", "C007", "C008", "C009",
                  "C010", "C012", "C013", "C014", "C015", "C018")


def test_study_events_do_not_depend_on_the_future(bars, ctx, cfg, catalog_run):
    days = ctx.days
    cut = pd.Timestamp(days[45]).tz_localize(TPL.timezone) + pd.Timedelta(hours=12)
    ctx_b = make_ctx(_perturbed_after(bars, cut), cfg)
    assert ctx_b.b5.index[: ctx.n5].equals(ctx.b5.index)  # same bar schedule
    checked = 0
    for sid in CAUSAL_STUDIES:
        ev_a = catalog_run[sid].events
        ev_b = STUDIES[sid](ctx_b, runner.study_spec(cfg, sid)).events

        def known(ev, c):
            if not len(ev):
                return set()
            t = c.b5.index[ev["pos"].to_numpy(np.int64)]
            m = np.asarray(t + pd.Timedelta(minutes=10) <= cut)  # decision bar and entry bar both before the cut
            lvl = ev["level"].to_numpy(float) if "level" in ev.columns else np.zeros(len(ev))
            return set(zip(t[m], ev["direction"].to_numpy()[m], np.round(lvl[m], 6)))

        a, b = known(ev_a, ctx), known(ev_b, ctx_b)
        assert a == b, f"{sid}: events before the cut changed when only the future changed"
        checked += len(a)
    assert checked > 50  # the test actually compared events


# ---------------------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------------------
def test_partner_days_and_scaled_shift(ctx):
    pdh = ctx.per_bar(ctx.prev(ctx.rth_high))
    partners = partner_days(ctx, ctx.rth_open, n_draws=5, seed=3)
    ok = ctx.ok_days()
    assert (partners[:, ok] != ok[None, :]).all() and (partners[:, ok] >= 0).all()
    draws = shifted_draws(ctx, {"pdh": pdh}, ctx.rth_open, n_draws=5, seed=3)
    rth = np.flatnonzero((ctx.day5 >= 0) & np.isfinite(pdh))
    for r, dr in enumerate(draws):
        for i in rth[::37]:
            k = ctx.day5[i]
            kp = partners[r, k]
            if kp < 0:
                assert np.isnan(dr["pdh"][i])
                continue
            j = ctx.slot_pos(int(ctx.slot5[i]))[kp]
            if j < 0:
                continue
            got = (dr["pdh"][i] - ctx.rth_open[k]) / ctx.atr_d[k]
            want = (pdh[j] - ctx.rth_open[kp]) / ctx.atr_d[kp]
            assert got == pytest.approx(want)


def test_time_matched_controls_same_minute_other_dates(ctx, catalog_run):
    ev = catalog_run["C004"].events[["pos", "direction"]]
    ct = time_matched(ctx, ev, n=5, seed=1)
    e = ev.iloc[ct["event_idx"].to_numpy()]
    assert (ctx.mod5[ct["pos"].to_numpy()] == ctx.mod5[e["pos"].to_numpy()]).all()
    assert (ctx.day5[ct["pos"].to_numpy()] != ctx.day5[e["pos"].to_numpy()]).all()
    assert (ct["direction"].to_numpy() == e["direction"].to_numpy()).all()
    assert ctx.elig5[ct["pos"].to_numpy()].all()


def test_displaced_zone_set(ctx):
    st = ctx.pivot_zone_state
    zero, _ = pivot_zones(ctx.b5, ctx.pivots_pa, ctx.atr5_series, merge_tol_atr=0.25, max_age_bars=1380,
                          query_offsets=np.zeros((1, ctx.n5)))
    pd.testing.assert_series_equal(zero["res_center_q0"], zero["res_center"], check_names=False)
    pd.testing.assert_series_equal(zero["sup_center_q0"], zero["sup_center"], check_names=False)
    c = ctx.c
    for r in range(int(ctx.p("pz_shift_draws"))):
        res, sup = st[f"res_center_q{r}"].to_numpy(), st[f"sup_center_q{r}"].to_numpy()
        f = np.isfinite(res)
        assert (res[f] > c[f]).all()
        f = np.isfinite(sup)
        assert (sup[f] <= c[f] + 1e-9).all()
        lv = ctx.pz_shift_levels[r]
        assert np.isnan(lv["res"][ctx.day5 < 0]).all()  # RTH only, lagged like pz_levels
    off = ctx.pz_shift_offsets
    code = ctx.trading_date_code5
    one_day = code == ctx.ok_days()[5]
    assert np.unique(off[0, one_day]).size == 1  # constant per draw and date


def test_cost_points_and_crypto_fee():
    from edgelab.studies import context as cmod

    c = cmod.StudyContext(pd.DataFrame(), ES, TPL, "synthetic-test")
    assert c.cost_points() == pytest.approx((1 + 1) * 0.25 + 4.50 / 50)
    btc = cmod.StudyContext(pd.DataFrame(), load_instrument("BTCUSDT_PERP"), TPL, "synthetic-test")
    with pytest.raises(ValueError):
        btc.cost_points()
    assert np.all(btc.cost_points(np.array([50_000.0])) > 2 * 5.0e-4 * 50_000)


def test_roll_exclusions():
    idx = cme_schedule("2024-03-04", 15, minutes=60)
    b = random_walk_bars(idx, seed=2)
    lab = label_sessions(b.index, TPL)
    td = pd.DatetimeIndex(sorted(lab["trading_date"].unique()))
    assert runner.roll_exclusions(b, lab, "difference", ES.roll, td) == set()
    b2 = b.assign(contract=np.where(lab["trading_date"] >= td[7], "ESM4", "ESH4"))
    assert runner.roll_exclusions(b2, lab, "unadjusted", ES.roll, td) == {td[7]}
    conv = runner.roll_exclusions(b, lab, "unadjusted", ES.roll, td)  # 2024-03-07 = third Friday (15th) - 8 days
    assert pd.Timestamp("2024-03-07") in conv and pd.Timestamp("2024-03-08") in conv and len(conv) == 2


# ---------------------------------------------------------------------------------------
# the whole catalog
# ---------------------------------------------------------------------------------------
def test_every_study_runs_and_matches_its_registration(cfg, catalog_run):
    allowed = {"OK", "NO_EVENTS", "BLOCKED", "REJECTED_AT_GATE"}
    for sid, data in catalog_run.items():
        s = cfg["studies"][sid]
        assert data.status in allowed, sid
        if data.status != "OK":
            continue
        if data.primary is not None and s["type"] == "E" and isinstance(s["twin"], str) and s["twin"] != "primary":
            assert data.twin is not None, f"{sid}: a separate twin was pre-registered but no twin test is returned"
        if s["twin"] in (None, "primary"):
            assert data.twin is None, f"{sid}: twin test returned but none pre-registered"
        if s["type"] == "E" and data.primary is not None:
            assert data.hurdle_norm is not None, f"{sid}: E study without cost hurdle"
        if s["type"] == "E" and len(data.events) and (data.events["pos"] >= 0).all():  # A014 is a day-level study
            assert {"date", "r12", "atr"} <= set(data.events.columns), sid
    assert catalog_run["B018"].status == "BLOCKED"
    assert concat([pd.DataFrame({"pos": pd.Series(dtype=np.int64), "x": pd.Series(dtype=float)})]).columns.tolist() == ["pos", "direction", "x"]


# ---------------------------------------------------------------------------------------
# gates, decisions, candidate selection
# ---------------------------------------------------------------------------------------
def _outcome(sid, typ="E", hyp="H1", point=0.1, lo=0.05, hi=0.15, se=0.02, n=200, halves=(0.1, 0.1), p_bh=0.01,
             hurdle=0.02, twin_mode="primary", twin=None, rank=1):
    o = StudyOutcome(sid, runner.family_of(sid), typ, hyp, sid, rank)
    o.primary = {"point": point, "lo": lo, "hi": hi, "se": se, "p_value": 0.001, "n_by_group": {"ev": n, "ct": 5 * n},
                 "label": sid, "n_obs": 6 * n, "n_dates": 100}
    o.halves, o.p_bh, o.hurdle, o.econ_effect, o.econ_se = halves, p_bh, hurdle, point, se
    o.twin_mode, o.twin = twin_mode, twin
    o.gates = apply_gates(o, q=0.10, min_events=100)
    o.decision = decide(o)
    o.score = ranking_score(o)
    return o


def test_gates_and_decisions():
    assert _outcome("C004").decision == "CANDIDATE"
    assert _outcome("C004", lo=-0.01).gates["G1"] is False
    assert _outcome("C004", p_bh=0.2).decision == "REJECTED"
    assert _outcome("C004", hurdle=0.5).gates["G3"] is False
    assert _outcome("C004", n=99).gates["G4"] is False
    assert _outcome("C004", halves=(0.2, -0.01)).gates["G5"] is False
    weak_twin = {"point": 0.01, "lo": -0.02, "hi": 0.04, "se": 0.01, "label": "t"}
    assert _outcome("B002", twin_mode="separate", twin=weak_twin).decision == "TWIN_EXPLAINS"
    assert _outcome("C018", twin_mode="none").gates["G6"] is None and _outcome("C018", twin_mode="none").decision == "CANDIDATE"
    i = _outcome("C002", typ="I", hurdle=np.nan)
    assert i.gates["G3"] is None and i.gates["G6"] is None and i.decision == "SUPPORTED"
    assert np.isnan(i.score)  # I and M studies are never ranked as candidates


def test_family_fallback_variants_and_cap():
    outs = [_outcome("A001", lo=-0.01, point=0.03, rank=4), _outcome("A005", lo=-0.03, point=0.01, rank=1)]
    select_candidates(outs, 5)
    assert outs[0].decision == "PROMOTED_WITHOUT_SUPPORT" and outs[1].decision == "REJECTED"
    variants = [_outcome("C004", hyp="HC04", point=0.10), _outcome("C006", hyp="HC04", point=0.20)]
    select_candidates(variants, 5)
    assert variants[1].decision == "CANDIDATE" and variants[0].decision == "CANDIDATE_VARIANT"
    many = [_outcome(f"B{10 + j:03d}", hyp=f"HB{j}", point=0.1 + 0.01 * j) for j in range(7)]
    select_candidates(many, 5)
    assert [o.decision for o in many].count("CANDIDATE") == 5 and [o.decision for o in many].count("CANDIDATE_CAPPED") == 2
    assert all(o.decision == "CANDIDATE_CAPPED" for o in many[:2])


# ---------------------------------------------------------------------------------------
# the runner end to end, and its guards
# ---------------------------------------------------------------------------------------
def test_runner_refuses_synthetic_data_in_the_repository(ctx, cfg, tmp_path):
    with pytest.raises(PermissionError):
        runner.run_all(ctx, cfg, ids=["C001"])
    with pytest.raises(PermissionError):
        runner.run_all(ctx, cfg, ids=["C001"], out_root=RESEARCH_ROOT / "event_studies" / "x", registry=tmp_path / "r.csv",
                       journal_path=tmp_path / "j.md", rejected=tmp_path / "rej.md")
    with pytest.raises(PermissionError, match="pre-registration"):
        runner.run_all(ctx, cfg, ids=["C001"], out_root=tmp_path / "o", registry=tmp_path / "r.csv",
                       journal_path=tmp_path / "j.md", rejected=tmp_path / "rej.md")


def test_runner_end_to_end(ctx, cfg, tmp_path):
    jp, reg, rej, out = tmp_path / "J.md", tmp_path / "reg.csv", tmp_path / "REJ.md", tmp_path / "out"
    rej.write_text("# REJECTED_IDEAS\n\n## Results-based rejections\n\n" + runner.REJECTED_PLACEHOLDER + "\n")
    assert len(runner.preregister_all(cfg, path=jp)) == len(cfg["studies"])
    assert runner.preregister_all(cfg, path=jp) == []  # never registered twice
    ids = ["A001", "A004", "B002", "B018", "C001", "C002", "C009"]
    kw = dict(out_root=out, registry=reg, journal_path=jp, rejected=rej, n_boot=200, n_boot_desc=50, period_label="synthetic")
    outs = runner.run_all(ctx, cfg, ids=ids, **kw)
    assert [o.sid for o in outs] == ids
    (summary_file,) = out.glob("SUMMARY_partial_*.md")  # a subset of studies: no family fallback, no cap
    summary = summary_file.read_text()
    assert "SYNTHETIC SOFTWARE TEST - NOT EVIDENCE" in summary and "Partial run" in summary
    assert not any(o.decision in ("PROMOTED_WITHOUT_SUPPORT", "CANDIDATE_CAPPED", "CANDIDATE_VARIANT") for o in outs)
    for o in outs:
        res = json.loads((out / o.sid / "result.json").read_text())
        assert res["evidence_class"] == "SYNTHETIC-TEST" and res["decision"] == o.decision
        assert (journal.has_result(o.sid, jp)) == (o.status not in runner.NOT_RUN)
    regdf = pd.read_csv(reg)
    assert len(regdf) == sum(len(o.test_ids) for o in outs) and set(regdf["experiment_id"]) <= set(ids)
    assert (out / "C001" / "horizons_descriptive.csv").exists() and (out / "C001" / "events.parquet").exists()
    rej_text = rej.read_text()
    assert runner.REJECTED_PLACEHOLDER not in rej_text
    assert sum(o.decision in ("REJECTED", "NOT_SUPPORTED", "NOT_TESTABLE") and o.status not in runner.NOT_RUN for o in outs) == \
        rej_text.count("`synthetic-test`")
    with pytest.raises(PermissionError, match="re-run"):
        runner.run_all(ctx, cfg, ids=["C001"], **kw)
    runner.run_all(ctx, cfg, ids=["C001"], rerun_reason="software test of the re-run path", **kw)
    assert journal.has_note("RE-RUN C001", jp) and len(pd.read_csv(reg)) > len(regdf)
