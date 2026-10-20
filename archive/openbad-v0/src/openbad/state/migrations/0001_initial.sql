CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'user_requested',
    horizon TEXT NOT NULL DEFAULT 'short',
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    due_at REAL,
    parent_task_id TEXT,
    root_task_id TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT 'system',
    lease_owner TEXT,
    recurrence_rule TEXT,
    requires_context INTEGER NOT NULL DEFAULT 0,
    isolated_execution INTEGER NOT NULL DEFAULT 0,
    notes_path TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS task_nodes (
    node_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    node_type TEXT NOT NULL DEFAULT 'reason',
    status TEXT NOT NULL DEFAULT 'pending',
    capability_requirements TEXT NOT NULL DEFAULT '[]',
    model_requirements TEXT NOT NULL DEFAULT '[]',
    reward_program_id TEXT,
    expected_info_gain REAL NOT NULL DEFAULT 0.0,
    blockage_score REAL NOT NULL DEFAULT 0.0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_edges (
    task_id TEXT NOT NULL,
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    PRIMARY KEY (task_id, from_node_id, to_node_id),
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
    FOREIGN KEY(from_node_id) REFERENCES task_nodes(node_id) ON DELETE CASCADE,
    FOREIGN KEY(to_node_id) REFERENCES task_nodes(node_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    node_id TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    actor TEXT NOT NULL DEFAULT 'system',
    routing_provider TEXT,
    routing_model TEXT,
    started_at REAL NOT NULL,
    finished_at REAL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
    FOREIGN KEY(node_id) REFERENCES task_nodes(node_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    node_id TEXT,
    event_type TEXT NOT NULL,
    created_at REAL NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
    FOREIGN KEY(node_id) REFERENCES task_nodes(node_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS task_notes (
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    note_text TEXT NOT NULL,
    summary_json TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_leases (
    lease_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    leased_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS heartbeat_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_heartbeat_at REAL,
    last_triage_at REAL,
    last_context_required_dispatch_at REAL,
    last_research_review_at REAL,
    last_sleep_cycle_at REAL,
    last_maintenance_at REAL,
    silent_skip_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS research_nodes (
    research_id TEXT PRIMARY KEY,
    source_task_id TEXT NOT NULL,
    source_node_id TEXT,
    trigger_reason TEXT NOT NULL DEFAULT '',
    blockage_score REAL NOT NULL DEFAULT 0.0,
    expected_info_gain REAL NOT NULL DEFAULT 0.0,
    urgency_score REAL NOT NULL DEFAULT 0.0,
    priority_score REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'pending',
    findings_summary TEXT,
    artifact_path TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(source_task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
    FOREIGN KEY(source_node_id) REFERENCES task_nodes(node_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS research_findings (
    finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    summary TEXT NOT NULL,
    artifact_path TEXT,
    payload_json TEXT,
    FOREIGN KEY(research_id) REFERENCES research_nodes(research_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reward_programs (
    program_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_audit (
    audit_id TEXT PRIMARY KEY,
    task_id TEXT,
    run_id TEXT,
    tool_name TEXT NOT NULL,
    server_name TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL,
    input_summary TEXT,
    output_summary TEXT,
    error_summary TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE SET NULL,
    FOREIGN KEY(run_id) REFERENCES task_runs(run_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS scheduler_windows (
    window_id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_type TEXT NOT NULL,
    start_at REAL NOT NULL,
    end_at REAL,
    status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT,
    last_run_at REAL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_due_at
    ON tasks (status, due_at);
CREATE INDEX IF NOT EXISTS idx_task_nodes_task_status
    ON task_nodes (task_id, status);
CREATE INDEX IF NOT EXISTS idx_task_edges_task_from
    ON task_edges (task_id, from_node_id);
CREATE INDEX IF NOT EXISTS idx_task_edges_task_to
    ON task_edges (task_id, to_node_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_task_started_at
    ON task_runs (task_id, started_at);
CREATE INDEX IF NOT EXISTS idx_task_events_task_created_at
    ON task_events (task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_events_node_created_at
    ON task_events (node_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_leases_owner_expires_at
    ON task_leases (owner_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_task_leases_resource
    ON task_leases (resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_research_nodes_status_priority
    ON research_nodes (status, priority_score, created_at);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_task_started_at
    ON mcp_audit (task_id, started_at);
CREATE INDEX IF NOT EXISTS idx_scheduler_windows_type_start_at
    ON scheduler_windows (window_type, start_at);
