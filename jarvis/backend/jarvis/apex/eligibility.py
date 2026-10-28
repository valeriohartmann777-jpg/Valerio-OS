"""The eligibility gate: eleven independent checks before a plan is a qualified paper signal.

A plan passes only if **every** check passes; each check says why it failed and what is
missing. Failing plans stay visible as ``RESEARCH_ONLY`` (a plan exists, not qualified) or
``BLOCKED`` (the data can't support a signal at all) — never as an entry instruction.

1. ``APP_READY`` — backend running with a known build.
2. ``DATA_FIT`` — quality gate passed, not stale, last bar at most ``max_age`` minutes old,
   mode DELAYED or LIVE (never SYNTHETIC test data, never a REPLAY of a past moment).
3. ``SOURCE_CAPABILITY`` — the source supports the setup's factors and realistic execution:
   tradable instrument, and either traded prices or a measured ask (bid-only with an estimated
   spread: ASK REQUIRED FOR REALISTIC EXECUTION).
4. ``ZONE_CONFIRMED`` — the plan's zone was confirmed before the trigger bar and is not
   broken against the trade or expired.
5. ``TRIGGER_CONFIRMED`` — the setup's trigger bar closed by ``as_of`` (a touch is not one).
6. ``EXECUTION_FEASIBLE`` — entry, stop and targets on the tick grid, side-correct geometry,
   dollar risk computed with the instrument's point value.
7. ``PROBABILITY`` — a validated **and** calibrated P(TP before SL) of at least
   ``MIN_PROBABILITY`` for this setup, market and version. A zone quality score, a path share
   (P(upper first)) or an LLM's conviction is never that probability.
8. ``UNCERTAINTY_SAMPLE`` — effective sample at least ``MIN_EFFECTIVE`` and the lower bound
   of its interval at least ``MIN_LOWER`` (pre-registered).
9. ``NET_EV_STRESSED`` — expected R after costs stressed by 1.5 is positive.
10. ``MACRO_LIQUIDITY`` — a macro calendar is loaded and no high-impact release is inside the
    blackout; inside the permitted session.
11. ``UNIQUE_BUDGET`` — not a repeat of an alert on the same zone and direction within the
    cooldown, and fewer than the day's maximum of alerts issued (``alerts.admit``).

``≥ PREFERRED`` with a lower bound of at least ``MIN_PROBABILITY`` is shown as the high-
conviction class. A qualified paper signal is still paper only: ``user_executable`` is false.
"""

from __future__ import annotations

from typing import Any

MIN_PROBABILITY = 0.70
PREFERRED = 0.80
MIN_LOWER = 0.60
MIN_EFFECTIVE = 30
CHECKS = (
    "APP_READY", "DATA_FIT", "SOURCE_CAPABILITY", "ZONE_CONFIRMED", "TRIGGER_CONFIRMED",
    "EXECUTION_FEASIBLE", "PROBABILITY", "UNCERTAINTY_SAMPLE", "NET_EV_STRESSED",
    "MACRO_LIQUIDITY", "UNIQUE_BUDGET",
)  # fmt: skip
BLOCKING = ("APP_READY", "DATA_FIT", "SOURCE_CAPABILITY")  # the data can't carry a signal


def net_expected_r(plan: dict[str, Any], p: float) -> float:
    """Expected R of the plan at success probability ``p`` (target 1 before stop), with the
    plan's stressed costs taken from the reward and added to the loss."""
    entry = float(plan["entry"]["price"])
    risk = abs(entry - float(plan["stop"]))
    reward = abs(float(plan["targets"][0]["price"]) - entry)
    c = float(plan.get("stressed_costs_points") or 0.0)
    return round((p * (reward - c) - (1 - p) * (risk + c)) / risk, 4)


