"""On-screen text — RapidOCR (PP-OCR models bundled in the wheel), inside the worker only."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from PIL import Image

MIN_SCORE = 0.6
MAX_SIDE = 1280


@dataclass(frozen=True)
class TextLine:
    text: str
    score: float
    top: int
    left: int


class Ocr:
    name = "rapidocr-ppocr"

    def __init__(self) -> None:
        from rapidocr import RapidOCR

        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        self._engine: Any = RapidOCR()

    def read(self, image: Image.Image) -> list[TextLine]:
        img = image.convert("RGB")
        scale = MAX_SIDE / max(img.size)
        if scale < 1:
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        import numpy as np

        result = self._engine(np.asarray(img))
        texts = getattr(result, "txts", None) or ()
        scores = getattr(result, "scores", None) or ()
        boxes = getattr(result, "boxes", None)
        lines: list[TextLine] = []
        for i, (text, score) in enumerate(zip(texts, scores, strict=False)):
            if float(score) < MIN_SCORE or not str(text).strip():
                continue
            top = left = 0
            if boxes is not None and len(boxes) > i:
                top = int(min(p[1] for p in boxes[i]))
                left = int(min(p[0] for p in boxes[i]))
            lines.append(TextLine(" ".join(str(text).split()), round(float(score), 3), top, left))
        lines.sort(key=lambda line: (line.top // 20, line.left))
        return lines


def joined(lines: list[TextLine]) -> str:
    return " / ".join(line.text for line in lines)
