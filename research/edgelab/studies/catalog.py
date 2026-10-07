"""The pre-registered event studies, one function per study ID.

Each function turns a :class:`StudyContext` and the study's frozen parameters
(``configs/event_studies.yaml``) into a :class:`StudyData`: events with outcomes,
controls with outcomes, the primary test, the G6 twin test (when the primary control
is not already the twin) and descriptive tables. Definitions follow
``hypotheses/HYPOTHESES_*.md``; every interpretation the prose left open is stated in
the function docstring and in the YAML, and was fixed before any data was loaded.

Outcome columns (signed by ``direction``, entry at the next bar's open, RTH-bounded):
``r{h}`` = return to the close of bar pos+h in ATR units of the event bar (5m ATR14);
``mfe12`` / ``mae12``; ``reod`` = return to the close of the bar ending 15:55 in ATR_d
units; ``reod_atr`` the same in ATR units.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..events.study import first_passage, forward_outcomes
from ..features.fvg import detect_fvgs
from ..features.profile import build_profile, tpo_profile, volume_nodes
from ..features.regime import tercile
from ..resample import resample_bars
from .context import StudyContext
from .controls import momentum_twin, partner_days, shifted_draws, strength_twin, time_matched
from .detectors import (
    band_fades,
    breakout_retests,
    breakouts,
    dedupe,
    first_close_beyond,
    in_window,
    level_runs,
    sweep_reclaim,
    zone_entries,
)
from .inference import TestInput

HORIZONS = (1, 3, 6, 12, 24, 48)


@dataclass
class StudyData:
    events: pd.DataFrame
    controls: pd.DataFrame | None = None
    primary: TestInput | None = None
    twin: TestInput | None = None
    econ: TestInput | None = None
    econ_scale: float = 1.0
    hurdle_norm: np.ndarray | None = None
    status: str = "OK"
    notes: list[str] = field(default_factory=list)
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    conditions: tuple[str, ...] = ()


# =======================================================================================
# helpers
# =======================================================================================
def child(spec: dict, sid: str) -> dict:
    """Spec of another registered study (for child studies that reuse its events)."""
    return {**spec["_cfg"]["studies"][sid], "id": sid, "_cfg": spec["_cfg"], "control_draws": spec["control_draws"]}


def win(p: dict, key: str = "window") -> tuple[int, int]:
    a, b = p[key]
    ha, ma = (int(x) for x in a.split(":"))
    hb, mb = (int(x) for x in b.split(":"))
    return ha * 60 + ma, hb * 60 + mb


def hhmm(s: str) -> int:
    h, m = (int(x) for x in s.split(":"))
    return h * 60 + m


def seed_of(ctx: StudyContext, sid: str, salt: int = 0) -> int:
    return int(ctx.seed + sum(ord(ch) * (31 ** i) for i, ch in enumerate(sid)) % 100_000 + salt)


def concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Row-bind; when every frame is empty the result keeps all their (typed) columns."""
    given = [f for f in frames if f is not None]
    full = [f for f in given if len(f)]
    if full:
        return pd.concat(full, ignore_index=True)
    out = pd.DataFrame({"pos": pd.Series(dtype=np.int64), "direction": pd.Series(dtype=np.int64)})
    for f in given:
        for c in f.columns:
            if c not in out.columns:
                out[c] = pd.Series(dtype=f[c].dtype)
    return out


def date_array(df: pd.DataFrame) -> np.ndarray:
    """Trading dates of a frame's rows as datetime64 (also for an empty frame)."""
    if "date" not in df.columns or not len(df):
        return np.array([], dtype="datetime64[ns]")
    return pd.to_datetime(df["date"]).to_numpy()


def add_outcomes(ctx: StudyContext, df: pd.DataFrame) -> pd.DataFrame:
    """Attach trading date, day code, ATR, ATR_d and signed forward outcomes."""
    df = df.reset_index(drop=True).copy()
    if df.empty:
        df["date"] = pd.Series(dtype="datetime64[ns]")
        for col in ["day", "atr", "atr_d", *[f"r{h}" for h in HORIZONS], "mfe12", "mae12", "reod", "reod_atr"]:
            df[col] = pd.Series(dtype=float)
        return df
    pos = df["pos"].to_numpy(np.int64)
    d = df["direction"].to_numpy(float)
    day = ctx.day5[pos]
    df["day"] = day
    df["date"] = ctx.days.to_numpy()[np.clip(day, 0, None)]
    a = ctx.atr5[pos]
    df["atr"] = a
    ad = np.where(day >= 0, ctx.atr_d[np.clip(day, 0, None)], np.nan)
    df["atr_d"] = ad
    fo = forward_outcomes(ctx.b5, pos, d, a, HORIZONS, ctx.sid5)
    for h in HORIZONS:
        df[f"r{h}"] = fo[f"ret_atr_{h}"].to_numpy()
    df["mfe12"] = fo["mfe_atr_12"].to_numpy()
    df["mae12"] = fo["mae_atr_12"].to_numpy()
    entry = fo["entry"].to_numpy()
    ep = ctx.eod_pos[np.clip(day, 0, None)]
    ok = (day >= 0) & (ep > pos) & np.isfinite(entry)
    exit_ = np.where(ok, ctx.c[np.clip(ep, 0, None)], np.nan)
    df["reod"] = d * (exit_ - entry) / ad
    df["reod_atr"] = d * (exit_ - entry) / a
    return df


def add_day_conditions(ctx: StudyContext, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    day = np.clip(df["day"].to_numpy(np.int64), 0, None)
    df["vol_tercile"] = ctx.vol_regime[day]
    df["trend_tercile"] = ctx.trend_regime[day]
    df["bucket"] = ctx.lab5["bucket"].astype(str).to_numpy()[df["pos"].to_numpy(np.int64)]
    return df


def diff_test(ev: pd.DataFrame, ct: pd.DataFrame, col: str, label: str, groups=("ev", "ct"), sign: float = 1.0) -> TestInput:
    return TestInput(
        "diff",
        np.concatenate([date_array(ev), date_array(ct)]),
        np.r_[ev[col].to_numpy(float), ct[col].to_numpy(float)],
        np.r_[np.full(len(ev), groups[0]), np.full(len(ct), groups[1])],
        groups,
        sign=sign,
        label=label,
    )


def stack(parts: dict[str, pd.DataFrame], col: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    dates, vals, grp = [], [], []
    for g, df in parts.items():
        dates.append(date_array(df))
        vals.append(df[col].to_numpy(float))
        grp.append(np.full(len(df), g))
    return np.concatenate(dates), np.concatenate(vals), np.concatenate(grp)


def dod_test(parts: dict[str, pd.DataFrame], col: str, label: str) -> TestInput:
    """``parts`` in order (a1, b1, a2, b2)."""
    d, v, g = stack(parts, col)
    return TestInput("dod", d, v, g, tuple(parts), label=label)


def shifted_controls(
    ctx: StudyContext, sid: str, arrays: dict[str, np.ndarray], detect: Callable[[dict], pd.DataFrame], anchor: np.ndarray, n: int,
    adaptive: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Rerun ``detect`` on ``n`` randomized-level draws. ``adaptive`` maps array names that
    are nearest-pivot-zone levels to their side ('res' / 'sup'); those come from the
    displaced zone set (``StudyContext.pz_shift_levels``), all others from another day."""
    adaptive = adaptive or {}
    if adaptive and n > len(ctx.pz_shift_levels):
        raise ValueError(f"{sid}: {n} draws requested, pz_shift_draws = {len(ctx.pz_shift_levels)}")
    static = {k: v for k, v in arrays.items() if k not in adaptive}
    draws = shifted_draws(ctx, static, anchor, n_draws=n, seed=seed_of(ctx, sid, 1)) if static else [{} for _ in range(n)]
    out = []
    for r, lv in enumerate(draws):
        lv = {**lv, **{k: ctx.pz_shift_levels[r][side] for k, side in adaptive.items()}}
        e = detect(lv)
        if len(e):
            out.append(e.assign(draw=r))
    return concat(out)


def tm_controls(ctx: StudyContext, sid: str, ev: pd.DataFrame, n: int, inherit: tuple[str, ...] = ()) -> pd.DataFrame:
    ct = time_matched(ctx, ev, n=n, seed=seed_of(ctx, sid, 2))
    for col in inherit:
        if col in ev.columns and len(ct):
            ct[col] = ev[col].to_numpy()[ct["event_idx"].to_numpy()]
    return ct


def finish(ctx: StudyContext, ev: pd.DataFrame, ct: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    ev = add_day_conditions(ctx, add_outcomes(ctx, ev))
    if ct is not None:
        ct = add_day_conditions(ctx, add_outcomes(ctx, ct))
    return ev, ct


def tercile_labels(x: np.ndarray, ref: np.ndarray | None = None) -> np.ndarray:
    """Sample terciles (cut points from ``ref``, default ``x``): '0', '1', '2', or '' if NaN."""
    x = np.asarray(x, float)
    r = x if ref is None else np.asarray(ref, float)
    r = r[np.isfinite(r)]
    if len(r) < 3:
        return np.full(len(x), "", dtype=object)
    q1, q2 = np.quantile(r, [1 / 3, 2 / 3])
    lab = np.where(x <= q1, "0", np.where(x <= q2, "1", "2")).astype(object)
    lab[~np.isfinite(x)] = ""
    return lab


def b30(ctx: StudyContext) -> pd.DataFrame:
    """30-minute bars anchored 09:30 with day code and close minute."""
    key = "_b30"
    if key not in ctx.__dict__:
        b = resample_bars(ctx.bars1, 30, tz=ctx.template.timezone, anchor="09:30")
        wall = b.index.tz_convert(ctx.template.timezone)
        mod = wall.hour * 60 + wall.minute
        lab_td = (wall.tz_localize(None) + pd.Timedelta(minutes=(24 * 60 - hhmm("18:00")) % (24 * 60))).normalize()
        b["day"] = ctx.days.get_indexer(lab_td)
        b["mod"] = np.asarray(mod)
        b["close_min"] = b["mod"] + 30
        rth = (b["mod"] >= ctx.rth_start_min) & (b["mod"] < ctx.rth_end_min)
        b.loc[~rth, "day"] = -1
        ctx.__dict__[key] = b
    return ctx.__dict__[key]


def pos_closing_at(ctx: StudyContext, day: int, close_min: int) -> int:
    """5m position of day ``day`` whose bar closes at ``close_min`` (-1 if missing)."""
    s5, e5 = ctx.rth_bounds5
    if s5[day] < 0:
        return -1
    seg = np.flatnonzero(ctx.close_min5[s5[day]: e5[day]] == close_min)
    return int(s5[day] + seg[0]) if seg.size else -1


# =======================================================================================
# Family A - auction market theory
# =======================================================================================
def _value_levels(ctx: StudyContext) -> dict[str, np.ndarray]:
    return {"val": ctx.per_bar(ctx.pv_val), "vah": ctx.per_bar(ctx.pv_vah)}


def _prior_visits(ctx: StudyContext, ev: pd.DataFrame, zone_atr: float = 0.10) -> np.ndarray:
    """Number of separate visits to the probed level's zone in the session before t0."""
    out = np.zeros(len(ev), dtype=np.int64)
    s5, _ = ctx.rth_bounds5
    for j, r in enumerate(ev.itertuples(index=False)):
        s, t0, L = int(s5[int(r.day)]), int(r.t0), float(r.level)
        if t0 <= s:
            continue
        seg = np.arange(s, t0)
        z = zone_atr * ctx.atr5[seg]
        inside = (ctx.l[seg] <= L + z) if r.direction > 0 else (ctx.h[seg] >= L - z)
        out[j] = int(np.sum(inside & ~np.r_[False, inside[:-1]]))
    return out


def study_A001(ctx: StudyContext, spec: dict) -> StudyData:
    """HA01: probe beyond prior VAL/VAH by 0.10 ATR after a close inside value, close back
    inside within 3 bars; one event per side per session; vs shifted-reference value."""
    p = spec["params"]
    w = win(p)

    def detect(lv: dict) -> pd.DataFrame:
        kw = dict(mode="pen", pen_atr=p["pen_atr"], n_bars=p["reclaim_bars"], window=w, arm_inside=True, near_side_open=False)
        return concat([sweep_reclaim(ctx, lv["val"], -1, **kw), sweep_reclaim(ctx, lv["vah"], +1, **kw)])

    lv = _value_levels(ctx)
    ev = detect(lv)
    ct = shifted_controls(ctx, "A001", lv, detect, ctx.rth_open, spec["control_draws"])
    for df in (ev, ct):
        if len(df):
            df["touch_number"] = np.where(_prior_visits(ctx, df) == 0, "1", "2+")
    ev, ct = finish(ctx, ev, ct)
    for df in (ev, ct):
        if len(df):
            k = df["day"].to_numpy()
            o = ctx.rth_open[k]
            df["open_in_value"] = np.where((o >= ctx.pv_val[k]) & (o <= ctx.pv_vah[k]), "yes", "no")
            val2, vah2 = ctx.prev(ctx.pv_val), ctx.prev(ctx.pv_vah)
            df["va_overlap"] = np.where((ctx.pv_val[k] <= vah2[k]) & (ctx.pv_vah[k] >= val2[k]), "yes", "no")
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "A001 r12: event - shifted reference"),
                     hurdle_norm=ev["atr"].to_numpy(), conditions=("open_in_value", "va_overlap", "touch_number", "vol_tercile"))


def _acceptance_events(ctx: StudyContext, spec: dict, mode: str) -> pd.DataFrame:
    """HA02 (``accept``) and HA03 (``reenter``) on 30-minute bars anchored 09:30."""
    p = spec["params"]
    last_close = hhmm(p["last_pair_close"])
    bb = b30(ctx)
    rows = []
    for k in ctx.ok_days():
        val, vah, o = ctx.pv_val[k], ctx.pv_vah[k], ctx.rth_open[k]
        if not (np.isfinite(val) and np.isfinite(vah) and np.isfinite(o)):
            continue
        d30 = bb[bb["day"] == k]
        cl = d30["close"].to_numpy(float)
        cm = d30["close_min"].to_numpy()
        if mode == "accept":
            sides = [(+1, o <= vah, lambda x: x > vah), (-1, o >= val, lambda x: x < val)]
        else:
            inside = lambda x: (x >= val) & (x <= vah)  # noqa: E731
            sides = [(-1, o > vah, inside), (+1, o < val, inside)]
        for d, pre, cond in sides:
            if not pre:
                continue
            ok = cond(cl)
            for j in range(1, len(cl)):
                if cm[j] > last_close:
                    break
                if ok[j - 1] and ok[j]:
                    pos = pos_closing_at(ctx, int(k), int(cm[j]))
                    if pos >= 0:
                        rows.append({"pos": pos, "direction": d, "day": int(k)})
                    break
    return concat([pd.DataFrame(rows)]) if rows else concat([])


def study_A002(ctx: StudyContext, spec: dict) -> StudyData:
    """HA02: RTH open <= VAH, first two consecutive 30m closes > VAH (second closes by 14:30);
    long (mirror short below VAL); vs momentum twin (sign of the prior 60-minute return)."""
    ev = _acceptance_events(ctx, spec, "accept")
    ct = momentum_twin(ctx, ev, lookback=spec["params"]["momentum_lookback_bars"], n=spec["control_draws"], seed=seed_of(ctx, "A002", 2))
    ev, ct = finish(ctx, ev, ct)
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "A002 r12: event - momentum twin"), hurdle_norm=ev["atr"].to_numpy(),
                     conditions=("trend_tercile",))


