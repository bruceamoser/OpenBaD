-- Reference folders are project-owned sources. Backfill a read grant for every
-- existing reference so the assistant can inspect it without another prompt.
INSERT OR IGNORE INTO outside_access_grants
    (id, workspace_id, path, write_access, reason, created_at)
SELECT lower(hex(randomblob(16))), workspace_id, path, 0,
       'reference folder (automatic)', created_at
FROM project_repos;
