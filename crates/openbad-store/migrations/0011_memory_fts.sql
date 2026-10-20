CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    id UNINDEXED,
    workspace_id UNINDEXED,
    content,
    tags,
    tokenize = 'porter unicode61'
);

INSERT INTO memory_fts (id, workspace_id, content, tags)
SELECT id, workspace_id, content, tags_json
FROM memories
WHERE forgotten_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM memory_fts f WHERE f.id = memories.id);

CREATE TRIGGER IF NOT EXISTS memory_fts_insert
AFTER INSERT ON memories
BEGIN
    INSERT INTO memory_fts (id, workspace_id, content, tags)
    VALUES (new.id, new.workspace_id, new.content, new.tags_json);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_update
AFTER UPDATE ON memories
BEGIN
    DELETE FROM memory_fts WHERE id = old.id;
    INSERT INTO memory_fts (id, workspace_id, content, tags)
    SELECT new.id, new.workspace_id, new.content, new.tags_json
    WHERE new.forgotten_at IS NULL;
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_delete
AFTER DELETE ON memories
BEGIN
    DELETE FROM memory_fts WHERE id = old.id;
END;
