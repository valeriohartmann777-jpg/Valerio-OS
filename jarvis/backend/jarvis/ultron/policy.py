"""Policy decided in code, before any side effect — never by a model's instructions.

- Paths: a task may write only inside its own worktree, only where its contract's
  globs allow, never into ``.git`` or ULTRON's own metadata.
- Commands: argv lists from a small allowlist (no shell): python modules for
  tests and compilation, scripts inside the workspace, read-only git.
- Action categories: workspace reads/writes and sandboxed commands run on their
  own; exporting a project out of the workspace needs the user's approval; the
  rest (network, installing packages, merging into a protected branch,
  deployments, messages, credentials, money) is locked in this release.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any


class Decision(StrEnum):
    AUTO = "auto"
    APPROVAL = "approval"
    LOCKED = "locked"


CATEGORIES: dict[str, tuple[Decision, str]] = {
    "workspace.read": (Decision.AUTO, "Read files in the mission workspace"),
    "workspace.write": (Decision.AUTO, "Write inside the task's worktree and allowed paths"),
    "command.run": (Decision.AUTO, "Allowlisted commands in the sandbox (no network)"),
    "export.project": (Decision.APPROVAL, "Copy a finished project out of the workspace"),
    "network": (Decision.LOCKED, "Network access from agents or their commands"),
    "dependency.install": (Decision.LOCKED, "Installing packages"),
    "repo.merge_protected": (Decision.LOCKED, "Merging into the running JARVIS checkout"),
    "deploy.production": (Decision.LOCKED, "Production deployments"),
    "message.external": (Decision.LOCKED, "Emails, posts and other outbound messages"),
    "credentials.change": (Decision.LOCKED, "Creating or changing credentials"),
    "finance.transaction": (Decision.LOCKED, "Payments, trades, transfers"),
    "delete.outside_workspace": (Decision.LOCKED, "Deleting anything outside the workspace"),
}

NEVER_WRITABLE = (".git", ".git/**", ".ultron", ".ultron/**")
PYTHON_MODULES = {"pytest", "unittest", "py_compile", "compileall", "doctest"}
GIT_READ_ONLY = {"status", "diff", "log", "show"}
_GIT_BAD_ARGS = re.compile(r"^(-c|--output|--ext-diff|--exec|--upload-pack|--config)")
MAX_ARGS = 40


class PolicyError(ValueError):
    """An action the policy refuses. ``category`` says which rule refused it."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


def decision(category: str) -> Decision:
    return CATEGORIES.get(category, (Decision.LOCKED, ""))[0]


# Paths -----------------------------------------------------------------------------------


def clean_relative(path: str) -> str:
    """A normalised relative POSIX path, or PolicyError if it could leave the workspace."""
    text = path.strip().replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text) or "\0" in text:
        raise PolicyError("workspace.write", f"'{path}' must be a path inside the workspace.")
    parts = [p for p in PurePosixPath(text).parts if p not in ("", ".")]
    if not parts or ".." in parts:
        raise PolicyError("workspace.write", f"'{path}' leaves the workspace.")
    return "/".join(parts)


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def glob_match(pattern: str, path: str) -> bool:
    return bool(_glob_regex(pattern).match(path))


def check_write(path: str, allowed: list[str]) -> str:
    rel = clean_relative(path)
    if any(glob_match(p, rel) for p in NEVER_WRITABLE):
        raise PolicyError("workspace.write", f"'{rel}' is managed by ULTRON and can't be written.")
    if not any(glob_match(p, rel) for p in allowed):
        raise PolicyError(
            "workspace.write",
            f"'{rel}' is outside this task's allowed paths ({', '.join(allowed) or 'none'}).",
        )
    return rel


def valid_glob(pattern: str) -> bool:
    try:
        rel = clean_relative(pattern.replace("**", "x").replace("*", "x").replace("?", "x"))
    except PolicyError:
        return False
    return not rel.startswith(".git")


# Commands --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]  # "python" stands for the sandbox's interpreter
    label: str


def check_command(argv: list[str]) -> Command:
    """Validate an argv list against the allowlist (no shell, no network tools)."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise PolicyError("command.run", "A command is a non-empty list of strings.")
    if len(argv) > MAX_ARGS or any(len(a) > 1000 or "\0" in a for a in argv):
        raise PolicyError("command.run", "Command too long.")
    program, args = argv[0], argv[1:]
    if program == "pytest":
        program, args = "python", ["-m", "pytest", *args]
    if program in ("python", "python3"):
        if len(args) >= 2 and args[0] == "-m":
            if args[1] not in PYTHON_MODULES:
                raise PolicyError(
                    "dependency.install" if args[1] == "pip" else "command.run",
                    f"python -m {args[1]} isn't allowed "
                    f"(allowed: {', '.join(sorted(PYTHON_MODULES))}).",
                )
            return Command(("python", *args), " ".join(["python", *args]))
        if args and args[0].endswith(".py"):
            clean_relative(args[0])
            return Command(("python", *args), " ".join(["python", *args]))
        raise PolicyError(
            "command.run",
            "python may run a module from the allowlist (-m pytest …) or a .py file in the "
            "workspace — not -c snippets or other flags.",
        )
    if program == "git":
        if not args or args[0] not in GIT_READ_ONLY:
            raise PolicyError(
                "command.run",
                f"Only read-only git ({', '.join(sorted(GIT_READ_ONLY))}); ULTRON commits itself.",
            )
        if any(_GIT_BAD_ARGS.match(a) for a in args):
            raise PolicyError("command.run", "That git option isn't allowed.")
        return Command(("git", *args), " ".join(["git", *args]))
    if program in ("curl", "wget", "ssh", "scp", "nc", "pip", "pip3", "npm", "brew"):
        raise PolicyError("network", f"{program} needs network or installs software — locked.")
    raise PolicyError("command.run", f"'{program}' isn't an allowed command.")


# Effects ---------------------------------------------------------------------------------


def effect_signature(effect: dict[str, Any]) -> str:
    """Identity of an approved effect: a retry can't silently widen what was approved."""
    canonical = json.dumps(effect, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
