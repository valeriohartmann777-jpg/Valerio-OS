"""Three zone setups on the causal zone map, long and short — spec I1-I3, version ``1.0.0`` each.

Every rule and number below is part of the version: changing one is a new version and a new
trial in the register. Only 5-minute bars that had **closed** by ``as_of`` are used, and the
zones are those of the zone map at ``as_of`` (``zonemap.zone_view``) — the same function runs
live and in the track record.

**Finite-state flow** (per setup and direction, reported as ``stage``)::

    SCANNING → NEAR_ZONE → WATCHING_SETUP → TRIGGER_PENDING → TRIGGER_CONFIRMED → ENTRY_ARMED

``ENTRY_ARMED`` means an order plan exists (a stop entry valid for ``trigger_valid_minutes``);
what happens to it afterwards — FILLED / NO_FILL / EXPIRED / INVALIDATED, then the outcome —
is the order lifecycle of ``resolve.py``, recorded separately and never written back into the
plan. ``setup_state`` is what a person sees: ``NO_TRADE`` (scanning, or a trigger whose plan
failed its checks) · ``WATCH`` (near or at a zone, nothing confirmed — a touch alone is never a
trade) · ``TRIGGER_PENDING`` · ``RESEARCH_ONLY`` (a plan exists but the eligibility gate has
not qualified it) · ``ENTRY_ARMED`` (qualified by the gate, paper only) · ``BLOCKED``.

**sweep_reclaim** (I1, long; short mirrored at resistance). A ranked zone that was confirmed
before the sweep and still live. *Sweep*: a closed 5-minute bar trades below the zone's low by
at least ``SWEEP_MIN_ATR`` ATR(15m) after the bar before it had closed at or above that low.
More than ``SWEEP_MAX_ATR`` below → a break, not a sweep (invalidated). *Reclaim*: within
``RECLAIM_BARS`` bars of the sweep bar, the first close back above the low, in the upper half
of its bar — that bar is the trigger. Entry: buy stop one tick above the trigger bar's high.
Stop: the sweep's extreme minus ``stop_buffer_atr`` ATR and a tick (plus the spread on a short's
buy-side stop: on bid-only data every buy-side price carries the modelled spread).

**rejection_rotation** (I2). The nearest ranked support (CONFIRMED / ACTIVE / TESTED /
FLIPPED — not WEAKENING), confirmed before the touch. The last closed 5-minute bar touched it,
closed above its top with a bullish body, a lower wick of at least ``WICK_MIN`` of its range
and a close in the top ``1 - CLOSE_LOCATION`` of it, and did not trade below the zone's low by
more than the stop buffer (that would be a sweep). Entry: buy stop one tick above the bar's
high. Stop: below the lower of the bar's low and the zone's low by the buffer.

**breakout_retest** (I3, long = a broken resistance; short = a broken support). The zone map
marked the zone BROKEN (two 15-minute closes beyond its far edge) within ``BREAK_MAX_HOURS``,
it was confirmed before the break and scores at least ``zonemap.MIN_SCORE``. *Acceptance*:
since the break, price extended at least ``DISPLACEMENT_ATR`` ATR beyond the far edge.
*Retest*: the last closed 5-minute bar came back to within ``RETEST_ATR`` ATR of the broken
edge, closed beyond it with a body in the break's direction and did not close back inside.
Entry: buy stop one tick above the retest bar's high; stop below the zone's low by the buffer.
A ranked resistance that is only being pressed is ``WATCHING_SETUP`` ("watching breakout"),
never a short by itself: a confirmed break turns a resistance into a continuation case.

**Targets (all three):** one tick before the next one or two ranked opposing zones beyond the
entry — never an invented price; none mapped → no plan. **Plan checks:** stop at least the
market's minimum and at most ``max_stop_atr`` ATR; reward/risk to target 1 after costs at
least ``min_rr``; target 1 at least ``ROUTE_MIN_ATR`` ATR away (no mid-range plan). Dollar
risk uses the market's point value (NQ 20, MNQ 2 USD per point and contract).
"""

from __future__ import annotations

from typing import Any

from jarvis.apex.candidates import candidate_id
from jarvis.apex.zones import snap
from jarvis.learning.market import Bars
from jarvis.settings import ApexMarketSettings, ApexSettings

