"""Draws the menu-bar icon: JARVIS's core (a ring around a dot) as a macOS
template image (black + alpha; macOS tints it for light and dark menu bars).

    python3 make-tray-icon.py   → trayTemplate.png (16 px), trayTemplate@2x.png (32 px)
"""

import math
import struct
import zlib
from pathlib import Path


def coverage(x: float, y: float, size: int) -> float:
    """How much of pixel (x, y) the shape covers, 0-1 (8x8 supersampling)."""
    centre, hits, steps = size / 2, 0, 8
    ring_mid, ring_half, dot = size * 0.39, size * 0.045, size * 0.14
    for i in range(steps):
        for j in range(steps):
            d = math.hypot(x + (i + 0.5) / steps - centre, y + (j + 0.5) / steps - centre)
            hits += abs(d - ring_mid) <= ring_half or d <= dot
    return hits / steps**2


def png(size: int) -> bytes:
    rows = b""
    for y in range(size):
        rows += b"\x00" + b"".join(
            bytes((0, 0, 0, round(255 * coverage(x, y, size)))) for x in range(size)
        )

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    here = Path(__file__).parent
    (here / "trayTemplate.png").write_bytes(png(16))
    (here / "trayTemplate@2x.png").write_bytes(png(32))
