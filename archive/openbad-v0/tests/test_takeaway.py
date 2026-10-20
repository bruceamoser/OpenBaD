"""Tests for the takeaway generator."""

from __future__ import annotations

import pytest

from openbad.active_inference.engine import ExplorationEvent
from openbad.active_inference.takeaway import Takeaway, TakeawayGenerator


class TestTakeaway:
    def test_to_dict(self) -> None:
        t = Takeaway(
            source_id="sys", summary="test",
            surprise_level=0.5, metrics={"cpu": 0.5},
            explored=True, timestamp=1.0,
        )
        d = t.to_dict()
        assert d["source_id"] == "sys"
        assert d["explored"] is True
        assert d["surprise_level"] == 0.5


class TestTakeawayGenerator:
    def test_no_takeaway_below_threshold(self) -> None:
        gen = TakeawayGenerator(surprise_threshold=0.5)
        events = [
            ExplorationEvent(
                source_id="sys", surprise=0.2,
                explored=False, errors={"cpu": 0.2},
            ),
        ]
        takeaways = gen.process(events)
        assert takeaways == []

    def test_takeaway_above_threshold(self) -> None:
        gen = TakeawayGenerator(surprise_threshold=0.3)
        events = [
            ExplorationEvent(
                source_id="sys", surprise=0.8,
                explored=False, errors={"cpu": 0.8},
            ),
        ]
        takeaways = gen.process(events)
        assert len(takeaways) == 1
        assert takeaways[0].source_id == "sys"
        assert takeaways[0].surprise_level == pytest.approx(0.8)

    def test_takeaway_when_explored(self) -> None:
        gen = TakeawayGenerator(surprise_threshold=0.9)
        events = [
            ExplorationEvent(
                source_id="sys", surprise=0.1,
                explored=True, errors={"cpu": 0.1},
            ),
        ]
        takeaways = gen.process(events)
        assert len(takeaways) == 1
        assert takeaways[0].explored is True

    def test_summary_format(self) -> None:
        gen = TakeawayGenerator(surprise_threshold=0.0)
        events = [
            ExplorationEvent(
                source_id="net", surprise=0.6,
                explored=True, errors={"latency": 0.6, "drops": 0.2},
            ),
        ]
        takeaways = gen.process(events)
        summary = takeaways[0].summary
        assert "[net]" in summary
        assert "surprise=0.60" in summary
        assert "(explored)" in summary
        assert "latency=0.60" in summary

    def test_history_grows(self) -> None:
        gen = TakeawayGenerator(surprise_threshold=0.0)
        for i in range(5):
            gen.process([
                ExplorationEvent(
                    source_id=f"s{i}", surprise=0.5,
                    explored=False, errors={},
                ),
            ])
        assert len(gen.history) == 5

    def test_history_trimmed(self) -> None:
        gen = TakeawayGenerator(surprise_threshold=0.0)
        gen._max_history = 3
        for i in range(5):
            gen.process([
                ExplorationEvent(
                    source_id=f"s{i}", surprise=0.5,
                    explored=False, errors={},
                ),
            ])
        assert len(gen.history) == 3
        assert gen.history[0].source_id == "s2"

    def test_clear_history(self) -> None:
        gen = TakeawayGenerator(surprise_threshold=0.0)
        gen.process([
            ExplorationEvent(
                source_id="sys", surprise=0.5,
                explored=False, errors={},
            ),
        ])
        gen.clear_history()
        assert gen.history == []

    def test_multiple_events_mixed(self) -> None:
        gen = TakeawayGenerator(surprise_threshold=0.5)
        events = [
            ExplorationEvent(
                source_id="a", surprise=0.1,
                explored=False, errors={},
            ),
            ExplorationEvent(
                source_id="b", surprise=0.8,
                explored=True, errors={"val": 0.8},
            ),
            ExplorationEvent(
                source_id="c", surprise=0.3,
                explored=False, errors={},
            ),
        ]
        takeaways = gen.process(events)
        assert len(takeaways) == 1
        assert takeaways[0].source_id == "b"
