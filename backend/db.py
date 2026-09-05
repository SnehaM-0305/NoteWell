"""
Video-to-Notes AI Platform — persistence layer
Small SQLite database so generated notes and chat history survive a page
refresh / server restart.

  sources       -> one row per video/text/pdf/docx/audio source processed
  notes         -> the structured markdown notes generated for that source
  chat_messages -> chat turns, scoped to 'general' or 'note:<note_id>'

Auth (users table) and chunk/embedding-based RAG retrieval aren't implemented
yet — chat currently uses the whole note's markdown as context, which is
plenty for typical lecture-length notes but will need real chunking +
retrieval if notes get much longer.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json
import os


DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "video_to_notes.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL DEFAULT 'video',   -- video / pdf / notes (future)
    title       TEXT NOT NULL,
    source_url  TEXT NOT NULL,
    video_id    TEXT,
    added_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id           INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    structured_markdown TEXT NOT NULL,
    num_chunks          INTEGER NOT NULL DEFAULT 1,
    transcript_origin   TEXT NOT NULL DEFAULT 'captions',  -- captions/whisper/pdf_extraction/docx_extraction/pasted_text
    learning_mode       TEXT NOT NULL DEFAULT 'medium',    -- beginner/medium/expert — see learning_modes.py
    generated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,      -- 'general' or 'note:<note_id>'
    role        TEXT NOT NULL,      -- 'user' or 'assistant'
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS question_sets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id        INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    requested_count INTEGER NOT NULL,
    learning_mode  TEXT NOT NULL DEFAULT 'medium',
    questions_json TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,      -- 'general' or 'note:<note_id>'
    title       TEXT NOT NULL DEFAULT 'New chat',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_sections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id       INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    order_index   INTEGER NOT NULL,
    start_seconds REAL NOT NULL,
    end_seconds   REAL NOT NULL,
    heading       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_url  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'running',   -- running/done/failed
    total         INTEGER NOT NULL,
    completed     INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_job_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE,
    video_url   TEXT NOT NULL,
    note_id     INTEGER,                            -- filled in on success
    status      TEXT NOT NULL DEFAULT 'pending',     -- pending/done/failed
    error       TEXT
);
CREATE TABLE IF NOT EXISTS generation_jobs (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    status                 TEXT NOT NULL DEFAULT 'running',
    stage                  TEXT NOT NULL DEFAULT 'transcribing',
    stage_started_at       TEXT,                                  
    total_duration_seconds REAL,
    processed_seconds      REAL NOT NULL DEFAULT 0,
    total_chunks           INTEGER,
    completed_chunks       INTEGER NOT NULL DEFAULT 0,
    note_id                INTEGER,
    error                  TEXT,
    started_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generation_job_sections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        INTEGER NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
    order_index   INTEGER NOT NULL,
    start_seconds REAL,
    end_seconds   REAL,
    markdown_text TEXT NOT NULL,   -- the FINAL formatted section, ready to render -- not a rough summary
    created_at    TEXT NOT NULL
);
"""


