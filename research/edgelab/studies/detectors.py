"""Generic, causal event detectors on 5-minute RTH bars.

Every detector scans the RTH bars of each event day (``ctx.day_ok``) in time order
and emits an event at bar ``pos`` only from information available at that bar's
close. Levels are per-bar arrays (``NaN`` = not known yet), so the same detector
runs unchanged on real levels and on shifted-reference levels.

Shared conventions (frozen before data, see ``configs/event_studies.yaml``):

* ``window = (w0, w1)``: the event bar's CLOSE minute must lie in ``[w0, w1]``.
* ``side = +1``: a level ABOVE price (resistance; a sweep fades it short),
  ``side = -1``: a level BELOW price. ``direction`` is the expected move (+1 / -1).
* "First" means first in the session: once a level has produced its first trigger
  (penetration, breakout, ...) it is finished for that session, whether or not the
  trigger became an event inside the window.
* ``near_side_open``: the RTH open must lie on the near side of the level (below a
  level above), so a session that opens beyond the level does not create a crossing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .context import StudyContext

EVENT_COLUMNS = ["pos", "direction"]


def _frame(rows: list[dict], extra: tuple[str, ...] = ()) -> pd.DataFrame:
    cols = EVENT_COLUMNS + list(extra)
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype=float) for c in cols}).astype({"pos": np.int64, "direction": np.int64})
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df:
            df[c] = np.nan
    df["pos"] = df["pos"].astype(np.int64)
    df["direction"] = df["direction"].astype(np.int64)
    return df


def in_window(ctx: StudyContext, i: int, window: tuple[int, int]) -> bool:
    cm = ctx.close_min5[i]
    return window[0] <= cm <= window[1]


def day_iter(ctx: StudyContext, days: np.ndarray | None = None):
    s5, e5 = ctx.rth_bounds5
    for k in (ctx.ok_days() if days is None else days):
        if s5[k] >= 0:
            yield int(k), int(s5[k]), int(e5[k])


def _scan_start(ctx: StudyContext, s: int, e: int, scan_from_min: int | None) -> int:
    if scan_from_min is None:
        return s
    m = ctx.mod5[s:e] >= scan_from_min
    return s + int(np.argmax(m)) if m.any() else e


# ---------------------------------------------------------------------------------------
# sweep / failed penetration / failed breakout
# ---------------------------------------------------------------------------------------
def sweep_reclaim(
    ctx: StudyContext,
    level: np.ndarray,
    side: int,
    *,
    mode: str = "pen",
    pen_atr: float = 0.10,
    n_bars: int = 3,
    close_thr_atr: float = 0.0,
    min_closes: int = 2,
    max_bars_multi: int = 6,
    window: tuple[int, int] = (585, 900),
    scan_from_min: int | None = None,
    arm_inside: bool = False,
    near_side_open: bool = True,
    days: np.ndarray | None = None,
) -> pd.DataFrame:
    """Penetration of ``level`` followed by a close back on the near side.

    Modes (t0 = trigger bar, L0 = level at t0; "inside" = strictly on the near side):

    ``pen``    t0: first bar whose extreme exceeds L by ``pen_atr`` * ATR; event = first
               t in [t0, t0 + n_bars] closing inside.
    ``wick``   t0 as ``pen``; event only if t0 itself closes inside.
    ``close``  t0: first close beyond L by more than ``close_thr_atr`` * ATR; event = first
               t in (t0, t0 + n_bars] closing inside.
    ``multi``  t0: first close beyond L; event at the first later inside close k when
               k - t0 <= ``max_bars_multi`` and at least ``min_closes`` closes in [t0, k)
               were beyond L.

    ``arm_inside``: the trigger may only occur after a close inside the level (HA01:
    approach from inside value). Time-varying levels are keyed by value: each value
    gets one trigger per session.
    """
    if mode not in ("pen", "wick", "close", "multi"):
        raise ValueError(mode)
    h, l, c, a = ctx.h, ctx.l, ctx.c, ctx.atr5
    lv = np.asarray(level, float)
    rows: list[dict] = []
    for k, s, e in day_iter(ctx, days):
        i0 = _scan_start(ctx, s, e, scan_from_min)
        used: set = set()
        armed_for: set = set()
        i = i0
        while i < e:
            L, A = lv[i], a[i]
            if not (np.isfinite(L) and np.isfinite(A)) or L in used:
                i += 1
                continue
            if near_side_open and not (side * (L - ctx.rth_open[k]) > 0):
                used.add(L)
                i += 1
                continue
            if arm_inside and L not in armed_for:
                if side * (c[i] - L) <= 0:
                    armed_for.add(L)
                i += 1
                continue
            ext = h[i] if side > 0 else l[i]
            if mode in ("pen", "wick"):
                trig = side * (ext - L) > pen_atr * A
            elif mode == "close":
                trig = side * (c[i] - L) > close_thr_atr * A
            else:
                trig = side * (c[i] - L) > 0
            if not trig:
                i += 1
                continue
            t0, L0 = i, L
            used.add(L0)
            ev = -1
            if mode == "wick":
                ev = t0 if side * (c[t0] - L0) < 0 else -1
            elif mode == "pen":
                for t in range(t0, min(t0 + n_bars, e - 1) + 1):
                    if side * (c[t] - L0) < 0:
                        ev = t
                        break
            elif mode == "close":
                for t in range(t0 + 1, min(t0 + n_bars, e - 1) + 1):
                    if side * (c[t] - L0) < 0:
                        ev = t
                        break
            else:
                beyond = 0
                for t in range(t0, min(t0 + max_bars_multi, e - 1) + 1):
                    if side * (c[t] - L0) < 0:
                        ev = t if beyond >= min_closes else -1
                        break
                    if side * (c[t] - L0) > 0:
                        beyond += 1
            if ev >= 0 and in_window(ctx, ev, window):
                seg_ext = h[t0: ev + 1].max() if side > 0 else l[t0: ev + 1].min()
                rng_ = h[ev] - l[ev]
                clv = (c[ev] - l[ev]) / rng_ if rng_ > 0 else 0.5
                rows.append({
                    "pos": ev, "direction": -side, "day": k, "level": L0, "t0": t0,
                    "bars_beyond": ev - t0, "depth_atr": side * (seg_ext - L0) / a[ev],
                    "extreme": seg_ext, "clv_dir": clv if side < 0 else 1 - clv,
                })
            i = max(i + 1, ev + 1 if ev >= 0 else t0 + 1)
    return _frame(rows, ("day", "level", "t0", "bars_beyond", "depth_atr", "extreme", "clv_dir"))


# ---------------------------------------------------------------------------------------
# zone entry (touch) of support / resistance
# ---------------------------------------------------------------------------------------
def zone_entries(
    ctx: StudyContext,
    level: np.ndarray,
    side: int,
    *,
    zone_atr: float = 0.10,
    break_atr: float = 0.25,
    rearm_atr: float = 0.50,
    window: tuple[int, int] = (585, 900),
    days: np.ndarray | None = None,
) -> pd.DataFrame:
    """Entries into the zone around a level (HYPOTHESES_PRICE_ACTION "Zone entry").

    ``side = -1`` support (level below price, bounce = +1), ``side = +1`` resistance.
    A level is ARMED by a close beyond it on the correct side by more than ``zone_atr``.
    A touch is the first armed bar that trades into the zone (low <= L + zone for
    support) without closing beyond ``break_atr`` (a close beyond that disarms: the level
    broke). After a touch the level re-arms once a close is ``rearm_atr`` * ATR away.
    Touches are numbered per level value within the session; numbers count touches before
    the window too, but only touches inside the window are emitted.
    """
    h, l, c, a = ctx.h, ctx.l, ctx.c, ctx.atr5
    lv = np.asarray(level, float)
    sgn = -side  # +1 when price sits above (support)
    rows: list[dict] = []
    for k, s, e in day_iter(ctx, days):
        counts: dict = {}
        state, cur = "unarmed", np.nan
        for i in range(s, e):
            L, A = lv[i], a[i]
            if not (np.isfinite(L) and np.isfinite(A)):
                state, cur = "unarmed", np.nan
                continue
            if L != cur:
                state, cur = "unarmed", L
            zone = zone_atr * A
            d = sgn * (c[i] - L)
            if state == "unarmed":
                if d > zone:
                    state = "armed"
                continue
            if state == "armed":
                entered = (l[i] <= L + zone) if sgn > 0 else (h[i] >= L - zone)
                if not entered:
                    continue
                if d >= -break_atr * A:
                    counts[L] = counts.get(L, 0) + 1
                    state = "touched"
                    if in_window(ctx, i, window):
                        rows.append({"pos": i, "direction": sgn, "day": k, "level": L, "touch_number": counts[L]})
                else:
                    state = "unarmed"
                continue
            if d < -break_atr * A:
                state = "unarmed"
            elif d >= rearm_atr * A:
                state = "armed"
    return _frame(rows, ("day", "level", "touch_number"))


# ---------------------------------------------------------------------------------------
# breakouts, retests and runs
# ---------------------------------------------------------------------------------------
def breakouts(
    ctx: StudyContext,
    level: np.ndarray,
    side: int,
    *,
    variant: str = "atr",
    window: tuple[int, int] = (585, 900),
    near_side_open: bool = True,
    days: np.ndarray | None = None,
) -> pd.DataFrame:
    """First breakout of each level value per session (HYPOTHESES_PRICE_ACTION variants).

    ``side=+1``: upside break of a level above price (direction +1); ``-1`` mirrors.
    wick: extreme beyond L by more than 1 tick; close: close beyond by > 0.10 ATR;
    atr: close beyond by > 0.25 ATR; volume / body: the FIRST close breakout, kept only
    when its bar volume >= the 80th percentile of the same slot over the prior 20
    sessions (volume) or it is a displacement bar in the break direction (body).
    """
    if variant not in ("wick", "close", "atr", "volume", "body"):
        raise ValueError(variant)
    h, l, c, a, v = ctx.h, ctx.l, ctx.c, ctx.atr5, ctx.v
    tick = ctx.instrument.tick_size
    vp = ctx.vol5_p80 if variant == "volume" else None
    disp = None
    if variant == "body":
        cf = ctx.candles
        disp = (cf["disp_up"] if side > 0 else cf["disp_dn"]).to_numpy(bool)
    lv = np.asarray(level, float)
    rows: list[dict] = []
    for k, s, e in day_iter(ctx, days):
        done: set = set()
        for i in range(s, e):
            L, A = lv[i], a[i]
            if not (np.isfinite(L) and np.isfinite(A)) or L in done:
                continue
            if near_side_open and not (side * (L - ctx.rth_open[k]) > 0):
                done.add(L)
                continue
            if variant == "wick":
                ext = h[i] if side > 0 else l[i]
                trig = side * (ext - L) > tick
            elif variant == "atr":
                trig = side * (c[i] - L) > 0.25 * A
            else:
                trig = side * (c[i] - L) > 0.10 * A
            if not trig:
                continue
            done.add(L)
            keep = True
            if variant == "volume":
                keep = bool(np.isfinite(vp[i]) and v[i] >= vp[i])
            elif variant == "body":
                keep = bool(disp[i])
            if keep and in_window(ctx, i, window):
                rows.append({"pos": i, "direction": side, "day": k, "level": L,
                             "beyond_atr": side * (c[i] - L) / A, "body_atr": abs(c[i] - ctx.o[i]) / A})
    return _frame(rows, ("day", "level", "beyond_atr", "body_atr"))


def breakout_retests(
    ctx: StudyContext,
    brk: pd.DataFrame,
    *,
    max_bars: int = 24,
    zone_atr: float = 0.10,
    break_atr: float = 0.25,
    window: tuple[int, int] = (585, 900),
) -> pd.DataFrame:
    """First retest of a broken level within ``max_bars`` (HC06 polarity).

    After an upside break of L at bar b: first later bar k in (b, b + max_bars] of the same
    session with low <= L + zone and close >= L - break (direction +1). ``retested`` = False
    rows are kept with pos = -1 so the share of breakouts without a retest (missed trades)
    can be reported.
    """
    h, l, c, a = ctx.h, ctx.l, ctx.c, ctx.atr5
    sid = ctx.sid5
    rows = []
    for r in brk.itertuples(index=False):
        b, d, L = int(r.pos), int(r.direction), float(r.level)
        found = -1
        for k in range(b + 1, min(b + max_bars, ctx.n5 - 1) + 1):
            if sid[k] != sid[b]:
                break
            A = a[k]
            touched = (l[k] <= L + zone_atr * A) if d > 0 else (h[k] >= L - zone_atr * A)
            if not touched:
                continue
            holds = (c[k] >= L - break_atr * A) if d > 0 else (c[k] <= L + break_atr * A)
            found = k if holds else -1
            break
        ok = found >= 0 and in_window(ctx, found, window)
        rows.append({"pos": found if ok else -1, "direction": d, "day": r.day, "level": L, "break_pos": b, "retested": ok})
    return _frame(rows, ("day", "level", "break_pos", "retested"))


def level_runs(
    ctx: StudyContext,
    level: np.ndarray,
    side: int,
    *,
    thr_atr: float = 0.10,
    hold_bars: int = 3,
    window: tuple[int, int] = (585, 900),
    near_side_open: bool = True,
    days: np.ndarray | None = None,
) -> pd.DataFrame:
    """HB04 liquidity run: first close beyond L by ``thr_atr`` * ATR in the session, then the
    next ``hold_bars`` closes all beyond L; event at the last of them (direction = side)."""
    c, a = ctx.c, ctx.atr5
    lv = np.asarray(level, float)
    rows = []
    for k, s, e in day_iter(ctx, days):
        for i in range(s, e):
            L, A = lv[i], a[i]
            if not (np.isfinite(L) and np.isfinite(A)):
                continue
            if near_side_open and not (side * (L - ctx.rth_open[k]) > 0):
                break
            if side * (c[i] - L) <= thr_atr * A:
                continue
            j = i + hold_bars
            if j < e and all(side * (c[t] - L) > 0 for t in range(i + 1, j + 1)) and in_window(ctx, j, window):
                rows.append({"pos": j, "direction": side, "day": k, "level": L, "break_pos": i})
            break
    return _frame(rows, ("day", "level", "break_pos"))


def first_close_beyond(
    ctx: StudyContext,
    upper: np.ndarray,
    lower: np.ndarray,
    *,
    window: tuple[int, int],
    scan_from_min: int | None = None,
    per_side: bool = False,
    days: np.ndarray | None = None,
) -> pd.DataFrame:
    """First 5m close above ``upper`` (direction +1) or below ``lower`` (-1) in the session.

    With ``per_side`` each side yields its own first event; otherwise only the first of
    either side counts. Bars before ``scan_from_min`` are ignored; a first close outside
    the window ends the search for that side.
    """
    c = ctx.c
    up, dn = np.asarray(upper, float), np.asarray(lower, float)
    rows = []
    for k, s, e in day_iter(ctx, days):
        i0 = _scan_start(ctx, s, e, scan_from_min)
        open_sides = {1, -1}
        for i in range(i0, e):
            hit = []
            if 1 in open_sides and np.isfinite(up[i]) and c[i] > up[i]:
                hit.append(1)
            if -1 in open_sides and np.isfinite(dn[i]) and c[i] < dn[i]:
                hit.append(-1)
            for d in hit:
                if in_window(ctx, i, window):
                    rows.append({"pos": i, "direction": d, "day": k, "level": up[i] if d > 0 else dn[i]})
                open_sides.discard(d)
                if not per_side:
                    open_sides.clear()
            if not open_sides:
                break
    return _frame(rows, ("day", "level"))


def band_fades(
    ctx: StudyContext,
    center: np.ndarray,
    upper: np.ndarray,
    lower: np.ndarray,
    *,
    window: tuple[int, int],
    days: np.ndarray | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """HA10 events on any centre line with bands (VWAP +/-2 SD or the EMA twin).

    fade: first close above ``upper`` (direction -1) and first close below ``lower``
    (direction +1) per session within the window. reclaim: after a fade deviation, the
    first later close back across ``center`` in the window (direction = the crossing).
    Only closes inside the window are scanned (the bands are unstable earlier).
    """
    c = ctx.c
    ce, up, dn = (np.asarray(x, float) for x in (center, upper, lower))
    fades, reclaims = [], []
    for k, s, e in day_iter(ctx, days):
        seen = {}
        reclaimed = set()
        for i in range(s, e):
            if not in_window(ctx, i, window):
                continue
            if not (np.isfinite(ce[i]) and np.isfinite(up[i]) and np.isfinite(dn[i])):
                continue
            for side, beyond in ((1, c[i] > up[i]), (-1, c[i] < dn[i])):
                if beyond and side not in seen:
                    seen[side] = i
                    fades.append({"pos": i, "direction": -side, "day": k, "dev_atr": (c[i] - ce[i]) / ctx.atr5[i]})
            for side, t in seen.items():
                if side in reclaimed or i <= t:
                    continue
                crossed = c[i] < ce[i] if side > 0 else c[i] > ce[i]
                if crossed:
                    reclaimed.add(side)
                    reclaims.append({"pos": i, "direction": -side, "day": k, "fade_pos": t})
    return _frame(fades, ("day", "dev_atr")), _frame(reclaims, ("day", "fade_pos"))


def dedupe(events: pd.DataFrame) -> pd.DataFrame:
    """One event per (bar, direction): overlapping levels must not double-count a bar."""
    if events.empty:
        return events
    return events.sort_values(["pos", "direction"], kind="stable").drop_duplicates(["pos", "direction"]).reset_index(drop=True)
