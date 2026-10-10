"""Idea intake: sources in, time-coded evidence out.

* Text is normalised and split into paragraph segments with exact character spans.
* Files are streamed to disk while hashed and size-capped, identified by their
  bytes (not their names), deduplicated by SHA-256 and stored privately.
* Media is decoded only in the sandboxed worker process
  (``jarvis.quantlab.intake.worker``): probe → speech → frames/on-screen text.
* Links are matched against an allowlist; only official oEmbed metadata is read.

Extracted text is evidence, never instruction: instruction-like passages are
flagged and audited. Times are positions in the source, not market time. The
originals of dropped media are deleted after ``keep_media_days``; transcripts,
keyframes and hashes stay until the user deletes the source.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import jarvis
from jarvis.events.bus import EventBus
from jarvis.events.types import Event, EventType, Severity
from jarvis.quantlab.intake import injection
from jarvis.quantlab.intake import text as textmod
from jarvis.quantlab.intake.detect import HEAD_BYTES, LIMITS, IntakeError, detect
from jarvis.quantlab.intake.store import SourceStore
from jarvis.quantlab.intake.urls import LinkResolver, parse_link
from jarvis.settings import IntakeSettings
from jarvis.ultron import sandbox
from jarvis.ultron.policy import Command
from jarvis.util import new_id, utcnow

log = logging.getLogger("jarvis.quantlab.intake")

BACKEND_ROOT = Path(jarvis.__file__).resolve().parent.parent
ACTIVE = ("QUEUED", "EXTRACTING")
READY = ("EXTRACTED", "PARTIAL")
WHISPER_REPO = "Systran/faster-whisper-{size}"
_ID = re.compile(r"^src-[0-9a-f]{16}$")
_FRAME_ID = re.compile(r"^fr-\d{1,9}$")
_NAME = re.compile(r"[^\w.\- ]+")

ReadyHook = Callable[[str], Awaitable[None]]


def _exists(path: str | Path | None) -> bool:
    return bool(path) and Path(str(path)).exists()


def _unlink(path: str | Path) -> None:
    Path(path).unlink(missing_ok=True)


def _read_text_file(path: str) -> str:
    return Path(path).read_bytes().decode("utf-8", errors="replace")


def _inside(folder: Path, path: str) -> Path | None:
    resolved = Path(path).resolve()
    return resolved if folder.resolve() in resolved.parents and resolved.exists() else None


class _Spool:
    """Writes an upload to a private temp file (sync file I/O, called from the stream loop)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = open(path, "wb")  # closed in close()
        os.chmod(path, 0o600)

    def write(self, chunk: bytes) -> None:
        self._file.write(chunk)

    def close(self) -> None:
        self._file.close()


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _source_id(fingerprint: str) -> str:
    return "src-" + _sha(fingerprint)[:16]


def _title(text: str) -> str:
    first = text.strip().split("\n", 1)[0]
    return (first[:77] + "…") if len(first) > 80 else first or "Untitled"


def _filename(name: str) -> str:
    base = Path(name or "upload").name
    return _NAME.sub("_", base)[:120] or "upload"