def init_db() -> None:
    """Create the database file + tables if they don't exist yet. Call once at startup."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Lightweight migrations for columns added after the DB was first created.
    CREATE TABLE IF NOT EXISTS (above) doesn't touch existing tables, so any
    new column needs to be added here, guarded by a check so it's a no-op on
    fresh databases (which already have the column from SCHEMA) and on
    databases that were already migrated."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(notes)")}
    if "learning_mode" not in existing_cols:
        conn.execute("ALTER TABLE notes ADD COLUMN learning_mode TEXT NOT NULL DEFAULT 'medium'")

    msg_cols = {row["name"] for row in conn.execute("PRAGMA table_info(chat_messages)")}
    if "session_id" not in msg_cols:
        conn.execute("ALTER TABLE chat_messages ADD COLUMN session_id INTEGER")

    src_cols = {row["name"] for row in conn.execute("PRAGMA table_info(sources)")}
    if "range_start_seconds" not in src_cols:
        conn.execute("ALTER TABLE sources ADD COLUMN range_start_seconds REAL")
    if "range_end_seconds" not in src_cols:
        conn.execute("ALTER TABLE sources ADD COLUMN range_end_seconds REAL")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_note(
    *,
    title: str,
    source_url: str,
    video_id: Optional[str],
    markdown: str,
    num_chunks: int,
    transcript_origin: str,
    source_type: str = "video",
    learning_mode: str = "medium",
    range_start_seconds: Optional[float] = None,
    range_end_seconds: Optional[float] = None,
) -> int:
    """Insert a source + its generated note. Returns the new note's id."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sources (type, title, source_url, video_id, added_at, "
            "range_start_seconds, range_end_seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (source_type, title, source_url, video_id, _now(),
             range_start_seconds, range_end_seconds),
        )
        source_id = cur.lastrowid

        cur = conn.execute(
            "INSERT INTO notes (source_id, structured_markdown, num_chunks, "
            "transcript_origin, learning_mode, generated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (source_id, markdown, num_chunks, transcript_origin, learning_mode, _now()),
        )
        return cur.lastrowid


def list_library() -> list[dict]:
    """Newest-first list of every note, joined with its source, for the Library screen."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT notes.id AS note_id, notes.generated_at, notes.num_chunks,
                   notes.transcript_origin, notes.learning_mode,
                   sources.title, sources.source_url, sources.video_id, sources.type
            FROM notes
            JOIN sources ON sources.id = notes.source_id
            ORDER BY notes.generated_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_note(note_id: int) -> Optional[dict]:
    """Fetch one note (with its source title/url) by id — used for viewing + export."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT notes.id AS note_id, notes.structured_markdown, notes.generated_at,
                   notes.num_chunks, notes.transcript_origin, notes.learning_mode,
                   sources.title, sources.source_url, sources.video_id, sources.type
            FROM notes
            JOIN sources ON sources.id = notes.source_id
            WHERE notes.id = ?
            """,
            (note_id,),
        ).fetchone()
        return dict(row) if row else None


def create_chat_session(scope: str, title: str = "New chat") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO chat_sessions (scope, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (scope, title, _now(), _now()),
        )
        return cur.lastrowid


def list_chat_sessions() -> list[dict]:
    """Newest-activity-first list of every conversation, for the dropdown."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, scope, title, updated_at FROM chat_sessions "
            "ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def rename_chat_session(session_id: int, title: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title[:80], _now(), session_id),
        )


def touch_chat_session(session_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?", (_now(), session_id)
        )


def save_chat_message(session_id: int, role: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, scope, role, content, created_at) "
            "VALUES (?, '', ?, ?, ?)",
            (session_id, role, content, _now()),
        )


