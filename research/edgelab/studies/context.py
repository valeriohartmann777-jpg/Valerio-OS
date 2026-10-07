"""Shared, causal feature context for the pre-registered event studies.

Everything a detector needs is computed once from 1-minute bars and cached here:
5-minute signal bars, ATR, session levels, prior value, overnight range, initial
balance, VWAP, swings, structure, pivot zones and volume baselines.

Conventions (see ``hypotheses/HYPOTHESES_AUCTION.md`` "Common definitions"):

* Bars are labelled by OPEN time. A feature of bar ``i`` is known at its close
  (``close_min5[i]`` = wall-clock minute of the close). Execution starts at the next
  bar's open.
* Per-day values are indexed by ``k`` = position of the trading date in ``days``
  (dates with RTH bars). ``day5[i]`` is ``k`` for RTH bars and -1 otherwise.
* Prior-session values (``prev_*``, ``pv_*``, ``atr_d``) of day ``k`` come from day
  ``k - 1`` and are known before the RTH open of ``k``. Same-day summaries (overnight
  range, IB, 30-minute OR) become usable only at their scheduled end (``per_bar``).
* ``day_ok[k]`` marks event days: in the research period, full RTH on ``k`` and on
  ``k - 1`` (the date that supplies the prior levels), neither an early close, not
  excluded (rolls), and at most 5 calendar days after ``k - 1``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

import numpy as np
import pandas as pd

from ..config import InstrumentSpec, SessionTemplate, minutes_of
from ..features.candles import candle_features
from ..features.levels import equal_levels
from ..features.profile import session_profiles
from ..features.regime import daily_regimes
from ..features.sr import pivot_zones
from ..features.structure import structure_breaks, structure_trend
from ..features.swings import find_pivots, swing_state
from ..features.volatility import atr
from ..features.vwap import session_vwap
from ..resample import resample_bars, session_bars, weekly_bars
from ..sessions import label_sessions, session_calendar
from ..timing import as_ns, attach_asof

DEFAULTS: dict = {
    "bar_minutes": 5,
    "atr_n": 14,
    "pivots_ict": [3, 3],
    "pivots_pa": [6, 6],
    "pz_merge_atr": 0.25,
    "pz_max_age_bars": 1380,
    "pz_shift_draws": 5,
    "pz_shift_atr": [0.5, 1.5],
    "equal_tol_atr": 0.10,
    "equal_min_sep": 5,
    "equal_max_sep": 300,
    "equal_min_reaction_atr": 1.0,
    "value_area": {"bin_ticks": 1, "method": "uniform", "va_pct": 0.70, "va_method": "single"},
    "volume_lookback_sessions": 20,
    "body_median_bars": 100,
    "displacement_body_mult": 1.5,
    "displacement_clv": 0.75,
    "ema_twin_span": 20,
    "ema_twin_sd_window": 20,
    "max_day_gap_days": 5,
}


def _rolling_by_slot(mat: np.ndarray, window: int, min_periods: int, how: str, q: float = 0.5) -> np.ndarray:
    """Rolling statistic over PRIOR days (row shift by one) for every intraday slot (column)."""
    df = pd.DataFrame(mat)
    r = df.rolling(window, min_periods=min_periods)
    out = r.median() if how == "median" else r.quantile(q)
    return out.shift(1).to_numpy()


@dataclass
class StudyContext:
    """Cached causal features for one instrument and one research period.

    ``provenance`` is ``"manifest:<dataset_id>:<sha16>"`` for real data registered in
    ``configs/datasets.yaml`` and ``"synthetic-test"`` for software tests. The runner
    refuses to write repository results for anything that is not a manifest dataset.
    """

    bars1: pd.DataFrame
    instrument: InstrumentSpec
    template: SessionTemplate
    provenance: str
    period_dates: pd.DatetimeIndex | None = None
    excluded_dates: frozenset = frozenset()
    params: dict = field(default_factory=dict)
    seed: int = 20261007

    def p(self, key: str):
        return self.params.get(key, DEFAULTS[key])

    @property
    def is_real(self) -> bool:
        return self.provenance.startswith("manifest:")

    # ------------------------------------------------------------------ clocks
    @cached_property
    def rth_start_min(self) -> int:
        return minutes_of(self.template.rth_start)

    @cached_property
    def rth_end_min(self) -> int:
        return minutes_of(self.template.rth_end)

    @cached_property
    def lab1(self) -> pd.DataFrame:
        return label_sessions(self.bars1.index, self.template)

    @cached_property
    def tf(self) -> int:
        return int(self.p("bar_minutes"))

    @cached_property
    def b5(self) -> pd.DataFrame:
        return resample_bars(self.bars1, self.tf, tz=self.template.timezone, anchor="18:00")

    @cached_property
    def lab5(self) -> pd.DataFrame:
        return label_sessions(self.b5.index, self.template)

    @cached_property
    def n5(self) -> int:
        return len(self.b5)

    @cached_property
    def o(self) -> np.ndarray:
        return self.b5["open"].to_numpy(float)

    @cached_property
    def h(self) -> np.ndarray:
        return self.b5["high"].to_numpy(float)

    @cached_property
    def l(self) -> np.ndarray:  # noqa: E743
        return self.b5["low"].to_numpy(float)

    @cached_property
    def c(self) -> np.ndarray:
        return self.b5["close"].to_numpy(float)

    @cached_property
    def v(self) -> np.ndarray:
        return self.b5["volume"].to_numpy(float)

    @cached_property
    def mod5(self) -> np.ndarray:
        return self.lab5["minute_of_day"].to_numpy(np.int64)

    @cached_property
    def close_min5(self) -> np.ndarray:
        """Wall-clock minute at which each 5m bar closes (the decision time)."""
        return self.mod5 + self.tf

    @cached_property
    def is_rth5(self) -> np.ndarray:
        return self.lab5["is_rth"].to_numpy(bool)

    @cached_property
    def atr5_series(self) -> pd.Series:
        return atr(self.b5, int(self.p("atr_n")))

    @cached_property
    def atr5(self) -> np.ndarray:
        return self.atr5_series.to_numpy(float)

    # ------------------------------------------------------------------ days
    @cached_property
    def rth(self) -> pd.DataFrame:
        """One row per trading date with RTH bars (1m based): open/high/low/close/volume."""
        return session_bars(self.bars1, self.lab1, self.template, "RTH")

    @cached_property
    def days(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.rth.index)

    @cached_property
    def n_days(self) -> int:
        return len(self.days)

    @cached_property
    def day5(self) -> np.ndarray:
        code = self.days.get_indexer(pd.DatetimeIndex(self.lab5["trading_date"].to_numpy()))
        return np.where(self.is_rth5, code, -1).astype(np.int64)

    @cached_property
    def slot5(self) -> np.ndarray:
        s = (self.mod5 - self.rth_start_min) // self.tf
        return np.where(self.day5 >= 0, s, -1).astype(np.int64)

    @cached_property
    def n_slots5(self) -> int:
        return (self.rth_end_min - self.rth_start_min) // self.tf

    @cached_property
    def rth_bounds5(self) -> tuple[np.ndarray, np.ndarray]:
        """(start, end) 5m positions of each day's RTH bars (end exclusive; -1 if none)."""
        s = np.full(self.n_days, -1, dtype=np.int64)
        e = np.full(self.n_days, -1, dtype=np.int64)
        pos = np.flatnonzero(self.day5 >= 0)
        if pos.size:
            d = self.day5[pos]
            first = np.r_[True, d[1:] != d[:-1]]
            last = np.r_[d[1:] != d[:-1], True]
            s[d[first]] = pos[first]
            e[d[last]] = pos[last] + 1
        return s, e

    @cached_property
    def calendar(self) -> pd.DataFrame:
        return session_calendar(self.lab1, 1, self.template).reindex(self.days)

    @cached_property
    def day_ok(self) -> np.ndarray:
        cal = self.calendar
        full = (cal["full_rth"].fillna(False).astype(bool) & ~cal["early_close"].fillna(True).astype(bool)).to_numpy()
        excl = self.days.isin(pd.DatetimeIndex(sorted(self.excluded_dates))) if self.excluded_dates else np.zeros(self.n_days, bool)
        period = np.ones(self.n_days, bool) if self.period_dates is None else self.days.isin(pd.DatetimeIndex(self.period_dates))
        ok = np.zeros(self.n_days, bool)
        if self.n_days > 1:
            gap = (self.days[1:] - self.days[:-1]).days.to_numpy() <= int(self.p("max_day_gap_days"))
            ok[1:] = full[1:] & full[:-1] & ~excl[1:] & ~excl[:-1] & gap
        s, _ = self.rth_bounds5
        return ok & period & (s >= 0) & np.isfinite(self.rth_open) & np.isfinite(self.atr_d)

    @cached_property
    def elig5(self) -> np.ndarray:
        """5m RTH bars of event days with a finite ATR (the control pool)."""
        d = self.day5
        ok = np.zeros(self.n5, bool)
        m = d >= 0
        ok[m] = self.day_ok[d[m]]
        return ok & np.isfinite(self.atr5)

    @cached_property
    def sid5(self) -> np.ndarray:
        """Session id for outcome horizons: the day code inside RTH, unique negatives outside."""
        return np.where(self.day5 >= 0, self.day5, -1 - np.arange(self.n5))

    @cached_property
    def sid5_to1555(self) -> np.ndarray:
        """Like ``sid5`` but the 15:55 bar is cut off, so horizons end with the 15:50 bar (close 15:55)."""
        cut = (self.day5 < 0) | (self.close_min5 > self.rth_end_min - self.tf)
        return np.where(cut, -1 - np.arange(self.n5), self.day5)

    @cached_property
    def eod_pos(self) -> np.ndarray:
        """Per day: position of the 5m bar that closes at 15:55 (-1 if missing)."""
        out = np.full(self.n_days, -1, dtype=np.int64)
        m = (self.day5 >= 0) & (self.close_min5 == self.rth_end_min - self.tf)
        out[self.day5[m]] = np.flatnonzero(m)
        return out

    def slot_pos(self, slot: int) -> np.ndarray:
        """Per day: 5m position of RTH slot ``slot`` (0 = 09:30 bar), -1 if missing."""
        out = np.full(self.n_days, -1, dtype=np.int64)
        m = self.slot5 == slot
        out[self.day5[m]] = np.flatnonzero(m)
        return out

    def minute_slot(self, hhmm: str) -> int:
        hh, mm = hhmm.split(":")
        return (int(hh) * 60 + int(mm) - self.rth_start_min) // self.tf

    def per_bar(self, day_values: np.ndarray, avail_close_min: int | None = None) -> np.ndarray:
        """Map per-day values to RTH 5m bars whose close is at or after ``avail_close_min``."""
        out = np.full(self.n5, np.nan)
        m = self.day5 >= 0
        if avail_close_min is not None:
            m &= self.close_min5 >= avail_close_min
        out[m] = np.asarray(day_values, float)[self.day5[m]]
        return out

    # ------------------------------------------------------------------ daily values
    @cached_property
    def rth_open(self) -> np.ndarray:
        """Open of the 09:30 5m bar (NaN when that bar is missing)."""
        out = np.full(self.n_days, np.nan)
        p0 = self.slot_pos(0)
        out[p0 >= 0] = self.o[p0[p0 >= 0]]
        return out

    @cached_property
    def rth_high(self) -> np.ndarray:
        return self.rth["high"].to_numpy(float)

    @cached_property
    def rth_low(self) -> np.ndarray:
        return self.rth["low"].to_numpy(float)

    @cached_property
    def rth_close(self) -> np.ndarray:
        return self.rth["close"].to_numpy(float)

    def prev(self, x: np.ndarray) -> np.ndarray:
        """Value of the previous trading date (day k-1) for each day k."""
        x = np.asarray(x, float)
        return np.r_[np.nan, x[:-1]] if len(x) else x

    @cached_property
    def regimes(self) -> pd.DataFrame:
        return daily_regimes(self.rth)

    @cached_property
    def atr_d(self) -> np.ndarray:
        """ATR(14) of completed daily RTH bars, known at the open (through day k-1)."""
        return self.prev(self.regimes["atr_d"].to_numpy(float))

    @cached_property
    def vol_regime(self) -> np.ndarray:
        return self.prev(self.regimes["vol_regime"].to_numpy(float))

    @cached_property
    def trend_regime(self) -> np.ndarray:
        return self.prev(self.regimes["trend_regime"].to_numpy(float))

    @cached_property
    def profiles(self) -> pd.DataFrame:
        va = {**DEFAULTS["value_area"], **self.params.get("value_area", {})}
        prof = session_profiles(
            self.bars1, self.lab1, tick_size=self.instrument.tick_size, template=self.template, phase="RTH",
            bin_ticks=int(va["bin_ticks"]), method=str(va["method"]), va_pct=float(va["va_pct"]),
            va_method=str(va["va_method"]), with_shape=True,
        )
        return prof.reindex(self.days)

    @cached_property
    def pv_poc(self) -> np.ndarray:
        return self.prev(self.profiles["poc"].to_numpy(float))

    @cached_property
    def pv_val(self) -> np.ndarray:
        return self.prev(self.profiles["val"].to_numpy(float))

    @cached_property
    def pv_vah(self) -> np.ndarray:
        return self.prev(self.profiles["vah"].to_numpy(float))

    @cached_property
    def overnight(self) -> pd.DataFrame:
        return session_bars(self.bars1, self.lab1, self.template, "ON").reindex(self.days)

    @cached_property
    def on_high(self) -> np.ndarray:
        return self.overnight["high"].to_numpy(float)

    @cached_property
    def on_low(self) -> np.ndarray:
        return self.overnight["low"].to_numpy(float)

    def _opening_window(self, minutes: int) -> pd.DataFrame:
        lab = self.lab1
        m = (lab["is_rth"] & (lab["minute_of_day"] < self.rth_start_min + minutes)).to_numpy()
        b = self.bars1[m]
        g = b.groupby(lab.loc[m, "trading_date"].to_numpy())
        tbl = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "n": g.size()})
        tbl.index = pd.DatetimeIndex(tbl.index)
        return tbl.reindex(self.days)

    @cached_property
    def ib(self) -> pd.DataFrame:
        """Initial balance 09:30-10:30 (usable from the close of the bar ending 10:30)."""
        return self._opening_window(int(self.template.initial_balance_minutes))

    @cached_property
    def or30(self) -> pd.DataFrame:
        """Opening range 09:30-10:00 (usable from 10:00)."""
        return self._opening_window(30)

    @cached_property
    def prior_week(self) -> pd.DataFrame:
        """High/low of the previous completed Monday-Friday week of RTH sessions."""
        wk = weekly_bars(self.rth)
        out = pd.DataFrame(index=self.days, columns=["pw_high", "pw_low"], dtype=float)
        if wk.empty:
            return out
        wk_ord = wk.index.asi8
        d_ord = self.days.to_period("W-FRI").asi8
        k = np.searchsorted(wk_ord, d_ord, side="left") - 1
        ok = k >= 0
        kk = np.clip(k, 0, len(wk) - 1)
        out["pw_high"] = np.where(ok, wk["high"].to_numpy(float)[kk], np.nan)
        out["pw_low"] = np.where(ok, wk["low"].to_numpy(float)[kk], np.nan)
        return out

    # ------------------------------------------------------------------ intraday features
    @cached_property
    def candles(self) -> pd.DataFrame:
        cf = candle_features(self.b5, self.atr5_series)
        n = int(self.p("body_median_bars"))
        cf["med_body"] = cf["abs_body"].rolling(n, min_periods=n).median().shift(1)
        mult, q = float(self.p("displacement_body_mult")), float(self.p("displacement_clv"))
        big = cf["abs_body"] >= mult * cf["med_body"]
        cf["disp_up"] = (big & (cf["body"] > 0) & (cf["clv"] >= q)).fillna(False)
        cf["disp_dn"] = (big & (cf["body"] < 0) & (cf["clv"] <= 1 - q)).fillna(False)
        return cf

    @cached_property
    def vwap5(self) -> pd.DataFrame:
        """RTH VWAP (anchored 09:30) and volume-weighted SD bands from 1m bars, read at each 5m close."""
        lab = self.lab1
        vw = session_vwap(self.bars1, lab["trading_date"].where(lab["is_rth"]))
        vw["available_at"] = as_ns(self.bars1.index + pd.Timedelta(minutes=1))
        cols = ["vwap", "vwap_sd", "vwap_up_2", "vwap_dn_2"]
        return attach_asof(self.b5.index, self.tf, vw, cols)

    @cached_property
    def ema_twin(self) -> pd.DataFrame:
        """EMA(20) of 5m closes with +/-2 rolling SD(20) of the close-minus-EMA residual."""
        c = self.b5["close"]
        ema = c.ewm(span=int(self.p("ema_twin_span")), adjust=False).mean()
        w = int(self.p("ema_twin_sd_window"))
        sd = (c - ema).rolling(w, min_periods=w).std(ddof=0)
        return pd.DataFrame({"ema": ema, "ema_up_2": ema + 2 * sd, "ema_dn_2": ema - 2 * sd}, index=self.b5.index)

    @cached_property
    def pivots_ict(self) -> pd.DataFrame:
        left, right = self.p("pivots_ict")
        return find_pivots(self.b5, int(left), int(right))

    @cached_property
    def swings_ict(self) -> pd.DataFrame:
        return swing_state(self.n5, self.pivots_ict, self.b5.index)

    @cached_property
    def breaks_ict(self) -> pd.DataFrame:
        return structure_breaks(self.b5, self.pivots_ict, "close")

    @cached_property
    def trend_ict(self) -> np.ndarray:
        return structure_trend(self.n5, self.breaks_ict)

    @cached_property
    def equal_highs(self) -> pd.DataFrame:
        return self._equal("H")

    @cached_property
    def equal_lows(self) -> pd.DataFrame:
        return self._equal("L")

    def _equal(self, kind: str) -> pd.DataFrame:
        return equal_levels(
            self.b5, self.pivots_ict, self.atr5_series, kind=kind, tol_atr=float(self.p("equal_tol_atr")),
            min_sep_bars=int(self.p("equal_min_sep")), max_sep_bars=int(self.p("equal_max_sep")),
            min_reaction_atr=float(self.p("equal_min_reaction_atr")),
        )

    @cached_property
    def pivots_pa(self) -> pd.DataFrame:
        left, right = self.p("pivots_pa")
        return find_pivots(self.b5, int(left), int(right))

    @cached_property
    def pz_shift_offsets(self) -> np.ndarray:
        """(draws, n5) displacement of the zone set for the randomized-level control of
        pivot zones: per draw and trading date a random sign times U(lo, hi) x the 5m ATR
        of the bar before the RTH open; NaN on dates without an RTH open."""
        lo, hi = (float(x) for x in self.p("pz_shift_atr"))
        r = int(self.p("pz_shift_draws"))
        rng = np.random.default_rng(self.seed + 7)
        p0 = self.slot_pos(0)
        a_open = np.where(p0 >= 1, self.atr5[np.clip(p0 - 1, 0, None)], np.nan)
        mag = rng.uniform(lo, hi, size=(r, self.n_days)) * rng.choice([-1.0, 1.0], size=(r, self.n_days))
        per_day = mag * a_open[None, :]
        code = self.trading_date_code5
        out = np.full((r, self.n5), np.nan)
        ok = code >= 0
        out[:, ok] = per_day[:, code[ok]]
        return out

    @cached_property
    def pivot_zone_state(self) -> pd.DataFrame:
        state, _ = pivot_zones(
            self.b5, self.pivots_pa, self.atr5_series,
            merge_tol_atr=float(self.p("pz_merge_atr")), max_age_bars=int(self.p("pz_max_age_bars")),
            query_offsets=self.pz_shift_offsets,
        )
        return state

    @cached_property
    def pz_levels(self) -> dict[str, np.ndarray]:
        """Nearest pivot-zone support/resistance as known at the PREVIOUS bar's close.

        A touch or breakout at bar i must be measured against a level that existed before
        bar i traded, so the zone state of bar i-1 (zones confirmed by then, nearest to
        close[i-1]) is used. ``*_first_pos`` is the first pivot of the zone (its age).
        """
        st = self.pivot_zone_state
        out = {}
        for side in ("sup", "res"):
            cen = st[f"{side}_center"].to_numpy(float)
            age = st[f"{side}_age"].to_numpy(float)
            first = np.arange(self.n5) - age
            out[side] = np.r_[np.nan, cen[:-1]]
            out[f"{side}_first_pos"] = np.r_[np.nan, first[:-1]]
        return out

    @cached_property
    def pz_shift_levels(self) -> list[dict[str, np.ndarray]]:
        """Randomized-level control for pivot zones, one dict per draw: the nearest zone of
        the DISPLACED zone set above / below the previous close (same lag as ``pz_levels``),
        RTH bars only. Zone density, spacing and update times stay those of the real zones,
        the levels stay adjacent to today's path, only their exact location is random."""
        st = self.pivot_zone_state
        rth = self.day5 >= 0
        out = []
        for r in range(int(self.p("pz_shift_draws"))):
            d = {}
            for side in ("sup", "res"):
                lv = np.r_[np.nan, st[f"{side}_center_q{r}"].to_numpy(float)[:-1]]
                d[side] = np.where(rth, lv, np.nan)
            out.append(d)
        return out

    @cached_property
    def trading_date_code5(self) -> np.ndarray:
        """Day code of every 5m bar's trading date (also outside RTH; -1 if the date has no RTH)."""
        return self.days.get_indexer(pd.DatetimeIndex(self.lab5["trading_date"].to_numpy())).astype(np.int64)

    @cached_property
    def vol5_p80(self) -> np.ndarray:
        """80th percentile of the 5m volume at the same RTH slot over the prior 20 sessions."""
        return self._slot_baseline(self.v, self.day5, self.slot5, self.n_slots5, "quantile", 0.80)

    def _slot_baseline(self, vol: np.ndarray, day: np.ndarray, slot: np.ndarray, n_slots: int, how: str, q: float = 0.5) -> np.ndarray:
        m = (day >= 0) & (slot >= 0) & (slot < n_slots)
        mat = np.full((self.n_days, n_slots), np.nan)
        mat[day[m], slot[m]] = vol[m]
        lb = int(self.p("volume_lookback_sessions"))
        base = _rolling_by_slot(mat, lb, max(2, lb // 2), how, q)
        out = np.full(len(vol), np.nan)
        out[m] = base[day[m], slot[m]]
        return out

    # ------------------------------------------------------------------ 1m view (B001)
    @cached_property
    def day1(self) -> np.ndarray:
        code = self.days.get_indexer(pd.DatetimeIndex(self.lab1["trading_date"].to_numpy()))
        return np.where(self.lab1["is_rth"].to_numpy(bool), code, -1).astype(np.int64)

    @cached_property
    def slot1(self) -> np.ndarray:
        s = self.lab1["minute_of_day"].to_numpy(np.int64) - self.rth_start_min
        return np.where(self.day1 >= 0, s, -1)

    @cached_property
    def atr1(self) -> np.ndarray:
        return atr(self.bars1, int(self.p("atr_n"))).to_numpy(float)

    @cached_property
    def vol1_med20(self) -> np.ndarray:
        """Median 1m volume of the same RTH minute over the prior 20 sessions."""
        return self._slot_baseline(self.bars1["volume"].to_numpy(float), self.day1, self.slot1, self.rth_end_min - self.rth_start_min, "median")

    @cached_property
    def rth_bounds1(self) -> tuple[np.ndarray, np.ndarray]:
        s = np.full(self.n_days, -1, dtype=np.int64)
        e = np.full(self.n_days, -1, dtype=np.int64)
        pos = np.flatnonzero(self.day1 >= 0)
        if pos.size:
            d = self.day1[pos]
            first = np.r_[True, d[1:] != d[:-1]]
            last = np.r_[d[1:] != d[:-1], True]
            s[d[first]] = pos[first]
            e[d[last]] = pos[last] + 1
        return s, e

    # ------------------------------------------------------------------ helpers
    def ok_days(self) -> np.ndarray:
        return np.flatnonzero(self.day_ok)

    def date_of_pos(self, pos: np.ndarray) -> np.ndarray:
        """Trading date (datetime64) of 5m positions (the bootstrap cluster key)."""
        return self.days.to_numpy()[self.day5[np.asarray(pos, dtype=np.int64)]]

    def cost_points(self, price: np.ndarray | float | None = None) -> np.ndarray | float:
        """Round-trip BASE cost in points: market entry + stop exit slippage + commission,
        plus the taker fee on both sides when the venue charges basis points (crypto proxy;
        then ``price`` is required)."""
        from ..config import load_cost_model

        cm = load_cost_model(self.instrument.symbol, "BASE")
        slip = (cm.slippage_ticks_market + cm.slippage_ticks_stop) * self.instrument.tick_size * cm.slippage_mult
        fixed = slip + cm.commission_rt / self.instrument.point_value
        if cm.fee_bps_taker == 0:
            return float(fixed) if price is None else np.full(np.shape(price), float(fixed))
        if price is None:
            raise ValueError(f"{self.instrument.symbol} charges {cm.fee_bps_taker} bps per side: pass the entry price")
        return fixed + 2 * cm.fee_bps_taker * 1e-4 * np.asarray(price, float)
