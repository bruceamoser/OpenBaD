-- ADR-009: the durable half of the supervised pipeline.
--
-- Every table is keyed by workspace, because a project opts into the pipeline and
-- two projects may watch the same repository with different policies.
--
-- The invariants the runner depends on are enforced here rather than by the caller:
--   * accepting the same trigger twice cannot create two tickets,
--   * a thread has at most one open ticket,
--   * a ticket has at most one active assignment,
--   * a ticket has at most one open question,
-- so a crash between two writes cannot leave a state the runner would have to
-- guess about.

CREATE TABLE pipeline_mandates (
    id                 TEXT PRIMARY KEY,
    workspace_id       TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    policy_version     TEXT NOT NULL,
    scope              TEXT NOT NULL,
    strength           TEXT NOT NULL,
    matched_rules_json TEXT NOT NULL,
    requester          TEXT NOT NULL,
    source_id          TEXT NOT NULL,
    received_via       TEXT NOT NULL,
    created_at         INTEGER NOT NULL
);

CREATE INDEX pipeline_mandates_workspace
    ON pipeline_mandates(workspace_id, created_at DESC);

CREATE TABLE pipeline_tickets (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_id    TEXT NOT NULL,
    thread_key   TEXT NOT NULL,
    trigger_id   TEXT NOT NULL,
    mandate_id   TEXT NOT NULL REFERENCES pipeline_mandates(id) ON DELETE RESTRICT,
    state        TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL,
    ended_at     INTEGER
);

CREATE UNIQUE INDEX pipeline_tickets_trigger
    ON pipeline_tickets(workspace_id, trigger_id);

-- A thread runs one ticket at a time. Re-requesting the same thread after its
-- ticket ended is a new admission, so it gets a new ticket and a new mandate.
CREATE UNIQUE INDEX pipeline_tickets_open
    ON pipeline_tickets(workspace_id, source_id, thread_key)
    WHERE state = 'open';

CREATE INDEX pipeline_tickets_workspace
    ON pipeline_tickets(workspace_id, updated_at DESC);

CREATE TABLE pipeline_assignments (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    ticket_id    TEXT NOT NULL REFERENCES pipeline_tickets(id) ON DELETE CASCADE,
    session_id   TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    executor     TEXT NOT NULL,
    state        TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL,
    ended_at     INTEGER
);

CREATE INDEX pipeline_assignments_ticket
    ON pipeline_assignments(ticket_id, created_at DESC);

-- One executor per ticket. A ticket has several assignments over time, never two
-- at once.
CREATE UNIQUE INDEX pipeline_assignments_active
    ON pipeline_assignments(ticket_id)
    WHERE state IN ('pending', 'running');

CREATE TABLE pipeline_reports (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    ticket_id     TEXT NOT NULL REFERENCES pipeline_tickets(id) ON DELETE CASCADE,
    assignment_id TEXT NOT NULL UNIQUE REFERENCES pipeline_assignments(id) ON DELETE CASCADE,
    outcome       TEXT NOT NULL,
    last_words    TEXT NOT NULL DEFAULT '',
    pull_request  TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE INDEX pipeline_reports_ticket
    ON pipeline_reports(ticket_id, updated_at DESC);

-- A question waiting for a person's answer. One at a time: the executor asks, then
-- waits, so a second question would be competing for the same attention.
CREATE TABLE pipeline_attentions (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    ticket_id    TEXT NOT NULL REFERENCES pipeline_tickets(id) ON DELETE CASCADE,
    question     TEXT NOT NULL,
    answer       TEXT,
    asked_at     INTEGER NOT NULL,
    answered_at  INTEGER,
    answered_by  TEXT
);

CREATE UNIQUE INDEX pipeline_attentions_open
    ON pipeline_attentions(ticket_id)
    WHERE answered_at IS NULL;

CREATE INDEX pipeline_attentions_ticket
    ON pipeline_attentions(ticket_id, asked_at DESC);

-- Many-to-many between tickets and threads, held by the thread: an ended ticket
-- keeps its subscriptions so a later message resumes it under the same mandate
-- without a new admission.
CREATE TABLE pipeline_subscriptions (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    ticket_id    TEXT NOT NULL REFERENCES pipeline_tickets(id) ON DELETE CASCADE,
    source_id    TEXT NOT NULL,
    thread_key   TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (ticket_id, source_id, thread_key)
);

CREATE INDEX pipeline_subscriptions_thread
    ON pipeline_subscriptions(workspace_id, source_id, thread_key);