def study_A003(ctx: StudyContext, spec: dict) -> StudyData:
    """HA03 80% rule: open outside value, two consecutive 30m closes inside value (second by
    14:30); target = opposite edge, stop = entry-side edge +/- 0.25 ATR_d, by 15:55.
    Primary: P(target first) event - geometry twin (time-matched, same distances in ATR_d)."""
    p = spec["params"]
    ev = _acceptance_events(ctx, spec, "reenter")
    ev = add_outcomes(ctx, ev)
    if ev.empty:
        return StudyData(ev, None, None, status="NO_EVENTS")
    k = ev["day"].to_numpy()
    pos = ev["pos"].to_numpy()
    d = ev["direction"].to_numpy()
    ad = ctx.atr_d[k]
    entry = ctx.o[np.clip(pos + 1, 0, ctx.n5 - 1)]
    target = np.where(d < 0, ctx.pv_val[k], ctx.pv_vah[k])
    stop = np.where(d < 0, ctx.pv_vah[k] + p["stop_atr_d"] * ad, ctx.pv_val[k] - p["stop_atr_d"] * ad)
    T = d * (target - entry) / ad
    S = d * (entry - stop) / ad
    good = (T > 0) & (S > 0)
    ev = ev[good].reset_index(drop=True)
    T, S, entry, target, stop = T[good], S[good], entry[good], target[good], stop[good]
    fp = first_passage(ctx.b5, ev["pos"].to_numpy(), ev["direction"].to_numpy(), target, stop, 100, ctx.sid5_to1555)
    ev["hit"] = (fp["outcome"].to_numpy() == 1).astype(float)
    ev["T"], ev["S"] = T, S
    ev["R"] = _r_multiple(ctx, ev, fp, T, S)
    ct = tm_controls(ctx, "A003", ev, spec["control_draws"])
    if len(ct):
        ct = add_outcomes(ctx, ct)
        cpos, cd = ct["pos"].to_numpy(), ct["direction"].to_numpy()
        cad = ctx.atr_d[ct["day"].to_numpy()]
        cT, cS = T[ct["event_idx"].to_numpy()], S[ct["event_idx"].to_numpy()]
        centry = ctx.o[np.clip(cpos + 1, 0, ctx.n5 - 1)]
        cfp = first_passage(ctx.b5, cpos, cd, centry + cd * cT * cad, centry - cd * cS * cad, 100, ctx.sid5_to1555)
        ct["hit"] = (cfp["outcome"].to_numpy() == 1).astype(float)
        ct["R"] = _r_multiple(ctx, ct, cfp, cT, cS)
    ev, ct = add_day_conditions(ctx, ev), add_day_conditions(ctx, ct)
    gap = np.abs(ctx.rth_open - ctx.prev(ctx.rth_close)) / ctx.atr_d
    for df in (ev, ct):
        if len(df):
            df["gap_tercile"] = tercile_labels(gap[df["day"].to_numpy()], gap[ev["day"].to_numpy()])
    stop_pts = S * ctx.atr_d[ev["day"].to_numpy()]
    return StudyData(ev, ct, diff_test(ev, ct, "hit", "A003 P(target first): event - geometry twin"),
                     econ=diff_test(ev, ct, "R", "A003 R: event - geometry twin"), hurdle_norm=stop_pts,
                     conditions=("gap_tercile", "vol_tercile"))


def _r_multiple(ctx: StudyContext, df: pd.DataFrame, fp: pd.DataFrame, T: np.ndarray, S: np.ndarray) -> np.ndarray:
    """+T/S on target, -1 on stop, else marked to the 15:55 close, in units of the stop distance."""
    out = np.where(fp["outcome"].to_numpy() == 1, T / S, np.where(fp["outcome"].to_numpy() == -1, -1.0, np.nan))
    open_ = fp["outcome"].to_numpy() == 0
    if open_.any():
        out[open_] = df["reod"].to_numpy()[open_] / S[open_]
    return out


def _open_class(ctx: StudyContext) -> np.ndarray:
    """HA04 per day: 1 inside prior value, 2 outside value inside prior range, 3 outside range."""
    o = ctx.rth_open
    val, vah = ctx.pv_val, ctx.pv_vah
    pdh, pdl = ctx.prev(ctx.rth_high), ctx.prev(ctx.rth_low)
    cls = np.where((o >= val) & (o <= vah), 1, np.where((o > pdh) | (o < pdl), 3, 2)).astype(float)
    cls[~(np.isfinite(o) & np.isfinite(val) & np.isfinite(vah) & np.isfinite(pdh) & np.isfinite(pdl))] = np.nan
    return cls


def study_A004(ctx: StudyContext, spec: dict) -> StudyData:
    """HA04 magnitude: RTH range / ATR_d of class-3 days minus class-1 days (one row per day)."""
    k = ctx.ok_days()
    cls = _open_class(ctx)[k]
    rng = (ctx.rth_high[k] - ctx.rth_low[k]) / ctx.atr_d[k]
    gap = (ctx.rth_open[k] - ctx.prev(ctx.rth_close)[k]) / ctx.atr_d[k]
    days = pd.DataFrame({"day": k, "date": ctx.days.to_numpy()[k], "open_class": cls, "range_atr_d": rng, "gap_atr_d": gap})
    days = days[np.isfinite(days["open_class"])].reset_index(drop=True)
    pdc = ctx.prev(ctx.rth_close)
    filled = np.where(days["gap_atr_d"] > 0, ctx.rth_low[days["day"]] <= pdc[days["day"]], ctx.rth_high[days["day"]] >= pdc[days["day"]])
    days["gap_filled"] = filled.astype(float)
    a, b = days[days["open_class"] == 3], days[days["open_class"] == 1]
    ti = diff_test(a, b, "range_atr_d", "A004 RTH range/ATR_d: class 3 - class 1", groups=("class3", "class1"))
    table = days.groupby("open_class").agg(n=("day", "size"), range_mean=("range_atr_d", "mean"), gap_fill_rate=("gap_filled", "mean"))
    return StudyData(days.assign(pos=-1, direction=0), None, ti, tables={"by_class": table.reset_index()})


def study_A005(ctx: StudyContext, spec: dict) -> StudyData:
    """HA04 tradable variant: class-3 open, decision 09:35, direction = gap sign, outcome to
    15:55 in ATR_d vs time-matched controls."""
    cls = _open_class(ctx)
    p0 = ctx.slot_pos(0)
    rows = []
    for k in ctx.ok_days():
        if cls[k] == 3 and p0[k] >= 0:
            g = np.sign(ctx.rth_open[k] - ctx.prev(ctx.rth_close)[k])
            if g != 0:
                rows.append({"pos": int(p0[k]), "direction": int(g), "day": int(k)})
    ev = concat([pd.DataFrame(rows)]) if rows else concat([])
    ct = tm_controls(ctx, "A005", ev, spec["control_draws"])
    ev, ct = finish(ctx, ev, ct)
    return StudyData(ev, ct, diff_test(ev, ct, "reod", "A005 open->15:55 ATR_d: class-3 continuation - time-matched"),
                     hurdle_norm=ev["atr_d"].to_numpy(), conditions=("vol_tercile",))


def _ib_levels(ctx: StudyContext) -> dict[str, np.ndarray]:
    avail = ctx.rth_start_min + int(ctx.template.initial_balance_minutes)
    return {"ib_high": ctx.per_bar(ctx.ib["high"].to_numpy(float), avail), "ib_low": ctx.per_bar(ctx.ib["low"].to_numpy(float), avail)}


def _ib_width_tercile(ctx: StudyContext, spec: dict) -> np.ndarray:
    w = (ctx.ib["high"].to_numpy(float) - ctx.ib["low"].to_numpy(float)) / ctx.atr_d
    p = spec["params"]
    return tercile(pd.Series(w), int(p["ib_tercile_window"]), int(p["ib_tercile_min"])).to_numpy()


def study_A006(ctx: StudyContext, spec: dict) -> StudyData:
    """HA05: after 10:30, high > IB high + 0.10 ATR then a close < IB high within 3 bars
    (short; mirror at IB low); vs shifted-reference IB (offset from the 10:30 price kept)."""
    p = spec["params"]
    w = win(p)
    scan = hhmm(p["scan_from"])

    def detect(lv: dict) -> pd.DataFrame:
        kw = dict(mode="pen", pen_atr=p["pen_atr"], n_bars=p["reclaim_bars"], window=w, scan_from_min=scan, near_side_open=False)
        return concat([sweep_reclaim(ctx, lv["ib_high"], +1, **kw), sweep_reclaim(ctx, lv["ib_low"], -1, **kw)])

    lv = _ib_levels(ctx)
    ev = detect(lv)
    p1030 = ctx.slot_pos(ctx.minute_slot(p["scan_from"]))
    anchor = np.where(p1030 >= 0, ctx.o[np.clip(p1030, 0, None)], np.nan)
    ct = shifted_controls(ctx, "A006", lv, detect, anchor, spec["control_draws"])
    ev, ct = finish(ctx, ev, ct)
    terc = _ib_width_tercile(ctx, spec)
    cls = _open_class(ctx)
    for df in (ev, ct):
        if len(df):
            k = df["day"].to_numpy()
            df["ib_tercile"] = terc[k]
            df["open_in_value"] = np.where(cls[k] == 1, "yes", "no")
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "A006 r12: event - shifted reference"), hurdle_norm=ev["atr"].to_numpy(),
                     conditions=("ib_tercile", "open_in_value", "bucket"))


def study_A007(ctx: StudyContext, spec: dict) -> StudyData:
    """HA06: narrow IB (bottom tercile of IB/ATR_d over the trailing 252 sessions, min 60);
    first 5m close beyond the IB after 10:30 (decisions to 15:00), with the close; vs
    time-matched. G6 twin: the same first extension on wide-IB (top tercile) days."""
    p = spec["params"]
    w = win(p)
    lv = _ib_levels(ctx)
    terc = _ib_width_tercile(ctx, spec)
    allev = first_close_beyond(ctx, lv["ib_high"], lv["ib_low"], window=w, scan_from_min=hhmm(p["scan_from"]))
    allev = add_outcomes(ctx, allev)
    allev["ib_tercile"] = terc[allev["day"].to_numpy()] if len(allev) else []
    ev = allev[allev["ib_tercile"] == 0].reset_index(drop=True)
    wide = allev[allev["ib_tercile"] == 2].reset_index(drop=True)
    ct = tm_controls(ctx, "A007", ev[["pos", "direction"]], spec["control_draws"])
    ct = add_outcomes(ctx, ct)
    ev, ct = add_day_conditions(ctx, ev), add_day_conditions(ctx, ct)
    k = ctx.ok_days()
    ext = []
    for t in (0, 1, 2):
        dk = k[terc[k] == t]
        n_ext = int(allev["day"].isin(dk).sum()) if len(allev) else 0
        ext.append({"ib_tercile": t, "days": len(dk), "days_with_extension": n_ext, "p_extension": n_ext / len(dk) if len(dk) else np.nan})
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "A007 r12: narrow-IB extension - time-matched"),
                     twin=diff_test(ev, wide, "r12", "A007 G6: narrow-IB - wide-IB extension", groups=("narrow", "wide")),
                     hurdle_norm=ev["atr"].to_numpy(), tables={"extension_by_tercile": pd.DataFrame(ext)}, conditions=("vol_tercile",))


def _first30(ctx: StudyContext) -> pd.DataFrame:
    """Per day from 1m bars: open 09:30, close 09:59, 09:30-09:59 high/low, 09:35-09:59 high/low."""
    lab = ctx.lab1
    mod = lab["minute_of_day"].to_numpy()
    s = ctx.rth_start_min
    m30 = lab["is_rth"].to_numpy() & (mod < s + 30)
    b = ctx.bars1[m30]
    td = pd.DatetimeIndex(lab["trading_date"].to_numpy()[m30])
    g = b.groupby(td)
    out = pd.DataFrame({"open": g["open"].first(), "close": g["close"].last(), "high": g["high"].max(), "low": g["low"].min(),
                        "n": g.size()})
    late = mod[m30] >= s + 5
    g2 = b[late].groupby(td[late])
    out["high_late"] = g2["high"].max()
    out["low_late"] = g2["low"].min()
    out["first_ok"] = pd.Series(mod[m30], index=td).groupby(level=0).min() == s
    return out.reindex(ctx.days)


