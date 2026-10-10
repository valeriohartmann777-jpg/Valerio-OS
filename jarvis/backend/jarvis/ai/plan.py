"""The Claude plan route: the official Claude Code CLI in print mode (``claude -p``).

Anthropic documents that the Claude Agent SDK and ``claude -p`` may use a Claude plan's
subscription limits (support article 15036540, 7 Oct 2026). JARVIS uses exactly that and
nothing else:

- it runs the owner's installed ``claude`` binary; Claude Code signs in and talks to
  Anthropic itself — JARVIS never reads, copies or forwards an OAuth token or cookie;
- the child process gets an allow-listed environment, so ``ANTHROPIC_API_KEY`` and other
  credentials of JARVIS's own process can't switch Claude Code to API billing;
- before use, ``claude auth status`` must report ``authMethod: claude.ai`` (a plan login);
  an API-key, token-helper or cloud-provider login is reported as *not supported* instead;
- the run is locked down: no built-in tools, no MCP servers, no hooks/plugins/CLAUDE.md
  (``--safe-mode``), nothing persisted, an empty working directory.

JARVIS's own tools are offered as a JSON schema (``--json-schema``); the model's tool calls
come back as structured output and run through JARVIS's tool layer with all its permission
gates — Claude Code itself executes nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis.ai import messages as msg
from jarvis.ai.classify import cli_error
from jarvis.llm.base import (
    ModelError,
    ModelReply,
    ToolCall,
    ToolDefinition,
    ToolOutcome,
    Usage,
)

log = logging.getLogger("jarvis.ai.plan")

# The only environment the child sees. Credentials are deliberately absent.
_ENV_ALLOW = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LANGUAGE",
    "TERM",
    "TMPDIR",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
    "CLAUDE_CONFIG_DIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    # Windows
    "SYSTEMROOT",
    "SystemRoot",
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
    "TEMP",
    "TMP",
}
# Never passed on, even if someone adds them to the allow-list by mistake.
_NEVER = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_PROFILE",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
_EXTRA_DIRS = (
    "~/.local/bin",
    "~/.claude/local",
    "~/.npm-global/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
)
PLAN_LOGIN = "claude.ai"


def isolated_env(base: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if k in _ENV_ALLOW or k.startswith("LC_")}
    for name in _NEVER:
        env.pop(name, None)
    extra = [os.path.expanduser(d) for d in _EXTRA_DIRS]
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), *extra]).strip(os.pathsep)
    env["DISABLE_AUTOUPDATER"] = "1"  # JARVIS never changes the owner's Claude Code install
    return env


Runner = Callable[[list[str], bytes | None, float, Path], Awaitable[tuple[int, bytes, bytes]]]


async def _run(
    args: list[str], stdin: bytes | None, limit: float, cwd: Path
) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=isolated_env(),
        cwd=str(cwd),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), limit)
    except (TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise
    return int(proc.returncode or 0), out, err


@dataclass(frozen=True)
class AuthStatus:
    installed: bool
    version: str | None
    logged_in: bool
    method: str | None  # none | claude.ai | oauth_token | api_key | api_key_helper | third_party
    error: str | None = None

    @property
    def plan_login(self) -> bool:
        return self.logged_in and self.method == PLAN_LOGIN


class ClaudeCli:
    """The owner's Claude Code installation, run in an isolated child process."""

    def __init__(
        self,
        *,
        path: str = "",
        workdir: Path,
        runner: Runner | None = None,
        timeout_seconds: float = 300.0,
    ) -> None:
        self._configured = path
        self._workdir = workdir
        self._runner = runner or _run
        self.timeout_seconds = timeout_seconds

    @property
    def workdir(self) -> Path:
        self._workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self._workdir

    def find(self) -> str | None:
        if self._configured:
            expanded = os.path.expanduser(self._configured)
            return expanded if os.path.isfile(expanded) or self._runner is not _run else None
        if self._runner is not _run:
            return "claude"  # tests: the fake runner stands in for the binary
        found = shutil.which("claude", path=isolated_env()["PATH"])
        if found is None and sys.platform == "win32":
            found = shutil.which("claude.cmd", path=isolated_env()["PATH"])
        return found

    async def run(
        self, args: list[str], stdin: bytes | None = None, limit: float | None = None
    ) -> tuple[int, str, str]:
        binary = self.find()
        if binary is None:
            raise ModelError(
                "not_configured",
                "Claude Code isn't installed on this computer.",
                suggestion="Settings → AI & Billing → Install Claude Code.",
            )
        try:
            code, out, err = await self._runner(
                [binary, *args], stdin, limit or self.timeout_seconds, self.workdir
            )
        except TimeoutError:
            raise ModelError(
                "timeout",
                "Claude Code took too long to answer.",
                suggestion="JARVIS retries shortly.",
                retryable=True,
            ) from None
        except OSError as exc:
            raise ModelError(
                "not_configured",
                "Claude Code couldn't be started.",
                suggestion="Reinstall Claude Code, then check again in Settings → AI & Billing.",
                detail=type(exc).__name__,
            ) from None
        return code, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def status(self) -> AuthStatus:
        """Installed? Signed in, and how? Free: no model call, no usage."""
        if self.find() is None:
            return AuthStatus(False, None, False, None)
        try:
            code, out, _ = await self.run(["--version"], limit=20)
            version = out.strip().split()[0] if code == 0 and out.strip() else None
            code, out, err = await self.run(["auth", "status"], limit=20)
        except ModelError as exc:
            return AuthStatus(True, None, False, None, exc.message)
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return AuthStatus(True, version, False, None, (err or out).strip()[:200] or None)
        # Only these two fields are read; nothing else (e.g. account details) is kept.
        return AuthStatus(
            True,
            version,
            bool(data.get("loggedIn")) and code == 0,
            str(data.get("authMethod") or "none"),
        )


