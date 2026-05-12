# Database Schema

SQLite database at `/var/lib/openbad/state.db`.
Migrations live in `src/openbad/state/migrations/`.

## Migration History

| Migration | Tables |
|-----------|--------|
| `0001_initial.sql` | Core state tables |
| `0002_endocrine_runtime.sql` | `endocrine_state`, `endocrine_adjustments`, `endocrine_doctor_notes` |
| `0003_tasks.sql` | `tasks`, `task_events` |
| `0004_research.sql` | `research_queue`, `research_findings` |
| `0005_memory.sql` | `memory_entries`, `session_messages` |
| `0006_library.sql` | Library hierarchy + vector search |
| `0007_telemetry.sql` | `telemetry_log`, `skill_access_requests` |
| `0008_routines.sql` | `routines`, `routine_runs` |

## Library Schema (0006)

```sql
libraries
  library_id  TEXT PRIMARY KEY
  name        TEXT NOT NULL
  description TEXT DEFAULT ''
  created_at  REAL DEFAULT unixepoch('now')

shelves
  shelf_id    TEXT PRIMARY KEY
  library_id  TEXT FK → libraries  ON DELETE CASCADE
  name        TEXT NOT NULL
  description TEXT DEFAULT ''
  created_at  REAL

sections
  section_id  TEXT PRIMARY KEY
  shelf_id    TEXT FK → shelves    ON DELETE CASCADE
  name        TEXT NOT NULL
  created_at  REAL

books
  book_id     TEXT PRIMARY KEY
  section_id  TEXT FK → sections   ON DELETE CASCADE
  title       TEXT NOT NULL
  summary     TEXT DEFAULT ''
  content     TEXT DEFAULT ''
  author      TEXT CHECK('user','system')
  created_at  REAL
  updated_at  REAL

book_edges
  source_book_id  TEXT FK → books  ON DELETE CASCADE
  target_book_id  TEXT FK → books  ON DELETE CASCADE
  relation_type   TEXT CHECK('supersedes','relies_on','contradicts','references')
  PRIMARY KEY (source_book_id, target_book_id)

book_chunks
  chunk_id     TEXT PRIMARY KEY
  book_id      TEXT FK → books  ON DELETE CASCADE
  chunk_index  INTEGER NOT NULL
  text_content TEXT NOT NULL
  created_at   REAL

book_chunk_vectors  (vec0 virtual table)
  chunk_id   TEXT PRIMARY KEY
  embedding  float[768]
```

## Routines Schema (0008)

```sql
routines
  routine_id       TEXT PRIMARY KEY
  name             TEXT NOT NULL
  description      TEXT DEFAULT ''
  body_md          TEXT NOT NULL
  recurrence_rule  TEXT DEFAULT ''
  next_run_at      TEXT            -- ISO 8601
  enabled          INTEGER DEFAULT 1
  created_at       TEXT
  updated_at       TEXT

routine_runs
  run_id       TEXT PRIMARY KEY
  routine_id   TEXT FK → routines
  started_at   TEXT
  finished_at  TEXT
  status       TEXT CHECK('running','done','failed')
  output       TEXT DEFAULT ''
  error        TEXT DEFAULT ''
  tokens_used  INTEGER DEFAULT 0
```

## Key Indexes

- `idx_shelves_library` — `shelves(library_id)`
- `idx_sections_shelf` — `sections(shelf_id)`
- `idx_books_section` — `books(section_id)`
- `idx_book_chunks_book` — `book_chunks(book_id)`
- `idx_routines_next_run` — `routines(next_run_at)` WHERE enabled=1
- `idx_routine_runs_routine` — `routine_runs(routine_id, started_at)`
