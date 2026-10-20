CREATE TABLE IF NOT EXISTS memories (
    id               TEXT PRIMARY KEY NOT NULL,
    workspace_id     TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    content          TEXT NOT NULL,
    tags_json        TEXT NOT NULL DEFAULT '[]',
    pinned           INTEGER NOT NULL DEFAULT 0,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    last_accessed_at INTEGER,
    access_count     INTEGER NOT NULL DEFAULT 0,
    forgotten_at     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_memories_workspace
    ON memories(workspace_id, forgotten_at, pinned, updated_at DESC);
