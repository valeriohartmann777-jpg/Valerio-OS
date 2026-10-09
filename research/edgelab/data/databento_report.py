"""reports/TRADE_DATA_QUALITY_REPORT.md from the validation and build JSON files.

The report holds counts, dates, contract symbols and a few aggregate statistics; no
trade, bar or per-session price level of the licensed data is written to it.
"""

from __future__ import annotations

from typing import Any

from .databento_build import FATAL_COUNTERS, REVIEW_COUNTERS

INFO_COUNTERS = {
    "records": "trade records in the files",
    "records_outright": "records of outright futures of the product",
    "records_excluded_non_outright": "records of spreads and other instruments (kept in the raw files, never used)",
    "outright_records_active_contract": "outright records of the contract active under the frozen roll rule",
}


def _runs(by_date: dict[str, Any]) -> list[str]:
    """``{date: contract}`` -> ``["ESH4: 2024-01-02 .. 2024-03-06 (46 dates)", ...]``."""
    out, cur, first, last, n = [], None, None, None, 0
    for d, c in sorted(by_date.items()):
        if c != cur:
            if cur is not None:
                out.append(f"{cur}: {first} .. {last} ({n} dates)")
            cur, first, n = c, d, 0
        last, n = d, n + 1
    if cur is not None:
        out.append(f"{cur}: {first} .. {last} ({n} dates)")
    return out


def _findings(fatal: list[str], review: list[str]) -> list[str]:
    lines = ["**Fatal (nothing is built or run):** " + ("none" if not fatal else "")]
    lines += [f"- {f}" for f in fatal]
    lines += ["", "**To review (kept in the data; handling goes into the DATA_QUALITY journal note):** "
              + ("none" if not review else "")]
    lines += [f"- {r}" for r in review]
    return lines


def render_validation(v: dict[str, Any]) -> str:
    c = v["trade_checks"]
    lines = [f"## Trades {v['product']} ({v['parent']}), {v['period'][0]} -> {v['period'][1]}", "",
             f"Validated {v['validated_utc']}. Result: **{'PASS' if v['passed'] else 'FAIL'}**.", ""]
    lines += _findings(v["fatal"], v["review"]) + [""]
    lines += ["| raw file | records | sha256 (first 16) |", "|---|---:|---|"]
    for files in v["raw_files"].values():
        lines += [f"| {f['path']} | {f['records']:,} | {f['sha256'][:16]} |" for f in files]
    k = v["contracts"]
    lines += ["", f"Instruments in the definitions: {k['instruments_in_definitions']}; outright futures used: "
              f"{', '.join(k['outright_symbols']) or 'none'}; excluded: "
              + (", ".join(f"{n} {r}" for r, n in k["excluded_by_reason"].items()) or "none") + ".", "",
              "| check | count |", "|---|---:|"]
    for key, text in {**INFO_COUNTERS, **FATAL_COUNTERS, **REVIEW_COUNTERS}.items():
        lines.append(f"| {text} | {int(c.get(key, 0)):,} |")
    lat = c.get("latency_ms", {})
    if lat:
        lines += ["", f"Capture minus exchange time (worst file): median {lat['median']:.3f} ms, p99 {lat['p99']:.3f} ms, "
                  f"max {lat['max']:.1f} ms."]
    for key in ("outright_off_tick_examples", "outright_outside_session_examples"):
        if c.get(key):
            lines.append(f"Examples ({key.replace('_', ' ')}, UTC): {', '.join(c[key])}.")
    if c.get("records_by_instrument_class"):
        by_class = ", ".join(f"{k} {n:,}" for k, n in c["records_by_instrument_class"].items())
        lines.append(f"Records by instrument class: {by_class}"
                     + " (F = outright future, S = calendar spread).")
    lines += ["", "Active contract (frozen calendar roll):", ""] + [f"- {r}" for r in _runs(v["active_contract_by_trading_date"])]
    return "\n".join(lines) + "\n"


