"""Tests for OCR fallback pipeline — Issue #47."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from openbad.nervous_system.schemas import ParsedScreen
from openbad.nervous_system.schemas.sensory_pb2 import ParseMethod
from openbad.sensory.vision.ocr_fallback import (
    OCRBackend,
    OCRPipeline,
    OCRResult,
    TextRegion,
)

# ---------------------------------------------------------------------------
# TextRegion tests
# ---------------------------------------------------------------------------


class TestTextRegion:
    def test_basic(self) -> None:
        r = TextRegion(text="Hello", confidence=0.95)
        assert r.text == "Hello"
        assert r.confidence == 0.95

    def test_to_dict_without_bounds(self) -> None:
        r = TextRegion(text="OK", confidence=0.8)
        d = r.to_dict()
        assert d == {"text": "OK", "confidence": 0.8}
        assert "bounds" not in d

    def test_to_dict_with_bounds(self) -> None:
        r = TextRegion(text="Button", confidence=0.9, x=10, y=20, width=100, height=30)
        d = r.to_dict()
        assert d["bounds"] == {"x": 10, "y": 20, "w": 100, "h": 30}

    def test_confidence_rounding(self) -> None:
        r = TextRegion(text="X", confidence=0.12345)
        d = r.to_dict()
        assert d["confidence"] == 0.123


# ---------------------------------------------------------------------------
# OCRResult tests
# ---------------------------------------------------------------------------


class TestOCRResult:
    def test_empty_result(self) -> None:
        result = OCRResult()
        assert result.regions == []
        assert result.node_count == 0

    def test_to_dict(self) -> None:
        result = OCRResult(
            regions=[TextRegion(text="Hello", confidence=0.9)],
            backend=OCRBackend.TESSERACT,
            extraction_ms=42.5,
        )
        d = result.to_dict()
        assert d["backend"] == "tesseract"
        assert d["extraction_ms"] == 42.5
        assert len(d["regions"]) == 1

    def test_to_json(self) -> None:
        result = OCRResult(
            regions=[TextRegion(text="A", confidence=1.0)],
            backend=OCRBackend.EASYOCR,
        )
        js = result.to_json()
        parsed = json.loads(js)
        assert parsed["backend"] == "easyocr"
        assert len(parsed["regions"]) == 1

    def test_node_count(self) -> None:
        regions = [
            TextRegion(text="A", confidence=0.9),
            TextRegion(text="B", confidence=0.8),
            TextRegion(text="C", confidence=0.7),
        ]
        result = OCRResult(regions=regions)
        assert result.node_count == 3


# ---------------------------------------------------------------------------
# OCRBackend enum tests
# ---------------------------------------------------------------------------


class TestOCRBackend:
    def test_values(self) -> None:
        assert OCRBackend.TESSERACT.value == "tesseract"
        assert OCRBackend.EASYOCR.value == "easyocr"


# ---------------------------------------------------------------------------
# OCRPipeline tests (with mocked backends)
# ---------------------------------------------------------------------------


class TestOCRPipelineConfig:
    def test_default_backend(self) -> None:
        pipeline = OCRPipeline()
        assert pipeline.backend == OCRBackend.TESSERACT

    def test_custom_backend(self) -> None:
        pipeline = OCRPipeline(backend=OCRBackend.EASYOCR)
        assert pipeline.backend == OCRBackend.EASYOCR


class TestOCRPipelineProcess:
    def test_process_with_mocked_tesseract(self) -> None:
        mock_regions = [
            TextRegion(text="Hello", confidence=0.95, x=10, y=20, width=80, height=20),
            TextRegion(text="World", confidence=0.88, x=10, y=50, width=80, height=20),
            TextRegion(text="noise", confidence=0.1, x=0, y=0, width=5, height=5),
        ]

        with patch(
            "openbad.sensory.vision.ocr_fallback._run_tesseract",
            return_value=mock_regions,
        ):
            pipeline = OCRPipeline(backend=OCRBackend.TESSERACT, min_confidence=0.3)
            result = pipeline.process_image(b"fake-jpeg-data")

        # Low-confidence region filtered out
        assert len(result.regions) == 2
        assert result.regions[0].text == "Hello"
        assert result.regions[1].text == "World"
        assert result.backend == OCRBackend.TESSERACT
        assert result.extraction_ms >= 0

    def test_process_with_mocked_easyocr(self) -> None:
        mock_regions = [
            TextRegion(text="Click", confidence=0.92, x=100, y=200, width=60, height=25),
        ]

        with patch(
            "openbad.sensory.vision.ocr_fallback._run_easyocr",
            return_value=mock_regions,
        ):
            pipeline = OCRPipeline(backend=OCRBackend.EASYOCR)
            result = pipeline.process_image(b"fake-png-data")

        assert len(result.regions) == 1
        assert result.regions[0].text == "Click"

    def test_confidence_filtering(self) -> None:
        mock_regions = [
            TextRegion(text="High", confidence=0.9),
            TextRegion(text="Med", confidence=0.5),
            TextRegion(text="Low", confidence=0.2),
        ]

        with patch(
            "openbad.sensory.vision.ocr_fallback._run_tesseract",
            return_value=mock_regions,
        ):
            pipeline = OCRPipeline(min_confidence=0.6)
            result = pipeline.process_image(b"data")

        assert len(result.regions) == 1
        assert result.regions[0].text == "High"

    def test_all_filtered_out(self) -> None:
        mock_regions = [
            TextRegion(text="Junk", confidence=0.1),
        ]

        with patch(
            "openbad.sensory.vision.ocr_fallback._run_tesseract",
            return_value=mock_regions,
        ):
            pipeline = OCRPipeline(min_confidence=0.5)
            result = pipeline.process_image(b"data")

        assert len(result.regions) == 0
        assert result.node_count == 0


class TestOCRPipelinePublish:
    async def test_process_and_publish(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        mock_regions = [
            TextRegion(text="Submit", confidence=0.97, x=200, y=300, width=80, height=30),
            TextRegion(text="Cancel", confidence=0.91, x=300, y=300, width=80, height=30),
        ]

        with patch(
            "openbad.sensory.vision.ocr_fallback._run_tesseract",
            return_value=mock_regions,
        ):
            pipeline = OCRPipeline(publish_fn=mock_publish)
            result = await pipeline.process_and_publish("app-1", b"image-data")

        assert isinstance(result, ParsedScreen)
        assert result.source_id == "app-1"
        assert result.method == ParseMethod.OCR
        assert result.node_count == 2

        # Verify JSON
        parsed = json.loads(result.tree_json)
        assert parsed["backend"] == "tesseract"
        assert len(parsed["regions"]) == 2

        # Verify published
        assert len(published) == 1
        topic, payload = published[0]
        assert topic == "agent/sensory/vision/app-1/parsed"

        restored = ParsedScreen()
        restored.ParseFromString(payload)
        assert restored.node_count == 2

    async def test_no_publish_fn(self) -> None:
        mock_regions = [TextRegion(text="X", confidence=0.9)]

        with patch(
            "openbad.sensory.vision.ocr_fallback._run_tesseract",
            return_value=mock_regions,
        ):
            pipeline = OCRPipeline()
            result = await pipeline.process_and_publish("test", b"data")

        assert isinstance(result, ParsedScreen)
        assert result.node_count == 1


# ---------------------------------------------------------------------------
# Tesseract backend import error
# ---------------------------------------------------------------------------


class TestBackendImportError:
    def test_tesseract_import_error(self) -> None:
        with patch.dict("sys.modules", {"pytesseract": None, "PIL": None}):
            pipeline = OCRPipeline(backend=OCRBackend.TESSERACT)
            with pytest.raises(RuntimeError, match="pytesseract"):
                pipeline.process_image(b"data")

    def test_easyocr_import_error(self) -> None:
        with patch.dict("sys.modules", {"easyocr": None}):
            pipeline = OCRPipeline(backend=OCRBackend.EASYOCR)
            with pytest.raises(RuntimeError, match="easyocr"):
                pipeline.process_image(b"data")
