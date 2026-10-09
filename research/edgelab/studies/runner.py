"""Runs the pre-registered event studies and applies the gates (RESEARCH_PROTOCOL.md 3-4).

``run_all`` does, in this order:

1. provenance guard: a context that is not a manifest dataset (``StudyContext.is_real``)
   may only write to explicitly given paths outside ``research/`` (software tests), so a
   synthetic number can never reach the repository's outputs, registry or journal;
2. pre-registration guard: a study without its journal entry is refused; a study whose
   primary test is already in the test registry for the same dataset is refused unless
   a written re-run reason is given (the re-run's tests are registered again and count);
3. each study: detector, controls, primary test (10,000 date-cluster resamples), the G6
   twin test and the G3 economic test when they are separate tests; every one of them
   is appended to the test registry;
4. Benjamini-Hochberg over the WHOLE registry (every family, every run so far) -> G2;
5. gates by study type, the family fallback, one candidate per hypothesis and at most
   five per family;
6. outputs: ``<out>/<ID>/`` (events, controls, descriptive horizon and condition tables,
   result.json), ``<out>/SUMMARY.md``, journal results, rows in REJECTED_IDEAS.md.

Study types (``configs/event_studies.yaml``): E = event effect, may become a candidate,
gates G1-G6; I = incremental comparison, never a candidate, G1 G2 G4 (smallest group) G5;
M = mechanism / magnitude / filter, never a candidate, G1 G2 G4 (smallest group) G5.
"""

from __future__ import annotations

import datetime as dt
import json
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .. import journal
from ..config import CONFIG_DIR, RESEARCH_ROOT, RESULTS_DIR, load_research_config, load_yaml
from ..data.rolls import contract_changes, equity_index_roll_dates, roll_exclusion_dates
from ..experiments import FAMILY_DIRS, git_commit
from ..stats.bootstrap import UNCERTAIN
from ..stats.multiple_testing import TestRegistry
from .catalog import HORIZONS, STUDIES, StudyData, diff_test, seed_of
from .context import StudyContext
from .inference import evaluate, half_split

CONFIG_PATH = CONFIG_DIR / "event_studies.yaml"
OUT_ROOT = RESEARCH_ROOT / "event_studies"
REJECTED = RESEARCH_ROOT / "REJECTED_IDEAS.md"
GATES = ("G1", "G2", "G3", "G4", "G5", "G6")
TYPE_GATES = {"E": GATES, "I": ("G1", "G2", "G4", "G5"), "M": ("G1", "G2", "G4", "G5")}
TYPE_TEXT = {
    "E": "event effect; may become a strategy candidate",
    "I": "incremental comparison; refines a candidate, never a candidate by itself",
    "M": "mechanism / magnitude / filter; not a signed trade return",
}
Z95 = 1.959963984540054
CANDIDATE_DECISIONS = ("CANDIDATE", "TWIN_EXPLAINS")
JOURNAL_DECISION = {
    "CANDIDATE": "KEEP",
    "TWIN_EXPLAINS": "MODIFY",
    "CANDIDATE_VARIANT": "KEEP",
    "CANDIDATE_CAPPED": "KEEP",
    "PROMOTED_WITHOUT_SUPPORT": "KEEP",
    "REJECTED": "REJECT",
    "SUPPORTED": "KEEP",
    "NOT_SUPPORTED": "REJECT",
    "NOT_TESTABLE": "REJECT",
}
DECISION_TEXT = {
    "CANDIDATE": "passes every gate of its type; becomes a strategy candidate",
    "TWIN_EXPLAINS": "passes G1-G5 but not G6: the simpler twin explains the effect, so the twin version becomes the candidate",
    "CANDIDATE_VARIANT": "passes its gates; another variant of the same hypothesis ranks higher and represents it",
    "CANDIDATE_CAPPED": "passes its gates; outside the five best of its family (protocol 4)",
    "PROMOTED_WITHOUT_SUPPORT": "no hypothesis of the family passed; best available by the ranking rule, promoted without statistical support (final status at best INCONCLUSIVE unless VAL and TEST support it)",
    "REJECTED": "fails at least one gate",
    "SUPPORTED": "passes the gates of its type; informs candidates and filters, never a candidate itself",
    "NOT_SUPPORTED": "fails at least one gate of its type",
    "NOT_TESTABLE": "the primary test could not be computed on this data",
}
NOT_RUN = ("BLOCKED", "ERROR")


