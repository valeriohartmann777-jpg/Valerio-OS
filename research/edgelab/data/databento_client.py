"""Databento Historical API: key handling, cost estimates, chunked raw downloads.

Data infrastructure only; nothing here knows about strategies or studies.

Safety rules
------------
* The API key comes from the environment variable ``DATABENTO_API_KEY`` and from nowhere
  else. It is never printed, logged, written to a metadata file or passed to anything
  but the Databento client.
* Every download is preceded by a cost estimate from the metadata API. When the estimate
  of the files still missing exceeds ``safety.max_auto_download_cost_usd``, the download
  aborts unless the caller confirms an amount at least as large as the estimate.
* Raw files are written once. An existing chunk is verified against its metadata and
  skipped (it is never bought twice) and never overwritten.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from ..config import CONFIG_DIR, RESEARCH_ROOT, load_yaml
from .loader import file_sha256

ENV_KEY = "DATABENTO_API_KEY"
CONFIG_PATH = CONFIG_DIR / "databento.yaml"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

KEY_HELP = f"""{ENV_KEY} is not set, so no Databento request can be made.

Set it in the shell that runs the scripts (the key is never written to any file):
  macOS / Linux:       export {ENV_KEY}="db-..."
  Windows PowerShell:  $env:{ENV_KEY}="db-..."
In a Claude Code cloud session, add {ENV_KEY} as an environment variable of the cloud
environment in the project settings; a new session picks it up.
Never commit the key, put it in a config file or paste it into a chat."""


class MissingApiKey(RuntimeError):
    """The environment holds no Databento API key."""


class CostLimitExceeded(RuntimeError):
    """A download's cost estimate is above the configured safety limit."""


def load_databento_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg = load_yaml(path or CONFIG_PATH)
    for key in ("dataset", "schema", "definition_schema", "stype_in", "products", "raw_dir", "processed_dir", "safety"):
        if key not in cfg:
            raise ValueError(f"databento config lacks {key!r}")
    return cfg


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else RESEARCH_ROOT / p


def api_key() -> str:
    """The key from the environment. Raises :class:`MissingApiKey` with set-up instructions."""
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        raise MissingApiKey(KEY_HELP)
    return key


def make_client():
    """A Databento Historical client authenticated from the environment."""
    import databento as db

    return db.Historical(key=api_key())


def redact(text: str) -> str:
    """``text`` with the API key (if one is set) replaced, for error messages and logs."""
    key = os.environ.get(ENV_KEY, "").strip()
    return text.replace(key, f"<{ENV_KEY}>") if key else text


def request_error(exc: BaseException) -> str:
    """One-line description of a failed Databento request, never containing the key."""
    msg = redact(f"{type(exc).__name__}: {exc}")
    if "403" in msg or "407" in msg or "proxy" in msg.lower() or "tunnel" in msg.lower():
        msg += ("\nThe network policy of this environment blocks the Databento host (hist.databento.com). "
                "Allow it in the environment's network settings, or run the script on a machine with access.")
    elif "401" in msg or "auth" in msg.lower():
        msg += f"\nDatabento rejected the key in {ENV_KEY}; check that it is current and has no extra characters."
    return msg


def library_versions() -> dict[str, str]:
    out = {}
    for pkg in ("databento", "databento-dbn"):
        try:
            out[pkg] = importlib_metadata.version(pkg)
        except importlib_metadata.PackageNotFoundError:
            out[pkg] = "not installed"
    return out


# ---------------------------------------------------------------------------------------
# requests and estimates
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Request:
    """One historical request; ``end`` is exclusive, both are dates (UTC midnight)."""

    dataset: str
    schema: str
    symbols: tuple[str, ...]
    stype_in: str
    start: str
    end: str

    def __post_init__(self) -> None:
        for v in (self.start, self.end):
            if not _DATE.match(v):
                raise ValueError(f"dates must be YYYY-MM-DD, got {v!r}")
        if pd.Timestamp(self.end) <= pd.Timestamp(self.start):
            raise ValueError("end must be after start")
        if not self.symbols:
            raise ValueError("no symbols")

    def kwargs(self) -> dict[str, Any]:
        return {"dataset": self.dataset, "schema": self.schema, "symbols": list(self.symbols),
                "stype_in": self.stype_in, "start": self.start, "end": self.end}


def parse_parent_symbol(symbol: str) -> tuple[str, str]:
    """``"ES.FUT"`` -> ``("ES", "FUT")``. Only futures parents are accepted."""
    m = re.fullmatch(r"([A-Z0-9]{1,6})\.(FUT)", symbol.strip())
    if not m:
        raise ValueError(f"{symbol!r} is not a futures parent symbol like 'ES.FUT'")
    return m.group(1), m.group(2)


def product_for_parent(cfg: dict[str, Any], parent: str) -> str:
    for product, p in cfg["products"].items():
        if p["parent"] == parent:
            return product
    raise KeyError(f"{parent!r} is not configured in configs/databento.yaml products")


def month_chunks(start: str, end: str) -> list[tuple[str, str]]:
    """Split ``[start, end)`` at calendar-month starts."""
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    if b <= a:
        raise ValueError("end must be after start")
    out = []
    cur = a
    while cur < b:
        nxt = cur + pd.offsets.MonthBegin(1)
        out.append((cur.strftime("%Y-%m-%d"), min(nxt, b).strftime("%Y-%m-%d")))
        cur = nxt
    return out


def estimate(client, req: Request, *, records: bool = False) -> dict[str, Any]:
    """Cost (USD) and billable size (bytes, uncompressed) from the metadata API."""
    kw = req.kwargs()
    out: dict[str, Any] = {
        "cost_usd": float(client.metadata.get_cost(**kw)),
        "billable_bytes": int(client.metadata.get_billable_size(**kw)),
    }
    if records:
        out["records"] = int(client.metadata.get_record_count(**kw))
    return out


