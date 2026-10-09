"""Data-quality checks and the DATA_QUALITY_REPORT.md renderer.

The report must exist, and be read, before any strategy research touches a file.
Checks are descriptive: they count and list problems, they never silently fix them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from ..config import SessionTemplate, minutes_of
from ..sessions import label_sessions, session_calendar
from .rolls import contract_changes, equity_index_roll_dates

EXAMPLES = 10


def _examples(index: pd.Index, mask: np.ndarray, n: int = EXAMPLES) -> list[str]:
    return [str(t) for t in index[np.asarray(mask)][:n]]


def expected_bars_per_day(template: SessionTemplate, bar_minutes: int) -> int:
    """Bars in a normal trading date (all minutes minus the maintenance break)."""
    total = 24 * 60
    if template.maintenance_break:
        a, b = (minutes_of(t) for t in template.maintenance_break)
        total -= (b - a) % (24 * 60)
    return total // bar_minutes


def dst_transitions(start: pd.Timestamp, end: pd.Timestamp, tz: str) -> list[dict[str, Any]]:
    """Calendar dates in [start, end] on which the UTC offset of ``tz`` changes."""
    days = pd.date_range(start.normalize().tz_localize(None), end.normalize().tz_localize(None) + pd.Timedelta(days=1), freq="D")
    noon = days + pd.Timedelta(hours=12)
    offs = noon.tz_localize(tz).map(lambda t: t.utcoffset())
    out = []
    for i in range(1, len(days)):
        if offs[i] != offs[i - 1]:
            out.append({"date": days[i].date(), "offset_before": str(offs[i - 1]), "offset_after": str(offs[i])})
    return out


def check_bars(
    bars: pd.DataFrame,
    *,
    instrument: str,
    source: str,
    template: SessionTemplate,
    bar_minutes: int,
    tick_size: float | None = None,
    roll_cfg: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run every quality check and return a nested dict (rendered by :func:`render_markdown`)."""
    idx = bars.index
    rep: dict[str, Any] = {"instrument": instrument, "source": source, "meta": meta or {}}
    n = len(bars)
    rep["rows"] = n
    if n == 0:
        rep["fatal"] = "empty file"
        return rep

    rep["timezone"] = str(idx.tz)
    rep["first_ts"] = str(idx.min())
    rep["last_ts"] = str(idx.max())
    rep["first_ts_utc"] = str(idx.min().tz_convert("UTC"))
    rep["last_ts_utc"] = str(idx.max().tz_convert("UTC"))
    rep["bar_minutes"] = bar_minutes

    # ordering and duplicates
    rep["monotonic"] = bool(idx.is_monotonic_increasing)
    dup_mask = idx.duplicated(keep=False)
    rep["duplicate_timestamps"] = int(idx.duplicated().sum())
    if dup_mask.any():
        d = bars[dup_mask]
        conflicting = d.groupby(level=0)[["open", "high", "low", "close", "volume"]].nunique().gt(1).any(axis=1)
        rep["duplicate_timestamps_conflicting_values"] = int(conflicting.sum())
        rep["duplicate_examples"] = _examples(idx, dup_mask)
    else:
        rep["duplicate_timestamps_conflicting_values"] = 0
        rep["duplicate_examples"] = []

    o, h, l, c, v = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    rep["nan_counts"] = {k: int(np.isnan(bars[k].to_numpy(dtype=float)).sum()) for k in ("open", "high", "low", "close", "volume")}
    invalid = {
        "high_lt_low": h < l,
        "open_gt_high": o > h,
        "open_lt_low": o < l,
        "close_gt_high": c > h,
        "close_lt_low": c < l,
        "nonpositive_price": (o <= 0) | (h <= 0) | (l <= 0) | (c <= 0),
    }
    rep["invalid_ohlc"] = {k: int(m.sum()) for k, m in invalid.items()}
    rep["invalid_ohlc_examples"] = {k: _examples(idx, m) for k, m in invalid.items() if m.any()}
    rep["negative_volume"] = int((v < 0).sum())
    rep["zero_volume"] = int((v == 0).sum())
    rep["zero_volume_pct"] = float((v == 0).mean() * 100)

    if tick_size:
        px = np.concatenate([o, h, l, c])
        px = px[np.isfinite(px)]
        q = px / tick_size
        on_grid = np.abs(q - np.round(q)) < 1e-6
        rep["tick_grid_conformity_pct"] = float(on_grid.mean() * 100)

    labels = label_sessions(idx, template)
    rep["trading_dates"] = int(labels["trading_date"].nunique())
    cal = session_calendar(labels, bar_minutes, template)
    exp_day = expected_bars_per_day(template, bar_minutes)
    rep["expected_bars_per_full_day"] = exp_day
    rep["unexpected_break_bars"] = int((labels["phase"] == "BREAK").sum())

    # coverage per date and per bucket
    cov = cal["n_bars"] / exp_day
    rep["days_full_coverage"] = int((cov >= 0.99).sum())
    rep["days_partial_coverage"] = int(((cov < 0.99) & (cov >= 0.5)).sum())
    rep["days_low_coverage"] = int((cov < 0.5).sum())
    rep["days_low_coverage_list"] = [str(d.date()) for d in cal.index[cov < 0.5][:40]]
    rep["early_close_dates"] = [str(d.date()) for d in cal.index[cal["early_close"]]]
    rep["no_rth_dates"] = [str(d.date()) for d in cal.index[cal["no_rth"]]]
    rep["full_rth_dates"] = int(cal["full_rth"].sum())

    # weekdays missing completely (holidays or vendor gaps)
    all_wd = pd.bdate_range(cal.index.min(), cal.index.max())
    missing_days = all_wd.difference(pd.DatetimeIndex(cal.index))
    rep["weekdays_without_bars"] = [str(d.date()) for d in missing_days]

    # bucket coverage on dates with a full RTH session
    full_dates = set(cal.index[cal["full_rth"]])
    lab_full = labels[labels["trading_date"].isin(full_dates)]
    bucket_rows = []
    for name, start, end in template.buckets:
        span = (minutes_of(end) - minutes_of(start)) % (24 * 60)
        exp = span // bar_minutes * len(full_dates)
        got = int((lab_full["bucket"] == name).sum())
        bucket_rows.append({"bucket": name, "present": got, "expected": int(exp), "coverage_pct": (100.0 * got / exp) if exp else float("nan")})
    rep["bucket_coverage"] = bucket_rows

    # time gaps inside a trading date (excluding the scheduled break)
    td = labels["trading_date"].to_numpy()
    same_day = td[1:] == td[:-1]
    step = (idx[1:] - idx[:-1]).total_seconds().to_numpy() / 60.0
    gap_mask = same_day & (step > bar_minutes)
    gaps = pd.DataFrame({"start": idx[:-1][gap_mask], "end": idx[1:][gap_mask], "minutes": step[gap_mask]})
    rep["intraday_gaps_count"] = int(len(gaps))
    rep["intraday_gaps_gt_5min"] = int((gaps["minutes"] > 5).sum())
    rep["intraday_gaps_gt_30min"] = int((gaps["minutes"] > 30).sum())
    rep["largest_intraday_gaps"] = gaps.sort_values("minutes", ascending=False).head(20).astype(str).to_dict("records")

    # price gaps between consecutive bars
    prev_c = np.r_[np.nan, c[:-1]]
    jump = np.abs(o - prev_c)
    med_move = pd.Series(np.abs(np.diff(c, prepend=np.nan))).rolling(2000, min_periods=200).median().to_numpy()
    rel = jump / np.where(med_move > 0, med_move, np.nan)
    first_of_day = np.r_[True, ~same_day]
    big = np.nan_to_num(rel) > 25
    rep["large_price_gaps_total"] = int(big.sum())
    rep["large_price_gaps_at_session_open"] = int((big & first_of_day).sum())
    rep["large_price_gaps_intrasession"] = int((big & ~first_of_day).sum())
    order = np.argsort(-np.nan_to_num(rel))[:20]
    rep["largest_price_gaps"] = [
        {"ts": str(idx[i]), "gap_points": float(o[i] - prev_c[i]), "x_median_move": float(rel[i]), "session_open": bool(first_of_day[i])}
        for i in order if np.isfinite(rel[i])
    ]

    # suspected corrupt bars: isolated spikes that revert, frozen feed
    rng = h - l
    med_rng = pd.Series(rng).rolling(2000, min_periods=200).median().to_numpy()
    next_c = np.r_[c[1:], np.nan]
    spike_up = (h - np.fmax(prev_c, next_c)) > 15 * med_rng
    spike_dn = (np.fmin(prev_c, next_c) - l) > 15 * med_rng
    spikes = np.nan_to_num(spike_up | spike_dn).astype(bool)
    rep["suspected_spikes"] = int(spikes.sum())
    rep["suspected_spike_examples"] = _examples(idx, spikes)
    same_ohlc = (o[1:] == o[:-1]) & (h[1:] == h[:-1]) & (l[1:] == l[:-1]) & (c[1:] == c[:-1])
    run = pd.Series(np.r_[False, same_ohlc]).groupby((~pd.Series(np.r_[False, same_ohlc])).cumsum()).cumsum().to_numpy()
    rth = labels["is_rth"].to_numpy()
    frozen = (run >= 30) & rth
    rep["frozen_feed_bars_rth"] = int(frozen.sum())
    rep["frozen_feed_examples"] = _examples(idx, frozen)

    # DST
    trans = dst_transitions(idx.min(), idx.max(), template.timezone)
    wc = idx.tz_convert(template.timezone).tz_localize(None)
    rep["dst_transitions"] = trans
    rep["local_wallclock_duplicates"] = int(pd.Index(wc).duplicated().sum())

    # rolls
    rolls: dict[str, Any] = {}
    if "contract" in bars.columns:
        ch = contract_changes(bars)
        rolls["type"] = "contract column present (individual contracts or stitched front month)"
        rolls["contracts"] = sorted(map(str, bars["contract"].astype(str).unique()))[:50]
        rolls["changes"] = ch.astype(str).to_dict("records")
    else:
        rolls["type"] = "no contract column: adjustment method UNKNOWN until documented by the vendor"
    if roll_cfg and roll_cfg.get("expiry") == "third_friday":
        rd = equity_index_roll_dates(
            idx.min().date(), idx.max().date(), tuple(roll_cfg.get("months", (3, 6, 9, 12))), int(roll_cfg.get("roll_offset_days", 8))
        )
        tdates = pd.DatetimeIndex(cal.index)
        roll_rows = []
        first_bar_pos = np.flatnonzero(first_of_day)
        first_bar_dates = pd.DatetimeIndex(td[first_bar_pos])
        for r in rd:
            k = tdates.searchsorted(pd.Timestamp(r))
            window = tdates[max(0, k - 2): k + 3]
            best = None
            for d in window:
                hit = np.flatnonzero(first_bar_dates == d)
                if hit.size:
                    i = first_bar_pos[hit[0]]
                    g = float(o[i] - prev_c[i]) if i > 0 else float("nan")
                    if best is None or abs(g) > abs(best[1]):
                        best = (str(d.date()), g)
            roll_rows.append({"expected_roll": str(r), "largest_open_gap_nearby": best})
        rolls["expected_roll_dates"] = roll_rows
    if tick_size and "tick_grid_conformity_pct" in rep:
        conf = rep["tick_grid_conformity_pct"]
        if conf < 99.0:
            rolls["adjustment_hint"] = f"only {conf:.2f}% of prices on the tick grid: consistent with RATIO back-adjustment"
        elif (np.nanmin(l) <= 0) if np.isfinite(l).any() else False:
            rolls["adjustment_hint"] = "non-positive prices: consistent with DIFFERENCE back-adjustment"
        else:
            rolls["adjustment_hint"] = "prices on tick grid: unadjusted or difference-adjusted; check roll-date gaps"
    rep["rolls"] = rolls
    return rep


