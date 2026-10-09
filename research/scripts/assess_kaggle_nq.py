"""Assess the Kaggle NQ 1-minute file and, if nothing stops it, register it for the runner.

Usage (from research/):
    python scripts/assess_kaggle_nq.py                 # reports only: schema, quality, rolls; registers nothing
    python scripts/assess_kaggle_nq.py --register      # + canonical input files, manifest entries, prepare
    python scripts/assess_kaggle_nq.py --file data/raw/kaggle/nq/<file>   # if the download holds several bar files

Steps
  1. find the bar file in data/raw/kaggle/nq/ by content (never by name); the raw file
     is read, never changed
  2. schema report: every column with dtype, nulls, range, examples and role (VWAP, RTH or
     ETH VWAP, session marker, contract, roll information are looked for explicitly)
  3. timezone and bar label from the CME session structure (edgelab/data/vendor_bars.py:
     infer_clock); STOP unless exactly one reading fits
  4. canonical rows: UTC bar-open time, OHLCV; impossible bars removed and counted, STOP
     above the configured share; duplicates are left to manifest.prepare (identical:
     dropped and counted; conflicting: STOP)
  5. quality checks (generic report + weekend, break, holidays, DST session starts,
     missing minutes, repeated bars) and the findings table: problem, count, handling, why
  6. roll assessment: contract column / unadjusted with roll gaps / back-adjusted /
     unclear; STOP if absolute historical levels are not available or a vendor roll
     escapes the frozen roll exclusion (RESEARCH_PROTOCOL.md 2.3, 13.3)
Writes outputs/nq_data_schema_report.md, outputs/nq_data_quality_report.md,
docs/KAGGLE_NQ_ROLLOVER_ASSESSMENT.md and reports/data_quality/kaggle_nq_assessment.json.

With --register and no STOP: data/processed/kaggle/<id>/bars_1m.parquet for the research
dataset and for the blind pilot (first months of the file, purpose pipeline_check), both
registered in configs/datasets.yaml and prepared. Then run scripts/data_quality.py and
write the DATA_QUALITY journal notes. Nothing here runs a study.
Exit codes: 0 ok, 2 STOP (reports written, nothing registered), 1 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from edgelab.config import CONFIG_DIR, RESEARCH_ROOT, load_instrument, load_session_template, load_yaml  # noqa: E402
from edgelab.data.loader import normalize_columns  # noqa: E402
from edgelab.data.manifest import get_entry, prepare, register  # noqa: E402
from edgelab.data.quality import check_bars, render_markdown  # noqa: E402
from edgelab.data.vendor_bars import (  # noqa: E402
    StopCondition, assess_rolls, column_mapping, column_profile, convert_clock, describe_file, extra_quality,
    file_identity, find_bar_files, infer_clock, normalise, raw_timestamps, read_bar_table, special_columns, to_ny_bars,
    vwap_scope,
)
from edgelab.data.vendor_reports import findings, render_quality, render_rolls, render_schema  # noqa: E402
from edgelab.sessions import trading_dates  # noqa: E402


def jdefault(v):
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    return str(v)


def show(path: Path) -> str:
    try:
        return str(path.relative_to(RESEARCH_ROOT))
    except ValueError:
        return str(path)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {show(path)}")


def load_raw(paths: list[Path], infos: list[dict]) -> pd.DataFrame:
    parts = [read_bar_table(p, i["kind"], headerless=(i.get("header") or "").startswith("absent")) for p, i in zip(paths, infos, strict=True)]
    return pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, nargs="+", help="bar file(s) to use instead of the content search")
    ap.add_argument("--register", action="store_true", help="register and prepare the datasets when nothing stops")
    ap.add_argument("--config", type=Path, default=CONFIG_DIR / "kaggle.yaml", help=argparse.SUPPRESS)  # software tests
    args = ap.parse_args()

    cfg = load_yaml(args.config)
    acfg = cfg["assessment"]
    inst = load_instrument(cfg["instrument"])
    tpl = load_session_template(inst.session_template)
    raw_dir = RESEARCH_ROOT / cfg["raw_dir"]
    rep_paths = {k: RESEARCH_ROOT / v for k, v in cfg["reports"].items()}

    if args.file:
        files = [describe_file(p) for p in args.file]
        bad = [f["name"] for f in files if not f["candidate"]]
        if bad:
            print(f"STOP: not bar files: {bad} ({[f['reason'] for f in files if not f['candidate']]})")
            return 2
        bar_paths = [Path(f["path"]) for f in files]
    else:
        if not raw_dir.exists():
            print(f"STOP: {cfg['raw_dir']} does not exist: run scripts/kaggle_download.py first")
            return 2
        bar_paths, files = find_bar_files(raw_dir)
    infos = [next(f for f in files if f["path"] == str(p)) for p in bar_paths]
    if not bar_paths:
        print(f"STOP: no file in {cfg['raw_dir']} holds 1-minute OHLCV bars:")
        for f in files:
            print(f"  {f['name']}: {f['reason']}")
        return 2
    if len({tuple(i["columns_canonical"]) for i in infos}) > 1:
        print(f"STOP: several bar files with different columns {[i['name'] for i in infos]}: choose with --file")
        return 2
    if len(bar_paths) > 1:
        print(f"{len(bar_paths)} bar files with the same columns are read as parts of one series: {[i['name'] for i in infos]}")

    ident = file_identity(bar_paths[0]) if len(bar_paths) == 1 else {
        "name": " + ".join(p.name for p in bar_paths), "bytes": sum(p.stat().st_size for p in bar_paths),
        "sha256": "+".join(file_identity(p)["sha256"][:16] for p in bar_paths), "kind": infos[0]["kind"]}
    ident.update({k: infos[0].get(k) for k in ("header", "timestamp_columns")})
    ident["rows"] = sum((i.get("rows") or 0) for i in infos) or None
    print(f"bar file: {ident['name']} ({ident['bytes']:,} bytes)")

    raw = load_raw(bar_paths, infos)
    try:
        mapping = column_mapping(raw)
        norm = normalize_columns(raw)
    except ValueError as exc:
        print(f"STOP: column mapping is ambiguous: {exc}")
        return 2
    profile = column_profile(raw, mapping)
    specials = special_columns(profile)

    print("inferring timezone and bar label ...")
    clock = infer_clock(norm, tpl, min_match=float(acfg["clock_min_match"]), min_label_share=float(acfg["clock_min_label_share"]))
    print(f"  {clock.status}: zone {clock.tz_in}, label {clock.label}, structure {clock.structure}" +
          (f"; {clock.reasons}" if clock.reasons else ""))

    stops: list[str] = []
    frame = norm_log = extra = chk = roll = None
    generic_md = None
    vwap_checks: list[dict] = []
    max_share = float(acfg["max_removed_share"])
    if clock.decided:
        try:
            frame, norm_log = normalise(norm, clock, max_removed_share=max_share)
        except StopCondition as exc:
            stops.append(str(exc))
    if frame is not None:
        bars = to_ny_bars(frame, tpl.timezone)
        extra = extra_quality(frame, bars, tpl, bar_minutes=clock.bar_minutes)
        meta = {"sha256": ident["sha256"], "label_convention": f"{clock.label} (inferred)", "tz_in": f"{clock.tz_in} (inferred)",
                "has_bid_ask_volume": {"bid_volume", "ask_volume"} <= set(frame.columns)}
        chk = check_bars(bars, instrument=inst.symbol, source=f"Kaggle {cfg['dataset']} ({ident['name']})", template=tpl,
                         bar_minutes=clock.bar_minutes, tick_size=inst.tick_size, roll_cfg=inst.roll, meta=meta)
        generic_md = render_markdown([chk], title="generic checks")
        w = acfg["roll_window_days"]
        roll = assess_rolls(bars, tpl, inst.roll or {}, inst.tick_size, bar_minutes=clock.bar_minutes, root=inst.symbol,
                            window_days=(int(w[0]), int(w[1])), clear_z=float(acfg["roll_clear_z"]),
                            dominance=float(acfg["roll_dominance"]))
        vcols = specials["vwap"] + specials["vwap_rth"] + specials["vwap_eth"]
        if vcols:
            rc, kind = raw_timestamps(norm)
            utc, _ = convert_clock(rc, kind, clock.tz_in, clock.label, clock.bar_minutes)
            ok = ~utc.isna()
            ny = pd.DatetimeIndex(utc[ok]).tz_convert(tpl.timezone)
            for col in vcols:
                vwap_checks.append(vwap_scope(pd.DataFrame({col: raw[col].to_numpy()[np.asarray(ok)]}, index=ny), col, tpl))
    rows, more = findings(clock, norm_log, extra, chk, roll, max_share)
    stops += more

    kept = [c for c in (frame.columns if frame is not None else []) if c != "ts"] or ["(none: stopped before normalisation)"]
    write(rep_paths["schema"], render_schema(dataset=cfg["dataset"], files=files, bar_file=ident, profile=profile, specials=specials,
                                             mapping=mapping, clock_ev=clock.evidence, vwap_checks=vwap_checks, kept=kept))
    write(rep_paths["quality"], render_quality(dataset=cfg["dataset"], bar_file=ident, clock=clock, rows=rows, stops=stops,
                                               extra=extra, generic_md=generic_md, max_removed_share=max_share))
    if roll is not None:
        period = (str(bars.index.min()), str(bars.index.max()))
        write(rep_paths["rollover"], render_rolls(dataset=cfg["dataset"], bar_file=ident, roll=roll, period=period))
    summary = {"dataset": cfg["dataset"], "file": ident, "files": files, "mapping": mapping, "profile": profile,
               "specials": specials, "clock": {"status": clock.status, "tz_in": clock.tz_in, "label": clock.label,
                                                "kind": clock.kind, "structure": clock.structure, "reasons": clock.reasons,
                                                "evidence": clock.evidence},
               "normalise": norm_log, "extra": extra, "generic": chk, "rolls": roll, "findings": rows, "stops": stops,
               "vwap_checks": vwap_checks}
    write(rep_paths["json"], json.dumps(summary, indent=2, default=jdefault))

    if stops:
        print("STOP:")
        for s in stops:
            print(f"  - {s}")
        return 2
    print("PASS: the file can be registered" + ("" if args.register else " (rerun with --register)"))
    if not args.register:
        return 0

    # ---- registration --------------------------------------------------------------------
    for line in register_sets(cfg=cfg, frame=frame, clock=clock, roll=roll, ident=ident, bar_paths=bar_paths,
                              removed=norm_log["removed_total"], tpl=tpl, symbol=inst.symbol):
        print(line)
    print("next: python scripts/data_quality.py, then the DATA_QUALITY journal notes (scripts/journal_note.py)")
    return 0


def register_sets(*, cfg: dict, frame: pd.DataFrame, clock, roll: dict, ident: dict, bar_paths: list[Path], removed: int,
                  tpl, symbol: str, manifest: Path | None = None, processed_dir: Path | None = None) -> list[str]:
    """Write the canonical input files of the research dataset and the blind pilot, register
    both in the manifest and prepare them. Returns progress lines."""
    con = roll["conclusion"]
    out_cols = ["ts", "open", "high", "low", "close", "volume"] + [c for c in ("n_trades", "bid_volume", "ask_volume", "contract")
                                                                   if c in frame.columns]
    src = (f"Kaggle {cfg['dataset']}, file {ident['name']} (sha256 {ident['sha256'][:16]}); timestamps read as "
           f"{clock.tz_in} {clock.label}-labelled (inferred from the CME session structure, outputs/nq_data_quality_report.md); "
           f"continuous front month, {roll['classification']} (docs/KAGGLE_NQ_ROLLOVER_ASSESSMENT.md); "
           "converted to UTC bar-open times by scripts/assess_kaggle_nq.py")
    td = trading_dates(pd.DatetimeIndex(frame["ts"]).tz_convert(tpl.timezone), tpl)
    first_td = pd.Timestamp(cfg["pilot"]["start"]) if cfg["pilot"].get("start") else td.min()
    pilot_end = first_td + pd.DateOffset(months=int(cfg["pilot"]["months"]))
    pilot_mask = np.asarray((td >= first_td) & (td < pilot_end))
    sets = [(cfg["dataset_id"], frame, "research", src),
            (cfg["pilot"]["id"], frame[pilot_mask], "pipeline_check",
             src + f"; PILOT: trading dates {first_td.date()} to {(pilot_end - pd.Timedelta(days=1)).date()} only")]
    lines = []
    for ds_id, fr, purpose, source in sets:
        d = RESEARCH_ROOT / cfg["derived_dir"] / ds_id
        d.mkdir(parents=True, exist_ok=True)
        pq = d / "bars_1m.parquet"
        fr[out_cols].to_parquet(pq, index=False)
        (d / "bars_1m.meta.json").write_text(json.dumps({
            "dataset_id": ds_id, "source_files": [file_identity(p) for p in bar_paths], "rows": int(len(fr)),
            "clock": {"tz_in": clock.tz_in, "label": clock.label, "kind": clock.kind}, "removed_impossible_bars": removed,
            "roll_classification": roll["classification"], "adjustment": con["adjustment"],
            "written_by": "scripts/assess_kaggle_nq.py"}, indent=2, default=jdefault))
        row = {"id": ds_id, "instrument": symbol, "path": show(pq), "source": source, "tz_in": "UTC", "label": "open",
               "bar_minutes": int(clock.bar_minutes), "adjustment": con["adjustment"], "on_conflict": "abort", "purpose": purpose}
        new = register(row, manifest)
        lines.append(f"{ds_id}: {'registered' if new else 'already registered'} ({len(fr):,} rows, purpose {purpose})")
        pq_out, meta = prepare(get_entry(ds_id, manifest), **({"out_dir": processed_dir} if processed_dir else {}))
        lines.append(f"  prepared {show(pq_out)}: {meta['rows_processed']:,} rows, identical duplicates dropped "
                     f"{meta['identical_duplicates_dropped']}, conflicting {meta['conflicting_duplicate_timestamps']}")
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
