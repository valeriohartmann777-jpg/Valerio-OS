"""Bar-based order simulator with conservative fills.

Timing model
------------
* A signal is known at ``decision_ts`` (the close of the strategy bar that produced it).
* The order goes live at the first base bar whose OPEN is >= ``decision_ts`` (plus an
  optional ``entry_delay_bars``). Nothing is filled at the signal bar's own prices.
* Base bars should be the finest data available (1 minute) so that most stop/target
  conflicts are resolved by bar order rather than by assumption.

Fill rules
----------
* market : open of the live bar, plus adverse slippage.
* limit  : fills at the limit only if price trades THROUGH it by ``limit_through_ticks``
           (a touch is not a fill); if the bar opens beyond the limit it fills at the open.
* stop   : triggers when price trades at the stop; a gap through fills at the open; plus
           adverse slippage.
* Protective stops are stop orders; profit targets are limit orders (trade-through rule).

Intrabar ambiguity (``ambiguity='conservative'``, the default for all primary results)
-------------------------------------------------------------------------------------
* Stop and target inside the same bar: the stop is assumed to fill first.
* Limit entry: a target touched on the fill bar is NOT credited (the high may have come
  before the fill). The stop on the fill bar is certain (price passed the limit first).
* Stop entry: a protective stop touched on the fill bar counts (fill then stop).
``optimistic`` flips each unknown ordering in the trader's favour, giving the best case.

Time exits are market orders at the open of the exit bar. If neither stop nor target nor
a time exit fires, the trade closes at the close of the last bar of the session.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..config import CostModel, InstrumentSpec, minutes_of, parse_hhmm
from .costs import fee_points, slippage_points

INF = np.iinfo(np.int64).max // 4
RESERVED = {
    "decision_ts", "direction", "entry_type", "entry_price", "stop_price", "target_price",
    "ttl_bars", "max_hold_bars", "signal_id",
}


@dataclass(frozen=True)
class ExecConfig:
    ambiguity: str = "conservative"          # or "optimistic"
    entry_delay_bars: int = 0                # extra BASE bars between signal and live order
    allow_overlap: bool = False              # False = one position/order at a time
    order_ttl_bars: int = 6                  # pending limit/stop orders expire after N base bars
    flat_time: str | None = None             # "15:55": close positions at that bar's open
    max_hold_bars: int | None = None         # base bars after the fill
    min_risk_ticks: float = 4.0              # skip trades with a stop closer than this
    cancel_if_stop_before_entry: bool = True
    cancel_if_target_before_entry: bool = True
    exit_on_session_end: bool = True         # never carry a position into the next trading date
    day_start: str = "18:00"                 # template trading-day start (for flat_time)


@dataclass
class SimResult:
    trades: pd.DataFrame
    cancelled: pd.DataFrame
    config: dict


def _ns(idx: pd.DatetimeIndex | pd.Series) -> np.ndarray:
    """int64 UTC nanoseconds of a tz-aware index/series."""
    di = pd.DatetimeIndex(idx)
    if di.tz is None:
        raise ValueError("tz-aware timestamps required")
    return di.tz_convert("UTC").tz_localize(None).as_unit("ns").to_numpy().view("i8")


def _first(mask: np.ndarray) -> int:
    return int(np.argmax(mask)) if mask.any() else INF


def _session_runs(codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For every bar: index of the first and last bar of its trading date (codes contiguous)."""
    n = len(codes)
    starts = np.r_[0, np.flatnonzero(codes[1:] != codes[:-1]) + 1]
    ends = np.r_[starts[1:] - 1, n - 1]
    run_id = np.repeat(np.arange(len(starts)), ends - starts + 1)
    return starts[run_id], ends[run_id]


