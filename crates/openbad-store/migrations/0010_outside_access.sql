CREATE TABLE IF NOT EXISTS outside_access_grants (
    id           TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    write_access INTEGER NOT NULL DEFAULT 0,
    reason       TEXT NOT NULL DEFAULT '',
    created_at   INTEGER NOT NULL,
    UNIQUE (workspace_id, path)
);

CREATE INDEX IF NOT EXISTS idx_outside_access_workspace
    ON outside_access_grants (workspace_id, created_at);