SWEEP_RECLAIM = "sweep_reclaim"
REJECTION_ROTATION = "rejection_rotation"
BREAKOUT_RETEST = "breakout_retest"
VERSION = "1.0.0"
SETUPS = {SWEEP_RECLAIM: VERSION, REJECTION_ROTATION: VERSION, BREAKOUT_RETEST: VERSION}
BAR_MINUTES = 5

STAGES = ("SCANNING", "NEAR_ZONE", "WATCHING_SETUP", "TRIGGER_PENDING", "TRIGGER_CONFIRMED",
          "ENTRY_ARMED")  # fmt: skip
SETUP_STATES = ("WATCH", "TRIGGER_PENDING", "TRIGGER_CONFIRMED", "ENTRY_ARMED", "RESEARCH_ONLY",
                "NO_TRADE", "BLOCKED")  # fmt: skip
EVENT = "TP_before_SL_after_fill_within_horizon"

WATCH_ATR = 0.5  # within this of a zone edge (or inside) → watching
SWEEP_MIN_ATR = 0.1
SWEEP_MAX_ATR = 1.0
RECLAIM_BARS = 3
WICK_MIN = 0.35
CLOSE_LOCATION = 0.6
BREAK_MAX_HOURS = 24
DISPLACEMENT_ATR = 1.0
RETEST_ATR = 0.15
ROUTE_MIN_ATR = 1.0
REJECTABLE = ("CONFIRMED", "ACTIVE", "TESTED", "FLIPPED")
SWEEPABLE = ("CONFIRMED", "ACTIVE", "TESTED", "WEAKENING", "FLIPPED")


def evaluate(
    name: str,
    market: str,
    zone_map: dict[str, Any],
    placed: list[dict[str, Any]],
    bars5: Bars,
    minute: Bars,
    *,
    as_of: int,
    trend_60: str,
    spec: ApexMarketSettings,
    cfg: ApexSettings,
    costs: float,
    spread: float = 0.0,
) -> dict[str, Any]:
    """One setup's decision at ``as_of``: {strategy, version, status, direction, stage,
    setup_state, setups (one per direction), trigger, candidates, rejected, reasons}.

    ``bars5``: closed 5-minute bars (the last few hours); ``minute``: closed one-minute bars
    since at least ``BREAK_MAX_HOURS`` ago (for the breakout's displacement)."""
    a15 = float(zone_map.get("atr15") or 0.0)
    price = zone_map.get("price")
    if price is None or a15 <= 0:
        return _decision(name, [], ["No zone map yet (too few bars or no volatility measure)."])
    fresh = len(bars5) > 0 and int(bars5.t[-1]) + 60 * BAR_MINUTES * 2 > as_of
    rule = {SWEEP_RECLAIM: _sweep, REJECTION_ROTATION: _rejection, BREAKOUT_RETEST: _breakout}[name]
    sides: list[dict[str, Any]] = []
    for long in (True, False):
        found = rule(long, zone_map, placed, bars5, minute, float(price), a15, as_of, cfg)
        found["direction"] = "long" if long else "short"
        if found["stage"] == "TRIGGER_CONFIRMED" and not fresh:
            found.update(stage="WATCHING_SETUP", trigger=None)
            found["reasons"].append("No closed 5-minute bar in the last 10 minutes.")
        if found["stage"] == "TRIGGER_CONFIRMED":
            plan = _plan(name, market, found, zone_map, long, as_of=as_of, a15=a15,
                         trend_60=trend_60, spec=spec, cfg=cfg, costs=costs,
                         spread=spread)  # fmt: skip
            if "reason" in plan:
                found["rejected"] = plan
                found["reasons"].append(plan["reason"])
            else:
                found["plan"] = plan
                found["stage"] = "ENTRY_ARMED"
        found["setup_state"] = _state(found)
        sides.append(found)
    return _decision(name, sides, [])


# -- the three rules -------------------------------------------------------------------------------


def _ranked(zone_map: dict[str, Any], long: bool) -> list[dict[str, Any]]:
    return list(zone_map["support"] if long else zone_map["resistance"])


def _nearest(zones: list[dict[str, Any]]) -> dict[str, Any] | None:
    return min(zones, key=lambda z: z["distance_atr"]) if zones else None