def gate(
    plan: dict[str, Any],
    *,
    build: str | None,
    quality: dict[str, Any],
    mode: str,
    data_age_minutes: float | None,
    max_age_minutes: int,
    execution: dict[str, Any],
    capability: dict[str, Any],
    probability: dict[str, Any],
    context: dict[str, Any] | None,
    in_session: bool,
    tick: float,
    point_value: float,
    as_of: int,
) -> dict[str, Any]:
    """The first ten checks (``UNIQUE_BUDGET`` is decided by ``alerts.admit`` afterwards)."""
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, why: str) -> None:
        checks.append({"check": name, "passed": bool(ok), "reason": why})

    check("APP_READY", bool(build) and build != "unknown",
          f"backend build {build}" if build else "build unknown")  # fmt: skip

    status = str(quality.get("status"))
    if mode == "SYNTHETIC":
        check("DATA_FIT", False, "SYNTHETIC test data — never a qualified signal")
    elif mode == "REPLAY":
        check("DATA_FIT", False, "REPLAY of a past moment — research only, no alert")
    elif status == "BLOCK" or quality.get("stale"):
        check("DATA_FIT", False, f"quality gate {status}" + (", stale" if quality.get("stale")
                                                            else ""))  # fmt: skip
    elif data_age_minutes is None or data_age_minutes > max_age_minutes:
        check("DATA_FIT", False, f"last bar {data_age_minutes} min old (max {max_age_minutes})")
    elif mode not in ("LIVE", "DELAYED"):
        check("DATA_FIT", False, f"mode {mode} — not current")
    else:
        check("DATA_FIT", True, f"{mode}, last bar {data_age_minutes:.0f} min old, "
                                f"quality {status}")  # fmt: skip

    if not execution.get("tradable", True):
        check("SOURCE_CAPABILITY", False, str(execution.get("reason") or "not tradable"))
    elif execution.get("quotes") == "synthetic":
        check("SOURCE_CAPABILITY", False, "generated data: no market capability")
    elif execution.get("quotes") == "bid" and execution.get("spread_source") != "measured":
        check(
            "SOURCE_CAPABILITY",
            False,
            "Bid only: ASK REQUIRED FOR REALISTIC EXECUTION (spread is an estimate)",
        )
    else:
        check("SOURCE_CAPABILITY", True,
              f"{capability.get('market_type')} {capability.get('schema')}: price structure "
              "and rejection measured on closed bars")  # fmt: skip

    confirmed = plan.get("zone_confirmed_at")
    bar = int((plan.get("trigger") or {}).get("bar_utc") or 0)
    state = str(plan.get("zone_state"))
    zone_ok = confirmed is not None and int(confirmed) <= bar and state not in ("EXPIRED",)
    if plan.get("setup") != "breakout_retest":
        zone_ok = zone_ok and state != "BROKEN"
    check("ZONE_CONFIRMED", zone_ok,
          f"zone {plan.get('zone')} {state}, confirmed before the trigger" if zone_ok
          else f"zone {plan.get('zone')} {state} — not confirmed before the trigger or no "
               "longer valid")  # fmt: skip

    trig = plan.get("trigger") or {}
    closed = trig.get("closed_utc")
    check("TRIGGER_CONFIRMED", closed is not None and int(closed) <= as_of,
          f"{trig.get('rule')}" if closed is not None else "no trigger bar")  # fmt: skip

    check("EXECUTION_FEASIBLE", *_feasible(plan, tick, point_value))

    p = probability.get("value")
    valid = bool(probability.get("validated")) and bool(probability.get("calibrated"))
    if p is None or not valid:
        check("PROBABILITY", False,
              "PROBABILITY NOT VALIDATED — no validated, calibrated P(TP before SL) for this "
              "setup, market and version" + (f" (an uncalibrated {p:.2f} doesn't count)"
                                             if p is not None else ""))  # fmt: skip
    elif probability.get("event_definition") != "TP_before_SL_after_fill_within_horizon":
        check("PROBABILITY", False, "the probability is not P(TP before SL) — a path or zone "
                                    "rate never qualifies a trade")  # fmt: skip
    else:
        check("PROBABILITY", float(p) >= MIN_PROBABILITY,
              f"P(TP before SL) {float(p):.2f} (minimum {MIN_PROBABILITY:.2f})")  # fmt: skip

    n_eff = int(probability.get("effective_sample_size") or 0)
    lower = probability.get("uncertainty_lower")
    sample_ok = valid and n_eff >= MIN_EFFECTIVE and lower is not None and lower >= MIN_LOWER
    check("UNCERTAINTY_SAMPLE", sample_ok,
          f"effective sample {n_eff} (min {MIN_EFFECTIVE}), lower bound "
          f"{'—' if lower is None else f'{float(lower):.2f}'} (min {MIN_LOWER:.2f})")  # fmt: skip

    if p is None or not valid:
        check("NET_EV_STRESSED", False,
              f"needs a validated probability; break-even after 1.5x costs is "
              f"P = {plan.get('break_even_probability')}")  # fmt: skip
        ev = None
    else:
        ev = net_expected_r(plan, float(p))
        check("NET_EV_STRESSED", ev > 0, f"expected {ev:+.2f} R after 1.5x costs")

    macro = (context or {}).get("macro") or {}
    m_status = str(macro.get("status") or "UNAVAILABLE")
    if not in_session:
        check("MACRO_LIQUIDITY", False, "outside the permitted session")
    elif m_status == "CONFIRMED":
        check("MACRO_LIQUIDITY", True, "calendar loaded, no high-impact release in the blackout")
    elif m_status == "CONTRADICTED":
        check("MACRO_LIQUIDITY", False, str(macro.get("reason") or "high-impact release "
                                                "inside the blackout"))  # fmt: skip
    else:
        check("MACRO_LIQUIDITY", False, "no macro calendar loaded — event risk unknown "
                                        "(import one in APEX → Market Context)")  # fmt: skip

    failed = [c["check"] for c in checks if not c["passed"]]
    passed_all = not failed
    conviction = None
    if passed_all and p is not None:
        conviction = "HIGH (≥ 0.80)" if float(p) >= PREFERRED and (lower or 0) >= MIN_PROBABILITY \
            else "QUALIFIED (≥ 0.70)"  # fmt: skip
    return {
        "checks": checks,
        "failed": failed,
        "pre_budget_passed": passed_all,
        "blocked": any(c in BLOCKING for c in failed),
        "net_expected_r": ev,
        "conviction": conviction,
        "probability_label": probability.get("label") or ("PROBABILITY NOT VALIDATED"
                                                          if not valid else None),
    }  # fmt: skip