def dataset_range(client, dataset: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First and last available time of ``dataset`` (UTC)."""
    rng = client.metadata.get_dataset_range(dataset=dataset)
    return pd.Timestamp(rng["start"]).tz_convert("UTC"), pd.Timestamp(rng["end"]).tz_convert("UTC")


def cost_limit_message(cost: float, limit: float) -> str:
    return (f"Estimated Databento cost is ${cost:,.2f}, above configured safety limit of ${limit:,.2f}. "
            "Explicit confirmation/config change required.")


def check_cost(cost: float, limit: float, confirmed: float | None = None) -> None:
    """Pass when ``cost <= limit`` or the caller confirmed at least ``cost``; raise otherwise."""
    if cost <= limit + 1e-9:
        return
    if confirmed is not None and confirmed + 1e-9 >= cost:
        return
    raise CostLimitExceeded(cost_limit_message(cost, limit))


# ---------------------------------------------------------------------------------------
# raw files
# ---------------------------------------------------------------------------------------
def raw_file(raw_dir: Path, product: str, req: Request) -> Path:
    sym = "+".join(req.symbols)
    return raw_dir / product / req.schema / f"{req.dataset}_{req.schema}_{sym}_{req.start}_{req.end}.dbn.zst"


def meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".json")


def verify_raw(path: Path) -> dict[str, Any] | None:
    """Metadata of a complete raw chunk, ``None`` when it is absent.

    A file whose hash no longer matches its metadata is an error: raw data is never
    silently replaced, so a person has to look at it.
    """
    mp = meta_path(path)
    if not path.exists() and not mp.exists():
        return None
    if path.exists() != mp.exists():
        raise RuntimeError(f"{path}: data file and metadata JSON must exist together; inspect it manually")
    meta = json.loads(mp.read_text())
    if meta.get("sha256") != file_sha256(path):
        raise RuntimeError(f"{path} does not match the SHA-256 in its metadata; raw files are never overwritten, inspect it")
    return meta


@dataclass
class ChunkTask:
    product: str
    request: Request
    path: Path
    exists: bool
    cost_usd: float = 0.0
    billable_bytes: int = 0


def plan_downloads(
    client, cfg: dict[str, Any], products: Iterable[str], schemas: Iterable[str], start: str, end: str,
    raw_dir: Path | None = None,
) -> list[ChunkTask]:
    """One task per product, schema and month; estimates only for chunks not on disk."""
    raw_dir = raw_dir or resolve(cfg["raw_dir"])
    tasks = []
    for product in products:
        parent = cfg["products"][product]["parent"]
        for schema in schemas:
            for a, b in month_chunks(start, end):
                req = Request(cfg["dataset"], schema, (parent,), cfg["stype_in"], a, b)
                path = raw_file(raw_dir, product, req)
                task = ChunkTask(product, req, path, exists=verify_raw(path) is not None)
                if not task.exists:
                    est = estimate(client, req)
                    task.cost_usd, task.billable_bytes = est["cost_usd"], est["billable_bytes"]
                tasks.append(task)
    return tasks


def dbn_summary(path: Path) -> tuple[int, dict[str, Any]]:
    """Record count and the DBN header of a file."""
    import databento as db

    store = db.DBNStore.from_file(path)
    n = 0
    for block in store.to_ndarray(count=2_000_000):
        n += len(block)
    md = store.metadata
    head = {
        "dataset": md.dataset, "schema": str(md.schema), "stype_in": str(md.stype_in), "stype_out": str(md.stype_out),
        "start_ns": int(md.start), "end_ns": None if md.end is None else int(md.end), "symbols": list(md.symbols),
        "partial": list(md.partial), "not_found": list(md.not_found), "n_symbol_mappings": len(md.mappings),
    }
    return n, head


def download_chunk(client, task: ChunkTask) -> dict[str, Any]:
    """Fetch one chunk to ``task.path`` and write its metadata JSON. Never overwrites."""
    path = task.path
    if path.exists() or meta_path(path).exists():
        raise FileExistsError(f"{path} exists; raw files are never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    if tmp.exists():
        tmp.unlink()  # an interrupted earlier attempt of this same chunk
    client.timeseries.get_range(**task.request.kwargs(), path=tmp)
    records, head = dbn_summary(tmp)
    meta = {
        "download_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "product": task.product,
        **task.request.kwargs(),
        "stype_out": "instrument_id",
        "request": task.request.kwargs(),
        "library_versions": library_versions(),
        "records": records,
        "file_bytes": tmp.stat().st_size,
        "sha256": file_sha256(tmp),
        "cost_estimate_usd": task.cost_usd,
        "billable_bytes_estimate": task.billable_bytes,
        "dbn_header": head,
    }
    os.replace(tmp, path)
    meta_path(path).write_text(json.dumps(meta, indent=2))
    return meta


def verified_chunks(cfg: dict[str, Any], product: str, schema: str, start: str, end: str,
                    raw_dir: Path | None = None) -> list[tuple[Path, dict[str, Any]]]:
    """The raw files covering ``[start, end)`` with their verified metadata; raises when one is missing."""
    raw_dir = raw_dir or resolve(cfg["raw_dir"])
    parent = cfg["products"][product]["parent"]
    out = []
    for a, b in month_chunks(start, end):
        path = raw_file(raw_dir, product, Request(cfg["dataset"], schema, (parent,), cfg["stype_in"], a, b))
        meta = verify_raw(path)
        if meta is None:
            raise FileNotFoundError(f"{path} missing: run scripts/databento_download.py first")
        out.append((path, meta))
    return out
