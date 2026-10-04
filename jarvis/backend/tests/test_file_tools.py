"""File tools: the folder allowlist, search, listing, reading and opening."""

from __future__ import annotations

import asyncio
import os
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.trace import TraceContext
from jarvis.runtime import Runtime
from jarvis.settings import FileSettings, Settings
from jarvis.tools.base import ToolError, ToolResult
from jarvis.tools.files import FileAccess, OpenFileArgs
from tests.conftest import eventually, make_settings

BLOCKED = [".env", "*.pem", "id_rsa*", "*passwort*"]


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF with real text (no PDF library needed)."""
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "home"
    docs, downloads = home / "Documents", home / "Downloads"
    for folder in (docs / "Steuern", docs / "node_modules" / "pkg", docs / "Notes.rtfd", downloads):
        folder.mkdir(parents=True)
    (docs / "Steuern" / "Rechnung_März.pdf").write_bytes(minimal_pdf("Total CHF 120"))
    (docs / "Rechnung alt.txt").write_text("alt", encoding="utf-8")
    (docs / "notizen.txt").write_text("Einkaufen: Milch, Brot\n", encoding="utf-8")
    (docs / "umlaut.txt").write_bytes("Grüsse aus Zürich".encode("cp1252"))
    (docs / ".env").write_text("SECRET=1")
    (docs / "Passwörter.txt").write_text("hunter2")
    (docs / "node_modules" / "pkg" / "rechnung.js").write_text("x")
    (docs / ".hidden").mkdir()
    (docs / ".hidden" / "rechnung.txt").write_text("hidden")
    (docs / "binary.dat").write_bytes(b"\x00\x01\x02" * 100)
    (docs / "run.sh").write_text("#!/bin/sh\necho hi\n")
    (docs / "tool").write_text("#!/bin/sh\n")
    os.chmod(docs / "tool", 0o755)
    (docs / "data.xyz").write_text("?")
    with zipfile.ZipFile(docs / "brief.docx", "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:document><w:body><w:p><w:r><w:t>Sehr geehrte</w:t></w:r>"
            "<w:r><w:t xml:space='preserve'> Damen</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Zweite Zeile</w:t></w:r></w:p></w:body></w:document>",
        )
    (downloads / "rechnung-neu.pdf").write_bytes(minimal_pdf("Neu"))
    (tmp_path / "outside.txt").write_text("outside")
    (docs / "link-out").symlink_to(tmp_path / "outside.txt")
    monkeypatch.setenv("HOME", str(home))
    yield home


def access(home: Path, **overrides: Any) -> FileAccess:
    settings = FileSettings(roots=["~/Documents", "~/Downloads"], blocked=BLOCKED, **overrides)
    return FileAccess(settings, home=home)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("~/Documents/notizen.txt", None),
        ("Documents/notizen.txt", None),
        ("~/Documents/../../outside.txt", "outside_allowed_folders"),
        ("/etc/passwd", "outside_allowed_folders"),
        ("~/Documents/link-out", "outside_allowed_folders"),  # symlink resolved first
        ("~/Documents/.env", "blocked_file"),
        ("~/Documents/.hidden/rechnung.txt", "blocked_file"),
        ("~/Documents/Passwörter.txt", "blocked_file"),
        ("~/Documents/missing.txt", "not_found"),
    ],
)
def test_access_check(home: Path, raw: str, code: str | None) -> None:
    checked = access(home).check(raw)
    if code is None:
        assert isinstance(checked, Path) and checked.name == "notizen.txt"
    else:
        assert isinstance(checked, ToolError) and checked.code == code


@pytest.fixture
async def rt(home: Path, tmp_path: Path) -> Any:
    settings: Settings = make_settings(tmp_path)
    settings = settings.model_copy(
        update={
            "files": FileSettings(
                roots=["~/Documents", "~/Downloads"], blocked=BLOCKED, max_read_chars=200
            )
        }
    )
    runtime = Runtime(settings)
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


async def run(
    rt: Runtime, tool: str, args: dict[str, Any], ctx: TraceContext | None = None
) -> ToolResult:
    return await rt.executor.run(tool, args, ctx=ctx or TraceContext.new(), reason="test")


async def test_find_files_matches_words_accents_and_skips_private_places(rt: Runtime) -> None:
    result = await run(rt, "find_files", {"query": "rechnung marz"})
    assert result.success, result.error
    assert [m["path"] for m in result.data["matches"]] == ["~/Documents/Steuern/Rechnung_März.pdf"]
    assert result.data["matches"][0]["kind"] == "file" and result.data["complete"]

    every = await run(rt, "find_files", {"query": "RECHNUNG"})
    paths = {m["path"] for m in every.data["matches"]}
    assert paths == {
        "~/Documents/Steuern/Rechnung_März.pdf",
        "~/Documents/Rechnung alt.txt",
        "~/Downloads/rechnung-neu.pdf",
    }  # not inside .hidden or node_modules

    only = await run(rt, "find_files", {"query": "rechnung", "folder": "~/Downloads"})
    assert [m["name"] for m in only.data["matches"]] == ["rechnung-neu.pdf"]

    notes = await run(rt, "find_files", {"query": "notes"})
    assert notes.data["matches"][0]["kind"] == "file"  # .rtfd package counts as a document

    outside = await run(rt, "find_files", {"query": "x", "folder": "/etc"})
    assert outside.error is not None and outside.error.code == "outside_allowed_folders"


async def test_list_folder(rt: Runtime, home: Path) -> None:
    os.utime(home / "Documents" / "notizen.txt", (2_000_000_000, 2_000_000_000))
    result = await run(rt, "list_folder", {"path": "~/Documents"})
    assert result.success, result.error
    names = [item["name"] for item in result.data["items"]]
    assert names[0] == "notizen.txt"  # newest first
    assert ".env" not in names and ".hidden" not in names and "Passwörter.txt" not in names
    assert {"Steuern", "brief.docx"} <= set(names)
    kinds = {i["name"]: i["kind"] for i in result.data["items"]}
    assert kinds["Steuern"] == "folder" and kinds["Notes.rtfd"] == "file"

    a_file = await run(rt, "list_folder", {"path": "~/Documents/notizen.txt"})
    assert a_file.error is not None and a_file.error.code == "not_a_folder"


async def test_read_file_formats_limits_and_exposure(rt: Runtime) -> None:
    ctx = TraceContext.new()
    text = await run(rt, "read_file", {"path": "~/Documents/notizen.txt"}, ctx)
    assert text.success and text.data["content"] == "Einkaufen: Milch, Brot\n"
    assert "untrusted" in text.data["note"]
    assert ctx.exposed == {"~/Documents/notizen.txt"}

    assert (await run(rt, "read_file", {"path": "~/Documents/umlaut.txt"})).data[
        "content"
    ] == "Grüsse aus Zürich"
    pdf = await run(rt, "read_file", {"path": "~/Documents/Steuern/Rechnung_März.pdf"})
    assert pdf.success, pdf.error
    assert "Total CHF 120" in pdf.data["content"]
    docx = await run(rt, "read_file", {"path": "~/Documents/brief.docx"})
    assert docx.data["content"] == "Sehr geehrte Damen\nZweite Zeile"

    binary = await run(rt, "read_file", {"path": "~/Documents/binary.dat"})
    assert binary.error is not None and binary.error.code == "unsupported_type"
    secret = await run(rt, "read_file", {"path": "~/Documents/.env"})
    assert secret.error is not None and secret.error.code == "blocked_file"
    folder = await run(rt, "read_file", {"path": "~/Documents/Steuern"})
    assert folder.error is not None and folder.error.code == "is_a_folder"


async def test_read_file_cuts_long_files(rt: Runtime, home: Path) -> None:
    (home / "Documents" / "long.txt").write_text("x" * 1000)
    result = await run(rt, "read_file", {"path": "~/Documents/long.txt"})
    assert result.data["truncated"] and len(result.data["content"]) == 200
    assert result.data["characters"] == 1000


async def test_open_file_opens_and_verifies(rt: Runtime) -> None:
    result = await run(rt, "open_file", {"path": "~/Documents/notizen.txt"})
    assert result.success, result.error
    assert result.observed_result == "notizen.txt opened in Notepad"
    tool = rt.tools.get("open_file")
    assert tool is not None
    verification = await tool.verify(
        OpenFileArgs(path="~/Documents/notizen.txt"), result, TraceContext.new()
    )
    assert verification is not None and verification.verified
    assert "notizen.txt" in verification.summary


@pytest.mark.parametrize("name", ["run.sh", "tool"])
async def test_open_file_never_runs_programs(rt: Runtime, name: str) -> None:
    result = await run(rt, "open_file", {"path": f"~/Documents/{name}"})
    assert result.error is not None and result.error.code == "program_refused"
    assert rt.permissions.pending() == []


async def test_unknown_file_types_need_approval(rt: Runtime) -> None:
    task = asyncio.create_task(run(rt, "open_file", {"path": "~/Documents/data.xyz"}))
    await eventually(rt.permissions.pending)
    [request] = rt.permissions.pending()
    assert request.action.level == 2
    assert "Unknown file type (.xyz)" in request.action.effects
    await rt.permissions.approve(request.id)
    assert (await task).success


async def test_macos_folder_denials_are_reported_not_hidden(
    rt: Runtime, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS answers a denied Files & Folders permission with EPERM; that must not
    look like "no matches". (Tests run as root, so the denial is simulated.)"""
    real_scandir = os.scandir
    downloads = home / "Downloads"

    def scandir(path: Any) -> Any:
        if Path(path) == downloads:
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_scandir(path)

    monkeypatch.setattr("jarvis.tools.files.os.scandir", scandir)
    partial = await run(rt, "find_files", {"query": "rechnung"})
    assert partial.success and partial.data["not_allowed"] == ["~/Downloads"]
    assert not partial.data["complete"]
    assert "not allowed to look in ~/Downloads" in partial.summary

    only = await run(rt, "find_files", {"query": "rechnung", "folder": "~/Downloads"})
    assert only.error is not None and only.error.code == "no_permission"
    assert "Files & Folders" in (only.error.suggestion or "")

    def read_bytes(self: Path) -> bytes:
        raise PermissionError(1, "Operation not permitted", str(self))

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    read = await run(rt, "read_file", {"path": "~/Documents/notizen.txt"})
    assert read.error is not None and read.error.code == "no_permission"
