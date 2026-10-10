"""Plain text as evidence: normalised once, split into paragraph segments with exact spans."""

from __future__ import annotations

import re
import unicodedata

MAX_TEXT = 200_000  # characters
PIECE = 600

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def normalize(text: str) -> str:
    """NFC, Unix newlines, no control characters, at most one blank line in a row."""
    out = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    out = _CONTROL.sub("", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def segments(text: str) -> list[tuple[int, int, str]]:
    """(start, end, text) spans into ``text``: paragraphs, long ones cut at sentences."""
    out: list[tuple[int, int, str]] = []
    for match in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text):
        start, para = match.start(), match.group(0)
        if len(para) <= PIECE:
            out.append((start, start + len(para), para))
            continue
        piece_start = 0
        for sentence in _SENTENCE.finditer(para):
            if sentence.start() - piece_start >= PIECE * 0.6:
                out.append(
                    (
                        start + piece_start,
                        start + sentence.start(),
                        para[piece_start : sentence.start()],
                    )
                )
                piece_start = sentence.end()
        while len(para) - piece_start > PIECE:  # no sentence breaks: hard cut
            out.append(
                (
                    start + piece_start,
                    start + piece_start + PIECE,
                    para[piece_start : piece_start + PIECE],
                )
            )
            piece_start += PIECE
        if piece_start < len(para):
            out.append((start + piece_start, start + len(para), para[piece_start:]))
    spans: list[tuple[int, int, str]] = []
    for a, b, t in out:
        if not t.strip():
            continue
        lead, trail = len(t) - len(t.lstrip()), len(t) - len(t.rstrip())
        spans.append((a + lead, b - trail, t.strip()))  # text[a:b] == the segment, exactly
    return spans
