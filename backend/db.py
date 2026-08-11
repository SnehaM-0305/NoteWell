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


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
) -> int:
    """Insert a source + its generated note. Returns the new note's id."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sources (type, title, source_url, video_id, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_type, title, source_url, video_id, _now()),
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