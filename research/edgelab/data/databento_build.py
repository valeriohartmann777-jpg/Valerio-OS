"""Raw Databento DBN files -> validated, normalised inputs for the existing pipeline.

Two steps, one script each:

1. :func:`validate_raw` (``scripts/validate_market_data.py``) reads the verified raw files
   of one product and period once, checks every trade (``trades.py``) and stages the
   per-file aggregates (1-minute bars of every outright, minute-price volume of the
   active contract) under ``data/processed/databento/<product>/<start>_<end>/stage/``
   with ``validation.json``: counts, fatal findings, findings to review, raw file hashes.
2. :func:`build_product` (``scripts/build_market_data.py``) refuses unless that
   validation passed for the same raw files, then writes under
   ``data/processed/databento/<dataset_id>/``:

   * ``bars_1m.parquet``             1-minute OHLCV of the active contract (UTC ``ts`` =
                                     minute open, ``contract``, ``n_trades``): the manifest input
   * ``bars_1m_outrights.parquet``   the same for every outright (diagnostics only)
   * ``contracts.csv``, ``active_contracts.csv``, ``rolls.csv``, ``liquidity_*.csv``, ``sessions.csv``
   * ``vap_<phase>.parquet``, ``levels_<phase>.parquet``, ``developing_<phase>.parquet``,
     ``composite<N>_<phase>.parquet``  exact volume-at-price products (RTH and ALL)
   * ``build.json``                  counts, session and DST checks, findings, raw hashes

Nothing here is a strategy decision: the roll rule is the frozen one from
``configs/instruments.yaml`` and the value-area definition the one of the studies.
No record is dropped silently; every exclusion is counted in the JSON files.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import RESEARCH_ROOT, InstrumentSpec, SessionTemplate
from ..features.profile import session_profiles
from ..features.vap import composite_levels, developing_levels, label_minutes, session_levels, session_vap
from ..features.volatility import atr
from ..resample import session_bars
from ..sessions import label_sessions, phase_end_times
from .contracts import active_contracts, contract_table, liquidity_diagnostics, read_definitions, roll_schedule, session_starts
from .databento_client import resolve, verified_chunks
from .loader import file_sha256
from .trades import active_bars, bars_to_frame, merge_bars, merge_mpv, process_chunk, read_trade_array, sum_checks

BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "n_trades", "contract"]
LEVEL_COLUMNS = ["poc", "val", "vah", "prof_low", "prof_high", "prof_volume", "vwap", "available_at"]

# Trade-level findings. Fatal ones mean the file is not what it claims to be (wrong price
# scale, corrupt records); nothing is built from it. Review findings stay in the data, are
# reported, and their handling is recorded in the DATA_QUALITY journal note.
FATAL_COUNTERS = {
    "outright_nonpositive_price": "outright trades with price <= 0",
    "outright_zero_size": "outright trades with size <= 0",
    "outright_off_tick_price": "outright trades off the tick grid (wrong price scale or instrument)",
}
REVIEW_COUNTERS = {
    "exact_duplicates_removed": "exact duplicate records (identical in every field; removed, first copy kept)",
    "records_without_definition": "trade records of instrument ids without a definition (not used)",
    "ts_recv_decreases": "records whose capture time runs backwards in file order",
    "outside_request_window": "records outside the requested time window",
    "negative_latency": "records captured before their exchange timestamp",
    "ts_event_decreases_within_instrument": "outright records whose exchange time runs backwards within the instrument",
    "outright_trades_after_expiration": "outright trades after the contract's expiration",
    "outright_trades_outside_session_hours": "outright trades in the 17:00-18:00 ET break or on a weekend trade date (kept)",
}


def _utc_ns(day: str) -> int:
    return pd.Timestamp(day, tz="UTC").value


def _rel(path: Path) -> str:
    try:
        return path.relative_to(RESEARCH_ROOT).as_posix()
    except ValueError:
        return str(path)


def stage_dir(cfg: dict[str, Any], product: str, start: str, end: str, out_root: Path | None = None) -> Path:
    return (out_root or resolve(cfg["processed_dir"])) / product / f"{start}_{end}" / "stage"


def raw_inventory(cfg: dict[str, Any], product: str, start: str, end: str,
                  raw_dir: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Verified raw files per schema: relative path, SHA-256, record count, request window."""
    raw_dir = raw_dir or resolve(cfg["raw_dir"])
    inv: dict[str, list[dict[str, Any]]] = {}
    for schema in (cfg["definition_schema"], cfg["schema"]):
        inv[schema] = [{"path": p.relative_to(raw_dir).as_posix(), "sha256": m["sha256"], "records": int(m["records"]),
                        "start": m["start"], "end": m["end"]}
                       for p, m in verified_chunks(cfg, product, schema, start, end, raw_dir)]
    return inv