def study_A008(ctx: StudyContext, spec: dict) -> StudyData:
    """HA07 open drive (decision 10:00, outcome to 15:55 in ATR_d) vs momentum twin: days with
    |first-30-min return| >= 0.30 ATR_d that are not open drives, direction = return sign."""
    p = spec["params"]
    f = _first30(ctx)
    k = ctx.ok_days()
    ad = ctx.atr_d[k]
    o, c, hi, lo = (f[x].to_numpy(float)[k] for x in ("open", "close", "high", "low"))
    hl, ll = f["high_late"].to_numpy(float)[k], f["low_late"].to_numpy(float)[k]
    ret = c - o
    rng = hi - lo
    clv = np.where(rng > 0, (c - lo) / np.where(rng > 0, rng, 1), 0.5)
    thr = p["drive_atr_d"] * ad
    up = (ret >= thr) & (ll > o) & (clv >= p["clv"])
    dn = (ret <= -thr) & (hl < o) & (clv <= 1 - p["clv"])
    drive = up | dn
    big = np.abs(ret) >= thr
    first_ok = f["first_ok"].to_numpy()[k].astype(bool) & np.isfinite(ret)
    pos = ctx.slot_pos(ctx.minute_slot(p["decision"]) - 1)[k]
    sel = first_ok & (pos >= 0)
    ev = pd.DataFrame({"pos": pos[sel & drive], "direction": np.where(up, 1, -1)[sel & drive]})
    tw = pd.DataFrame({"pos": pos[sel & big & ~drive], "direction": np.sign(ret[sel & big & ~drive]).astype(int)})
    ev, tw = finish(ctx, ev, tw)
    tm = tm_controls(ctx, "A008", ev, spec["control_draws"])
    tm = add_outcomes(ctx, tm)
    return StudyData(ev, tw, diff_test(ev, tw, "reod", "A008 10:00->15:55 ATR_d: open drive - momentum twin", groups=("drive", "twin")),
                     hurdle_norm=ev["atr_d"].to_numpy(), tables={"vs_time_matched": _desc_vs(ev, tm, "reod")}, conditions=("vol_tercile",))


def _desc_vs(ev: pd.DataFrame, ct: pd.DataFrame, col: str) -> pd.DataFrame:
    return pd.DataFrame([{"n_ev": len(ev), "n_ct": len(ct), "ev_mean": ev[col].mean() if len(ev) else np.nan,
                          "ct_mean": ct[col].mean() if len(ct) else np.nan}])


def study_A009(ctx: StudyContext, spec: dict) -> StudyData:
    """HA08/HB14 open-rejection-reverse: before 10:30 a first excursion >= 0.20 ATR_d from the
    RTH open (1m order), then a 5m close beyond the open on the other side by >= 0.05 ATR_d
    (close by 10:30); direction = the new side; 24-bar outcome vs time-matched. G6 twin: the
    first 5m close crossing the open by >= 0.05 ATR_d without a prior 0.20 ATR_d excursion."""
    p = spec["params"]
    end = hhmm(p["end"])
    s1, e1 = ctx.rth_bounds1
    b1h, b1l = ctx.bars1["high"].to_numpy(float), ctx.bars1["low"].to_numpy(float)
    mod1 = ctx.lab1["minute_of_day"].to_numpy()
    s5, e5 = ctx.rth_bounds5
    ev_rows, tw_rows = [], []
    for k in ctx.ok_days():
        O, A = ctx.rth_open[k], ctx.atr_d[k]
        if s1[k] < 0:
            continue
        idx = np.arange(s1[k], e1[k])
        idx = idx[mod1[idx] < end]
        up = np.flatnonzero(b1h[idx] - O >= p["excursion_atr_d"] * A)
        dn = np.flatnonzero(O - b1l[idx] >= p["excursion_atr_d"] * A)
        iu = up[0] if up.size else 10**9
        idn = dn[0] if dn.size else 10**9
        bars5 = np.arange(s5[k], e5[k])
        bars5 = bars5[ctx.close_min5[bars5] <= end]
        if iu != idn:
            first_side = 1 if iu < idn else -1
            exc_min = int(mod1[idx[min(iu, idn)]])
            for j in bars5:
                if ctx.close_min5[j] <= exc_min:
                    continue
                if -first_side * (ctx.c[j] - O) >= p["cross_atr_d"] * A:
                    ev_rows.append({"pos": int(j), "direction": -first_side, "day": int(k)})
                    break
        prev_c = O
        for j in bars5:
            side = int(np.sign(ctx.c[j] - O))
            crossed = side != 0 and side * (ctx.c[j] - O) >= p["cross_atr_d"] * A and side * (prev_c - O) < 0
            if crossed:
                seg = np.arange(s5[k], j)
                opp = (O - ctx.l[seg]).max() if side > 0 else (ctx.h[seg] - O).max()
                if opp < p["excursion_atr_d"] * A:
                    tw_rows.append({"pos": int(j), "direction": side, "day": int(k)})
                break
            prev_c = ctx.c[j]
    ev = concat([pd.DataFrame(ev_rows)]) if ev_rows else concat([])
    tw = concat([pd.DataFrame(tw_rows)]) if tw_rows else concat([])
    w = win(p)
    ev = ev[[in_window(ctx, int(i), w) for i in ev["pos"]]] if len(ev) else ev
    tw = tw[[in_window(ctx, int(i), w) for i in tw["pos"]]] if len(tw) else tw
    ct = tm_controls(ctx, "A009", ev, spec["control_draws"])
    ev, ct = finish(ctx, ev, ct)
    tw = add_outcomes(ctx, tw)
    return StudyData(ev, ct, diff_test(ev, ct, "r24", "A009 r24: ORR - time-matched"),
                     twin=diff_test(ev, tw, "r24", "A009 G6: ORR - open-crossing twin", groups=("orr", "cross")),
                     hurdle_norm=ev["atr"].to_numpy(), conditions=("vol_tercile",))


def study_A010(ctx: StudyContext, spec: dict) -> StudyData:
    """HA09: open in the top 15% of the overnight range and ON return >= +0.30 ATR_d -> short
    at 09:35 (mirror long); 6-bar outcome vs time-matched."""
    p = spec["params"]
    p0 = ctx.slot_pos(0)
    rows = []
    for k in ctx.ok_days():
        hi, lo, O, A = ctx.on_high[k], ctx.on_low[k], ctx.rth_open[k], ctx.atr_d[k]
        if not (np.isfinite(hi) and np.isfinite(lo) and hi > lo) or p0[k] < 0:
            continue
        loc = (O - lo) / (hi - lo)
        r = (O - ctx.prev(ctx.rth_close)[k]) / A
        if loc >= p["top_frac"] and r >= p["on_ret_atr_d"]:
            rows.append({"pos": int(p0[k]), "direction": -1, "day": int(k)})
        elif loc <= 1 - p["top_frac"] and r <= -p["on_ret_atr_d"]:
            rows.append({"pos": int(p0[k]), "direction": 1, "day": int(k)})
    ev = concat([pd.DataFrame(rows)]) if rows else concat([])
    ct = tm_controls(ctx, "A010", ev, spec["control_draws"])
    ev, ct = finish(ctx, ev, ct)
    return StudyData(ev, ct, diff_test(ev, ct, "r6", "A010 r6: ON-inventory fade - time-matched"), hurdle_norm=ev["atr"].to_numpy(),
                     conditions=("vol_tercile",))


def _vwap_and_twin(ctx: StudyContext, spec: dict, which: int) -> StudyData:
    p = spec["params"]
    w = win(p)
    vw = ctx.vwap5
    fa, rc = band_fades(ctx, vw["vwap"].to_numpy(), vw["vwap_up_2"].to_numpy(), vw["vwap_dn_2"].to_numpy(), window=w)
    et = ctx.ema_twin
    tfa, trc = band_fades(ctx, et["ema"].to_numpy(), et["ema_up_2"].to_numpy(), et["ema_dn_2"].to_numpy(), window=w)
    ev, ct = (fa, tfa) if which == 0 else (rc, trc)
    ev, ct = finish(ctx, ev, ct)
    sid = "A011" if which == 0 else "A012"
    name = "VWAP 2SD fade" if which == 0 else "VWAP reclaim"
    return StudyData(ev, ct, diff_test(ev, ct, "r12", f"{sid} r12: {name} - EMA twin"), hurdle_norm=ev["atr"].to_numpy(),
                     conditions=("trend_tercile",))


def study_A011(ctx: StudyContext, spec: dict) -> StudyData:
    """HA10a: first 5m close beyond RTH VWAP +/- 2 SD per side (decisions 10:00-15:00), toward
    VWAP; vs the same detector on EMA(20) +/- 2 rolling SD(20) of the residual."""
    return _vwap_and_twin(ctx, spec, 0)


def study_A012(ctx: StudyContext, spec: dict) -> StudyData:
    """HA10b: after the first -2 SD (+2 SD) deviation, the first later close back across VWAP;
    with the reclaim; vs the EMA twin."""
    return _vwap_and_twin(ctx, spec, 1)


def study_A013(ctx: StudyContext, spec: dict) -> StudyData:
    """HA11: poor (top price >= 2 TPO letters) vs excess (>= 2 single-print ticks) extremes of
    day d; outcome = d+1 RTH trades through the extreme. Only extremes the d+1 open sits
    inside of (distance > 0). Primary: P(revisit) poor - excess, stratified by distance
    tercile (CMH weights), day-cluster bootstrap."""
    lab = ctx.lab1
    mod = lab["minute_of_day"].to_numpy()
    s1, e1 = ctx.rth_bounds1
    tick = ctx.instrument.tick_size
    rows = []
    for k in range(ctx.n_days - 1):
        k1 = k + 1
        if not ctx.day_ok[k1] or s1[k] < 0:
            continue
        sl = slice(s1[k], e1[k])
        tp = tpo_profile(ctx.bars1.iloc[sl], mod[sl], tick_size=tick, start_minute=ctx.rth_start_min, period_minutes=30)
        if not len(tp.get("tpo", [])):
            continue
        A, O = ctx.atr_d[k1], ctx.rth_open[k1]
        for side, ext, poor, excess, nxt in (
            ("high", ctx.rth_high[k], tp["poor_high"], tp["excess_high"] >= 2, ctx.rth_high[k1] > ctx.rth_high[k]),
            ("low", ctx.rth_low[k], tp["poor_low"], tp["excess_low"] >= 2, ctx.rth_low[k1] < ctx.rth_low[k]),
        ):
            dist = ((ext - O) if side == "high" else (O - ext)) / A
            cls = "poor" if poor else ("excess" if excess else "")
            if cls and np.isfinite(dist) and dist > 0:
                rows.append({"day": k1, "date": ctx.days[k1], "side": side, "cls": cls, "dist_atr_d": dist, "revisit": float(nxt)})
    df = pd.DataFrame(rows, columns=["day", "date", "side", "cls", "dist_atr_d", "revisit"])
    if df.empty:
        return StudyData(df.assign(pos=-1, direction=0), None, None, status="NO_EVENTS")
    df["dist_tercile"] = tercile_labels(df["dist_atr_d"].to_numpy())
    ti = TestInput("strat_diff", df["date"].to_numpy(), df["revisit"].to_numpy(), df["cls"].to_numpy(), ("poor", "excess"),
                   stratum=df["dist_tercile"].to_numpy(), label="A013 P(revisit): poor - excess | distance tercile")
    table = df.groupby(["dist_tercile", "cls"]).agg(n=("revisit", "size"), p_revisit=("revisit", "mean")).reset_index()
    return StudyData(df.assign(pos=-1, direction=0), None, ti, tables={"revisit_by_tercile": table})


def study_A014(ctx: StudyContext, spec: dict) -> StudyData:
    """HA12: next-day open->15:55 return in ATR_d on POC-up (POC_d > POC_d-1) vs POC-down
    days, controlling for the sign of day d's close-to-close return (OLS coefficient)."""
    poc = ctx.profiles["poc"].to_numpy(float)
    rows = []
    for k1 in ctx.ok_days():
        k, k0 = k1 - 1, k1 - 2
        if k0 < 0 or ctx.eod_pos[k1] < 0:
            continue
        dp = poc[k] - poc[k0]
        dc = ctx.rth_close[k] - ctx.rth_close[k0]
        if not (np.isfinite(dp) and np.isfinite(dc)) or dp == 0 or dc == 0:
            continue
        y = (ctx.c[ctx.eod_pos[k1]] - ctx.rth_open[k1]) / ctx.atr_d[k1]
        rows.append({"day": int(k1), "date": ctx.days[k1], "poc_up": float(dp > 0), "ret_sign": float(np.sign(dc)), "y": y,
                     "shape_prev": ctx.profiles["shape"].iloc[k] if "shape" in ctx.profiles else ""})
    df = pd.DataFrame(rows, columns=["day", "date", "poc_up", "ret_sign", "y", "shape_prev"])
    if df.empty:
        return StudyData(df.assign(pos=-1, direction=0), None, None, status="NO_EVENTS")
    ti = TestInput("ols", df["date"].to_numpy(), df["y"].to_numpy(), np.full(len(df), "all"), ("all",),
                   x=df["poc_up"].to_numpy(), x2=df["ret_sign"].to_numpy(), label="A014 coef(POC up) on d+1 return | sign(d return)")
    shape = df.groupby("shape_prev").agg(n=("y", "size"), next_ret_mean=("y", "mean")).reset_index()
    return StudyData(df.assign(pos=-1, direction=0), None, ti, econ_scale=0.5, hurdle_norm=ctx.atr_d[df["day"].to_numpy()],
                     tables={"by_prior_shape": shape},
                     notes=["G3 uses half the coefficient: a long-after-up / short-after-down rule earns about b/2 per trade beyond drift."])


