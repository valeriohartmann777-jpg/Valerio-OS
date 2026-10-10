"""Git workspaces: one repository per mission, one worktree and branch per task.

- ``sandbox`` missions start a fresh repository (``main``) in the mission folder.
- ``jarvis`` missions add a worktree of the JARVIS repository on the branch
  ``ultron/<mission>``; the user's checkout and branch are never touched.

Agents never run git that writes: the runtime commits a task's work, merges it
into the mission's integration branch (``--no-ff``, aborted on conflict), and
gives SENTINEL a fresh detached checkout to review, so a reviewer can't alter the
evidence it judges. Hooks and signing are disabled for every git call.
"""

from __future__ import annotations

import asyncio
import io
import shutil
import tarfile
from pathlib import Path
from typing import Literal

Kind = Literal["sandbox", "jarvis"]
GIT_ID = ("-c", "user.name=ULTRON", "-c", "user.email=ultron@jarvis.local")
GIT_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "gc.auto=0")
IGNORED = ".pytest_cache/\n__pycache__/\n*.pyc\n.ultron/\n"


class GitError(RuntimeError):
    pass


async def git(cwd: Path, *args: str, check: bool = True, binary: bool = False) -> str | bytes:
    process = await asyncio.create_subprocess_exec(
        shutil.which("git") or "git",
        *GIT_SAFE,
        *GIT_ID,
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": str(cwd),
            "GIT_CONFIG_NOSYSTEM": "1",
            "LANG": "C",
        },
    )
    out, err = await process.communicate()
    if check and process.returncode != 0:
        raise GitError(f"git {' '.join(args[:3])}: {err.decode(errors='replace').strip()[:500]}")
    return out if binary else out.decode(errors="replace")


async def text(cwd: Path, *args: str, check: bool = True) -> str:
    out = await git(cwd, *args, check=check)
    assert isinstance(out, str)
    return out