# =======================================================================================
# configuration
# =======================================================================================
def load_config(path: str | Path | None = None) -> dict:
    """The frozen study definitions; checks that YAML and catalog describe the same studies."""
    cfg = load_yaml(path or CONFIG_PATH)
    studies = cfg.get("studies") or {}
    missing, extra = sorted(set(STUDIES) - set(studies)), sorted(set(studies) - set(STUDIES))
    if missing or extra:
        raise ValueError(f"event_studies.yaml and catalog disagree: no YAML for {missing}, no code for {extra}")
    for sid, s in studies.items():
        lacking = [k for k in ("hypothesis", "title", "type", "rank", "norm", "primary", "twin", "params", "expected") if k not in s]
        if lacking:
            raise ValueError(f"{sid}: missing {lacking}")
        if s["type"] not in TYPE_GATES:
            raise ValueError(f"{sid}: type must be one of {sorted(TYPE_GATES)}")
    return cfg


def study_spec(cfg: dict, sid: str) -> dict:
    return {**cfg["studies"][sid], "id": sid, "_cfg": cfg, "control_draws": int(cfg["common"]["control_draws"])}


def context_params(cfg: dict) -> dict:
    """``StudyContext.params`` from the YAML (the YAML, not the code defaults, is binding)."""
    return {"bar_minutes": int(cfg["common"]["bar_minutes"]), **(cfg["common"].get("context") or {})}


def family_of(sid: str) -> str:
    return FAMILY_DIRS[sid[0]]


HYPOTHESIS_FILES = {"A": "hypotheses/HYPOTHESES_AUCTION.md", "B": "hypotheses/HYPOTHESES_ICT.md", "C": "hypotheses/HYPOTHESES_PRICE_ACTION.md"}
DATASET_TEXT = ("ES 1-minute OHLCV, DEV period only (first 50% of trading dates; split frozen in results/split_ledger.json "
                "when the data is registered). No market data existed at pre-registration.")


def preregister_all(cfg: dict, *, path: Path = journal.JOURNAL, dataset_text: str = DATASET_TEXT) -> list[str]:
    """Journal entry for every study not yet registered: hypothesis, detector definition
    (the study function's docstring), parameters, primary test and expected outcome."""
    common = {k: cfg["common"][k] for k in ("bar_minutes", "control_draws", "n_boot", "fdr_q", "min_events", "seed", "cost_scenario")}
    done = []
    for sid, s in cfg["studies"].items():
        if journal.is_preregistered(sid, path):
            continue
        prim = s["primary"]
        rules = " ".join((STUDIES[sid].__doc__ or "").split())
        journal.preregister(
            sid, s["title"],
            hypothesis=f"{s['hypothesis']} ({HYPOTHESIS_FILES[sid[0]]})",
            reason=f"Event study of type {s['type']} ({TYPE_TEXT[s['type']]}); pre-data rank {s['rank']} in family {family_of(sid)}.",
            rules=(f"{rules} Primary test: {prim['metric']} against {prim['control']}, unit {s['norm']}; G6 twin: "
                   f"{s['twin'] or 'none pre-registered'}; gates {', '.join(TYPE_GATES[s['type']])} (RESEARCH_PROTOCOL.md 4)."),
            parameters=json.dumps({"params": s["params"], "common": common}, sort_keys=True, default=str),
            dataset=dataset_text, expected=s["expected"], path=path,
        )
        done.append(sid)
    return done


def roll_exclusions(
    bars: pd.DataFrame, labels: pd.DataFrame, adjustment: str, roll_cfg: dict | None, trading_dates: pd.DatetimeIndex
) -> set:
    """Trading dates that are not event days and supply no prior levels (protocol 2.3).

    * difference / ratio adjusted series: none, levels are used as given;
    * a ``contract`` column: the trading date of every contract change;
    * otherwise (unadjusted or unknown) the conventional roll date (third Friday minus
      ``roll_offset_days``) and the next session, because the vendor's switch day is
      not known exactly.
    """
    if adjustment in ("difference", "ratio"):
        return set()
    if "contract" in bars.columns:
        ch = contract_changes(bars)
        if ch.empty:
            return set()
        td = pd.DatetimeIndex(labels.loc[pd.DatetimeIndex(ch["ts"]), "trading_date"])
        return roll_exclusion_dates([t.date() for t in td], trading_dates, sessions_after=0)
    if roll_cfg and roll_cfg.get("expiry") == "third_friday" and len(trading_dates):
        rd = equity_index_roll_dates(
            trading_dates.min().date(), trading_dates.max().date(),
            tuple(roll_cfg.get("months", (3, 6, 9, 12))), int(roll_cfg.get("roll_offset_days", 8)),
        )
        return roll_exclusion_dates(rd, trading_dates, sessions_after=1)
    return set()


