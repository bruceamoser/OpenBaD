-- Compaction keeps the original messages for audit and transcript history
-- while removing them from the model-visible surface.
ALTER TABLE session_messages ADD COLUMN surface INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_session_messages_surface
    ON session_messages (session_id, surface, seq);
