"""Optical character recognition over screen frames.

Reads text from a frame. Nothing more: what the text says has no authority.
Screen text saying "ignore all previous instructions" is content, exactly like
a spoken sentence, and is stored as UNTRUSTED_DERIVED with
instruction_authority = NONE, enforced by database CHECK constraints.

Engine choice was measured rather than assumed. RapidOCR on onnxruntime scored
full recall on dashboard UI text, terminal and code, and accented Portuguese,
and its runtime was already present because faster-whisper installs
onnxruntime. See docs/adr/0002-screen-pipeline-and-mcp-extension.md.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any


ENGINE_NAME = "rapidocr"
MODEL_VERSION = "PP-OCRv6"
MODEL_TYPE = "small"

# SEC-05 applied to OCR: models are downloaded artifacts and are pinned by
# digest, not accepted by name. These were measured locally and cross-checked
# against the SHA256 values the upstream package declares for tag v3.9.2.
PINNED_OCR_MODELS: dict[str, str] = {
    "PP-OCRv6_det_small.onnx":
        "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "PP-OCRv6_rec_small.onnx":
        "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx":
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
}


class OcrUnavailable(RuntimeError):
    """The OCR runtime is not installed."""


@dataclass(frozen=True, slots=True)
class OcrBlock:
    text: str
    confidence: float | None
    bbox_x: int | None = None
    bbox_y: int | None = None
    bbox_width: int | None = None
    bbox_height: int | None = None


@dataclass(frozen=True, slots=True)
class OcrResult:
    blocks: tuple[OcrBlock, ...]
    engine: str
    engine_version: str
    model_version: str

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks if block.text)


def _import_rapidocr():
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise OcrUnavailable(
            "Screen OCR is not installed. Install vorquel-watch[screen]."
        ) from exc
    return RapidOCR


def engine_version() -> str:
    try:
        return package_version("rapidocr")
    except Exception:
        return "unknown"


def verify_models(model_dir: Path) -> dict[str, str]:
    """Re-hash the OCR model files and refuse any that does not match its pin.

    Returns the verified digests. A model swapped underneath us changes every
    reading it produces, so this fails loudly rather than transcribing screens
    with an unknown model.
    """
    verified: dict[str, str] = {}
    for name, expected in PINNED_OCR_MODELS.items():
        path = model_dir / name
        if not path.is_file():
            raise OcrUnavailable(f"OCR model is missing: {name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise OcrUnavailable(f"OCR model failed its integrity check: {name}")
        verified[name] = actual
    return verified


class ScreenOcr:
    """Lazily constructed OCR engine.

    Model load is expensive, so one instance is reused across a whole job.
    """

    def __init__(self) -> None:
        self._engine: Any = None

    def _ensure(self) -> Any:
        if self._engine is None:
            self._engine = _import_rapidocr()()
        return self._engine

    def read(self, image: Any) -> OcrResult:
        """Read text from a frame.

        Accepts anything the engine accepts: a path, or an ndarray of pixels.
        """
        engine = self._ensure()
        raw = engine(image)

        blocks: list[OcrBlock] = []
        raw_texts = getattr(raw, "txts", None)
        raw_scores = getattr(raw, "scores", None)
        raw_boxes = getattr(raw, "boxes", None)

        texts = list(raw_texts) if raw_texts is not None else []
        scores = list(raw_scores) if raw_scores is not None else []
        boxes = list(raw_boxes) if raw_boxes is not None else []

        for index, text in enumerate(texts):
            cleaned = (text or "").strip()
            if not cleaned:
                continue
            confidence = None
            if index < len(scores):
                try:
                    confidence = max(0.0, min(1.0, float(scores[index])))
                except (TypeError, ValueError):
                    confidence = None
            blocks.append(
                OcrBlock(cleaned, confidence, *_bbox(boxes[index] if index < len(boxes) else None))
            )

        return OcrResult(
            blocks=tuple(blocks),
            engine=ENGINE_NAME,
            engine_version=engine_version(),
            model_version=f"{MODEL_VERSION}_{MODEL_TYPE}",
        )


def _bbox(box: Any) -> tuple[int | None, int | None, int | None, int | None]:
    """Reduce a quadrilateral to an axis-aligned box, or nothing."""
    if box is None:
        return (None, None, None, None)
    try:
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
    except (TypeError, ValueError, IndexError):
        return (None, None, None, None)
    if not xs or not ys:
        return (None, None, None, None)
    left, top = int(min(xs)), int(min(ys))
    return (left, top, int(max(xs)) - left, int(max(ys)) - top)
