"""Dataset manifest (``configs/datasets.yaml``) and the processed-data store.

Raw vendor files are converted once into the canonical bar frame and written to
``data/processed/<id>.parquet`` with a ``<id>.meta.json`` that records every
normalisation decision. Research code loads data only through :func:`load_processed`,
so each result can name the exact file hash it came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import CONFIG_DIR, RESEARCH_ROOT, load_yaml
from .loader import file_sha256, load_bars

PROCESSED_DIR = RESEARCH_ROOT / "data" / "processed"
ADJUSTMENTS = ("unadjusted", "difference", "ratio", "per_contract", "unknown")
CONFLICT_POLICIES = ("abort", "first", "last")


@dataclass(frozen=True)
class DatasetEntry:
    id: str
    instrument: str
    path: str
    source: str
    tz_in: str
    label: str
    bar_minutes: int
    adjustment: str = "unknown"
    on_conflict: str = "abort"
    column_map: dict[str, str] = field(default_factory=dict)
    read_kwargs: dict[str, Any] = field(default_factory=dict)
    ts_format: str | None = None
    price_scale: float = 1.0

    def __post_init__(self) -> None:
        if self.label not in ("open", "close"):
            raise ValueError(f"{self.id}: label must be 'open' or 'close'")
        if self.adjustment not in ADJUSTMENTS:
            raise ValueError(f"{self.id}: adjustment must be one of {ADJUSTMENTS}")
        if self.on_conflict not in CONFLICT_POLICIES:
            raise ValueError(f"{self.id}: on_conflict must be one of {CONFLICT_POLICIES}")

    @property
    def raw_path(self) -> Path:
        p = Path(self.path)
        return p if p.is_absolute() else RESEARCH_ROOT / p


def load_manifest(path: str | Path | None = None) -> list[DatasetEntry]:
    """All registered datasets (empty list while no data has been supplied)."""
    rows = load_yaml(path or CONFIG_DIR / "datasets.yaml").get("datasets") or []
    out = []
    for r in rows:
        missing = [k for k in ("id", "instrument", "path", "source", "tz_in", "label", "bar_minutes") if k not in r]
        if missing:
            raise ValueError(f"dataset entry {r.get('id', '?')} lacks required keys {missing}")
        out.append(DatasetEntry(**{**r, "bar_minutes": int(r["bar_minutes"])}))
    ids = [e.id for e in out]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate dataset ids in manifest")
    return out


def get_entry(dataset_id: str, path: str | Path | None = None) -> DatasetEntry:
    for e in load_manifest(path):
        if e.id == dataset_id:
            return e
    raise KeyError(f"dataset {dataset_id!r} is not in the manifest")


def resolve_duplicates(bars: pd.DataFrame, policy: str) -> tuple[pd.DataFrame, dict[str, int]]:
    """Drop identical duplicate rows; handle conflicting ones according to ``policy``."""
    cols = [c for c in ("open", "high", "low", "close", "volume") if c in bars.columns]
    frame = bars[cols].reset_index()
    identical = frame.duplicated(keep="first").to_numpy()
    out = bars[~identical]
    conflicting = out.index.duplicated(keep=False)
    n_conflict_ts = int(pd.Index(out.index[conflicting]).nunique())
    if n_conflict_ts and policy == "abort":
        examples = [str(t) for t in pd.Index(out.index[conflicting]).unique()[:5]]
        raise ValueError(
            f"{n_conflict_ts} timestamps have conflicting duplicate bars (e.g. {examples}); "
            "inspect the file and set on_conflict to 'first' or 'last' in datasets.yaml"
        )
    if n_conflict_ts:
        out = out[~out.index.duplicated(keep=policy)]
    return out, {"identical_duplicates_dropped": int(identical.sum()), "conflicting_duplicate_timestamps": n_conflict_ts}


def prepare(entry: DatasetEntry, out_dir: Path = PROCESSED_DIR) -> tuple[Path, dict[str, Any]]:
    """Convert one raw file into the canonical parquet + meta.json. Never fills or smooths."""
    bars, meta = load_bars(
        entry.raw_path,
        tz_in=entry.tz_in,
        label=entry.label,  # type: ignore[arg-type]
        bar_minutes=entry.bar_minutes,
        column_map=entry.column_map or None,
        price_scale=entry.price_scale,
        ts_format=entry.ts_format,
        read_kwargs=dict(entry.read_kwargs) or None,
    )
    if meta["bar_minutes_inferred"] != entry.bar_minutes:
        meta["warning_bar_minutes"] = f"manifest says {entry.bar_minutes} min, modal spacing is {meta['bar_minutes_inferred']} min"
    bars, dup = resolve_duplicates(bars, entry.on_conflict)
    out_dir.mkdir(parents=True, exist_ok=True)
    pq = out_dir / f"{entry.id}.parquet"
    bars.to_parquet(pq)
    meta.update(dup)
    meta.update({
        "dataset_id": entry.id,
        "instrument": entry.instrument,
        "source": entry.source,
        "adjustment": entry.adjustment,
        "on_conflict": entry.on_conflict,
        "rows_processed": int(len(bars)),
        "processed_sha256": file_sha256(pq),
    })
    (out_dir / f"{entry.id}.meta.json").write_text(json.dumps(meta, indent=2, default=str))
    return pq, meta


def load_processed(dataset_id: str, out_dir: Path = PROCESSED_DIR) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Canonical bars and their meta for a prepared dataset."""
    pq = out_dir / f"{dataset_id}.parquet"
    if not pq.exists():
        raise FileNotFoundError(f"{pq} missing; run scripts/prepare_data.py first")
    meta = json.loads((out_dir / f"{dataset_id}.meta.json").read_text())
    bars = pd.read_parquet(pq)
    if file_sha256(pq) != meta["processed_sha256"]:
        raise RuntimeError(f"{pq} changed since it was prepared; re-run prepare_data.py")
    return bars, meta
