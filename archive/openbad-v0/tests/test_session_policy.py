from __future__ import annotations

from pathlib import Path

import yaml

from openbad.autonomy.session_policy import load_session_policy


def test_load_policy_includes_doctor_flag_default(tmp_path: Path) -> None:
    path = tmp_path / "session_policy.yaml"
    policy = load_session_policy(path)

    immune = policy["sessions"]["immune"]
    assert immune["allow_endocrine_doctor"] is True
    doctor = policy["sessions"]["doctor"]
    assert doctor["session_id"] == "doctor-autonomy"
    assert doctor["allow_endocrine_doctor"] is True


def test_load_policy_merges_existing_without_doctor_flag(tmp_path: Path) -> None:
    path = tmp_path / "session_policy.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "sessions": {
                    "immune": {
                        "session_id": "immune-monitor",
                        "label": "Immune",
                        "allow_task_autonomy": False,
                        "allow_research_autonomy": False,
                        "allow_destructive": False,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    policy = load_session_policy(path)
    assert policy["sessions"]["immune"]["allow_endocrine_doctor"] is True
