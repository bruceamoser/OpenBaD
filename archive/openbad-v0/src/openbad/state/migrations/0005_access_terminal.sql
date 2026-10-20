CREATE TABLE IF NOT EXISTS path_access_requests (
    request_id TEXT PRIMARY KEY,
    requested_path TEXT NOT NULL,
    normalized_root TEXT NOT NULL,
    requester TEXT NOT NULL DEFAULT 'session',
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL DEFAULT (unixepoch('now')),
    decided_at REAL,
    decided_by TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_path_access_requests_status_created
    ON path_access_requests(status, created_at DESC);

CREATE TABLE IF NOT EXISTS path_access_grants (
    grant_id TEXT PRIMARY KEY,
    requested_path TEXT NOT NULL,
    normalized_root TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    approved_by TEXT NOT NULL DEFAULT 'user',
    created_at REAL NOT NULL DEFAULT (unixepoch('now')),
    expires_at REAL,
    source_request_id TEXT NOT NULL DEFAULT '',
    revoked_at REAL,
    revoked_by TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_path_access_grants_root_active
    ON path_access_grants(normalized_root, revoked_at);

CREATE TABLE IF NOT EXISTS terminal_session_audit (
    audit_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL DEFAULT (unixepoch('now'))
);

CREATE INDEX IF NOT EXISTS idx_terminal_session_audit_session_created
    ON terminal_session_audit(session_id, created_at DESC);