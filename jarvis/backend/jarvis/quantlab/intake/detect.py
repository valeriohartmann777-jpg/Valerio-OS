"""What a dropped file really is: magic bytes decide, the extension must agree.

A file whose bytes say one thing and whose name says another is refused, so a
renamed text file can't reach the video decoder and an archive can't reach any
parser. Limits per kind keep a single upload from exhausting disk or decoders.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import Literal

Kind = Literal["video", "audio", "image", "pdf", "text_file"]

MB = 1024 * 1024
LIMITS: dict[str, int] = {
    "video": 512 * MB,
    "audio": 200 * MB,
    "image": 25 * MB,
    "pdf": 40 * MB,
    "text_file": 2 * MB,
}
EXTENSIONS: dict[str, set[str]] = {
    "video": {".mp4", ".mov", ".m4v", ".webm"},
    "audio": {".wav", ".mp3", ".m4a"},
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "pdf": {".pdf"},
    "text_file": {".txt", ".md", ".markdown"},
}
HEAD_BYTES = 64 * 1024  # how much of the file sniffing looks at


class IntakeError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Detected:
    kind: Kind
    mime: str
    container: str  # mp4, mov, webm, wav, mp3, m4a, png, jpeg, webp, pdf, text


def _iso_media(head: bytes) -> tuple[Kind, str, str] | None:
    """ISO base media (MP4, MOV, M4A): an ``ftyp`` box first, its major brand decides."""
    if len(head) < 12 or head[4:8] != b"ftyp":
        return None
    brand = head[8:12]
    if brand == b"qt  ":
        return "video", "video/quicktime", "mov"
    if brand in (b"M4A ", b"M4B "):
        return "audio", "audio/mp4", "m4a"
    return "video", "video/mp4", "mp4"


def _sniff(head: bytes) -> tuple[Kind, str, str] | None:
    iso = _iso_media(head)
    if iso:
        return iso
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        # Matroska family: only WebM (its DocType) is accepted.
        if b"webm" in head[:64]:
            return "video", "video/webm", "webm"
        return None
    if head.startswith(b"RIFF") and len(head) >= 12:
        if head[8:12] == b"WAVE":
            return "audio", "audio/wav", "wav"
        if head[8:12] == b"WEBP":
            return "image", "image/webp", "webp"
        return None
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio", "audio/mpeg", "mp3"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png", "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg", "jpeg"
    if head.startswith(b"%PDF-"):
        return "pdf", "application/pdf", "pdf"
    return None


def _is_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    sample = head[3:] if head.startswith(b"\xef\xbb\xbf") else head
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut at the end of the sample is still text.
        if exc.start < len(sample) - 4:
            return False
    return True


def detect(head: bytes, filename: str, size: int) -> Detected:
    """Classify an upload by its first bytes; refuse anything ambiguous or oversized."""
    suffix = PurePath(filename or "").suffix.lower()
    if head.startswith((b"PK\x03\x04", b"Rar!", b"7z\xbc\xaf", b"\x1f\x8b")):
        raise IntakeError(
            "ARCHIVE_REFUSED",
            "Archives aren't opened. Drop the video, PDF, image or text file itself.",
        )
    if size <= 0:
        raise IntakeError("EMPTY_FILE", "The file is empty.")
    sniffed = _sniff(head)
    if sniffed is None and suffix in EXTENSIONS["text_file"] and _is_text(head):
        sniffed = ("text_file", "text/plain", "text")
    if sniffed is None:
        known = sorted(set().union(*EXTENSIONS.values()))
        raise IntakeError(
            "UNSUPPORTED_FILE",
            "This file type isn't supported — its contents don't match any accepted "
            f"format ({', '.join(known)}).",
        )
    kind, mime, container = sniffed
    if suffix not in EXTENSIONS[kind]:
        raise IntakeError(
            "TYPE_MISMATCH",
            f"The file is named '{suffix or 'without extension'}' but its contents are "
            f"{container.upper()} — refused, because a mislabelled file is not trusted.",
        )
    if size > LIMITS[kind]:
        raise IntakeError(
            "FILE_TOO_LARGE",
            f"{kind.replace('_', ' ').capitalize()} files are limited to "
            f"{LIMITS[kind] // MB} MB (this one is {size / MB:.0f} MB).",
            413,
        )
    return Detected(kind, mime, container)
