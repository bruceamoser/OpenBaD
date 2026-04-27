CREATE TABLE IF NOT EXISTS libraries (
    library_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT (unixepoch('now'))
);

CREATE TABLE IF NOT EXISTS shelves (
    shelf_id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT (unixepoch('now')),
    FOREIGN KEY(library_id) REFERENCES libraries(library_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_shelves_library ON shelves(library_id);

CREATE TABLE IF NOT EXISTS sections (
    section_id TEXT PRIMARY KEY,
    shelf_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch('now')),
    FOREIGN KEY(shelf_id) REFERENCES shelves(shelf_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sections_shelf ON sections(shelf_id);

CREATE TABLE IF NOT EXISTS books (
    book_id TEXT PRIMARY KEY,
    section_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT 'user' CHECK(author IN ('user', 'system')),
    created_at REAL NOT NULL DEFAULT (unixepoch('now')),
    updated_at REAL NOT NULL DEFAULT (unixepoch('now')),
    FOREIGN KEY(section_id) REFERENCES sections(section_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_books_section ON books(section_id);

CREATE TABLE IF NOT EXISTS book_edges (
    source_book_id TEXT NOT NULL,
    target_book_id TEXT NOT NULL,
    relation_type TEXT NOT NULL CHECK(relation_type IN ('supersedes', 'relies_on', 'contradicts', 'references')),
    PRIMARY KEY (source_book_id, target_book_id),
    FOREIGN KEY(source_book_id) REFERENCES books(book_id) ON DELETE CASCADE,
    FOREIGN KEY(target_book_id) REFERENCES books(book_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS book_chunks (
    chunk_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text_content TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch('now')),
    FOREIGN KEY(book_id) REFERENCES books(book_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_book_chunks_book ON book_chunks(book_id);

CREATE VIRTUAL TABLE IF NOT EXISTS book_chunk_vectors USING vec0(
    chunk_id TEXT PRIMARY KEY,
    embedding float[768]
);
