-- ADR-009: one read position per pipeline source.
--
-- Kept out of ticket state on purpose: a cursor is how far a source has read, and
-- it advances on every poll whether or not anything was admitted. Storing it next
-- to the tickets would tie two different lifecycles together.
CREATE TABLE pipeline_cursors (
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_id     TEXT NOT NULL,
    cursor_json   TEXT NOT NULL,
    updated_at    INTEGER NOT NULL,
    PRIMARY KEY (workspace_id, source_id)
);