def _approach(z: dict[str, Any] | None, cfg: ApexSettings, what: str) -> dict[str, Any] | None:
    """SCANNING / NEAR_ZONE / WATCHING_SETUP from the distance alone (None = closer look)."""
    if z is None:
        return _found("SCANNING", None, [f"No ranked {what} zone."])
    if z["distance_atr"] > cfg.reach_atr:
        return _found("SCANNING", z, [f"{_name(z)} is {z['distance_atr']:.1f} ATR away "
                                      f"(reach {cfg.reach_atr:g})."])  # fmt: skip
    if z["distance_atr"] > WATCH_ATR and not z["inside"]:
        return _found("NEAR_ZONE", z, [f"{_name(z)} in reach ({z['distance_atr']:.1f} ATR)."])
    return None


def _rejection(
    long: bool, zone_map: dict[str, Any], placed: list[dict[str, Any]], bars5: Bars,
    minute: Bars, price: float, a15: float, as_of: int, cfg: ApexSettings,
) -> dict[str, Any]:  # fmt: skip
    z = _nearest(_ranked(zone_map, long))
    if z is None or len(bars5) == 0:
        early = _approach(z, cfg, "support" if long else "resistance")
        return early or _found("WATCHING_SETUP", z, ["No closed 5-minute bar yet."])
    lo, hi = z["bounds"]["low"], z["bounds"]["high"]
    o, h, low, c = _bar(bars5, -1)
    touched = low <= hi if long else h >= lo
    if not touched:
        early = _approach(z, cfg, "support" if long else "resistance")
        return early or _found("WATCHING_SETUP", z, [
            f"At {_name(z)}: watching — no touch on the last closed 5-minute bar."])  # fmt: skip
    if z["state"] not in REJECTABLE:
        return _found("NEAR_ZONE", z, [f"{_name(z)} is {z['state']} — not traded for a rejection."])
    if not _confirmed_before(z, int(bars5.t[-1])):
        return _found("WATCHING_SETUP", z, [f"{_name(z)} was confirmed after this bar opened."])
    rng = max(h - low, 1e-12)
    buffer = cfg.stop_buffer_atr * a15
    if long:
        out, body = c > hi, c > o
        wick = (min(o, c) - low) / rng
        location = (c - low) / rng
        held = low >= lo - buffer
    else:
        out, body = c < lo, c < o
        wick = (h - max(o, c)) / rng
        location = (h - c) / rng
        held = h <= hi + buffer
    checks = (
        ("close back out of it", out),
        ("close in the trade's direction", body),
        (f"show a rejection wick of at least {WICK_MIN:.0%} of its range", wick >= WICK_MIN),
        (f"close in the outer {1 - CLOSE_LOCATION:.0%} of its range", location >= CLOSE_LOCATION),
        ("stay within the stop buffer (deeper = a sweep)", held),
    )
    missing = [label for label, ok in checks if not ok]
    if missing:
        return _found("WATCHING_SETUP", z, [f"Touched {_name(z)} without confirmation: the bar "
                                            f"did not {', '.join(missing)}."])  # fmt: skip
    trigger = _trigger(bars5, -1, f"{'bullish' if long else 'bearish'} 5-minute rejection of "
                       f"{_name(z)} (wick {wick:.0%}, close location {location:.0%})")  # fmt: skip
    stop_ref = min(low, lo) if long else max(h, hi)
    return _found("TRIGGER_CONFIRMED", z, [], trigger=trigger, stop_ref=stop_ref)


