"""Running an allowlisted command where it can do the least harm.

Every command runs without a shell, in the task's worktree, with a scrubbed
environment (no API keys, no proxy, HOME and TMPDIR in a scratch folder), a
timeout that kills the whole process group, and capped output. On top:

- macOS: a Seatbelt profile (``sandbox-exec``) denies all network access and
  every file write outside the worktree and its scratch folder.
- Linux: a fresh network namespace (``unshare -rn``) — no network; writes are
  not confined by the kernel there (development containers only).
- Elsewhere, or if the probe fails: process-level limits only, and the UI says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from jarvis.ultron.policy import Command

OUTPUT_LIMIT = 64_000
SEATBELT = "/usr/bin/sandbox-exec"


@dataclass(frozen=True)
class Isolation:
    kind: Literal["seatbelt", "netns", "none"]
    network_blocked: bool
    writes_confined: bool
    detail: str


@dataclass(frozen=True)
class Outcome:
    command: str
    exit_code: int | None
    output: str
    seconds: float
    timed_out: bool
    truncated: bool

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def _profile(*writable: Path) -> str:
    allowed = " ".join(f'(subpath "{p.resolve()}")' for p in writable)
    return (
        "(version 1)(allow default)(deny network*)(deny file-write*)"
        f'(allow file-write* {allowed} (literal "/dev/null") (literal "/dev/zero") '
        '(literal "/dev/dtracehelper") (regex #"^/dev/tty") (regex #"^/dev/fd/"))'
    )


@cache
def detect() -> Isolation:
    """Probe once which isolation actually works on this machine."""
    if sys.platform == "darwin" and Path(SEATBELT).exists():
        probe = Path(os.environ.get("TMPDIR", "/tmp")).resolve()
        try:
            ok = (
                subprocess.run(
                    [SEATBELT, "-p", _profile(probe), "/usr/bin/true"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                ).returncode
                == 0
            )
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            return Isolation(
                "seatbelt", True, True, "macOS Seatbelt: no network, writes only in the workspace"
            )
    if sys.platform.startswith("linux") and (unshare := shutil.which("unshare")):
        try:
            ok = (
                subprocess.run(
                    [unshare, "-rn", "true"], capture_output=True, timeout=10, check=False
                ).returncode
                == 0
            )
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            return Isolation(
                "netns", True, False, "Linux network namespace: no network (writes not confined)"
            )
    return Isolation(
        "none", False, False, "No OS sandbox available: allowlist, scrubbed env and timeouts only"
    )


def environment(scratch: Path, pythonpath: list[Path]) -> dict[str, str]:
    """The only environment a command sees: no keys, no proxies, no user config."""
    home = scratch / "home"
    tmp = scratch / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": os.pathsep.join(str(p) for p in pythonpath),
        "NO_COLOR": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }


async def run(
    command: Command,
    *,
    cwd: Path,
    scratch: Path,
    pythonpath: list[Path],
    timeout: float,  # noqa: ASYNC109 - on expiry the whole process group is killed
    isolation: Isolation | None = None,
) -> Outcome:
    iso = isolation or detect()
    program = sys.executable if command.argv[0] == "python" else command.argv[0]
    argv = [program, *command.argv[1:]]
    if command.argv[0] == "git":
        argv = [shutil.which("git") or "git", *command.argv[1:]]
    if iso.kind == "seatbelt":
        argv = [SEATBELT, "-p", _profile(cwd, scratch), *argv]
    elif iso.kind == "netns":
        argv = [shutil.which("unshare") or "unshare", "-rn", *argv]
    env = environment(scratch, pythonpath)
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,  # its own process group: a timeout kills everything it started
    )
    timed_out = False
    try:
        data, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        timed_out = True
        _kill(process)
        data = b""
        with contextlib.suppress(Exception):
            rest = await asyncio.wait_for(process.stdout.read(), 5) if process.stdout else b""
            data = rest
        await process.wait()
    except asyncio.CancelledError:
        _kill(process)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), 5)
        raise
    text = data.decode("utf-8", errors="replace")
    truncated = len(text) > OUTPUT_LIMIT
    if truncated:
        text = text[:2000] + "\n…[output truncated]…\n" + text[-(OUTPUT_LIMIT - 2100) :]
    return Outcome(
        command=command.label,
        exit_code=None if timed_out else process.returncode,
        output=text,
        seconds=round(time.monotonic() - started, 2),
        timed_out=timed_out,
        truncated=truncated,
    )


def _kill(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if sys.platform == "win32":
            process.kill()
        else:  # the whole process group: whatever the command started dies too
            os.killpg(process.pid, signal.SIGKILL)
