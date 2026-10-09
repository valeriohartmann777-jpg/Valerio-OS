"""Get the Kaggle NQ 1-minute file into data/raw/kaggle/nq/ without ever changing it.

Usage (from research/):
    python scripts/kaggle_download.py --check            # what is missing (network, CLI); downloads nothing
    python scripts/kaggle_download.py                    # download the public dataset with the Kaggle CLI
    python scripts/kaggle_download.py --from-file PATH   # import a ZIP or CSV downloaded by hand

The dataset is public: the Kaggle CLI (2.x) downloads it without a login. A Kaggle token
is used by the CLI when one is configured (KAGGLE_API_TOKEN, ~/.kaggle/access_token or
the legacy ~/.kaggle/kaggle.json); this script only reports WHICH source exists and never
reads, prints, logs or writes a token. The network must allow api.kaggle.com.

Files land in data/raw/kaggle/nq/ (never committed). An existing file is never
overwritten: identical content is skipped, different content stops the import. Archives
are extracted with checks against absolute or parent paths, links and archive bombs. A
<file>.meta.json beside each file records origin, time, size and SHA-256. Every run
appends what happened to reports/DATA_ACQUISITION_LOG.md.
Exit codes: 0 ok, 3 network blocked, 4 Kaggle refused the request, 5 import stopped, 1 other.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgelab.config import CONFIG_DIR, RESEARCH_ROOT, load_yaml  # noqa: E402
from edgelab.data.loader import file_sha256  # noqa: E402
from edgelab.data.vendor_bars import safe_name, sniff  # noqa: E402

LOG = RESEARCH_ROOT / "reports" / "DATA_ACQUISITION_LOG.md"
MAX_TOTAL_BYTES = 20 * 1024**3
MAX_RATIO = 200
TOKEN_ENV = ("KAGGLE_API_TOKEN", "KAGGLE_API_V1_TOKEN", "KAGGLE_KEY")


class ImportStop(RuntimeError):
    pass


def load_cfg() -> dict:
    return load_yaml(CONFIG_DIR / "kaggle.yaml")


def credential_sources() -> list[str]:
    """Names of configured Kaggle credential sources (never their values)."""
    found = [f"environment variable {k}" for k in TOKEN_ENV if os.environ.get(k)]
    home = Path(os.path.expanduser("~/.kaggle"))
    found += [f"~/.kaggle/{n}" for n in ("access_token", "access_token.txt", "kaggle.json") if (home / n).is_file()]
    return found


def secret_values() -> list[str]:
    return [v for k in TOKEN_ENV if (v := os.environ.get(k)) and len(v) >= 6]


def redact(text: str) -> str:
    for v in secret_values():
        text = text.replace(v, "***")
    return text


def classify_failure(text: str) -> str:
    t = text.lower()
    if "tunnel connection failed" in t or "proxyerror" in t or "unable to connect to proxy" in t or "max retries exceeded" in t \
            or "name or service not known" in t or "connection refused" in t:
        return "network"
    if "401" in t or "unauthorized" in t or "unauthenticated" in t or "403" in t or "forbidden" in t:
        return "auth"
    if "404" in t or "not found" in t:
        return "not_found"
    return "other"


def run_kaggle(args: list[str], timeout: int = 3600) -> tuple[int, str]:
    """The Kaggle CLI as a subprocess (``python -m kaggle`` so its anonymous mode applies)."""
    try:
        proc = subprocess.run([sys.executable, "-m", "kaggle", *args], capture_output=True, text=True, timeout=timeout,
                              cwd=RESEARCH_ROOT, env=os.environ.copy())
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    return proc.returncode, redact((proc.stdout or "") + (proc.stderr or ""))


def log(text: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = LOG.read_text(encoding="utf-8") if LOG.exists() else "# Data acquisition log\n"
    LOG.write_text(body.rstrip("\n") + f"\n\n**{stamp}, scripts/kaggle_download.py.** {redact(text)}\n", encoding="utf-8")


def safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Archive members that may be extracted; raises on anything unsafe."""
    out, total = [], 0
    for info in zf.infolist():
        name = info.filename
        if info.is_dir():
            continue
        p = PurePosixPath(name.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts or (p.parts and ":" in p.parts[0]):
            raise ImportStop(f"archive member {name!r} points outside the target directory")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ImportStop(f"archive member {name!r} is a symbolic link")
        if not all(safe_name(part) for part in p.parts):
            raise ImportStop(f"archive member {name!r} has an unexpected name")
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise ImportStop("archive expands beyond 20 GB")
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise ImportStop(f"archive member {name!r} has a compression ratio above {MAX_RATIO}")
        out.append(info)
    return out


def extract(archive: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=False)
    paths = []
    with zipfile.ZipFile(archive) as zf:
        for info in safe_members(zf):
            target = dest.joinpath(*PurePosixPath(info.filename.replace("\\", "/")).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "xb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            paths.append(target)
    return paths


def place(src: Path, raw_dir: Path, rel: Path, meta: dict) -> str:
    """Move ``src`` to ``raw_dir/rel`` unless it exists. Identical: skipped; different: stop."""
    target = raw_dir / rel
    sha = file_sha256(src)
    if target.exists():
        if file_sha256(target) == sha:
            return f"{rel} already present (identical, skipped)"
        raise ImportStop(f"{target} exists with different content; nothing was overwritten")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(target))
    meta = {**meta, "file": str(rel), "bytes": target.stat().st_size, "sha256": sha,
            "imported_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    target.with_name(target.name + ".meta.json").write_text(json.dumps(meta, indent=2))
    return f"{rel} ({meta['bytes']:,} bytes, sha256 {sha[:16]})"


def import_file(path: Path, raw_dir: Path, origin: dict) -> list[str]:
    """Copy a ZIP or table into ``raw_dir`` through a fresh staging directory."""
    path = Path(path)
    if not path.is_file():
        raise ImportStop(f"{path} is not a file")
    kind = sniff(path)
    sha = file_sha256(path)
    raw_dir.mkdir(parents=True, exist_ok=True)
    staging = raw_dir / f"_staging_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
    staging.mkdir()
    done = []
    try:
        if kind == "zip":
            name = path.name if safe_name(path.name) and path.name.lower().endswith(".zip") else f"upload_{sha[:12]}.zip"
            copy = staging / name
            shutil.copy2(path, copy)
            members = extract(copy, staging / "extracted")
            for m in members:
                rel = m.relative_to(staging / "extracted")
                done.append(place(m, raw_dir, rel, {**origin, "archive": name, "archive_sha256": sha, "member": str(rel)}))
            done.append(place(copy, raw_dir, Path(name), {**origin, "kind": "archive as downloaded, kept unchanged"}))
        elif kind in ("text", "parquet", "feather"):
            suffix = {"text": ".csv", "parquet": ".parquet", "feather": ".feather"}[kind]
            name = path.name if safe_name(path.name) and path.suffix else f"upload_{sha[:12]}{suffix}"
            copy = staging / name
            shutil.copy2(path, copy)
            done.append(place(copy, raw_dir, Path(name), origin))
        else:
            raise ImportStop(f"{path} is {kind}, not a ZIP archive or a table")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return done


def check(cfg: dict) -> int:
    rc, out = run_kaggle(["--version"], timeout=120)
    print(f"Kaggle CLI: {out.strip().splitlines()[-1] if out.strip() else 'not available'}")
    src = credential_sources()
    print(f"Kaggle credentials configured: {', '.join(src) if src else 'none (not needed for a public dataset)'}")
    rc, out = run_kaggle(["datasets", "files", cfg["dataset"]], timeout=300)
    if rc == 0:
        print(f"api.kaggle.com reachable; files of {cfg['dataset']}:\n{out.strip()}")
        return 0
    why = classify_failure(out)
    print(f"Kaggle not reachable ({why}): {out.strip().splitlines()[-1][:300] if out.strip() else rc}")
    if why == "network":
        print("The environment's network policy blocks api.kaggle.com. Allow it under Allowed domains, or attach the "
              "dataset ZIP and run --from-file.")
        return 3
    return 4 if why in ("auth", "not_found") else 1


def download(cfg: dict, raw_dir: Path) -> int:
    staging = raw_dir.parent / f"_download_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    staging.mkdir(parents=True)
    try:
        rc, out = run_kaggle(["datasets", "download", cfg["dataset"], "-p", str(staging)])
        zips = sorted(staging.glob("*.zip"))
        if rc != 0 or not zips:
            why = classify_failure(out)
            last = out.strip().splitlines()[-1][:300] if out.strip() else f"exit {rc}"
            log(f"Download of `{cfg['dataset']}` failed ({why}): {last}. Nothing was written to {cfg['raw_dir']}.")
            print(f"download failed ({why}): {last}")
            if why == "network":
                print("STOP: the network policy blocks api.kaggle.com. Allow it under Allowed domains (no Kaggle token is "
                      "needed for this public dataset), or download the ZIP in a browser and run "
                      "`python scripts/kaggle_download.py --from-file <zip>`.")
                return 3
            if why == "auth":
                print("STOP: Kaggle refused the request. If the dataset now needs a login, create a token at "
                      "https://www.kaggle.com/settings/api and set it as the environment variable KAGGLE_API_TOKEN "
                      "(never in a file of this repository).")
                return 4
            return 1
        origin = {"origin": "kaggle-cli", "dataset": cfg["dataset"], "downloaded_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        done = []
        for z in zips:
            done += import_file(z, raw_dir, origin)
    except ImportStop as exc:
        log(f"Import of `{cfg['dataset']}` stopped: {exc}")
        print(f"STOP: {exc}")
        return 5
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log(f"Downloaded `{cfg['dataset']}` with the Kaggle CLI into {cfg['raw_dir']}: " + "; ".join(done) + ".")
    print("\n".join(done))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="check CLI, credentials (names only) and network; download nothing")
    g.add_argument("--from-file", type=Path, help="import a ZIP or CSV downloaded by hand")
    args = ap.parse_args()
    cfg = load_cfg()
    raw_dir = RESEARCH_ROOT / cfg["raw_dir"]
    if args.check:
        return check(cfg)
    if args.from_file:
        origin = {"origin": "manual download, imported from a local file", "dataset": cfg["dataset"],
                  "source_path": str(args.from_file)}
        try:
            done = import_file(args.from_file, raw_dir, origin)
        except ImportStop as exc:
            log(f"Import of {args.from_file} stopped: {exc}")
            print(f"STOP: {exc}")
            return 5
        log(f"Imported `{cfg['dataset']}` from {args.from_file} into {cfg['raw_dir']}: " + "; ".join(done) + ".")
        print("\n".join(done))
        return 0
    return download(cfg, raw_dir)


if __name__ == "__main__":
    raise SystemExit(main())
