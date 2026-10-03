"""Structured logging: JSON lines to file, concise text to the console."""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path
from typing import Any

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and value is not None:
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        trace = getattr(record, "trace_id", None)
        prefix = (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname[:4]:<4} {record.name:<18}"
        )
        line = f"{prefix} {record.getMessage()}" + (f"  [{trace}]" if trace else "")
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("jarvis")
    root.setLevel(logging.DEBUG)
    root.propagate = False
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "jarvis.jsonl", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonFormatter())

    console = logging.StreamHandler()
    console.setLevel(level.upper())
    console.setFormatter(ConsoleFormatter())

    root.addHandler(file_handler)
    root.addHandler(console)
