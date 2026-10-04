"""Small user preferences set from the dashboard (data/preferences.json).

Configuration that people change by clicking lives here; everything else stays
in config/*.yaml. Written atomically.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class Preferences:
    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = {}
        self._values: dict[str, Any] = loaded if isinstance(loaded, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def update(self, **values: Any) -> None:
        self._values.update(values)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        tmp.write_text(json.dumps(self._values, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)