class SourceService:
    def __init__(
        self,
        *,
        db: Any,
        bus: EventBus,
        root: Path,
        settings: IntakeSettings,
        resolver: LinkResolver | None = None,
        isolation: sandbox.Isolation | None = None,
    ) -> None:
        self.store = SourceStore(db)
        self._bus = bus
        self._root = root
        self._settings = settings
        self._resolver = resolver or LinkResolver()
        self._isolation = isolation
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._current: tuple[str, asyncio.Task[None]] | None = None
        self._on_ready: ReadyHook | None = None
        self._model_job: asyncio.Task[None] | None = None
        self._model_state: dict[str, Any] = {"status": "idle"}

    def set_on_ready(self, hook: ReadyHook | None) -> None:
        self._on_ready = hook

    @property
    def settings(self) -> IntakeSettings:
        return self._settings

    # lifecycle ------------------------------------------------------------------------------

    async def start(self) -> None:
        for sub in ("sources", "work", "incoming", "models"):
            (self._root / sub).mkdir(parents=True, exist_ok=True)
        for leftover in (self._root / "incoming").glob("*.part"):
            leftover.unlink(missing_ok=True)
        for src in await self.store.in_status(*ACTIVE):
            # Extraction is deterministic: an interrupted one simply runs again.
            await self.store.transition(src["id"], ACTIVE, "QUEUED", stage=None)
            await self.store.audit(src["id"], "EXTRACTION_RESUMED", {"after": "restart"})
            self._queue.put_nowait(src["id"])
        await self.purge_expired()
        self._tasks = [
            asyncio.create_task(self._loop(), name="quantlab-intake"),
            asyncio.create_task(self._purge_loop(), name="quantlab-intake-retention"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._current is not None:
            self._current[1].cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []

    async def wait_idle(self, timeout: float = 120.0) -> None:  # noqa: ASYNC109
        """Tests: wait until the queue is drained and nothing is extracting."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if (
                self._queue.empty()
                and self._current is None
                and not await self.store.in_status(*ACTIVE)
            ):
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("intake still busy")

    # events ---------------------------------------------------------------------------------

    async def _emit(self, source_id: str, message: str, severity: Severity = Severity.INFO) -> None:
        src = await self.store.get(source_id)
        payload = {
            "source_id": source_id,
            "status": src["status"] if src else "DELETED",
            "stage": src["stage"] if src else None,
            "title": src["title"] if src else None,
        }
        await self._bus.publish(
            Event(type=EventType.QUANTLAB_SOURCE, message=message, severity=severity, data=payload)
        )

    # intake: text ---------------------------------------------------------------------------

    async def add_text(self, raw: str, note: str | None = None) -> dict[str, Any]:
        norm = textmod.normalize(raw or "")
        if not norm:
            raise IntakeError("EMPTY_TEXT", "There's no text to analyse.")
        if len(norm) > textmod.MAX_TEXT:
            raise IntakeError(
                "TEXT_TOO_LONG", f"Text is limited to {textmod.MAX_TEXT:,} characters.", 413
            )
        fingerprint = "text:" + _sha(norm)
        existing = await self.store.by_fingerprint(fingerprint)
        if existing:
            return await self._duplicate(existing, note)
        id_ = _source_id(fingerprint)
        folder = self._folder(id_)
        folder.mkdir(parents=True, exist_ok=True)
        original = folder / "original.txt"
        original.write_bytes(raw.encode("utf-8"))
        os.chmod(original, 0o600)
        await self.store.add(
            {
                "id": id_,
                "fingerprint": fingerprint,
                "kind": "text",
                "title": _title(norm),
                "status": "EXTRACTED",
                "size_bytes": len(raw.encode("utf-8")),
                "language": None,
                "meta": {
                    "coverage": {"text": {"characters": len(norm)}},
                    "warnings": [],
                    "sha256": _sha(raw),
                },
            }
        )
        await self._store_text_segments(id_, norm, "text", "plain-text")
        await self.store.audit(id_, "SOURCE_RECEIVED", {"kind": "text", "characters": len(norm)})
        if note:
            await self.add_note(id_, note)
        await self._emit(id_, "Idea received as text")
        await self._ready(id_)
        return await self.source(id_)

    async def _store_text_segments(self, id_: str, norm: str, modality: str, provider: str) -> None:
        segments = [
            {
                "modality": modality,
                "char_start": a,
                "char_end": b,
                "text": t,
                "quality": "ok",
                "provider": provider,
            }
            for a, b, t in textmod.segments(norm)
        ]
        await self._flag_and_store(id_, segments)

    async def _flag_and_store(self, id_: str, segments: list[dict[str, Any]]) -> None:
        for number, seg in enumerate(segments, start=1):
            hits = injection.scan(seg["text"])
            if hits:
                seg["flags"] = sorted({*(seg.get("flags") or []), "instruction_like"})
                await self.store.audit(
                    id_,
                    "INJECTION_SUSPECTED",
                    {
                        "segment": f"{id_}/s{number}",
                        "patterns": hits,
                        "excerpt": seg["text"][:160],
                        "handling": "kept as quoted evidence; never executed or obeyed",
                    },
                )
        await self.store.replace_segments(id_, segments)

    async def _duplicate(self, existing: dict[str, Any], note: str | None) -> dict[str, Any]:
        await self.store.audit(existing["id"], "DUPLICATE_SUBMITTED", {})
        if note:
            await self.add_note(existing["id"], note)
        detail = await self.source(existing["id"])
        detail["duplicate"] = True
        return detail

    # intake: links --------------------------------------------------------------------------

    async def add_link(self, url: str, note: str | None = None) -> dict[str, Any]:
        link = parse_link(url)
        fingerprint = "url:" + link.canonical
        existing = await self.store.by_fingerprint(fingerprint)
        if existing:
            return await self._duplicate(existing, note)
        id_ = _source_id(fingerprint)
        await self.store.add(
            {
                "id": id_,
                "fingerprint": fingerprint,
                "kind": "link",
                "title": link.canonical,
                "status": "RESOLVING",
                "origin_url": link.canonical,
                "meta": {"provider": link.provider},
            }
        )
        if note:
            await self.add_note(id_, note)
        await self._emit(id_, "Link received")
        resolution = await self._resolver.resolve(link)
        meta = {
            "provider": resolution.link.provider,
            "canonical": resolution.link.canonical,
            "metadata": resolution.metadata,
            "notes": resolution.notes,
            "error": resolution.error,
            "warnings": [],
        }
        segments: list[dict[str, Any]] = []
        caption = str(resolution.metadata.get("title") or "").strip()
        if caption:
            segments.append(
                {
                    "modality": "metadata",
                    "text": caption,
                    "quality": "ok",
                    "provider": f"{resolution.link.provider}-oembed",
                    "flags": ["creator_caption"],
                }
            )
        await self._flag_and_store(id_, segments)
        author = str(resolution.metadata.get("author_name") or "")
        title = caption[:80] if caption else resolution.link.canonical
        await self.store.update(
            id_,
            status="NEEDS_UPLOAD",
            capability=resolution.capability,
            fallback=resolution.fallback,
            title=f"{title} — {author}" if author else title,
            meta=meta,
            error=resolution.error,
        )
        await self.store.audit(
            id_,
            "LINK_RESOLVED",
            {
                "provider": resolution.link.provider,
                "capability": resolution.capability,
                "fetched": "oEmbed metadata only" if resolution.metadata else "nothing",
                "error": resolution.error,
            },
        )
        await self._emit(id_, f"Link checked: {resolution.capability.replace('_', ' ').lower()}")
        return await self.source(id_)

    # intake: files --------------------------------------------------------------------------

    async def receive_upload(
        self,
        chunks: AsyncIterator[bytes],
        filename: str,
        *,
        note: str | None = None,
        language: str | None = None,
        link_source_id: str | None = None,
    ) -> dict[str, Any]:
        if link_source_id is not None:
            link = await self._get(link_source_id)
            if link["kind"] != "link":
                raise IntakeError("NOT_A_LINK", "Files can only be attached to a link source.")
        name = _filename(filename)
        incoming = self._root / "incoming" / f"{uuid.uuid4().hex}.part"
        incoming.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        head = b""
        size = 0
        limit = max(LIMITS.values())
        spool = _Spool(incoming)
        try:
            try:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    if len(head) < HEAD_BYTES:
                        head += chunk[: HEAD_BYTES - len(head)]
                        if len(head) >= 16 and limit == max(LIMITS.values()):
                            with contextlib.suppress(IntakeError):
                                limit = LIMITS[detect(head, name, 1).kind]
                    size += len(chunk)
                    if size > limit:
                        raise IntakeError(
                            "FILE_TOO_LARGE",
                            f"The file exceeds the {limit // (1024 * 1024)} MB limit for its type.",
                            413,
                        )
                    hasher.update(chunk)
                    spool.write(chunk)
            finally:
                spool.close()
            detected = detect(head, name, size)
        except BaseException:
            incoming.unlink(missing_ok=True)
            raise
        sha = hasher.hexdigest()
        fingerprint = "file:" + sha
        existing = await self.store.by_fingerprint(fingerprint)
        if existing:
            incoming.unlink(missing_ok=True)
            if link_source_id:
                await self.store.update(link_source_id, linked_source_id=existing["id"])
            return await self._duplicate(existing, note)
        id_ = _source_id(fingerprint)
        folder = self._folder(id_)
        folder.mkdir(parents=True, exist_ok=True)
        ext = "txt" if detected.kind == "text_file" else detected.container
        stored = folder / f"original.{ext}"
        shutil.move(str(incoming), stored)
        os.chmod(stored, 0o600)
        lang = (language or self._settings.default_language or "en").lower()[:8]
        origin = None
        if link_source_id:
            origin = (await self._get(link_source_id)).get("origin_url")
        await self.store.add(
            {
                "id": id_,
                "fingerprint": fingerprint,
                "kind": detected.kind,
                "title": name,
                "status": "QUEUED",
                "filename": name,
                "mime": detected.mime,
                "size_bytes": size,
                "language": lang,
                "media_path": str(stored),
                "origin_url": origin,
                "meta": {"sha256": sha, "container": detected.container, "warnings": []},
            }
        )
        await self.store.audit(
            id_,
            "SOURCE_RECEIVED",
            {"kind": detected.kind, "bytes": size, "sha256": sha, "stored": "private, local only"},
        )
        if link_source_id:
            await self.store.update(link_source_id, linked_source_id=id_)
            await self.store.audit(link_source_id, "UPLOAD_ATTACHED", {"source": id_})
        if note:
            await self.add_note(id_, note)
        await self._emit(id_, f"{detected.kind.replace('_', ' ').capitalize()} received")
        self._queue.put_nowait(id_)
        return await self.source(id_)

    async def add_note(self, source_id: str, text: str) -> dict[str, Any]:
        await self._get(source_id)
        clean = textmod.normalize(text)[:4000]
        if not clean:
            raise IntakeError("EMPTY_NOTE", "The note is empty.")
        await self.store.add_note("note-" + new_id(), source_id, clean)
        await self.store.audit(source_id, "NOTE_ADDED", {"characters": len(clean)})
        await self._emit(source_id, "Note added")
        return {"source_id": source_id, "notes": await self.store.notes(source_id)}

    # control --------------------------------------------------------------------------------

    async def extract(self, source_id: str) -> dict[str, Any]:
        src = await self._get(source_id)
        if src["kind"] in ("text", "link"):
            raise IntakeError("NOTHING_TO_EXTRACT", "Text and links don't need extraction.")
        if src["status"] in ACTIVE:
            return await self.source(source_id)
        if not _exists(src["media_path"]):
            raise IntakeError(
                "MEDIA_DELETED", "The original was deleted; drop the file again to re-extract."
            )
        await self.store.transition(source_id, (src["status"],), "QUEUED", stage=None, error=None)
        await self.store.audit(source_id, "EXTRACTION_REQUESTED", {})
        self._queue.put_nowait(source_id)
        await self._emit(source_id, "Extraction queued")
        return await self.source(source_id)

    async def cancel(self, source_id: str) -> dict[str, Any]:
        await self._get(source_id)
        if await self.store.transition(source_id, ("QUEUED",), "CANCELED"):
            await self.store.audit(source_id, "EXTRACTION_CANCELED", {"while": "queued"})
        elif self._current is not None and self._current[0] == source_id:
            self._current[1].cancel()
        await self._emit(source_id, "Extraction cancelled")
        return await self.source(source_id)

    async def delete_media(self, source_id: str) -> dict[str, Any]:
        src = await self._get(source_id)
        await self._drop_media(src, "user")
        return await self.source(source_id)

    async def _drop_media(self, src: dict[str, Any], by: str) -> None:
        path = src.get("media_path")
        if not path:
            return
        _unlink(path)
        await self.store.update(
            src["id"], media_path=None, media_expires_at=None, media_deleted_at=utcnow().isoformat()
        )
        await self.store.audit(
            src["id"], "MEDIA_DELETED", {"by": by, "kept": "transcript, keyframes, hashes"}
        )
        await self._emit(src["id"], "Original media deleted")

    async def delete(self, source_id: str) -> None:
        src = await self._get(source_id)
        if self._current is not None and self._current[0] == source_id:
            self._current[1].cancel()
        shutil.rmtree(self._folder(source_id), ignore_errors=True)
        shutil.rmtree(self._root / "work" / source_id, ignore_errors=True)
        await self.store.delete(source_id)
        await self.store.audit(None, "SOURCE_DELETED", {"source": source_id, "kind": src["kind"]})
        await self._bus.publish(
            Event(
                type=EventType.QUANTLAB_SOURCE,
                message="Source deleted",
                data={"source_id": source_id, "status": "DELETED"},
            )
        )

    async def purge_expired(self) -> int:
        due = await self.store.media_due(utcnow().isoformat())
        for src in due:
            await self._drop_media(src, "retention")
        return len(due)

    async def _purge_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)
            with contextlib.suppress(Exception):
                await self.purge_expired()

    # reading --------------------------------------------------------------------------------

    def _folder(self, source_id: str) -> Path:
        if not _ID.match(source_id):
            raise IntakeError("SOURCE_NOT_FOUND", "No such source.", 404)
        return self._root / "sources" / source_id

    async def _get(self, source_id: str) -> dict[str, Any]:
        self._folder(source_id)
        src = await self.store.get(source_id)
        if src is None:
            raise IntakeError("SOURCE_NOT_FOUND", "No such source.", 404)
        return src

    async def sources(self) -> list[dict[str, Any]]:
        out = []
        for src in await self.store.recent():
            out.append(self._public(src))
        return out

    def _public(self, src: dict[str, Any]) -> dict[str, Any]:
        item = {k: v for k, v in src.items() if k not in ("media_path", "fingerprint")}
        item["media_available"] = bool(src.get("media_path"))
        return item

    async def source(self, source_id: str) -> dict[str, Any]:
        src = await self._get(source_id)
        frames = await self.store.frames(source_id)
        detail = self._public(src)
        detail["segments"] = await self.store.segments(source_id)
        detail["frames"] = [{k: v for k, v in f.items() if k != "path"} for f in frames]
        detail["notes"] = await self.store.notes(source_id)
        detail["claims"] = await self.store.claims(source_id)
        detail["blueprints"] = await self.store.blueprints(source_id)
        detail["extractions"] = await self.store.extractions(source_id)
        detail["audit"] = await self.store.audit_log(source_id, 60)
        if src.get("linked_source_id"):
            linked = await self.store.get(src["linked_source_id"])
            detail["linked"] = self._public(linked) if linked else None
        return detail

    async def frame_file(self, source_id: str, frame_id: str) -> Path:
        if not _FRAME_ID.match(frame_id):
            raise IntakeError("FRAME_NOT_FOUND", "No such frame.", 404)
        frame = await self.store.frame(source_id, frame_id)
        if frame is None:
            raise IntakeError("FRAME_NOT_FOUND", "No such frame.", 404)
        path = _inside(self._folder(source_id), frame["path"])
        if path is None:
            raise IntakeError("FRAME_NOT_FOUND", "No such frame.", 404)
        return path

    async def media_file(self, source_id: str) -> tuple[Path, str]:
        src = await self._get(source_id)
        if not _exists(src.get("media_path")):
            raise IntakeError("MEDIA_DELETED", "The original was deleted.", 404)
        return Path(src["media_path"]), str(src.get("mime") or "application/octet-stream")

    # extraction -----------------------------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            source_id = await self._queue.get()
            task = asyncio.create_task(self._extract(source_id))
            self._current = (source_id, task)
            try:
                await task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise  # JARVIS is stopping: the source resumes on the next start
                # Only this extraction was cancelled (by the user); the worker is already killed.
                await self.store.transition(source_id, ACTIVE, "CANCELED", stage=None)
                await self.store.audit(source_id, "EXTRACTION_CANCELED", {"while": "running"})
                await self._emit(source_id, "Extraction cancelled")
            except Exception as exc:  # never lose the loop to one bad source
                log.exception("intake failed", extra={"source": source_id})
                await self.store.transition(
                    source_id,
                    ACTIVE,
                    "FAILED",
                    stage=None,
                    error=f"Internal error: {type(exc).__name__}",
                )
                await self._emit(source_id, "Extraction failed", Severity.ERROR)
            finally:
                self._current = None

    def _timeout(self, duration_ms: int | None) -> float:
        seconds = (duration_ms or 60_000) / 1000
        return 90.0 + self._settings.worker_timeout_factor * seconds

    async def _stage(
        self, source_id: str, work: Path, stage: str, job: dict[str, Any], limit: float
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Run one worker stage; returns (result, problem)."""
        await self.store.update(source_id, stage=stage)
        await self._emit(source_id, f"Extracting: {stage}")
        (work / "job.json").write_text(json.dumps(job), encoding="utf-8")
        (work / f"{stage}.json").unlink(missing_ok=True)
        outcome = await sandbox.run(
            Command(
                ("python", "-P", "-m", "jarvis.quantlab.intake.worker", stage, "job.json"),
                f"intake {stage}",
            ),
            cwd=work,
            scratch=work / ".scratch",
            pythonpath=[BACKEND_ROOT],
            timeout=limit,
            isolation=self._isolation,
        )
        result_file = work / f"{stage}.json"
        if outcome.timed_out:
            return None, f"{stage}: stopped after {limit:.0f} s (time limit)"
        if not result_file.exists():
            tail = outcome.output.strip().splitlines()[-1:] if outcome.output else []
            return None, f"{stage}: the worker stopped unexpectedly ({' '.join(tail)[:200]})"
        data = json.loads(result_file.read_text(encoding="utf-8"))
        if "error" in data:
            return data, f"{data['error']['message']}"
        return data, None

    async def _extract(self, source_id: str) -> None:
        src = await self.store.get(source_id)
        if src is None or not await self.store.transition(source_id, ("QUEUED",), "EXTRACTING"):
            return
        await self.store.audit(source_id, "EXTRACTION_STARTED", {"isolation": self._iso().kind})
        kind = src["kind"]
        if kind == "text_file":
            raw = _read_text_file(src["media_path"])
            norm = textmod.normalize(raw)[: textmod.MAX_TEXT]
            await self._store_text_segments(source_id, norm, "text", "plain-text")
            await self._finish(src, [], {"text": {"characters": len(norm)}}, [], None)
            return
        work = self._root / "work" / source_id
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        job: dict[str, Any] = {
            "source": src["media_path"],
            "max_ms": int(self._settings.max_video_minutes * 60_000),
            "language": src.get("language") or "en",
            "cpu_seconds": 3600,
        }
        warnings: list[str] = []
        coverage: dict[str, Any] = {}
        segments: list[dict[str, Any]] = []
        frames: list[dict[str, Any]] = []
        try:
            if kind in ("video", "audio"):
                probe, problem = await self._stage(source_id, work, "probe", job, 60)
                if problem or probe is None:
                    await self._refuse(src, problem or "unreadable media")
                    return
                info = probe["probe"]
                await self.store.update(source_id, duration_ms=info["duration_ms"])
                job["duration_ms"] = info["duration_ms"]
                coverage["media"] = info
                timeout = self._timeout(info["duration_ms"])
                if info["has_audio"]:
                    speech_segments, speech_cov, warn = await self._speech(
                        source_id, work, job, timeout
                    )
                    segments += speech_segments
                    coverage["speech"] = speech_cov
                    warnings += warn
                else:
                    warnings.append("The file has no audio: nothing was transcribed.")
                if info["has_video"] and self._settings.ocr:
                    data, problem = await self._stage(source_id, work, "frames", job, timeout)
                    if problem or data is None:
                        warnings.append(f"On-screen text not read — {problem}")
                    else:
                        frames = self._import_frames(source_id, work, data["frames"])
                        onscreen = self._onscreen(data, frames, info["duration_ms"])
                        segments += onscreen
                        coverage["onscreen"] = {
                            "provider": data["provider"],
                            "sampled": data["sampled"],
                            "interval_ms": data["interval_ms"],
                            "keyframes": len(frames),
                            "text_segments": len(onscreen),
                        }
            elif kind == "image":
                data, problem = await self._stage(source_id, work, "image", job, 120)
                if problem or data is None:
                    await self._refuse(src, problem or "unreadable image")
                    return
                frames = self._import_frames(source_id, work, data["frames"])
                for frame in frames:
                    if frame.get("text"):
                        segments.append(
                            {
                                "modality": "image_text",
                                "text": frame["text"],
                                "quality": "ok",
                                "confidence": frame.get("min_score"),
                                "provider": data["provider"],
                                "frame_id": frame["id"],
                            }
                        )
                coverage["image"] = {"provider": data["provider"], "text_found": bool(segments)}
            elif kind == "pdf":
                data, problem = await self._stage(source_id, work, "pdf", job, 300)
                if problem or data is None:
                    await self._refuse(src, problem or "unreadable PDF")
                    return
                no_text = []
                for page in data["pages"]:
                    body = textmod.normalize(page["text"])
                    for a, b, t in textmod.segments(body):
                        segments.append(
                            {
                                "modality": "pdf_page",
                                "page": page["page"],
                                "char_start": a,
                                "char_end": b,
                                "text": t,
                                "quality": "ok",
                                "provider": "pypdf",
                            }
                        )
                    for ocr_text in page["ocr"]:
                        if ocr_text.strip():
                            segments.append(
                                {
                                    "modality": "pdf_page_image",
                                    "page": page["page"],
                                    "text": ocr_text,
                                    "quality": "ok",
                                    "provider": "rapidocr-ppocr",
                                }
                            )
                    if not body and not any(x.strip() for x in page["ocr"]):
                        no_text.append(page["page"])
                if no_text:
                    warnings.append(
                        f"No readable text on page(s) {', '.join(map(str, no_text[:20]))}."
                    )
                if data.get("truncated"):
                    warnings.append("Only the first 200 pages were read.")
                coverage["pdf"] = {"pages": data["page_count"], "pages_without_text": len(no_text)}
            await self._finish(src, segments, coverage, warnings, frames)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _iso(self) -> sandbox.Isolation:
        return self._isolation or sandbox.detect()

    async def _speech(
        self, source_id: str, work: Path, job: dict[str, Any], limit: float
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
        settings = self._settings
        language = job["language"]
        if settings.speech == "off":
            return (
                [],
                {"skipped": "speech recognition is switched off"},
                ["Speech recognition is off in Settings: nothing said in the video was read."],
            )
        whisper_dir = self._root / "models" / f"whisper-{settings.whisper_model}"
        use_whisper = settings.speech == "whisper" or language != "en"
        if use_whisper:
            if not (whisper_dir / "model.bin").exists():
                note = (
                    f"Spoken language '{language}' needs the multilingual speech model "
                    "(faster-whisper). Install it once in QuantLab (download from Hugging Face), "
                    "then re-extract — or paste the transcript."
                )
                return [], {"skipped": "multilingual speech model not installed"}, [note]
            job = {**job, "asr": "whisper", "whisper_dir": str(whisper_dir)}
        else:
            job = {**job, "asr": "moonshine"}
        data, problem = await self._stage(source_id, work, "speech", job, limit)
        if problem or data is None:
            return [], {"failed": problem}, [f"Speech not transcribed — {problem}"]
        out = [
            {
                "modality": "speech",
                "start_ms": s["start_ms"],
                "end_ms": s["end_ms"],
                "text": s["text"] or "[unintelligible]",
                "quality": s["quality"],
                "provider": data["provider"],
                "flags": [s["note"]] if s.get("note") else [],
            }
            for s in data["segments"]
        ]
        transcribed = sum((s["end_ms"] - s["start_ms"]) for s in data["segments"]) / 1000
        low = sum(1 for s in data["segments"] if s["quality"] != "ok")
        coverage = {
            "provider": data["provider"],
            "language": data.get("language"),
            "audio_seconds": data["audio_seconds"],
            "voiced_seconds": data["voiced_seconds"],
            "transcribed_seconds": round(transcribed, 2),
            "segments": len(out),
            "low_quality_segments": low,
            "timestamps": "per chunk of speech (≈3–20 s)",
            "seconds": data.get("seconds"),
        }
        warnings = []
        if low:
            warnings.append(
                f"{low} speech segment(s) may be misheard — check them against the video."
            )
        if data["voiced_seconds"] and transcribed < 0.6 * data["voiced_seconds"]:
            warnings.append("Part of the audio with speech energy produced no transcript.")
        return out, coverage, warnings

    def _import_frames(
        self, source_id: str, work: Path, raw: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        target = self._folder(source_id) / "frames"
        target.mkdir(parents=True, exist_ok=True)
        out = []
        for item in raw:
            src_path = (work / item["file"]).resolve()
            if work.resolve() not in src_path.parents or not src_path.exists():
                continue
            data = src_path.read_bytes()
            if _sha(data) != item["sha256"]:
                continue
            frame_id = f"fr-{item['t_ms'] if item.get('t_ms') is not None else 0}"
            dest = target / f"{frame_id}.jpg"
            dest.write_bytes(data)
            scores = [line.get("score") for line in item.get("lines", []) if line.get("score")]
            out.append(
                {
                    "id": frame_id,
                    "t_ms": item.get("t_ms"),
                    "path": str(dest),
                    "sha256": item["sha256"],
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "scene_score": item.get("scene_score"),
                    "text": item.get("text") or "",
                    "min_score": min(scores) if scores else None,
                }
            )
        return out

    def _onscreen(
        self, data: dict[str, Any], frames: list[dict[str, Any]], duration_ms: int
    ) -> list[dict[str, Any]]:
        """Runs of identical on-screen text become one segment spanning their samples."""
        by_file = {f"fr-{f['t_ms']}": f["id"] for f in frames}
        interval = int(data["interval_ms"])
        out: list[dict[str, Any]] = []
        run: dict[str, Any] | None = None
        for sample in data.get("samples", []):
            text = sample.get("text") or ""
            if run is not None and text == run["text"]:
                run["end_ms"] = min(duration_ms, sample["t_ms"] + interval)
                if sample.get("score") is not None:
                    run["scores"].append(sample["score"])
                continue
            if run is not None and run["text"]:
                out.append(run)
            run = {
                "text": text,
                "start_ms": sample["t_ms"],
                "end_ms": min(duration_ms, sample["t_ms"] + interval),
                "frame_id": by_file.get(f"fr-{sample['t_ms']}"),
                "scores": [sample["score"]] if sample.get("score") is not None else [],
            }
        if run is not None and run["text"]:
            out.append(run)
        return [
            {
                "modality": "onscreen",
                "start_ms": r["start_ms"],
                "end_ms": r["end_ms"],
                "text": r["text"],
                "confidence": min(r["scores"]) if r["scores"] else None,
                "quality": "ok" if (not r["scores"] or min(r["scores"]) >= 0.8) else "low",
                "provider": data["provider"],
                "frame_id": r["frame_id"],
            }
            for r in out
        ]

    async def _refuse(self, src: dict[str, Any], problem: str) -> None:
        await self.store.transition(src["id"], ACTIVE, "REJECTED", stage=None, error=problem)
        await self.store.audit(src["id"], "MEDIA_REFUSED", {"reason": problem})
        await self._emit(src["id"], f"Media refused: {problem}", Severity.WARNING)

    async def _finish(
        self,
        src: dict[str, Any],
        segments: list[dict[str, Any]],
        coverage: dict[str, Any],
        warnings: list[str],
        frames: list[dict[str, Any]] | None,
    ) -> None:
        id_ = src["id"]
        # Speech first, then on-screen text, each by time: the order a viewer meets them.
        order = {"speech": 0, "onscreen": 1}
        segments.sort(
            key=lambda s: (s.get("page") or 0, s.get("start_ms") or 0, order.get(s["modality"], 2))
        )
        if frames is not None and src["kind"] != "text_file":
            await self.store.replace_frames(id_, frames)
        if src["kind"] != "text_file":
            await self._flag_and_store(id_, segments)
        stored = await self.store.segments(id_)
        if not stored:
            warnings.append("Nothing readable was found in this source.")
        partial = bool(warnings) or any(s["quality"] != "ok" for s in stored)
        status = "PARTIAL" if partial or not stored else "EXTRACTED"
        meta = {**src["meta"], "coverage": coverage, "warnings": warnings}
        days = self._settings.keep_media_days
        expires = (utcnow() + timedelta(days=days)).isoformat() if src.get("media_path") else None
        await self.store.transition(
            id_, ("EXTRACTING",), status, stage=None, meta=meta, media_expires_at=expires
        )
        await self.store.audit(
            id_,
            "EXTRACTION_FINISHED",
            {
                "status": status,
                "segments": len(stored),
                "warnings": len(warnings),
                "media_kept_until": expires,
            },
        )
        if src.get("media_path") and days == 0:
            fresh = await self.store.get(id_)
            if fresh:
                await self._drop_media(fresh, "retention")
        await self._emit(id_, "Source ready" if status == "EXTRACTED" else "Source partly readable")
        await self._ready(id_)

    async def _ready(self, source_id: str) -> None:
        if self._on_ready is None or not self._settings.auto_research:
            return
        src = await self.store.get(source_id)
        if src and src["status"] in READY:
            try:
                await self._on_ready(source_id)
            except Exception:
                log.exception("research hook failed", extra={"source": source_id})

    # multilingual speech model ----------------------------------------------------------------

    def speech_model(self) -> dict[str, Any]:
        size = self._settings.whisper_model
        folder = self._root / "models" / f"whisper-{size}"
        return {
            "default": "moonshine-base-en (bundled, English, offline)",
            "multilingual": f"faster-whisper {size}",
            "installed": (folder / "model.bin").exists(),
            "job": self._model_state,
        }

    async def install_speech_model(self) -> dict[str, Any]:
        if self._model_job is not None and not self._model_job.done():
            return self.speech_model()
        self._model_state = {"status": "downloading", "started_at": utcnow().isoformat()}
        self._model_job = asyncio.create_task(self._download_model())
        return self.speech_model()

    async def _download_model(self) -> None:
        size = self._settings.whisper_model
        folder = self._root / "models" / f"whisper-{size}"
        try:
            from huggingface_hub import snapshot_download

            await asyncio.to_thread(
                snapshot_download, repo_id=WHISPER_REPO.format(size=size), local_dir=str(folder)
            )
            self._model_state = {"status": "installed", "finished_at": utcnow().isoformat()}
            await self.store.audit(
                None, "SPEECH_MODEL_INSTALLED", {"model": WHISPER_REPO.format(size=size)}
            )
        except Exception as exc:
            self._model_state = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            }
            await self.store.audit(
                None, "SPEECH_MODEL_FAILED", {"error": self._model_state["error"]}
            )
