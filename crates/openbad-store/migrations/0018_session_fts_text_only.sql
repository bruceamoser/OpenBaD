-- The FTS index must contain the text the content column actually has.
--
-- Until now the triggers indexed a COALESCE that fell back to the raw
-- payload_json. `session_events_fts` is external-content, so `snippet()`
-- re-reads `session_events.search_text` to build a preview; for a row indexed
-- from its JSON, that column is NULL and every search touching it failed with
-- "Invalid column type Null".
--
-- Rebuild the index from `search_text` alone. A term that exists only inside a
-- payload (a tool-call argument, say) no longer matches, which is the accepted
-- trade: the tool searches conversation text.

DROP TRIGGER IF EXISTS session_events_ai;
DROP TRIGGER IF EXISTS session_events_ad;
DROP TRIGGER IF EXISTS session_events_au;
DROP TABLE IF EXISTS session_events_fts;

CREATE VIRTUAL TABLE session_events_fts USING fts5(
    search_text,
    content='session_events',
    content_rowid='rowid'
);

INSERT INTO session_events_fts(session_events_fts) VALUES('rebuild');

CREATE TRIGGER session_events_ai AFTER INSERT ON session_events BEGIN
    INSERT INTO session_events_fts(rowid, search_text)
    VALUES (new.rowid, new.search_text);
END;

CREATE TRIGGER session_events_ad AFTER DELETE ON session_events BEGIN
    INSERT INTO session_events_fts(session_events_fts, rowid, search_text)
    VALUES ('delete', old.rowid, old.search_text);
END;

CREATE TRIGGER session_events_au AFTER UPDATE ON session_events BEGIN
    INSERT INTO session_events_fts(session_events_fts, rowid, search_text)
    VALUES ('delete', old.rowid, old.search_text);
    INSERT INTO session_events_fts(rowid, search_text)
    VALUES (new.rowid, new.search_text);
END;