def load_contracts(cfg: dict[str, Any], product: str, start: str, end: str, instrument: InstrumentSpec,
                   template: SessionTemplate, raw_dir: Path | None = None) -> pd.DataFrame:
    """Contract table of the product from the raw definition files (frozen roll rule)."""
    roll = instrument.roll or {}
    if roll.get("expiry") != "third_friday":
        raise ValueError(f"{product}: only the third-Friday roll rule of instruments.yaml is implemented")
    files = [p for p, _ in verified_chunks(cfg, product, cfg["definition_schema"], start, end, raw_dir)]
    return contract_table(read_definitions(files), root=cfg["products"][product]["root"],
                          months=roll.get("months", (3, 6, 9, 12)), tick_size=instrument.tick_size,
                          roll_offset_days=int(roll.get("roll_offset_days", 8)), tz=template.timezone)


def calendar_schedule(contracts: pd.DataFrame, start: str, end: str, template: SessionTemplate) -> pd.DataFrame:
    """Active contract for every calendar date around the period (the rule needs no market data)."""
    cal = pd.date_range(pd.Timestamp(start) - pd.Timedelta(days=7), pd.Timestamp(end) + pd.Timedelta(days=7), freq="D")
    return active_contracts(cal, contracts, template)


# ---------------------------------------------------------------------------------------
# step 1: validation of the raw trades
# ---------------------------------------------------------------------------------------
def active_coverage(bars_all: pd.DataFrame, contracts: pd.DataFrame, template: SessionTemplate) -> pd.DataFrame:
    """Per trading date with outright trades: the rule's active contract and its trade count."""
    cols = ["active_instrument_id", "active_contract", "active_trades", "outright_trades"]
    if bars_all.empty:
        return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], name="trading_date"))
    td = label_sessions(pd.DatetimeIndex(bars_all["ts"]), template)["trading_date"].to_numpy()
    n = bars_all.assign(trading_date=td).groupby(["trading_date", "instrument_id"])["n_trades"].sum().unstack(fill_value=0)
    act = active_contracts(n.index, contracts, template)
    ids = act["instrument_id"]
    counts = [int(n.at[d, int(i)]) if pd.notna(i) and int(i) in n.columns else 0 for d, i in ids.items()]
    return pd.DataFrame({"active_instrument_id": ids.to_numpy(), "active_contract": act["contract"].to_numpy(),
                         "active_trades": counts, "outright_trades": n.sum(axis=1).astype(int).to_numpy()},
                        index=pd.DatetimeIndex(act.index, name="trading_date"))


