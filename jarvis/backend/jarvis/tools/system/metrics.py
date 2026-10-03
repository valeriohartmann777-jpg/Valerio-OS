"""Host metrics via psutil — real on every platform."""

from __future__ import annotations

import platform
import socket
import time
from dataclasses import asdict, dataclass
from typing import Any

import psutil

_GB = 1024**3


@dataclass(frozen=True, slots=True)
class SystemMetrics:
    hostname: str
    os: str
    os_release: str
    cpu_percent: float
    cpu_count: int
    memory_percent: float
    memory_used_gb: float
    memory_total_gb: float
    uptime_seconds: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def prime_cpu_counter() -> None:
    """psutil's first non-blocking CPU reading is always 0.0; take it early."""
    psutil.cpu_percent(interval=None)


def read_metrics() -> SystemMetrics:
    memory = psutil.virtual_memory()
    return SystemMetrics(
        hostname=socket.gethostname(),
        os=platform.system(),
        os_release=platform.release(),
        cpu_percent=round(psutil.cpu_percent(interval=None), 1),
        cpu_count=psutil.cpu_count() or 1,
        memory_percent=round(memory.percent, 1),
        memory_used_gb=round((memory.total - memory.available) / _GB, 1),
        memory_total_gb=round(memory.total / _GB, 1),
        uptime_seconds=int(time.time() - psutil.boot_time()),
    )


def format_duration(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