def get_chat_history(session_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM chat_messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_chat_session(session_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, scope, title FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_chat_session(session_id: int) -> bool:
    with get_conn() as conn:
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        cur = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0

def clear_chat_history(scope: str) -> int:
    """Delete every chat message for one scope. Returns rows deleted.
    Notes and the RAG index are untouched."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM chat_messages WHERE scope = ?", (scope,))
        return cur.rowcount


def save_question_set(*, note_id: int, requested_count: int,
                      learning_mode: str, questions: list) -> int:
    import json
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO question_sets (note_id, requested_count, learning_mode, "
            "questions_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (note_id, requested_count, learning_mode, json.dumps(questions), _now()),
        )
        return cur.lastrowid


def get_question_set(set_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT question_sets.*, sources.title
            FROM question_sets
            JOIN notes ON notes.id = question_sets.note_id
            JOIN sources ON sources.id = notes.source_id
            WHERE question_sets.id = ?
            """,
            (set_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["questions"] = json.loads(data.pop("questions_json"))
        return data

def delete_note(note_id: int) -> bool:
    """Delete a note and everything hanging off it. Returns False if it
    didn't exist.

    Deleting the *source* row is what does the work: notes cascade from
    sources, and question_sets cascade from notes (PRAGMA foreign_keys is ON
    in get_conn). Chat history isn't a foreign key, so it goes manually."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT source_id FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if not row:
            return False

        conn.execute("DELETE FROM chat_messages WHERE scope = ?", (f"note:{note_id}",))
        conn.execute("DELETE FROM sources WHERE id = ?", (row["source_id"],))
        return True

def save_note_sections(note_id: int, sections: list[dict]) -> None:
    """
    sections: [{"start_seconds": float, "end_seconds": float, "heading": str}, ...]
    in the order they appear in the note. Called once, right after a note (or a
    regenerated section) is saved — replaces any existing rows for this note_id
    first, so re-running never leaves stale/duplicate section rows behind.
    """
    with get_conn() as conn:
        conn.execute("DELETE FROM note_sections WHERE note_id = ?", (note_id,))
        conn.executemany(
            "INSERT INTO note_sections (note_id, order_index, start_seconds, end_seconds, heading) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (note_id, i, s["start_seconds"], s["end_seconds"], s["heading"])
                for i, s in enumerate(sections)
            ],
        )


def get_note_sections(note_id: int) -> list[dict]:
    """Ordered section list for one note — the frontend's timeline/chip data."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT order_index, start_seconds, end_seconds, heading FROM note_sections "
            "WHERE note_id = ? ORDER BY order_index ASC",
            (note_id,),
        ).fetchall()
        return [dict(r) for r in rows]

def create_generation_job(total_duration_seconds: Optional[float] = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO generation_jobs (status, stage, stage_started_at, total_duration_seconds, "
            "processed_seconds, completed_chunks, started_at, updated_at) "
            "VALUES ('running', 'transcribing', ?, ?, 0, 0, ?, ?)",
            (_now(), total_duration_seconds, _now(), _now()),
        )
        return cur.lastrowid


def update_job_progress(
    job_id: int, *, stage: Optional[str] = None, processed_seconds: Optional[float] = None,
    total_duration_seconds: Optional[float] = None, total_chunks: Optional[int] = None,
    completed_chunks: Optional[int] = None,
) -> None:
    fields, values = [], []
    if stage is not None:
        fields.append("stage = ?"); values.append(stage)
        fields.append("stage_started_at = ?"); values.append(_now())   # NEW
    if processed_seconds is not None:
        fields.append("processed_seconds = ?"); values.append(processed_seconds)
    if total_duration_seconds is not None:
        fields.append("total_duration_seconds = ?"); values.append(total_duration_seconds)
    if total_chunks is not None:
        fields.append("total_chunks = ?"); values.append(total_chunks)
    if completed_chunks is not None:
        fields.append("completed_chunks = ?"); values.append(completed_chunks)
    if not fields:
        return
    fields.append("updated_at = ?"); values.append(_now())
    values.append(job_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE generation_jobs SET {', '.join(fields)} WHERE id = ?", values)


def add_job_section(job_id: int, order_index: int, start_seconds: float,
                     end_seconds: float, markdown_text: str) -> None:
    """Called the instant one chunk's FINAL section is written. The frontend's
    poll picks this up and renders it immediately -- this is real content,
    not a progress percentage."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO generation_job_sections (job_id, order_index, start_seconds, "
            "end_seconds, markdown_text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, order_index, start_seconds, end_seconds, markdown_text, _now()),
        )
        conn.execute("UPDATE generation_jobs SET updated_at = ? WHERE id = ?", (_now(), job_id))


def mark_job_done(job_id: int, note_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE generation_jobs SET status = 'done', stage = 'done', "
            "note_id = ?, updated_at = ? WHERE id = ?",
            (note_id, _now(), job_id),
        )


def mark_job_failed(job_id: int, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (error, _now(), job_id),
        )


def get_job(job_id: int) -> Optional[dict]:
    """Full job state + its streamed sections so far -- what the frontend poll reads."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        job = dict(row)
        sections = conn.execute(
            "SELECT order_index, start_seconds, end_seconds, markdown_text "
            "FROM generation_job_sections WHERE job_id = ? ORDER BY order_index ASC",
            (job_id,),
        ).fetchall()
        job["sections"] = [dict(s) for s in sections]
        return job 