# =======================================================================================
# one study
# =======================================================================================
@dataclass
class StudyOutcome:
    sid: str
    family: str
    type: str
    hypothesis: str
    title: str
    rank: int
    status: str = "OK"
    n_events: int = 0
    n_event_dates: int = 0
    primary: dict | None = None
    twin: dict | None = None
    econ: dict | None = None
    twin_mode: str = "none"
    halves: tuple[float, float] = (np.nan, np.nan)
    hurdle: float = np.nan
    econ_effect: float = np.nan
    econ_se: float = np.nan
    p_bh: float = np.nan
    score: float = np.nan
    gates: dict = field(default_factory=dict)
    decision: str = ""
    notes: list = field(default_factory=list)
    test_ids: dict = field(default_factory=dict)

    @property
    def failed_gates(self) -> list[str]:
        return [g for g in TYPE_GATES[self.type] if self.gates.get(g) is False]


def cost_hurdle(ctx: StudyContext, events: pd.DataFrame, norm: np.ndarray | None) -> float:
    """Median over events of the round-trip BASE cost divided by the effect's unit."""
    if norm is None:
        return np.nan
    norm = np.asarray(norm, float)
    if len(norm) == len(events) and "pos" in events.columns and "day" in events.columns:
        pos = events["pos"].to_numpy(np.int64)
        day = events["day"].to_numpy(np.int64)
        price = np.where(pos >= 0, ctx.c[np.clip(pos, 0, ctx.n5 - 1)], ctx.rth_open[np.clip(day, 0, ctx.n_days - 1)])
    else:
        price = np.full(len(norm), np.nanmedian(ctx.c))
    cost = np.asarray(ctx.cost_points(price), float)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = cost / norm
    r = r[np.isfinite(r) & (norm > 0)]
    return float(np.median(r)) if len(r) else np.nan


def run_one(ctx: StudyContext, cfg: dict, sid: str, *, n_boot: int) -> tuple[StudyOutcome, StudyData | None]:
    """Detector, controls and the decision tests of one study (nothing registered yet)."""
    s = cfg["studies"][sid]
    out = StudyOutcome(sid, family_of(sid), s["type"], s["hypothesis"], s["title"], int(s["rank"]))
    try:
        data = STUDIES[sid](ctx, study_spec(cfg, sid))
    except Exception:  # an error is reported, never turned into a rejection
        out.status = "ERROR"
        out.notes.append(traceback.format_exc(limit=6))
        return out, None
    out.status = data.status
    out.notes += list(data.notes)
    ev = data.events
    out.n_events = int(len(ev))
    out.n_event_dates = int(pd.Series(ev["date"]).nunique()) if "date" in ev.columns and len(ev) else 0
    if out.status != "OK":
        return out, data
    if data.primary is None or len(data.primary.value) == 0:
        out.status = "NO_EVENTS"
        return out, data
    seed = seed_of(ctx, sid, 100)
    out.primary = evaluate(data.primary, n_boot=n_boot, seed=seed)
    out.halves = tuple(float(x) for x in half_split(data.primary))
    if data.twin is not None:
        out.twin_mode = "separate"
        out.twin = evaluate(data.twin, n_boot=n_boot, seed=seed + 1)
    elif s.get("twin") == "primary":
        out.twin_mode = "primary"
    if data.econ is not None:
        out.econ = evaluate(data.econ, n_boot=n_boot, seed=seed + 2)
    base = out.econ or out.primary
    out.econ_effect = float(data.econ_scale * base["point"])
    out.econ_se = float(abs(data.econ_scale) * base["se"])
    out.hurdle = cost_hurdle(ctx, ev, data.hurdle_norm)
    return out, data


def apply_gates(out: StudyOutcome, *, q: float, min_events: int) -> dict:
    g: dict = {k: None for k in GATES}
    pr = out.primary
    if out.status != "OK" or pr is None or not np.isfinite(pr["point"]):
        return g
    g["G1"] = bool(pr["point"] > 0 and pr["lo"] > 0)
    g["G2"] = bool(np.isfinite(out.p_bh) and out.p_bh <= q)
    nb = pr["n_by_group"]
    n_min = nb[next(iter(nb))] if out.type == "E" else min(nb.values())
    g["G4"] = bool(n_min >= min_events)
    h1, h2 = out.halves
    g["G5"] = bool(np.isfinite(h1) and np.isfinite(h2) and h1 > 0 and h2 > 0)
    if out.type == "E":
        g["G3"] = bool(np.isfinite(out.econ_effect) and np.isfinite(out.hurdle) and out.econ_effect >= out.hurdle)
        if out.twin_mode == "primary":
            g["G6"] = g["G1"]
        elif out.twin_mode == "separate":
            tw = out.twin
            g["G6"] = bool(tw is not None and np.isfinite(tw["point"]) and tw["point"] > 0 and tw["lo"] > 0)
    return g


