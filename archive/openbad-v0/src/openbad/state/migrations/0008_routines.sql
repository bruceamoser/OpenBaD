-- Scheduled routines: markdown instructions executed by an agent on a schedule.

CREATE TABLE IF NOT EXISTS routines (
    routine_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    body_md      TEXT NOT NULL DEFAULT '',
    recurrence_rule TEXT,                        -- daily|HH:MM|TZ, weekly|DOW|HH:MM|TZ, interval|Ns
    next_run_at  REAL,                           -- Unix timestamp of next scheduled run
    enabled      INTEGER NOT NULL DEFAULT 1,     -- 0 = paused, 1 = active
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS routine_runs (
    run_id       TEXT PRIMARY KEY,
    routine_id   TEXT NOT NULL REFERENCES routines(routine_id) ON DELETE CASCADE,
    started_at   REAL NOT NULL,
    finished_at  REAL,
    status       TEXT NOT NULL DEFAULT 'running', -- running, done, failed
    output       TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    tokens_used  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_routines_next_run ON routines(next_run_at)
    WHERE enabled = 1 AND next_run_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_routine_runs_routine ON routine_runs(routine_id, started_at DESC);
