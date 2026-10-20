CREATE TABLE IF NOT EXISTS file_checkpoints (
    id           TEXT PRIMARY KEY NOT NULL,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn         INTEGER,
    path         TEXT NOT NULL,
    existed      INTEGER NOT NULL,
    content      BLOB,
    content_hash BLOB,
    created_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_file_checkpoints_session
    ON file_checkpoints(session_id, created_at DESC);
