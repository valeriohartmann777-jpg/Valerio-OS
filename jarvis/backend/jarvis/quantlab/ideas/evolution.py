"""Bounded strategy evolution: reasoned variants, every trial counted, the holdout once.

Loop (per iteration, within an immutable budget):

    diagnose  CIPHER   deterministic diagnosis of the parent's development results
    propose   CIPHER   ≤ k variants as explicit change sets + mechanism + falsifiable
                       prediction (Claude; checked: allowed paths only, valid spec,
                       not a duplicate, within the variant/combination budget)
    test      ARCHIVE/CIPHER/SENTINEL  each variant → new immutable version (parent
                       link) → validation run with the holdout sealed → independent
                       audit → one append-only trial record
    compare   CIPHER   Pareto view (OOS net ↑, OOS trades ↑, drawdown ↓, complexity ↓)
                       with the family's deflated Sharpe — never a single "best" score
    holdout   owner    lock one candidate; its sealed holdout is evaluated once.
                       Anything tested on this family afterwards is marked
                       post-holdout and can't claim independent out-of-sample evidence.

The development data are the dataset's in-sample and out-of-sample segments; no
step here reads holdout results before the owner locks a candidate.
"""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

from jarvis.quantlab.futures.spec import FuturesSpecError, describe, parse
from jarvis.quantlab.ideas import agents
from jarvis.quantlab.ideas.catalog import apply_patch, get_path
from jarvis.util import new_id

if TYPE_CHECKING:
    from jarvis.quantlab.ideas.missions import MissionService

COMMON_PATHS = (
    "exits.stop.type",
    "exits.stop.ticks",
    "exits.target.type",
    "exits.target.value",
    "session.entry_cutoff",
    "filters.min_range_ticks",
    "filters.max_range_ticks",
    "filters.entry_after",
    "filters.prior_close_bias",
    "filters.max_gap_ticks",
)
RULE_PATHS = {
    "opening_range_breakout": ("range_minutes", "entry", "direction", "buffer_ticks"),
    "ma_crossover": ("fast", "slow", "direction", "bar_minutes"),
    "level_sweep_reclaim": (
        "range_minutes",
        "sides",
        "sweep_min_ticks",
        "reclaim",
        "reclaim_within_bars",
        "entry_buffer_ticks",
    ),
    "opening_range_retest": (
        "range_minutes",
        "direction",
        "breakout_buffer_ticks",
        "retest_within_bars",
        "retest_tolerance_ticks",
    ),
}
PER_ROUND = 3
NOT_EVOLVABLE = ("SOURCE_UNAVAILABLE", "RULES_UNCLEAR", "DATA_INSUFFICIENT", "SIMULATION_INVALID")


def allowed_paths(rule_type: str) -> list[str]:
    return [f"rule.{p}" for p in RULE_PATHS[rule_type]] + list(COMMON_PATHS)


def complexity(base: dict[str, Any], spec: dict[str, Any]) -> int:
    """Fields changed from the source's baseline + active filters (each adds a degree of freedom)."""
    changed = sum(
        1
        for path in {*allowed_paths(spec["rule"]["type"]), *allowed_paths(base["rule"]["type"])}
        if get_path(base, path) != get_path(spec, path)
    )
    return changed + len([k for k, v in (spec.get("filters") or {}).items() if v])