def _tool_section(tools: list[ToolDefinition]) -> str:
    lines = [
        "",
        "# Tools",
        "You can call the tools below. To call tools, list them in `tool_calls` of your JSON "
        "answer (name + input that matches the tool's input schema); several at once is fine. "
        "Their results arrive in the next user turn as <tool_result> blocks. Never claim a "
        "tool ran before you have seen its result. When you are done, return an empty "
        "`tool_calls` list.",
    ]
    for tool in tools:
        lines += [
            "",
            f"## {tool.name}",
            tool.description,
            "Input schema: " + json.dumps(tool.input_schema, ensure_ascii=False),
        ]
    return "\n".join(lines)


def _schema(tools: list[ToolDefinition]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "What you say (may be empty)."},
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": [t.name for t in tools]},
                        "input": {"type": "object"},
                    },
                    "required": ["name", "input"],
                },
            },
        },
        "required": ["text", "tool_calls"],
    }


_INSTRUCTION = "The conversation so far is in the input. Write only the assistant's next turn."
_INSTRUCTION_TOOLS = (
    "The conversation so far is in the input. Write only the assistant's next turn as JSON: "
    "`text` for what you say, `tool_calls` for the tools to call now (empty if none)."
)


class PlanChatModel:
    """One Claude model on the owner's Claude plan, through Claude Code."""

    route = "plan"

    def __init__(
        self,
        cli: ClaudeCli,
        *,
        model: str,
        effort: str | None = None,
        max_turns: int = 4,
        timeout_seconds: float | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._cli = cli
        self._model = model
        self._effort = effort
        self._max_turns = max_turns
        self._timeout = timeout_seconds
        self._clock = clock or (lambda: datetime.now().astimezone())

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def label(self) -> str:
        from jarvis.llm.anthropic_provider import _label

        return f"{_label(self._model)} · Claude plan"

    def user_message(self, blocks: list[str]) -> Any:
        return msg.user_message(blocks)

    def assistant_text(self, text: str) -> Any:
        return msg.assistant_text(text)

    def tool_results(self, outcomes: list[ToolOutcome]) -> Any:
        return msg.tool_results(outcomes)

    async def complete(
        self,
        *,
        system: str,
        messages: list[Any],
        tools: list[ToolDefinition],
        server_tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply:
        if server_tools:
            raise ModelError(
                "capability",
                "Web search isn't available on the Claude plan route.",
                detail="web_search server tool is not supported by the plan route",
            )
        if msg.has_images(messages):
            raise ModelError(
                "capability",
                "Images and documents can't be passed on the Claude plan route.",
                detail="image/document blocks are not supported by the plan route",
            )
        prompt_file = None
        try:
            fd, name = tempfile.mkstemp(prefix="system-", suffix=".txt", dir=self._cli.workdir)
            prompt_file = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:  # created 0600
                handle.write(system + (_tool_section(tools) if tools else ""))
            args = [
                "-p",
                _INSTRUCTION_TOOLS if tools else _INSTRUCTION,
                "--output-format",
                "json",
                "--model",
                self._model,
                "--system-prompt-file",
                str(prompt_file),
                "--tools",
                "",
                "--strict-mcp-config",
                "--disallowedTools",
                "mcp__*",
                "--permission-mode",
                "dontAsk",
                "--safe-mode",
                "--no-session-persistence",
                "--max-turns",
                str(self._max_turns),
            ]
            if self._effort:
                args += ["--effort", self._effort]
            if tools:
                args += ["--json-schema", json.dumps(_schema(tools))]
            code, out, err = await self._cli.run(
                args, msg.render_transcript(messages).encode("utf-8"), self._timeout
            )
        finally:
            if prompt_file is not None:
                with contextlib.suppress(OSError):
                    prompt_file.unlink()
        return self._reply(code, out, err, tools)

    def _reply(self, code: int, out: str, err: str, tools: list[ToolDefinition]) -> ModelReply:
        result = _last_json(out)
        if result is None:
            raise cli_error(err or out, None, now=self._clock(), detail=(err or out)[-600:])
        status = result.get("api_error_status")
        status = int(status) if isinstance(status, int | float) else None
        text = str(result.get("result") or "")
        if result.get("is_error") or result.get("subtype") not in (None, "success") or code != 0:
            raise cli_error(text or err, status, now=self._clock())
        calls: list[ToolCall] = []
        if tools:
            data = result.get("structured_output")
            if not isinstance(data, dict):
                raise ModelError(
                    "bad_request",
                    "Claude Code returned no structured answer.",
                    detail=text[:600],
                )
            text = str(data.get("text") or "")
            for item in data.get("tool_calls") or []:
                if isinstance(item, dict) and item.get("name"):
                    calls.append(
                        ToolCall(
                            id="toolu_plan_" + uuid.uuid4().hex[:20],
                            name=str(item["name"]),
                            input=dict(item.get("input") or {}),
                        )
                    )
        usage = result.get("usage") or {}
        content: list[dict[str, Any]] = []
        if text.strip():
            content.append({"type": "text", "text": text})
        content += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls
        ]
        if not content:
            content.append({"type": "text", "text": "(no answer)"})
        cost = result.get("total_cost_usd")
        return ModelReply(
            text=text.strip(),
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            assistant_message={"role": "assistant", "content": content},
            model=self._model,
            usage=Usage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
                cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            ),
            route="plan",
            list_cost_usd=float(cost) if isinstance(cost, int | float) else None,
        )


def _last_json(out: str) -> dict[str, Any] | None:
    for line in reversed([ln for ln in out.strip().splitlines() if ln.strip()]):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("type") in ("result", None):
            return data
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
