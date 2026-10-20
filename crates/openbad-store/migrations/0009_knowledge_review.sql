ALTER TABLE knowledge_documents ADD COLUMN supersedes TEXT;
ALTER TABLE knowledge_documents ADD COLUMN review_status TEXT NOT NULL DEFAULT 'accepted';

CREATE INDEX IF NOT EXISTS idx_knowledge_review
    ON knowledge_documents(workspace_id, review_status, updated_at DESC);