def decide(out: StudyOutcome) -> str:
    if out.status != "OK" or out.primary is None or not np.isfinite(out.primary["point"]):
        return "NOT_TESTABLE"
    g = out.gates
    if out.type == "E":
        core = all(g[k] for k in ("G1", "G2", "G3", "G4", "G5"))
        if core and g["G6"] is False:
            return "TWIN_EXPLAINS"
        return "CANDIDATE" if core else "REJECTED"
    return "SUPPORTED" if all(g[k] for k in TYPE_GATES[out.type]) else "NOT_SUPPORTED"


def ranking_score(out: StudyOutcome) -> float:
    """Unit-free ranking for the family fallback and the candidate cap: the lower 95% bound
    of the net-of-cost effect in standard errors, (effect - hurdle) / se - 1.96. Effects of
    different studies are in different units (ATR, ATR_d, R, probability), so their raw
    lower CI bounds are not comparable. Without a hurdle: lower bound / se."""
    if out.type != "E" or out.primary is None or not np.isfinite(out.primary["point"]):
        return np.nan
    if np.isfinite(out.hurdle) and np.isfinite(out.econ_se) and out.econ_se > 0:
        return float((out.econ_effect - out.hurdle) / out.econ_se - Z95)
    pr = out.primary
    return float(pr["lo"] / pr["se"]) if np.isfinite(pr["se"]) and pr["se"] > 0 else np.nan


def select_candidates(outs: list[StudyOutcome], max_per_family: int) -> None:
    """One candidate per hypothesis (its best variant), at most ``max_per_family`` per family;
    if a family has none, its best E study is promoted without statistical support."""
    key = lambda o: (-o.score if np.isfinite(o.score) else np.inf, o.rank, o.sid)  # noqa: E731
    for fam in dict.fromkeys(o.family for o in outs):
        e_ok = [o for o in outs if o.family == fam and o.type == "E" and o.status == "OK" and np.isfinite(o.score)]
        passed = sorted((o for o in e_ok if o.decision in CANDIDATE_DECISIONS), key=key)
        if passed:
            reps: dict[str, StudyOutcome] = {}
            for o in passed:
                if o.hypothesis in reps:
                    o.decision = "CANDIDATE_VARIANT"
                    o.notes.append(f"variant of {o.hypothesis}; represented by {reps[o.hypothesis].sid}")
                else:
                    reps[o.hypothesis] = o
            for i, o in enumerate(sorted(reps.values(), key=key)):
                if i >= max_per_family:
                    o.decision = "CANDIDATE_CAPPED"
        elif e_ok:
            best = sorted(e_ok, key=key)[0]
            best.decision = "PROMOTED_WITHOUT_SUPPORT"


# =======================================================================================
# descriptive tables (never decision tests; never registered)
# =======================================================================================
def _describe(x: np.ndarray, prefix: str) -> dict:
    x = x[np.isfinite(x)]
    if not len(x):
        return {f"{prefix}_n": 0}
    q = np.quantile(x, [0.10, 0.25, 0.75, 0.90])
    return {
        f"{prefix}_n": int(len(x)), f"{prefix}_mean": float(x.mean()), f"{prefix}_median": float(np.median(x)),
        f"{prefix}_sd": float(x.std(ddof=1)) if len(x) > 1 else np.nan,
        f"{prefix}_q10": q[0], f"{prefix}_q25": q[1], f"{prefix}_q75": q[2], f"{prefix}_q90": q[3],
        f"{prefix}_p_pos": float((x > 0).mean()),
    }


