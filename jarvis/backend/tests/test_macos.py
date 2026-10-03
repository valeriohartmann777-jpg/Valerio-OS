"""macOS backend.

Everything except the Quartz window-server call runs here on any OS: a fake
``Fake.app`` bundle contains a real executable, a stand-in for ``open -a``
starts it, and process detection goes through real psutil + bundle paths.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psutil
import pytest

from jarvis.core.trace import TraceContext
from jarvis.permissions.models import PermissionLevel
from jarvis.settings import (
    AppCatalogSettings,
    AppEntry,
    catalog_platform,
    load_settings,
    resolve_backend,
)
from jarvis.tools.system.apps import AppCatalog
from jarvis.tools.system.backend import LaunchError
from jarvis.tools.system.macos import AppIndex, MacOSSystemBackend, bundle_key, windows_from_cg
from jarvis.tools.system.tools import OpenApplicationArgs, OpenApplicationTool

SLEEP = shutil.which("sleep")

# --- pure helpers --------------------------------------------------------------


def test_bundle_key() -> None:
    assert bundle_key("/Applications/Spotify.app/Contents/MacOS/Spotify") == "spotify.app"
    helper = (
        "/Applications/Google Chrome.app/Contents/Frameworks/X.framework"
        "/Helpers/H.app/Contents/MacOS/H"
    )
    assert bundle_key(helper) == "google chrome.app"
    assert bundle_key("/usr/bin/python3") is None


def cg(pid: int, owner: str, title: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "kCGWindowLayer": 0,
        "kCGWindowOwnerPID": pid,
        "kCGWindowOwnerName": owner,
        "kCGWindowName": title,
        "kCGWindowNumber": pid * 10,
        "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 800, "Height": 600},
        "kCGWindowAlpha": 1.0,
        **extra,
    }


def test_windows_from_cg_filters_and_orders() -> None:
    infos = [
        cg(1, "Dock", kCGWindowLayer=20),
        cg(2, "TextEdit", "Untitled"),
        cg(3, "Helper", kCGWindowBounds={"Width": 1, "Height": 1}),
        cg(4, "Ghost", kCGWindowAlpha=0.0),
        cg(5, "Rechner"),
    ]
    windows = windows_from_cg(infos, {2: "textedit.app"})
    assert [w.pid for w in windows] == [2, 5]
    assert windows[0].is_foreground and not windows[1].is_foreground
    assert windows[0].process_name == "textedit.app"
    assert windows[1].title == "Rechner"  # no Screen Recording permission → app name
    assert windows[1].process_name == "rechner"


def test_app_index_scans_standard_and_grouping_folders(tmp_path: Path) -> None:
    (tmp_path / "Apps" / "Safari.app").mkdir(parents=True)
    (tmp_path / "Apps" / "Utilities" / "Terminal.app").mkdir(parents=True)
    (tmp_path / "Apps" / "notes.txt").write_text("x")
    index = AppIndex([tmp_path / "Apps", tmp_path / "missing"])
    assert set(index.scan()) == {"safari", "terminal"}
    assert index.find("SAFARI") == tmp_path / "Apps" / "Safari.app"
    assert index.find("terminal.app") == tmp_path / "Apps" / "Utilities" / "Terminal.app"
    assert index.find(str(tmp_path / "Apps" / "Safari.app")) == tmp_path / "Apps" / "Safari.app"
    assert index.find("/etc/passwd") is None
    assert index.find("Nope") is None


# --- configuration ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("choice", "host", "backend", "catalog"),
    [
        ("auto", "darwin", "macos", "macos"),
        ("auto", "win32", "windows", "windows"),
        ("auto", "linux", "simulated", "windows"),
        ("macos", "linux", "macos", "macos"),
        ("simulated", "darwin", "simulated", "windows"),
    ],
)
def test_backend_and_catalog_selection(choice: str, host: str, backend: str, catalog: str) -> None:
    assert resolve_backend(choice, host) == backend
    assert catalog_platform(choice, host) == catalog


def test_macos_catalog_understands_how_people_ask() -> None:
    settings = load_settings(environ={"JARVIS_SYSTEM_BACKEND": "macos"})
    catalog = AppCatalog(settings.apps)
    assert catalog.platform == "macos"
    for spoken, app in [
        ("texteditor", "TextEdit"),
        ("Notepad", "TextEdit"),
        ("finder", "Finder"),
        ("rechner", "Calculator"),
        ("systemeinstellungen", "System Settings"),
        ("browser", "Safari"),
    ]:
        target = catalog.resolve(spoken)
        assert target is not None and target.name == app, spoken
    terminal = catalog.resolve("terminal")
    assert terminal is not None and terminal.level is PermissionLevel.MODIFICATION

    unknown = catalog.resolve("Visual Studio Code Insiders")
    assert unknown is not None and not unknown.known
    assert unknown.level is PermissionLevel.SAFE_ACTION
    assert unknown.launch == ("visual studio code insiders",)
    assert unknown.processes == frozenset({"visual studio code insiders.app"})
    assert catalog.resolve("../../bin/sh") is None
    assert catalog.config_file == "config/apps.macos.yaml"


# --- integration: real processes, fake window server ------------------------------


@pytest.fixture
def mac_env(tmp_path: Path) -> Iterator[dict[str, Any]]:
    if SLEEP is None:
        pytest.skip("needs a sleep binary")
    apps = tmp_path / "Applications"
    for bundle in ("Fake.app", "Utilities/Tool.app"):
        macos_dir = apps / bundle / "Contents" / "MacOS"
        macos_dir.mkdir(parents=True)
        exe = macos_dir / Path(bundle).stem.lower()
        shutil.copy(SLEEP, exe)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    if subprocess.run([str(apps / "Fake.app/Contents/MacOS/fake"), "0"], check=False).returncode:
        pytest.skip("copied sleep binary does not run from another path")

    opener = tmp_path / "open"
    opener.write_text(
        "#!/bin/sh\n"
        '[ "$1" = "-a" ] || exit 2\n'
        'exe="$2/Contents/MacOS/$(basename "$2" .app | tr "A-Z" "a-z")"\n'
        '[ -x "$exe" ] || { echo "Unable to find application named $2" >&2; exit 1; }\n'
        '"$exe" 30 >/dev/null 2>&1 &\n'
    )
    opener.chmod(0o755)

    def window_server() -> list[dict[str, Any]]:
        """Every running fake-bundle process shows one window, newest in front."""
        infos = []
        for proc in psutil.process_iter(["pid", "exe", "create_time"]):
            key = bundle_key(proc.info.get("exe") or "")
            if key in ("fake.app", "tool.app"):
                infos.append(
                    (proc.info["create_time"], cg(proc.info["pid"], key[:-4].title(), "Untitled"))
                )
        ordered = [info for _, info in sorted(infos, key=lambda item: -item[0])]
        return [*ordered, cg(1, "JARVIS")]

    backend = MacOSSystemBackend(
        app_index=AppIndex([apps]), window_source=window_server, opener=str(opener)
    )
    catalog = AppCatalog(
        AppCatalogSettings(
            platform="macos",
            applications={
                "fake": AppEntry(
                    name="Fake", aliases=["phony"], launch=["Fake"], processes=["fake.app"]
                )
            },
        )
    )
    yield {"backend": backend, "catalog": catalog}
    for proc in psutil.process_iter(["exe"]):
        if bundle_key(proc.info.get("exe") or "") in ("fake.app", "tool.app"):
            proc.kill()


async def test_open_verify_and_detect_closing(mac_env: dict[str, Any]) -> None:
    backend, catalog = mac_env["backend"], mac_env["catalog"]
    tool = OpenApplicationTool(backend, catalog, verify_timeout=5)
    args = OpenApplicationArgs(name="phony")
    ctx = TraceContext.new()

    assert await tool.precheck(args, ctx) is None
    result = await tool.execute(args, ctx)
    assert result.success and not result.simulated
    assert result.data["outcome"] == "window_opened"
    assert result.data["launch_target"].endswith("Fake.app")
    assert any("New process: fake.app" in line for line in result.observations)

    verification = await tool.verify(args, result, ctx)
    assert verification.status == "verified"

    active = await backend.active_window()
    assert active is not None and active.process_name == "fake.app"

    for proc in psutil.process_iter(["exe"]):
        if bundle_key(proc.info.get("exe") or "") == "fake.app":
            proc.kill()
            proc.wait(5)
    assert (await tool.verify(args, result, ctx)).status == "failed"


async def test_unknown_installed_app_and_missing_app(mac_env: dict[str, Any]) -> None:
    tool = OpenApplicationTool(mac_env["backend"], mac_env["catalog"], verify_timeout=5)
    ctx = TraceContext.new()

    tool_args = OpenApplicationArgs(name="tool")
    assert tool.describe_action(tool_args).level is PermissionLevel.SAFE_ACTION
    result = await tool.execute(tool_args, ctx)
    assert result.success and result.target == "Tool"

    missing = await tool.precheck(OpenApplicationArgs(name="Final Cut Pro"), ctx)
    assert missing is not None and missing.code == "app_not_found"
    assert missing.suggestion and "apps.macos.yaml" in missing.suggestion


async def test_open_failure_becomes_launch_error(mac_env: dict[str, Any]) -> None:
    with pytest.raises(LaunchError) as error:
        await mac_env["backend"].launch("/nowhere/Missing.app")
    assert error.value.code == "launch_failed"
    assert error.value.detail and "Unable to find application" in error.value.detail


def test_missing_quartz_falls_back_to_simulation_with_a_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from jarvis.tools.system import create_backend

    settings = load_settings(environ={"JARVIS_SYSTEM_BACKEND": "macos"})
    try:
        import Quartz  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("Quartz is installed here; the fallback cannot be observed")
    with caplog.at_level("ERROR", logger="jarvis.tools"):
        backend = create_backend(settings)
    assert backend.simulated
    assert "macOS control unavailable" in caplog.text
