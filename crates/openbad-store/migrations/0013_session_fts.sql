CREATE VIRTUAL TABLE session_events_fts USING fts5(
    search_text,
    content='session_events',
    content_rowid='rowid'
);

INSERT INTO session_events_fts(rowid, search_text)
SELECT rowid,
       COALESCE(
           search_text,
           json_extract(payload_json, '$.content'),
           json_extract(payload_json, '$.message'),
           payload_json
       )
FROM session_events;

CREATE TRIGGER session_events_ai AFTER INSERT ON session_events BEGIN
    INSERT INTO session_events_fts(rowid, search_text)
    VALUES (new.rowid, COALESCE(new.search_text, json_extract(new.payload_json, '$.content'), json_extract(new.payload_json, '$.message'), new.payload_json));
END;

CREATE TRIGGER session_events_ad AFTER DELETE ON session_events BEGIN
    INSERT INTO session_events_fts(session_events_fts, rowid, search_text)
    VALUES ('delete', old.rowid, COALESCE(old.search_text, json_extract(old.payload_json, '$.content'), json_extract(old.payload_json, '$.message'), old.payload_json));
END;

CREATE TRIGGER session_events_au AFTER UPDATE ON session_events BEGIN
    INSERT INTO session_events_fts(session_events_fts, rowid, search_text)
    VALUES ('delete', old.rowid, COALESCE(old.search_text, json_extract(old.payload_json, '$.content'), json_extract(old.payload_json, '$.message'), old.payload_json));
    INSERT INTO session_events_fts(rowid, search_text)
    VALUES (new.rowid, COALESCE(new.search_text, json_extract(new.payload_json, '$.content'), json_extract(new.payload_json, '$.message'), new.payload_json));
END;