def horizon_table(ev: pd.DataFrame, ct: pd.DataFrame | None, *, n_boot: int, seed: int) -> pd.DataFrame:
    """Distribution of every outcome for events and controls, difference, Cohen's d and a
    descriptive date-cluster CI of the difference. Only the registered primary test decides."""
    rows = []
    for col in [*(f"r{h}" for h in HORIZONS), "mfe12", "mae12", "reod"]:
        if col not in ev.columns or not len(ev):
            continue
        x = ev[col].to_numpy(float)
        row = {"outcome": col, **_describe(x, "ev")}
        if ct is not None and len(ct) and col in ct.columns:
            y = ct[col].to_numpy(float)
            row.update(_describe(y, "ct"))
            xf, yf = x[np.isfinite(x)], y[np.isfinite(y)]
            if len(xf) > 1 and len(yf) > 1:
                d = xf.mean() - yf.mean()
                sp = np.sqrt(((len(xf) - 1) * xf.var(ddof=1) + (len(yf) - 1) * yf.var(ddof=1)) / (len(xf) + len(yf) - 2))
                r = evaluate(diff_test(ev, ct, col, f"descriptive {col}"), n_boot=n_boot, seed=seed)
                row.update({"diff": d, "cohen_d": d / sp if sp > 0 else np.nan, "diff_ci_lo_desc": r["lo"], "diff_ci_hi_desc": r["hi"]})
        rows.append(row)
    return pd.DataFrame(rows)


def metric_column(spec: dict, ev: pd.DataFrame, ct: pd.DataFrame | None) -> str | None:
    tok = str(spec["primary"]["metric"]).split()[0]
    cols = set(ev.columns) & (set(ct.columns) if ct is not None and len(ct) else set(ev.columns))
    for c in (tok, "hit", "r12"):
        if c in cols:
            return c
    return None


def condition_table(data: StudyData, col: str | None) -> pd.DataFrame:
    """Point estimates of the primary outcome by pre-registered condition (descriptive)."""
    ev, ct = data.events, data.controls
    rows = []
    if col is None or not len(ev):
        return pd.DataFrame(rows)
    for cond in data.conditions:
        if cond not in ev.columns:
            continue
        for lvl, g in ev.groupby(cond, observed=True):
            row = {"condition": cond, "level": str(lvl), "n_ev": int(g[col].notna().sum()), "mean_ev": float(g[col].mean())}
            if ct is not None and len(ct) and cond in ct.columns:
                gc = ct[ct[cond] == lvl]
                row.update({"n_ct": int(gc[col].notna().sum()), "mean_ct": float(gc[col].mean()) if len(gc) else np.nan})
                row["diff"] = row["mean_ev"] - row["mean_ct"]
            rows.append(row)
    return pd.DataFrame(rows)


# =======================================================================================
# the run
# =======================================================================================
@dataclass(frozen=True)
class RunPaths:
    out_root: Path
    registry: Path
    journal: Path
    rejected: Path


def _inside(p: Path, root: Path) -> bool:
    try:
        Path(p).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def guard_paths(ctx: StudyContext, out_root=None, registry=None, journal_path=None, rejected=None) -> RunPaths:
    given = {"out_root": out_root, "registry": registry, "journal": journal_path, "rejected": rejected}
    if not ctx.is_real:
        missing = [k for k, v in given.items() if v is None]
        inside = [k for k, v in given.items() if v is not None and _inside(Path(v), RESEARCH_ROOT)]
        if missing or inside:
            raise PermissionError(
                f"provenance {ctx.provenance!r} is not a manifest dataset: results may only go to explicit paths "
                f"outside {RESEARCH_ROOT} (missing: {missing}, inside research/: {inside})"
            )
    dflt = {"out_root": OUT_ROOT, "registry": RESULTS_DIR / "test_registry.csv", "journal": journal.JOURNAL, "rejected": REJECTED}
    return RunPaths(**{k: Path(v) if v is not None else dflt[k] for k, v in given.items()})


def already_registered(reg: TestRegistry, sid: str, provenance: str) -> bool:
    df = reg.load()
    if df.empty:
        return False
    notes = df["notes"].astype(str)
    return bool(((df["experiment_id"].astype(str) == sid) & notes.str.contains("role=primary", regex=False)
                 & notes.str.contains(f"prov={provenance}", regex=False)).any())


def _register(reg: TestRegistry, out: StudyOutcome, spec: dict, ctx: StudyContext, period: str, commit: str) -> None:
    for role, res in (("primary", out.primary), ("twin", out.twin), ("econ", out.econ)):
        if res is None:
            continue
        out.test_ids[role] = reg.register(
            experiment_id=out.sid, hypothesis_id=out.hypothesis, family=out.family, description=res["label"],
            metric=spec["primary"]["metric"] if role == "primary" else role, horizon="", n=res["n_obs"],
            effect=res["point"], p_value=res["p_value"], data_period=period, instrument=ctx.instrument.symbol,
            git_commit=commit, notes=f"role={role};type={out.type};prov={ctx.provenance}",
        )