def _md_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_none_\n"
    cols = list(rows[0].keys())
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(f"{r[c]:.2f}" if isinstance(r[c], float) else str(r[c]) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def render_markdown(reports: list[dict[str, Any]], title: str = "DATA_QUALITY_REPORT") -> str:
    """Render one or more quality reports as Markdown."""
    out = [f"# {title}\n", f"_Generated {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}_\n"]
    for rep in reports:
        out.append(f"\n## {rep['instrument']} — {rep['source']}\n")
        if rep.get("fatal"):
            out.append(f"**FATAL:** {rep['fatal']}\n")
            continue
        meta = rep.get("meta", {})
        out.append("### Identity\n")
        basic = [
            ("instrument", rep["instrument"]), ("source", rep["source"]), ("file sha256", meta.get("sha256", "n/a")),
            ("rows", rep["rows"]), ("trading dates", rep["trading_dates"]),
            ("first bar (NY)", rep["first_ts"]), ("last bar (NY)", rep["last_ts"]),
            ("first bar (UTC)", rep["first_ts_utc"]), ("last bar (UTC)", rep["last_ts_utc"]),
            ("bar length (min)", rep["bar_minutes"]), ("timezone", rep["timezone"]),
            ("vendor timestamp label", meta.get("label_convention", "n/a")), ("vendor timezone", meta.get("tz_in", "n/a")),
            ("bid/ask volume", meta.get("has_bid_ask_volume", "n/a")),
        ]
        out.append(_md_table([{"field": k, "value": v} for k, v in basic]))
        out.append("\n### Integrity\n")
        integ = [
            ("timestamps ordered", rep["monotonic"]),
            ("duplicate timestamps", rep["duplicate_timestamps"]),
            ("duplicates with conflicting values", rep["duplicate_timestamps_conflicting_values"]),
            ("NaN counts", rep["nan_counts"]),
            ("negative volume", rep["negative_volume"]),
            ("zero-volume bars", f"{rep['zero_volume']} ({rep['zero_volume_pct']:.2f}%)"),
            ("tick-grid conformity %", rep.get("tick_grid_conformity_pct", "n/a")),
            ("bars inside maintenance break", rep["unexpected_break_bars"]),
            ("wall-clock duplicate timestamps (NY)", rep["local_wallclock_duplicates"]),
        ]
        for k, val in rep["invalid_ohlc"].items():
            integ.append((f"invalid OHLC: {k}", val))
        out.append(_md_table([{"check": k, "result": v} for k, v in integ]))
        if rep.get("invalid_ohlc_examples"):
            out.append("\nInvalid OHLC examples: `" + str(rep["invalid_ohlc_examples"]) + "`\n")
        if rep.get("duplicate_examples"):
            out.append("\nDuplicate examples: `" + str(rep["duplicate_examples"]) + "`\n")
        out.append("\n### Coverage\n")
        cov = [
            ("expected bars per full day", rep["expected_bars_per_full_day"]),
            ("dates >= 99% coverage", rep["days_full_coverage"]),
            ("dates 50-99% coverage", rep["days_partial_coverage"]),
            ("dates < 50% coverage", rep["days_low_coverage"]),
            ("dates with full RTH", rep["full_rth_dates"]),
            ("intraday gaps (any)", rep["intraday_gaps_count"]),
            ("intraday gaps > 5 min", rep["intraday_gaps_gt_5min"]),
            ("intraday gaps > 30 min", rep["intraday_gaps_gt_30min"]),
        ]
        out.append(_md_table([{"metric": k, "value": v} for k, v in cov]))
        out.append("\nSession-bucket coverage (dates with full RTH):\n\n")
        out.append(_md_table(rep["bucket_coverage"]))
        out.append("\nLargest intraday time gaps:\n\n")
        out.append(_md_table(rep["largest_intraday_gaps"]))
        out.append("\n### Market closures\n")
        out.append(f"- Weekdays with no bars (holidays or vendor gaps): {len(rep['weekdays_without_bars'])} → `{rep['weekdays_without_bars'][:60]}`\n")
        out.append(f"- Dates without RTH bars: `{rep['no_rth_dates'][:60]}`\n")
        out.append(f"- Early-close dates: `{rep['early_close_dates'][:60]}`\n")
        out.append(f"- Dates with < 50% coverage: `{rep['days_low_coverage_list']}`\n")
        out.append("\n### DST transitions in range\n")
        out.append(_md_table([{k: str(v) for k, v in t.items()} for t in rep["dst_transitions"]]))
        out.append("\n### Price gaps and suspected corruption\n")
        out.append(f"- Bar-to-bar gaps > 25x rolling median move: {rep['large_price_gaps_total']} "
                   f"(session open: {rep['large_price_gaps_at_session_open']}, intrasession: {rep['large_price_gaps_intrasession']})\n")
        out.append(f"- Isolated reverting spikes (> 15x median range): {rep['suspected_spikes']} `{rep['suspected_spike_examples']}`\n")
        out.append(f"- Frozen-feed RTH bars (>= 30 identical OHLC in a row): {rep['frozen_feed_bars_rth']} `{rep['frozen_feed_examples']}`\n\n")
        out.append(_md_table(rep["largest_price_gaps"]))
        out.append("\n### Futures rolls\n")
        rolls = rep["rolls"]
        out.append(f"- Series type: {rolls.get('type')}\n")
        if "adjustment_hint" in rolls:
            out.append(f"- Adjustment hint: {rolls['adjustment_hint']}\n")
        if rolls.get("changes"):
            out.append("\nContract changes:\n\n" + _md_table(rolls["changes"]))
        if rolls.get("expected_roll_dates"):
            out.append("\nExpected roll dates vs. largest session-open gap nearby:\n\n")
            out.append(_md_table([{"expected_roll": r["expected_roll"], "largest_open_gap_nearby": r["largest_open_gap_nearby"]} for r in rolls["expected_roll_dates"]]))
    return "".join(out)
