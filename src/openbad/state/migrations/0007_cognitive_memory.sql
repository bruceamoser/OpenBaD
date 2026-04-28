-- Cognitive Memory Engine: engrams, associations, activation log, FTS5
-- Replaces JSON-file memory backends with SQLite-backed cognitive storage.

CREATE TABLE IF NOT EXISTS engrams (
    engram_id       TEXT PRIMARY KEY,
    tier            TEXT NOT NULL CHECK(tier IN ('stm','episodic','semantic','procedural')),
    key             TEXT NOT NULL,
    concept         TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL DEFAULT '',
    confidence      REAL NOT NULL DEFAULT 0.5,
    access_count    INTEGER NOT NULL DEFAULT 0,
    last_access_at  REAL NOT NULL DEFAULT 0.0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    ttl_seconds     REAL,
    context         TEXT NOT NULL DEFAULT '',
    metadata        TEXT NOT NULL DEFAULT '{}',
    state           TEXT NOT NULL DEFAULT 'active'
                    CHECK(state IN ('active','archived','soft_deleted'))
);

CREATE INDEX IF NOT EXISTS idx_engrams_tier   ON engrams(tier, state);
CREATE INDEX IF NOT EXISTS idx_engrams_key    ON engrams(key);
CREATE INDEX IF NOT EXISTS idx_engrams_created ON engrams(created_at);

-- Hebbian association edges (bidirectional, weighted)
CREATE TABLE IF NOT EXISTS engram_associations (
    source_id           TEXT NOT NULL,
    target_id           TEXT NOT NULL,
    weight              REAL NOT NULL DEFAULT 0.1,
    co_activation_count INTEGER NOT NULL DEFAULT 1,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    PRIMARY KEY (source_id, target_id),
    FOREIGN KEY(source_id) REFERENCES engrams(engram_id) ON DELETE CASCADE,
    FOREIGN KEY(target_id) REFERENCES engrams(engram_id) ON DELETE CASCADE
);

-- Co-activation ring buffer for Hebbian learning
CREATE TABLE IF NOT EXISTS activation_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    engram_id       TEXT NOT NULL,
    activated_at    REAL NOT NULL,
    query_context   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(engram_id) REFERENCES engrams(engram_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_activation_log_time ON activation_log(activated_at);

-- FTS5 full-text index for BM25 scoring
CREATE VIRTUAL TABLE IF NOT EXISTS engrams_fts USING fts5(
    engram_id UNINDEXED,
    concept,
    content,
    content='engrams',
    content_rowid='rowid'
);

-- Triggers to keep FTS5 in sync with engrams table
CREATE TRIGGER IF NOT EXISTS engrams_fts_ai AFTER INSERT ON engrams BEGIN
    INSERT INTO engrams_fts(rowid, engram_id, concept, content)
    VALUES (new.rowid, new.engram_id, new.concept, new.content);
END;

CREATE TRIGGER IF NOT EXISTS engrams_fts_ad AFTER DELETE ON engrams BEGIN
    INSERT INTO engrams_fts(engrams_fts, rowid, engram_id, concept, content)
    VALUES ('delete', old.rowid, old.engram_id, old.concept, old.content);
END;

CREATE TRIGGER IF NOT EXISTS engrams_fts_au AFTER UPDATE ON engrams BEGIN
    INSERT INTO engrams_fts(engrams_fts, rowid, engram_id, concept, content)
    VALUES ('delete', old.rowid, old.engram_id, old.concept, old.content);
    INSERT INTO engrams_fts(rowid, engram_id, concept, content)
    VALUES (new.rowid, new.engram_id, new.concept, new.content);
END;