def evaluate_trade_checks(checks: dict[str, Any], contracts: pd.DataFrame,
                          coverage: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Fatal findings and findings to review from the summed trade counters."""
    fatal: list[str] = []
    review: list[str] = []
    if int(checks.get("records", 0)) == 0:
        fatal.append("the trade files contain no records")
    if not contracts["is_outright"].any():
        fatal.append("the definitions contain no outright future of the product")
    if int(checks.get("records_outright", 0)) == 0:
        fatal.append("no outright trades")
    for k, text in FATAL_COUNTERS.items():
        if int(checks.get(k, 0)):
            fatal.append(f"{int(checks[k])} {text}")
    for k, text in REVIEW_COUNTERS.items():
        if int(checks.get(k, 0)):
            review.append(f"{int(checks[k])} {text}")
    if len(coverage):
        no_active = coverage.index[coverage["active_instrument_id"].isna()]
        if len(no_active):
            fatal.append(f"{len(no_active)} trading dates with outright trades but no eligible active contract "
                         f"(e.g. {[str(d.date()) for d in no_active[:5]]})")
        silent = coverage.index[coverage["active_instrument_id"].notna() & (coverage["active_trades"] == 0)]
        if len(silent):
            fatal.append(f"{len(silent)} trading dates on which other outrights traded but the active contract did not "
                         f"(e.g. {[str(d.date()) for d in silent[:5]]})")
    out = contracts[contracts["is_outright"]]
    moved = out.loc[out["n_expirations"] > 1, "raw_symbol"].tolist()
    if moved:
        fatal.append(f"outright contracts whose expiration changed between definitions: {moved} "
                     "(the calendar roll would depend on a later definition)")
    odd = out.loc[out["expiry_matches_rule"] == False, "raw_symbol"].tolist()  # noqa: E712
    if odd:
        review.append(f"outright expiries not on the third Friday: {odd} (roll dates still follow the frozen rule)")
    multi = contracts.loc[contracts["n_raw_symbols"] > 1, "instrument_id"].tolist()
    if multi:
        review.append(f"instrument ids with more than one raw symbol in the definitions: {multi}")
    return fatal, review


def validate_raw(cfg: dict[str, Any], product: str, start: str, end: str, *, instrument: InstrumentSpec,
                 template: SessionTemplate, raw_dir: Path | None = None, out_root: Path | None = None) -> dict[str, Any]:
    """Check every trade of the raw files and stage the per-file aggregates."""
    raw_dir = raw_dir or resolve(cfg["raw_dir"])
    sdir = stage_dir(cfg, product, start, end, out_root)
    if sdir.exists():
        shutil.rmtree(sdir)  # derived data only; raw files are never touched
    sdir.mkdir(parents=True)
    inv = raw_inventory(cfg, product, start, end, raw_dir)
    contracts = load_contracts(cfg, product, start, end, instrument, template, raw_dir)
    sched = calendar_schedule(contracts, start, end, template)
    active_by_date = {d: int(i) for d, i in sched["instrument_id"].items() if pd.notna(i)}

    checks, files = [], []
    for k, info in enumerate(inv[cfg["schema"]]):
        arr = read_trade_array(raw_dir / info["path"])
        res = process_chunk(arr, chunk_index=k, contracts=contracts, tick_size=instrument.tick_size,
                            request_start_ns=_utc_ns(info["start"]), request_end_ns=_utc_ns(info["end"]),
                            template=template, active_by_date=active_by_date)
        del arr
        b_path, m_path = sdir / f"bars_{k:03d}.parquet", sdir / f"mpv_{k:03d}.parquet"
        res.bars.to_parquet(b_path, index=False)
        res.mpv.to_parquet(m_path, index=False)
        files.append({"raw": info["path"], "bars": b_path.name, "mpv": m_path.name, "checks": res.checks})
        checks.append(res.checks)
    total = sum_checks(checks)

    bars_all = bars_to_frame(merge_bars([pd.read_parquet(sdir / f["bars"]) for f in files]), contracts)
    coverage = active_coverage(bars_all, contracts, template)
    fatal, review = evaluate_trade_checks(total, contracts, coverage)
    out = contracts[contracts["is_outright"]]
    val = {
        "product": product, "parent": cfg["products"][product]["parent"], "dataset": cfg["dataset"],
        "schema": cfg["schema"], "period": [start, end],
        "validated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "raw_files": inv, "stage_files": files, "trade_checks": total,
        "contracts": {
            "instruments_in_definitions": int(len(contracts)), "outright": int(len(out)),
            "outright_symbols": out["raw_symbol"].tolist(),
            "excluded_by_reason": {str(k): int(v) for k, v in
                                   contracts.loc[~contracts["is_outright"], "exclusion"].value_counts().items()},
        },
        "trading_dates_with_outright_trades": int(len(coverage)),
        "active_contract_by_trading_date": {str(d.date()): c for d, c in coverage["active_contract"].items()},
        "fatal": fatal, "review": review, "passed": not fatal,
    }
    (sdir / "validation.json").write_text(json.dumps(val, indent=2, default=str))
    return val


# ---------------------------------------------------------------------------------------
# step 2: build
# ---------------------------------------------------------------------------------------
def session_windows(trading_dates: pd.DatetimeIndex, template: SessionTemplate) -> pd.DataFrame:
    """Scheduled start and end of each trading date's Globex session and of its RTH."""
    td = pd.DatetimeIndex(trading_dates)
    return pd.DataFrame({
        "session_start": session_starts(td, template),
        "session_end": phase_end_times(td, template, "POST"),
        "rth_start": phase_end_times(td, template, "ON"),
        "rth_end": phase_end_times(td, template, "RTH"),
    }, index=td)


def build_product(
    cfg: dict[str, Any], product: str, start: str, end: str, *, dataset_id: str, instrument: InstrumentSpec,
    template: SessionTemplate, raw_dir: Path | None = None, out_root: Path | None = None,
) -> dict[str, Any]:
    """Bars, contract schedule and exact volume-at-price products from validated stage files."""
    raw_dir = raw_dir or resolve(cfg["raw_dir"])
    sdir = stage_dir(cfg, product, start, end, out_root)
    vpath = sdir / "validation.json"
    if not vpath.exists():
        raise FileNotFoundError(f"{vpath} missing: run scripts/validate_market_data.py first")
    val = json.loads(vpath.read_text())
    if val["fatal"]:
        raise RuntimeError(f"validation failed, nothing is built: {val['fatal']}")
    if raw_inventory(cfg, product, start, end, raw_dir) != val["raw_files"]:
        raise RuntimeError("the raw files differ from the validated ones: run scripts/validate_market_data.py again")
    out = (out_root or resolve(cfg["processed_dir"])) / dataset_id
    out.mkdir(parents=True, exist_ok=True)
    vcfg = cfg.get("volume_at_price", {})
    va_pct, va_method = float(vcfg.get("va_pct", 0.70)), str(vcfg.get("va_method", "single"))
    n_comp = int(vcfg.get("composite_sessions", 5))
    tick = instrument.tick_size

    contracts = load_contracts(cfg, product, start, end, instrument, template, raw_dir)
    files = val["stage_files"]
    bars_all = bars_to_frame(merge_bars([pd.read_parquet(sdir / f["bars"]) for f in files]), contracts)
    lab_all = label_sessions(pd.DatetimeIndex(bars_all["ts"]), template)
    present = pd.DatetimeIndex(sorted(pd.unique(lab_all["trading_date"])), name="trading_date")
    active = active_contracts(present, contracts, template)
    bars_act = active_bars(bars_all, active, template)

    win = session_windows(present, template)
    req_a, req_b = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    complete = {"ALL": (win["session_start"] >= req_a) & (win["session_end"] <= req_b),
                "RTH": (win["rth_start"] >= req_a) & (win["rth_end"] <= req_b)}

    mpv = label_minutes(merge_mpv([pd.read_parquet(sdir / f["mpv"]) for f in files]), template)
    products: dict[str, Any] = {}
    levels: dict[str, pd.DataFrame] = {}
    for phase in ("RTH", "ALL"):
        vap = session_vap(mpv, phase, tick)
        lv = session_levels(vap, tick_size=tick, template=template, phase=phase, va_pct=va_pct, va_method=va_method)
        if lv.empty:
            lv = pd.DataFrame(columns=LEVEL_COLUMNS, index=pd.DatetimeIndex([], name="trading_date"))
        lv["contract"] = active["contract"].reindex(lv.index).to_numpy()
        lv["complete"] = complete[phase].reindex(lv.index).fillna(False).astype(bool).to_numpy()
        dev = developing_levels(mpv, phase=phase, tick_size=tick, va_pct=va_pct, va_method=va_method)
        comp = composite_levels(vap, lv, lv["contract"], lv["complete"], n_sessions=n_comp, tick_size=tick,
                                va_pct=va_pct, va_method=va_method)
        vap.to_parquet(out / f"vap_{phase}.parquet", index=False)
        lv.to_parquet(out / f"levels_{phase}.parquet")
        dev.to_parquet(out / f"developing_{phase}.parquet", index=False)
        comp.to_parquet(out / f"composite{n_comp}_{phase}.parquet")
        levels[phase] = lv
        products[phase] = {
            "sessions": int(len(lv)), "complete_sessions": int(lv["complete"].sum()),
            "developing_rows": int(len(dev)),
            "composite_rows_filled": int(comp["poc"].notna().sum()) if len(comp) else 0,
        }

    dv = (bars_all.assign(trading_date=lab_all["trading_date"].to_numpy())
          .groupby(["trading_date", "instrument_id"])["volume"].sum().unstack(fill_value=0.0))
    liq_daily, liq_rolls = liquidity_diagnostics(dv, active)
    rolls = roll_schedule(active)
    checks, sessions = session_checks(bars_act, bars_all, levels["RTH"], rolls, instrument, template, start, end)
    fatal, review = evaluate_session_checks(checks, template.trading_day_start.strftime("%H:%M"))

    bars_act[BAR_COLUMNS].to_parquet(out / "bars_1m.parquet", index=False)
    bars_all.to_parquet(out / "bars_1m_outrights.parquet", index=False)
    contracts.to_csv(out / "contracts.csv", index=False)
    active.to_csv(out / "active_contracts.csv")
    rolls.to_csv(out / "rolls.csv", index=False)
    liq_daily.to_csv(out / "liquidity_daily.csv")
    liq_rolls.to_csv(out / "liquidity_rolls.csv", index=False)
    sessions.to_csv(out / "sessions.csv")

    summary = {
        "dataset_id": dataset_id, "product": product, "parent": val["parent"], "dataset": val["dataset"],
        "schema": val["schema"], "period": [start, end],
        "built_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "raw_files": val["raw_files"], "validation": _rel(vpath),
        "roll_rule": {"source": "configs/instruments.yaml", **(instrument.roll or {})},
        "timestamp": "ts_event (exchange time); bars are labelled by the UTC minute they open",
        "contracts_used": active["contract"].dropna().unique().tolist(),
        "rolls": [{"trading_date": str(r.trading_date.date()), "from": r.from_contract, "to": r.to_contract}
                  for r in rolls.itertuples(index=False)],
        "trading_dates": int(len(present)),
        "first_trading_date": str(present.min().date()) if len(present) else None,
        "last_trading_date": str(present.max().date()) if len(present) else None,
        "incomplete_sessions_by_request_window": [str(d.date()) for d in present[~complete["ALL"].to_numpy()]],
        "bars_active": int(len(bars_act)), "bars_all_outrights": int(len(bars_all)),
        "volume_active": float(bars_act["volume"].sum()), "trades_active": int(bars_act["n_trades"].sum()),
        "trade_checks": val["trade_checks"], "products": products, "composite_sessions": n_comp,
        "value_area": {"va_pct": va_pct, "va_method": va_method},
        "session_checks": checks,
        "liquidity": {
            "median_active_share": float(liq_daily["active_share"].median()) if len(liq_daily) else None,
            "share_dates_active_was_prev_session_volume_leader": (
                float(liq_daily["prev_leader_is_active"].mean()) if len(liq_daily) else None),
            "rolls": json.loads(liq_rolls.to_json(orient="records", date_format="iso")),
        },
        "fatal": fatal, "review": val["review"] + review, "passed": not fatal,
        "bars_file_sha256": file_sha256(out / "bars_1m.parquet"),
    }
    (out / "build.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary


# ---------------------------------------------------------------------------------------
# checks on the built products (sessions, DST, gaps, profile approximation, roll gaps)
# ---------------------------------------------------------------------------------------
def session_checks(bars_act: pd.DataFrame, bars_all: pd.DataFrame, levels_rth: pd.DataFrame, rolls: pd.DataFrame,
                   instrument: InstrumentSpec, template: SessionTemplate, start: str,
                   end: str) -> tuple[dict[str, Any], pd.DataFrame]:
    """Session boundaries in New York time across DST, RTH coverage, gaps, profile and roll diagnostics."""
    rep: dict[str, Any] = {}
    idx = pd.DatetimeIndex(bars_act["ts"]).tz_convert(template.timezone)
    b = bars_act.set_index(idx)
    lab = label_sessions(idx, template)
    rth = lab["is_rth"].to_numpy()
    frame = pd.DataFrame({"ts": idx, "td": lab["trading_date"].to_numpy(), "rth": rth,
                          "volume": b["volume"].to_numpy(), "contract": b["contract"].to_numpy()})
    g = frame.groupby("td", sort=True)
    sessions = pd.DataFrame({
        "first_bar": g["ts"].min(), "last_bar": g["ts"].max(), "n_bars": g.size(), "rth_bars": g["rth"].sum(),
        "max_gap_min": g["ts"].apply(lambda s: s.diff().dt.total_seconds().max() / 60.0),
        "volume": g["volume"].sum(),
        "rth_volume": frame[frame["rth"]].groupby("td")["volume"].sum(),
        "contract": g["contract"].first(),
    })
    sessions.index = pd.DatetimeIndex(sessions.index, name="trading_date")
    sessions["rth_volume"] = sessions["rth_volume"].fillna(0.0)
    sessions["rth_bars"] = sessions["rth_bars"].astype(int)
    win = session_windows(sessions.index, template)
    req_a, req_b = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    sessions["complete_in_request"] = ((win["session_start"] >= req_a) & (win["session_end"] <= req_b)).to_numpy()

    opens = sessions.loc[sessions["complete_in_request"], "first_bar"]
    day_start = template.trading_day_start.strftime("%H:%M")
    wall = opens.dt.strftime("%H:%M")
    regime = opens.map(lambda t: "EDT" if t.dst() else "EST")
    utc = opens.dt.tz_convert("UTC").dt.strftime("%H:%M")
    rep["session_open_by_regime"] = {
        r: {"sessions": int((regime == r).sum()),
            "first_bar_ny": {str(k): int(v) for k, v in wall[regime == r].value_counts().items()},
            "first_bar_utc": {str(k): int(v) for k, v in utc[regime == r].value_counts().items()}}
        for r in ("EST", "EDT") if (regime == r).any()
    }
    rep["sessions_first_bar_not_at_day_start"] = {str(d.date()): w for d, w in wall.items() if w != day_start}
    rep["bars_in_maintenance_break"] = int((lab["phase"].astype(str) == "BREAK").sum())
    rep["bars_on_weekend_trade_dates"] = int((lab["weekday"].to_numpy() >= 5).sum())
    full = int(round((dt.datetime.combine(dt.date(2000, 1, 3), template.rth_end)
                      - dt.datetime.combine(dt.date(2000, 1, 3), template.rth_start)).total_seconds() / 60))
    rep["rth_minutes_full_session"] = full
    rep["sessions"] = int(len(sessions))
    rep["sessions_full_rth"] = int((sessions["rth_bars"] == full).sum())
    rep["sessions_missing_rth_minutes"] = {str(d.date()): int(full - r) for d, r in sessions["rth_bars"].items() if 0 < r < full}
    rep["sessions_without_rth"] = [str(d.date()) for d, r in sessions["rth_bars"].items() if r == 0]
    rep["largest_intrasession_gaps_min"] = {str(d.date()): float(v) for d, v in sessions["max_gap_min"].nlargest(5).items()}

    # exact (trades) vs. the pre-registered approximation (1-minute bars, uniform allocation)
    approx = session_profiles(b[["open", "high", "low", "close", "volume"]], lab, tick_size=instrument.tick_size,
                              template=template, phase="RTH", method="uniform", with_shape=False)
    cmp: dict[str, Any] = {}
    if len(approx) and len(levels_rth):
        both = levels_rth[levels_rth["complete"]].join(approx[["poc", "val", "vah"]], rsuffix="_bars", how="inner")
        for k in ("poc", "val", "vah"):
            diff = ((both[f"{k}_bars"].astype(float) - both[k].astype(float)) / instrument.tick_size).abs().dropna()
            cmp[k] = {"sessions": int(len(diff)),
                      "median_abs_ticks": float(diff.median()) if len(diff) else None,
                      "p90_abs_ticks": float(diff.quantile(0.9)) if len(diff) else None,
                      "max_abs_ticks": float(diff.max()) if len(diff) else None,
                      "share_within_1_tick": float((diff <= 1).mean()) if len(diff) else None}
    rep["rth_profile_trades_vs_1m_uniform"] = cmp

    # gap between the two contracts at a roll, in daily-ATR units (the series is unadjusted)
    daily = session_bars(b[["open", "high", "low", "close", "volume"]], lab, template, phase="RTH")
    atr_d = atr(daily, 14) if len(daily) else pd.Series(dtype=float)
    a_idx = pd.DatetimeIndex(bars_all["ts"]).tz_convert(template.timezone)
    a_lab = label_sessions(a_idx, template)
    allb = bars_all.assign(trading_date=a_lab["trading_date"].to_numpy(), is_rth=a_lab["is_rth"].to_numpy(), ts_ny=a_idx)
    gaps = []
    for r in rolls.itertuples(index=False):
        prior = daily.index[daily.index < r.trading_date]
        if not len(prior):
            continue
        pdate = prior[-1]
        sel = allb[(allb["trading_date"] == pdate) & allb["is_rth"] & allb["contract"].isin([r.from_contract, r.to_contract])]
        last_bar = sel.sort_values("ts").groupby("contract").tail(1).set_index("contract")
        if not {r.from_contract, r.to_contract} <= set(last_bar.index):
            continue
        spread = float(last_bar.at[r.to_contract, "close"] - last_bar.at[r.from_contract, "close"])
        a = float(atr_d.get(pdate, np.nan))
        gaps.append({"roll_trading_date": str(r.trading_date.date()), "from": r.from_contract, "to": r.to_contract,
                     "prior_rth_date": str(pdate.date()), "spread_points": spread,
                     "last_rth_bar_ny_from": str(last_bar.at[r.from_contract, "ts_ny"]),
                     "last_rth_bar_ny_to": str(last_bar.at[r.to_contract, "ts_ny"]),
                     "atr_d14": None if np.isnan(a) else a, "spread_in_atr_d": None if np.isnan(a) or a == 0 else spread / a})
    rep["roll_gaps"] = gaps
    return rep, sessions


def evaluate_session_checks(rep: dict[str, Any], day_start_ny: str = "18:00") -> tuple[list[str], list[str]]:
    """A time-zone error shows as sessions whose usual first bar is not 18:00 New York time in
    one DST regime; that is fatal. Everything else is reported for review."""
    fatal: list[str] = []
    review: list[str] = []
    for regime, d in rep.get("session_open_by_regime", {}).items():
        counts = d["first_bar_ny"]
        modal = max(counts, key=counts.get) if counts else None
        if modal != day_start_ny:
            fatal.append(f"{regime}: sessions mostly start at {modal} New York time, not {day_start_ny} "
                         "(time-zone conversion error)")
    if rep.get("bars_in_maintenance_break"):
        review.append(f"{rep['bars_in_maintenance_break']} bars in the 17:00-18:00 ET maintenance break (kept)")
    if rep.get("bars_on_weekend_trade_dates"):
        review.append(f"{rep['bars_on_weekend_trade_dates']} bars on weekend trade dates (kept)")
    if rep.get("sessions_first_bar_not_at_day_start"):
        review.append(f"sessions whose first bar is not at {day_start_ny} ET: {rep['sessions_first_bar_not_at_day_start']}")
    if rep.get("sessions_missing_rth_minutes"):
        review.append("sessions with RTH minutes without a trade (holiday, early close or gaps): "
                      f"{rep['sessions_missing_rth_minutes']}")
    if rep.get("sessions_without_rth"):
        review.append(f"sessions without RTH trading: {rep['sessions_without_rth']}")
    return fatal, review