def _sweep(
    long: bool, zone_map: dict[str, Any], placed: list[dict[str, Any]], bars5: Bars,
    minute: Bars, price: float, a15: float, as_of: int, cfg: ApexSettings,
) -> dict[str, Any]:  # fmt: skip
    # The zone swept may now be on either side of the price: look at every ranked zone.
    zones = [
        z for z in (*zone_map["support"], *zone_map["resistance"])
        if z["state"] in SWEEPABLE and (z["role_at_confirmation"] == ("support" if long
                                                                     else "resistance"))
    ]  # fmt: skip
    n = len(bars5)
    best: dict[str, Any] | None = None
    for z in sorted(zones, key=lambda z: -z["quality_score"]):
        lo, hi = z["bounds"]["low"], z["bounds"]["high"]
        edge = lo if long else hi
        for k in range(max(1, n - RECLAIM_BARS - 1), n):
            _, h, low, _ = _bar(bars5, k)
            prev_close = float(bars5.close[k - 1])
            depth = (edge - low) if long else (h - edge)
            came_from = prev_close >= edge if long else prev_close <= edge
            if depth < SWEEP_MIN_ATR * a15 or not came_from:
                continue
            if not _confirmed_before(z, int(bars5.t[k])):
                continue
            seg = slice(k, n)
            extreme = float(bars5.low[seg].min()) if long else float(bars5.high[seg].max())
            deepest = (edge - extreme) if long else (extreme - edge)
            if deepest > SWEEP_MAX_ATR * a15:
                best = best or _found("SCANNING", z, [
                    f"{_name(z)} breached by {deepest / a15:.1f} ATR — a break, not a sweep "
                    f"(limit {SWEEP_MAX_ATR:g} ATR)."])  # fmt: skip
                break
            closes = bars5.close[k:n]
            back = [j for j, cl in enumerate(closes) if (cl > edge if long else cl < edge)]
            if not back:
                if n - 1 - k < RECLAIM_BARS:
                    why = (f"{_name(z)} swept by {depth / a15:.2f} ATR — waiting for a close "
                           f"back {'above' if long else 'below'} {edge:g} within "
                           f"{RECLAIM_BARS} bars.")  # fmt: skip
                    sweep = {"bar_utc": int(bars5.t[k]), "extreme": extreme}
                    return _found("TRIGGER_PENDING", z, [why], sweep=sweep)
                best = best or _found("SCANNING", z, [
                    f"{_name(z)} swept, no reclaim within {RECLAIM_BARS} bars."])  # fmt: skip
                break
            j = k + back[0]
            if j != n - 1:
                best = best or _found(
                    "WATCHING_SETUP",
                    z,
                    [f"{_name(z)} swept and reclaimed earlier — the trigger bar has passed."],
                )
                break
            _, h_j, l_j, c_j = _bar(bars5, j)
            location = ((c_j - l_j) if long else (h_j - c_j)) / max(h_j - l_j, 1e-12)
            if location < 0.5:
                return _found("TRIGGER_PENDING", z, [
                    f"{_name(z)} reclaimed, but the close is in the wrong half of its bar."],
                    sweep={"bar_utc": int(bars5.t[k]), "extreme": extreme})  # fmt: skip
            trigger = _trigger(bars5, j, f"sweep of {_name(z)} by {deepest / a15:.2f} ATR, "
                               f"reclaimed {'above' if long else 'below'} {edge:g}")  # fmt: skip
            trigger["sweep_bar_utc"] = int(bars5.t[k])
            trigger["sweep_extreme"] = extreme
            return _found("TRIGGER_CONFIRMED", z, [], trigger=trigger, stop_ref=extreme)
    if best is not None:
        return best
    z = _nearest(_ranked(zone_map, long))
    early = _approach(z, cfg, "support" if long else "resistance")
    if early is not None:
        return early
    return _found("WATCHING_SETUP", z, [f"At {_name(z)}: no sweep of its "
                                        f"{'low' if long else 'high'} yet."])  # fmt: skip


