"""Tests for visual attention filter — Issue #48."""

from __future__ import annotations

import pytest

from openbad.nervous_system.schemas import AttentionTrigger
from openbad.sensory.vision.attention_filter import (
    AttentionFilter,
    compute_mse,
    compute_pixel_delta,
    count_changed_pixels,
)
from openbad.sensory.vision.config import AttentionConfig

# ---------------------------------------------------------------------------
# compute_mse
# ---------------------------------------------------------------------------


class TestComputeMSE:
    def test_identical(self) -> None:
        frame = bytes([100, 150, 200])
        assert compute_mse(frame, frame) == 0.0

    def test_max_difference(self) -> None:
        a = bytes([0, 0, 0])
        b = bytes([255, 255, 255])
        assert abs(compute_mse(a, b) - 1.0) < 0.001

    def test_partial_difference(self) -> None:
        a = bytes([100, 100, 100])
        b = bytes([110, 100, 100])
        mse = compute_mse(a, b)
        assert 0.0 < mse < 0.01

    def test_empty_frames(self) -> None:
        assert compute_mse(b"", b"") == 0.0

    def test_mismatched_length(self) -> None:
        with pytest.raises(ValueError, match="Frame size mismatch"):
            compute_mse(bytes(3), bytes(5))


# ---------------------------------------------------------------------------
# compute_pixel_delta
# ---------------------------------------------------------------------------


class TestComputePixelDelta:
    def test_identical(self) -> None:
        frame = bytes([50] * 100)
        assert compute_pixel_delta(frame, frame) == 0.0

    def test_all_changed(self) -> None:
        a = bytes([0] * 100)
        b = bytes([255] * 100)
        assert compute_pixel_delta(a, b) == 1.0

    def test_threshold(self) -> None:
        a = bytes([100] * 100)
        b = bytes([105] * 100)  # diff=5, below default threshold=10
        assert compute_pixel_delta(a, b, threshold=10) == 0.0

    def test_just_above_threshold(self) -> None:
        a = bytes([100] * 100)
        b = bytes([111] * 100)  # diff=11, above threshold=10
        assert compute_pixel_delta(a, b, threshold=10) == 1.0

    def test_empty(self) -> None:
        assert compute_pixel_delta(b"", b"") == 0.0

    def test_mismatched_length(self) -> None:
        with pytest.raises(ValueError, match="Frame size mismatch"):
            compute_pixel_delta(bytes(3), bytes(5))


# ---------------------------------------------------------------------------
# count_changed_pixels
# ---------------------------------------------------------------------------


class TestCountChangedPixels:
    def test_no_change(self) -> None:
        assert count_changed_pixels(b"\x50\x50", b"\x50\x50") == 0

    def test_all_changed(self) -> None:
        assert count_changed_pixels(bytes([0, 0]), bytes([255, 255])) == 2

    def test_mismatched(self) -> None:
        with pytest.raises(ValueError, match="Frame size mismatch"):
            count_changed_pixels(bytes(1), bytes(2))


# ---------------------------------------------------------------------------
# AttentionFilter
# ---------------------------------------------------------------------------


class TestAttentionFilterEvaluate:
    def test_first_frame_no_trigger(self) -> None:
        f = AttentionFilter()
        triggered, delta, changed = f.evaluate(bytes(100))
        assert triggered is False
        assert delta == 0.0

    def test_second_identical_frame(self) -> None:
        f = AttentionFilter(config=AttentionConfig(ssim_threshold=0.05))
        f.evaluate(bytes(100))
        triggered, delta, changed = f.evaluate(bytes(100))
        assert triggered is False
        assert delta == 0.0

    def test_changed_frame_triggers(self) -> None:
        config = AttentionConfig(ssim_threshold=0.05, cooldown_ms=0)
        f = AttentionFilter(config=config)
        f.evaluate(bytes([0] * 100))
        triggered, delta, changed = f.evaluate(bytes([255] * 100))
        assert triggered is True
        assert delta == 1.0
        assert changed == 100
        assert f.trigger_count == 1

    def test_cooldown_prevents_rapid_triggers(self) -> None:
        config = AttentionConfig(ssim_threshold=0.01, cooldown_ms=9999999)
        f = AttentionFilter(config=config)

        f.evaluate(bytes([0] * 50))
        triggered1, _, _ = f.evaluate(bytes([255] * 50))
        assert triggered1 is True

        # Immediately another changed frame should be blocked by cooldown
        triggered2, delta2, _ = f.evaluate(bytes([0] * 50))
        assert triggered2 is False
        assert delta2 > 0  # still detected change, just blocked

    def test_reset(self) -> None:
        f = AttentionFilter()
        f.evaluate(bytes(10))
        f.reset()
        assert f._prev_frame is None
        assert f.trigger_count == 0


# ---------------------------------------------------------------------------
# AttentionFilter.process_frame (async)
# ---------------------------------------------------------------------------


class TestAttentionFilterPublish:
    async def test_triggers_publish(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        config = AttentionConfig(ssim_threshold=0.01, cooldown_ms=0)
        f = AttentionFilter(config=config, publish_fn=mock_publish)

        # First frame: establish baseline
        result1 = await f.process_frame("screen-0", bytes([0] * 50))
        assert result1 is None

        # Second frame: big change
        result2 = await f.process_frame("screen-0", bytes([200] * 50), "Full screen changed")
        assert result2 is not None
        assert isinstance(result2, AttentionTrigger)
        assert result2.source_id == "screen-0"
        assert result2.region_description == "Full screen changed"

        assert len(published) == 1
        topic, payload = published[0]
        assert topic == "agent/reflex/attention/trigger"

    async def test_no_trigger_no_publish(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        f = AttentionFilter(publish_fn=mock_publish)
        await f.process_frame("x", bytes(10))
        await f.process_frame("x", bytes(10))
        assert len(published) == 0

    async def test_no_publish_fn(self) -> None:
        config = AttentionConfig(ssim_threshold=0.01, cooldown_ms=0)
        f = AttentionFilter(config=config)
        await f.process_frame("x", bytes([0] * 10))
        result = await f.process_frame("x", bytes([255] * 10))
        assert isinstance(result, AttentionTrigger)