def simulate(
    base: pd.DataFrame,
    labels: pd.DataFrame,
    signals: pd.DataFrame,
    inst: InstrumentSpec,
    cost: CostModel,
    cfg: ExecConfig = ExecConfig(),
) -> SimResult:
    """Simulate every signal; returns trades and cancelled signals with reasons.

    ``signals`` columns: decision_ts (tz-aware), direction (+1/-1), entry_type
    (market/limit/stop), stop_price, and optionally entry_price, target_price,
    ttl_bars, max_hold_bars, signal_id. Any other columns are copied to the trades.
    """
    if cfg.ambiguity not in ("conservative", "optimistic"):
        raise ValueError(cfg.ambiguity)
    o = base["open"].to_numpy(float)
    h = base["high"].to_numpy(float)
    l = base["low"].to_numpy(float)
    c = base["close"].to_numpy(float)
    n = len(o)
    ts_ns = _ns(base.index)
    codes = pd.factorize(labels["trading_date"].to_numpy())[0]
    if len(codes) != n:
        raise ValueError("labels must align with base bars")
    _, run_end = _session_runs(codes)
    is_rth = labels["is_rth"].to_numpy(bool)
    sess_min = labels["session_minute"].to_numpy()
    tdates = labels["trading_date"].to_numpy()
    buckets = labels["bucket"].astype(str).to_numpy() if "bucket" in labels else np.full(n, "")
    tick = inst.tick_size
    thr = cost.limit_through_ticks * tick
    optimistic = cfg.ambiguity == "optimistic"

    flat_pos = np.full(n, INF, dtype=np.int64)
    if cfg.flat_time:
        fsm = (minutes_of(parse_hhmm(cfg.flat_time)) - minutes_of(parse_hhmm(cfg.day_start))) % 1440
        starts = np.r_[0, np.flatnonzero(codes[1:] != codes[:-1]) + 1]
        ends = np.r_[starts[1:], n]
        for s, e in zip(starts, ends):
            hit = np.flatnonzero(sess_min[s:e] >= fsm)
            if hit.size:
                flat_pos[s:e] = s + hit[0]

    sig = signals.reset_index(drop=True)
    if len(sig) == 0:
        return SimResult(pd.DataFrame(), pd.DataFrame(columns=["signal_id", "reason"]), asdict(cfg))
    dec_ns = _ns(sig["decision_ts"])
    dirs = sig["direction"].to_numpy(float)
    etypes = sig["entry_type"].astype(str).to_numpy()
    eprice = sig["entry_price"].to_numpy(float) if "entry_price" in sig else np.full(len(sig), np.nan)
    sprice = sig["stop_price"].to_numpy(float)
    tprice = sig["target_price"].to_numpy(float) if "target_price" in sig else np.full(len(sig), np.nan)
    ttl = sig["ttl_bars"].to_numpy(float) if "ttl_bars" in sig else np.full(len(sig), np.nan)
    mhold = sig["max_hold_bars"].to_numpy(float) if "max_hold_bars" in sig else np.full(len(sig), np.nan)
    sid = sig["signal_id"].to_numpy() if "signal_id" in sig else np.arange(len(sig))
    meta_cols = [col for col in sig.columns if col not in RESERVED]

    trades, cancelled = [], []
    busy_until = -1
    order = np.argsort(dec_ns, kind="stable")

    def cancel(i: int, reason: str) -> None:
        cancelled.append({"signal_id": sid[i], "reason": reason, "decision_ts": sig.at[i, "decision_ts"]})

    for i in order:
        d = dirs[i]
        stop = sprice[i]
        tgt = tprice[i]
        has_tgt = np.isfinite(tgt)
        k0 = int(np.searchsorted(ts_ns, dec_ns[i], side="left"))
        k = k0 + int(cfg.entry_delay_bars)
        if d not in (1.0, -1.0) or not np.isfinite(stop):
            cancel(i, "bad_signal")
            continue
        if k >= n:
            cancel(i, "no_data")
            continue
        if not cfg.allow_overlap and k <= busy_until:
            cancel(i, "overlap")
            continue
        dec_bar = k0 - 1
        if cfg.exit_on_session_end and dec_bar >= 0 and codes[k] != codes[dec_bar]:
            cancel(i, "session_boundary")
            continue
        hard_end = int(run_end[k]) if cfg.exit_on_session_end else n - 1
        flat = int(flat_pos[k])
        if k >= flat:
            cancel(i, "after_flat_time")
            continue
        last_entry = min(hard_end, flat - 1)

        # ---------------- entry ----------------
        et = etypes[i]
        if et == "market":
            f, raw_in, in_order = k, o[k], "market"
        elif et in ("limit", "stop"):
            P = eprice[i]
            if not np.isfinite(P):
                cancel(i, "bad_signal")
                continue
            life = int(ttl[i]) if np.isfinite(ttl[i]) else cfg.order_ttl_bars
            e = min(last_entry, k + life - 1)
            if e < k:
                cancel(i, "expired")
                continue
            so, sh, sl = o[k:e + 1], h[k:e + 1], l[k:e + 1]
            if et == "limit":
                gap = so <= P if d > 0 else so >= P
                touch = sl <= P - thr if d > 0 else sh >= P + thr
            else:
                gap = so >= P if d > 0 else so <= P
                touch = sh >= P if d > 0 else sl <= P
            fill_mask = gap | touch
            i_fill = _first(fill_mask)
            i_inv = _first(sl <= stop if d > 0 else sh >= stop) if cfg.cancel_if_stop_before_entry else INF
            i_tg = _first(sh >= tgt if d > 0 else sl <= tgt) if (has_tgt and cfg.cancel_if_target_before_entry) else INF
            if i_fill == INF:
                cancel(i, "expired")
                busy_until = max(busy_until, e)
                continue
            if i_inv < i_fill:
                cancel(i, "invalidated_before_entry")
                busy_until = max(busy_until, k + i_inv)
                continue
            if i_tg < i_fill:
                cancel(i, "target_before_entry")
                busy_until = max(busy_until, k + i_tg)
                continue
            if et == "stop" and i_inv == i_fill and optimistic and not gap[i_fill]:
                cancel(i, "invalidated_before_entry")
                busy_until = max(busy_until, k + i_fill)
                continue
            f = k + i_fill
            raw_in = o[f] if gap[i_fill] else P
            in_order = et
        else:
            cancel(i, "bad_signal")
            continue

        risk = d * (raw_in - stop)
        if risk <= 0:
            cancel(i, "invalid_at_entry")
            busy_until = max(busy_until, f)
            continue
        if risk < cfg.min_risk_ticks * tick - 1e-9:
            cancel(i, "risk_too_small")
            busy_until = max(busy_until, f)
            continue
        slip_in = slippage_points(cost, tick, in_order, bool(is_rth[f]))

        # ---------------- exit ----------------
        time_exit = flat if flat <= hard_end else INF
        mh = mhold[i] if np.isfinite(mhold[i]) else cfg.max_hold_bars
        if mh:
            time_exit = min(time_exit, f + int(mh))
        last = min(hard_end, time_exit) if time_exit != INF else hard_end
        seg = slice(f, last + 1)
        if d > 0:
            s_hit = l[seg] <= stop
            s_gap = o[seg] <= stop
            t_hit = h[seg] >= tgt + thr if has_tgt else np.zeros(last - f + 1, bool)
            t_gap = o[seg] >= tgt if has_tgt else np.zeros(last - f + 1, bool)
        else:
            s_hit = h[seg] >= stop
            s_gap = o[seg] >= stop
            t_hit = l[seg] <= tgt - thr if has_tgt else np.zeros(last - f + 1, bool)
            t_gap = o[seg] <= tgt if has_tgt else np.zeros(last - f + 1, bool)
        s_gap[0] = False
        t_gap[0] = False
        if in_order == "limit" and not optimistic:
            t_hit[0] = False
        i_s, i_t = _first(s_hit), _first(t_hit)
        i_time = (time_exit - f) if (time_exit != INF and time_exit <= last) else INF
        ambiguous = False
        if i_time != INF and i_time <= min(i_s, i_t):
            j = f + i_time
            if i_s == i_time and s_gap[i_time]:
                reason, raw_out, out_order = "stop", o[j], "stop"
            elif i_t == i_time and t_gap[i_time]:
                reason, raw_out, out_order = "target", tgt, "limit"
            else:
                reason, raw_out, out_order = ("flat_time" if time_exit == flat else "max_hold"), o[j], "market"
        elif i_s == INF and i_t == INF:
            j = last
            reason = "session_end" if last == hard_end and cfg.exit_on_session_end else "end_of_data"
            raw_out, out_order = c[j], "market"
        else:
            if i_s < i_t:
                take_stop = True
            elif i_t < i_s:
                take_stop = False
            else:
                if s_gap[i_s]:
                    take_stop = True
                elif t_gap[i_t]:
                    take_stop = False
                else:
                    ambiguous = True
                    take_stop = not optimistic
            if take_stop:
                j = f + i_s
                reason, out_order = "stop", "stop"
                raw_out = o[j] if s_gap[i_s] else stop
            else:
                j = f + i_t
                reason, out_order, raw_out = "target", "limit", tgt
        slip_out = slippage_points(cost, tick, out_order, bool(is_rth[j]))
        fees = fee_points(cost, inst, raw_in, raw_out, in_order, out_order)
        gross = d * (raw_out - raw_in)
        net = gross - slip_in - slip_out - fees
        if j > f:
            hi_seg, lo_seg = max(h[f:j].max(), raw_out), min(l[f:j].min(), raw_out)
        else:
            hi_seg, lo_seg = max(raw_in, raw_out), min(raw_in, raw_out)
        mfe = (hi_seg - raw_in) if d > 0 else (raw_in - lo_seg)
        mae = (raw_in - lo_seg) if d > 0 else (hi_seg - raw_in)
        rec = {
            "signal_id": sid[i],
            "direction": int(d),
            "entry_type": et,
            "decision_ts": sig.at[i, "decision_ts"],
            "entry_ts": base.index[f],
            "exit_ts": base.index[j],
            "entry_raw": raw_in,
            "exit_raw": raw_out,
            "entry_price": raw_in + d * slip_in,
            "exit_price": raw_out - d * slip_out,
            "stop_price": stop,
            "target_price": tgt,
            "exit_reason": reason,
            "ambiguous": ambiguous,
            "risk_pts": risk,
            "gross_pts": gross,
            "slippage_pts": slip_in + slip_out,
            "fees_pts": fees,
            "net_pts": net,
            "R_gross": gross / risk,
            "R_net": net / risk,
            "mfe_pts": max(mfe, 0.0),
            "mae_pts": max(mae, 0.0),
            "mfe_R": max(mfe, 0.0) / risk,
            "mae_R": max(mae, 0.0) / risk,
            "bars_held": int(j - f),
            "duration_min": (ts_ns[j] - ts_ns[f]) / 6e10,
            "pnl_ccy": net * inst.point_value,
            "entry_is_rth": bool(is_rth[f]),
            "trading_date": tdates[f],
            "entry_bucket": buckets[f],
        }
        for col in meta_cols:
            rec[col] = sig.at[i, col]
        trades.append(rec)
        busy_until = max(busy_until, j)

    tr = pd.DataFrame(trades)
    if len(tr):
        tr = tr.sort_values("entry_ts", kind="stable").reset_index(drop=True)
    return SimResult(tr, pd.DataFrame(cancelled, columns=["signal_id", "reason", "decision_ts"]), {**asdict(cfg), "cost_model": cost.label})
