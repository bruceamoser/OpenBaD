CREATE TABLE IF NOT EXISTS workspace_files (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    hash         TEXT NOT NULL,
    size         INTEGER NOT NULL,
    modified_at  INTEGER NOT NULL,
    language     TEXT,
    indexed_at   INTEGER NOT NULL,
    PRIMARY KEY (workspace_id, path)
);

CREATE INDEX IF NOT EXISTS idx_workspace_files_hash
    ON workspace_files(workspace_id, hash);

CREATE TABLE IF NOT EXISTS workspace_symbols (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    line         INTEGER NOT NULL,
    signature    TEXT NOT NULL,
    PRIMARY KEY (workspace_id, path, line, name)
);

CREATE INDEX IF NOT EXISTS idx_workspace_symbols_name
    ON workspace_symbols(workspace_id, name);

CREATE VIRTUAL TABLE IF NOT EXISTS workspace_fts USING fts5(
    workspace_id UNINDEXED,
    path UNINDEXED,
    content,
    tokenize = 'unicode61'
);
