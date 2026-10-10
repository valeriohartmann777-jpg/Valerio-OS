"""The intake worker: ``python -m jarvis.quantlab.intake.worker <stage> <job.json>``.

Runs in its own process, started by the intake service through the ULTRON
sandbox (macOS Seatbelt / Linux network namespace: no network; scrubbed
environment; a timeout that kills the process group). On top it lowers its own
resource limits. It reads one source file and writes ``<stage>.json`` (and
keyframes under ``frames/``) into its working directory — nothing else.

Stages: ``probe``, ``speech``, ``frames``, ``image``, ``pdf``.
Exit codes: 0 done, 2 the media was refused (the reason is in the JSON).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

FRAME_WIDTH = 360


def _limit_resources(cpu_seconds: int) -> None:
    if sys.platform == "win32":
        return
    import resource

    caps = [
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_FSIZE, 512 * 1024 * 1024),
    ]
    if sys.platform.startswith("linux"):
        caps.append((resource.RLIMIT_AS, 6 * 1024 * 1024 * 1024))
    for which, value in caps:
        try:
            _soft, hard = resource.getrlimit(which)
            limit = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(which, (limit, hard))
        except (ValueError, OSError):
            pass


def _write(stage: str, payload: dict[str, Any]) -> None:
    tmp = Path(f"{stage}.json.part")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f"{stage}.json")


def _save_frame(image: Any, name: str) -> dict[str, Any]:
    frames = Path("frames")
    frames.mkdir(exist_ok=True)
    img = image.convert("RGB")
    if img.width > FRAME_WIDTH:
        img = img.resize((FRAME_WIDTH, max(1, int(img.height * FRAME_WIDTH / img.width))))
    path = frames / f"{name}.jpg"
    img.save(path, "JPEG", quality=72)
    data = path.read_bytes()
    return {
        "file": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": img.width,
        "height": img.height,
    }


def probe(job: dict[str, Any]) -> dict[str, Any]:
    from jarvis.quantlab.intake import media

    return {"probe": media.probe(job["source"], job["max_ms"]).as_dict()}


def speech(job: dict[str, Any]) -> dict[str, Any]:
    from jarvis.quantlab.intake import asr, media

    audio = media.decode_audio(job["source"], job["max_ms"])
    seconds = audio.size / media.SAMPLE_RATE
    if audio.size == 0:
        return {"provider": None, "segments": [], "audio_seconds": 0, "voiced_seconds": 0}
    voiced = sum(b - a for a, b in asr.voiced_regions(audio)) / media.SAMPLE_RATE
    if job["asr"] == "whisper":
        engine: Any = asr.WhisperASR(job["whisper_dir"])
    else:
        engine = asr.MoonshineASR()
    started = time.monotonic()
    segments = engine.transcribe(audio, job.get("language", "en"))
    return {
        "provider": engine.name,
        "language": job.get("language", "en"),
        "audio_seconds": round(seconds, 2),
        "voiced_seconds": round(voiced, 2),
        "seconds": round(time.monotonic() - started, 2),
        "segments": [s.__dict__ for s in segments],
    }


def frames(job: dict[str, Any]) -> dict[str, Any]:
    """Sample frames, read their text, keep a keyframe whenever the text or the scene changes.

    Every sample is read (OCR text changes are what matter for overlays); only
    frames whose text differs, whose picture changed a lot, or that are due
    periodically are stored as keyframes.
    """
    from difflib import SequenceMatcher

    from jarvis.quantlab.intake import media
    from jarvis.quantlab.intake.ocr import Ocr, joined

    ocr = Ocr() if job.get("ocr", True) else None
    duration = int(job.get("duration_ms") or 0)
    budget = int(job.get("max_samples", 240))
    interval = max(int(job.get("frame_interval_ms", 1000)), duration // max(1, budget))
    samples: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    last_sig = None
    last_text = ""
    last_kept_ms = -(10**9)
    for t_ms, image in media.sample_frames(job["source"], interval, budget):
        sig = media.signature(image)
        score = 1.0 if last_sig is None else media.change(sig, last_sig)
        last_sig = sig
        lines = ocr.read(image) if ocr is not None else []
        text = joined(lines)
        same = bool(text) == bool(last_text) and (
            text == last_text or SequenceMatcher(None, text, last_text).ratio() >= 0.85
        )
        if same:
            text = last_text  # OCR noise between frames isn't a new overlay
        frame_ref = None
        due = t_ms - last_kept_ms >= int(job.get("periodic_ms", 6000))
        if (not same or score >= 0.08 or due) and len(kept) < int(job.get("max_keyframes", 60)):
            record = _save_frame(image, f"f{t_ms:08d}")
            record.update(
                {
                    "t_ms": t_ms,
                    "scene_score": round(score, 4),
                    "text": text,
                    "lines": [line.__dict__ for line in lines],
                }
            )
            kept.append(record)
            frame_ref = record["file"]
            last_kept_ms = t_ms
        samples.append(
            {
                "t_ms": t_ms,
                "text": text,
                "frame": frame_ref,
                "score": min((x.score for x in lines), default=None),
            }
        )
        last_text = text
    return {
        "provider": "rapidocr-ppocr" if ocr else None,
        "interval_ms": interval,
        "sampled": len(samples),
        "samples": samples,
        "frames": kept,
    }


def _line(raw: dict[str, Any]) -> Any:
    from jarvis.quantlab.intake.ocr import TextLine

    return TextLine(**raw)


def image(job: dict[str, Any]) -> dict[str, Any]:
    from PIL import Image

    from jarvis.quantlab.intake.ocr import Ocr, joined

    Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard
    with Image.open(job["source"]) as img:
        img.load()
        lines = Ocr().read(img)
        record = _save_frame(img, "image")
    record.update({"t_ms": None, "scene_score": 1.0, "lines": [x.__dict__ for x in lines]})
    record["text"] = joined(lines)
    return {"provider": "rapidocr-ppocr", "frames": [record]}


def pdf(job: dict[str, Any]) -> dict[str, Any]:
    import io

    from PIL import Image
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    from jarvis.quantlab.intake.ocr import Ocr, joined

    Image.MAX_IMAGE_PIXELS = 40_000_000
    try:
        reader = PdfReader(job["source"], strict=False)
        if reader.is_encrypted:
            return {"error": {"code": "PDF_ENCRYPTED", "message": "The PDF is encrypted."}}
        pages = list(reader.pages)
    except (PdfReadError, ValueError, KeyError, OSError) as exc:
        return {
            "error": {
                "code": "PDF_UNREADABLE",
                "message": f"The PDF can't be read ({type(exc).__name__}).",
            }
        }
    out: list[dict[str, Any]] = []
    ocr: Ocr | None = None
    images_read = 0
    for number, page in enumerate(pages[:200], start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # a broken page shouldn't sink the document
            text = ""
        entry: dict[str, Any] = {"page": number, "text": text, "ocr": []}
        if not text.strip() and images_read < 20:
            try:
                page_images = list(page.images)[:3]
            except Exception:
                page_images = []
            for item in page_images:
                try:
                    with Image.open(io.BytesIO(item.data)) as img:
                        img.load()
                        ocr = ocr or Ocr()
                        entry["ocr"].append(joined(ocr.read(img)))
                        images_read += 1
                except Exception:
                    continue
        out.append(entry)
    return {"pages": out, "page_count": len(pages), "truncated": len(pages) > 200}


STAGES = {"probe": probe, "speech": speech, "frames": frames, "image": image, "pdf": pdf}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in STAGES:
        print("usage: worker <stage> <job.json>", file=sys.stderr)
        return 64
    stage, job_path = argv
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    _limit_resources(int(job.get("cpu_seconds", 900)))
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    from jarvis.quantlab.intake.media import MediaError

    started = time.monotonic()
    try:
        payload = STAGES[stage](job)
    except MediaError as exc:
        _write(stage, {"error": {"code": exc.code, "message": exc.message}})
        return 2
    payload["elapsed_seconds"] = round(time.monotonic() - started, 2)
    _write(stage, payload)
    return 2 if "error" in payload else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
