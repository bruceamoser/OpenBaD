CREATE TABLE IF NOT EXISTS providers (
    id           TEXT PRIMARY KEY NOT NULL,
    kind         TEXT NOT NULL,
    name         TEXT NOT NULL,
    base_url     TEXT,
    keychain_ref TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS model_routes (
    id                   TEXT PRIMARY KEY NOT NULL,
    name                 TEXT NOT NULL UNIQUE,
    task_class           TEXT NOT NULL,
    provider_id          TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    model                TEXT NOT NULL,
    context_window       INTEGER NOT NULL,
    max_output_tokens    INTEGER NOT NULL,
    pricing_json         TEXT NOT NULL DEFAULT '{}',
    peak_pricing_json    TEXT NOT NULL DEFAULT '{}',
    cache_mode           TEXT NOT NULL DEFAULT 'automatic',
    api_format           TEXT NOT NULL DEFAULT 'openai',
    thinking_default     TEXT NOT NULL DEFAULT 'enabled',
    capabilities_json    TEXT NOT NULL DEFAULT '{}',
    enabled              INTEGER NOT NULL DEFAULT 1
);

ALTER TABLE workspaces ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE workspaces ADD COLUMN last_opened_at INTEGER;

ALTER TABLE sessions ADD COLUMN forked_from_seq INTEGER;
ALTER TABLE sessions ADD COLUMN model_route_id TEXT REFERENCES model_routes(id) ON DELETE SET NULL;
ALTER TABLE sessions ADD COLUMN plan_mode INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN goal_json TEXT;
ALTER TABLE sessions ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE sessions ADD COLUMN last_turn INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN deleted_at INTEGER;

ALTER TABLE session_events ADD COLUMN search_text TEXT;
ALTER TABLE session_events ADD COLUMN content_hash BLOB;

CREATE TABLE IF NOT EXISTS session_messages (
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    turn         INTEGER,
    step         INTEGER,
    role         TEXT NOT NULL,
    content_json TEXT NOT NULL,
    source       TEXT,
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (session_id, seq)
);

CREATE TABLE IF NOT EXISTS session_turns (
    session_id        TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn              INTEGER NOT NULL,
    started_at        INTEGER NOT NULL,
    ended_at          INTEGER,
    reason            TEXT,
    steps             INTEGER NOT NULL DEFAULT 0,
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, turn)
);

CREATE TABLE IF NOT EXISTS approvals (
    id           TEXT PRIMARY KEY NOT NULL,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    tool_call_id TEXT,
    risk_level   TEXT NOT NULL,
    request_json TEXT NOT NULL,
    decision     TEXT NOT NULL,
    decided_by   TEXT,
    reason       TEXT,
    created_at   INTEGER NOT NULL,
    decided_at   INTEGER,
    expires_at   INTEGER
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            TEXT PRIMARY KEY NOT NULL,
    ts            INTEGER NOT NULL,
    actor         TEXT NOT NULL,
    action        TEXT NOT NULL,
    target        TEXT,
    decision      TEXT,
    policy        TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    prev_hash     BLOB NOT NULL,
    hash          BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS llm_calls (
    id                  TEXT PRIMARY KEY NOT NULL,
    session_id          TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    turn                INTEGER,
    step                INTEGER,
    series_id           TEXT,
    route_id            TEXT,
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    api_format          TEXT NOT NULL DEFAULT 'openai',
    thinking_enabled    INTEGER NOT NULL DEFAULT 0,
    reasoning_effort    TEXT,
    user_id             TEXT,
    prompt_hash         BLOB,
    tools_hash          BLOB,
    workspace_hash      BLOB,
    request_bytes       INTEGER,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL DEFAULT 0,
    peak_pricing        INTEGER NOT NULL DEFAULT 0,
    latency_ms          INTEGER,
    first_token_ms      INTEGER,
    finish_reason       TEXT,
    status              TEXT NOT NULL,
    error_code          TEXT,
    created_at          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id, created_at);

CREATE TABLE IF NOT EXISTS tool_invocations (
    id              TEXT PRIMARY KEY NOT NULL,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn            INTEGER,
    step            INTEGER,
    call_id         TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    args_json       TEXT NOT NULL,
    result_preview  TEXT,
    result_bytes    INTEGER,
    spilled_path    TEXT,
    success         INTEGER NOT NULL,
    error_code      TEXT,
    risk_level      TEXT,
    approval_id     TEXT REFERENCES approvals(id) ON DELETE SET NULL,
    duration_ms     INTEGER,
    created_at      INTEGER NOT NULL,
    UNIQUE(session_id, call_id)
);

CREATE TABLE IF NOT EXISTS evidence (
    id                  TEXT PRIMARY KEY NOT NULL,
    session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    tool_invocation_id  TEXT REFERENCES tool_invocations(id) ON DELETE SET NULL,
    source_type         TEXT NOT NULL,
    source_name         TEXT NOT NULL,
    locator_json        TEXT,
    content             TEXT NOT NULL,
    content_hash        BLOB,
    confidence          REAL,
    retrieval_method    TEXT,
    observed_at         INTEGER,
    accepted            INTEGER NOT NULL DEFAULT 1,
    rejection_reason    TEXT
);

CREATE INDEX IF NOT EXISTS idx_evidence_session ON evidence(session_id, source_type);

CREATE TABLE IF NOT EXISTS request_envelopes (
    id             TEXT PRIMARY KEY NOT NULL,
    session_id     TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn           INTEGER,
    step           INTEGER,
    series_id      TEXT NOT NULL,
    route_id       TEXT,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    system_hash    BLOB NOT NULL,
    tools_hash     BLOB NOT NULL,
    workspace_hash BLOB,
    volatile_hash  BLOB,
    envelope_json  TEXT NOT NULL,
    created_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_request_envelopes_session
    ON request_envelopes(session_id, created_at);
