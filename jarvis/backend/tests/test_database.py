"""Schema migrations never touch the owner's data without a backup first (audit A-03)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.storage import database
from jarvis.storage.database import Database

V1 = ["CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"]
V2 = ["CREATE TABLE extra (id INTEGER PRIMARY KEY)"]
V3 = ["CREATE TABLE more (id INTEGER PRIMARY KEY)"]


def versions(path: Path) -> list[int]:
    with sqlite3.connect(path) as conn:
        return [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")]


async def open_with(
    path: Path, migrations: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> Database:
    monkeypatch.setattr(database, "MIGRATIONS", migrations)
    db = Database(path)
    await db.connect()
    return db


async def test_pending_migration_is_preceded_by_a_consistent_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "jarvis.db"
    db = await open_with(path, [V1], monkeypatch)
    await db.execute("INSERT INTO notes (body) VALUES ('my strategy notes')")
    await db.close()
    assert not (tmp_path / "backups").exists()  # a fresh database needs no backup

    db = await open_with(path, [V1, V2], monkeypatch)
    await db.close()
    (backup,) = (tmp_path / "backups").glob("jarvis-v1-*.db")
    assert versions(backup) == [1]  # the copy is the state before the change
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT body FROM notes").fetchall() == [("my strategy notes",)]
    assert versions(path) == [1, 2]

    db = await open_with(path, [V1, V2], monkeypatch)  # up to date: no new copy
    await db.close()
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


async def test_only_the_newest_backups_are_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "jarvis.db"
    folder = tmp_path / "backups"
    folder.mkdir()
    for i in range(7):  # older copies from earlier updates (v15 sorts before v9 by name)
        (folder / f"jarvis-v{9 + i}-2026010{i}T000000000000Z.db").write_bytes(b"old")
    db = await open_with(path, [V1], monkeypatch)
    await db.close()
    db = await open_with(path, [V1, V2, V3], monkeypatch)
    await db.close()
    kept = sorted(p.name for p in folder.glob("*.db"))
    assert len(kept) == database.BACKUPS_KEPT
    assert any(name.startswith("jarvis-v1-") for name in kept)  # the new one survives
    assert "jarvis-v9-20260100T000000000000Z.db" not in kept  # the oldest went first


async def test_a_failed_backup_is_logged_and_the_additive_migration_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "jarvis.db"
    db = await open_with(path, [V1], monkeypatch)
    await db.close()
    (tmp_path / "backups").write_text("not a folder")  # mkdir fails
    db = await open_with(path, [V1, V2], monkeypatch)
    await db.close()
    assert versions(path) == [1, 2]
    assert "backup before migration failed" in caplog.text