def _breakout(
    long: bool, zone_map: dict[str, Any], placed: list[dict[str, Any]], bars5: Bars,
    minute: Bars, price: float, a15: float, as_of: int, cfg: ApexSettings,
) -> dict[str, Any]:  # fmt: skip
    role = "resistance" if long else "support"
    broken = [
        z for z in placed
        if z["state"] == "BROKEN" and z["role_at_confirmation"] == role
        and z.get("broken_at_t") is not None
        and as_of - int(z["broken_at_t"]) <= BREAK_MAX_HOURS * 3600
        and z["quality_score"] >= float(zone_map.get("min_score") or 0)
        and _confirmed_before(z, int(z["broken_at_t"]))
    ]  # fmt: skip
    if not broken:
        # Watching a breakout: price pressing a ranked zone it hasn't broken.
        z = _nearest(_ranked(zone_map, not long))
        word = "resistance" if long else "support"
        if z is not None and (z["inside"] or z["distance_atr"] <= WATCH_ATR):
            return _found("WATCHING_SETUP", z, [
                f"Watching a breakout of {_name(z)}: a break needs two 15-minute closes beyond "
                "it, then acceptance and a retest — not a trade by itself."])  # fmt: skip
        if z is not None and z["distance_atr"] <= cfg.reach_atr:
            return _found("NEAR_ZONE", z, [f"{_name(z)} in reach — no break."])
        return _found("SCANNING", z, [f"No {word} broken in the last {BREAK_MAX_HOURS} h."])
    z = max(broken, key=lambda z: int(z["broken_at_t"]))
    lo, hi = z["bounds"]["low"], z["bounds"]["high"]
    edge = hi if long else lo
    since = minute.between(int(z["broken_at_t"]), as_of)
    if len(since) == 0:
        return _found("TRIGGER_PENDING", z, [f"{_name(z)} broken — no bars since."])
    reach = (float(since.high.max()) - edge) if long else (edge - float(since.low.min()))
    if reach < DISPLACEMENT_ATR * a15:
        return _found("TRIGGER_PENDING", z, [
            f"{_name(z)} broken; acceptance needs {DISPLACEMENT_ATR:g} ATR beyond it "
            f"(so far {reach / a15:.2f})."])  # fmt: skip
    if len(bars5) == 0 or int(bars5.t[-1]) < int(z["broken_at_t"]):
        return _found("TRIGGER_PENDING", z, [f"{_name(z)} broken and accepted — waiting for a "
                                             "retest."])  # fmt: skip
    o, h, low, c = _bar(bars5, -1)
    buffer = cfg.stop_buffer_atr * a15
    if long:
        came_back = low <= edge + RETEST_ATR * a15
        held, body, inside = c > edge, c > o, low >= lo - buffer
    else:
        came_back = h >= edge - RETEST_ATR * a15
        held, body, inside = c < edge, c < o, h <= hi + buffer
    if not came_back:
        return _found("TRIGGER_PENDING", z, [
            f"{_name(z)} broken and accepted ({reach / a15:.1f} ATR) — waiting for a retest of "
            f"{edge:g}."])  # fmt: skip
    if not (held and body and inside):
        return _found("TRIGGER_PENDING", z, [
            f"Retesting {_name(z)}: the bar did not hold beyond {edge:g} with a "
            f"{'bullish' if long else 'bearish'} body."])  # fmt: skip
    trigger = _trigger(bars5, -1, f"retest of broken {_name(z)} held at {edge:g} "
                       f"(break accepted by {reach / a15:.1f} ATR)")  # fmt: skip
    trigger["broken_at_utc"] = int(z["broken_at_t"])
    stop_ref = min(low, lo) if long else max(h, hi)
    return _found("TRIGGER_CONFIRMED", z, [], trigger=trigger, stop_ref=stop_ref)


# -- the order plan --------------------------------------------------------------------------------


