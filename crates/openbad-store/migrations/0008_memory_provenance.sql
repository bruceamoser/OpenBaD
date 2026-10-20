ALTER TABLE memories ADD COLUMN origin_turn INTEGER;
ALTER TABLE memories ADD COLUMN origin_task TEXT;
ALTER TABLE memories ADD COLUMN author TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'project';
ALTER TABLE memories ADD COLUMN supersedes TEXT;
ALTER TABLE memories ADD COLUMN expires_at INTEGER;

CREATE INDEX IF NOT EXISTS idx_memories_scope
    ON memories(workspace_id, scope, forgotten_at, expires_at);
