-- Persistent system event log with automatic rotation.
-- Every error, warning, fallback, and significant state change
-- is recorded here so the UI can show what actually happened.

CREATE TABLE IF NOT EXISTS system_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL    NOT NULL,
    level      TEXT    NOT NULL DEFAULT 'INFO',   -- ERROR, WARNING, INFO
    source     TEXT    NOT NULL DEFAULT 'system',  -- subsystem: heartbeat, wui, chat, provider, endocrine, immune, ...
    category   TEXT    NOT NULL DEFAULT 'general', -- error, fallback, degradation, recovery, gate, doctor, storage, llm
    summary    TEXT    NOT NULL DEFAULT '',         -- short human-readable headline
    detail     TEXT    NOT NULL DEFAULT '',         -- full error message, traceback snippet, or context
    metadata_json TEXT                              -- optional JSON blob for structured data
);

CREATE INDEX IF NOT EXISTS idx_system_events_ts ON system_events (ts);
CREATE INDEX IF NOT EXISTS idx_system_events_level_ts ON system_events (level, ts);
CREATE INDEX IF NOT EXISTS idx_system_events_source_ts ON system_events (source, ts);