def _plan(
    name: str,
    market: str,
    found: dict[str, Any],
    zone_map: dict[str, Any],
    long: bool,
    *,
    as_of: int,
    a15: float,
    trend_60: str,
    spec: ApexMarketSettings,
    cfg: ApexSettings,
    costs: float,
    spread: float,
) -> dict[str, Any]:
    tick = spec.tick
    z, trigger = found["zone"], found["trigger"]
    buffer = cfg.stop_buffer_atr * a15
    direction = "long" if long else "short"
    base = {"template": name, "direction": direction, "zone": z["zone_id"]}
    # Order prices: buys execute on the ask (bid + spread on bid-only data), sells on the bid;
    # the zones and bars are bid prices there, so every buy-side price carries the spread.
    if long:
        entry = snap(trigger["high"] + spread + tick, tick, "up")
        stop = snap(found["stop_ref"] - buffer - tick, tick, "down")
        ahead = sorted((x for x in zone_map["resistance"] if x["bounds"]["low"] > entry + tick
                        and x["zone_id"] != z["zone_id"]),
                       key=lambda x: x["bounds"]["low"])  # fmt: skip
        targets = [snap(x["bounds"]["low"] - tick, tick, "down") for x in ahead[:2]]
    else:
        entry = snap(trigger["low"] - tick, tick, "down")
        stop = snap(found["stop_ref"] + buffer + tick + spread, tick, "up")
        ahead = sorted((x for x in zone_map["support"] if x["bounds"]["high"] + spread
                        < entry - tick and x["zone_id"] != z["zone_id"]),
                       key=lambda x: -x["bounds"]["high"])  # fmt: skip
        targets = [snap(x["bounds"]["high"] + spread + tick, tick, "up") for x in ahead[:2]]
    risk = abs(entry - stop)
    if risk < spec.min_stop_points:
        return {**base, "reason": f"Stop {risk:g} is below the minimum of "
                                  f"{spec.min_stop_points:g} points."}  # fmt: skip
    if risk > cfg.max_stop_atr * a15:
        return {**base, "reason": f"Stop {risk / a15:.1f} ATR is wider than "
                                  f"{cfg.max_stop_atr:g} ATR."}  # fmt: skip
    if not targets:
        return {**base, "reason": "No opposing ranked zone mapped beyond the entry — no target "
                                  "is invented."}  # fmt: skip
    if abs(targets[0] - entry) < ROUTE_MIN_ATR * a15:
        return {**base, "reason": f"Target 1 is only {abs(targets[0] - entry) / a15:.2f} ATR "
                                  f"away (route at least {ROUTE_MIN_ATR:g} ATR)."}  # fmt: skip
    rr = [round((abs(t - entry) - costs) / risk, 2) for t in targets]
    if rr[0] < cfg.min_rr:
        return {**base, "reason": f"Reward/risk to target 1 after costs is {rr[0]:g} "
                                  f"(minimum {cfg.min_rr:g})."}  # fmt: skip
    stress = 1.5
    stressed = round(stress * costs + (stress - 1) * spread, 10)
    reward = abs(targets[0] - entry)
    break_even = round((risk + stressed) / (reward + risk), 4)
    with_trend = (trend_60 == "up") == long if trend_60 in ("up", "down") else None
    fields = {"market": market, "as_of": as_of, "template": name, "version": VERSION,
              "direction": direction, "entry": entry, "stop": stop, "targets": targets}  # fmt: skip
    valid_until = as_of + 60 * cfg.trigger_valid_minutes
    return {
        "id": candidate_id(fields),
        **base,
        "setup": name,
        "strategy_version": VERSION,
        "market": market,
        "created_at": as_of,
        "entry": {"type": "stop", "price": entry},
        "stop": stop,
        "targets": [
            {"price": t, "rr": r, "zone": x["zone_id"], "rank": x.get("rank")}
            for t, r, x in zip(targets, rr, ahead, strict=False)
        ],
        "zone_ids": [z["zone_id"], *(x["zone_id"] for x in ahead[: len(targets)])],
        "zone_rank": z.get("rank"),
        "zone_state": z["state"],
        "zone_quality": z["quality_score"],
        "zone_bounds": z["bounds"],
        "zone_confirmed_at": z.get("confirmed_at"),
        "risk_points": round(risk, 10),
        "atr15": round(a15, 6),
        "risk_ticks": round(risk / tick),
        "tick_value_usd": round(tick * spec.point_value, 4),
        "risk_usd": round(risk * spec.point_value, 2),  # before fees, one contract / lot
        "cost_usd": round(costs * spec.point_value, 2),
        "unit": spec.unit,
        "stressed_costs_points": stressed,
        "cost_stress_factor": stress,
        "break_even_probability": break_even,
        "valid_until": valid_until,
        "expiry_utc": valid_until,
        "max_hold_minutes": cfg.max_hold_minutes,
        "trend_60": trend_60,
        "with_trend": with_trend,
        "trigger": trigger,
        "trade_success_probability": unvalidated(),
        "user_executable": False,
        "rules": [
            f"Fill: {'buy' if long else 'sell'} stop {entry:g} — only if the move continues, "
            f"only until {cfg.trigger_valid_minutes} minutes after the trigger.",
            "Before the fill: target 1 traded first → missed (no chase).",
            "After the fill: stop and target in the same minute → counted as stop (conservative).",
            f"Still open after {cfg.max_hold_minutes} minutes → closed at that minute's close.",
            f"Invalid when price closes beyond the stop {stop:g} before the fill.",
        ],
    }


