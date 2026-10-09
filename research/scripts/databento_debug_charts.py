"""Debug charts of built Databento products, to check the data by eye.

Usage (from research/):
    python scripts/databento_debug_charts.py --id ES_DB_PILOT
    python scripts/databento_debug_charts.py --id NQ_DB_PILOT --copy-to /mnt/project-files/databento_charts

Sessions are chosen by rule, never by looking at trades or outcomes:
  normal       two seeded random complete sessions with full RTH that are no roll date,
               not the session after one, not the first session after a DST change and
               not the high-volume session
  roll         the first session on each new contract (the runner excludes it)
  dst          the first session after each daylight-saving change in the period
  high_volume  the session with the most RTH volume
  overnight    a seeded random complete session, its overnight phase (18:00-09:30 ET)

Each chart: 5-minute candles of the active contract, developing POC / VAH / VAL and VWAP
(known at each minute's close), the completed profile's levels (known only at the end of
the phase) and the exact volume-at-price histogram from trades. Charts are written next
to the data (never committed: the data is licensed).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from edgelab.config import load_instrument  # noqa: E402
from edgelab.data.databento_client import load_databento_config, resolve  # noqa: E402
from edgelab.data.manifest import get_entry  # noqa: E402

TZ = "America/New_York"


def windows(d: pd.Timestamp, view: str) -> tuple[pd.Timestamp, pd.Timestamp, str]:
    """(start, end) New York wall-clock window of a view and the profile phase it shows."""
    day = pd.Timestamp(d).normalize()
    if view == "RTH":
        a, b, ph = day + pd.Timedelta(hours=9, minutes=30), day + pd.Timedelta(hours=16), "RTH"
    elif view == "ON":
        a, b, ph = day - pd.Timedelta(hours=6), day + pd.Timedelta(hours=9, minutes=30), "ALL"
    else:
        a, b, ph = day - pd.Timedelta(hours=6), day + pd.Timedelta(hours=17), "ALL"
    return a.tz_localize(TZ), b.tz_localize(TZ), ph


def first_after_dst(dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    off = [(pd.Timestamp(d).normalize() + pd.Timedelta(hours=12)).tz_localize(TZ).utcoffset() for d in dates]
    return [dates[i] for i in range(1, len(dates)) if off[i] != off[i - 1]]


def pick_sessions(sessions: pd.DataFrame, rolls: pd.DataFrame, seed: int,
                  n_normal: int = 2) -> list[tuple[str, pd.Timestamp, str]]:
    rng = np.random.default_rng(seed)
    full = sessions[(sessions["rth_bars"] == sessions["rth_bars"].max()) & sessions["complete_in_request"]]
    roll_dates = [pd.Timestamp(d) for d in rolls["trading_date"]] if len(rolls) else []
    after_roll = [sessions.index[sessions.index > r][0] for r in roll_dates if (sessions.index > r).any()]
    dst = first_after_dst(sessions.index)
    picks: list[tuple[str, pd.Timestamp, str]] = []
    for r in roll_dates:
        if r in sessions.index:
            picks.append(("roll", r, "RTH"))
    for d in dst:
        picks.append(("dst", d, "ALL"))
    high = full["rth_volume"].idxmax() if len(full) else None
    if high is not None:
        picks.append(("high_volume", high, "RTH"))
    taken = set(roll_dates) | set(after_roll) | set(dst) | ({high} if high is not None else set())
    pool = [d for d in full.index if d not in taken]
    for d in sorted(rng.choice(pool, size=min(n_normal, len(pool)), replace=False)) if pool else []:
        picks.append(("normal", pd.Timestamp(d), "RTH"))
    complete = sessions.index[sessions["complete_in_request"]]
    if len(complete):
        picks.append(("overnight", pd.Timestamp(rng.choice(complete)), "ON"))
    return picks


def candles(ax, b5: pd.DataFrame, width_min: float = 3.5) -> None:
    w = width_min / (24 * 60)
    for t, r in b5.iterrows():
        x = mdates.date2num(t)
        up = r["close"] >= r["open"]
        col = "#2e7d32" if up else "#c62828"
        ax.vlines(x, r["low"], r["high"], color=col, linewidth=0.7)
        ax.add_patch(plt.Rectangle((x - w / 2, min(r["open"], r["close"])), w, max(abs(r["close"] - r["open"]), 1e-9),
                                   color=col, linewidth=0))


def chart(out: Path, data: dict, cat: str, d: pd.Timestamp, view: str, dataset_id: str, tick: float,
          rolls: pd.DataFrame) -> Path:
    a, b, ph = windows(d, view)
    bars = data["bars"]
    w = bars[(bars.index >= a) & (bars.index < b)]
    if w.empty:
        return None
    b5 = w.resample("5min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["open"])
    dev = data[f"dev_{ph}"]
    dv = dev[(dev["trading_date"] == d)]
    dv = dv[(dv["ts"] >= a) & (dv["ts"] < b)]
    lv = data[f"lv_{ph}"]
    vap = data[f"vap_{ph}"]
    vp = vap[vap["trading_date"] == d]
    contract = w["contract"].iloc[0]

    fig = plt.figure(figsize=(15, 7.5))
    ax = fig.add_axes([0.05, 0.08, 0.72, 0.84])
    axp = fig.add_axes([0.78, 0.08, 0.18, 0.84], sharey=ax)
    candles(ax, b5)
    if len(dv):
        known = dv["known_at"].dt.tz_convert(TZ)
        ax.step(known, dv["dev_poc"], where="post", color="#1565c0", lw=1.2, label="developing POC")
        ax.step(known, dv["dev_vah"], where="post", color="#1565c0", lw=0.8, ls="--", label="developing VAH / VAL")
        ax.step(known, dv["dev_val"], where="post", color="#1565c0", lw=0.8, ls="--")
        ax.plot(known, dv["dev_vwap"], color="#ef6c00", lw=1.1, label="developing VWAP")
    if d in lv.index:
        row = lv.loc[d]
        avail = pd.Timestamp(row["available_at"]).tz_convert(TZ)
        for k, ls in (("poc", "-"), ("vah", ":"), ("val", ":")):
            ax.axhline(row[k], color="#6a1b9a", lw=0.9, ls=ls, alpha=0.8)
        ax.text(0.01, 0.98, f"final {ph} POC/VAH/VAL (purple) known only at {avail.strftime('%H:%M')} ET",
                transform=ax.transAxes, va="top", fontsize=8, color="#6a1b9a")
    if len(vp):
        axp.barh(vp["price"], vp["volume"], height=tick * 0.9, color="#90a4ae")
        if d in lv.index:
            row = lv.loc[d]
            ins = (vp["price"] >= row["val"]) & (vp["price"] <= row["vah"])
            axp.barh(vp.loc[ins, "price"], vp.loc[ins, "volume"], height=tick * 0.9, color="#5c6bc0")
            pk = vp["price"].sub(row["poc"]).abs().idxmin()
            axp.barh([vp.at[pk, "price"]], [vp.at[pk, "volume"]], height=tick * 0.9, color="#6a1b9a")
    axp.set_title(f"exact volume at price ({ph}, trades)", fontsize=9)
    axp.tick_params(labelleft=False, labelsize=7)
    roll_txt = ""
    if len(rolls) and (pd.to_datetime(rolls["trading_date"]) == d).any():
        r = rolls[pd.to_datetime(rolls["trading_date"]) == d].iloc[0]
        roll_txt = f"   ROLL {r['from_contract']} -> {r['to_contract']} (date excluded by the runner)"
        ax.axvline(mdates.date2num(a), color="black", lw=1.5)
    ax.set_title(f"{dataset_id}  trading date {d.date()}  view {view}  contract {contract}  [{cat}]  "
                 f"session clock {a.strftime('%Z (UTC%z)')}{roll_txt}", fontsize=10)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TZ))
    ax.set_xlim(mdates.date2num(a), mdates.date2num(b))
    ax.legend(loc="lower left", fontsize=8)
    ax.grid(alpha=0.2)
    path = out / f"{dataset_id}_{d.date()}_{view}_{cat}.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def load(dir_: Path) -> dict:
    bars = pd.read_parquet(dir_ / "bars_1m.parquet")
    bars = bars.set_index(pd.DatetimeIndex(bars.pop("ts")).tz_convert(TZ))
    data = {"bars": bars}
    for ph in ("RTH", "ALL"):
        dev = pd.read_parquet(dir_ / f"developing_{ph}.parquet")
        dev["ts"] = pd.DatetimeIndex(dev["ts"]).tz_convert(TZ)
        dev["trading_date"] = pd.to_datetime(dev["trading_date"])
        data[f"dev_{ph}"] = dev
        data[f"lv_{ph}"] = pd.read_parquet(dir_ / f"levels_{ph}.parquet")
        vap = pd.read_parquet(dir_ / f"vap_{ph}.parquet")
        vap["trading_date"] = pd.to_datetime(vap["trading_date"])
        data[f"vap_{ph}"] = vap
    return data


def make_charts(dir_: Path, dataset_id: str, tick: float, seed: int) -> list[Path]:
    sessions = pd.read_csv(dir_ / "sessions.csv", index_col="trading_date", parse_dates=["trading_date"])
    rolls = pd.read_csv(dir_ / "rolls.csv")
    data = load(dir_)
    out = dir_ / "charts"
    out.mkdir(exist_ok=True)
    written = []
    for cat, d, view in pick_sessions(sessions, rolls, seed):
        p = chart(out, data, cat, d, view, dataset_id, tick, rolls)
        if p is not None:
            written.append(p)
            print(f"{cat:12s} {d.date()} {view:4s} -> {p}")
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True, help="dataset id built by scripts/build_market_data.py")
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--copy-to", type=Path, help="also copy the charts to this directory")
    args = ap.parse_args()

    cfg = load_databento_config()
    inst = load_instrument(get_entry(args.id).instrument)
    dir_ = resolve(cfg["processed_dir"]) / args.id
    written = make_charts(dir_, args.id, inst.tick_size, args.seed)
    if args.copy_to:
        dest = args.copy_to / args.id
        dest.mkdir(parents=True, exist_ok=True)
        for p in written:
            shutil.copy(p, dest / p.name)
        print(f"copied {len(written)} charts to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