def render_build(b: dict[str, Any]) -> str:
    s = b["session_checks"]
    lines = [f"## Dataset {b['dataset_id']} ({b['product']}, {b['period'][0]} -> {b['period'][1]})", "",
             f"Built {b['built_utc']}. Result: **{'PASS' if b['passed'] else 'FAIL'}**. "
             f"{b['trading_dates']} trading dates ({b['first_trading_date']} .. {b['last_trading_date']}), "
             f"{b['bars_active']:,} one-minute bars of the active contract built from {b['trades_active']:,} trades. "
             "Minutes without a trade have no bar (nothing is filled).", ""]
    lines += _findings(b["fatal"], b["review"]) + [""]
    lines += [f"Contracts used: {', '.join(b['contracts_used'])}. Rolls (frozen rule: third Friday minus "
              f"{b['roll_rule'].get('roll_offset_days')} days; the runner excludes each contract-change date): "
              + ("; ".join(f"{r['trading_date']} {r['from']} -> {r['to']}" for r in b["rolls"]) or "none") + ".", ""]
    if b.get("incomplete_sessions_by_request_window"):
        lines += [f"Sessions cut by the request window (kept, flagged incomplete in the level tables): "
                  f"{', '.join(b['incomplete_sessions_by_request_window'])}.", ""]
    lines += ["Session opens in New York and UTC time per DST regime (first bar of each complete session):", "",
              "| regime | sessions | first bar New York | first bar UTC |", "|---|---:|---|---|"]
    for r, d in s.get("session_open_by_regime", {}).items():
        lines.append(f"| {r} | {d['sessions']} | {d['first_bar_ny']} | {d['first_bar_utc']} |")
    lines += ["", f"Full RTH sessions ({s['rth_minutes_full_session']} one-minute bars): "
              f"{s['sessions_full_rth']} of {s['sessions']}. "
              f"Bars in the maintenance break: {s['bars_in_maintenance_break']}; on weekend trade dates: "
              f"{s['bars_on_weekend_trade_dates']}.", ""]
    cmp = s.get("rth_profile_trades_vs_1m_uniform", {})
    if cmp:
        lines += ["RTH profile: exact (trades) vs. the pre-registered approximation (1-minute bars, uniform allocation), "
                  "absolute difference in ticks over complete sessions:", "",
                  "| level | sessions | median | p90 | max | share within 1 tick |", "|---|---:|---:|---:|---:|---:|"]
        for k, d in cmp.items():
            if d.get("sessions"):
                lines.append(f"| {k.upper()} | {d['sessions']} | {d['median_abs_ticks']:.1f} | {d['p90_abs_ticks']:.1f} | "
                             f"{d['max_abs_ticks']:.1f} | {d['share_within_1_tick']:.0%} |")
        lines.append("")
    if s.get("roll_gaps"):
        lines += ["Gap between the contracts at each roll (last RTH bar of the prior session; the series is unadjusted):", "",
                  "| roll date | from -> to | spread in ATR_d(14) |", "|---|---|---:|"]
        for g in s["roll_gaps"]:
            val = "n/a (fewer than 14 sessions)" if g["spread_in_atr_d"] is None else f"{g['spread_in_atr_d']:+.2f}"
            lines.append(f"| {g['roll_trading_date']} | {g['from']} -> {g['to']} | {val} |")
        lines.append("")
    liq = b.get("liquidity", {})
    if liq.get("median_active_share") is not None:
        lines += [f"Liquidity check of the frozen rule (diagnostic only, never used to choose a contract): median share of "
                  f"outright volume in the active contract {liq['median_active_share']:.1%}; the active contract was the "
                  f"previous session's volume leader on {liq['share_dates_active_was_prev_session_volume_leader']:.0%} of dates."]
        for r in liq.get("rolls", []):
            lines.append(f"- roll {str(r['roll_trading_date'])[:10]}: the new contract first out-traded the old one on "
                         f"{str(r['volume_crossover_date'])[:10]} "
                         f"({r['business_days_crossover_before_roll']} business days before the roll)")
        lines.append("")
    p = b["products"]
    lines.append("Exact volume-at-price products (not committed): " + "; ".join(
        f"{ph}: {d['sessions']} session profiles ({d['complete_sessions']} complete), {d['developing_rows']:,} developing rows, "
        f"{d['composite_rows_filled']} filled {b['composite_sessions']}-session composites" for ph, d in p.items()) + ".")
    return "\n".join(lines) + "\n"


def render_report(validations: list[dict[str, Any]], builds: list[dict[str, Any]]) -> str:
    head = [
        "# TRADE_DATA_QUALITY_REPORT", "",
        "Generated by `scripts/validate_market_data.py` and `scripts/build_market_data.py` from Databento trades "
        "(GLBX.MDP3). Every trade is checked; fatal findings stop the pipeline, findings to review stay in the data and "
        "are handled in the `DATA_QUALITY <dataset id>` journal note. The bar-level report of the registered datasets "
        "is `reports/DATA_QUALITY_REPORT.md`.", "",
    ]
    if not validations and not builds:
        head.append("_No Databento data has been validated yet._")
        return "\n".join(head) + "\n"
    body = [render_validation(v) for v in sorted(validations, key=lambda v: (v["product"], v["period"][0]))]
    body += [render_build(b) for b in sorted(builds, key=lambda b: b["dataset_id"])]
    return "\n".join(head) + "\n" + "\n".join(body)
