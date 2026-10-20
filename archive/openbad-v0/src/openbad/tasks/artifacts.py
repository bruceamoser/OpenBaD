"""Task artifact storage and resume-linkage for Phase 9 context persistence.

:class:`ArtifactStore` writes structured artifact files under
``data/tasks/<task_id>/`` and records their paths in task notes so that a
resume path can reconstruct the working context from disk rather than
re-running completed nodes.

Artifact files are JSON by default; the caller supplies the content as a dict.
References are stored in the ``artifact_refs`` field of the corresponding
:class:`~openbad.tasks.notes.TaskNote`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Artifact store
# ---------------------------------------------------------------------------


class ArtifactStore:
    """Writes artifact files and links them to task notes.

    Parameters
    ----------
    conn:
        Open ``sqlite3.Connection`` to the state database.
    base_dir:
        Root directory under which per-task artifact directories are created.
        Defaults to ``data/tasks`` relative to the current working directory.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        base_dir: str | Path = "data/tasks",
    ) -> None:
        self._conn = conn
        self._base_dir = Path(base_dir)

    # ------------------------------------------------------------------

    def write_artifact(
        self,
        task_id: str,
        artifact_name: str,
        content: dict,
        *,
        summary_text: str | None = None,
        facts: list[str] | None = None,
    ) -> Path:
        """Write *content* to JSON and record the path in a task note.

        Parameters
        ----------
        task_id:
            Owning task.  The file is written to
            ``<base_dir>/<task_id>/<artifact_name>``.
        artifact_name:
            File name (e.g. ``"output.json"``).
        content:
            Serialisable dict written as JSON.
        summary_text:
            Prose summary stored in the linked note.
        facts:
            Optional fact list stored in the linked note's ``summary_json``.

        Returns
        -------
        Path
            Absolute path of the written file.
        """
        artifact_dir = self._base_dir / task_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / artifact_name
        artifact_path.write_text(json.dumps(content, indent=2), encoding="utf-8")

        # Record the artifact reference in a task note
        from openbad.tasks.notes import NoteStore

        ns = NoteStore(self._conn)
        ns.add_note(
            task_id,
            summary_text or f"Artifact: {artifact_name}",
            facts=facts,
            artifact_refs=[str(artifact_path)],
        )

        return artifact_path

    def load_artifacts(self, task_id: str) -> list[dict]:
        """Load all artifact files referenced in notes for *task_id*.

        Returns a list of dicts, one per artifact.  Files that no longer exist
        are silently skipped so that resume is robust to partial storage.

        Returns
        -------
        list[dict]
            Loaded artifact contents in note-insertion order.
        """
        from openbad.tasks.notes import NoteStore

        ns = NoteStore(self._conn)
        notes = ns.list_notes(task_id)

        results: list[dict] = []
        for note in notes:
            for ref in note.summary.get("artifact_refs", []):
                p = Path(ref)
                if p.exists():
                    results.append(json.loads(p.read_text(encoding="utf-8")))

        return results
