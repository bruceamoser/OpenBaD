CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY NOT NULL,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL UNIQUE,
    trust_state TEXT NOT NULL DEFAULT 'untrusted',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY NOT NULL,
    workspace_id      TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
    parent_session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    title             TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS session_events (
    id           TEXT PRIMARY KEY NOT NULL,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    turn         INTEGER,
    step         INTEGER,
    kind         TEXT NOT NULL,
    surface      INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_events_session_seq
    ON session_events(session_id, seq);

CREATE INDEX IF NOT EXISTS idx_events_session_kind
    ON session_events(session_id, kind, seq);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
