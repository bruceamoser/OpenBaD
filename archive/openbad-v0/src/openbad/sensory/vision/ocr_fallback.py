"""OCR fallback pipeline for applications without accessibility support.

When AT-SPI2 and CDP are unavailable, this module applies Optical
Character Recognition to captured screen frames to extract UI elements
(text regions, buttons, labels) with bounding box coordinates.

Supported backends:
  - **Tesseract OCR** (Apache 2.0) — via ``pytesseract``
  - **EasyOCR** (Apache 2.0) — deep-learning-based, higher accuracy on
    complex layouts

The extracted text regions are serialised as a JSON tree and published
as a ``ParsedScreen`` protobuf message on
``agent/sensory/vision/{source_id}/parsed``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openbad.nervous_system.schemas import Header, ParsedScreen
from openbad.nervous_system.schemas.sensory_pb2 import ParseMethod
from openbad.nervous_system.topics import SENSORY_VISION_PARSED, topic_for

logger = logging.getLogger(__name__)


class OCRBackend(Enum):
    """Available OCR engine backends."""

    TESSERACT = "tesseract"
    EASYOCR = "easyocr"


@dataclass
class TextRegion:
    """A single text region detected by OCR."""

    text: str
    confidence: float  # 0.0–1.0
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"text": self.text, "confidence": round(self.confidence, 3)}
        if self.width > 0 or self.height > 0:
            d["bounds"] = {
                "x": self.x, "y": self.y,
                "w": self.width, "h": self.height,
            }
        return d


@dataclass
class OCRResult:
    """Complete OCR result for a single frame."""

    regions: list[TextRegion] = field(default_factory=list)
    backend: OCRBackend = OCRBackend.TESSERACT
    extraction_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend.value,
            "extraction_ms": round(self.extraction_ms, 1),
            "regions": [r.to_dict() for r in self.regions],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @property
    def node_count(self) -> int:
        return len(self.regions)


# ---------------------------------------------------------------------------
# Backend adapters
# ---------------------------------------------------------------------------


def _run_tesseract(image_bytes: bytes) -> list[TextRegion]:
    """Run Tesseract OCR on raw image bytes. Requires pytesseract + Pillow."""
    try:
        import io

        import pytesseract  # type: ignore[import-untyped]
        from PIL import Image  # type: ignore[import-untyped]
    except ImportError:
        msg = (
            "pytesseract and Pillow are required for Tesseract OCR. "
            "Install with: pip install pytesseract Pillow"
        )
        raise RuntimeError(msg) from None

    img = Image.open(io.BytesIO(image_bytes))
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

    regions: list[TextRegion] = []
    n = len(data["text"])
    for i in range(n):
        text = data["text"][i].strip()
        if not text:
            continue
        conf = float(data["conf"][i])
        if conf < 0:
            continue
        regions.append(TextRegion(
            text=text,
            confidence=conf / 100.0,
            x=int(data["left"][i]),
            y=int(data["top"][i]),
            width=int(data["width"][i]),
            height=int(data["height"][i]),
        ))
    return regions


def _run_easyocr(image_bytes: bytes, languages: list[str] | None = None) -> list[TextRegion]:
    """Run EasyOCR on raw image bytes."""
    try:
        import easyocr  # type: ignore[import-untyped]
    except ImportError:
        msg = (
            "easyocr is required for EasyOCR backend. "
            "Install with: pip install easyocr"
        )
        raise RuntimeError(msg) from None

    langs = languages or ["en"]
    reader = easyocr.Reader(langs, gpu=False)
    results = reader.readtext(image_bytes)

    regions: list[TextRegion] = []
    for bbox, text, conf in results:
        # bbox is [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        x = int(min(xs))
        y = int(min(ys))
        w = int(max(xs) - x)
        h = int(max(ys) - y)
        regions.append(TextRegion(
            text=text, confidence=float(conf),
            x=x, y=y, width=w, height=h,
        ))
    return regions


# ---------------------------------------------------------------------------
# OCR pipeline
# ---------------------------------------------------------------------------


class OCRPipeline:
    """OCR fallback pipeline for screen frame text extraction.

    Parameters
    ----------
    backend : OCRBackend
        Which OCR engine to use.
    min_confidence : float
        Discard regions below this confidence threshold (0.0–1.0).
    languages : list[str] | None
        Language codes for EasyOCR (default: ``["en"]``).
    publish_fn : callable | None
        Optional async callback ``(topic, payload) -> None``.
    """

    def __init__(
        self,
        backend: OCRBackend = OCRBackend.TESSERACT,
        min_confidence: float = 0.3,
        languages: list[str] | None = None,
        publish_fn: Any | None = None,
    ) -> None:
        self._backend = backend
        self._min_confidence = min_confidence
        self._languages = languages
        self._publish = publish_fn

    @property
    def backend(self) -> OCRBackend:
        return self._backend

    def process_image(self, image_bytes: bytes) -> OCRResult:
        """Run OCR on raw image bytes (JPEG/PNG).

        Returns an ``OCRResult`` with detected text regions.
        """
        start = time.perf_counter()

        if self._backend == OCRBackend.TESSERACT:
            raw_regions = _run_tesseract(image_bytes)
        elif self._backend == OCRBackend.EASYOCR:
            raw_regions = _run_easyocr(image_bytes, self._languages)
        else:
            msg = f"Unknown OCR backend: {self._backend}"
            raise ValueError(msg)

        # Filter by confidence
        regions = [r for r in raw_regions if r.confidence >= self._min_confidence]

        elapsed_ms = (time.perf_counter() - start) * 1000

        return OCRResult(
            regions=regions,
            backend=self._backend,
            extraction_ms=elapsed_ms,
        )

    async def process_and_publish(
        self,
        source_id: str,
        image_bytes: bytes,
    ) -> ParsedScreen:
        """Process an image and optionally publish via the event bus."""
        result = self.process_image(image_bytes)

        proto = ParsedScreen(
            header=Header(
                timestamp_unix=time.time(),
                source_module="sensory.vision.ocr_fallback",
                schema_version=1,
            ),
            source_id=source_id,
            method=ParseMethod.OCR,
            tree_json=result.to_json(),
            node_count=result.node_count,
            extraction_ms=result.extraction_ms,
        )

        if self._publish is not None:
            topic = topic_for(SENSORY_VISION_PARSED, source_id=source_id)
            await self._publish(topic, proto.SerializeToString())

        return proto
