-- Which session a fork came from, alongside the existing fork sequence.
ALTER TABLE sessions ADD COLUMN forked_from_session TEXT;
