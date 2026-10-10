#!/usr/bin/env python3
"""Stand-in for Claude Code's `claude` binary in tests (never a real model call).

Behaviour comes from $HOME/.fake-claude.json; every call is appended to
$HOME/.fake-claude-log.jsonl with its arguments, stdin and the environment's
variable names — so tests can prove what the child process could see.
"""

import json
import os
import sys
from pathlib import Path

home = Path(os.environ.get("HOME", "."))
cfg = (
    json.loads((home / ".fake-claude.json").read_text())
    if (home / ".fake-claude.json").exists()
    else {}
)
args = sys.argv[1:]
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
with (home / ".fake-claude-log.jsonl").open("a") as log:
    log.write(json.dumps({"args": args, "stdin": stdin[:4000], "env": sorted(os.environ)}) + "\n")

if args[:1] == ["--version"]:
    print("2.1.296 (Claude Code)")
    sys.exit(0)
if args[:2] == ["auth", "status"]:
    logged_in = cfg.get("logged_in", True)
    print(
        json.dumps(
            {
                "loggedIn": logged_in,
                "authMethod": cfg.get("method", "claude.ai") if logged_in else "none",
            }
        )
    )
    sys.exit(0 if logged_in else 1)

leak = [
    k
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
    if k in os.environ
]
mode = cfg.get("mode", "ok")
base = {
    "type": "result",
    "session_id": "fake",
    "num_turns": 2,
    "total_cost_usd": 0.0123,
    "usage": {
        "input_tokens": 120,
        "output_tokens": 30,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    },
}
if leak:
    print(
        json.dumps(
            {**base, "subtype": "success", "is_error": True, "result": "LEAK " + ",".join(leak)}
        )
    )
elif mode == "limit":
    print(
        json.dumps(
            {
                **base,
                "subtype": "success",
                "is_error": True,
                "api_error_status": 429,
                "result": "You've hit your session limit · resets 3:45pm",
            }
        )
    )
elif mode == "auth":
    print(
        json.dumps(
            {
                **base,
                "subtype": "success",
                "is_error": True,
                "result": "Not logged in · Please run /login",
            }
        )
    )
else:
    out = {
        **base,
        "subtype": "success",
        "is_error": False,
        "result": cfg.get("text", "Hello from the plan"),
    }
    if "--json-schema" in args:
        out["structured_output"] = cfg.get("structured", {"text": "Plan answer", "tool_calls": []})
    print(json.dumps(out))
