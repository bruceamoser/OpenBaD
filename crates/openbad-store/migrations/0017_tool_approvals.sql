-- Standing "allow this tool in this workspace" approvals. They outlive the
-- process so a workspace grant is asked once, not every launch.
CREATE TABLE IF NOT EXISTS tool_approvals (
    workspace  TEXT NOT NULL,
    tool       TEXT NOT NULL,
    granted_at INTEGER NOT NULL,
    PRIMARY KEY (workspace, tool)
);