def finish(result: dict[str, Any], budget: dict[str, Any] | None) -> dict[str, Any]:
    """Add ``UNIQUE_BUDGET`` and the verdict. ``budget`` None = not asked (an earlier check
    failed, so no alert slot is used)."""
    checks = list(result["checks"])
    if budget is None:
        checks.append(
            {
                "check": "UNIQUE_BUDGET",
                "passed": False,
                "reason": "not reached — an earlier check failed (no alert slot used)",
            }
        )
    else:
        checks.append({"check": "UNIQUE_BUDGET", "passed": bool(budget["admitted"]),
                       "reason": str(budget["reason"])})  # fmt: skip
    failed = [c["check"] for c in checks if not c["passed"]]
    eligible = not failed
    state = "ENTRY_ARMED" if eligible else ("BLOCKED" if result["blocked"] else "RESEARCH_ONLY")
    return {**result, "checks": checks, "failed": failed, "eligible": eligible,
            "setup_state": state}  # fmt: skip


def _feasible(plan: dict[str, Any], tick: float, point_value: float) -> tuple[bool, str]:
    entry = float(plan["entry"]["price"])
    stop = float(plan["stop"])
    targets = [float(t["price"]) for t in plan["targets"]]
    long = plan["direction"] == "long"

    def on_grid(x: float) -> bool:
        return abs(x / tick - round(x / tick)) < 1e-6

    if not all(on_grid(x) for x in (entry, stop, *targets)):
        return False, "a price is off the tick grid"
    ordered = (stop < entry < targets[0]) if long else (stop > entry > targets[0])
    if not ordered:
        return False, f"geometry broken: stop {stop}, entry {entry}, target {targets[0]}"
    usd = round(abs(entry - stop) * point_value, 2)
    if abs(usd - float(plan.get("risk_usd") or -1)) > 0.01:
        return False, f"dollar risk {plan.get('risk_usd')} ≠ {usd}"
    return True, (f"stop {abs(entry - stop):g} points = {usd:g} USD per {plan.get('unit')} "
                  "before fees; stop entry valid until expiry")  # fmt: skip