async def create(
    svc: MissionService, parent_id: str, budget: dict[str, Any] | None
) -> dict[str, Any]:
    from jarvis.quantlab.ideas.missions import MissionError, _hash, check_budget

    parent = await svc.store.get(parent_id)
    if parent is None or parent["kind"] != "idea":
        raise MissionError("NOT_FOUND", "Evolution starts from a completed research mission.", 404)
    if (
        parent["state"] != "COMPLETE"
        or parent.get("verdict") in NOT_EVOLVABLE
        or not parent["refs"].get("run_id")
    ):
        raise MissionError(
            "NOT_EVOLVABLE",
            "Only a completed mission with a valid, audited baseline can evolve "
            f"(this one: {parent['state'].lower()}, {parent.get('verdict') or 'no verdict'}).",
            409,
        )
    family = parent["refs"]["strategy_id"]
    for other in await svc.store.recent(500):
        if (
            other["kind"] == "evolution"
            and other["refs"].get("family_id") == family
            and other["state"]
            in ("RUNNING", "WAITING_USER", "WAITING_APPROVAL", "PAUSED", "BLOCKED")
        ):
            return await svc.mission(other["id"])
    checked = check_budget({**(budget or {}), "max_paid_data_usd": 0.0})  # no purchases here
    strategy = await svc.research.strategy(family)
    exposed = [h for h in strategy.get("holdouts", [])]
    mission_id = "qm-" + new_id()[:12]
    base_version = parent["refs"]["version_id"]
    refs = {
        "family_id": family,
        "base_version_id": base_version,
        "parent_version_id": base_version,
        "dataset_id": parent["refs"]["dataset_id"],
        "base_run_id": parent["refs"]["run_id"],
        "parent_run_id": parent["refs"]["run_id"],
        "iteration": 0,
        "proposals": [],
        "holdout_exposed_before": bool(exposed),
        "strategy_id": family,
        "spec_sha256": parent["refs"].get("spec_sha256"),
    }
    await svc.store.add(
        {
            "id": mission_id,
            "kind": "evolution",
            "source_id": parent.get("source_id"),
            "parent_id": parent_id,
            "title": f"Evolution · {parent['title']}"[:120],
            "state": "RUNNING",
            "stage": "diagnose",
            "budget": checked,
            "budget_sha256": _hash(checked),
            "refs": refs,
        }
    )
    m = await svc.store.get(mission_id)
    assert m is not None
    run = await svc.research.run(parent["refs"]["run_id"])
    audit = parent["refs"].get("audit") or {}
    await svc.store.add_trial(
        _trial(
            m,
            0,
            base_version,
            None,
            run,
            "Baseline (from the source)",
            [],
            "",
            "",
            0,
            audit,
            bool(exposed),
        )
    )
    await svc.store.event(
        mission_id,
        "created",
        "Evolution started from the audited baseline",
        agent="jarvis",
        data={"budget": checked, "family": family},
    )
    if exposed:
        await svc.store.event(
            mission_id,
            "warning",
            "This family's holdout was already looked at: new variants can't "
            "earn independent holdout evidence (a new data window is needed).",
            agent="sentinel",
        )
    await svc.emit(mission_id, "Strategy evolution started")
    svc._spawn(mission_id)
    return await svc.mission(mission_id)


def _metrics(run: dict[str, Any]) -> dict[str, Any]:
    summary = run.get("summary") or {}
    seg = summary.get("segments") or {}
    oos, ins = seg.get("oos") or {}, seg.get("insample") or {}
    tests = {t["id"]: t for t in summary.get("tests") or []}
    selection = (tests.get("SELECTION_BIAS") or {}).get("metric") or {}
    return {
        "oos_trades": oos.get("trades"),
        "oos_net": oos.get("net_pnl"),
        "oos_win_rate": oos.get("win_rate"),
        "oos_profit_factor": oos.get("profit_factor"),
        "is_net": ins.get("net_pnl"),
        "is_trades": ins.get("trades"),
        "max_drawdown": (summary.get("metrics") or {}).get("max_drawdown"),
        "dsr": selection.get("dsr"),
        "family_trials": selection.get("trials"),
        "verdict": (summary.get("verdict") or {}).get("verdict"),
        "would_be": (summary.get("verdict") or {}).get("would_be"),
        "tests": {k: v["status"] for k, v in tests.items()},
    }