def run_all(
    ctx: StudyContext,
    cfg: dict | None = None,
    *,
    ids: list[str] | None = None,
    out_root: str | Path | None = None,
    registry: str | Path | None = None,
    journal_path: str | Path | None = None,
    rejected: str | Path | None = None,
    n_boot: int | None = None,
    n_boot_desc: int | None = None,
    rerun_reason: str | None = None,
    period_label: str = "",
) -> list[StudyOutcome]:
    cfg = cfg or load_config()
    com = cfg["common"]
    ids = list(ids or cfg["studies"])
    paths = guard_paths(ctx, out_root, registry, journal_path, rejected)
    reg = TestRegistry(paths.registry)
    q, min_events = float(com["fdr_q"]), int(com["min_events"])
    n_boot = int(n_boot or com["n_boot"])
    n_boot_desc = int(n_boot_desc or com["n_boot_descriptive"])
    max_per_family = int(load_research_config()["development_budget"]["max_candidates_per_family"])

    for sid in ids:
        if sid not in cfg["studies"]:
            raise KeyError(f"{sid} is not a registered study")
        if not journal.is_preregistered(sid, paths.journal):
            raise PermissionError(f"{sid} has no pre-registration in {paths.journal}; run scripts/preregister_studies.py first")
        if already_registered(reg, sid, ctx.provenance) and not rerun_reason:
            raise PermissionError(f"{sid} already ran on {ctx.provenance}; a re-run needs a written reason (it is registered again)")
    if rerun_reason:
        journal.note(f"RE-RUN {' '.join(ids)}", f"Reason: {rerun_reason}\n\nDataset: {ctx.provenance}. The re-run's tests are "
                     "registered again and count toward the multiple-testing burden.", path=paths.journal)

    commit = git_commit()
    outs: list[StudyOutcome] = []
    datas: dict[str, StudyData | None] = {}
    for sid in ids:
        out, data = run_one(ctx, cfg, sid, n_boot=n_boot)
        if out.primary is not None:
            _register(reg, out, cfg["studies"][sid], ctx, period_label, commit)
        outs.append(out)
        datas[sid] = data

    adj = reg.adjusted(q)
    pbh = dict(zip(adj["test_id"].astype(str), pd.to_numeric(adj["p_bh_global"], errors="coerce"))) if len(adj) else {}
    for o in outs:
        o.p_bh = float(pbh.get(o.test_ids.get("primary", ""), np.nan))
        o.gates = apply_gates(o, q=q, min_events=min_events)
        o.decision = decide(o)
        o.score = ranking_score(o)
    full_run = set(ids) == set(cfg["studies"])
    if full_run:  # the fallback and the cap compare a family's studies, so they need all of them
        select_candidates(outs, max_per_family)

    paths.out_root.mkdir(parents=True, exist_ok=True)
    for o in outs:
        _write_study(paths.out_root, o, datas[o.sid], cfg, ctx, n_boot=n_boot, n_boot_desc=n_boot_desc, commit=commit)
    write_summary(paths.out_root / ("SUMMARY.md" if full_run else f"SUMMARY_partial_{'_'.join(ids)[:80]}.md"), outs, ctx,
                  period_label=period_label, n_tests=len(adj), q=q, n_boot=n_boot, commit=commit, ids_run=ids,
                  all_ids=list(cfg["studies"]))
    for o in outs:
        if o.status not in NOT_RUN:
            journal.record_result(o.sid, actual=result_text(o), interpretation=interpretation_text(o),
                                  decision=JOURNAL_DECISION[o.decision], path=paths.journal)
    append_rejections(outs, paths.rejected, ctx.provenance)
    return outs


