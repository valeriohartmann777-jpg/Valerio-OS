"""The tool broker: what each agent may call, and the check before every effect.

An agent sees only its own tools. Every call is validated against the policy
(paths, commands) before anything happens, and every call — allowed or
refused — is written to the mission's event log. Tool results go back to the
model as data; file contents and command output are untrusted text.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.llm.base import ToolCall, ToolDefinition
from jarvis.ultron import sandbox
from jarvis.ultron.policy import PolicyError, check_command, check_write, clean_relative

MAX_FILE_BYTES = 200_000
MAX_READ_CHARS = 60_000
MAX_LIST = 400
TAIL_FOR_MODEL = 12_000

_SUBMIT_RESULT = ToolDefinition(
    name="submit_result",
    description=(
        "Finish your task. Report what you delivered and, for each done_when criterion, "
        "whether it is met and the evidence. The runtime re-runs the task's checks itself — "
        "a claim without evidence counts for nothing."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "What you did, 1-5 sentences."},
            "criteria": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"},
                        "met": {"type": "boolean"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["criterion", "met", "evidence"],
                },
            },
            "files": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "criteria"],
    },
)

_SUBMIT_REVIEW = ToolDefinition(
    name="submit_review",
    description=(
        "Finish the review with a verdict. 'pass' only if every criterion is met with "
        "evidence you observed; otherwise 'fail' with concrete findings FORGE can act on."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "fail"]},
            "summary": {"type": "string"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                        "issue": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["severity", "issue", "evidence"],
                },
            },
            "criteria": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"},
                        "met": {"type": "boolean"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["criterion", "met", "evidence"],
                },
            },
        },
        "required": ["verdict", "summary", "findings", "criteria"],
    },
)

_ARGV = {"type": "array", "items": {"type": "string"}}
SUBMIT_PLAN = ToolDefinition(
    name="submit_plan",
    description=(
        "Submit the mission charter and task graph. The runtime validates it (acyclic, active "
        "owners, scopes, runnable checks) and tells you what to fix if it can't accept it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "objective": {"type": "string"},
            "in_scope": {"type": "array", "items": {"type": "string"}},
            "out_of_scope": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "blocking_unknowns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only questions without which the work can't start.",
            },
            "success_criteria": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"},
                        "check": {**_ARGV, "description": "Optional argv proving it."},
                    },
                    "required": ["criterion"],
                },
            },
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "lowercase id, e.g. spec"},
                        "title": {"type": "string"},
                        "owner": {"type": "string", "enum": ["axiom", "forge", "sentinel"]},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "objective": {"type": "string"},
                        "deliverables": {"type": "array", "items": {"type": "string"}},
                        "done_when": {"type": "array", "items": {"type": "string"}},
                        "allowed_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Globs the owner may write, e.g. src/**",
                        },
                        "checks": {"type": "array", "items": _ARGV},
                    },
                    "required": ["key", "title", "owner", "objective", "done_when"],
                },
            },
        },
        "required": ["title", "objective", "success_criteria", "tasks"],
    },
)

_TOOLS: dict[str, ToolDefinition] = {
    "list_files": ToolDefinition(
        name="list_files",
        description="List files under a folder of your workspace (relative path, default '.').",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    ),
    "read_file": ToolDefinition(
        name="read_file",
        description="Read a text file from your workspace.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    "write_file": ToolDefinition(
        name="write_file",
        description="Create or overwrite a text file. Only paths your task allows.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    ),
    "edit_file": ToolDefinition(
        name="edit_file",
        description="Replace one exact, unique occurrence of old with new in a file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
    ),
    "run_command": ToolDefinition(
        name="run_command",
        description=(
            "Run an allowlisted command as an argv list in your workspace, sandboxed without "
            "network: ['python','-m','pytest','-q'], ['python','script.py'], "
            "['git','diff']. No shell."
        ),
        input_schema={
            "type": "object",
            "properties": {"argv": _ARGV},
            "required": ["argv"],
        },
    ),
    "submit_result": _SUBMIT_RESULT,
    "submit_review": _SUBMIT_REVIEW,
    "submit_plan": SUBMIT_PLAN,
}

FINAL_TOOLS = {"submit_result", "submit_review", "submit_plan"}


def definitions(names: tuple[str, ...]) -> list[ToolDefinition]:
    return [_TOOLS[n] for n in names]


@dataclass
class Scope:
    """Where one agent run may act."""

    mission_id: str
    task_id: str | None
    agent: str
    root: Path  # the worktree (or review checkout) the agent works in
    scratch: Path
    pythonpath: list[Path]
    writable: list[str]  # globs; empty = read-only
    timeout: float
    tools: tuple[str, ...]
    commands: list[dict[str, Any]] = field(default_factory=list)  # every command run


Audit = Callable[..., Awaitable[None]]


class Broker:
    def __init__(self, scope: Scope, audit: Audit) -> None:
        self.scope = scope
        self._audit = audit

    def _path(self, path: str, *, must_exist: bool) -> Path:
        rel = "." if path in ("", ".") else clean_relative(path)
        target = (self.scope.root / rel).resolve()
        root = self.scope.root.resolve()
        if target != root and not target.is_relative_to(root):
            raise PolicyError("workspace.read", f"'{path}' resolves outside the workspace.")
        if any(part == ".git" for part in target.relative_to(root).parts):
            raise PolicyError("workspace.read", "The .git folder isn't part of the workspace.")
        if must_exist and not target.exists():
            raise FileNotFoundError(path)
        return target

    async def execute(self, call: ToolCall) -> tuple[str, bool]:
        """Run one tool call; returns (content for the model, is_error)."""
        name, args = call.name, call.input
        if name not in self.scope.tools:
            await self._deny(name, args, "tool.not_granted", f"{name} isn't one of your tools.")
            return f"Refused: {name} isn't one of your tools.", True
        try:
            if name == "list_files":
                return await self._list(str(args.get("path", "."))), False
            if name == "read_file":
                return await self._read(str(args.get("path", ""))), False
            if name == "write_file":
                return await self._write(
                    str(args.get("path", "")), str(args.get("content", ""))
                ), False
            if name == "edit_file":
                return await self._edit(
                    str(args.get("path", "")), str(args.get("old", "")), str(args.get("new", ""))
                ), False
            if name == "run_command":
                return await self._run(args.get("argv"))
        except PolicyError as exc:
            await self._deny(name, args, exc.category, exc.message)
            return f"Refused by policy ({exc.category}): {exc.message}", True
        except FileNotFoundError:
            return f"No such file or folder: {args.get('path')}", True
        except (OSError, UnicodeDecodeError) as exc:
            return f"{name} failed: {exc}", True
        return f"Unknown tool {name}.", True

    async def _deny(self, tool: str, args: dict[str, Any], category: str, reason: str) -> None:
        await self._audit(
            kind="policy.denied",
            severity="warning",
            message=f"{self.scope.agent.upper()} denied: {reason}",
            data={"tool": tool, "category": category, "args": _brief(args)},
        )

    async def _list(self, path: str) -> str:
        base = self._path(path, must_exist=True)
        root = self.scope.root.resolve()
        files: list[str] = []
        for p in sorted(base.rglob("*")):
            rel = p.resolve().relative_to(root) if p.resolve().is_relative_to(root) else None
            if rel is None or ".git" in rel.parts or "__pycache__" in rel.parts:
                continue
            if p.is_file():
                files.append(str(rel))
            if len(files) >= MAX_LIST:
                files.append("… (more files not listed)")
                break
        await self._audit(
            kind="tool",
            severity="debug",
            message=f"{self.scope.agent.upper()} listed {path}",
            data={"tool": "list_files", "path": path, "count": len(files)},
        )
        return "\n".join(files) or "(empty)"

    async def _read(self, path: str) -> str:
        target = self._path(path, must_exist=True)
        if target.is_dir():
            return f"{path} is a folder; use list_files."
        text = target.read_text(encoding="utf-8", errors="replace")
        await self._audit(
            kind="tool",
            severity="debug",
            message=f"{self.scope.agent.upper()} read {path}",
            data={"tool": "read_file", "path": path, "bytes": len(text)},
        )
        if len(text) > MAX_READ_CHARS:
            return text[:MAX_READ_CHARS] + f"\n… [truncated, {len(text)} characters in total]"
        return text

    async def _write(self, path: str, content: str) -> str:
        rel = check_write(path, self.scope.writable)
        if len(content.encode()) > MAX_FILE_BYTES:
            raise PolicyError("workspace.write", f"{rel} is larger than {MAX_FILE_BYTES} bytes.")
        target = self._path(rel, must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        await self._audit(
            kind="tool",
            severity="info",
            message=f"{self.scope.agent.upper()} wrote {rel}",
            data={"tool": "write_file", "path": rel, "bytes": len(content)},
        )
        return f"Wrote {rel} ({len(content)} characters)."

    async def _edit(self, path: str, old: str, new: str) -> str:
        rel = check_write(path, self.scope.writable)
        target = self._path(rel, must_exist=True)
        text = target.read_text(encoding="utf-8")
        count = text.count(old) if old else 0
        if count != 1:
            return f"'old' must occur exactly once in {rel}; it occurs {count} times."
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        await self._audit(
            kind="tool",
            severity="info",
            message=f"{self.scope.agent.upper()} edited {rel}",
            data={"tool": "edit_file", "path": rel},
        )
        return f"Edited {rel}."

    async def _run(self, argv: Any) -> tuple[str, bool]:
        command = check_command(argv)
        outcome = await sandbox.run(
            command,
            cwd=self.scope.root,
            scratch=self.scope.scratch,
            pythonpath=self.scope.pythonpath,
            timeout=self.scope.timeout,
        )
        self.scope.commands.append(
            {
                "command": outcome.command,
                "exit_code": outcome.exit_code,
                "timed_out": outcome.timed_out,
                "seconds": outcome.seconds,
            }
        )
        await self._audit(
            kind="command",
            severity="info",
            message=f"{self.scope.agent.upper()} ran {outcome.command} → "
            + ("timeout" if outcome.timed_out else f"exit {outcome.exit_code}"),
            data={
                "command": outcome.command,
                "exit_code": outcome.exit_code,
                "seconds": outcome.seconds,
                "timed_out": outcome.timed_out,
            },
        )
        tail = outcome.output[-TAIL_FOR_MODEL:]
        status = "timed out" if outcome.timed_out else f"exit code {outcome.exit_code}"
        return f"{outcome.command}: {status}\n{tail}", not outcome.passed


def _brief(args: dict[str, Any]) -> dict[str, Any]:
    """Arguments for the audit log, without file contents."""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key in ("content", "old", "new"):
            out[key] = f"<{len(str(value))} characters>"
        else:
            out[key] = value if len(json.dumps(value, default=str)) < 400 else "<long>"
    return out