def _trial(
    m: dict[str, Any],
    iteration: int,
    version_id: str | None,
    parent_version: str | None,
    run: dict[str, Any] | None,
    title: str,
    changes: list[dict[str, Any]],
    mechanism: str,
    prediction: str,
    cx: int,
    audit: dict[str, Any] | None,
    post_holdout: bool,
    outcome: str = "TESTED",
    spec_sha: str | None = None,
) -> dict[str, Any]:
    return {
        "id": "tr-" + new_id()[:12],
        "mission_id": m["id"],
        "family_id": m["refs"]["family_id"],
        "iteration": iteration,
        "version_id": version_id,
        "parent_version_id": parent_version,
        "spec_sha256": spec_sha or (run or {}).get("manifest", {}).get("spec_sha256"),
        "title": title[:200],
        "changes": changes,
        "mechanism": mechanism[:1200],
        "prediction": prediction[:600],
        "complexity": cx,
        "run_id": (run or {}).get("id"),
        "outcome": outcome,
        "metrics": _metrics(run) if run else None,
        "audit": {
            "passed": audit.get("passed"),
            "sha256": audit.get("sha256"),
            "second_engine": (audit.get("second_engine") or {}).get("mismatch_count"),
        }
        if audit
        else None,
        "post_holdout": int(post_holdout),
    }


async def diagnose(svc: MissionService, m: dict[str, Any]) -> str:
    run_id = m["refs"]["parent_run_id"]
    task = await svc.task(
        m,
        "cipher",
        "diagnose",
        "Diagnose the parent's development results",
        {"run": run_id},
        ["read_run"],
    )
    run = await svc.research.run(run_id)
    rows = (await svc.research.trades(run_id, 0, 10**9))["rows"]
    dev = [r for r in rows if r["segment"] in ("IS", "OOS")]
    diagnosis = _diagnosis(run, dev)
    await svc._set_refs(m, diagnosis=diagnosis)
    for line in diagnosis["findings"]:
        await task.act(line)
    await task.done(
        diagnosis,
        evidence=[run_id],
        limitations=["Development segments only; the holdout stays sealed."],
    )
    return "next"


