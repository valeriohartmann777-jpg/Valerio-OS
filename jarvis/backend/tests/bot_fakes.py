"""A MetaTrader 5 install on a Mac, faked: folders, EAs, and a strategy tester
that turns an EA's source into deals (its "edge" is written in a comment)."""

from __future__ import annotations

import random
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from jarvis.bots.mql import EXPORT_MARKER
from jarvis.bots.mt5 import CompileResult, Mt5Paths, RunResult, RunSpec

GOLD_EA = """//+------------------------------------------------------------------+
//| GoldScalper.mq5                                                  |
//+------------------------------------------------------------------+
#property copyright "Valerio"
#include <Trade\\Trade.mqh>
#include "GoldTools.mqh"

input group "Risk"
input double InpLots = 0.10;            // Lot size
input int    InpStopPoints=300; // Stop loss in points
sinput int   InpMagic = 4242;           // Magic number
input ENUM_TIMEFRAMES InpTrendTF = PERIOD_H1; // Trend timeframe
/* input int InpDisabled = 5; */
input bool   InpUseSession = true;

// EDGE=0.00
CTrade trade;

int OnInit() { return(INIT_SUCCEEDED); }
void OnTick()
  {
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
  }
"""


def mac_install(home: Path, source: str = GOLD_EA) -> Path:
    """MetaTrader 5.app, its Wine prefix and the user's MQL5 folder under ``home``."""
    app = home / "Applications" / "MetaTrader 5.app"
    wine = app / "Contents" / "SharedSupport" / "metatrader5" / "wine" / "bin" / "wine64"
    wine.parent.mkdir(parents=True)
    wine.write_text("#!/bin/sh\n")
    prefix = home / "Library" / "Application Support" / "net.metaquotes.wine.metatrader5"
    terminal = prefix / "drive_c" / "Program Files" / "MetaTrader 5"
    (terminal / "Bases" / "Broker" / "history").mkdir(parents=True)
    (terminal / "Bases" / "Broker" / "history" / "XAUUSD.hcc").write_bytes(b"x" * 100)
    for exe in ("terminal64.exe", "MetaEditor64.exe"):
        (terminal / exe).write_bytes(b"MZ")
    (terminal / "config").mkdir()
    (terminal / "config" / "accounts.dat").write_bytes(b"saved logins")
    experts = terminal / "MQL5" / "Experts"
    (experts / "Examples" / "MACD").mkdir(parents=True)
    (experts / "Examples" / "MACD" / "MACD Sample.mq5").write_text("// sample")
    (experts / "Gold").mkdir()
    (experts / "Gold" / "GoldScalper.mq5").write_text(source, encoding="utf-8")
    (experts / "Gold" / "GoldTools.mqh").write_text("// tools\n", encoding="utf-8")
    (terminal / "MQL5" / "Include" / "Trade").mkdir(parents=True)
    (terminal / "MQL5" / "Include" / "Trade" / "Trade.mqh").write_text("// trade")
    common = prefix / "drive_c" / "users" / "valerio" / "AppData" / "Roaming" / "MetaQuotes"
    (common / "Terminal" / "Common" / "Files").mkdir(parents=True)
    return home


def deals_csv(edge: float, start: date, end: date, *, lots: float = 0.1, seed: int = 1) -> str:
    """One trade per weekday; mean result ``edge`` * 100 USD, spread 100 USD."""
    rng = random.Random(seed)
    lines = ["time,type,entry,volume,price,profit,commission,swap,symbol,position"]
    t0 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
    lines.append(f"{t0},2,0,0.00,0.00000,10000.00,0.00,0.00,,0")
    day, position = start, 1
    scale = lots / 0.1
    while day < end:
        if day.weekday() < 5:
            opened = int(datetime(day.year, day.month, day.day, 9, tzinfo=UTC).timestamp())
            pnl = rng.gauss(edge * 100, 100) * scale
            lines.append(f"{opened},0,0,{lots:.2f},2000.00000,0.00,-2.00,0.00,XAUUSD,{position}")
            lines.append(
                f"{opened + 3600},1,1,{lots:.2f},2001.00000,{pnl:.2f},-2.00,0.00,XAUUSD,{position}"
            )
            position += 1
        day += timedelta(days=1)
    return "\n".join(lines) + "\n"


class FakeTester:
    """The strategy tester: compiles anything without SYNTAX_ERROR and backtests
    by reading ``// EDGE=x`` from the version it is given."""

    def __init__(self, paths: Mt5Paths) -> None:
        self.paths = paths
        self.compiled: list[str] = []
        self.specs: list[RunSpec] = []
        self.set_up_calls = 0

    @property
    def ready(self) -> bool:
        return self.paths.test_dir is not None

    def experts_dir(self) -> Path:
        assert self.paths.test_dir is not None
        return self.paths.test_dir / "MQL5" / "Experts"

    def set_up(self) -> None:
        self.set_up_calls += 1

    def sync(self) -> None:
        pass

    def user_terminal_running(self) -> bool:
        return False

    async def open_test_terminal(self) -> None:
        pass

    async def compile(self, source: Path) -> CompileResult:
        text = source.read_text(encoding="utf-8-sig")  # noqa: ASYNC240 - a test fake
        assert EXPORT_MARKER in text, "versions are compiled with the deal export"
        self.compiled.append(source.name)
        if "SYNTAX_ERROR" in text:
            return CompileResult(False, [f"{source.name}(20,4) : error 256: undeclared"], 0, "")
        source.with_suffix(".ex5").write_bytes(b"EX5")
        return CompileResult(True, [], 0, "")

    async def backtest(self, spec: RunSpec) -> RunResult:
        self.specs.append(spec)
        path = self.experts_dir() / (spec.expert.replace("\\", "/") + ".mq5")
        text = path.read_text(encoding="utf-8-sig")
        match = re.search(r"EDGE=(-?[\d.]+)", text)
        edge = float(match.group(1)) if match else 0.0
        lots = float(spec.inputs.get("InpLots", "0.1"))
        return RunResult(deals_csv(edge, spec.start, spec.end, lots=lots), 1.0)
