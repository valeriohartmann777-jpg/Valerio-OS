"""The causal zone map: the four strongest supports and resistances as of a moment (spec F).

Version ``ZONEMAP_VERSION``. Everything is computed from bars that had **closed** by ``as_of``
(``Series.until``); the same moment always gives the same map, whatever came later.

**Candidates** (each with its formation and confirmation time):

- confirmed 15-minute and 60-minute swing highs / lows — a pivot exists only once its right-hand
  bars have closed (``swing_bars`` 15-minute, 2 hourly); the zone is the pivot bar's wick
  (body edge → extreme), kept between 0.1 and 0.5 ATR(15m) wide;
- the previous trading day's and week's high / low (known from the start of the next day /
  week), the last completed Asia / London / New York session highs and lows (known at the
  session's end; the session still running is only a candidate);
- displacement origins: a 15-minute bar of at least 2 ATR with a close in its outer quarter
  marks the demand / supply it left;
- major round numbers near the price;
- the previous day's bar-volume profile (POC / value area) — a **proxy** (bar or quote volume,
  not trades): shown, never scored.

Candidates closer than 0.15 ATR merge into one zone (at most 0.8 ATR wide).

**Episodes.** After confirmation, 15-minute bars are walked in order. A touch starts an episode
only after price had left the zone by at least 0.25 ATR (one visit = one episode, however many
bars it lasts). An episode ends when a bar closes back outside on the zone's side and is
classified: ``rejection`` (moved ≥ 1.5 ATR away within 8 bars), ``sweep_reclaim`` (traded through
the far edge by ≥ 0.05 ATR first), ``weak`` (less reaction), ``pending`` (too recent to know).
Two consecutive closes beyond the far edge by 0.1 ATR **break** the zone. A broken zone **flips**
only after that acceptance *and* a retest from the other side that is rejected (≥ 1.5 ATR); if
price instead closes back on the original side twice, the break was a **fakeout** and the zone
is active again (with the fakeout as counter-evidence).

**States:** ``CANDIDATE`` (not confirmed) · ``CONFIRMED`` (no touch yet) · ``ACTIVE`` (≥ 1
rejection) · ``TESTED`` (touched, no rejection yet) · ``WEAKENING`` (≥ 3 touches with the last
reaction < 0.5 ATR, or ≥ 2 sweeps) · ``BROKEN`` · ``FLIPPED`` · ``EXPIRED`` (no confirmation or
touch for ``expire_days``). Every transition is listed with its time and reason.

**Quality score 0-100** (never a probability) from capped families so that one event seen
several ways is not counted several times: structure (≤ 35), price reaction (≤ 35), freshness
(≤ 15), width (≤ 10), minus counter-evidence. **Rank 1-4** per side among non-broken,
non-expired zones with score ≥ ``MIN_SCORE``, by score x relevance (1 within 6 ATR, then 6 /
distance, at least 0.3). Fewer than four qualify → fewer are shown, with a note.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from jarvis.apex.capability import Evidence
from jarvis.learning.features import atr as atr_series
from jarvis.learning.features import volume_profile
from jarvis.learning.market import Bars, local_clock, resample

ZONEMAP_VERSION = "zonemap-1.0.0"
NY = "America/New_York"
MIN_SCORE = 30.0
RANKED = 4
EXPIRE_DAYS = 10
SESSIONS = {
    "asia": (19 * 60, 3 * 60),
    "london": (3 * 60, 9 * 60 + 30),
    "newyork": (9 * 60 + 30, 16 * 60),
}
STRUCTURE_POINTS = {
    "htf_swing": 15, "week": 15, "prev_day": 12, "session": 8, "swing": 8,
    "displacement": 8, "round_major": 6,
}  # fmt: skip
LIVE_STATES = ("CONFIRMED", "ACTIVE", "TESTED", "WEAKENING", "FLIPPED")
REACTIONS = ("rejection", "sweep_reclaim", "flip_retest")
REACT = 1.5  # ATR(15m) a reaction must travel within 8 bars to count as a rejection


def _utc(t: int | None) -> str | None:
    return None if t is None else datetime.fromtimestamp(int(t), UTC).isoformat()


@dataclass
class Member:
    kind: (
        str  # htf_swing | swing | prev_day | week | session | displacement | round_major | profile
    )
    label: str
    low: float
    high: float
    formed: int
    confirmed: int | None  # None = still a candidate
    role: str  # support | resistance | either

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


@dataclass
class ZoneState:
    low: float
    high: float
    members: list[Member]
    role: str = "support"
    state: str = "CONFIRMED"
    episodes: list[dict[str, Any]] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    last_tested: int | None = None
    broken_at: int | None = None
    flipped_at: int | None = None
    fakeouts: int = 0

    @property
    def confirmed(self) -> int | None:
        times = [m.confirmed for m in self.members if m.confirmed is not None]
        return min(times) if times else None

    @property
    def formed(self) -> int:
        return min(m.formed for m in self.members)


# Candidates --------------------------------------------------------------------------------


def _band(extreme: float, body_edge: float, a: float, high_side: bool) -> tuple[float, float]:
    width = min(max(abs(extreme - body_edge), 0.1 * a), 0.5 * a)
    return (extreme - width, extreme) if high_side else (extreme, extreme + width)


def _swings(b: Bars, a: np.ndarray, n: int, kind: str, label: str) -> list[Member]:
    out: list[Member] = []
    step = 60 * b.minutes
    for j in range(n, len(b) - n):
        aj = float(a[j]) if np.isfinite(a[j]) else 0.0
        if aj <= 0:
            continue
        confirmed = int(b.t[j + n]) + step  # close of the n-th right-hand bar
        if b.high[j] > b.high[j - n : j].max() and b.high[j] >= b.high[j + 1 : j + n + 1].max():
            lo, hi = _band(float(b.high[j]), float(max(b.open[j], b.close[j])), aj, True)
            out.append(Member(kind, f"{label} high", lo, hi, int(b.t[j]), confirmed, "resistance"))
        if b.low[j] < b.low[j - n : j].min() and b.low[j] <= b.low[j + 1 : j + n + 1].min():
            lo, hi = _band(float(b.low[j]), float(min(b.open[j], b.close[j])), aj, False)
            out.append(Member(kind, f"{label} low", lo, hi, int(b.t[j]), confirmed, "support"))
    return out


def _line(price: float, a: float, tick: float) -> tuple[float, float]:
    half = max(0.15 * a, 2 * tick)
    return price - half, price + half


def candidates(bars: Bars, as_of: int, a15: float, tick: float, round_step: float) -> list[Member]:
    """Every zone candidate known at ``as_of`` (``bars``: closed one-minute bars until then)."""
    out: list[Member] = []
    fifteen = resample(bars, 15)
    if len(fifteen) and int(fifteen.t[-1]) + 900 > as_of:
        fifteen = fifteen.between(int(fifteen.t[0]), int(fifteen.t[-1]))
    hourly = resample(bars, 60)
    if len(hourly) and int(hourly.t[-1]) + 3600 > as_of:
        hourly = hourly.between(int(hourly.t[0]), int(hourly.t[-1]))
    if len(fifteen) < 20:
        return out
    a_15 = atr_series(fifteen.high, fifteen.low, fifteen.close, 14)
    out += _swings(fifteen, a_15, 3, "swing", "15m swing")
    if len(hourly) >= 10:
        a_60 = atr_series(hourly.high, hourly.low, hourly.close, 14) / 2.0  # in ~15m ATR units
        out += _swings(hourly, a_60, 2, "htf_swing", "1h swing")
    # Displacement origins.
    rng = fifteen.high - fifteen.low
    for j in np.flatnonzero(np.isfinite(a_15) & (a_15 > 0) & (rng >= 2.0 * a_15)).tolist():
        loc = (fifteen.close[j] - fifteen.low[j]) / max(rng[j], 1e-12)
        aj = float(a_15[j])
        done = int(fifteen.t[j]) + 900
        if loc >= 0.75:
            lo = float(fifteen.low[j])
            hi = min(float(fifteen.open[j]), lo + 0.6 * aj)
            if hi > lo:
                out.append(Member("displacement", "bullish displacement origin", lo, hi,
                                  int(fifteen.t[j]), done, "support"))  # fmt: skip
        elif loc <= 0.25:
            hi = float(fifteen.high[j])
            lo = max(float(fifteen.open[j]), hi - 0.6 * aj)
            if hi > lo:
                out.append(Member("displacement", "bearish displacement origin", lo, hi,
                                  int(fifteen.t[j]), done, "resistance"))  # fmt: skip
    out += _session_levels(bars, as_of, a15, tick)
    # Major round numbers near the price.
    price = float(bars.close[-1])
    base = np.floor(price / round_step) * round_step
    for k in range(-2, 3):
        level = float(base + k * round_step)
        lo, hi = _line(level, a15, tick)
        out.append(Member("round_major", f"round number {level:g}", lo, hi,
                          int(bars.t[0]), int(bars.t[0]), "either"))  # fmt: skip
    return out


def _session_levels(bars: Bars, as_of: int, a15: float, tick: float) -> list[Member]:
    clock = local_clock(bars.t, NY)
    minute = clock.minute
    trading_day = (clock.day * 1440 + minute + 360) // 1440  # 18:00 New York starts the day
    days = np.unique(trading_day)
    out: list[Member] = []
    current = int(days[-1])
    first_of = {int(d): int(bars.t[np.argmax(trading_day == d)]) for d in days[-8:]}

    def add(mask: np.ndarray, kind: str, label: str, known: int | None) -> None:
        if not mask.any():
            return
        hi_p, lo_p = float(bars.high[mask].max()), float(bars.low[mask].min())
        start = int(bars.t[mask][0])
        for price, side, word in ((hi_p, "resistance", "high"), (lo_p, "support", "low")):
            lo, hi = _line(price, a15, tick)
            out.append(Member(kind, f"{label} {word}", lo, hi, start, known, side))

    previous = [int(d) for d in days if d < current]
    if previous:
        prev = previous[-1]
        add(trading_day == prev, "prev_day", "previous day", first_of.get(current))
        mask = trading_day == prev
        typical = (bars.high[mask] + bars.low[mask] + bars.close[mask]) / 3.0
        if mask.sum() > 30:
            poc, vah, val = volume_profile(typical, bars.volume[mask], float(bars.close[mask][-1]))
            for price, word in ((poc, "POC"), (vah, "VAH"), (val, "VAL")):
                lo, hi = _line(float(price), a15, tick)
                out.append(Member("profile", f"previous day {word} (proxy)", lo, hi,
                                  int(bars.t[mask][0]), first_of.get(current),
                                  "either"))  # fmt: skip
    # Previous trading week (Monday-based week of the trading day).
    week = (trading_day + 3) // 7  # day 0 = Thursday 1970-01-01 → weeks start on Monday
    this_week = int(week[-1])
    earlier = week < this_week
    if earlier.any():
        last_week = int(week[earlier].max())
        known = int(bars.t[np.argmax(week == this_week)])
        add(week == last_week, "week", "previous week", known)
    # Sessions of the current and previous trading day: known at their end.
    for day in [*previous[-1:], current]:
        for name, (start, end) in SESSIONS.items():
            if start < end:
                mask = (trading_day == day) & (minute >= start) & (minute < end)
            else:
                mask = (trading_day == day) & ((minute >= start) | (minute < end))
            if not mask.any():
                continue
            last_t = int(bars.t[mask][-1])
            now_minute = int(minute[-1])
            running = (
                (start <= now_minute < end)
                if start < end
                else (now_minute >= start or now_minute < end)
            )
            ended = day != current or not running
            add(mask, "session", f"{name} session", last_t + 60 if ended else None)
    return out


# Zones -----------------------------------------------------------------------------------


def _cluster(members: list[Member], a: float) -> list[ZoneState]:
    """Merge candidates that overlap or lie within 0.15 ATR; a merged zone is at most 0.8 ATR
    wide, centred on its members (zones never overlap each other)."""
    zones: list[ZoneState] = []
    for m in sorted(members, key=lambda x: x.mid):
        last = zones[-1] if zones else None
        if last is not None and m.low - last.high <= 0.15 * a:
            last.members.append(m)
            low, high = min(last.low, m.low), max(last.high, m.high)
            if high - low > 0.8 * a:
                centre = float(np.median([x.mid for x in last.members]))
                low, high = max(low, centre - 0.4 * a), min(high, centre + 0.4 * a)
            last.low, last.high = low, high
            continue
        zones.append(ZoneState(m.low, m.high, [m]))
    return zones


def _walk(z: ZoneState, b: Bars, a: np.ndarray, as_of: int) -> None:
    """Episodes, breaks and flips of one zone on 15-minute bars after its confirmation."""
    confirmed = z.confirmed
    step = 60 * b.minutes
    if confirmed is None:
        z.state = "CANDIDATE"
        z.transitions.append(
            {"state": "CANDIDATE", "at": _utc(z.formed), "reason": "formed, not yet confirmed"}
        )
        return
    start = int(np.searchsorted(b.t + step, confirmed, side="left"))
    first = min(start, len(b) - 1)
    ref_close = float(b.close[first]) if len(b) else z.high
    roles = {m.role for m in z.members} - {"either"}
    if ref_close > z.high:
        z.role = "support"
    elif ref_close < z.low:
        z.role = "resistance"
    else:
        z.role = (
            roles.pop()
            if len(roles) == 1
            else ("support" if ref_close >= (z.low + z.high) / 2 else "resistance")
        )
    z.transitions.append(
        {
            "state": "CONFIRMED",
            "at": _utc(confirmed),
            "reason": "+".join(sorted({m.kind for m in z.members})),
        }
    )
    armed = True
    episode: dict[str, Any] | None = None
    beyond = 0
    broken = False
    back = 0
    retest: dict[str, Any] | None = None
    i = start + 1
    n = len(b)
    while i < n:
        ai = float(a[i]) if np.isfinite(a[i]) and a[i] > 0 else 1e-9
        lo, hi, cl = float(b.low[i]), float(b.high[i]), float(b.close[i])
        t_close = int(b.t[i]) + step
        sup = z.role == "support"
        # distance outside on the zone's side and the far edge
        if not broken:
            touching = lo <= z.high if sup else hi >= z.low
            through = cl < z.low - 0.1 * ai if sup else cl > z.high + 0.1 * ai
            outside_far = lo > z.high + 0.25 * ai if sup else hi < z.low - 0.25 * ai
            if episode is None and touching and armed:
                episode = {"start": _utc(int(b.t[i])), "extreme": lo if sup else hi}
                z.last_tested = t_close
            if episode is not None:
                episode["extreme"] = (
                    min(episode["extreme"], lo) if sup else max(episode["extreme"], hi)
                )
                beyond = beyond + 1 if through else 0
                if beyond >= 2:
                    episode.update(kind="break", end=_utc(t_close))
                    z.episodes.append(episode)
                    episode = None
                    broken, z.broken_at, back = True, t_close, 0
                    z.state = "BROKEN"
                    z.transitions.append(
                        {
                            "state": "BROKEN",
                            "at": _utc(t_close),
                            "reason": "two closes beyond the far edge",
                        }
                    )
                    i += 1
                    continue
                closed_out = cl > z.high if sup else cl < z.low
                if closed_out:
                    window = slice(i, min(i + 9, n))
                    reaction = (
                        (float(b.high[window].max()) - z.high)
                        if sup
                        else (z.low - float(b.low[window].min()))
                    )
                    swept = (
                        episode["extreme"] < z.low - 0.05 * ai
                        if sup
                        else episode["extreme"] > z.high + 0.05 * ai
                    )
                    known = i + 8 < n
                    kind = (
                        ("sweep_reclaim" if swept else "rejection") if reaction >= REACT * ai
                        else "weak" if known else "pending"
                    )  # fmt: skip
                    episode.update(
                        kind=kind, end=_utc(t_close), reaction_atr=round(reaction / ai, 2)
                    )
                    z.episodes.append(episode)
                    episode = None
                    armed = False
            elif not armed and outside_far:
                armed = True
        else:
            # Broken: wait for a retest from the other side (flip) or a return (fakeout).
            back_side = cl > z.high if sup else cl < z.low
            back = back + 1 if back_side else 0
            if back >= 2:
                z.fakeouts += 1
                broken, z.broken_at = False, None
                z.state = "ACTIVE"
                z.transitions.append(
                    {
                        "state": "ACTIVE",
                        "at": _utc(t_close),
                        "reason": "fakeout: closed back on the original side twice",
                    }
                )
                armed = False
                i += 1
                continue
            touching_other = hi >= z.low if sup else lo <= z.high
            if retest is None and touching_other:
                retest = {"bar": i}
            if retest is not None:
                held = cl < z.low if sup else cl > z.high
                window = slice(i, min(i + 9, n))
                reaction = (
                    (z.low - float(b.low[window].min()))
                    if sup
                    else (float(b.high[window].max()) - z.high)
                )
                if held and reaction >= REACT * ai:
                    z.role = "resistance" if sup else "support"
                    z.flipped_at = t_close
                    z.state = "FLIPPED"
                    z.transitions.append(
                        {
                            "state": "FLIPPED",
                            "at": _utc(t_close),
                            "reason": "break accepted, retest from the other side rejected",
                        }
                    )
                    broken = False
                    armed = False
                    episode = None
                    retest = None
                    z.episodes.append(
                        {
                            "kind": "flip_retest",
                            "start": _utc(int(b.t[i])),
                            "end": _utc(t_close),
                            "reaction_atr": round(reaction / ai, 2),
                        }
                    )
        i += 1
    if episode is not None:
        episode.update(kind="open", end=None)
        z.episodes.append(episode)
    if z.state in ("BROKEN",):
        return
    rejections = [
        e for e in z.episodes if e["kind"] in ("rejection", "sweep_reclaim", "flip_retest")
    ]
    touches = [e for e in z.episodes if e["kind"] != "flip_retest"]
    sweeps = [e for e in z.episodes if e["kind"] == "sweep_reclaim"]
    last_reaction = next(
        (e.get("reaction_atr") for e in reversed(touches) if "reaction_atr" in e), None
    )
    if z.state != "FLIPPED":
        if (len(touches) >= 3 and (last_reaction or 0) < 0.5) or len(sweeps) >= 2:
            new = "WEAKENING"
        elif rejections:
            new = "ACTIVE"
        elif touches:
            new = "TESTED"
        else:
            new = "CONFIRMED"
        if new != z.state:
            z.transitions.append(
                {
                    "state": new,
                    "at": _utc(z.last_tested or confirmed),
                    "reason": f"{len(touches)} touch episode(s), {len(rejections)} rejection(s)",
                }
            )
        z.state = new
    recent = max(t for t in (confirmed, z.last_tested, z.flipped_at) if t is not None)
    if as_of - recent > EXPIRE_DAYS * 86400:
        z.state = "EXPIRED"
        z.transitions.append(
            {
                "state": "EXPIRED",
                "at": _utc(recent + EXPIRE_DAYS * 86400),
                "reason": f"no confirmation or touch for {EXPIRE_DAYS} days",
            }
        )


def _score(
    z: ZoneState, a: float, as_of: int, vwap_status: str
) -> tuple[float, list[dict[str, Any]], list[str]]:
    kinds = {m.kind for m in z.members}
    evidence: list[dict[str, Any]] = []
    counter: list[str] = []
    known = _utc(z.confirmed or z.formed)

    def ev(factor: str, status: Evidence, points: float, detail: str) -> None:
        evidence.append({"factor": factor, "status": status.value, "known_at_utc": known,
                         "points": round(points, 1), "detail": detail})  # fmt: skip

    structure = min(35, sum(STRUCTURE_POINTS.get(k, 0) for k in kinds))
    ev("price_structure", Evidence.CONFIRMED, structure,
       ", ".join(sorted(m.label for m in z.members if m.kind != "profile")) or "—")  # fmt: skip
    rejections = [e for e in z.episodes if e["kind"] in ("rejection", "flip_retest")]
    sweeps = [e for e in z.episodes if e["kind"] == "sweep_reclaim"]
    reactions = [e["reaction_atr"] for e in rejections + sweeps if "reaction_atr" in e]
    # Rejections and sweeps are views of the same family of events: one cap for both.
    reaction = min(30, 10 * len(rejections) + 8 * len(sweeps))
    if reactions and float(np.mean(reactions)) >= 1.5:
        reaction += 5
    reaction = min(35, reaction)
    ev("price_rejection", Evidence.CONFIRMED, reaction,
       f"{len(rejections)} distinct rejection episode(s), {len(sweeps)} sweep-and-reclaim(s) "
       "(one capped family)")  # fmt: skip
    recent = max(t for t in (z.confirmed or z.formed, z.last_tested, z.flipped_at) if t is not None)
    age = (as_of - recent) / 86400
    fresh = 15 if age <= 1 else 10 if age <= 3 else 5 if age <= 7 else 0
    width = (z.high - z.low) / a if a > 0 else 1.0
    narrow = 10 if width <= 0.3 else 6 if width <= 0.5 else 2
    penalty = 8 * z.fakeouts + (10 if z.state == "WEAKENING" else 0)
    if z.fakeouts:
        counter.append(f"{z.fakeouts} failed break(s) through this zone")
        ev("counter_fakeout", Evidence.CONTRADICTED, -8 * z.fakeouts, counter[-1])
    if z.state == "WEAKENING":
        counter.append("weakening: repeated touches with shrinking reactions or repeated sweeps")
        ev("counter_weakening", Evidence.CONTRADICTED, -10, counter[-1])
    if "profile" in kinds:
        ev(
            "volume_profile",
            Evidence.PROXY,
            0,
            "previous day POC/VAH/VAL from bar or quote volume — not traded volume, not scored",
        )
    else:
        missing = Evidence.UNAVAILABLE if vwap_status == "UNAVAILABLE" else Evidence.PROXY
        ev("volume_profile", missing, 0,
           "no traded-volume profile (needs trades)")  # fmt: skip
    ev("delta", Evidence.UNAVAILABLE, 0, "needs trades with aggressor side")
    ev("absorption", Evidence.UNAVAILABLE, 0, "ABSORPTION NOT MEASURABLE without depth and trades")
    total = max(0.0, min(100.0, structure + reaction + fresh + narrow - penalty))
    components = {"structure": structure, "price_reaction": reaction, "freshness": fresh,
                  "width": narrow, "counter_evidence": -penalty}  # fmt: skip
    evidence.append({"factor": "score_components", "status": Evidence.CONFIRMED.value,
                     "known_at_utc": known, "points": round(total, 1),
                     "detail": components})  # fmt: skip
    return round(total, 1), evidence, counter


def zone_map(
    bars: Bars,
    market: str,
    as_of: int,
    *,
    a15: float,
    tick: float,
    round_step: float,
    reach_atr: float,
    source_ids: list[str],
    vwap_status: str = "UNAVAILABLE",
) -> dict[str, Any]:
    """The zone snapshot as of ``as_of`` (``bars``: closed one-minute bars of one contract)."""
    if len(bars) == 0 or a15 <= 0:
        return _empty(market, as_of, "No bars or no volatility measure yet.")
    price = float(bars.close[-1])
    members = candidates(bars, as_of, a15, tick, round_step)
    fifteen = resample(bars, 15)
    if len(fifteen) and int(fifteen.t[-1]) + 900 > as_of:
        fifteen = fifteen.between(int(fifteen.t[0]), int(fifteen.t[-1]))
    a_15 = atr_series(fifteen.high, fifteen.low, fifteen.close, 14)
    zones = _cluster(members, a15)
    out: list[dict[str, Any]] = []
    for z in zones:
        _walk(z, fifteen, a_15, as_of)
        score, evidence, counter = _score(z, a15, as_of, vwap_status)
        inside = z.low <= price <= z.high
        side = "SUPPORT" if (z.high < price or (inside and z.role == "support")) else "RESISTANCE"
        edge = z.high if side == "SUPPORT" else z.low
        distance = 0.0 if inside else abs(price - edge) / a15
        relevance = 1.0 if distance <= 6 else max(0.3, 6 / distance)
        primary = min(z.members, key=lambda m: (m.formed, m.kind))
        zid = (
            "z-"
            + hashlib.sha1(
                f"{market}|{primary.kind}|{primary.formed}|"
                f"{round(z.low / tick)}|{round(z.high / tick)}".encode()
            ).hexdigest()[:12]
        )
        out.append({
            "zone_id": zid,
            "market": market,
            "side": side,
            "role_at_confirmation": z.role,
            "bounds": {"low": round(z.low, 10), "high": round(z.high, 10)},
            "formation_time_utc": _utc(z.formed),
            "confirmed_time_utc": _utc(z.confirmed),
            "as_of_utc": _utc(as_of),
            "state": z.state,
            "quality_score": score,
            "rank": None,
            "rank_value": round(score * relevance, 2),
            "distance_atr": round(distance, 2),
            "inside": inside,
            "timeframe_of_origin": "1h" if "htf_swing" in {m.kind for m in z.members} else "15m",
            "kinds": sorted({m.kind for m in z.members}),
            "labels": [m.label for m in z.members],
            "source_ids": source_ids,
            "evidence": evidence,
            "counterevidence": counter,
            "episodes": z.episodes,
            "touches": len([e for e in z.episodes if e["kind"] != "flip_retest"]),
            "rejections": len([e for e in z.episodes if e["kind"] in REACTIONS]),
            "last_tested_at": _utc(z.last_tested),
            "broken_at": _utc(z.broken_at),
            "flipped_at": _utc(z.flipped_at),
            "transitions": z.transitions,
            "version": ZONEMAP_VERSION,
            "invalidation_reason": "broken: two closes beyond the far edge" if z.state == "BROKEN"
            else "expired" if z.state == "EXPIRED" else None,
        })  # fmt: skip
    return rank(out, market, as_of, price, a15, reach_atr)


def rank(
    zones: list[dict[str, Any]], market: str, as_of: int, price: float, a15: float, reach_atr: float
) -> dict[str, Any]:
    """Top four per side by score x relevance; the rest stay listed without a rank."""
    eligible = [z for z in zones if z["state"] in LIVE_STATES and z["quality_score"] >= MIN_SCORE]
    notes: list[str] = []
    sides: dict[str, list[dict[str, Any]]] = {}
    for side in ("SUPPORT", "RESISTANCE"):
        ranked = sorted(
            (z for z in eligible if z["side"] == side),
            key=lambda z: (-z["rank_value"], z["distance_atr"]),
        )[:RANKED]
        for k, z in enumerate(ranked, 1):
            z["rank"] = k
        sides[side] = sorted(ranked, key=lambda z: z["rank"])
        if len(ranked) < RANKED:
            notes.append(
                f"Only {len(ranked)} {side.lower()} zone(s) meet the evidence bar "
                f"(score ≥ {MIN_SCORE:g}, "
                "not broken or expired) — no zone is invented to fill four."
            )
    ranked_ids = {z["zone_id"] for s in sides.values() for z in s}
    others = sorted(
        (z for z in zones if z["zone_id"] not in ranked_ids), key=lambda z: -z["quality_score"]
    )[:12]

    def pick(side: str) -> dict[str, Any] | None:
        near = [z for z in sides[side] if z["distance_atr"] <= reach_atr]
        best = (
            max(near, key=lambda z: z["quality_score"])
            if near
            else (sides[side][0] if sides[side] else None)
        )
        if best is None:
            return None
        return {"zone_id": best["zone_id"], "rank": best["rank"], "bounds": best["bounds"],
                "distance_atr": best["distance_atr"], "state": "WATCH" if near else "FAR",
                "quality_score": best["quality_score"]}  # fmt: skip

    return {
        "version": ZONEMAP_VERSION,
        "market": market,
        "as_of_utc": _utc(as_of),
        "as_of": as_of,
        "price": price,
        "atr15": round(a15, 6),
        "support": sides["SUPPORT"],
        "resistance": sides["RESISTANCE"],
        "others": others,
        "buy_zone": pick("SUPPORT"),
        "sell_zone": pick("RESISTANCE"),
        "notes": notes,
        "min_score": MIN_SCORE,
        "score_is_probability": False,
    }


def _empty(market: str, as_of: int, why: str) -> dict[str, Any]:
    return {"version": ZONEMAP_VERSION, "market": market, "as_of_utc": _utc(as_of), "as_of": as_of,
            "price": None, "atr15": 0.0, "support": [], "resistance": [], "others": [],
            "buy_zone": None, "sell_zone": None, "notes": [why], "min_score": MIN_SCORE,
            "score_is_probability": False}  # fmt: skip


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    """Content hash of a zone snapshot (stored once; the same as-of always hashes the same)."""
    import json

    raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()