def unvalidated() -> dict[str, Any]:
    """The trade-success probability before a validated, calibrated model exists for it."""
    return {
        "event_definition": EVENT,
        "value": None,
        "validated": False,
        "calibrated": False,
        "effective_sample_size": 0,
        "uncertainty_lower": None,
        "uncertainty_upper": None,
        "label": "PROBABILITY NOT VALIDATED",
        "model_id": None,
        "promoted": False,
    }


# -- helpers ---------------------------------------------------------------------------------------


def _bar(b: Bars, i: int) -> tuple[float, float, float, float]:
    return float(b.open[i]), float(b.high[i]), float(b.low[i]), float(b.close[i])


def _trigger(b: Bars, i: int, rule: str) -> dict[str, Any]:
    o, h, lo, c = _bar(b, i)
    t = int(b.t[i])
    return {"bar_utc": t, "closed_utc": t + 60 * BAR_MINUTES, "open": o, "high": h, "low": lo,
            "close": c, "rule": rule}  # fmt: skip


def _confirmed_before(z: dict[str, Any], t: int) -> bool:
    confirmed = z.get("confirmed_at")
    return confirmed is not None and int(confirmed) <= t


def _name(z: dict[str, Any] | None) -> str:
    if z is None:
        return "no zone"
    side = "S" if z["side"] == "SUPPORT" else "R"
    rank = z.get("rank")
    where = f"{z['bounds']['low']:g}-{z['bounds']['high']:g}"
    return f"{side}{rank} {where}" if rank else f"zone {where}"


def _found(
    stage: str, zone: dict[str, Any] | None, reasons: list[str], **extra: Any
) -> dict[str, Any]:
    return {"stage": stage, "zone": zone, "reasons": reasons, "trigger": extra.pop("trigger", None),
            **extra}  # fmt: skip


def _state(found: dict[str, Any]) -> str:
    stage = found["stage"]
    if stage == "ENTRY_ARMED":
        return "RESEARCH_ONLY"  # until the eligibility gate qualifies it
    if stage == "TRIGGER_CONFIRMED":
        return "NO_TRADE"  # triggered, but the plan failed its checks (reason given)
    if stage == "TRIGGER_PENDING":
        return "TRIGGER_PENDING"
    if stage in ("NEAR_ZONE", "WATCHING_SETUP"):
        return "WATCH"
    return "NO_TRADE"


def _decision(name: str, sides: list[dict[str, Any]], reasons: list[str]) -> dict[str, Any]:
    plans = [s["plan"] for s in sides if "plan" in s]
    rejected = [s["rejected"] for s in sides if "rejected" in s]
    order = {s: k for k, s in enumerate(STAGES)}
    top = max(sides, key=lambda s: order[s["stage"]], default=None)
    stage = top["stage"] if top else "SCANNING"
    status = (
        "CANDIDATE" if plans
        else "WAITING_FOR_TRIGGER" if stage in ("NEAR_ZONE", "WATCHING_SETUP", "TRIGGER_PENDING",
                                                "TRIGGER_CONFIRMED")
        else "NO_TRADE"
    )  # fmt: skip
    summary = [
        {
            "direction": s["direction"],
            "stage": s["stage"],
            "setup_state": s["setup_state"],
            "zone": s["zone"]["zone_id"] if s["zone"] else None,
            "zone_rank": s["zone"].get("rank") if s["zone"] else None,
            "zone_side": s["zone"]["side"] if s["zone"] else None,
            "zone_bounds": s["zone"]["bounds"] if s["zone"] else None,
            "trigger": s["trigger"],
            "reasons": s["reasons"],
        }
        for s in sides
    ]
    return {
        "strategy": name,
        "version": SETUPS[name],
        "status": status,
        "direction": plans[0]["direction"] if len(plans) == 1 else "none",
        "stage": stage,
        "setup_state": top["setup_state"] if top else "NO_TRADE",
        "setups": summary,
        "trigger": next((s["trigger"] for s in sides if "plan" in s), None),
        "candidates": plans,
        "rejected": rejected,
        "reasons": reasons + [r for s in sides for r in s["reasons"]],
    }