# =======================================================================================
# writing
# =======================================================================================
def _json_default(v):
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (pd.Timestamp, dt.date, np.datetime64)):
        return str(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    return str(v)


def _clean(v):
    if isinstance(v, float) and not np.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def _save_frame(df: pd.DataFrame, path: Path) -> None:
    try:
        df.to_parquet(path, index=False)
    except Exception:
        df.to_csv(path.with_suffix(".csv"), index=False)


def _write_study(root: Path, o: StudyOutcome, data: StudyData | None, cfg: dict, ctx: StudyContext, *, n_boot: int,
                 n_boot_desc: int, commit: str) -> None:
    d = root / o.sid
    d.mkdir(parents=True, exist_ok=True)
    spec = {k: v for k, v in cfg["studies"][o.sid].items()}
    if data is not None:
        if len(data.events):
            _save_frame(data.events, d / "events.parquet")
        if data.controls is not None and len(data.controls):
            _save_frame(data.controls, d / "controls.parquet")
        hz = horizon_table(data.events, data.controls, n_boot=n_boot_desc, seed=seed_of(ctx, o.sid, 200))
        if len(hz):
            hz.to_csv(d / "horizons_descriptive.csv", index=False)
        cond = condition_table(data, metric_column(spec, data.events, data.controls))
        if len(cond):
            cond.to_csv(d / "conditions_descriptive.csv", index=False)
        for name, tbl in data.tables.items():
            tbl.to_csv(d / f"table_{name}.csv", index=False)
    res = {
        **asdict(o), "failed_gates": o.failed_gates, "spec": spec, "provenance": ctx.provenance,
        "evidence_class": "PROXY" if ctx.instrument.is_proxy else ("CME" if ctx.is_real else "SYNTHETIC-TEST"),
        "instrument": ctx.instrument.symbol, "n_boot": n_boot, "git_commit": commit,
        "written_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    (d / "result.json").write_text(json.dumps(_clean(json.loads(json.dumps(res, default=_json_default))), indent=2))


def _f(x: float, nd: int = 3) -> str:
    return "–" if x is None or not np.isfinite(x) else f"{x:+.{nd}f}"


def _g(v) -> str:
    return "n/a" if v is None else ("✓" if v else "✗")


def result_text(o: StudyOutcome) -> str:
    if o.primary is None:
        return f"status {o.status}; {o.n_events} event rows. " + " ".join(n.splitlines()[0] for n in o.notes if n)
    pr = o.primary
    parts = [
        f"{pr['label']}: {_f(pr['point'], 4)} [{_f(pr['lo'], 4)}, {_f(pr['hi'], 4)}], p = {pr['p_value']:.4f}, "
        f"BH-adjusted p = {o.p_bh:.4f}; n by group {pr['n_by_group']} on {pr['n_dates']} dates",
        f"halves {_f(o.halves[0], 4)} / {_f(o.halves[1], 4)}",
    ]
    if o.type == "E":
        parts.append(f"economic effect {_f(o.econ_effect, 4)} vs cost hurdle {_f(o.hurdle, 4)}")
        if o.twin is not None:
            parts.append(f"twin {o.twin['label']}: {_f(o.twin['point'], 4)} [{_f(o.twin['lo'], 4)}, {_f(o.twin['hi'], 4)}]")
    parts.append("gates " + ", ".join(f"{g} {_g(o.gates.get(g))}" for g in TYPE_GATES[o.type]))
    return "; ".join(parts)


def interpretation_text(o: StudyOutcome) -> str:
    txt = DECISION_TEXT[o.decision]
    if o.failed_gates:
        txt += f" (failed: {', '.join(o.failed_gates)})"
    if o.primary is not None and np.isfinite(o.primary["lo"]) and o.primary["lo"] <= 0 <= o.primary["hi"]:
        txt += ". The 95% CI of the effect includes 0: the effect remains statistically uncertain"
    return txt + "."


def write_summary(path: Path, outs: list[StudyOutcome], ctx: StudyContext, *, period_label: str, n_tests: int, q: float,
                  n_boot: int, commit: str, ids_run: list[str], all_ids: list[str]) -> None:
    proxy = ctx.instrument.is_proxy
    cls = "PROXY (crypto; never CME evidence)" if proxy else ("CME" if ctx.is_real else "SYNTHETIC SOFTWARE TEST - NOT EVIDENCE")
    lines = [
        "# Event studies - summary",
        "",
        f"- Dataset: `{ctx.provenance}`, instrument {ctx.instrument.symbol}, evidence class **{cls}**",
        f"- Period: {period_label or 'n/a'}; event days: {int(ctx.day_ok.sum())}",
        f"- Run (UTC): {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}, code {commit}",
        f"- Studies in this run: {len(ids_run)} of {len(all_ids)} registered",
        f"- Tests in the registry (all runs, all families): {n_tests}; Benjamini-Hochberg q = {q} over all of them (G2)",
        f"- Primary tests: date-cluster bootstrap, {n_boot:,} resamples, 95% percentile CI, two-sided p",
        "",
    ]
    if proxy:
        lines += ["> **PROXY evidence.** Crypto results never enter a CME conclusion.", ""]
    if set(ids_run) != set(all_ids):
        lines += ["> **Partial run.** The family fallback and the five-per-family cap compare all studies of a family "
                  "and are applied only on a full run.", ""]
    lines += ["## Candidates", ""]
    any_c = False
    for fam in dict.fromkeys(o.family for o in outs):
        fam_rows = [o for o in outs if o.family == fam and o.decision in (*CANDIDATE_DECISIONS, "PROMOTED_WITHOUT_SUPPORT")]
        for o in sorted(fam_rows, key=lambda o: -o.score if np.isfinite(o.score) else np.inf):
            any_c = True
            lines.append(f"- **{o.sid}** ({fam}, {o.hypothesis}) {o.title}: {o.decision} — {DECISION_TEXT[o.decision]}; "
                         f"ranking score {_f(o.score, 2)}")
    if not any_c:
        lines.append("- none")
    lines += [
        "",
        "## All studies",
        "",
        "| ID | type | title | events (dates) | effect [95% CI] | p | p_BH | econ / hurdle | halves | twin effect [CI] | "
        + " | ".join(GATES) + " | decision |",
        "|" + "---|" * (11 + len(GATES)),
    ]
    for o in outs:
        pr = o.primary
        eff = f"{_f(pr['point'])} [{_f(pr['lo'])}, {_f(pr['hi'])}]" if pr else o.status
        p = f"{pr['p_value']:.4f}" if pr and np.isfinite(pr["p_value"]) else "–"
        pb = f"{o.p_bh:.4f}" if np.isfinite(o.p_bh) else "–"
        eh = f"{_f(o.econ_effect)} / {o.hurdle:.3f}" if o.type == "E" and np.isfinite(o.hurdle) else "–"
        hv = f"{_f(o.halves[0])} / {_f(o.halves[1])}" if pr else "–"
        tw = (f"{_f(o.twin['point'])} [{_f(o.twin['lo'])}, {_f(o.twin['hi'])}]" if o.twin else
              ("= primary" if o.twin_mode == "primary" else "–"))
        gates = " | ".join(_g(o.gates.get(g)) if g in TYPE_GATES[o.type] else "" for g in GATES)
        lines.append(f"| {o.sid} | {o.type} | {o.title} | {o.n_events} ({o.n_event_dates}) | {eff} | {p} | {pb} | {eh} | {hv} | "
                     f"{tw} | {gates} | {o.decision} |")
    lines += [
        "",
        "Gates: G1 effect > 0 and CI excludes 0; G2 BH-adjusted p <= q over the whole registry; G3 economic effect >= "
        "median round-trip BASE cost in the effect's unit; G4 >= 100 events (I and M: smallest group); G5 effect > 0 in "
        "both chronological halves; G6 effect over the simpler twin > 0 with CI excluding 0 ('= primary': the primary "
        "control is the twin). Effects are oriented so that positive is the direction the hypothesis predicts.",
        "",
        f"Whenever a CI includes 0 the effect remains statistically uncertain. For trade expectancy: \"{UNCERTAIN}\"",
        "",
    ]
    notes = [(o.sid, n) for o in outs for n in o.notes if n]
    if notes:
        lines += ["## Notes", ""] + [f"- {sid}: {n.strip().splitlines()[-1] if 'Traceback' in n else n.strip()}" for sid, n in notes]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


REJECTED_HEADER = (
    "| study | hypothesis | family | type | result | reason | dataset |\n"
    "|---|---|---|---|---|---|---|"
)
REJECTED_PLACEHOLDER = "_None yet: no real data has been available._"


def append_rejections(outs: list[StudyOutcome], path: Path, provenance: str) -> None:
    rows = [o for o in outs if o.decision in ("REJECTED", "NOT_SUPPORTED", "NOT_TESTABLE") and o.status not in NOT_RUN]
    if not rows:
        return
    text = path.read_text(encoding="utf-8") if path.exists() else "# REJECTED_IDEAS\n\n## Results-based rejections\n\n" + REJECTED_PLACEHOLDER + "\n"
    if REJECTED_PLACEHOLDER in text:
        text = text.replace(REJECTED_PLACEHOLDER, REJECTED_HEADER)
    elif REJECTED_HEADER.splitlines()[0] not in text:
        text = text.rstrip("\n") + "\n\n" + REJECTED_HEADER
    add = []
    for o in rows:
        pr = o.primary
        res = f"{_f(pr['point'])} [{_f(pr['lo'])}, {_f(pr['hi'])}], p_BH {o.p_bh:.3f}" if pr else o.status
        reason = f"failed {', '.join(o.failed_gates)}" if o.failed_gates else o.status
        add.append(f"| {o.sid} {o.title} | {o.hypothesis} | {o.family} | {o.type} | {res} | {reason} | `{provenance}` |")
    path.write_text(text.rstrip("\n") + "\n" + "\n".join(add) + "\n", encoding="utf-8")
