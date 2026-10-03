"""Which code a running backend was started from (the git commit).

The desktop app compares it with its own checkout and replaces a backend left
over from an older version instead of silently talking to it.
"""

from __future__ import annotations

import os
import subprocess
from functools import cache

from jarvis.settings import PROJECT_ROOT


@cache
def build_id() -> str:
    if value := os.environ.get("JARVIS_BUILD"):
        return value
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else "unknown"
