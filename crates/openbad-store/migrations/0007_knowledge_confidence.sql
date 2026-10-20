ALTER TABLE knowledge_documents ADD COLUMN confidence TEXT NOT NULL DEFAULT 'inferred';
ALTER TABLE knowledge_documents ADD COLUMN source_hash TEXT;
