"""JARVIS's written reading of an APEX analysis.

Built only from the analysis' own numbers — structure, zones, counted rates, candidates, data
status — so it can't say more than the analysis shows. No model call, no cost; the chat can go
deeper with the ``apex_analysis`` tool.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

CALIBRATION_TEXT = {
    "CALIBRATED": "In the last track record the stated rates matched what later touches did "
    "(calibrated) — still counts from the past, not promises.",
    "MISCALIBRATED": "In the last track record the stated rates did NOT match what later touches "
    "did — treat them as rough.",
}
NOT_CHECKED = (
    "The stated rates haven't been checked against later touches yet (run a track record) — "
    "read them as past frequencies, not probabilities."
)


def _price(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _rate(rate: dict[str, Any] | None) -> str:
    if not rate:
        return "no counted rate"
    if rate.get("p") is None:
        return f"too few earlier touches to count ({rate.get('n', 0)})"
    lo, hi = rate["ci"]
    return f"{rate['p']:.0%} of {rate['n']} earlier touches (95% {lo:.0%}-{hi:.0%})"


def narrate(analysis: dict[str, Any], calibration: str | None = None) -> dict[str, Any]:
    market = analysis["market"]
    data = analysis["data"]
    quality = analysis["quality"]
    update = data.get("update") or {}
    source = "SYNTHETIC test data" if data["synthetic"] else data["label"]
    question = (
        f"Explain the current APEX analysis for {market}: structure, the zones S1 and R1, the "
        "scenarios and whether there is a trade candidate — name the data source and the "
        "counted rates."
    )
    caveats = []
    if data["synthetic"]:
        caveats.append("Generated test data — nothing here says anything about the market.")

    if analysis.get("blocked"):
        blocks = [f for f in quality["findings"] if f["level"] == "BLOCK"]
        found = [
            f"{f['code']}: {f['message']}"
            for f in quality["findings"]
            if f["level"] in ("BLOCK", "WARN")
        ]
        if update.get("error"):
            found.append(f"The data update failed: {update['error']}")
        found.append("APEX → Data shows the cause and repairs the data.")
        return {
            "headline": f"{market}: no analysis — "
            + (blocks[0]["message"] if blocks else "the data check failed."),
            "points": found,
            "caveats": [*caveats, "Research only — APEX places no orders."],
            "question": question,
        }

    structure = analysis.get("structure") or {}
    hour, quarter = structure.get("60") or {}, structure.get("15") or {}
    headline = (
        f"{market} {_price(analysis.get('price'))} — 1h trend {hour.get('trend', '—')}, "
        f"15m {quarter.get('trend', '—')}."
    )
    points: list[str] = []
    if hour:
        hi, lo = hour.get("last_high"), hour.get("last_low")
        line = (
            f"1h structure: last high {_price(hi['price']) if hi else '—'}"
            f"{' (' + hi['label'] + ')' if hi and hi.get('label') else ''}, last low "
            f"{_price(lo['price']) if lo else '—'}"
            f"{' (' + lo['label'] + ')' if lo and lo.get('label') else ''}."
        )
        if hour.get("events"):
            event = hour["events"][-1]
            line += (
                f" Latest {event['type']} {event['direction']} through {_price(event['level'])}"
                f" at {_utc(event['t'])}."
            )
        points.append(line)
    zones = {z["id"]: z for z in analysis.get("zones", [])}
    for zid, side, where in (("S1", "Support", "below"), ("R1", "Resistance", "above")):
        z = zones.get(zid)
        if not z:
            continue
        away = "price inside" if z["inside"] else f"{z['distance_atr']:.1f} ATR {where}"
        points.append(
            f"{side} {zid} {_price(z['low'])}-{_price(z['high'])} ({', '.join(z['reasons'])}; "
            f"{away}): held on {_rate(z.get('rate'))}."
        )
    scenarios = {s["id"]: s for s in analysis.get("scenarios", [])}

    def case(first: str, second: str) -> str:
        parts = []
        for key in (first, second):
            s = scenarios.get(key)
            if s:
                parts.append(f"{s['statement'].rstrip('.')} — {_rate(s['rate'])}")
        return "; ".join(parts)

    long_case = case("S1-hold", "R1-break")
    short_case = case("R1-hold", "S1-break")
    if long_case:
        points.append(f"Long case: {long_case}.")
    if short_case:
        points.append(f"Short case: {short_case}.")
    candidates = analysis.get("candidates") or []
    for c in candidates:
        targets = ", ".join(
            f"target {k + 1} {_price(t['price'])} ({t['rr']}R after costs)"
            for k, t in enumerate(c["targets"])
        )
        trend = (
            "1h range"
            if c.get("with_trend") is None
            else "with the 1h trend"
            if c["with_trend"]
            else "against the 1h trend"
        )
        entry = _price(c["entry"]["price"])
        points.append(
            f"{c['direction'].upper()} candidate at {c['zone']}: limit {entry}"
            f", stop {_price(c['stop'])}, {targets}; valid until {_utc(c['valid_until'])}; "
            f"{trend}. A rule's output, not a recommendation."
        )
    if not candidates:
        reasons = []
        for r in analysis.get("rejected") or []:
            reason = str(r["reason"]).rstrip(".")
            text = (f"{r['direction']} at {r['zone']}: " if r.get("direction") else "") + reason
            if text not in reasons:
                reasons.append(text)
        points.append(
            "No trade now"
            + (": " + "; ".join(reasons[:3]) + "." if reasons else " — no rule fired.")
            + " Standing aside is the default."
        )
    summary = quality.get("summary") or {}
    line = f"Data: {source}"
    if data.get("provenance", {}).get("prices") == "bid" and "bid" not in source.lower():
        line += ", bid prices"
    if summary.get("last_close"):
        line += f"; newest bar closed {_utc(int(summary['last_close']))}"
    if update.get("requested"):
        line += "; updated now" if update.get("ok") else f"; update failed ({update.get('error')})"
    spread = data.get("spread") or {}
    if spread.get("measured"):
        line += (
            f"; spread median {spread['session_median']:g} in the session "
            f"(costs assume {spread['cost_points']:g})"
        )
    points.append(line + ".")
    warns = [f for f in quality["findings"] if f["level"] == "WARN" and f["code"] != "PROXY"]
    if warns:
        caveats.append("Data warnings: " + "; ".join(f"{f['code']}" for f in warns) + ".")
    if data.get("proxy"):
        caveats.append(data["proxy"])
    caveats.append("Rates are counted shares of earlier touches before this moment.")
    caveats.append(CALIBRATION_TEXT.get(calibration or "", NOT_CHECKED))
    caveats.append("Research only — APEX places no orders.")
    return {"headline": headline, "points": points, "caveats": caveats, "question": question}
