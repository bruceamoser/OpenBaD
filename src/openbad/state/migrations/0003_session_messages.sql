CREATE TABLE IF NOT EXISTS session_messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'assistant',
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_session_messages_session_id
    ON session_messages (session_id, created_at);
