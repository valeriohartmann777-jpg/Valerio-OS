"""Markdown reports for a vendor bar file (``vendor_bars``): schema, quality, rolls.

The reports hold descriptions and counts only. A finding is never fixed here; the
findings table states for each problem how the pipeline handles it and why.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np

from .vendor_bars import ClockDecision

SPEC_ITEMS = (
    ("VWAP", "vwap"), ("RTH VWAP", "vwap_rth"), ("ETH VWAP", "vwap_eth"), ("session marker", "session_marker"),
    ("contract symbol", "contract"), ("rollover information", "roll_info"), ("trade count", "n_trades"),
    ("bid/ask or order-flow volume", "order_flow"), ("open interest", "open_interest"),
)


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def cell(v: Any) -> str:
    if isinstance(v, float):
        if not np.isfinite(v):
            return "–"
        if v == int(v) and abs(v) < 1e15:
            return f"{int(v):,}" if abs(v) >= 1e4 else str(int(v))
        return f"{v:,.2f}" if abs(v) >= 100 else f"{v:.4g}"
    if isinstance(v, (list, tuple)):
        v = ", ".join(str(x) for x in v)
    return str(v).replace("|", "\\|").replace("\n", " ")


def table(rows: list[dict[str, Any]], cols: list[str] | None = None) -> str:
    if not rows:
        return "_none_\n"
    cols = cols or list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(cell(r.get(c, "")) for c in cols) + " |" for r in rows]
    return "\n".join(out) + "\n"


def kv(pairs: list[tuple[str, Any]]) -> str:
    return table([{"field": k, "value": v} for k, v in pairs])


# =======================================================================================
# schema
# =======================================================================================
def render_schema(*, dataset: str, files: list[dict], bar_file: dict, profile: list[dict], specials: dict[str, list[str]],
                  mapping: dict[str, str], clock_ev: dict[str, Any], vwap_checks: list[dict], kept: list[str]) -> str:
    mb = bar_file["bytes"] / 1e6
    lines = [
        "# NQ data schema report (Kaggle)", "",
        f"Generated {now_utc()} by `scripts/assess_kaggle_nq.py` from the raw file, before any normalisation. "
        f"Source: Kaggle dataset `{dataset}`. The raw file is not changed.", "",
        "## Files in the download", "",
        table([{"file": f["name"], "kind": f["kind"], "bytes": f["bytes"], "bar file": f["candidate"], "reason": f["reason"]}
               for f in files]),
        "", "## Bar file", "",
        kv([("file", bar_file["name"]), ("sha256", bar_file["sha256"]), ("size", f"{mb:,.1f} MB"),
            ("data rows", bar_file.get("rows")), ("columns", len(profile)), ("header", bar_file.get("header")),
            ("timestamp column(s)", " + ".join(bar_file.get("timestamp_columns", []))),
            ("timestamp kind", clock_ev.get("kind")), ("first timestamp as written", clock_ev.get("first_raw")),
            ("last timestamp as written", clock_ev.get("last_raw")),
            ("unparseable timestamps", clock_ev.get("unparseable")),
            ("timestamps not on a whole minute", clock_ev.get("timestamps_with_seconds")),
            ("modal spacing (minutes)", clock_ev.get("modal_spacing_minutes"))]),
        "", "## Columns", "",
        table([{"column": r["column"], "role": r["role"], "description": r["description"], "dtype": r["dtype"],
                "nulls": r["nulls"], "min": r.get("min"), "max": r.get("max"), "examples": r["examples"]} for r in profile]),
    ]
    lows = [r for r in profile if "values" in r]
    if lows:
        lines += ["", "Value counts of low-cardinality text columns:", ""]
        lines += [f"- `{r['column']}`: {r['values']}" for r in lows]
    lines += ["", "## Items the specification asks about", "",
              table([{"item": name, "present": bool(specials.get(key)), "columns": specials.get(key) or "–"} for name, key in SPEC_ITEMS])]
    if vwap_checks:
        lines += ["", "Vendor VWAP columns, scope check (diagnostic; never used by the studies):", "",
                  table(vwap_checks, ["column", "days", "days_constant", "verdict"])]
    lines += [
        "", "## Mapping into the canonical input", "",
        table([{"raw column": a, "canonical": b} for a, b in mapping.items()]),
        "", f"Columns carried into the canonical input: {', '.join(kept)}. Every other column is documented above and "
        "not used: the frozen studies build sessions from `configs/sessions.yaml` (CME template, New York time) and "
        "compute VWAP, profiles and all levels from OHLCV themselves, causally. A vendor column could carry values that "
        "are only known at the end of a session; not reading them rules that out.", "",
    ]
    return "\n".join(lines)


# =======================================================================================
# clock
# =======================================================================================
def render_clock(c: ClockDecision) -> str:
    ev = c.evidence
    lines = ["## Timezone and bar label", ""]
    if c.decided:
        eq = ev.get("equivalent_zones")
        lines += [f"**Decided: timestamps are `{c.tz_in}` wall-clock time ({c.kind}), labelled by bar {c.label.upper()} time; "
                  f"session structure: {c.structure}.**" + (f" On this file's dates these zones are identical: {eq}." if eq else ""), ""]
    else:
        lines += [f"**{c.status}: the clock could not be determined. The pipeline stops before any research run.**", "",
                  *[f"- {r}" for r in c.reasons], ""]
    lines += [
        "Nothing is assumed. Three independent checks on the CME Globex session (New York time: trading-date start 18:00, "
        "maintenance break 17:00-18:00, cash open 09:30):", "",
        "1. **Session starts.** The first bar after every gap of 45 minutes or more is a session start. If it is "
        "18:00 New York time, the file clock is ahead of UTC by a whole number of hours; that offset must follow one "
        "timezone on every date, through both DST regimes and the weeks in which the US and Europe change on different "
        "dates. A start one bar after 18:00 means bars are labelled by their close.",
        "2. **Empty maintenance hour.** Per DST regime, the share of weekday trading dates with a bar at each New York "
        "minute must be near 1 everywhere except 17:00-17:59, where it must be near 0.",
        "3. **Cash-open volume spike.** Per DST regime, the median volume of the 09:30 bar must be the largest of "
        "09:20-09:40 and at least twice the 09:20-09:29 level.", "",
        "Rows without volume (no trade) are left out of all three checks, so vendor filler bars cannot hide the "
        "maintenance break or a session start.", "",
        kv([("timestamp kind", c.kind), ("rows without volume left out", ev.get("rows_without_volume_ignored", 0)),
            ("session starts found", ev.get("session_starts")),
            ("alignment with 18:00 / 09:30 anchors", ev.get("anchor_alignment")),
            ("label shares at session starts", ev.get("label_shares")),
            ("implied file-clock offsets (minutes ahead of UTC: count)", ev.get("implied_offsets_minutes")),
            ("starts not aligned to the anchor (examples)", ev.get("unaligned_starts"))]),
        "", "Method 1, share of session starts each timezone explains:", "",
        table([{"zone": z["zone"], "match": f"{100 * z['match']:.1f}%", "same as best": z["equivalent_to_best"]}
               for z in ev.get("zones", [])[:10]]),
    ]
    rows = []
    for ch in ev.get("checks", []):
        for reg, m in ch["presence"].items():
            sp = ch["spike"].get(reg, {})
            rows.append({"reading": f"{ch['zone']} / {ch['label']}", "regime": reg, "dates": m["n_dates"],
                         "template mismatches": m["mismatches"], "examples": m["mismatch_minutes"][:6],
                         "uncertain minutes": m["uncertain_minutes"],
                         "09:30 spike": (f"{'ok' if sp.get('ok') else 'no'} (max {sp.get('argmax')}, x{sp.get('ratio_0930', float('nan')):.1f})"
                                         if sp else "n/a")})
    lines += ["", "Methods 2 and 3 for the chosen reading and its nearest rivals:", "", table(rows)]
    if ev.get("single_regime"):
        lines += ["", "Only one DST regime is in the file, so a DST zone and its fixed-offset twin cannot be told apart; they "
                      "give the same instants for every bar of this file."]
    if ev.get("spike_unavailable"):
        lines += ["", f"Method 3: {ev['spike_unavailable']}."]
    return "\n".join(lines) + "\n"


# =======================================================================================
# quality
# =======================================================================================
def findings(clock: ClockDecision, norm_log: dict | None, extra: dict | None, chk: dict | None, roll: dict | None,
             max_removed_share: float) -> tuple[list[dict], list[str]]:
    """The findings table (problem, count, handling, reason) and the reasons to stop."""
    rows: list[dict] = []
    stops: list[str] = []

    def add(problem: str, count: Any, handling: str, reason: str) -> None:
        rows.append({"problem": problem, "count": count, "handling": handling, "reason": reason})

    if not clock.decided:
        stops.append("timezone or bar label not determinable: " + "; ".join(clock.reasons))
    ev = clock.evidence
    add("timestamps not on a whole minute", ev.get("timestamps_with_seconds", 0),
        "STOP if > 0", "the bar convention (open, close, last trade) would be unclear")
    if norm_log:
        lim = "removed; the minute stays missing (STOP if all removals exceed the limit above)"
        for why, d in norm_log["removed"].items():
            add(f"impossible bar: {why}", d["count"], lim,
            "the instant of such a bar is undefined" if "wall-clock" in why or "timestamp" in why
            else "an impossible bar cannot be traded or allocated to a price profile")
        nt = norm_log.get("no_trade_bars", {"count": 0, "share": 0.0, "flat": 0, "with_range": 0})
        add("bars with zero volume (no trade; any price, any wall-clock time)", nt["count"],
            "removed; the minute stays missing (not an error: no limit)",
            "a bar without a trade carries no trade price; the pipeline's own bars exist only for minutes with trades"
            + (f"; {nt['share']:.1%} of rows: the vendor fills empty minutes" if nt["share"] > 0.05 else ""))
        if nt.get("with_range"):
            add("  of these with a price range (prices without trades)", nt["with_range"], "removed with them",
                f"quote-based prices or a volume error (e.g. {nt.get('examples_with_range', [])[:3]})")
    if extra:
        add("rows out of time order in the file", extra["raw_backward_steps"], "sorted (stable) by the loader",
            "file order carries no information")
        add("duplicate timestamps, identical rows", extra["identical_duplicate_rows"],
            "first kept, the others dropped and counted by `manifest.prepare`", "the same bar twice")
        add("duplicate timestamps, conflicting values", extra["conflicting_duplicate_timestamps"],
            "STOP if > 0 (manifest `on_conflict: abort`)", "which of two different bars is right cannot be decided from the data")
        if extra.get("minutes_with_several_contracts"):
            add("minutes with bars of several contracts", extra["minutes_with_several_contracts"], "STOP",
                f"{extra['contracts']} contract values, e.g. {list(extra['contract_counts'])[:6]}")
            stops.append(f"the file holds several contracts per minute ({extra['contracts']} contract values): one "
                         "active contract per trading date must first be chosen by the frozen roll rule (not done "
                         "automatically)")
        elif extra["conflicting_duplicate_timestamps"]:
            stops.append(f"{extra['conflicting_duplicate_timestamps']} timestamps carry conflicting bars "
                         f"(e.g. {extra['conflicting_examples'][:3]})")
        add("bars inside the 17:00-18:00 maintenance break", extra["break_bars"], "kept, reported",
            "a real print belongs to no RTH session and never makes an event day")
        add("bars on weekend trading dates", extra["weekend_bars"], "kept, reported", "a trading date without RTH is never an event day")
        hol = [h for h in extra.get("holidays", []) if h["bars"]]
        add("trading dates that are NYSE holidays (with bars)", len(hol), "kept; not event days (no full RTH)",
            "protocol 2.2: event days need full RTH coverage")
        add("missing minutes on regular trading dates", extra["missing_minutes_regular_dates"], "not filled (protocol 2.4)",
            "a minute without trades has no bar; filling would invent prices")
        add("missing RTH minutes on regular trading dates", extra["missing_rth_minutes_regular_dates"],
            "not filled; dates under 95% RTH coverage are not event days", "protocol 2.2")
        add("identical consecutive bars (OHLCV equal)", extra["identical_consecutive_bars"], "kept, reported",
            f"possible repeated bars; longest run {extra['longest_identical_run']}")
    if chk and not chk.get("fatal"):
        add("isolated reverting price spikes (> 15x median range)", chk.get("suspected_spikes", 0), "kept, reported",
            "removing a real fast move would bias every result; none are filtered")
        add("frozen-feed RTH bars (>= 30 identical OHLC in a row)", chk.get("frozen_feed_bars_rth", 0), "kept, reported",
            "a stale feed would show here")
        add("bar-to-bar gaps > 25x the rolling median move", chk.get("large_price_gaps_total", 0),
            "kept; those at quarterly rolls are handled by the roll assessment", "see docs/KAGGLE_NQ_ROLLOVER_ASSESSMENT.md")
    if roll:
        con = roll.get("conclusion", {})
        add("series type / roll gaps", roll.get("windows_clear", 0),
            con.get("runner_path", "STOP: " + con.get("stop", "")), f"classification: {roll.get('classification')}")
        if not con.get("ok"):
            stops.append("roll assessment: " + con.get("stop", "not ok"))
    return rows, stops


def render_quality(*, dataset: str, bar_file: dict, clock: ClockDecision, rows: list[dict], stops: list[str],
                   extra: dict | None, generic_md: str | None, max_removed_share: float) -> str:
    verdict = "STOP" if stops else "PASS"
    lines = [
        "# NQ data quality report (Kaggle)", "",
        f"Generated {now_utc()} by `scripts/assess_kaggle_nq.py`. File `{bar_file['name']}` "
        f"(sha256 `{bar_file['sha256'][:16]}`), dataset `{dataset}`.", "",
        f"**Verdict: {verdict}.**" + ("" if not stops else " Nothing is registered and no study runs until this is resolved:"), "",
        *[f"- {s}" for s in stops], "" if stops else "",
        "Nothing is removed or changed silently: each row below states the count, what the pipeline does with it and why. "
        f"Removal is limited to impossible bars, at most {max_removed_share:.2%} of rows in total.", "",
        "## Findings and handling", "", table(rows), "",
        render_clock(clock),
    ]
    if extra:
        lines += ["## Session starts by DST regime (New York time)", "", table(extra.get("session_starts_by_regime", [])), "",
                  "## Holidays (NYSE calendar; CME trades an abbreviated session on most)", "", table(extra.get("holidays", [])), "",
                  "## Missing minutes", "",
                  kv([("regular trading dates (full RTH, weekday)", extra["regular_dates"]),
                      ("missing minutes on them", extra["missing_minutes_regular_dates"]),
                      ("per date (median / p95 / max)", extra["missing_minutes_per_date"]),
                      ("missing RTH minutes on them", extra["missing_rth_minutes_regular_dates"]),
                      ("dates missing more than 30 minutes (first 30)", extra["dates_missing_over_30_minutes"])]), "",
                  "## Examples", "",
                  f"- weekend bars: `{extra['weekend_examples']}`",
                  f"- maintenance-break bars: `{extra['break_examples']}`",
                  f"- conflicting duplicate timestamps: `{extra['conflicting_examples']}`", ""]
    if generic_md:
        body = generic_md.split("\n", 2)[-1] if generic_md.startswith("# ") else generic_md
        body = "\n".join("#" + ln if ln.startswith("#") else ln for ln in body.splitlines())
        lines += ["## Generic bar checks (`edgelab/data/quality.py`, the same checks as for every dataset)", "", body]
    return "\n".join(lines) + "\n"


# =======================================================================================
# rolls
# =======================================================================================
RISKS = [
    "Multi-session features (daily ATR, prior week, 20-session volume, pivot zones) still straddle a roll in an "
    "unadjusted series. The runner excludes the roll session and the session after it from being an event day; the "
    "remaining distortion is the calendar spread in units of the daily ATR and is not corrected (no pre-registered rule).",
    "The roll timing is read from price gaps. A large genuine move at a roll date could hide or mimic a roll gap; the "
    "dominance condition (largest gap at least 3x the next) and the per-window table make this visible.",
    "Early-2022 calendar spreads were small (low rates), so those roll gaps can be too small to separate from noise. "
    "Such windows are marked unclear and take the roll rule identified from the clear windows.",
    "Without a contract column the active contract between rolls is inferred, not documented by the vendor.",
    "If the vendor follows the expiring contract until expiry, its last days have little volume; the volume ratio "
    "column shows this (a value well below 1).",
]


def render_rolls(*, dataset: str, bar_file: dict, roll: dict, period: tuple[str, str]) -> str:
    con = roll.get("conclusion", {})
    rule = roll.get("rule", {})
    lines = [
        "# KAGGLE_NQ_ROLLOVER_ASSESSMENT", "",
        f"Generated {now_utc()} by `scripts/assess_kaggle_nq.py` from `{bar_file['name']}` (sha256 `{bar_file['sha256'][:16]}`), "
        f"Kaggle dataset `{dataset}`, bars {period[0]} to {period[1]} (New York time).", "",
        "## Result", "",
        kv([("series type", roll.get("classification")), ("adjustment for configs/datasets.yaml", con.get("adjustment")),
            ("roll windows assessable / with a clear gap", f"{roll.get('windows_assessable')} / {roll.get('windows_clear')}"),
            ("tick-grid conformity", f"{roll.get('tick_grid_conformity_pct', float('nan')):.3f}%"),
            ("lowest price", roll.get("min_price")),
            ("vendor roll timing (business days from expiry: count)", rule.get("offsets", "–")),
            ("roll gap at session open or inside a session", f"{rule.get('kind_mode', '–')} ({rule.get('kind_share', float('nan')):.0%})" if rule else "–"),
            ("share of positive roll gaps (new contract above old)", rule.get("positive_share", "–")),
            ("frozen exclusion covers every vendor roll", roll.get("all_rolls_covered", "–")),
            ("usable for absolute historical price levels", con.get("ok"))]),
        "",
        (f"**OK.** {con.get('runner_path')}." if con.get("ok") else f"**STOP.** {con.get('stop')}."), "",
        "## Method (fixed before the data was seen)", "",
        "A continuous front-month series that switches contracts without adjustment jumps by the calendar spread at the "
        "switch (the next contract trades above the expiring one by roughly the carry, price x (rate - dividend yield) x "
        "time; for NQ in 2023-2025 about 150-250 points, in early 2022 near zero). A back-adjusted series has no such "
        "jumps (difference adjustment: prices stay on the tick grid; ratio adjustment: they leave it).", "",
        f"Per quarterly expiry E (third Friday of Mar/Jun/Sep/Dec), every gap between consecutive bars in the trading dates "
        f"[E - {roll['params']['window_days'][0]} days, E + {roll['params']['window_days'][1]} days] is divided by a robust "
        "scale of its kind measured outside all windows (adjacent bars: 99.9th percentile of |gap|; bars after missing "
        "minutes, after the daily break and after a weekend: 99th percentile). The largest one is a clear roll gap if it is "
        f"at least {roll['params']['clear_z']:g} scales and {roll['params']['dominance']:g} times the next-largest in the window. "
        "Clear in at least half of the windows: unadjusted; in at most 10%: back-adjusted or gapless; otherwise unclear.", "",
        "The runner's frozen rule (RESEARCH_PROTOCOL.md 13.3) for a series without a contract column excludes the "
        "conventional roll date (third Friday minus 8 days) and the session after it. A vendor roll on another date is "
        "reported as not covered; then the pipeline stops for a decision instead of changing the rule.", "",
        "## Gap scales outside the roll windows (points)", "",
        table([{"kind": k, **v} for k, v in roll.get("gap_scales_outside_windows", {}).items()]), "",
        "## Per expiry", "",
        table([{"expiry": w["expiry"], "from": w["from"], "window in data": w["covered"], "largest gap at (NY)": w.get("jump_ts", "–"),
                "kind": w.get("jump_kind", "–"), "points": w.get("jump_points", float("nan")), "z": w.get("z", float("nan")),
                "next z": w.get("z_next", float("nan")), "clear": w["clear"], "bd from expiry": w.get("offset_bd", "–"),
                "volume ratio": w.get("volume_ratio_last5_vs_base", float("nan"))} for w in roll.get("windows", [])]),
    ]
    if roll.get("rolls"):
        lines += ["", "## Roll sessions", "",
                  table([{"expiry": r["expiry"], "from": r["from"], "first bar on the new contract": r["first_bar_new"],
                          "trading date": r["trading_date"], "how found": r["method"],
                          "excluded by the frozen rule": r["covered_by_frozen_exclusion"]} for r in roll["rolls"]])]
    lines += ["", "## Risks", "", *[f"- {r}" for r in RISKS], ""]
    return "\n".join(lines)
