"""The validation laboratory: pre-registered tests, each with assumptions and a status.

A test returns PASSED, WARNING, FAILED, INCONCLUSIVE, NOT_APPLICABLE or NOT_RUN,
with what it assumed, what it needed, what it measured and how to read it. The
verdict is a fixed function of those statuses (documented in
docs/quantlab/VALIDATION_PROTOCOL.md) — no aggregate score, no hidden weights.

Splits are chronological by session. The last ``holdout_fraction`` of sessions is
sealed: no test here looks at it unless the user explicitly evaluates it, once.
Parameters are only ever chosen on training data (in-sample, or each walk-forward
training window) — never on the out-of-sample or holdout sessions.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from statistics import NormalDist
from typing import Any

import numpy as np

from jarvis.quantlab.futures.engine import MINUTE, Bars, Result
from jarvis.quantlab.futures.metrics import daily_series, segment
from jarvis.quantlab.futures.sessions import Window
from jarvis.quantlab.futures.spec import FuturesSpec

VERDICTS = (
    "INVALID_DATA_OR_METHOD",
    "INSUFFICIENT_EVIDENCE",
    "REJECTED_HYPOTHESIS",
    "PROMISING_RESEARCH_CANDIDATE",
    "FORWARD_VALIDATION_REQUIRED",
    "ROBUST_UNDER_TESTED_ASSUMPTIONS",  # reserved: needs forward evidence (not built yet)
)
ROBUSTNESS = (
    "WALK_FORWARD",
    "COST_STRESS",
    "PARAMETER_SENSITIVITY",
    "BOOTSTRAP",
    "SELECTION_BIAS",
    "AMBIGUITY",
    "TAIL_DEPENDENCE",
    "HOLDOUT",
)
EULER = 0.5772156649015329
MIN_IS_SESSIONS = 20


@dataclass
class Split:
    insample: list[Window]
    oos: list[Window]
    holdout: list[Window]
    embargo: list[Window]

    def labels(self, part: str) -> list[str]:
        return [w.label.isoformat() for w in getattr(self, part)]

    def as_dict(self) -> dict[str, Any]:
        def span(ws: list[Window]) -> dict[str, Any]:
            return {
                "sessions": len(ws),
                "first": ws[0].label.isoformat() if ws else None,
                "last": ws[-1].label.isoformat() if ws else None,
            }

        return {
            "insample": span(self.insample),
            "oos": span(self.oos),
            "holdout": span(self.holdout),
            "embargo_sessions": [w.label.isoformat() for w in self.embargo],
        }


def split(windows: list[Window], spec: FuturesSpec) -> Split:
    v = spec.validation
    n = len(windows)
    hold = math.ceil(n * v.holdout_fraction) if v.holdout_fraction > 0 else 0
    oos = math.ceil(n * v.oos_fraction)
    e = v.embargo_sessions
    holdout = windows[n - hold :] if hold else []
    rest = windows[: n - hold - (e if hold else 0)]
    embargo = windows[n - hold - e : n - hold] if hold and e else []
    oos_part = rest[len(rest) - oos :] if oos else []
    before = rest[: len(rest) - oos - e]
    embargo = rest[len(rest) - oos - e : len(rest) - oos] + embargo if e else embargo
    return Split(insample=before, oos=oos_part, holdout=holdout, embargo=embargo)


def test(
    id_: str,
    name: str,
    status: str,
    *,
    assumptions: list[str],
    requirements: str,
    interpretation: str,
    metric: dict[str, Any] | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id_,
        "name": name,
        "status": status,
        "assumptions": assumptions,
        "requirements": requirements,
        "metric": metric or {},
        "interpretation": interpretation,
        "evidence": evidence,
    }


# -- benchmark ------------------------------------------------------------------------------


def session_drift(
    bars: Bars,
    windows: list[Window],
    tick_values: dict[int, tuple[int, float]],
    contracts: int,
    fee_per_side: float,
    slippage_ticks: int,
) -> dict[str, float]:
    """Naive benchmark, computed independently of the engine: long from the first bar's
    open in the window to the open of the first bar at/after the flatten time, same costs."""
    out: dict[str, float] = {}
    for w in windows:
        lo, hi = (int(x) for x in np.searchsorted(bars.ts, [w.start_ns, w.end_ns]))
        if hi <= lo:
            continue
        ids = set(bars.iid[lo:hi].tolist())
        if len(ids) != 1:
            continue
        instrument = ids.pop()
        if instrument not in tick_values:
            continue
        tick, value = tick_values[instrument]
        exit_i = next((i for i in range(lo, hi) if bars.ts[i] >= w.flatten_ns), None)
        entry = int(bars.open[lo]) // tick + slippage_ticks
        if exit_i is None:
            leave = int(bars.close[hi - 1]) // tick - slippage_ticks
        else:
            leave = int(bars.open[exit_i]) // tick - slippage_ticks
        out[w.label.isoformat()] = (
            leave - entry
        ) * value * contracts - 2 * fee_per_side * contracts
    return out


# -- statistics ------------------------------------------------------------------------------


def stationary_bootstrap(
    values: Sequence[float], block: int, samples: int, seed: int
) -> np.ndarray:
    """Politis–Romano stationary bootstrap of a daily series (keeps short-range dependence).
    Returns an array (samples × n) of resampled series."""
    data = np.asarray(values, dtype=float)
    n = data.size
    rng = np.random.default_rng(seed)
    p = 1.0 / max(block, 1)
    idx = np.empty((samples, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, samples)
    restart = rng.random((samples, n)) < p
    fresh = rng.integers(0, n, (samples, n))
    for t in range(1, n):
        idx[:, t] = np.where(restart[:, t], fresh[:, t], (idx[:, t - 1] + 1) % n)
    return data[idx]


def max_drawdown(paths: np.ndarray) -> np.ndarray:
    cumulative = np.cumsum(paths, axis=1)
    peaks = np.maximum.accumulate(
        np.concatenate([np.zeros((paths.shape[0], 1)), cumulative], axis=1), axis=1
    )[:, 1:]
    return np.max(peaks - cumulative, axis=1)


def sharpe(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    stdev = statistics.stdev(values)
    return statistics.fmean(values) / stdev if stdev > 0 else None


def deflated_sharpe(
    returns: Sequence[float], trial_sharpes: Sequence[float], trials: int
) -> dict[str, Any]:
    """Bailey & López de Prado (2014). Per-period (daily) Sharpe ratios throughout."""
    t = len(returns)
    sr = sharpe(returns)
    if sr is None or t < 30:
        return {
            "dsr": None,
            "reason": "fewer than 30 sessions or zero variance",
            "trials": trials,
            "sessions": t,
        }
    values = np.asarray(returns, dtype=float)
    mean, stdev = values.mean(), values.std()
    skew = float(np.mean(((values - mean) / stdev) ** 3)) if stdev > 0 else 0.0
    kurt = float(np.mean(((values - mean) / stdev) ** 4)) if stdev > 0 else 3.0
    norm = NormalDist()
    if trials >= 2 and len(trial_sharpes) >= 2:
        variance = statistics.pvariance(trial_sharpes)
        expected_max = math.sqrt(variance) * (
            (1 - EULER) * norm.inv_cdf(1 - 1 / trials)
            + EULER * norm.inv_cdf(1 - 1 / (trials * math.e))
        )
    else:
        expected_max = 0.0
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if denom <= 0:
        return {
            "dsr": None,
            "reason": "non-normal returns make the statistic undefined",
            "trials": trials,
            "sessions": t,
        }
    z = (sr - expected_max) * math.sqrt(t - 1) / math.sqrt(denom)
    return {
        "dsr": round(norm.cdf(z), 4),
        "sharpe_daily": round(sr, 5),
        "expected_max_sharpe_daily": round(expected_max, 5),
        "trials": trials,
        "sessions": t,
        "skew": round(skew, 3),
        "kurtosis": round(kurt, 3),
    }


# -- the suite ---------------------------------------------------------------------------------


Simulate = Callable[..., Result]


@dataclass
class Context:
    spec: FuturesSpec
    bars: Bars
    windows: list[Window]
    split: Split
    simulate: Simulate  # (spec, windows, mode=..., cost_multiplier=..., record_equity=...)
    fitness: dict[str, Any]
    audit_ok: bool
    fixture: bool
    trials_now: Callable[[], tuple[int, list[float]]]
    holdout: dict[str, Any] | None  # {"evaluated": bool, "independent": bool, "result": Result}
    progress: Callable[[str, float], None] = lambda stage, fraction: None
    record_trial: Callable[[dict[str, float], str, Result, list[str]], None] = (
        lambda params, segment_name, result, labels: None
    )
    extra: dict[str, Any] = field(default_factory=dict)


def _net(result: Result, labels: Sequence[str]) -> float:
    chosen = set(labels)
    return float(sum((t.net for t in result.trades if t.session in chosen), 0))


def run_suite(ctx: Context, base: Result, optimistic: Result) -> list[dict[str, Any]]:
    spec, sp, v = ctx.spec, ctx.split, ctx.spec.validation
    is_l, oos_l = sp.labels("insample"), sp.labels("oos")
    tests: list[dict[str, Any]] = []
    is_m, oos_m = segment(base, is_l), segment(base, oos_l)

    # 1 baseline vs naive benchmark
    tick_values = {
        i: (c.tick_size_fixed, float(c.tick_value)) for i, c in base.contracts_used.items()
    }
    drift = session_drift(
        ctx.bars,
        sp.insample + sp.oos,
        tick_values,
        spec.sizing.contracts,
        float(base.fee_per_side),
        base.slippage_ticks,
    )
    drift_oos = sum(drift.get(label, 0.0) for label in oos_l)
    tests.append(
        test(
            "BASELINE",
            "Baseline vs. naive benchmark",
            "PASSED" if (oos_m["net_pnl"] or 0) > drift_oos else "WARNING",
            assumptions=[
                "Benchmark: long 1 lot from the window's first open to the flatten "
                "open each session, same costs (not exposure-matched)."
            ],
            requirements="Out-of-sample sessions.",
            metric={
                "strategy_oos_net": oos_m["net_pnl"],
                "benchmark_oos_net": round(drift_oos, 2),
                "strategy_insample_net": is_m["net_pnl"],
            },
            interpretation="Beating a naive session drift is necessary, not sufficient.",
        )
    )

    # 2 data integrity + 16 independent arithmetic
    fit = ctx.fitness["status"]
    tests.append(
        test(
            "DATA_INTEGRITY",
            "Data fitness and integrity",
            "FAILED" if fit == "INVALID" else "WARNING" if fit != "FIT" else "PASSED",
            assumptions=["Quality gates from the Data Hub plus strategy-specific fitness."],
            requirements="A dataset without BLOCK findings and with contract specs.",
            metric={"fitness": fit, "reasons": ctx.fitness.get("reasons", [])},
            interpretation="INVALID data makes every other number meaningless.",
        )
    )
    tests.append(
        test(
            "INDEPENDENT_REFERENCE",
            "Independent ledger audit",
            "PASSED" if ctx.audit_ok else "FAILED",
            assumptions=["Audit re-derives prices from bars and money from ticks × tick value."],
            requirements="The run's ledger.",
            metric={"audit_passed": ctx.audit_ok},
            interpretation="A second, independent engine comparison is planned (R6); this is the "
            "ledger-level check.",
        )
    )

    # 3 leakage: perturb the future, past decisions must not move
    tests.append(_leakage(ctx, base))
    ctx.progress("leakage check", 0.15)

    # 4 out-of-sample
    trades_oos = oos_m["trades"]
    if trades_oos < v.min_trades_oos:
        status = "INCONCLUSIVE"
    else:
        status = "PASSED" if (oos_m["net_pnl"] or 0) > 0 else "FAILED"
    tests.append(
        test(
            "OUT_OF_SAMPLE",
            "Chronological out-of-sample",
            status,
            assumptions=[
                f"Fixed split: {len(sp.insample)} in-sample, {len(sp.oos)} out-of-sample "
                f"sessions, {len(sp.embargo)} embargoed; no parameter chosen on OOS."
            ],
            requirements=f"At least {v.min_trades_oos} out-of-sample trades.",
            metric={
                "oos_trades": trades_oos,
                "oos_net": oos_m["net_pnl"],
                "oos_expectancy": oos_m["expectancy"],
                "is_expectancy": is_m["expectancy"],
                "oos_sharpe": oos_m["sharpe"],
                "is_sharpe": is_m["sharpe"],
            },
            interpretation="Positive out-of-sample net after costs is the minimum for a candidate; "
            "a sample below the minimum can't decide anything.",
        )
    )

    # 7 cost stress
    tests.append(_cost_stress(ctx, oos_l))
    ctx.progress("cost stress", 0.3)
    # 8 parameter sensitivity (in-sample only)
    tests.append(_sensitivity(ctx, is_l))
    ctx.progress("parameter sensitivity", 0.5)
    # 6 walk-forward
    tests.append(_walk_forward(ctx))
    ctx.progress("walk-forward", 0.7)
    # 10 bootstrap
    tests.append(_bootstrap(ctx, base, oos_l))
    # 11 selection bias
    oos_returns = [n / float(base.capital) for _, n in daily_series(base, oos_l)]
    trials, trial_sharpes = ctx.trials_now()
    dsr = deflated_sharpe(oos_returns, trial_sharpes, trials)
    if dsr["dsr"] is None:
        status = "NOT_APPLICABLE"
    else:
        status = (
            "PASSED" if dsr["dsr"] >= 0.95 else "INCONCLUSIVE" if dsr["dsr"] >= 0.5 else "FAILED"
        )
    tests.append(
        test(
            "SELECTION_BIAS",
            "Multiple testing (deflated Sharpe ratio)",
            status,
            assumptions=[
                f"{trials} variant(s) of this strategy family were evaluated so far; "
                "their Sharpe ratios set the bar a lucky best variant would clear."
            ],
            requirements="At least 30 out-of-sample sessions.",
            metric=dsr,
            interpretation="Probability that the out-of-sample Sharpe beats what the best of "
            "the tried variants would show by chance. ≥ 0.95 passes.",
        )
    )
    # 9 regimes, 11 subperiods
    tests.append(_regimes(ctx, base, is_l, oos_l))
    tests.append(_subperiods(base, is_l + oos_l))
    # 13 tails, 14 ambiguity
    tests.append(_tails(oos_m))
    tests.append(_ambiguity(base, optimistic, oos_l))
    ctx.progress("statistics", 0.85)
    # 5 holdout
    tests.append(_holdout(ctx))
    # 12, 15: not run in this release
    tests.append(
        test(
            "CROSS_INSTRUMENT",
            "Cross-instrument check",
            "NOT_RUN",
            assumptions=[],
            requirements="A second, economically related dataset (e.g. ES for NQ).",
            interpretation="Planned (R6): run the frozen spec on a related market.",
        )
    )
    tests.append(
        test(
            "FORWARD_PAPER",
            "Forward paper monitoring",
            "NOT_RUN",
            assumptions=[],
            requirements="Live data after the research period.",
            interpretation="Planned (R7). Until then no strategy can be called robust.",
        )
    )
    return tests


def _leakage(ctx: Context, base: Result) -> dict[str, Any]:
    windows = ctx.split.insample + ctx.split.oos
    if len(windows) < 4:
        return test(
            "LEAKAGE",
            "Look-ahead check",
            "NOT_APPLICABLE",
            assumptions=[],
            requirements="At least 4 sessions.",
            interpretation="",
        )
    cut = windows[len(windows) // 2]
    bars = ctx.bars
    i = int(np.searchsorted(bars.ts, cut.start_ns))
    rng = np.random.default_rng(1234)
    shuffled = Bars(
        ts=bars.ts.copy(),
        iid=bars.iid.copy(),
        open=bars.open.copy(),
        high=bars.high.copy(),
        low=bars.low.copy(),
        close=bars.close.copy(),
    )
    # Reverse and jitter every price from the cut on (kept on the tick grid, OHLC-consistent).
    tick = (
        min(c.tick_size_fixed for c in base.contracts_used.values()) if base.contracts_used else 1
    )
    shift = (rng.integers(-40, 40, bars.ts.size - i) * tick).astype(np.int64)
    for name in ("open", "high", "low", "close"):
        getattr(shuffled, name)[i:] = getattr(bars, name)[i:][::-1] + shift
    hi = np.maximum.reduce(
        [shuffled.open[i:], shuffled.high[i:], shuffled.low[i:], shuffled.close[i:]]
    )
    lo = np.minimum.reduce(
        [shuffled.open[i:], shuffled.high[i:], shuffled.low[i:], shuffled.close[i:]]
    )
    shuffled.high[i:], shuffled.low[i:] = hi, lo
    again = ctx.simulate(ctx.spec, windows, bars=shuffled, record_equity=False)
    before = cut.start_ns

    def key(r: Result) -> list[tuple[Any, ...]]:
        return [
            (t.session, t.entry_bar_ts, t.entry_ticks, t.exit_bar_ts, t.exit_ticks)
            for t in r.trades
            if t.exit_bar_ts < before
        ]

    same = key(base) == key(again)
    return test(
        "LEAKAGE",
        "Look-ahead check (future perturbed)",
        "PASSED" if same else "FAILED",
        assumptions=[
            f"Every price from {cut.label} on was reversed and jittered; trades that "
            "closed before then must not change."
        ],
        requirements="At least 4 sessions.",
        metric={"trades_before_cut": len(key(base)), "identical": same},
        interpretation="If changing the future changes past trades, the engine sees the future.",
    )


def _cost_stress(ctx: Context, oos_l: list[str]) -> dict[str, Any]:
    v = ctx.spec.validation
    rows = []
    for k in v.cost_stress:
        stressed = ctx.simulate(
            ctx.spec, ctx.split.insample + ctx.split.oos, cost_multiplier=k, record_equity=False
        )
        rows.append(
            {
                "multiplier": k,
                "oos_net": round(_net(stressed, oos_l), 2),
                "oos_trades": sum(1 for t in stressed.trades if t.session in set(oos_l)),
            }
        )
    if not rows:
        return test(
            "COST_STRESS",
            "Cost and slippage stress",
            "NOT_RUN",
            assumptions=[],
            requirements="cost_stress multipliers in the spec.",
            interpretation="",
        )
    first = rows[0]["oos_net"]
    if first <= 0:
        status = "FAILED"
    elif all(r["oos_net"] > 0 for r in rows):
        status = "PASSED"
    else:
        status = "WARNING"
    return test(
        "COST_STRESS",
        "Cost and slippage stress",
        status,
        assumptions=[
            "Commission, fees and slippage ticks multiplied (slippage rounded up to "
            "whole ticks); fills otherwise unchanged."
        ],
        requirements="Out-of-sample sessions.",
        metric={"scenarios": rows},
        interpretation=f"An edge that disappears at {v.cost_stress[0]:g}× costs is too thin to "
        "survive real execution.",
    )


def _grid_results(
    ctx: Context, windows: list[Window], segment_name: str
) -> list[tuple[dict[str, float], Result]]:
    out = []
    for params in ctx.spec.grid():
        variant = ctx.spec.with_params(params)
        result = ctx.simulate(variant, windows, record_equity=False)
        ctx.record_trial(params, segment_name, result, [w.label.isoformat() for w in windows])
        out.append((params, result))
    return out


def _sensitivity(ctx: Context, is_l: list[str]) -> dict[str, Any]:
    if not ctx.spec.grid():
        return test(
            "PARAMETER_SENSITIVITY",
            "Parameter sensitivity",
            "NOT_APPLICABLE",
            assumptions=[],
            requirements="A parameter grid declared in the spec.",
            interpretation="Without a grid, fragility can't be measured.",
        )
    results = _grid_results(ctx, ctx.split.insample, "IS")
    ctx.extra["grid"] = results
    rows: list[dict[str, Any]] = [
        {"params": p, "is_net": round(_net(r, is_l), 2), "trades": len(r.trades)}
        for p, r in results
    ]
    positive = sum(1 for r in rows if r["is_net"] > 0) / len(rows)
    current = ctx.spec.current_params()
    chosen = next((r for r in rows if r["params"] == current), None)
    neighbours = [r for r in rows if _neighbour(r["params"], current, ctx.spec)]
    neighbour_mean = statistics.fmean([r["is_net"] for r in neighbours]) if neighbours else None
    isolated = (
        chosen is not None
        and chosen["is_net"] > 0
        and neighbour_mean is not None
        and neighbour_mean < 0.5 * chosen["is_net"]
    )
    if positive < 0.3:
        status = "FAILED"
    elif positive >= 0.6 and not isolated:
        status = "PASSED"
    else:
        status = "WARNING"
    return test(
        "PARAMETER_SENSITIVITY",
        "Parameter sensitivity (in-sample only)",
        status,
        assumptions=[
            "Grid fixed in the spec before the run; evaluated on in-sample sessions only."
        ],
        requirements="A parameter grid.",
        metric={
            "grid": rows,
            "share_positive": round(positive, 3),
            "chosen": chosen,
            "neighbour_mean_net": neighbour_mean,
            "isolated_peak": isolated,
        },
        interpretation="A plateau of positive neighbours is reassuring; a lone peak is a sign "
        "of fitting noise.",
    )


def _neighbour(params: dict[str, float], current: dict[str, float], spec: FuturesSpec) -> bool:
    grid = spec.validation.parameter_grid
    diffs = 0
    for key, value in params.items():
        if value == current.get(key):
            continue
        values = sorted(grid[key])
        if current.get(key) not in values:
            return False
        if abs(values.index(value) - values.index(current[key])) != 1:
            return False
        diffs += 1
    return diffs == 1


def _walk_forward(ctx: Context) -> dict[str, Any]:
    wf = ctx.spec.validation.walk_forward
    windows = ctx.split.insample + ctx.split.oos
    if len(windows) < wf.train_sessions + wf.test_sessions:
        return test(
            "WALK_FORWARD",
            "Walk-forward",
            "NOT_APPLICABLE",
            assumptions=[],
            requirements=f"{wf.train_sessions + wf.test_sessions} sessions (have {len(windows)}).",
            interpretation="Not enough history for one window.",
        )
    grid = ctx.spec.grid()
    rows: list[dict[str, Any]] = []
    start = 0
    while start + wf.train_sessions + wf.test_sessions <= len(windows):
        train_from = 0 if wf.mode == "anchored" else start
        train = windows[train_from : start + wf.train_sessions]
        test_w = windows[start + wf.train_sessions : start + wf.train_sessions + wf.test_sessions]
        chosen = ctx.spec.current_params()
        if grid:
            best = None
            for params in grid:
                result = ctx.simulate(ctx.spec.with_params(params), train, record_equity=False)
                ctx.record_trial(params, "WF-train", result, [w.label.isoformat() for w in train])
                net = float(sum((t.net for t in result.trades), 0))
                if best is None or net > best[0]:
                    best = (net, params)
            assert best is not None
            chosen = best[1]
        variant = ctx.spec.with_params(chosen) if grid else ctx.spec
        result = ctx.simulate(variant, test_w, record_equity=False)
        rows.append(
            {
                "train": f"{train[0].label} → {train[-1].label}",
                "test": f"{test_w[0].label} → {test_w[-1].label}",
                "params": chosen,
                "test_net": round(float(sum((t.net for t in result.trades), 0)), 2),
                "test_trades": len(result.trades),
            }
        )
        start += wf.test_sessions
    total = sum(r["test_net"] for r in rows)
    share = sum(1 for r in rows if r["test_net"] > 0) / len(rows)
    status = "FAILED" if total <= 0 else "PASSED" if share >= 0.5 else "WARNING"
    return test(
        "WALK_FORWARD",
        f"Walk-forward ({wf.mode})",
        status,
        assumptions=[
            "Parameters re-chosen on each training window by net P&L "
            "(conservative fills); test windows never influence the choice."
            if grid
            else "No grid: the fixed parameters are evaluated window by window."
        ],
        requirements=f"{wf.train_sessions} training + {wf.test_sessions} test sessions per window.",
        metric={
            "windows": rows,
            "total_test_net": round(total, 2),
            "share_positive": round(share, 3),
        },
        interpretation="Stitched test windows are the closest a backtest gets to how the "
        "strategy would have been run.",
    )


def _bootstrap(ctx: Context, base: Result, oos_l: list[str]) -> dict[str, Any]:
    b = ctx.spec.validation.bootstrap
    daily = [n for _, n in daily_series(base, oos_l)]
    if len(daily) < 20:
        return test(
            "BOOTSTRAP",
            "Block bootstrap",
            "NOT_APPLICABLE",
            assumptions=[],
            requirements="At least 20 out-of-sample sessions.",
            interpretation="Too few sessions to resample.",
        )
    paths = stationary_bootstrap(daily, b.block_sessions, b.samples, b.seed)
    means = paths.mean(axis=1)
    totals = paths.sum(axis=1)
    lo, hi = np.quantile(means, [0.05, 0.95])
    dd95 = float(np.quantile(max_drawdown(paths), 0.95))
    status = "PASSED" if lo > 0 else "FAILED" if hi < 0 else "INCONCLUSIVE"
    return test(
        "BOOTSTRAP",
        "Stationary block bootstrap (out-of-sample)",
        status,
        assumptions=[
            f"Daily P&L resampled in blocks of mean length {b.block_sessions} "
            f"sessions, {b.samples} samples, seed {b.seed}; assumes the OOS period is "
            "representative."
        ],
        requirements="At least 20 out-of-sample sessions.",
        metric={
            "mean_daily_ci90": [round(float(lo), 2), round(float(hi), 2)],
            "probability_total_loss": round(float(np.mean(totals <= 0)), 4),
            "max_drawdown_p95": round(dd95, 2),
            "sessions": len(daily),
        },
        interpretation="If the 90% interval of the mean daily P&L includes zero, the data can't "
        "tell the strategy from luck.",
    )


def _regimes(ctx: Context, base: Result, is_l: list[str], oos_l: list[str]) -> dict[str, Any]:
    bars = ctx.bars
    ranges: dict[str, float] = {}
    for w in ctx.windows:
        lo, hi = (int(x) for x in np.searchsorted(bars.ts, [w.start_ns, w.end_ns]))
        if hi > lo:
            ranges[w.label.isoformat()] = float(bars.high[lo:hi].max() - bars.low[lo:hi].min())
    labels = [w.label.isoformat() for w in ctx.windows]
    prior: dict[str, float] = {}
    for previous, current in itertools.pairwise(labels):
        if previous in ranges:
            prior[current] = ranges[previous]  # known before the session starts
    is_values = sorted(prior[label] for label in is_l if label in prior)
    if len(is_values) < 9:
        return test(
            "REGIMES",
            "Volatility regimes",
            "NOT_APPLICABLE",
            assumptions=[],
            requirements="At least 9 in-sample sessions.",
            interpretation="",
        )
    low_cut = is_values[len(is_values) // 3]
    high_cut = is_values[2 * len(is_values) // 3]

    def regime(label: str) -> str | None:
        value = prior.get(label)
        if value is None:
            return None
        return "low" if value < low_cut else "high" if value >= high_cut else "mid"

    table: dict[str, dict[str, float]] = {}
    for t in base.trades:
        if t.session not in set(oos_l):
            continue
        name = regime(t.session)
        if name is None:
            continue
        row = table.setdefault(name, {"trades": 0, "net": 0.0})
        row["trades"] += 1
        row["net"] += float(t.net)
    weekdays: dict[str, dict[str, float]] = {}
    for t in base.trades:
        if t.session in set(is_l) | set(oos_l):
            day = date.fromisoformat(t.session).strftime("%a")
            row = weekdays.setdefault(day, {"trades": 0, "net": 0.0})
            row["trades"] += 1
            row["net"] += float(t.net)
    enough = {k: v for k, v in table.items() if v["trades"] >= 10}
    if not enough:
        status = "INCONCLUSIVE"
    elif all(v["net"] >= 0 for v in enough.values()):
        status = "PASSED"
    else:
        status = "WARNING"
    return test(
        "REGIMES",
        "Volatility regimes (ex-ante)",
        status,
        assumptions=[
            "Regime = previous session's regular-hours range, tercile cut-offs from "
            "in-sample sessions only — known before each session, never ex post."
        ],
        requirements="10+ out-of-sample trades in a regime to judge it.",
        metric={
            "oos_by_regime": {
                k: {"trades": v["trades"], "net": round(v["net"], 2)} for k, v in table.items()
            },
            "by_weekday_is_oos": {
                k: {"trades": v["trades"], "net": round(v["net"], 2)} for k, v in weekdays.items()
            },
        },
        interpretation="A strategy that only works in one regime needs a regime filter decided "
        "in advance — weekday splits are shown for context and are subject to "
        "multiple-comparison noise.",
    )


def _subperiods(base: Result, labels: list[str]) -> dict[str, Any]:
    months: dict[str, float] = {}
    for label, net in daily_series(base, labels):
        months[label[:7]] = months.get(label[:7], 0.0) + net
    if len(months) < 3:
        return test(
            "SUBPERIODS",
            "Subperiod stability",
            "NOT_APPLICABLE",
            assumptions=[],
            requirements="At least 3 calendar months.",
            interpretation="",
        )
    share = sum(1 for v in months.values() if v > 0) / len(months)
    return test(
        "SUBPERIODS",
        "Subperiod stability (monthly)",
        "PASSED" if share >= 0.5 else "WARNING",
        assumptions=["Calendar months of in-sample + out-of-sample sessions."],
        requirements="At least 3 months.",
        metric={
            "months": {k: round(v, 2) for k, v in sorted(months.items())},
            "share_positive": round(share, 3),
        },
        interpretation="Results concentrated in a few months are fragile.",
    )


def _tails(oos: dict[str, Any]) -> dict[str, Any]:
    if not oos["trades"]:
        return test(
            "TAIL_DEPENDENCE",
            "Tail and outlier dependence",
            "NOT_APPLICABLE",
            assumptions=[],
            requirements="Out-of-sample trades.",
            interpretation="",
        )
    without = oos["net_without_top5"]
    status = "PASSED" if without is not None and without > 0 else "WARNING"
    return test(
        "TAIL_DEPENDENCE",
        "Tail and outlier dependence (out-of-sample)",
        status,
        assumptions=["Removes the five best trades."],
        requirements="Out-of-sample trades.",
        metric={
            k: oos.get(k)
            for k in (
                "worst_trade",
                "best_trade",
                "max_loss_streak",
                "top5_share",
                "net_without_top5",
                "max_drawdown",
            )
        },
        interpretation="If the result depends on a handful of trades, it's luck until shown "
        "otherwise.",
    )


def _ambiguity(base: Result, optimistic: Result, oos_l: list[str]) -> dict[str, Any]:
    chosen = set(oos_l)
    trades = [t for t in base.trades if t.session in chosen]
    share = sum(1 for t in trades if t.ambiguous) / len(trades) if trades else 0.0
    sessions = sum(1 for s in base.sessions if s.status == "AMBIGUOUS_ENTRY" and s.label in chosen)
    cons, opt = _net(base, oos_l), _net(optimistic, oos_l)
    flips = cons <= 0 < opt
    status = "WARNING" if flips or share > 0.1 else "PASSED"
    return test(
        "AMBIGUITY",
        "Intrabar ambiguity (1-minute bars)",
        status,
        assumptions=[
            "Conservative: stop before target inside one bar; optimistic: the reverse. "
            "Days where both breakouts trigger in one bar are excluded."
        ],
        requirements="—",
        metric={
            "oos_conservative_net": round(cons, 2),
            "oos_optimistic_net": round(opt, 2),
            "ambiguous_trade_share": round(share, 4),
            "excluded_sessions": sessions,
        },
        interpretation="If the sign depends on the unknowable order inside a minute, get "
        "1-second bars or trades for those days before trusting the result.",
    )


def _holdout(ctx: Context) -> dict[str, Any]:
    h = ctx.holdout
    if not ctx.split.holdout:
        return test(
            "HOLDOUT",
            "Final holdout",
            "NOT_APPLICABLE",
            assumptions=[],
            requirements="holdout_fraction > 0.",
            interpretation="No holdout declared.",
        )
    if not h or not h.get("evaluated"):
        return test(
            "HOLDOUT",
            "Final holdout (sealed)",
            "NOT_RUN",
            assumptions=[
                f"{len(ctx.split.holdout)} sessions sealed: "
                f"{ctx.split.holdout[0].label} → {ctx.split.holdout[-1].label}."
            ],
            requirements="Your explicit decision to evaluate it — once.",
            interpretation="Evaluate the holdout only when the strategy is final; every further "
            "look makes it less independent.",
        )
    result: Result = h["result"]
    labels = ctx.split.labels("holdout")
    m = segment(result, labels)
    if m["trades"] < max(5, ctx.spec.validation.min_trades_oos // 3):
        status = "INCONCLUSIVE"
    elif (m["net_pnl"] or 0) <= 0:
        status = "FAILED"
    else:
        status = "PASSED" if h.get("independent") else "WARNING"
    return test(
        "HOLDOUT",
        "Final holdout",
        status,
        assumptions=[
            "Evaluated once with the frozen spec."
            + (
                ""
                if h.get("independent")
                else f" Not independent: this strategy's holdout was evaluated "
                f"{h.get('previous', 0)} time(s) before."
            )
        ],
        requirements="Unsealed by an explicit decision.",
        metric={
            "holdout_net": m["net_pnl"],
            "holdout_trades": m["trades"],
            "holdout_sharpe": m["sharpe"],
            "independent": h.get("independent"),
        },
        interpretation="The last untouched data. A loss here rejects the hypothesis.",
    )


# -- verdict -----------------------------------------------------------------------------------


def verdict(tests: list[dict[str, Any]], *, fixture: bool) -> dict[str, Any]:
    status = {t["id"]: t["status"] for t in tests}
    reasons: list[str] = []
    next_steps: list[str] = []
    if any(
        status.get(k) == "FAILED" for k in ("DATA_INTEGRITY", "LEAKAGE", "INDEPENDENT_REFERENCE")
    ):
        label = "INVALID_DATA_OR_METHOD"
        reasons.append("Data integrity, look-ahead or ledger audit failed.")
        next_steps.append("Fix the data or method problem shown in the failing gate first.")
    elif status.get("OUT_OF_SAMPLE") == "INCONCLUSIVE":
        label = "INSUFFICIENT_EVIDENCE"
        reasons.append("Too few out-of-sample trades to decide anything.")
        next_steps.append("Add history (a longer dataset) before changing the strategy.")
    elif (
        status.get("OUT_OF_SAMPLE") == "FAILED"
        or status.get("HOLDOUT") == "FAILED"
        or status.get("BOOTSTRAP") == "FAILED"
    ):
        label = "REJECTED_HYPOTHESIS"
        reasons.append("Out-of-sample (or holdout) results are negative after costs.")
        next_steps.append("Record why it failed; a new idea is a new hypothesis, not a tweak.")
    else:
        unmet = [k for k in ROBUSTNESS if status.get(k) != "PASSED"]
        if unmet:
            label = "PROMISING_RESEARCH_CANDIDATE"
            reasons.append("Positive out-of-sample result; not yet robust: " + ", ".join(unmet))
            for k in unmet:
                next_steps.append(_STEP.get(k, f"Resolve {k}."))
        else:
            label = "FORWARD_VALIDATION_REQUIRED"
            reasons.append("Every historical gate run passed, including the sealed holdout.")
            next_steps.append("Paper-trade it forward (not built yet) before any real decision.")
    result = {
        "verdict": label,
        "reasons": reasons,
        "next_steps": next_steps[:6],
        "never": "No verdict here means a strategy will make money.",
    }
    if fixture:
        result = {
            **result,
            "verdict": "INSUFFICIENT_EVIDENCE",
            "would_be": label,
            "reasons": [
                "SYNTHETIC fixture data — an engineering check, not market evidence.",
                *reasons,
            ],
        }
    return result


_STEP = {
    "WALK_FORWARD": "Walk-forward didn't pass: check whether parameters chosen in the past keep "
    "working in the next window.",
    "COST_STRESS": "The edge doesn't survive higher costs: measure real fills or reduce trading.",
    "PARAMETER_SENSITIVITY": "Results depend on exact parameters: look for a plateau, not a peak.",
    "BOOTSTRAP": "The out-of-sample result can't be told from luck: more sessions are needed.",
    "SELECTION_BIAS": "Many variants were tried: the deflated Sharpe isn't convincing yet.",
    "AMBIGUITY": "Resolve ambiguous 1-minute bars with 1-second or trade data.",
    "TAIL_DEPENDENCE": "A few trades carry the result: check them in the Trade Explorer.",
    "HOLDOUT": "When the spec is final, evaluate the sealed holdout once.",
}


def fitness(
    quality: dict[str, Any],
    contract_findings: list[dict[str, Any]],
    spec: FuturesSpec,
    dataset_symbol: str,
    sessions_observed: int,
    skipped: dict[str, int],
) -> dict[str, Any]:
    """Data Fitness for this strategy on this dataset: FIT, FIT_WITH_LIMITATIONS,
    INSUFFICIENT or INVALID, with every reason listed."""
    reasons: list[dict[str, str]] = []
    status = "FIT"

    def worse(level: str, code: str, text: str) -> None:
        nonlocal status
        order = ["FIT", "FIT_WITH_LIMITATIONS", "INSUFFICIENT", "INVALID"]
        if order.index(level) > order.index(status):
            status = level
        reasons.append({"level": level, "code": code, "message": text})

    if quality.get("status") == "BLOCK":
        worse("INVALID", "QUALITY_BLOCK", "The dataset has blocking quality findings.")
    root = (
        dataset_symbol.split(".")[0].rstrip("0123456789").rstrip("HMUZ") if dataset_symbol else ""
    )
    if (
        dataset_symbol
        and not dataset_symbol.startswith(spec.instrument.product + ".")
        and root != spec.instrument.product
    ):
        worse(
            "INVALID",
            "INSTRUMENT_MISMATCH",
            f"Dataset {dataset_symbol} isn't {spec.instrument.product}.",
        )
    for f in contract_findings:
        if f["level"] == "BLOCK":
            worse("INVALID", f["code"], f["message"])
        elif f["level"] == "WARN":
            worse("FIT_WITH_LIMITATIONS", f["code"], f["message"])
    for f in quality.get("findings", []):
        if f["level"] == "WARN" and f["code"] in (
            "PROVIDER_CONDITION",
            "MISSING_SESSIONS",
            "RTH_GAPS",
            "JUMPS",
        ):
            worse("FIT_WITH_LIMITATIONS", f["code"], f["message"] + f" ({f['count']})")
    intrabar = (
        spec.exits.stop.type != "none"
        or spec.exits.target.type != "none"
        or (getattr(spec.rule, "entry", "") == "stop_through_range")
    )
    if intrabar:
        worse(
            "FIT_WITH_LIMITATIONS",
            "INTRABAR_ORDER",
            "Stops/targets/stop entries on 1-minute bars: the order of high and low within a "
            "minute is unknown — ambiguous bars are resolved conservatively and reported.",
        )
    worse(
        "FIT_WITH_LIMITATIONS",
        "NO_SPREAD",
        "OHLCV has no bid/ask: the spread is inside the slippage assumption.",
    )
    if skipped.get("SKIPPED_ROLL"):
        worse(
            "FIT_WITH_LIMITATIONS",
            "ROLL_SESSIONS",
            f"{skipped['SKIPPED_ROLL']} session(s) skipped: the contract changed inside "
            "the window.",
        )
    if sessions_observed < 40:
        worse(
            "INSUFFICIENT",
            "FEW_SESSIONS",
            f"Only {sessions_observed} sessions with data — at least 40 are needed.",
        )
    return {"status": status, "reasons": reasons}


def session_minutes(windows: list[Window]) -> int:
    return sum((w.end_ns - w.start_ns) // MINUTE for w in windows)
