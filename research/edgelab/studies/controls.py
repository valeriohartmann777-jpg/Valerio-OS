"""Control groups for the pre-registered event studies.

* ``time_matched``: same wall-clock bar on 5 OTHER event days of the period, same
  direction (``edgelab.events.controls.time_matched_controls``).
* ``momentum_twin``: time-matched bars whose direction is the sign of the trailing
  ``lookback``-bar return (does the event beat plain momentum?).
* ``strength_twin``: time-matched bars with a bar body of the same sign and within
  +/-20% of the event bar's body in ATR (does the level beat a strong bar anywhere?).
* ``shifted_draws``: the randomized-level test. For every event day d, the levels of a
  randomly drawn other event day d' are re-anchored: level_d = anchor[d] +
  (level_d' - anchor[d']) * ATR_d[d] / ATR_d[d'], so their offset from the RTH open (or
  from the 10:30 price) is preserved in units of daily ATR, aligned by RTH slot; the
  identical detector is then rerun. Five draws per day. Adaptive levels that follow
  today's price (the nearest pivot zone) use ``StudyContext.pz_shift_levels`` instead:
  another day's nearest zones would sit far from today's path and would only be
  reached after large moves, a selection the real levels do not have.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..events.controls import time_matched_controls
from .context import StudyContext


def time_matched(ctx: StudyContext, events: pd.DataFrame, *, n: int = 5, seed: int = 0, eligible: np.ndarray | None = None) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame({"pos": pd.Series(dtype=np.int64), "direction": pd.Series(dtype=np.int64), "event_idx": pd.Series(dtype=np.int64)})
    ct = time_matched_controls(
        ctx.lab5, events[["pos", "direction"]].reset_index(drop=True), n_per_event=n, tolerance_minutes=0,
        bar_minutes=ctx.tf, eligible=ctx.elig5 if eligible is None else eligible, seed=seed,
    )
    ct["direction"] = ct["direction"].astype(np.int64)
    return ct


def momentum_twin(ctx: StudyContext, events: pd.DataFrame, *, lookback: int, n: int = 5, seed: int = 0) -> pd.DataFrame:
    """Time-matched bars with direction = sign(close[p] - close[p - lookback])."""
    ct = time_matched(ctx, events, n=n, seed=seed)
    if ct.empty:
        return ct
    p = ct["pos"].to_numpy()
    q = p - lookback
    d = np.where(q >= 0, np.sign(ctx.c[p] - ctx.c[np.clip(q, 0, None)]), 0)
    ct = ct.assign(direction=d.astype(np.int64))
    return ct[ct["direction"] != 0].reset_index(drop=True)


def strength_twin(ctx: StudyContext, events: pd.DataFrame, *, n: int = 5, tol: float = 0.20, seed: int = 0) -> pd.DataFrame:
    """Same-minute bars on other event days whose signed body/ATR is within ``tol`` (relative)
    of the event bar's and has the event's sign; direction = the body sign."""
    rng = np.random.default_rng(seed)
    body = (ctx.c - ctx.o) / ctx.atr5
    mod = ctx.mod5
    td = ctx.day5
    pool = np.flatnonzero(ctx.elig5 & np.isfinite(body))
    by_min = {m: pool[mod[pool] == m] for m in np.unique(mod[pool])}
    rows_p, rows_d, rows_e = [], [], []
    for k, (p, d) in enumerate(zip(events["pos"].to_numpy(), events["direction"].to_numpy())):
        b = body[p]
        cand = by_min.get(int(mod[p]))
        if cand is None or not np.isfinite(b) or np.sign(b) != d:
            continue
        cb = body[cand]
        sel = cand[(td[cand] != td[p]) & (np.sign(cb) == d) & (np.abs(cb - b) <= tol * abs(b))]
        if sel.size == 0:
            continue
        pick = rng.choice(sel, size=n, replace=sel.size < n)
        rows_p.extend(pick.tolist())
        rows_d.extend([int(d)] * n)
        rows_e.extend([k] * n)
    return pd.DataFrame({"pos": np.asarray(rows_p, dtype=np.int64), "direction": np.asarray(rows_d, dtype=np.int64), "event_idx": np.asarray(rows_e, dtype=np.int64)})


def partner_days(ctx: StudyContext, anchor: np.ndarray, *, n_draws: int = 5, seed: int = 0) -> np.ndarray:
    """(n_draws, n_days) partner day codes: for each event day a different random event day
    with a finite anchor; -1 where the day is not an event day."""
    rng = np.random.default_rng(seed)
    ok = np.flatnonzero(ctx.day_ok & np.isfinite(np.asarray(anchor, float)))
    out = np.full((n_draws, ctx.n_days), -1, dtype=np.int64)
    m = len(ok)
    if m < 2:
        return out
    for r in range(n_draws):
        u = rng.integers(0, m - 1, size=m)
        u = u + (u >= np.arange(m))
        out[r, ok] = ok[u]
    return out


def shifted_draws(
    ctx: StudyContext,
    arrays: dict[str, np.ndarray],
    anchor: np.ndarray,
    *,
    n_draws: int = 5,
    seed: int = 0,
) -> list[dict[str, np.ndarray]]:
    """Shifted-reference versions of per-bar (5m) level arrays, one dict per draw."""
    anchor = np.asarray(anchor, float)
    scale = np.asarray(ctx.atr_d, float)
    partners = partner_days(ctx, np.where(np.isfinite(scale) & (scale > 0), anchor, np.nan), n_draws=n_draws, seed=seed)
    rth = ctx.day5 >= 0
    d5, s5 = ctx.day5[rth], ctx.slot5[rth]
    mats = {}
    for name, arr in arrays.items():
        mat = np.full((ctx.n_days, ctx.n_slots5), np.nan)
        mat[d5, s5] = np.asarray(arr, float)[rth]
        mats[name] = mat
    out = []
    pos = np.flatnonzero(rth)
    for r in range(n_draws):
        kp = partners[r, d5]
        ok = kp >= 0
        draw = {}
        for name, mat in mats.items():
            vals = np.full(ctx.n5, np.nan)
            dd, pp = d5[ok], kp[ok]
            vals[pos[ok]] = anchor[dd] + (mat[pp, s5[ok]] - anchor[pp]) * (scale[dd] / scale[pp])
            draw[name] = vals
        out.append(draw)
    return out