class Workspace:
    def __init__(
        self,
        root: Path,
        mission_id: str,
        kind: Kind,
        source: Path | None = None,
        subdir: str = "",
    ) -> None:
        self.root = root
        self.mission_id = mission_id
        self.kind = kind
        self.source = source  # the git repository holding JARVIS, for kind == "jarvis"
        self.subdir = subdir  # where JARVIS sits inside that repository ("jarvis")
        self.repo = root / "repo"
        self._lock = asyncio.Lock()

    @property
    def integration(self) -> str:
        return "main" if self.kind == "sandbox" else f"ultron/{self.mission_id}"

    def branch(self, key: str) -> str:
        return f"task/{key}" if self.kind == "sandbox" else f"ultron/{self.mission_id}-{key}"

    def tree(self, key: str) -> Path:
        return self.root / "tasks" / key

    def scratch(self, key: str) -> Path:
        path = self.root / "scratch" / key
        path.mkdir(parents=True, exist_ok=True)
        return path

    def pythonpath(self, tree: Path) -> list[Path]:
        if self.kind == "jarvis":
            return [tree / self.subdir / "backend" if self.subdir else tree / "backend"]
        return [tree]

    async def create(self, title: str) -> str:
        """Set up the mission repository; returns the base commit."""
        async with self._lock:
            if (self.repo / ".git").exists():
                return await self.head(self.integration)
            self.root.mkdir(parents=True, exist_ok=True)
            if self.kind == "sandbox":
                self.repo.mkdir(parents=True, exist_ok=True)
                await git(self.repo, "init", "-q", "-b", "main")
                (self.repo / "README.md").write_text(
                    f"# {title}\n\nCreated by JARVIS ULTRON (mission {self.mission_id}).\n",
                    encoding="utf-8",
                )
                (self.repo / ".gitignore").write_text(IGNORED, encoding="utf-8")
                await git(self.repo, "add", "-A")
                await git(self.repo, "commit", "-q", "-m", "ULTRON: project scaffold")
            else:
                if self.source is None or not (self.source / ".git").exists():
                    raise GitError("The JARVIS checkout isn't a git repository.")
                await git(
                    self.source,
                    "worktree",
                    "add",
                    "-q",
                    "-b",
                    self.integration,
                    str(self.repo),
                    "HEAD",
                )
            return await self.head(self.integration)

    async def head(self, ref: str = "HEAD") -> str:
        return (await text(self.repo, "rev-parse", ref)).strip()

    async def task_tree(self, key: str) -> Path:
        """The task's own worktree, branched from the integration head (created once)."""
        tree = self.tree(key)
        async with self._lock:
            if not (tree / ".git").exists():
                tree.parent.mkdir(parents=True, exist_ok=True)
                exists = await text(self.repo, "branch", "--list", self.branch(key))
                if exists.strip():
                    await git(self.repo, "worktree", "add", "-q", str(tree), self.branch(key))
                else:
                    await git(
                        self.repo,
                        "worktree",
                        "add",
                        "-q",
                        "-b",
                        self.branch(key),
                        str(tree),
                        self.integration,
                    )
        return tree

    async def catch_up(self, key: str) -> None:
        """Bring the integration branch into a task's tree before another attempt."""
        tree = self.tree(key)
        async with self._lock:
            await git(tree, "merge", "-q", "--no-edit", self.integration)

    async def reset(self, key: str) -> None:
        """Drop uncommitted leftovers of an interrupted attempt (only in the task's tree)."""
        tree = self.tree(key)
        if (tree / ".git").exists():
            await git(tree, "reset", "-q", "--hard")
            await git(tree, "clean", "-q", "-fd")

    async def commit(self, key: str, message: str) -> str | None:
        """Commit everything the agent changed; None if nothing changed."""
        tree = self.tree(key)
        await git(tree, "add", "-A")
        if not (await text(tree, "status", "--porcelain")).strip():
            return None
        await git(tree, "commit", "-q", "-m", message)
        return (await text(tree, "rev-parse", "HEAD")).strip()

    async def merge(self, key: str, message: str) -> str:
        """Merge a task branch into the integration branch; aborts cleanly on conflict."""
        async with self._lock:
            await git(self.repo, "merge", "--no-ff", "-m", message, self.branch(key), check=False)
            status = await text(self.repo, "status", "--porcelain")
            conflicts = ("UU", "AA", "DD", "AU", "UA", "DU", "UD")
            if any(line[:2] in conflicts for line in status.splitlines()):
                await git(self.repo, "merge", "--abort", check=False)
                raise GitError(f"merging {key} conflicts with work already integrated")
            return await self.head(self.integration)

    async def diff(self, base: str, head: str, *, cwd: Path | None = None) -> str:
        return await text(cwd or self.repo, "diff", "--stat", "--patch", f"{base}..{head}")

    async def review_tree(self, key: str) -> Path:
        """A fresh detached checkout of the integration head for an independent review."""
        path = self.root / "review" / key
        async with self._lock:
            if path.exists():
                await git(self.repo, "worktree", "remove", "--force", str(path), check=False)
                shutil.rmtree(path, ignore_errors=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            await git(self.repo, "worktree", "add", "-q", "--detach", str(path), self.integration)
        return path

    async def files(self) -> list[str]:
        return [f for f in (await text(self.repo, "ls-files")).splitlines() if f]

    async def export(self, destination: Path) -> int:
        """Write the integration head's tracked files to ``destination`` (a new folder)."""
        data = await git(self.repo, "archive", "--format=tar", self.integration, binary=True)
        assert isinstance(data, bytes)
        return await asyncio.to_thread(_unpack, data, destination)


def _unpack(data: bytes, destination: Path) -> int:
    if destination.exists():
        raise GitError(f"{destination} already exists")
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        members = archive.getmembers()
        archive.extractall(destination, filter="data")
    return sum(1 for m in members if m.isfile())
