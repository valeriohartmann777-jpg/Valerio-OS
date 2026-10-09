"""Instrument definitions, outright filtering and the frozen calendar roll.

A parent request (``ES.FUT``) returns every instrument of the product: outright futures
of all listed expiries, calendar spreads and possibly other instrument types. Research
uses exactly one outright contract per CME trading date, chosen by the roll rule frozen
in ``configs/instruments.yaml``: quarterly expiries on the third Friday, roll
``roll_offset_days`` (8) calendar days before expiry. The contract with the nearest
expiry whose roll date lies AFTER the trading date is active; from the roll date on,
the next one is. The choice uses only the calendar and contract attributes known at
listing (expiry, listing time), never volume, so later trading cannot change an earlier
choice. Volume is used only to report how the frozen rule compares with liquidity.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..config import SessionTemplate, minutes_of
from .rolls import third_friday

MONTH_CODES = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6, "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}
UNDEF_TS = np.iinfo(np.uint64).max  # Databento's "undefined" timestamp
DEF_COLUMNS = [
    "ts_recv", "instrument_id", "raw_symbol", "instrument_class", "security_type", "asset", "expiration",
    "activation", "min_price_increment", "user_defined_instrument", "security_update_action", "exchange", "cfi",
]


def read_definitions(paths: Iterable[Path]) -> pd.DataFrame:
    """All definition records of the given DBN files (prices in fixed-point 1e-9 units)."""
    import databento as db

    frames = []
    for p in paths:
        df = db.DBNStore.from_file(p).to_df(price_type="fixed", pretty_ts=False, map_symbols=False)
        if len(df):
            df = df.reset_index()
            frames.append(df[[c for c in DEF_COLUMNS if c in df.columns]])
    if not frames:
        return pd.DataFrame(columns=DEF_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def _ts(values: pd.Series) -> pd.Series:
    """uint64 ns -> tz-aware UTC timestamps, Databento's undefined value -> NaT."""
    raw = values.to_numpy(dtype="uint64")
    undef = raw == UNDEF_TS
    out = pd.Series(pd.to_datetime(np.where(undef, 0, raw).astype("int64"), unit="ns", utc=True), index=values.index)
    return out.mask(undef)


def contract_table(defs: pd.DataFrame, *, root: str, months: Iterable[int], tick_size: float,
                   roll_offset_days: int, tz: str = "America/New_York") -> pd.DataFrame:
    """One row per instrument_id (its latest definition) with the outright decision.

    Expiry and listing time of a listed future do not change; ``n_expirations`` counts the
    distinct expirations an instrument's definitions carried, and the validation stops
    when an outright's expiration changed (the calendar roll would then depend on a
    definition published later).

    An instrument is an outright of the product when its class is FUTURE (``F``), its
    security type ``FUT``, its asset the product root, it is not user-defined, its raw
    symbol is ``<root><month code><year digit(s)>`` and the month is in the product's
    expiry cycle. Everything else is kept in the table with the reason it is excluded.
    """
    cols = ["instrument_id", "raw_symbol", "instrument_class", "security_type", "asset", "expiration", "activation",
            "tick_size", "n_definitions", "n_raw_symbols", "n_expirations", "is_outright", "exclusion", "expiry_date",
            "rule_expiry", "expiry_matches_rule", "roll_date"]
    if defs.empty:
        return pd.DataFrame(columns=cols)
    d = defs.sort_values(["instrument_id", "ts_recv"], kind="stable")
    n_defs = d.groupby("instrument_id").size()
    n_syms = d.groupby("instrument_id")["raw_symbol"].nunique()
    n_exp = d.groupby("instrument_id")["expiration"].nunique()
    last = d.groupby("instrument_id", sort=True).tail(1).set_index("instrument_id")
    out = pd.DataFrame(index=last.index)
    out["raw_symbol"] = last["raw_symbol"].astype(str)
    out["instrument_class"] = last["instrument_class"].astype(str)
    out["security_type"] = last["security_type"].astype(str)
    out["asset"] = last["asset"].astype(str)
    out["expiration"] = _ts(last["expiration"])
    out["activation"] = _ts(last["activation"])
    out["tick_size"] = last["min_price_increment"].astype("int64") / 1e9
    out["n_definitions"] = n_defs.reindex(out.index).astype(int)
    out["n_raw_symbols"] = n_syms.reindex(out.index).astype(int)
    out["n_expirations"] = n_exp.reindex(out.index).astype(int)

    months = set(int(m) for m in months)
    pattern = re.compile(rf"^{re.escape(root)}([FGHJKMNQUVXZ])(\d{{1,2}})$")
    excl = []
    for row in out.itertuples():
        m = pattern.match(row.raw_symbol)
        reason = None
        if row.instrument_class != "F":
            reason = {"S": "future spread", "C": "option", "P": "option", "M": "mixed spread",
                      "T": "option spread"}.get(row.instrument_class, f"instrument class {row.instrument_class!r}")
        elif row.security_type != "FUT":
            reason = f"security type {row.security_type!r}"
        elif row.asset != root:
            reason = f"asset {row.asset!r} is not {root}"
        elif last.loc[row.Index, "user_defined_instrument"] not in ("N", "", None):
            reason = "user-defined instrument"
        elif not m:
            reason = "raw symbol does not match the outright pattern"
        elif MONTH_CODES[m.group(1)] not in months:
            reason = f"month {m.group(1)} is outside the expiry cycle"
        elif pd.isna(row.expiration):
            reason = "no expiration in the definition"
        elif not np.isclose(row.tick_size, tick_size):
            reason = f"tick size {row.tick_size} differs from instruments.yaml ({tick_size})"
        excl.append(reason)
    out["exclusion"] = excl
    out["is_outright"] = out["exclusion"].isna()

    exp_local = out["expiration"].dt.tz_convert(tz)
    out["expiry_date"] = [None if pd.isna(t) else t.date() for t in exp_local]
    rule, roll = [], []
    for row in out.itertuples():
        if row.is_outright:
            code = pattern.match(row.raw_symbol).group(1)
            year = row.expiry_date.year
            tf = third_friday(year, MONTH_CODES[code])
            rule.append(tf)
            roll.append(tf - dt.timedelta(days=int(roll_offset_days)))
        else:
            rule.append(None)
            roll.append(None)
    out["rule_expiry"] = rule
    out["expiry_matches_rule"] = [None if r is None else bool(r == e) for r, e in zip(rule, out["expiry_date"])]
    out["roll_date"] = roll
    return out.reset_index()[cols]


