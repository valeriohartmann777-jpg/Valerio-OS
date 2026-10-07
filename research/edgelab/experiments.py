"""Experiment registry and reproducible run directories.

Every experiment gets an ID (A### auction, B### ICT, C### price action), a
directory ``results/<family>/<ID>/`` holding config.yaml, metrics.json,
trades.parquet, equity.csv, charts/ and notes.md, and one row in
``results/experiments.parquet`` (mirrored to experiments.csv for readable diffs).
The row records the parameter hash, data hash, seeds and git commit.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .config import RESEARCH_ROOT, RESULTS_DIR

FAMILY_DIRS = {"A": "auction", "B": "ict", "C": "price_action", "X": "comparison"}
STATUSES = ("EXPLORATORY", "VALIDATION", "FROZEN", "FINAL_TEST", "REJECTED")
REGISTRY_COLUMNS = [
    "experiment_id", "family", "candidate_name", "version", "instrument", "timeframe",
    "development_period", "validation_period", "test_period", "parameter_hash", "trade_count",
    "win_rate", "expectancy_R", "profit_factor", "sharpe", "max_drawdown_R", "cost_model",
    "notes", "status", "data_sha256", "git_commit", "seed", "created_utc", "evidence_class",
]


def param_hash(params: dict[str, Any]) -> str:
    """Stable 12-hex hash of a parameter dict (sorted keys, JSON canonical form)."""
    blob = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def git_commit(root: Path = RESEARCH_ROOT) -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


@dataclass
class ExperimentSpec:
    experiment_id: str
    candidate_name: str
    version: str
    instrument: str
    timeframe: str
    params: dict[str, Any]
    cost_model: str
    status: str = "EXPLORATORY"
    development_period: str = ""
    validation_period: str = ""
    test_period: str = ""
    data_sha256: str = ""
    seed: int = 0
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.experiment_id[:1] not in FAMILY_DIRS:
            raise ValueError("experiment_id must start with A, B, C or X")
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")

    @property
    def family(self) -> str:
        return FAMILY_DIRS[self.experiment_id[0]]

    @property
    def evidence_class(self) -> str:
        return "PROXY" if self.instrument.upper().startswith(("BTC", "ETH")) else "CME"


def run_dir(spec: ExperimentSpec, results_root: Path = RESULTS_DIR) -> Path:
    return results_root / spec.family / spec.experiment_id


def _jsonable(v: Any) -> Any:
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    if isinstance(v, float) and (np.isinf(v) or np.isnan(v)):
        return str(v)
    if isinstance(v, (pd.Timestamp, dt.date)):
        return str(v)
    return v


def save_run(
    spec: ExperimentSpec,
    trades: pd.DataFrame,
    metrics: dict[str, Any],
    *,
    notes_md: str = "",
    results_root: Path = RESULTS_DIR,
    extra_tables: dict[str, pd.DataFrame] | None = None,
) -> Path:
    """Write the run directory and append the registry row. Returns the directory."""
    d = run_dir(spec, results_root)
    (d / "charts").mkdir(parents=True, exist_ok=True)
    commit = git_commit()
    cfg = {**asdict(spec), "parameter_hash": param_hash(spec.params), "git_commit": commit, "evidence_class": spec.evidence_class}
    (d / "config.yaml").write_text(yaml.safe_dump(json.loads(json.dumps(cfg, default=str)), sort_keys=False))
    (d / "metrics.json").write_text(json.dumps({k: _jsonable(v) for k, v in metrics.items()}, indent=2, default=str))
    if len(trades):
        trades.to_parquet(d / "trades.parquet", index=False)
        eq = pd.DataFrame({"exit_ts": trades["exit_ts"], "R_net": trades["R_net"], "equity_R": trades["R_net"].cumsum()})
        eq.to_csv(d / "equity.csv", index=False)
    for name, tbl in (extra_tables or {}).items():
        tbl.to_csv(d / f"{name}.csv")
    (d / "notes.md").write_text(notes_md or f"# {spec.experiment_id} {spec.candidate_name} {spec.version}\n")
    append_registry(spec, metrics, commit, results_root)
    return d


def append_registry(spec: ExperimentSpec, metrics: dict[str, Any], commit: str, results_root: Path = RESULTS_DIR) -> pd.DataFrame:
    row = {
        "experiment_id": spec.experiment_id,
        "family": spec.family,
        "candidate_name": spec.candidate_name,
        "version": spec.version,
        "instrument": spec.instrument,
        "timeframe": spec.timeframe,
        "development_period": spec.development_period,
        "validation_period": spec.validation_period,
        "test_period": spec.test_period,
        "parameter_hash": param_hash(spec.params),
        "trade_count": metrics.get("trades", 0),
        "win_rate": metrics.get("win_rate", np.nan),
        "expectancy_R": metrics.get("expectancy_R", np.nan),
        "profit_factor": metrics.get("profit_factor", np.nan),
        "sharpe": metrics.get("sharpe_daily_ann", np.nan),
        "max_drawdown_R": metrics.get("max_drawdown_R", np.nan),
        "cost_model": spec.cost_model,
        "notes": spec.notes,
        "status": spec.status,
        "data_sha256": spec.data_sha256,
        "git_commit": commit,
        "seed": spec.seed,
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "evidence_class": spec.evidence_class,
    }
    path = results_root / "experiments.parquet"
    reg = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=REGISTRY_COLUMNS)
    reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    for col in ("trade_count", "seed"):
        reg[col] = pd.to_numeric(reg[col], errors="coerce")
    results_root.mkdir(parents=True, exist_ok=True)
    reg.to_parquet(path, index=False)
    reg.to_csv(results_root / "experiments.csv", index=False)
    return reg


def load_registry(results_root: Path = RESULTS_DIR) -> pd.DataFrame:
    path = results_root / "experiments.parquet"
    return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=REGISTRY_COLUMNS)


def next_experiment_id(prefix: str, results_root: Path = RESULTS_DIR, journal_path: Path | None = None) -> str:
    """Next free ID of a family: above every ID in the registry AND in the journal (the
    event studies A001-A015, B001-B018, C001-C018 hold their IDs in the journal)."""
    from .journal import JOURNAL, entries

    reg = load_registry(results_root)
    ids = list(reg.get("experiment_id", pd.Series(dtype=str)).astype(str)) + entries(journal_path or JOURNAL)
    nums = [int(x[1:4]) for x in ids if x.startswith(prefix) and x[1:4].isdigit()]
    return f"{prefix}{(max(nums) + 1) if nums else 1:03d}"
