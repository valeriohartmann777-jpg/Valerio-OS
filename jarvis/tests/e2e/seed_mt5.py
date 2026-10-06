"""Seeds a fake "MetaTrader 5 for Mac" for the E2E test and prints a config folder
that points JARVIS at it.

The app's ``wine64`` is replaced by a small Python script that plays
MetaEditor (writes a compile log and an .ex5) and the strategy tester (reads
the tester .ini, finds the EA's ``// EDGE=x`` and the file its export writes,
and writes deals to Common\\Files) — so the E2E drives JARVIS's real
MetaTrader code: processes, paths, configuration files and deal files.

Usage: python seed_mt5.py <folder>   → prints the config folder to use
"""

from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from tests.bot_fakes import GOLD_EA, mac_install

WINE = r'''#!/usr/bin/env python3
"""Fake Wine for JARVIS's E2E test: plays MetaEditor and the MT5 strategy tester."""
import os, random, re, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

prefix = Path(os.environ["WINEPREFIX"])


def unix(path):
    return prefix / "drive_c" / Path(*path[3:].split("\\"))


program, args = Path(sys.argv[1]).name, sys.argv[2:]
if program == "MetaEditor64.exe":
    source = unix(next(a[9:] for a in args if a.startswith("/compile:")))
    log = unix(next(a[5:] for a in args if a.startswith("/log:")))
    source.with_suffix(".ex5").write_bytes(b"EX5")
    log.write_bytes("Result: 0 errors, 0 warnings\r\n".encode("utf-16"))
    sys.exit(0)
config = next((a[8:] for a in args if a.startswith("/config:")), None)
if program != "terminal64.exe" or config is None:
    sys.exit(0)  # the test terminal opened with its window: nothing to do here
ini = dict(
    line.split("=", 1) for line in unix(config).read_text().splitlines() if "=" in line
)
expert = Path(sys.argv[1]).parent / "MQL5" / "Experts" / Path(*ini["Expert"].split("\\"))
text = expert.with_suffix(".mq5").read_text(encoding="utf-8-sig")
edge = float(re.search(r"EDGE=(-?[\d.]+)", text).group(1))
export = re.search(r'FileOpen\("([^"]+)"', text).group(1)
start = datetime.strptime(ini["FromDate"], "%Y.%m.%d").date()
end = datetime.strptime(ini["ToDate"], "%Y.%m.%d").date()
rng = random.Random(7)
rows = ["time,type,entry,volume,price,profit,commission,swap,symbol,position"]
t0 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
rows.append(f"{t0},2,0,0.00,0.00000,{float(ini['Deposit']):.2f},0.00,0.00,,0")
day, position = start, 1
while day < end:
    if day.weekday() < 5:
        opened = int(datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc).timestamp())
        pnl = rng.gauss(edge * 100, 100)
        rows.append(f"{opened},0,0,0.10,2000.00000,0.00,-2.00,0.00,XAUUSD,{position}")
        rows.append(f"{opened + 3600},1,1,0.10,2001.00000,{pnl:.2f},-2.00,0.00,XAUUSD,{position}")
        position += 1
    day += timedelta(days=1)
common = next(prefix.glob("drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/Common/Files"))
(common / export).write_text("\n".join(rows) + "\n")
'''


def main(folder: Path) -> None:
    home = mac_install(folder / "home", GOLD_EA.replace("EDGE=0.00", "EDGE=0.25"))
    app = home / "Applications" / "MetaTrader 5.app"
    wine = next(app.glob("Contents/SharedSupport/**/bin/wine64"))
    wine.write_text(WINE)
    wine.chmod(wine.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    config = folder / "config"
    shutil.copytree(BACKEND.parent / "config", config)
    prefix = home / "Library" / "Application Support" / "net.metaquotes.wine.metatrader5"
    bots = (config / "bots.yaml").read_text()
    bots = bots.replace('  app: ""', f'  app: "{app}"').replace('  prefix: ""', f'  prefix: "{prefix}"')
    (config / "bots.yaml").write_text(bots)
    print(config)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
