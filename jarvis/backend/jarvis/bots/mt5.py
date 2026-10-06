"""MetaTrader 5 on this computer: where it is, compiling, strategy-tester runs.

On a Mac, MetaTrader 5 is a Windows program running in Wine inside
``MetaTrader 5.app``; its drive C is
``~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c``.

JARVIS never touches the user's running MetaTrader (it may be trading). It
works with its own **test terminal**: a copy of the MetaTrader program folder
and of the user's MQL5 folder in ``drive_c/JARVIS/MT5``, started in
portable mode. A backtest is a run of that terminal with a configuration file
(``/config:``) that starts the strategy tester and closes the terminal when
done (``ShutdownTerminal=1``). Compiling uses MetaEditor's command line
(``/compile:``); success means "0 errors" in its log *and* a fresh ``.ex5``.

Every path and the Wine binary can be set in ``config/bots.yaml`` if the
automatic search doesn't find them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jarvis.settings import BotsSettings

log = logging.getLogger("jarvis.bots")

PREFIX_NAME = "net.metaquotes.wine.metatrader5"
# No spaces anywhere in JARVIS's own paths: they go through Wine on command lines.
TEST_FOLDER = ("JARVIS", "MT5")
MODELS = {"every_tick": 0, "ohlc_1m": 1, "open_prices": 2, "real_ticks": 4}
PERIODS = ("M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15", "M20", "M30",
           "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D1", "W1", "MN1")  # fmt: skip
# Folders not worth copying into the test terminal (history is downloaded again).
_SKIP_PROGRAM = {"Bases", "logs", "Tester", "MQL5", "Config", "config"}
_SKIP_MQL5 = {"Logs"}


class Mt5Error(Exception):
    pass


@dataclass(frozen=True)
class Mt5Paths:
    app: Path | None = None  # MetaTrader 5.app (macOS)
    wine: Path | None = None  # the app's Wine; None on Windows
    prefix: Path | None = None  # Wine prefix (contains drive_c)
    terminal_dir: Path | None = None  # the user's MetaTrader program folder
    data_dirs: tuple[Path, ...] = ()  # the user's MQL5 folders, most recent first
    common_files: Path | None = None  # Terminal/Common/Files (FILE_COMMON)

    @property
    def data_dir(self) -> Path | None:
        return self.data_dirs[0] if self.data_dirs else None

    @property
    def test_dir(self) -> Path | None:
        """JARVIS's own portable test terminal."""
        if self.prefix is not None:
            return self.prefix.joinpath("drive_c", *TEST_FOLDER)
        if self.terminal_dir is not None:  # Windows: next to the user's
            return self.terminal_dir.parent / "JARVIS-MT5"
        return None

    @property
    def runs_dir(self) -> Path | None:
        return self.test_dir.parent / "runs" if self.test_dir else None


def _rank(mql5: Path) -> tuple[bool, float]:
    """MQL5 folders with the user's own EAs first, then the most recently edited."""
    own = list_experts(mql5)
    return bool(own), max((p.stat().st_mtime for p in own), default=0.0)


