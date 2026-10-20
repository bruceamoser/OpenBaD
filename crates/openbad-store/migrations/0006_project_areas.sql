CREATE TABLE IF NOT EXISTS project_repos (
    id           TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    path         TEXT NOT NULL,
    read_only    INTEGER NOT NULL DEFAULT 1,
    created_at   INTEGER NOT NULL,
    UNIQUE(workspace_id, path)
);

CREATE INDEX IF NOT EXISTS idx_project_repos_workspace
    ON project_repos(workspace_id, created_at);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id           TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL,
    source       TEXT,
    tags_json    TEXT NOT NULL DEFAULT '[]',
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_workspace
    ON knowledge_documents(workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id           TEXT PRIMARY KEY NOT NULL,
    document_id  TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    content      TEXT NOT NULL,
    UNIQUE(document_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document
    ON knowledge_chunks(document_id, seq);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    workspace_id UNINDEXED,
    document_id UNINDEXED,
    title UNINDEXED,
    seq UNINDEXED,
    content,
    tokenize = 'unicode61'
);