def study_A015(ctx: StudyContext, spec: dict) -> StudyData:
    """HA13: node stability gate first; only if >= 50% of reference nodes are stable, LVN
    traversal speed (median 5m bars to exit the far side of a 0.10 ATR_d zone) vs random
    zones of equal width inside the composite range. Positive effect = LVNs are faster."""
    p = spec["params"]
    tick = ctx.instrument.tick_size
    s1, e1 = ctx.rth_bounds1
    lo, hi, cl, vo = (ctx.bars1[x].to_numpy(float) for x in ("low", "high", "close", "volume"))
    settings = [(b, s) for b in p["bin_ticks"] for s in p["sigmas"]]
    ref = (p["ref_bin_ticks"], p["ref_sigma"])
    n_comp = int(p["composite_sessions"])
    nodes_by_day: dict[int, dict] = {}
    total = stable = 0
    for k in ctx.ok_days():
        if k < n_comp or s1[k - n_comp] < 0:
            continue
        idx = np.concatenate([np.arange(s1[j], e1[j]) for j in range(k - n_comp, k) if s1[j] >= 0])
        found = {}
        for b, sg in settings:
            prof = build_profile(lo[idx], hi[idx], cl[idx], vo[idx], tick * b, "uniform")
            found[(b, sg)] = (volume_nodes(prof, sg, p["prominence"]), tick * b)
            if (b, sg) == ref:
                ref_prof = prof
        ref_nodes, _ = found[ref]
        st = {}
        for kind in ("lvn", "hvn"):
            for x in ref_nodes[kind]:
                hits = sum(1 for (nd, bs) in found.values() if len(nd[kind]) and np.min(np.abs(nd[kind] - x)) <= p["match_bins"] * bs)
                ok = hits >= p["min_settings"]
                total += 1
                stable += int(ok)
                if ok and kind == "lvn":
                    st.setdefault("lvn", []).append(float(x))
        nz = np.flatnonzero(ref_prof.volume > 0)
        if nz.size:
            st["range"] = (float(ref_prof.levels[nz[0]]), float(ref_prof.levels[nz[-1]]))
        nodes_by_day[int(k)] = st
    frac = stable / total if total else np.nan
    gate = pd.DataFrame([{"nodes": total, "stable": stable, "stable_frac": frac, "threshold": p["gate_frac"]}])
    if not (np.isfinite(frac) and frac >= p["gate_frac"]):
        return StudyData(concat([]), None, None, status="REJECTED_AT_GATE", tables={"stability_gate": gate},
                         notes=[f"stability gate failed: {stable}/{total} stable nodes ({frac:.2f}) < {p['gate_frac']}; direction not tested"])
    rng = np.random.default_rng(seed_of(ctx, "A015", 3))
    rows = []
    s5, e5 = ctx.rth_bounds5
    for k, st in nodes_by_day.items():
        if "lvn" not in st or "range" not in st:
            continue
        wdt = p["zone_atr_d"] * ctx.atr_d[k]
        lo_r, hi_r = st["range"]
        if hi_r - lo_r <= wdt:
            continue
        for x in st["lvn"]:
            for grp, centers in (("lvn", [x]), ("rand", rng.uniform(lo_r + wdt / 2, hi_r - wdt / 2, size=p["random_per_node"]))):
                for cen in centers:
                    t = _traverse_bars(ctx, int(s5[k]), int(e5[k]), cen - wdt / 2, cen + wdt / 2)
                    if t is not None:
                        rows.append({"day": int(k), "date": ctx.days[k], "group": grp, "bars": t})
    df = pd.DataFrame(rows, columns=["day", "date", "group", "bars"])
    ti = TestInput("median_diff", df["date"].to_numpy(), df["bars"].to_numpy(float), df["group"].to_numpy(), ("rand", "lvn"),
                   label="A015 median traversal bars: random zones - LVN") if len(df) else None
    return StudyData(df.assign(pos=-1, direction=0), None, ti, tables={"stability_gate": gate})


def _traverse_bars(ctx: StudyContext, s: int, e: int, zlo: float, zhi: float) -> float | None:
    """Bars from the first entry into [zlo, zhi] to the first trade beyond the far side; a close
    back out on the entry side or the session end counts as no traversal (1e6)."""
    for i in range(s + 1, e):
        pc = ctx.c[i - 1]
        if pc > zhi and ctx.l[i] <= zhi:
            frm = 1
        elif pc < zlo and ctx.h[i] >= zlo:
            frm = -1
        else:
            continue
        for j in range(i, e):
            if (frm > 0 and ctx.l[j] < zlo) or (frm < 0 and ctx.h[j] > zhi):
                return float(j - i + 1)
            if j > i and ((frm > 0 and ctx.c[j] > zhi) or (frm < 0 and ctx.c[j] < zlo)):
                return 1e6
        return 1e6
    return None


# =======================================================================================
# Family B - ICT / smart money concepts
# =======================================================================================
def cached(ctx: StudyContext, key: str, fn: Callable[[], object]):
    k = f"_study_cache_{key}"
    if k not in ctx.__dict__:
        ctx.__dict__[k] = fn()
    return ctx.__dict__[k]


def _pd_levels(ctx: StudyContext) -> dict[str, np.ndarray]:
    return {"hi": ctx.per_bar(ctx.prev(ctx.rth_high)), "lo": ctx.per_bar(ctx.prev(ctx.rth_low))}


def _on_levels(ctx: StudyContext) -> dict[str, np.ndarray]:
    return {"hi": ctx.per_bar(ctx.on_high, ctx.rth_start_min), "lo": ctx.per_bar(ctx.on_low, ctx.rth_start_min)}


def _pz_levels(ctx: StudyContext) -> dict[str, np.ndarray]:
    return {"hi": ctx.pz_levels["res"], "lo": ctx.pz_levels["sup"]}


def _sweep_kw(p: dict) -> dict:
    return dict(mode=p.get("mode", "pen"), pen_atr=p.get("pen_atr", 0.10), n_bars=p.get("reclaim_bars", 3),
                close_thr_atr=p.get("close_thr_atr", 0.0), min_closes=p.get("min_closes", 2),
                max_bars_multi=p.get("max_bars_multi", 6), window=win(p))


def _sweeps(ctx: StudyContext, lv: dict, kw: dict, near_side_open: bool) -> pd.DataFrame:
    return concat([sweep_reclaim(ctx, lv["hi"], +1, near_side_open=near_side_open, **kw),
                   sweep_reclaim(ctx, lv["lo"], -1, near_side_open=near_side_open, **kw)])


def _sweep_events(ctx: StudyContext, spec: dict, which: str) -> pd.DataFrame:
    """Cached real-level sweep events (with outcomes) of a sweep study, for child studies."""
    lv = _pd_levels(ctx) if which == "pd" else _on_levels(ctx)
    return cached(ctx, f"sweeps_{spec['id']}", lambda: add_day_conditions(ctx, add_outcomes(ctx, _sweeps(ctx, lv, _sweep_kw(spec["params"]), True))))


def _sweep_study(ctx: StudyContext, spec: dict, which: str) -> StudyData:
    """HB01 / HB02 family: sweep-reclaim at PDH/PDL or ONH/ONL vs shifted reference; G6 =
    the identical detector on the nearest pivot zones (PA twin)."""
    sid = spec["id"]
    kw = _sweep_kw(spec["params"])
    lv = _pd_levels(ctx) if which == "pd" else _on_levels(ctx)
    ev = _sweep_events(ctx, spec, which).copy()
    ct = shifted_controls(ctx, sid, lv, lambda x: _sweeps(ctx, x, kw, True), ctx.rth_open, spec["control_draws"])
    ct = add_day_conditions(ctx, add_outcomes(ctx, ct))
    pa = add_outcomes(ctx, _sweeps(ctx, _pz_levels(ctx), kw, False))
    for df in (ev, ct):
        if len(df):
            df["depth_tercile"] = tercile_labels(df["depth_atr"].to_numpy(), ev["depth_atr"].to_numpy())
            df["bars_beyond_cls"] = np.where(df["bars_beyond"].to_numpy() == 0, "1", "2+")
    return StudyData(ev, ct, diff_test(ev, ct, "r12", f"{sid} r12: sweep - shifted reference"),
                     twin=diff_test(ev, pa, "r12", f"{sid} G6: sweep - PA twin (pivot zones)", groups=("ict", "pa")),
                     hurdle_norm=ev["atr"].to_numpy(), conditions=("depth_tercile", "bars_beyond_cls", "vol_tercile", "bucket"))


def study_B002(ctx: StudyContext, spec: dict) -> StudyData:
    """HB01 reclaim sweep: high > PDH + 0.10 ATR, close < PDH within 3 bars (mirror PDL)."""
    return _sweep_study(ctx, spec, "pd")


def study_B003(ctx: StudyContext, spec: dict) -> StudyData:
    """HB01 wick variant: the penetrating bar itself closes back inside."""
    return _sweep_study(ctx, spec, "pd")


def study_B004(ctx: StudyContext, spec: dict) -> StudyData:
    """HB01 close variant: a close beyond PDH, then a close back below within 3 bars."""
    return _sweep_study(ctx, spec, "pd")


def study_B005(ctx: StudyContext, spec: dict) -> StudyData:
    """HB01 multi-bar variant: >= 2 closes beyond, back inside within 6 bars."""
    return _sweep_study(ctx, spec, "pd")


def study_B006(ctx: StudyContext, spec: dict) -> StudyData:
    """HB02: sweep-reclaim of the overnight high/low, decisions 09:35-11:00."""
    return _sweep_study(ctx, spec, "on")


def _equal_level_table(ctx: StudyContext, max_age: int) -> pd.DataFrame:
    """Equal highs/lows and single pivots as level lists: level, side, start (first usable bar),
    expire, ref_pos (bar of the most recent pivot), kind ('equal' / 'single')."""
    rows = []
    used = {"H": set(), "L": set()}
    for kind, eq in (("H", ctx.equal_highs), ("L", ctx.equal_lows)):
        for r in eq.itertuples(index=False):
            used[kind].update((int(r.p1_pos), int(r.p2_pos)))
            rows.append({"level": float(r.level), "side": 1 if kind == "H" else -1, "start": int(r.confirm_pos) + 1,
                         "expire": int(r.p2_pos) + max_age, "ref_pos": int(r.p2_pos), "kind": "equal"})
    for r in ctx.pivots_ict.itertuples(index=False):
        if int(r.pivot_pos) in used[r.kind]:
            continue
        rows.append({"level": float(r.price), "side": 1 if r.kind == "H" else -1, "start": int(r.confirm_pos) + 1,
                     "expire": int(r.pivot_pos) + max_age, "ref_pos": int(r.pivot_pos), "kind": "single"})
    return pd.DataFrame(rows, columns=["level", "side", "start", "expire", "ref_pos", "kind"])