def discover(settings: BotsSettings, home: Path | None = None) -> Mt5Paths:
    """Find MetaTrader 5 (macOS app with Wine, or Windows)."""
    home = home or Path.home()
    cfg = settings.mt5
    app = Path(cfg.app).expanduser() if cfg.app else None
    if app is None:
        for candidate in (Path("/Applications/MetaTrader 5.app"),
                          home / "Applications" / "MetaTrader 5.app"):  # fmt: skip
            if candidate.exists():
                app = candidate
                break
    wine = Path(cfg.wine).expanduser() if cfg.wine else None
    if wine is None and app is not None:
        found = sorted(app.glob("Contents/SharedSupport/**/bin/wine64")) or sorted(
            app.glob("Contents/SharedSupport/**/bin/wine")
        )
        wine = found[0] if found else None
    prefix = Path(cfg.prefix).expanduser() if cfg.prefix else None
    if prefix is None:
        candidate = home / "Library" / "Application Support" / PREFIX_NAME
        prefix = candidate if (candidate / "drive_c").exists() else None

    terminal_dir: Path | None = None
    roots: list[Path] = []
    if prefix is not None:
        terminal_dir = prefix / "drive_c" / "Program Files" / "MetaTrader 5"
        roots = [
            terminal_dir,
            *prefix.glob("drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/*"),
        ]
    elif sys.platform == "win32":
        program = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "MetaTrader 5"
        terminal_dir = program
        appdata = Path(os.environ.get("APPDATA", ""))
        roots = [program, *appdata.glob("MetaQuotes/Terminal/*")]
    if terminal_dir is not None and not (terminal_dir / "terminal64.exe").exists():
        terminal_dir = None

    if cfg.data_dir:
        data_dirs: tuple[Path, ...] = (Path(cfg.data_dir).expanduser(),)
    else:
        found_dirs = [r / "MQL5" for r in roots if (r / "MQL5" / "Experts").is_dir()]
        found_dirs = [d for d in found_dirs if TEST_FOLDER[0] not in d.parts]
        data_dirs = tuple(sorted(found_dirs, key=_rank, reverse=True))

    common = None
    if prefix is not None:
        hits = sorted(prefix.glob("drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/Common"))
        users = sorted(p for p in prefix.glob("drive_c/users/*") if p.name != "Public")
        if hits:
            common = hits[0] / "Files"
        elif users:
            common = users[0] / "AppData/Roaming/MetaQuotes/Terminal/Common/Files"
    elif sys.platform == "win32":
        common = Path(os.environ.get("APPDATA", "")) / "MetaQuotes/Terminal/Common/Files"
    return Mt5Paths(app, wine, prefix, terminal_dir, data_dirs, common)


def windows_path(path: Path, prefix: Path | None) -> str:
    """How the Windows program sees ``path`` (drive C inside the prefix, Z: = /)."""
    if prefix is None:
        return str(path)
    try:
        rel = path.relative_to(prefix / "drive_c")
        return "C:\\" + "\\".join(rel.parts)
    except ValueError:
        return "Z:" + str(path).replace("/", "\\")


def list_experts(mql5: Path) -> list[Path]:
    """The user's EAs with source code (MetaQuotes' examples and JARVIS's own excluded)."""
    experts = mql5 / "Experts"
    if not experts.is_dir():
        return []
    out = []
    for path in sorted(experts.rglob("*.mq5")):
        top = path.relative_to(experts).parts[0]
        if top in ("Examples", "Advisors", "JARVIS", "Free Robots"):
            continue
        out.append(path)
    return out


# The tester -------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSpec:
    expert: str  # under MQL5\Experts without extension, e.g. JARVIS\Gold\Gold_v3
    symbol: str
    period: str
    model: int
    start: date
    end: date
    deposit: float
    currency: str
    leverage: int
    export_file: str  # deals CSV in Common\Files
    report: str  # MetaTrader's own report, relative to the terminal folder
    inputs: dict[str, str] = field(default_factory=dict)


def config_ini(spec: RunSpec) -> str:
    lines = [
        "[Tester]",
        f"Expert={spec.expert}",
        f"Symbol={spec.symbol}",
        f"Period={spec.period}",
        f"Model={spec.model}",
        "Optimization=0",
        f"FromDate={spec.start:%Y.%m.%d}",
        f"ToDate={spec.end:%Y.%m.%d}",
        "ForwardMode=0",
        f"Deposit={spec.deposit:g}",
        f"Currency={spec.currency}",
        f"Leverage={spec.leverage}",
        "ExecutionMode=0",
        "Visual=0",
        f"Report={spec.report}",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
    ]
    if spec.inputs:
        lines.append("[TesterInputs]")
        for name, value in spec.inputs.items():
            if not re.fullmatch(r"[A-Za-z_]\w*", name) or any(c in value for c in "\r\n|"):
                raise Mt5Error(f"invalid input {name}={value!r}")
            lines.append(f"{name}={value}")
    return "\r\n".join(lines) + "\r\n"


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    errors: list[str]
    warnings: int
    log: str


