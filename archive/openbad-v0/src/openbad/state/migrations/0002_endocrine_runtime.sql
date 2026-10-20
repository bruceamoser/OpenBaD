CREATE TABLE IF NOT EXISTS endocrine_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    dopamine REAL NOT NULL DEFAULT 0.0,
    adrenaline REAL NOT NULL DEFAULT 0.0,
    cortisol REAL NOT NULL DEFAULT 0.0,
    endorphin REAL NOT NULL DEFAULT 0.0,
    mood_tags_json TEXT NOT NULL DEFAULT '[]',
    last_decay_at REAL NOT NULL DEFAULT (unixepoch('now')),
    updated_at REAL NOT NULL DEFAULT (unixepoch('now'))
);

CREATE TABLE IF NOT EXISTS endocrine_subsystems (
    system_name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    disabled_reason TEXT NOT NULL DEFAULT '',
    disabled_until REAL,
    updated_at REAL NOT NULL DEFAULT (unixepoch('now'))
);

CREATE TABLE IF NOT EXISTS endocrine_adjustments (
    adjustment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    reason TEXT NOT NULL,
    deltas_json TEXT NOT NULL DEFAULT '{}',
    levels_json TEXT NOT NULL DEFAULT '{}',
    doctor_revelation INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS endocrine_doctor_notes (
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    doctor_revelation INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_endocrine_adjustments_ts
    ON endocrine_adjustments (ts);
CREATE INDEX IF NOT EXISTS idx_endocrine_adjustments_source_ts
    ON endocrine_adjustments (source, ts);
CREATE INDEX IF NOT EXISTS idx_endocrine_doctor_notes_ts
    ON endocrine_doctor_notes (ts);