def session_starts(trading_dates: pd.DatetimeIndex, template: SessionTemplate) -> pd.DatetimeIndex:
    """Scheduled start of each trading date's session (18:00 ET of the previous calendar day)."""
    d = pd.DatetimeIndex(trading_dates).tz_localize(None).normalize() - pd.Timedelta(days=1)
    wall = d + pd.Timedelta(minutes=minutes_of(template.trading_day_start))
    return wall.tz_localize(template.timezone, ambiguous="NaT", nonexistent="shift_forward")


def active_contracts(trading_dates: pd.DatetimeIndex, contracts: pd.DataFrame, template: SessionTemplate) -> pd.DataFrame:
    """The frozen calendar rule: one outright per trading date.

    Eligible on date ``d``: listed before the session of ``d`` starts, and ``d`` earlier
    than the contract's roll date. The eligible contract with the nearest expiry is
    active; the following one is reported as ``next_*`` for diagnostics.
    """
    o = contracts[contracts["is_outright"]].sort_values("expiration", kind="stable")
    td = pd.DatetimeIndex(sorted(set(pd.DatetimeIndex(trading_dates).tz_localize(None).normalize())), name="trading_date")
    starts = session_starts(td, template)
    rows = []
    for d, s in zip(td, starts):
        listed = o["activation"].isna() | (o["activation"] <= s)
        elig = o[listed & (pd.to_datetime(o["roll_date"]) > d)]
        row: dict[str, Any] = {"instrument_id": np.nan, "contract": None, "expiration": pd.NaT, "roll_date": None,
                               "next_instrument_id": np.nan, "next_contract": None}
        if len(elig):
            a = elig.iloc[0]
            row.update(instrument_id=a["instrument_id"], contract=a["raw_symbol"], expiration=a["expiration"],
                       roll_date=a["roll_date"])
            if len(elig) > 1:
                row.update(next_instrument_id=elig.iloc[1]["instrument_id"], next_contract=elig.iloc[1]["raw_symbol"])
        rows.append(row)
    out = pd.DataFrame(rows, index=td)
    out["days_to_expiry"] = [
        np.nan if pd.isna(e) else (e.tz_convert(template.timezone).date() - d.date()).days
        for d, e in zip(td, out["expiration"])
    ]
    return out


def roll_schedule(active: pd.DataFrame) -> pd.DataFrame:
    """Trading dates on which the active contract changes."""
    c = active["contract"].astype(object).to_numpy()
    chg = [i for i in range(1, len(c)) if c[i] != c[i - 1]]
    return pd.DataFrame({
        "trading_date": active.index[chg],
        "from_contract": c[[i - 1 for i in chg]],
        "to_contract": c[chg],
    })


def liquidity_diagnostics(daily_volume: pd.DataFrame, active: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """How the frozen calendar rule compares with traded volume. Diagnostics only.

    ``daily_volume``: trading_date x instrument_id volume of outright contracts.
    Per date: the active contract's share of outright volume and whether it was the
    volume leader of the PREVIOUS session (the causal volume rule a vendor might use).
    Per roll: the first date on which the new contract out-traded the old one (this uses
    the whole sample and is reported, never used to choose a contract).
    """
    dv = daily_volume.reindex(active.index).fillna(0.0)
    tot = dv.sum(axis=1)
    act_ids = active["instrument_id"]
    act_vol = pd.Series([dv.at[d, int(i)] if pd.notna(i) and int(i) in dv.columns else np.nan for d, i in act_ids.items()],
                        index=dv.index)
    leader = dv.idxmax(axis=1).where(tot > 0)
    prev_leader = leader.shift(1)
    per_date = pd.DataFrame({
        "contract": active["contract"], "active_volume": act_vol, "outright_volume": tot,
        "active_share": act_vol / tot.where(tot > 0), "prev_session_leader": prev_leader,
        "prev_leader_is_active": prev_leader == act_ids,
    })
    rolls = roll_schedule(active)
    rows = []
    for r in rolls.itertuples(index=False):
        old_id = active.loc[active["contract"] == r.from_contract, "instrument_id"].iloc[0]
        new_id = active.loc[active["contract"] == r.to_contract, "instrument_id"].iloc[0]
        if old_id in dv.columns and new_id in dv.columns:
            near = (dv.index >= r.trading_date - pd.Timedelta(days=21)) & (dv.index <= r.trading_date + pd.Timedelta(days=14))
            win = dv.loc[near]
            cross = win.index[win[new_id] > win[old_id]]
            first = cross[0] if len(cross) else pd.NaT
        else:
            first = pd.NaT
        lag = np.nan if pd.isna(first) else int(np.busday_count(first.date(), r.trading_date.date()))
        rows.append({"roll_trading_date": r.trading_date, "from_contract": r.from_contract, "to_contract": r.to_contract,
                     "volume_crossover_date": first, "business_days_crossover_before_roll": lag})
    return per_date, pd.DataFrame(rows)
