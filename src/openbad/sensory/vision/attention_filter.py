"""Visual attention filter — frame change detection and gating.

Computes frame-to-frame differences using pixel-level mean squared error
(MSE) or structural similarity (SSIM) and only forwards frames that
exceed a configurable change threshold.  This prevents unnecessary token
consumption from static or slowly-changing screens.

When significant change is detected an ``AttentionTrigger`` protobuf is
published to ``agent/reflex/attention/trigger`` so the reflex arc can
escalate cognitive resources.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from openbad.nervous_system.schemas import AttentionTrigger, Header
from openbad.nervous_system.topics import SENSORY_ATTENTION_TRIGGER
from openbad.sensory.vision.config import AttentionConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pixel-level change metrics (pure Python — no NumPy required)
# ---------------------------------------------------------------------------


def compute_mse(frame_a: bytes, frame_b: bytes) -> float:
    """Compute Mean Squared Error between two equal-length byte buffers.

    Returns a normalised value 0.0 (identical) to 1.0 (max difference).
    """
    if len(frame_a) != len(frame_b):
        msg = (
            f"Frame size mismatch: {len(frame_a)} vs {len(frame_b)}"
        )
        raise ValueError(msg)
    if len(frame_a) == 0:
        return 0.0

    total = 0.0
    for a, b in zip(frame_a, frame_b, strict=True):
        diff = a - b
        total += diff * diff
    mse = total / len(frame_a)
    # Normalise to 0–1 (max pixel diff = 255^2 = 65025)
    return mse / 65025.0


def compute_pixel_delta(frame_a: bytes, frame_b: bytes, threshold: int = 10) -> float:
    """Compute the fraction of pixels that differ by more than *threshold*.

    Returns 0.0 (identical) to 1.0 (every pixel changed).
    """
    if len(frame_a) != len(frame_b):
        msg = (
            f"Frame size mismatch: {len(frame_a)} vs {len(frame_b)}"
        )
        raise ValueError(msg)
    if len(frame_a) == 0:
        return 0.0

    changed = sum(1 for a, b in zip(frame_a, frame_b, strict=True) if abs(a - b) > threshold)
    return changed / len(frame_a)


def count_changed_pixels(frame_a: bytes, frame_b: bytes, threshold: int = 10) -> int:
    """Count the number of bytes that differ by more than *threshold*."""
    if len(frame_a) != len(frame_b):
        msg = (
            f"Frame size mismatch: {len(frame_a)} vs {len(frame_b)}"
        )
        raise ValueError(msg)
    return sum(1 for a, b in zip(frame_a, frame_b, strict=True) if abs(a - b) > threshold)


# ---------------------------------------------------------------------------
# Attention filter
# ---------------------------------------------------------------------------


class AttentionFilter:
    """Frame-level change detection gate.

    Compares each incoming frame against the previous one and only fires
    an attention event when the change exceeds the configured threshold.

    Parameters
    ----------
    config : AttentionConfig | None
        Threshold and cooldown settings.  Uses defaults if omitted.
    publish_fn : callable | None
        Optional async callback ``(topic, payload) -> None``.
    """

    def __init__(
        self,
        config: AttentionConfig | None = None,
        publish_fn: Any | None = None,
    ) -> None:
        self._config = config or AttentionConfig()
        self._publish = publish_fn
        self._prev_frame: bytes | None = None
        self._last_trigger_time: float = 0.0
        self._trigger_count: int = 0

    @property
    def trigger_count(self) -> int:
        """Number of attention triggers fired since creation."""
        return self._trigger_count

    def reset(self) -> None:
        """Clear the previous frame and trigger state."""
        self._prev_frame = None
        self._last_trigger_time = 0.0
        self._trigger_count = 0

    def _is_in_cooldown(self) -> bool:
        """Check whether we are still within the cooldown window."""
        if self._last_trigger_time == 0.0:
            return False
        elapsed_ms = (time.time() - self._last_trigger_time) * 1000
        return elapsed_ms < self._config.cooldown_ms

    def evaluate(self, frame: bytes) -> tuple[bool, float, int]:
        """Evaluate whether *frame* should trigger attention.

        Returns
        -------
        (triggered, delta, changed_pixels)
            *triggered* is True if the frame exceeds the threshold and
            the cooldown has elapsed.
        """
        if self._prev_frame is None:
            self._prev_frame = frame
            return False, 0.0, 0

        delta = compute_pixel_delta(self._prev_frame, frame)
        changed = count_changed_pixels(self._prev_frame, frame)
        self._prev_frame = frame

        threshold = self._config.ssim_threshold
        if delta < threshold:
            return False, delta, changed

        if self._is_in_cooldown():
            return False, delta, changed

        self._last_trigger_time = time.time()
        self._trigger_count += 1
        return True, delta, changed

    async def process_frame(
        self,
        source_id: str,
        frame: bytes,
        region_description: str = "",
    ) -> AttentionTrigger | None:
        """Evaluate a frame and optionally publish an attention trigger.

        Returns the ``AttentionTrigger`` proto if fired, else ``None``.
        """
        triggered, delta, changed = self.evaluate(frame)

        if not triggered:
            return None

        proto = AttentionTrigger(
            header=Header(
                timestamp_unix=time.time(),
                source_module="sensory.vision.attention_filter",
                schema_version=1,
            ),
            source_id=source_id,
            ssim_delta=delta,
            region_description=region_description,
            changed_pixels=changed,
        )

        if self._publish is not None:
            await self._publish(
                SENSORY_ATTENTION_TRIGGER,
                proto.SerializeToString(),
            )

        return proto