def _list_sweeps(ctx: StudyContext, levels: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Sweep-reclaim for explicit level lists: t0 = first bar after ``start`` (before ``expire``)
    beyond the level by ``pen_atr`` ATR; t0 must be an RTH bar of an event day."""
    w = win(p)
    rows = []
    for r in levels.itertuples(index=False):
        s, e = r.start, min(r.expire, ctx.n5)
        if s >= e:
            continue
        ext = ctx.h[s:e] if r.side > 0 else ctx.l[s:e]
        cond = r.side * (ext - r.level) > p["pen_atr"] * ctx.atr5[s:e]
        if not cond.any():
            continue
        t0 = s + int(np.argmax(cond))
        k = ctx.day5[t0]
        if k < 0 or not ctx.day_ok[k]:
            continue
        for t in range(t0, min(t0 + p["reclaim_bars"], ctx.n5 - 1) + 1):
            if ctx.sid5[t] != ctx.sid5[t0]:
                break
            if r.side * (ctx.c[t] - r.level) < 0:
                if in_window(ctx, t, w):
                    rows.append({"pos": t, "direction": -r.side, "day": int(k), "level": r.level, "kind": r.kind,
                                 "age_bars": t0 - r.ref_pos, "dist_atr_d": abs(r.level - ctx.rth_open[k]) / ctx.atr_d[k]})
                break
    return concat([pd.DataFrame(rows)]) if rows else concat([])


def study_B007(ctx: StudyContext, spec: dict) -> StudyData:
    """HB03: sweeps of equal highs/lows vs sweeps of single pivots (3/3 pivots not part of an
    equal pair), stratified by age tercile x distance-from-open tercile (CMH weights)."""
    p = spec["params"]
    ev = _list_sweeps(ctx, _equal_level_table(ctx, int(p["max_age_bars"])), p)
    ev = dedupe(ev)
    ev = add_day_conditions(ctx, add_outcomes(ctx, ev))
    if ev.empty:
        return StudyData(ev, None, None, status="NO_EVENTS")
    ev["stratum"] = [f"{a}|{b}" for a, b in zip(tercile_labels(ev["age_bars"].to_numpy()), tercile_labels(ev["dist_atr_d"].to_numpy()))]
    ti = TestInput("strat_diff", ev["date"].to_numpy(), ev["r12"].to_numpy(), ev["kind"].to_numpy(), ("equal", "single"),
                   stratum=ev["stratum"].to_numpy(), label="B007 r12: equal-level sweeps - single-pivot sweeps | age x distance")
    return StudyData(ev, None, ti, hurdle_norm=ev["atr"].to_numpy())


def study_B001(ctx: StudyContext, spec: dict) -> StudyData:
    """HB00 footprint: first RTH 1m bar trading >= 1 tick through PDH/PDL/ONH/ONL or an equal
    high/low active at the open; log(volume / same-minute median of 20 prior sessions), real
    levels minus shifted-reference levels (each level type on its near side of the open)."""
    p = spec["params"]
    tick = ctx.instrument.tick_size
    sets = _open_level_sets(ctx, int(p["equal_max_age_bars"]))
    s1, e1 = ctx.rth_bounds1
    h1, l1, v1 = (ctx.bars1[x].to_numpy(float) for x in ("high", "low", "volume"))
    med, a1 = ctx.vol1_med20, ctx.atr1
    partners = partner_days(ctx, ctx.rth_open, n_draws=spec["control_draws"], seed=seed_of(ctx, "B001", 1))

    def detect(k: int, levels: list[tuple[str, float, int]], grp: str) -> list[dict]:
        out = []
        if s1[k] < 0:
            return out
        hs, ls = h1[s1[k]: e1[k]], l1[s1[k]: e1[k]]
        for typ, L, side in levels:
            hit = (hs >= L + tick) if side > 0 else (ls <= L - tick)
            if not hit.any():
                continue
            i = s1[k] + int(np.argmax(hit))
            if np.isfinite(med[i]) and med[i] > 0 and v1[i] > 0:
                out.append({"day": k, "date": ctx.days[k], "type": typ, "group": grp,
                            "log_vol_ratio": float(np.log(v1[i] / med[i])), "range_atr": float((h1[i] - l1[i]) / a1[i])})
        return out

    rows = []
    for k in ctx.ok_days():
        rows += detect(int(k), sets.get(int(k), []), "real")
        for r in range(partners.shape[0]):
            kp = int(partners[r, k])
            if kp < 0:
                continue
            o_k, o_p, sc = ctx.rth_open[k], ctx.rth_open[kp], ctx.atr_d[k] / ctx.atr_d[kp]
            rows += detect(int(k), [(t, o_k + (L - o_p) * sc, sd) for t, L, sd in sets.get(kp, [])], "shifted")
    df = pd.DataFrame(rows, columns=["day", "date", "type", "group", "log_vol_ratio", "range_atr"])
    if df.empty:
        return StudyData(df.assign(pos=-1, direction=0), None, None, status="NO_EVENTS")
    ti = TestInput("diff", df["date"].to_numpy(), df["log_vol_ratio"].to_numpy(), df["group"].to_numpy(), ("real", "shifted"),
                   label="B001 log volume ratio at first crossing: real - shifted levels")
    table = df.groupby(["type", "group"]).agg(n=("log_vol_ratio", "size"), log_vol_ratio=("log_vol_ratio", "mean"),
                                              range_atr=("range_atr", "mean")).reset_index()
    return StudyData(df.assign(pos=-1, direction=0), None, ti, tables={"by_level_type": table})


def _open_level_sets(ctx: StudyContext, max_age: int) -> dict[int, list[tuple[str, float, int]]]:
    """Per day: liquidity levels known at the RTH open, each with the side it lies on."""
    p0 = ctx.slot_pos(0)
    out: dict[int, list] = {}
    pdh, pdl = ctx.prev(ctx.rth_high), ctx.prev(ctx.rth_low)
    eqs = []
    for kind, eq in (("eqh", ctx.equal_highs), ("eql", ctx.equal_lows)):
        side = 1 if kind == "eqh" else -1
        for r in eq.itertuples(index=False):
            s, e = int(r.confirm_pos) + 1, min(int(r.p2_pos) + max_age, ctx.n5)
            if s >= e:
                continue
            ext = ctx.h[s:e] if side > 0 else ctx.l[s:e]
            hit = side * (ext - r.level) > 0
            cross = s + int(np.argmax(hit)) if hit.any() else e
            eqs.append((kind, float(r.level), side, s, cross, e))
    for k in range(ctx.n_days):
        O = ctx.rth_open[k]
        if not np.isfinite(O) or p0[k] < 0:
            continue
        lv = []
        for typ, L, side in (("pdh", pdh[k], 1), ("pdl", pdl[k], -1), ("onh", ctx.on_high[k], 1), ("onl", ctx.on_low[k], -1)):
            if np.isfinite(L) and side * (L - O) > 0:
                lv.append((typ, float(L), side))
        for typ, L, side, s, cross, e in eqs:
            if s <= p0[k] <= cross and p0[k] < e and side * (L - O) > 0:
                lv.append((typ, L, side))
        out[k] = lv
    return out


def study_B008(ctx: StudyContext, spec: dict) -> StudyData:
    """HB04 run: first close > PDH + 0.10 ATR, the next 3 closes > PDH; event at the third
    (mirror PDL); vs time-matched. G6: the same detector on shifted-reference levels."""
    p = spec["params"]
    kw = dict(thr_atr=p["thr_atr"], hold_bars=p["hold_bars"], window=win(p))

    def detect(lv: dict) -> pd.DataFrame:
        return concat([level_runs(ctx, lv["hi"], +1, **kw), level_runs(ctx, lv["lo"], -1, **kw)])

    lv = _pd_levels(ctx)
    ev = detect(lv)
    ct = tm_controls(ctx, "B008", ev, spec["control_draws"])
    sh = add_outcomes(ctx, shifted_controls(ctx, "B008", lv, detect, ctx.rth_open, spec["control_draws"]))
    ev, ct = finish(ctx, ev, ct)
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "B008 r12: run - time-matched"),
                     twin=diff_test(ev, sh, "r12", "B008 G6: run - shifted-reference run", groups=("ev", "shifted")),
                     hurdle_norm=ev["atr"].to_numpy(), conditions=("vol_tercile", "trend_tercile"))


def study_B009(ctx: StudyContext, spec: dict) -> StudyData:
    """HB05: B002 events whose reclaim bar is a displacement bar in the reversal direction vs
    B002 events whose reclaim bar is not."""
    ev = _sweep_events(ctx, child(spec, "B002"), "pd").copy()
    if ev.empty:
        return StudyData(ev, None, None, status="NO_EVENTS")
    cf = ctx.candles
    pos = ev["pos"].to_numpy()
    disp = np.where(ev["direction"].to_numpy() > 0, cf["disp_up"].to_numpy(bool)[pos], cf["disp_dn"].to_numpy(bool)[pos])
    ev["grp"] = np.where(disp, "disp", "nodisp")
    ti = TestInput("diff", ev["date"].to_numpy(), ev["r12"].to_numpy(), ev["grp"].to_numpy(), ("disp", "nodisp"),
                   label="B009 r12: displacement reclaim - other B002 events")
    return StudyData(ev, None, ti, hurdle_norm=ev["atr"].to_numpy())


def study_B010(ctx: StudyContext, spec: dict) -> StudyData:
    """HB06: after a B002 event, within 12 bars, the first close beyond the most recent swing
    (3/3, confirmed by the previous bar) in the reversal direction; vs time-matched.
    G6: the parent B002 entries of the same sessions."""
    p = spec["params"]
    w = win(p)
    parent = _sweep_events(ctx, child(spec, "B002"), "pd")
    sw = ctx.swings_ict
    last_l, last_h = sw["last_l"].to_numpy(float), sw["last_h"].to_numpy(float)
    rows, par = [], []
    for j, r in enumerate(parent.itertuples(index=False)):
        t, d = int(r.pos), int(r.direction)
        for k in range(t + 1, min(t + p["within_bars"], ctx.n5 - 1) + 1):
            if ctx.sid5[k] != ctx.sid5[t]:
                break
            ref = last_l[k - 1] if d < 0 else last_h[k - 1]
            if np.isfinite(ref) and d * (ctx.c[k] - ref) > 0:
                if in_window(ctx, k, w):
                    rows.append({"pos": k, "direction": d, "parent": j})
                    par.append(j)
                break
    ev = concat([pd.DataFrame(rows)]) if rows else concat([])
    ct = tm_controls(ctx, "B010", ev, spec["control_draws"])
    ev, ct = finish(ctx, ev, ct)
    parents = parent.iloc[sorted(set(par))] if par else parent.iloc[:0]
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "B010 r12: MSS after sweep - time-matched"),
                     twin=diff_test(ev, parents, "r12", "B010 G6: MSS entry - B002 entry same sessions", groups=("mss", "b002")),
                     hurdle_norm=ev["atr"].to_numpy())


def _fvg_setup(ctx: StudyContext, p: dict) -> dict:
    """Filtered FVGs (width >= 0.25 ATR, formed 09:45-14:30 on event days) with move size,
    size tercile and the relative depth of their entry and invalidation edges, plus the
    pool of gap-free 3-bar moves (matched-displacement twins) under the same filters."""

    def build() -> dict:
        w0, w1 = win(p, "formed")
        o, h, l, c, a = ctx.o, ctx.h, ctx.l, ctx.c, ctx.atr5
        f = detect_fvgs(ctx.b5, ctx.atr5_series)
        t = f["pos"].to_numpy(np.int64)
        okd = (ctx.day5[t] >= 0) & ctx.day_ok[np.clip(ctx.day5[t], 0, None)] & (ctx.day5[t - 2] == ctx.day5[t])
        cm = ctx.close_min5[t]
        f = f[okd & (f["width_atr"].to_numpy() >= p["min_width_atr"]) & (cm >= w0) & (cm <= w1)].reset_index(drop=True)
        t = f["pos"].to_numpy(np.int64)
        d = f["direction"].to_numpy(np.int64)
        ext = np.where(d > 0, h[t], l[t])
        org = np.where(d > 0, l[t - 2], h[t - 2])
        R = d * (ext - org)
        f["size"] = d * (c[t] - o[t - 2]) / a[t - 1]
        entry = np.where(d > 0, f["top"], f["bottom"])
        inval = np.where(d > 0, f["bottom"], f["top"])
        f["q_entry"] = d * (ext - entry) / R
        f["q_inval"] = d * (ext - inval) / R
        f["entry"], f["inval"] = entry, inval
        cuts = np.quantile(f["size"], [1 / 3, 2 / 3]) if len(f) >= 3 else np.array([np.inf, np.inf])
        f["tercile"] = np.searchsorted(cuts, f["size"].to_numpy(), side="left")
        tt = np.arange(2, ctx.n5)
        dd = np.sign(c[tt] - o[tt - 2]).astype(np.int64)
        okc = (ctx.day5[tt] >= 0) & ctx.day_ok[np.clip(ctx.day5[tt], 0, None)] & (ctx.day5[tt - 2] == ctx.day5[tt])
        okc &= (ctx.close_min5[tt] >= w0) & (ctx.close_min5[tt] <= w1) & (dd != 0)
        nogap = np.where(dd > 0, l[tt] <= h[tt - 2], h[tt] >= l[tt - 2])
        cand = pd.DataFrame({"pos": tt, "direction": dd})[okc & nogap].reset_index(drop=True)
        ct_ = cand["pos"].to_numpy(np.int64)
        cd = cand["direction"].to_numpy(np.int64)
        cand["ext"] = np.where(cd > 0, h[ct_], l[ct_])
        cand["org"] = np.where(cd > 0, l[ct_ - 2], h[ct_ - 2])
        cand["R"] = cd * (cand["ext"] - cand["org"])
        cand["size"] = cd * (c[ct_] - o[ct_ - 2]) / a[ct_ - 1]
        cand["pre"] = np.where(cd > 0, h[ct_ - 2], l[ct_ - 2])
        cand = cand[(cand["R"] > 0) & np.isfinite(cand["size"])].reset_index(drop=True)
        cand["tercile"] = np.searchsorted(cuts, cand["size"].to_numpy(), side="left")
        return {"fvg": f, "cand": cand}

    return cached(ctx, "fvg_setup", build)


def _sample_twins(ctx: StudyContext, f: pd.DataFrame, cand: pd.DataFrame, n: int, seed: int, extra=None) -> pd.DataFrame:
    """``n`` gap-free moves of the same direction and size tercile per FVG (with replacement)."""
    rng = np.random.default_rng(seed)
    pools = {(d, t): g.index.to_numpy() for (d, t), g in cand.groupby(["direction", "tercile"]) if len(g)}
    pick, src = [], []
    for j, r in enumerate(f.itertuples(index=False)):
        pool = pools.get((int(r.direction), int(r.tercile)))
        if extra is not None and pool is not None:
            pool = pool[extra[pool]]
        if pool is None or pool.size == 0:
            continue
        pick.append(rng.choice(pool, size=n, replace=True))
        src.append(np.full(n, j))
    if not pick:
        return cand.iloc[:0].assign(src=pd.Series(dtype=np.int64))
    tw = cand.loc[np.concatenate(pick)].reset_index(drop=True)
    tw["src"] = np.concatenate(src)
    return tw


def _retrace_events(ctx: StudyContext, t: np.ndarray, d: np.ndarray, entry: np.ndarray, inval: np.ndarray, max_bars: int,
                    w: tuple[int, int]) -> pd.DataFrame:
    """First bar within ``max_bars`` that trades back to ``entry``; event if it does not close
    beyond ``inval`` (then the setup is void)."""
    rows = []
    for ti, di, E, V in zip(t, d, entry, inval):
        for k in range(int(ti) + 1, min(int(ti) + max_bars, ctx.n5 - 1) + 1):
            if ctx.sid5[k] != ctx.sid5[ti]:
                break
            touched = ctx.l[k] <= E if di > 0 else ctx.h[k] >= E
            if touched:
                if (ctx.c[k] >= V if di > 0 else ctx.c[k] <= V) and in_window(ctx, k, w):
                    rows.append({"pos": k, "direction": int(di), "origin": int(ti)})
                break
    return concat([pd.DataFrame(rows)]) if rows else concat([])


def _twin_levels(f: pd.DataFrame, tw: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    q_e = f["q_entry"].to_numpy()[tw["src"].to_numpy()]
    q_b = f["q_inval"].to_numpy()[tw["src"].to_numpy()]
    d = tw["direction"].to_numpy()
    return tw["ext"].to_numpy() - d * q_e * tw["R"].to_numpy(), tw["ext"].to_numpy() - d * q_b * tw["R"].to_numpy()


def study_B011(ctx: StudyContext, spec: dict) -> StudyData:
    """HB07: first retrace into the FVG within 24 bars that does not close through it; with the
    FVG; vs matched-displacement twins entered at the same relative depth."""
    p = spec["params"]
    s = _fvg_setup(ctx, p)
    f, cand = s["fvg"], s["cand"]
    ev = _retrace_events(ctx, f["pos"].to_numpy(), f["direction"].to_numpy(), f["entry"].to_numpy(), f["inval"].to_numpy(), p["max_bars"], win(p))
    tw = _sample_twins(ctx, f, cand, spec["control_draws"], seed_of(ctx, "B011", 2))
    e_, v_ = _twin_levels(f, tw)
    ct = _retrace_events(ctx, tw["pos"].to_numpy(), tw["direction"].to_numpy(), e_, v_, p["max_bars"], win(p))
    ev, ct = finish(ctx, ev, ct)
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "B011 r12: FVG retrace - matched-displacement twin"),
                     hurdle_norm=ev["atr"].to_numpy(), conditions=("trend_tercile",))


def study_B012(ctx: StudyContext, spec: dict) -> StudyData:
    """HB08: P(partial fill within 12 bars) of FVGs vs matched-displacement twins (same depth).
    Also the dedicated FVG table: full fill, time to fill, by width tercile and trend."""
    p = spec["params"]
    s = _fvg_setup(ctx, p)
    f, cand = s["fvg"], s["cand"]
    tw = _sample_twins(ctx, f, cand, spec["control_draws"], seed_of(ctx, "B012", 2))
    e_, _ = _twin_levels(f, tw)

    def filled(t, d, E):
        out = np.zeros(len(t))
        for j, (ti, di, Ei) in enumerate(zip(t, d, E)):
            for k in range(int(ti) + 1, min(int(ti) + p["fill_bars"], ctx.n5 - 1) + 1):
                if ctx.sid5[k] != ctx.sid5[ti]:
                    break
                if (ctx.l[k] <= Ei) if di > 0 else (ctx.h[k] >= Ei):
                    out[j] = 1.0
                    break
        return out

    fv = f.assign(fill=filled(f["pos"].to_numpy(), f["direction"].to_numpy(), f["entry"].to_numpy()))
    tv = tw.assign(fill=filled(tw["pos"].to_numpy(), tw["direction"].to_numpy(), e_))
    for df in (fv, tv):
        df["date"] = ctx.days.to_numpy()[ctx.day5[df["pos"].to_numpy(np.int64)]] if len(df) else []
    ti = diff_test(fv, tv, "fill", "B012 P(partial fill <= 12 bars): FVG - twin", groups=("fvg", "twin"))
    full = _fvg_fill_table(ctx, f, p)
    return StudyData(fv.assign(day=ctx.day5[fv["pos"].to_numpy(np.int64)]), tv, ti, tables={"fvg_fill_study": full})


def _fvg_fill_table(ctx: StudyContext, f: pd.DataFrame, p: dict) -> pd.DataFrame:
    if f.empty:
        return pd.DataFrame()
    out = []
    for r in f.itertuples(index=False):
        t, d = int(r.pos), int(r.direction)
        touch = mid = full = np.nan
        midp = (r.top + r.bottom) / 2
        for k in range(t + 1, min(t + p["fill_bars"] * 4, ctx.n5 - 1) + 1):
            if ctx.sid5[k] != ctx.sid5[t]:
                break
            x = ctx.l[k] if d > 0 else ctx.h[k]
            if np.isnan(touch) and (x <= r.top if d > 0 else x >= r.bottom):
                touch = k - t
            if np.isnan(mid) and (x <= midp if d > 0 else x >= midp):
                mid = k - t
            if np.isnan(full) and (x <= r.bottom if d > 0 else x >= r.top):
                full = k - t
                break
        out.append({"width_atr": r.width_atr, "direction": d, "touch_bars": touch, "mid_bars": mid, "full_bars": full,
                    "trend_tercile": ctx.trend_regime[ctx.day5[t]]})
    df = pd.DataFrame(out)
    df["width_tercile"] = tercile_labels(df["width_atr"].to_numpy())
    return df.groupby(["width_tercile", "direction"]).agg(
        n=("touch_bars", "size"), p_touch=("touch_bars", lambda x: x.notna().mean()), p_mid=("mid_bars", lambda x: x.notna().mean()),
        p_full=("full_bars", lambda x: x.notna().mean()), median_bars_to_touch=("touch_bars", "median")).reset_index()


def study_B013(ctx: StudyContext, spec: dict) -> StudyData:
    """HB09 inverse FVG: a close through the far edge within 60 bars of formation (same
    session), direction against the original gap; vs the same close through the pre-move
    extreme of gap-free moves of the same size tercile that closed beyond it."""
    p = spec["params"]
    s = _fvg_setup(ctx, p)
    f, cand = s["fvg"], s["cand"]
    w = win(p)

    def through(t, d, lvl):
        rows = []
        for ti, di, L in zip(t, d, lvl):
            for k in range(int(ti) + 1, min(int(ti) + p["max_bars"], ctx.n5 - 1) + 1):
                if ctx.sid5[k] != ctx.sid5[ti]:
                    break
                if (ctx.c[k] < L) if di > 0 else (ctx.c[k] > L):
                    if in_window(ctx, k, w):
                        rows.append({"pos": k, "direction": -int(di), "origin": int(ti)})
                    break
        return concat([pd.DataFrame(rows)]) if rows else concat([])

    ev = through(f["pos"].to_numpy(), f["direction"].to_numpy(), f["inval"].to_numpy())
    cpos = cand["pos"].to_numpy(np.int64)
    beyond = (cand["direction"].to_numpy() * (ctx.c[cpos] - cand["pre"].to_numpy())) > 0
    tw = _sample_twins(ctx, f, cand, spec["control_draws"], seed_of(ctx, "B013", 2), extra=beyond)
    ct = through(tw["pos"].to_numpy(), tw["direction"].to_numpy(), tw["pre"].to_numpy())
    ev, ct = finish(ctx, ev, ct)
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "B013 r12: inverse FVG - twin"), hurdle_norm=ev["atr"].to_numpy())


def study_B014(ctx: StudyContext, spec: dict) -> StudyData:
    """HB10 order block: a structure break (close beyond the last 3/3 swing) by a displacement
    bar; OB = last opposite-colour bar among the 5 before it; event = first bar within 48 that
    trades into the OB without closing through it (a close through voids it); vs zones of the
    same width at a uniform random depth of the impulse leg (5 draws)."""
    p = spec["params"]
    w = win(p)
    cf = ctx.candles
    up, dn = cf["disp_up"].to_numpy(bool), cf["disp_dn"].to_numpy(bool)
    rng = np.random.default_rng(seed_of(ctx, "B014", 2))
    rows, twin_rows = [], []
    for r in ctx.breaks_ict.itertuples(index=False):
        dpos, d = int(r.pos), int(r.direction)
        k = ctx.day5[dpos]
        if k < 0 or not ctx.day_ok[k] or not (up[dpos] if d > 0 else dn[dpos]):
            continue
        tdc = ctx.trading_date_code5
        cand = [j for j in range(dpos - 1, max(dpos - 6, -1), -1)
                if tdc[j] == tdc[dpos] and ((ctx.c[j] < ctx.o[j]) if d > 0 else (ctx.c[j] > ctx.o[j]))]
        if not cand:
            continue
        j = cand[0]
        zlo, zhi = ctx.l[j], ctx.h[j]
        leg_lo = ctx.l[j: dpos + 1].min() if d > 0 else ctx.l[dpos]
        leg_hi = ctx.h[dpos] if d > 0 else ctx.h[j: dpos + 1].max()
        width = zhi - zlo
        e = _zone_retest(ctx, dpos, d, zlo, zhi, p["max_bars"], w)
        if e >= 0:
            rows.append({"pos": e, "direction": d, "ob_pos": j, "disp_pos": dpos})
        if leg_hi - leg_lo > width > 0:
            for b in rng.uniform(leg_lo, leg_hi - width, size=spec["control_draws"]):
                e2 = _zone_retest(ctx, dpos, d, b, b + width, p["max_bars"], w)
                if e2 >= 0:
                    twin_rows.append({"pos": e2, "direction": d, "disp_pos": dpos})
    ev = dedupe(concat([pd.DataFrame(rows)]) if rows else concat([]))
    ct = concat([pd.DataFrame(twin_rows)]) if twin_rows else concat([])
    ev, ct = finish(ctx, ev, ct)
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "B014 r12: order-block retest - random-depth twin"), hurdle_norm=ev["atr"].to_numpy())


def _zone_retest(ctx: StudyContext, start: int, d: int, zlo: float, zhi: float, max_bars: int, w: tuple[int, int]) -> int:
    for k in range(start + 1, min(start + max_bars, ctx.n5 - 1) + 1):
        if ctx.sid5[k] != ctx.sid5[start]:
            return -1
        if (ctx.c[k] < zlo) if d > 0 else (ctx.c[k] > zhi):
            return -1
        if (ctx.l[k] <= zhi) if d > 0 else (ctx.h[k] >= zlo):
            return k if in_window(ctx, k, w) else -1
    return -1


def study_B015(ctx: StudyContext, spec: dict) -> StudyData:
    """HB11a: every 6th RTH bar (from 09:30), location in the last confirmed swing range
    (3/3), deciles 1-10 inside [0, 1]; 12-bar long return; pooled within-stratum slope over
    structure trend x daily-ER tercile. Effect = -slope (positive = discount is better)."""
    p = spec["params"]
    w = win(p)
    sw = ctx.swings_ict
    hh, ll = sw["last_h"].to_numpy(float), sw["last_l"].to_numpy(float)
    i = np.flatnonzero(ctx.elig5 & (ctx.slot5 % p["every_bars"] == 0) & (ctx.close_min5 >= w[0]) & (ctx.close_min5 <= w[1]))
    rngp = hh[i] - ll[i]
    loc = (ctx.c[i] - ll[i]) / np.where(rngp > 0, rngp, np.nan)
    keep = np.isfinite(loc) & (loc >= 0) & (loc <= 1)
    i, loc = i[keep], loc[keep]
    ev = add_day_conditions(ctx, add_outcomes(ctx, pd.DataFrame({"pos": i, "direction": np.ones(len(i), dtype=np.int64)})))
    ev["decile"] = np.minimum(9, np.floor(loc * 10)).astype(int) + 1
    ev["stratum"] = [f"{a:g}|{b}" for a, b in zip(ctx.trend_ict[i], ev["trend_tercile"].to_numpy())]
    ti = TestInput("slope", ev["date"].to_numpy(), ev["r12"].to_numpy(), np.full(len(ev), "all"), ("all",),
                   stratum=ev["stratum"].to_numpy(), x=ev["decile"].to_numpy(float), sign=-1.0,
                   label="B015 -slope of r12 on location decile | trend strata")
    table = ev.groupby("decile").agg(n=("r12", "size"), r12_mean=("r12", "mean")).reset_index()
    return StudyData(ev, None, ti, tables={"r12_by_decile": table})


def study_B016(ctx: StudyContext, spec: dict) -> StudyData:
    """HB11b OTE: after a structure break, leg = last confirmed opposite swing -> running
    extreme (through the previous bar); first bar entering each retracement band within 24
    bars (leg void when price passes the swing); OTE [0.62, 0.79] minus [0.50, 0.62) bands."""
    p = spec["params"]
    w = win(p)
    bands = {name: tuple(v) for name, v in p["bands"].items()}
    sw = ctx.swings_ict
    rows = []
    for r in ctx.breaks_ict.itertuples(index=False):
        b, d = int(r.pos), int(r.direction)
        k = ctx.day5[b]
        if k < 0 or not ctx.day_ok[k]:
            continue
        L = sw["last_l"].iat[b] if d > 0 else sw["last_h"].iat[b]
        Lp = sw["last_l_pos"].iat[b] if d > 0 else sw["last_h_pos"].iat[b]
        if not (np.isfinite(L) and np.isfinite(Lp)) or int(Lp) >= b:
            continue
        Lp = int(Lp)
        ext = ctx.h[Lp: b + 1].max() if d > 0 else ctx.l[Lp: b + 1].min()
        entered = set()
        for j in range(b + 1, min(b + p["max_bars"], ctx.n5 - 1) + 1):
            if ctx.sid5[j] != ctx.sid5[b]:
                break
            if (ctx.l[j] < L) if d > 0 else (ctx.h[j] > L):
                break
            span = d * (ext - L)
            retr = d * (ext - (ctx.l[j] if d > 0 else ctx.h[j])) / span if span > 0 else np.nan
            for name, (lo, _hi) in bands.items():
                if name not in entered and np.isfinite(retr) and retr >= lo:
                    entered.add(name)
                    if in_window(ctx, j, w):
                        rows.append({"pos": j, "direction": d, "band": name, "break_pos": b})
            ext = max(ext, ctx.h[j]) if d > 0 else min(ext, ctx.l[j])
    ev = concat([pd.DataFrame(rows)]) if rows else concat([])
    ev = add_day_conditions(ctx, add_outcomes(ctx, ev))
    if ev.empty:
        return StudyData(ev, None, None, status="NO_EVENTS")
    ti = TestInput("diff", ev["date"].to_numpy(), ev["r12"].to_numpy(), ev["band"].to_numpy(), ("ote", "b50"),
                   label="B016 r12: OTE band - [0.50, 0.62) band")
    table = ev.groupby("band").agg(n=("r12", "size"), r12_mean=("r12", "mean")).reset_index()
    return StudyData(ev, None, ti, hurdle_norm=ev["atr"].to_numpy(), tables={"by_band": table})


def study_B017(ctx: StudyContext, spec: dict) -> StudyData:
    """HB12: (event - time-matched) of B002 + B006 sweeps in RTH_OPEN + MORNING minus the same
    difference in MIDDAY."""
    ev = dedupe(concat([_sweep_events(ctx, child(spec, "B002"), "pd"), _sweep_events(ctx, child(spec, "B006"), "on")]))
    if ev.empty:
        return StudyData(ev, None, None, status="NO_EVENTS")
    ct = add_outcomes(ctx, tm_controls(ctx, "B017", ev, spec["control_draws"], inherit=("bucket",)))
    kz = set(spec["params"]["killzone_buckets"])
    mid = set(spec["params"]["comparison_buckets"])
    parts = {"ev_kz": ev[ev["bucket"].isin(kz)], "ct_kz": ct[ct["bucket"].isin(kz)],
             "ev_mid": ev[ev["bucket"].isin(mid)], "ct_mid": ct[ct["bucket"].isin(mid)]}
    return StudyData(ev, ct, dod_test(parts, "r12", "B017 r12: (ev-ct) killzone - (ev-ct) midday"), hurdle_norm=ev["atr"].to_numpy())


def study_B018(ctx: StudyContext, spec: dict) -> StudyData:
    """HB13 SMT divergence: needs ES and NQ on one grid, and NQ may not be used before rule
    freeze (protocol 2.5). Registered as BLOCKED so the test count stays honest."""
    return StudyData(concat([]), None, None, status="BLOCKED", notes=["needs NQ data; NQ embargoed until rule freeze"])


# =======================================================================================
# Family C - price action / support and resistance
# =======================================================================================
def _touch_kw(p: dict) -> dict:
    return dict(zone_atr=p["zone_atr"], break_atr=p["break_atr"], rearm_atr=p["rearm_atr"], window=win(p))


def _touches(ctx: StudyContext, lv: dict, kw: dict) -> pd.DataFrame:
    """Zone entries of the 'lo' level as support (+1) and of the 'hi' level as resistance (-1)."""
    return concat([zone_entries(ctx, lv["lo"], -1, **kw), zone_entries(ctx, lv["hi"], +1, **kw)])


def _first_touch(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["touch_number"] == 1].reset_index(drop=True) if len(df) else df


def _pd_first_touch(ctx: StudyContext, spec: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """C001 events and their shifted-reference controls (cached; reused by C009vC001, C016, C017)."""
    def build():
        kw = _touch_kw(spec["params"])
        lv = _pd_levels(ctx)
        ev = _first_touch(_touches(ctx, lv, kw))
        ct = shifted_controls(ctx, "C001", lv, lambda x: _first_touch(_touches(ctx, x, kw)), ctx.rth_open, spec["control_draws"])
        return finish(ctx, ev, ct)
    return cached(ctx, "C001_first_touch", build)


def study_C001(ctx: StudyContext, spec: dict) -> StudyData:
    """HC01: first zone entry of PDL (long) / PDH (short) in the session; vs shifted reference."""
    ev, ct = _pd_first_touch(ctx, spec)
    ev, ct = ev.copy(), ct.copy()
    for df in (ev, ct):
        if len(df):
            p = df["pos"].to_numpy()
            mom = np.sign(ctx.c[p] - ctx.c[np.clip(p - 20, 0, None)])
            df["trend_aligned"] = np.where(mom == df["direction"].to_numpy(), "yes", "no")
            k = df["day"].to_numpy()
            df["open_dist_tercile"] = tercile_labels(np.abs(ctx.rth_open[k] - df["level"].to_numpy()) / ctx.atr_d[k],
                                                     np.abs(ctx.rth_open[ev["day"].to_numpy()] - ev["level"].to_numpy()) / ctx.atr_d[ev["day"].to_numpy()])
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "C001 r12: PD first touch - shifted reference"), hurdle_norm=ev["atr"].to_numpy(),
                     conditions=("trend_aligned", "trend_tercile", "vol_tercile", "open_dist_tercile"))


def _pd_pz_touches(ctx: StudyContext, spec: dict) -> pd.DataFrame:
    kw = _touch_kw(spec["params"])
    pd_ = _touches(ctx, _pd_levels(ctx), kw).assign(source="PD")
    pz = _touches(ctx, _pz_levels(ctx), kw).assign(source="PZ")
    return concat([pd_, pz])


def study_C002(ctx: StudyContext, spec: dict) -> StudyData:
    """HC02: all zone entries of PD and PZ levels numbered within the session; (event -
    time-matched) for touch 1 minus the same for touches 3+."""
    ev = _pd_pz_touches(ctx, spec)
    if ev.empty:
        return StudyData(ev, None, None, status="NO_EVENTS")
    ev["touch_grp"] = np.where(ev["touch_number"] == 1, "1", np.where(ev["touch_number"] == 2, "2", "3+"))
    ev = dedupe(ev)
    ct = tm_controls(ctx, "C002", ev, spec["control_draws"], inherit=("touch_grp",))
    ev, ct = finish(ctx, ev, ct)
    parts = {"ev1": ev[ev["touch_grp"] == "1"], "ct1": ct[ct["touch_grp"] == "1"],
             "ev3": ev[ev["touch_grp"] == "3+"], "ct3": ct[ct["touch_grp"] == "3+"]}
    table = ev.groupby("touch_grp").agg(n=("r12", "size"), r12_mean=("r12", "mean")).reset_index()
    return StudyData(ev, ct, dod_test(parts, "r12", "C002 r12: (ev-ct) touch 1 - (ev-ct) touch 3+"), hurdle_norm=ev["atr"].to_numpy(),
                     tables={"by_touch": table})


def study_C003(ctx: StudyContext, spec: dict) -> StudyData:
    """HC03: first zone entries of pivot zones by zone age (sessions since the zone's first
    pivot): current session vs 1 vs 2-5 sessions; (ev - ct) current minus (ev - ct) 2-5."""
    kw = _touch_kw(spec["params"])
    ev = _first_touch(_touches(ctx, _pz_levels(ctx), kw))
    if ev.empty:
        return StudyData(ev, None, None, status="NO_EVENTS")
    pos = ev["pos"].to_numpy()
    first = np.where(ev["direction"].to_numpy() > 0, ctx.pz_levels["sup_first_pos"][pos], ctx.pz_levels["res_first_pos"][pos])
    ok = np.isfinite(first)
    fd = np.full(len(ev), -10**6)
    fd[ok] = ctx.trading_date_code5[first[ok].astype(np.int64)]
    age = ctx.day5[pos] - fd
    ev["age_grp"] = np.where(age == 0, "0", np.where(age == 1, "1", np.where((age >= 2) & (age <= 5), "2-5", "")))
    ev = ev[ok & (ev["age_grp"] != "")].reset_index(drop=True)
    ct = tm_controls(ctx, "C003", ev, spec["control_draws"], inherit=("age_grp",))
    ev, ct = finish(ctx, ev, ct)
    parts = {"ev0": ev[ev["age_grp"] == "0"], "ct0": ct[ct["age_grp"] == "0"],
             "ev25": ev[ev["age_grp"] == "2-5"], "ct25": ct[ct["age_grp"] == "2-5"]}
    return StudyData(ev, ct, dod_test(parts, "r12", "C003 r12: (ev-ct) current-session zones - (ev-ct) 2-5 sessions old"),
                     hurdle_norm=ev["atr"].to_numpy())


def _breakout_events(ctx: StudyContext, spec: dict, variant: str, levels: dict | None = None) -> pd.DataFrame:
    p = spec["params"]
    w = win(p)
    pdl = _pd_levels(ctx) if levels is None else {"hi": levels["pd_hi"], "lo": levels["pd_lo"]}
    pzl = _pz_levels(ctx) if levels is None else {"hi": levels["pz_hi"], "lo": levels["pz_lo"]}
    parts = [breakouts(ctx, pdl["hi"], +1, variant=variant, window=w, near_side_open=True),
             breakouts(ctx, pdl["lo"], -1, variant=variant, window=w, near_side_open=True),
             breakouts(ctx, pzl["hi"], +1, variant=variant, window=w, near_side_open=False),
             breakouts(ctx, pzl["lo"], -1, variant=variant, window=w, near_side_open=False)]
    return dedupe(concat(parts))


def _breakout_study(ctx: StudyContext, spec: dict, variant: str) -> StudyData:
    """HC04 family: first breakout of PD or PZ levels per session; vs time-matched; G6 = the
    strength twin (same-minute bars with a body of the same sign within +/-20%)."""
    sid = spec["id"]
    ev = _breakout_events(ctx, spec, variant)
    ct = tm_controls(ctx, sid, ev, spec["control_draws"])
    tw = add_outcomes(ctx, strength_twin(ctx, ev, n=spec["control_draws"], seed=seed_of(ctx, sid, 3)))
    ev, ct = finish(ctx, ev, ct)
    if len(ev):
        back = []
        for r in ev.itertuples(index=False):
            p0, d, L = int(r.pos), int(r.direction), float(r.level)
            seg = [t for t in range(p0 + 1, min(p0 + 3, ctx.n5 - 1) + 1) if ctx.sid5[t] == ctx.sid5[p0]]
            back.append(float(any(d * (ctx.c[t] - L) < 0 for t in seg)))
        ev["false_break_3"] = back
    return StudyData(ev, ct, diff_test(ev, ct, "r12", f"{sid} r12: {variant} breakout - time-matched"),
                     twin=diff_test(ev, tw, "r12", f"{sid} G6: breakout - strength twin", groups=("ev", "strength")),
                     hurdle_norm=ev["atr"].to_numpy(), conditions=("trend_tercile", "vol_tercile"),
                     tables={"false_break": pd.DataFrame([{"variant": variant, "n": len(ev),
                                                           "false_break_rate_3": ev["false_break_3"].mean() if len(ev) else np.nan}])})


def study_C004(ctx: StudyContext, spec: dict) -> StudyData:
    """HC04 ATR breakout: close beyond the level by > 0.25 ATR."""
    return _breakout_study(ctx, spec, "atr")


def study_C005(ctx: StudyContext, spec: dict) -> StudyData:
    """HC04 wick breakout: extreme beyond the level by more than 1 tick."""
    return _breakout_study(ctx, spec, "wick")


def study_C006(ctx: StudyContext, spec: dict) -> StudyData:
    """HC04 close breakout: close beyond the level by > 0.10 ATR."""
    return _breakout_study(ctx, spec, "close")


def study_C007(ctx: StudyContext, spec: dict) -> StudyData:
    """HC04 volume breakout: C006 breakouts with bar volume >= 80th pct of the slot (20 sessions)."""
    return _breakout_study(ctx, spec, "volume")


def study_C008(ctx: StudyContext, spec: dict) -> StudyData:
    """HC04 strong-body breakout: C006 breakouts on a displacement bar in the break direction."""
    return _breakout_study(ctx, spec, "body")


def _failed_breakouts(ctx: StudyContext, spec: dict, lv: dict, sources: tuple[str, ...]) -> pd.DataFrame:
    p = spec["params"]
    kw = dict(mode="close", close_thr_atr=p["close_thr_atr"], n_bars=p["reclaim_bars"], window=win(p))
    parts = []
    if "PD" in sources:
        parts += [sweep_reclaim(ctx, lv["pd_hi"], +1, near_side_open=True, **kw), sweep_reclaim(ctx, lv["pd_lo"], -1, near_side_open=True, **kw)]
    if "PZ" in sources:
        parts += [sweep_reclaim(ctx, lv["pz_hi"], +1, near_side_open=False, **kw), sweep_reclaim(ctx, lv["pz_lo"], -1, near_side_open=False, **kw)]
    return dedupe(concat(parts))


PZ_ADAPTIVE = {"pz_hi": "res", "pz_lo": "sup"}


def _all_levels(ctx: StudyContext) -> dict[str, np.ndarray]:
    pdl, pzl = _pd_levels(ctx), _pz_levels(ctx)
    return {"pd_hi": pdl["hi"], "pd_lo": pdl["lo"], "pz_hi": pzl["hi"], "pz_lo": pzl["lo"]}


def _c009(ctx: StudyContext, spec: dict, sources: tuple[str, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    def build():
        lv = _all_levels(ctx)
        ev = _failed_breakouts(ctx, spec, lv, sources)
        ct = shifted_controls(ctx, "C009" + "".join(sources), lv, lambda x: _failed_breakouts(ctx, spec, x, sources), ctx.rth_open,
                              spec["control_draws"], adaptive=PZ_ADAPTIVE)
        return finish(ctx, ev, ct)
    return cached(ctx, "C009_" + "".join(sources), build)


def study_C009(ctx: StudyContext, spec: dict) -> StudyData:
    """HC05: close beyond a PD or PZ level by > 0.10 ATR, then a close back inside within 3
    bars; reversal; vs shifted reference (PD and PZ levels shifted together)."""
    ev, ct = _c009(ctx, spec, ("PD", "PZ"))
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "C009 r12: failed breakout - shifted reference"), hurdle_norm=ev["atr"].to_numpy(),
                     conditions=("vol_tercile", "trend_tercile"))


def study_C009vC001(ctx: StudyContext, spec: dict) -> StudyData:
    """Registered HC05 comparison on PD levels: (C009 - shifted) minus (C001 - shifted)."""
    fb_ev, fb_ct = _c009(ctx, child(spec, "C009"), ("PD",))
    t_ev, t_ct = _pd_first_touch(ctx, child(spec, "C001"))
    parts = {"fb_ev": fb_ev, "fb_ct": fb_ct, "touch_ev": t_ev, "touch_ct": t_ct}
    ev = concat([fb_ev.assign(grp="failed_breakout"), t_ev.assign(grp="first_touch")])
    return StudyData(ev, None, dod_test(parts, "r12", "C009vC001 r12: (failed breakout - shifted) - (first touch - shifted), PD"),
                     hurdle_norm=ev["atr"].to_numpy() if len(ev) else None)


def study_C010(ctx: StudyContext, spec: dict) -> StudyData:
    """HC06 polarity: after a C004 breakout, the first retest within 24 bars that holds
    (low <= L + 0.10 ATR, close >= L - 0.25 ATR); vs the same pipeline on shifted levels."""
    p = spec["params"]
    c4 = child(spec, "C004")

    def detect(lv: dict) -> pd.DataFrame:
        brk = _breakout_events(ctx, c4, "atr", lv)
        rt = breakout_retests(ctx, brk, max_bars=p["max_bars"], zone_atr=p["zone_atr"], break_atr=p["break_atr"], window=win(p))
        return rt

    lv = _all_levels(ctx)
    allrt = detect(lv)
    ev = allrt[allrt["retested"]].reset_index(drop=True) if len(allrt) else allrt
    ct = shifted_controls(ctx, "C010", lv, lambda x: (lambda r: r[r["retested"]] if len(r) else r)(detect(x)), ctx.rth_open,
                          spec["control_draws"], adaptive=PZ_ADAPTIVE)
    ev, ct = finish(ctx, ev, ct)
    miss = pd.DataFrame([{"breakouts": len(allrt), "retested": int(allrt["retested"].sum()) if len(allrt) else 0,
                          "share_without_retest": 1 - allrt["retested"].mean() if len(allrt) else np.nan}])
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "C010 r12: polarity retest - shifted reference"), hurdle_norm=ev["atr"].to_numpy(),
                     tables={"missed_trades": miss})


def _compression(ctx: StudyContext, spec: dict) -> pd.DataFrame:
    """HC07 range breaks with a compression flag at t-1 (cached)."""
    def build():
        p = spec["params"]
        w = win(p)
        n = int(p["range_bars"])
        h, l, c, a = ctx.h, ctx.l, ctx.c, ctx.atr5
        hs, ls = pd.Series(h), pd.Series(l)
        rhi = hs.rolling(n, min_periods=n).max().shift(1).to_numpy()
        rlo = ls.rolling(n, min_periods=n).min().shift(1).to_numpy()
        rth = ctx.day5 >= 0
        pct = np.full(ctx.n5, np.nan)
        pct[rth] = pd.Series(a[rth]).rolling(int(p["pct_window"]), min_periods=int(p["pct_window"])).rank(pct=True).to_numpy()
        rng12 = rhi - rlo  # range of bars t-12 .. t-1, i.e. the 12-bar range as of t-1
        comp = (np.r_[np.nan, pct[:-1]] <= p["atr_pct_max"]) & (rng12 <= p["range_atr_max"] * np.r_[np.nan, a[:-1]])
        up = c > rhi
        dn = c < rlo
        prev_in = np.r_[False, ~(up | dn)[:-1]]
        brk = (up | dn) & prev_in & ctx.elig5 & (ctx.close_min5 >= w[0]) & (ctx.close_min5 <= w[1])
        i = np.flatnonzero(brk)
        df = pd.DataFrame({"pos": i, "direction": np.where(up[i], 1, -1)})
        df["compressed"] = np.where(comp[i], "comp", "twin")
        df = add_day_conditions(ctx, add_outcomes(ctx, df))
        df["abs_r12"] = df["r12"].abs()
        df["abs_r12_atr_d"] = (df["r12"] * df["atr"] / df["atr_d"]).abs()
        return df
    return cached(ctx, "compression", build)


def study_C011(ctx: StudyContext, spec: dict) -> StudyData:
    """HC07 magnitude: mean |12-bar return| in ATR after a range break from compression minus
    after a range break without compression (also reported in ATR_d units)."""
    df = _compression(ctx, spec)
    ti = TestInput("diff", df["date"].to_numpy(), df["abs_r12"].to_numpy(), df["compressed"].to_numpy(), ("comp", "twin"),
                   label="C011 |r12| in ATR: compression - no compression")
    tbl = df.groupby("compressed").agg(n=("abs_r12", "size"), abs_r12=("abs_r12", "mean"), abs_r12_atr_d=("abs_r12_atr_d", "mean")).reset_index()
    return StudyData(df, None, ti, tables={"magnitude_units": tbl})


def study_C012(ctx: StudyContext, spec: dict) -> StudyData:
    """HC07 direction: signed 12-bar return after compression breaks minus non-compressed breaks."""
    df = _compression(ctx, spec)
    ev, ct = df[df["compressed"] == "comp"], df[df["compressed"] == "twin"]
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "C012 r12: compression break - non-compressed break"), hurdle_norm=ev["atr"].to_numpy())


def study_C013(ctx: StudyContext, spec: dict) -> StudyData:
    """HC08: structure trend +1 (3/3 pivots, close breaks) and a newly confirmed higher low ->
    long at the confirmation bar (mirror: trend -1, lower high); vs momentum twin (20 bars)."""
    p = spec["params"]
    w = win(p)
    pv = ctx.pivots_ict
    rows = []
    for kind, d in (("L", 1), ("H", -1)):
        q = pv[pv["kind"] == kind].sort_values("confirm_pos", kind="stable")
        prev_price = q["price"].shift(1).to_numpy()
        for (cp, price), pp in zip(q[["confirm_pos", "price"]].itertuples(index=False, name=None), prev_price):
            cp = int(cp)
            if not np.isfinite(pp) or not ctx.elig5[cp] or ctx.trend_ict[cp] != d or not in_window(ctx, cp, w):
                continue
            if (price > pp) if d > 0 else (price < pp):
                rows.append({"pos": cp, "direction": d})
    ev = dedupe(concat([pd.DataFrame(rows)]) if rows else concat([]))
    ct = momentum_twin(ctx, ev, lookback=p["momentum_lookback_bars"], n=spec["control_draws"], seed=seed_of(ctx, "C013", 2))
    ev, ct = finish(ctx, ev, ct)
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "C013 r12: higher-low confirmation - momentum twin"), hurdle_norm=ev["atr"].to_numpy())


def _rn_step(ctx: StudyContext, spec: dict) -> float:
    steps = spec["params"]["rn_step"]
    if ctx.instrument.symbol not in steps:
        raise KeyError(f"no round-number step for {ctx.instrument.symbol}")
    return float(steps[ctx.instrument.symbol])


def _rn_levels(ctx: StudyContext, step: float, offset: float) -> dict[str, np.ndarray]:
    prev = np.r_[np.nan, ctx.c[:-1]]
    lo = np.floor((prev - offset) / step) * step + offset
    hi = np.ceil((prev - offset) / step) * step + offset
    return {"lo": lo, "hi": hi}


def study_C014(ctx: StudyContext, spec: dict) -> StudyData:
    """HC09a: zone entries at the nearest round number below (support) / above (resistance);
    vs the identical detector on the grid shifted by half a step."""
    step = _rn_step(ctx, spec)
    kw = _touch_kw(spec["params"])
    ev = dedupe(_touches(ctx, _rn_levels(ctx, step, 0.0), kw))
    ct = dedupe(_touches(ctx, _rn_levels(ctx, step, step / 2), kw))
    ev, ct = finish(ctx, ev, ct)
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "C014 r12: round-number touch - half-step twin"), hurdle_norm=ev["atr"].to_numpy())


def _rn_cascade(ctx: StudyContext, step: float, offset: float, thr: float, w: tuple[int, int]) -> pd.DataFrame:
    prev = np.r_[np.nan, ctx.c[:-1]]
    up_lvl = (np.floor((prev - offset) / step) + 1) * step + offset
    dn_lvl = (np.ceil((prev - offset) / step) - 1) * step + offset
    a = ctx.atr5
    base = ctx.elig5 & (ctx.close_min5 >= w[0]) & (ctx.close_min5 <= w[1]) & (np.r_[-1, ctx.sid5[:-1]] == ctx.sid5)
    up = base & (ctx.c >= up_lvl + thr * a)
    dn = base & (ctx.c <= dn_lvl - thr * a)
    i_up, i_dn = np.flatnonzero(up), np.flatnonzero(dn)
    return pd.DataFrame({"pos": np.r_[i_up, i_dn], "direction": np.r_[np.ones(len(i_up), int), -np.ones(len(i_dn), int)],
                         "level": np.r_[up_lvl[i_up], dn_lvl[i_dn]]}).sort_values("pos", kind="stable").reset_index(drop=True)


def study_C015(ctx: StudyContext, spec: dict) -> StudyData:
    """HC09b: a bar whose previous close was below a round number closes >= 0.25 ATR above it
    (mirror down); with the cross; vs the half-step grid."""
    step = _rn_step(ctx, spec)
    p = spec["params"]
    ev = _rn_cascade(ctx, step, 0.0, p["thr_atr"], win(p))
    ct = _rn_cascade(ctx, step, step / 2, p["thr_atr"], win(p))
    ev, ct = finish(ctx, ev, ct)
    return StudyData(ev, ct, diff_test(ev, ct, "r12", "C015 r12: round-number cascade - half-step twin"), hurdle_norm=ev["atr"].to_numpy())


def study_C016(ctx: StudyContext, spec: dict) -> StudyData:
    """HC10: C001 events whose touch bar is a rejection candle (wick >= 0.50 range toward the
    level, close location >= 0.65 away from it) vs the other C001 events."""
    ev, _ = _pd_first_touch(ctx, child(spec, "C001"))
    ev = ev.copy()
    if ev.empty:
        return StudyData(ev, None, None, status="NO_EVENTS")
    p = spec["params"]
    cf = ctx.candles
    pos = ev["pos"].to_numpy()
    rng_ = cf["range"].to_numpy()[pos]
    clv = cf["clv"].to_numpy()[pos]
    lw, uw = cf["lower_wick"].to_numpy()[pos], cf["upper_wick"].to_numpy()[pos]
    sup = ev["direction"].to_numpy() > 0
    rej = np.where(sup, (lw >= p["wick_frac"] * rng_) & (clv >= p["clv"]), (uw >= p["wick_frac"] * rng_) & (clv <= 1 - p["clv"]))
    ev["grp"] = np.where(rej & (rng_ > 0), "rejection", "other")
    ti = TestInput("diff", ev["date"].to_numpy(), ev["r12"].to_numpy(), ev["grp"].to_numpy(), ("rejection", "other"),
                   label="C016 r12: rejection-candle touch - other first touches")
    return StudyData(ev, None, ti, hurdle_norm=ev["atr"].to_numpy())


def study_C017(ctx: StudyContext, spec: dict) -> StudyData:
    """HC11: (first touch - shifted) for PD levels minus the same for pivot zones; prior-week
    and round-number first touches against their own shifted reference are descriptive."""
    pd_ev, pd_ct = _pd_first_touch(ctx, child(spec, "C001"))
    kw = _touch_kw(spec["params"])
    det = lambda x: _first_touch(_touches(ctx, x, kw))  # noqa: E731
    pz = _pz_levels(ctx)
    pz_ev, pz_ct = finish(ctx, det(pz), shifted_controls(ctx, "C017", pz, det, ctx.rth_open, spec["control_draws"],
                                                         adaptive={"hi": "res", "lo": "sup"}))
    pw = {"hi": ctx.per_bar(ctx.prior_week["pw_high"].to_numpy(float)), "lo": ctx.per_bar(ctx.prior_week["pw_low"].to_numpy(float))}
    pw_ev, pw_ct = finish(ctx, det(pw), shifted_controls(ctx, "C017pw", pw, det, ctx.rth_open, spec["control_draws"]))
    desc = []
    for name, e, c in (("PD", pd_ev, pd_ct), ("PZ", pz_ev, pz_ct), ("PW", pw_ev, pw_ct)):
        desc.append({"source": name, "n_ev": len(e), "n_ct": len(c), "ev_r12": e["r12"].mean() if len(e) else np.nan,
                     "ct_r12": c["r12"].mean() if len(c) else np.nan})
    parts = {"pd_ev": pd_ev, "pd_ct": pd_ct, "pz_ev": pz_ev, "pz_ct": pz_ct}
    ev = concat([pd_ev.assign(source="PD"), pz_ev.assign(source="PZ")])
    return StudyData(ev, None, dod_test(parts, "r12", "C017 r12: (PD - shifted) - (PZ - shifted), first touches"),
                     hurdle_norm=ev["atr"].to_numpy() if len(ev) else None, tables={"by_source": pd.DataFrame(desc)})


def study_C018(ctx: StudyContext, spec: dict) -> StudyData:
    """HC12: 30-minute opening range (known at 10:00); first 5m close beyond either side,
    decisions 10:00-15:00; with the close; outcome to 15:55 in ATR_d vs time-matched."""
    p = spec["params"]
    avail = ctx.rth_start_min + 30
    hi = ctx.per_bar(ctx.or30["high"].to_numpy(float), avail)
    lo = ctx.per_bar(ctx.or30["low"].to_numpy(float), avail)
    ev = first_close_beyond(ctx, hi, lo, window=win(p), scan_from_min=avail)
    ct = tm_controls(ctx, "C018", ev, spec["control_draws"])
    ev, ct = finish(ctx, ev, ct)
    if len(ev):
        k = ev["day"].to_numpy()
        w_ = (ctx.or30["high"].to_numpy(float) - ctx.or30["low"].to_numpy(float)) / ctx.atr_d
        ev["or_width_tercile"] = tercile_labels(w_[k])
        gap = np.sign(ctx.rth_open - ctx.prev(ctx.rth_close))[k]
        ev["gap_agrees"] = np.where(gap == ev["direction"].to_numpy(), "yes", "no")
    return StudyData(ev, ct, diff_test(ev, ct, "reod", "C018 next bar->15:55 ATR_d: ORB30 - time-matched"),
                     hurdle_norm=ev["atr_d"].to_numpy(), conditions=("trend_tercile",))


STUDIES: dict[str, Callable[[StudyContext, dict], StudyData]] = {
    name.replace("study_", ""): fn for name, fn in list(globals().items()) if name.startswith("study_") and callable(fn)
}