_RESULT = re.compile(r"Result:\s*(\d+)\s+errors?,\s*(\d+)\s+warnings?", re.I)
_ERROR_LINE = re.compile(r"\berror\b", re.I)


def parse_compile_log(text: str) -> tuple[int | None, int, list[str]]:
    """(errors from the Result line or None, warnings, error lines)."""
    errors = [
        line.strip()
        for line in text.splitlines()
        if _ERROR_LINE.search(line) and not _RESULT.search(line)
    ]
    match = _RESULT.search(text)
    if match is None:
        return None, 0, errors
    return int(match.group(1)), int(match.group(2)), errors


def read_log(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if len(raw) > 1 and raw[1:2] == b"\x00":  # UTF-16 without BOM
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class RunResult:
    deals: str | None  # the exported CSV, None if the run produced none
    seconds: float
    error: str | None = None


Runner = Callable[[list[str], dict[str, str], float], Awaitable[tuple[int, str]]]


async def run_process(command: list[str], env: dict[str, str], limit: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *command,
        env={**os.environ, **env},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=limit)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise
    return proc.returncode or 0, out.decode("utf-8", errors="replace")[-4000:]


class MetaTrader:
    """Compiles and backtests in JARVIS's test terminal."""

    def __init__(
        self, paths: Mt5Paths, settings: BotsSettings, *, runner: Runner = run_process
    ) -> None:
        self.paths = paths
        self._settings = settings
        self._runner = runner
        self._lock = asyncio.Lock()

    # Setup -----------------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        test = self.paths.test_dir
        return (
            test is not None
            and (test / "terminal64.exe").exists()
            and (self.paths.wine is not None or sys.platform == "win32")
        )

    def experts_dir(self) -> Path:
        test = self.paths.test_dir
        if test is None:
            raise Mt5Error("MetaTrader 5 wasn't found.")
        return test / "MQL5" / "Experts"

    def set_up(self) -> None:
        """Create or refresh the test terminal: program files once, the user's
        MQL5 folder (includes, indicators, libraries) every time."""
        paths = self.paths
        if paths.terminal_dir is None or paths.data_dir is None or paths.test_dir is None:
            raise Mt5Error("MetaTrader 5 or your MQL5 folder wasn't found.")
        test = paths.test_dir
        if not (test / "terminal64.exe").exists():
            shutil.copytree(
                paths.terminal_dir,
                test,
                ignore=lambda folder, names: (
                    [n for n in names if n in _SKIP_PROGRAM]
                    if Path(folder) == paths.terminal_dir
                    else []
                ),
                dirs_exist_ok=True,
            )
            # The user's servers and saved logins, so the tester can download history.
            config = paths.data_dir.parent / "config"
            if not config.is_dir():
                config = paths.data_dir.parent / "Config"
            if config.is_dir():
                shutil.copytree(config, test / "config", dirs_exist_ok=True)
        self.sync()

    def sync(self) -> None:
        """Bring the user's MQL5 files (not JARVIS's versions) into the test terminal."""
        if self.paths.data_dir is None or self.paths.test_dir is None:
            raise Mt5Error("MetaTrader 5 or your MQL5 folder wasn't found.")
        target = self.paths.test_dir / "MQL5"
        shutil.copytree(
            self.paths.data_dir,
            target,
            ignore=lambda folder, names: [
                n
                for n in names
                if n in _SKIP_MQL5 or (Path(folder).name == "Experts" and n == "JARVIS")
            ],
            dirs_exist_ok=True,
        )

    def env(self) -> dict[str, str]:
        out = {"WINEDEBUG": "-all"}
        if self.paths.prefix is not None:
            out["WINEPREFIX"] = str(self.paths.prefix)
        out.update(self._settings.mt5.env)
        return out

    def _command(self, program: Path, *args: str) -> list[str]:
        if self.paths.wine is not None:
            return [str(self.paths.wine), str(program), *args]
        return [str(program), *args]

    def _win(self, path: Path) -> str:
        return windows_path(path, self.paths.prefix)

    def user_terminal_running(self) -> bool:
        """Is the user's own MetaTrader open? (Then it may be trading — never touched.)"""
        return any("terminal64.exe" in line and TEST_FOLDER[0] not in line for line in _processes())

    async def open_test_terminal(self) -> None:
        """Start the test terminal with its window, e.g. to log in to the demo account once."""
        test = self.paths.test_dir
        if test is None or not self.ready:
            raise Mt5Error("Set up the test terminal first.")
        await asyncio.create_subprocess_exec(
            *self._command(test / "terminal64.exe", "/portable"),
            env={**os.environ, **self.env()},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    # Compile ---------------------------------------------------------------------------

    async def compile(self, source: Path) -> CompileResult:
        test = self.paths.test_dir
        if test is None or not self.ready or self.paths.runs_dir is None:
            raise Mt5Error("The test terminal isn't set up.")
        self.paths.runs_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.paths.runs_dir / f"compile-{int(time.time() * 1000)}.log"
        binary = source.with_suffix(".ex5")
        before = binary.stat().st_mtime if binary.exists() else 0.0
        command = self._command(
            test / "MetaEditor64.exe",
            "/portable",
            f"/compile:{self._win(source)}",
            f"/log:{self._win(log_file)}",
            f"/inc:{self._win(test / 'MQL5')}",
        )
        async with self._lock:
            await self._runner(command, self.env(), 300.0)
        text = read_log(log_file) if log_file.exists() else ""
        errors, warnings, lines = parse_compile_log(text)
        fresh = binary.exists() and binary.stat().st_mtime > before
        ok = errors == 0 and fresh
        if not ok and not lines:
            lines = ["MetaEditor wrote no result" if not text else "no .ex5 was produced"]
        return CompileResult(ok, lines[:30], warnings, text[-6000:])

    # Backtest --------------------------------------------------------------------------

    async def backtest(self, spec: RunSpec) -> RunResult:
        test, runs, common = self.paths.test_dir, self.paths.runs_dir, self.paths.common_files
        if test is None or runs is None or common is None or not self.ready:
            raise Mt5Error("The test terminal isn't set up.")
        runs.mkdir(parents=True, exist_ok=True)
        ini = runs / f"{spec.export_file}.ini"
        ini.write_text(config_ini(spec), encoding="utf-8")
        exported = common / spec.export_file
        timeout = self._settings.test_timeout_minutes * 60
        started = time.monotonic()
        async with self._lock:
            for attempt in range(2):  # the first run may only download history
                exported.unlink(missing_ok=True)
                command = self._command(
                    test / "terminal64.exe", "/portable", f"/config:{self._win(ini)}"
                )
                try:
                    _, output = await self._runner(command, self.env(), timeout)
                except TimeoutError:
                    _kill_test_terminals()
                    return RunResult(None, time.monotonic() - started, "the backtest timed out")
                if exported.exists():
                    text = exported.read_text(encoding="utf-8", errors="replace")
                    return RunResult(text, time.monotonic() - started)
                log.info("backtest attempt %d produced no deals file: %s", attempt + 1, output)
        return RunResult(
            None,
            time.monotonic() - started,
            "MetaTrader finished without results — is the symbol right and is the test "
            "terminal logged in (open it once and log in to your demo account)?",
        )


def _processes() -> list[str]:
    if sys.platform == "win32":
        return []
    try:
        import subprocess

        out = subprocess.run(
            ["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return out.splitlines()


def _kill_test_terminals() -> None:
    """Stop a hung test terminal — only JARVIS's, never the user's."""
    for line in _processes():
        if "terminal64.exe" in line and TEST_FOLDER[0] in line:
            pid = line.strip().split(" ", 1)[0]
            if pid.isdigit():
                with contextlib.suppress(OSError):
                    os.kill(int(pid), 9)