def _diagnosis(run: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = run["summary"]
    gross = sum(abs(r["gross"]) for r in rows) or 1.0
    costs = sum(r["fees"] + r["slippage_cost"] for r in rows)
    by_side: dict[str, dict[str, float]] = {}
    by_hour: dict[str, dict[str, float]] = {}
    reasons: dict[str, int] = {}
    for r in rows:
        side = by_side.setdefault(r["direction"], {"trades": 0, "net": 0.0})
        side["trades"] += 1
        side["net"] = round(side["net"] + r["net"], 2)
        hour = r["entry_time"][11:13] + ":00Z"
        h = by_hour.setdefault(hour, {"trades": 0, "net": 0.0})
        h["trades"] += 1
        h["net"] = round(h["net"] + r["net"], 2)
        reasons[r["exit_reason"]] = reasons.get(r["exit_reason"], 0) + 1
    nets = sorted((r["net"] for r in rows), reverse=True)
    total = sum(nets)
    top = sum(nets[:5])
    tests = {t["id"]: t["status"] for t in summary.get("tests") or []}
    findings = [
        f"{len(rows)} development trades; costs are {costs / gross:.0%} of gross movement",
        "By side: "
        + "; ".join(f"{k} {v['trades']} trades, net {v['net']}" for k, v in by_side.items()),
        "Exit reasons: " + ", ".join(f"{k} {n}" for k, n in sorted(reasons.items())),
        f"Top 5 trades carry {top:.2f} of {total:.2f} net",
        "Tests: " + ", ".join(f"{k} {v}" for k, v in tests.items() if v != "PASSED"),
    ]
    return {
        "trades": len(rows),
        "cost_share": round(costs / gross, 4),
        "by_side": by_side,
        "by_hour_utc": by_hour,
        "exit_reasons": reasons,
        "top5_net": round(top, 2),
        "net": round(total, 2),
        "tests": tests,
        "findings": findings,
    }


async def propose(svc: MissionService, m: dict[str, Any]) -> str:
    budget = m["budget"]
    trials = await svc.store.trials(family_id=m["refs"]["family_id"])
    tested = [t for t in trials if t["mission_id"] == m["id"] and t["iteration"] > 0]
    left = int(budget["max_variants"]) - len(tested)
    combos_used = sum(_grid_size(t) for t in tested)
    combos_left = int(budget["max_parameter_combinations"]) - combos_used
    task = await svc.task(
        m,
        "cipher",
        "propose",
        "Propose reasoned variants (ATLAS + CIPHER view)",
        {"diagnosis": m["refs"]["diagnosis"], "trials": len(trials)},
        ["propose_variants"],
    )
    if left <= 0 or combos_left <= 0:
        await task.done({"proposed": 0, "reason": "variant budget used up"})
        await svc._set_refs(m, proposals=[])
        return "goto:compare"
    parent = await _version(svc, m, m["refs"]["parent_version_id"])
    base = await _version(svc, m, m["refs"]["base_version_id"])
    spec = parent["spec"]
    paths = allowed_paths(spec["rule"]["type"])
    known = {t["spec_sha256"] for t in trials if t.get("spec_sha256")}
    k = min(PER_ROUND, left)
    opening = (
        "Parent rule (plain language):\n- "
        + "\n- ".join(describe(parse(spec)))
        + f"\n\nParent spec (JSON):\n{json.dumps({x: spec[x] for x in ('rule', 'exits', 'session', 'filters') if x in spec})}"
        + f"\n\nDiagnosis of development results (holdout sealed):\n{json.dumps(m['refs']['diagnosis']['findings'])}"
        + "\n\nTrials already spent in this family:\n"
        + "\n".join(
            f"- {t['title']}: {t['changes'] or 'baseline'} → OOS net {(t.get('metrics') or {}).get('oos_net')}"
            for t in trials
        )
        + f"\n\nBudget left: {left} variant(s), {combos_left} parameter combination(s). Propose at most {k}."
    )

    def check(payload: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        problems = []
        variants = payload.get("variants") or []
        if not isinstance(variants, list) or len(variants) > k:
            raise ValueError(f"propose at most {k} variants")
        seen = set(known)
        for n, v in enumerate(variants, start=1):
            changes = v.get("changes") or []
            bad = [c.get("path") for c in changes if c.get("path") not in paths]
            if bad or not changes:
                problems.append(f"variant {n}: paths must be from {paths} (got {bad or 'none'})")
                continue
            raw = apply_patch(
                {**copy.deepcopy(spec), "assumptions": []}, {c["path"]: c["value"] for c in changes}
            )
            raw["assumptions"] = spec.get("assumptions", [])
            try:
                parsed = parse(raw)
            except FuturesSpecError as exc:
                problems.append(f"variant {n}: {exc.message}")
                continue
            sha = parsed.sha256()
            if sha in seen:
                problems.append(f"variant {n}: identical to a rule already tested")
                continue
            if (
                not str(v.get("mechanism") or "").strip()
                or not str(v.get("prediction") or "").strip()
            ):
                problems.append(f"variant {n}: needs a mechanism and a falsifiable prediction")
                continue
            seen.add(sha)
            dumped = parsed.model_dump(mode="json")
            out.append(
                {
                    "id": "pv-" + new_id()[:8],
                    "title": str(v.get("title") or f"variant {n}")[:120],
                    "changes": [{"path": c["path"], "value": c["value"]} for c in changes],
                    "mechanism": str(v["mechanism"])[:1200],
                    "prediction": str(v["prediction"])[:600],
                    "risks": str(v.get("risks") or "")[:600],
                    "spec": dumped,
                    "spec_sha256": sha,
                    "complexity": complexity(base["spec"], dumped),
                    "grid": max(1, _grid_of(dumped)),
                }
            )
        if problems:
            raise ValueError("; ".join(problems))
        if sum(p["grid"] for p in out) > combos_left:
            raise ValueError(
                f"these variants need more than the {combos_left} parameter combinations left"
            )
        return out

    system = agents.CIPHER_VARIANTS_SYSTEM.format(k=k, paths=", ".join(paths))
    proposals = await svc.call(
        m,
        task,
        "cipher",
        lambda model, **kw: agents.run_tool(
            model, system, opening, agents.PROPOSE_VARIANTS, check, **kw
        ),
    )
    for p in proposals:
        await task.act(
            f"Proposed “{p['title']}”: {', '.join(f'{c["path"]}={c["value"]}' for c in p['changes'])} — {p['mechanism'][:140]}"
        )
    await svc._set_refs(m, proposals=proposals)
    await task.done(
        {"proposed": len(proposals)},
        artifacts=[{"kind": "proposal", "id": p["id"]} for p in proposals],
    )
    return "next" if proposals else "goto:compare"


def _grid_of(spec: dict[str, Any]) -> int:
    size = 1
    for values in (spec.get("validation") or {}).get("parameter_grid", {}).values():
        size *= len(values)
    return size


def _grid_size(trial: dict[str, Any]) -> int:
    return int(((trial.get("metrics") or {}).get("grid")) or 1)


async def _version(svc: MissionService, m: dict[str, Any], version_id: str) -> dict[str, Any]:
    strategy = await svc.research.strategy(m["refs"]["family_id"])
    return next(v for v in strategy["versions"] if v["id"] == version_id)


async def test(svc: MissionService, m: dict[str, Any]) -> str:
    proposals = m["refs"].get("proposals") or []
    iteration = int(m["refs"]["iteration"]) + 1
    done_ids = set(m["refs"].get("tested_proposals") or [])
    exposed = await _exposed(svc, m)
    for p in proposals:
        if p["id"] in done_ids:
            continue
        task = await svc.task(
            m,
            "archive",
            "variant",
            f"Freeze and test “{p['title']}”",
            {"spec_sha256": p["spec_sha256"]},
            ["add_version", "start_run"],
        )
        strategy = await svc.research.add_version(
            m["refs"]["family_id"],
            p["spec"],
            origin="ai",
            note=f"Evolution {m['id']} · {p['title']}",
            parent_id=m["refs"]["parent_version_id"],
        )
        version = next(v for v in strategy["versions"] if v["spec_sha256"] == p["spec_sha256"])
        await task.act(f"Version v{version['number']} frozen (sha256 {p['spec_sha256'][:12]}…)")
        run = await svc.research.start_run(
            version["id"], m["refs"]["dataset_id"], kind="validation"
        )
        await task.act(f"CIPHER: engine run {run['id']} (holdout sealed)")
        run = await svc.wait_run(m, run["id"])
        if run["status"] != "COMPLETED":
            await svc.store.add_trial(
                _trial(
                    m,
                    iteration,
                    version["id"],
                    m["refs"]["parent_version_id"],
                    None,
                    p["title"],
                    p["changes"],
                    p["mechanism"],
                    p["prediction"],
                    p["complexity"],
                    None,
                    exposed,
                    outcome="RUN_FAILED",
                    spec_sha=p["spec_sha256"],
                )
            )
            await task.fail(run.get("error") or run["status"])
        else:
            audit = await svc.audit_run(run["id"])
            await task.act(
                f"SENTINEL: {'audit passed' if audit['passed'] else 'AUDIT FAILED'} "
                f"({audit['second_engine']['agree']}/{audit['second_engine']['compared']} trades agree)"
            )
            trial = _trial(
                m,
                iteration,
                version["id"],
                m["refs"]["parent_version_id"],
                run,
                p["title"],
                p["changes"],
                p["mechanism"],
                p["prediction"],
                p["complexity"],
                audit,
                exposed,
                outcome="TESTED" if audit["passed"] else "AUDIT_FAILED",
            )
            trial["metrics"]["grid"] = p["grid"]
            await svc.store.add_trial(trial)
            oos = trial["metrics"]
            await task.done(
                {
                    "version_id": version["id"],
                    "run_id": run["id"],
                    "oos_net": oos["oos_net"],
                    "oos_trades": oos["oos_trades"],
                    "audit": audit["passed"],
                },
                artifacts=[{"kind": "run", "id": run["id"]}],
            )
        done_ids.add(p["id"])
        await svc._set_refs(m, tested_proposals=sorted(done_ids))
    await svc._set_refs(m, iteration=iteration)
    return "next"


async def _exposed(svc: MissionService, m: dict[str, Any]) -> bool:
    strategy = await svc.research.strategy(m["refs"]["family_id"])
    return bool(strategy.get("holdouts"))


def pareto(rows: list[dict[str, Any]]) -> list[str]:
    """Trial ids not dominated on (OOS net ↑, OOS trades ↑, drawdown ↓, complexity ↓)."""

    def key(t: dict[str, Any]) -> tuple[float, float, float, float]:
        mt = t["metrics"]
        return (
            float(mt["oos_net"] or 0),
            float(mt["oos_trades"] or 0),
            -float(mt["max_drawdown"] or 0),
            -float(t["complexity"]),
        )

    keep = []
    for a in rows:
        ka = key(a)
        dominated = any(
            all(x >= y for x, y in zip(key(b), ka, strict=True)) and key(b) != ka
            for b in rows
            if b is not a
        )
        if not dominated:
            keep.append(a["id"])
    return keep


async def compare(svc: MissionService, m: dict[str, Any]) -> str:
    trials = [
        t
        for t in await svc.store.trials(family_id=m["refs"]["family_id"])
        if t["outcome"] == "TESTED" and t["metrics"]
    ]
    task = await svc.task(
        m,
        "cipher",
        "compare",
        "Compare all audited trials (Pareto, not a single score)",
        {"trials": [t["id"] for t in trials]},
        [],
    )
    front = pareto(trials)
    best_is = max(trials, key=lambda t: float(t["metrics"]["is_net"] or 0)) if trials else None
    best_oos = max(trials, key=lambda t: float(t["metrics"]["oos_net"] or 0)) if trials else None
    family_trials = max((t["metrics"].get("family_trials") or 0 for t in trials), default=0)
    notes = [
        f"{len(trials)} audited trial(s); {family_trials} variants counted in this family's deflated Sharpe."
    ]
    if best_is and best_oos and best_is["id"] != best_oos["id"]:
        notes.append(
            "The best in-sample variant is not the best out-of-sample one — in-sample rank says little."
        )
    notes.append("Best in-sample is never best live; the sealed holdout decides once, at the end.")
    comparison = {
        "pareto": front,
        "rows": [
            {
                "id": t["id"],
                "title": t["title"],
                "version_id": t["version_id"],
                "iteration": t["iteration"],
                "complexity": t["complexity"],
                **t["metrics"],
                "pareto": t["id"] in front,
            }
            for t in trials
        ],
        "notes": notes,
    }
    await svc._set_refs(m, comparison=comparison)
    for note in notes:
        await task.act(note)
    await task.done({"pareto": front}, evidence=[t["run_id"] for t in trials if t.get("run_id")])
    budget = m["budget"]
    tested = [t for t in await svc.store.trials(mission_id=m["id"]) if t["iteration"] > 0]
    fresh = await svc.store.get(m["id"])
    assert fresh is not None
    spent_ok = float(fresh["spent_usd"]) < float(budget["max_model_usd"]) * 0.9
    more = (
        int(m["refs"]["iteration"]) < int(budget["max_iterations"])
        and len(tested) < int(budget["max_variants"])
        and spent_ok
        and m["refs"].get("proposals")
    )
    if more:
        # Next round builds on the Pareto member with the most out-of-sample trades and
        # positive OOS net — a development choice; the holdout is untouched.
        pool = [t for t in trials if t["id"] in front and t["iteration"] > 0] or [
            t for t in trials if t["iteration"] == 0
        ]
        nxt = max(
            pool,
            key=lambda t: (
                float(t["metrics"]["oos_net"] or 0) > 0,
                t["metrics"]["oos_trades"] or 0,
            ),
        )
        await svc._set_refs(
            m, parent_version_id=nxt["version_id"], parent_run_id=nxt["run_id"], proposals=[]
        )
        await svc.store.event(
            m["id"], "iteration", f"Next round builds on “{nxt['title']}”", agent="cipher"
        )
        return "goto:diagnose"
    return "next"


async def holdout(svc: MissionService, m: dict[str, Any]) -> str:
    lock_ref = m["refs"].get("holdout")
    if not lock_ref:
        rows = [r for r in m["refs"]["comparison"]["rows"] if r["pareto"]]
        return await svc._wait(
            m,
            "WAITING_USER",
            {
                "kind": "lock_candidate",
                "candidates": rows,
                "note": "Pick one candidate. Its sealed holdout is evaluated once; afterwards this "
                "family's holdout is spent for any further variant.",
            },
            "Lock one candidate for its one-time holdout evaluation",
        )
    task = await svc.task(
        m,
        "sentinel",
        "holdout",
        "Evaluate the locked candidate's holdout once",
        {"version": lock_ref["version_id"]},
        [],
    )
    run = await svc.wait_run(m, lock_ref["run_id"])
    if run["status"] != "COMPLETED":
        await task.fail(run.get("error") or run["status"])
        from jarvis.quantlab.ideas.missions import Blocked

        raise Blocked("RUN_FAILED", f"The holdout run {run['status'].lower()}.")
    audit = await svc.audit_run(run["id"])
    summary = run["summary"]
    tests = {t["id"]: t for t in summary.get("tests") or []}
    hold = tests.get("HOLDOUT") or {}
    # Once a family's holdout has been looked at, every later look in that family is
    # contaminated — even re-reading an earlier candidate, because choosing between
    # variants with that knowledge is exactly the bias the holdout exists to prevent.
    exposed_before = bool(m["refs"].get("holdout_exposed_before"))
    independent = bool((hold.get("metric") or {}).get("independent")) and not exposed_before
    from jarvis.quantlab.ideas.missions import research_verdict

    label, reasons = research_verdict(summary, audit)
    if not independent:
        reasons.append(
            "The holdout of this family had been looked at before: this evaluation is "
            "not independent (contaminated). A new, untouched data window is required."
        )
        if label in (
            "PROMISING_RESEARCH_CANDIDATE",
            "ROBUST_UNDER_TESTED_ASSUMPTIONS",
            "FORWARD_VALIDATION_REQUIRED",
        ):
            label = "INSUFFICIENT_EVIDENCE"
    await task.act(
        f"Holdout {hold.get('status', 'NOT_RUN')} · independent: {independent} · audit "
        f"{'passed' if audit['passed'] else 'failed'}"
    )
    await svc._set_refs(
        m,
        run_id=run["id"],
        audit=audit,
        holdout={**lock_ref, "independent": independent, "status": hold.get("status")},
    )
    await task.done(
        {"verdict": label, "independent": independent}, artifacts=[{"kind": "run", "id": run["id"]}]
    )
    from jarvis.quantlab.ideas.missions import evidence_block

    note_task = await svc.task(
        m, "jarvis", "report", "Write the evolution dossier", {"run": run["id"]}, ["submit_report"]
    )
    note = await svc._narrative(m, note_task, evidence_block(summary, audit, label))
    await svc._finish(m, label, reasons, note=note, summary=summary)
    await note_task.done({"verdict": label})
    return "done"


async def lock(
    svc: MissionService, mission_id: str, version_id: str, confirm: bool
) -> dict[str, Any]:
    from jarvis.quantlab.ideas.missions import MissionError

    m = await svc._open(mission_id)
    waiting = m.get("waiting") or {}
    if m["state"] != "WAITING_USER" or waiting.get("kind") != "lock_candidate":
        raise MissionError("NOT_WAITING", "The mission isn't waiting for a candidate.", 409)
    if not confirm:
        raise MissionError("CONFIRM_REQUIRED", "Confirm that the holdout may be evaluated once.")
    if version_id not in {c["version_id"] for c in waiting["candidates"]}:
        raise MissionError("NOT_A_CANDIDATE", "Pick one of the listed candidates.")
    run = await svc.research.start_run(
        version_id,
        m["refs"]["dataset_id"],
        kind="validation",
        include_holdout=True,
        confirm_holdout=True,
    )
    refs = {**m["refs"], "holdout": {"version_id": version_id, "run_id": run["id"]}}
    await svc.store.event(
        mission_id,
        "lock",
        "You locked a candidate; its holdout is evaluated once",
        agent="jarvis",
        data={"version": version_id, "run": run["id"]},
    )
    await svc.store.transition(mission_id, ("WAITING_USER",), "RUNNING", waiting=None, refs=refs)
    svc._spawn(mission_id)
    return await svc.mission(mission_id)
