"""
Video-to-Notes AI Platform — backend API

Pipeline: any source (YouTube link, pasted text, PDF, DOCX, audio upload) is
reduced to plain text, chunked, summarized per-chunk, then combined into
structured Markdown study notes. Notes are persisted in SQLite and indexed
into a local Chroma vector store.

Features on top of that:
  * Chat — general Q&A across the whole note library, or scoped to one note.
    Both are RAG-backed. Conversations are stored as *sessions*, so past
    threads can be reopened instead of being overwritten.
  * Practice questions — generated from a saved note, in batches, with
    .docx/.pdf export that follows the answers-shown/hidden state.
  * Library — list, reopen, and delete anything previously generated.

Still not implemented: OCR for scanned/image-only PDFs, and MCP tools.
"""

import os
import re
import shutil
import tempfile
import textwrap
import threading
import xml.etree.ElementTree as ET
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from groq import Groq
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

import db
import export
import questions as questions_mod
import rag
from extraction import extract_text_from_pdf, extract_text_from_docx
from learning_modes import (
    DEFAULT_LEARNING_MODE,
    apply_learning_mode,
    normalize_learning_mode,
    notes_structure,
    question_style,
)
from transcription import transcribe_video_with_whisper, transcribe_audio


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
app = FastAPI(title="Video-to-Notes AI")

load_dotenv()  # reads the .env file sitting next to this script (see README)
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:8000").split(",")
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is not set. Create a .env file (copy .env.example) and "
        "paste your free Groq API key into it before starting the server."
    )

client = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL = "llama-3.3-70b-versatile"  # free tier, fast, good quality

app = FastAPI(title="Video-to-Notes AI")

# Allow the frontend to call the API even if it's opened from a different origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()  # creates video_to_notes.db + tables next to this file, if not already there


@app.on_event("startup")
def _backfill_rag_index() -> None:
    """Index any notes that existed before RAG was added (or were generated
    while the embedding deps weren't installed yet). Cheap no-op once
    everything is already indexed, since index_note()/upsert() is idempotent.
    Runs in the background so it never blocks the server from accepting
    requests."""

    def _run():
        try:
            result = rag.reindex_all()
            print(f"[rag] startup backfill: {result}")
        except Exception as exc:  # embedding deps missing, first-run model download, etc.
            print(f"[rag] startup backfill skipped: {exc}")

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class NotesRequest(BaseModel):
    video_url: str
    learning_mode: Optional[str] = DEFAULT_LEARNING_MODE


class TextNotesRequest(BaseModel):
    text: str
    title: Optional[str] = None
    learning_mode: Optional[str] = DEFAULT_LEARNING_MODE


class ChatRequest(BaseModel):
    session_id: Optional[int] = None   # None = start a new conversation
    scope: str = "general"             # only read when creating a session
    message: str
    learning_mode: Optional[str] = DEFAULT_LEARNING_MODE


class QuestionsRequest(BaseModel):
    note_id: int
    count: int = 10
    learning_mode: Optional[str] = DEFAULT_LEARNING_MODE


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def extract_video_id(url: str) -> str:
    """Pull the 11-character YouTube video ID out of any common URL format."""
    patterns = [
        r"(?:v=|/)([0-9A-Za-z_-]{11})(?:[?&]|$|/)",
        r"youtu\.be/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError("Could not find a valid YouTube video ID in that URL.")


def fetch_video_title(video_url: str, fallback: str) -> str:
    """Best-effort video title lookup (for the Library screen) via yt-dlp metadata
    only — no download involved. Falls back to the raw video id if this fails."""
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(video_url, download=False)
            return info.get("title") or fallback
    except Exception:
        return fallback


def fetch_transcript(video_id: str, video_url: str) -> tuple[str, str]:
    """Try YouTube captions first (free + instant), fall back to Whisper.

    Returns (transcript_text, origin) where origin is "captions" or "whisper".
    """
    try:
        print("Trying YouTube captions...")
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
        transcript_text = " ".join(
            item.text if hasattr(item, "text") else item["text"]
            for item in transcript_list
        )
        if transcript_text.strip():
            print("Transcript fetched from YouTube captions.")
            return transcript_text, "captions"

    except (TranscriptsDisabled, NoTranscriptFound, ET.ParseError) as e:
        print(f"YouTube captions unavailable: {e}")
    except Exception as e:
        # Catch unexpected YouTube API issues
        print(f"Unexpected transcript error: {e}")

    print("Falling back to Whisper transcription...")
    result = transcribe_video_with_whisper(video_url)

    if not result.text.strip():
        raise HTTPException(
            status_code=422,
            detail="Whisper transcription produced no speech text for this video.",
        )

    print("Transcript generated using Whisper.")
    return result.text, "whisper"


def chunk_text(text: str, max_words: int = 900) -> List[str]:
    """Split the cleaned transcript into LLM-sized chunks (map-reduce)."""
    words = text.split()
    return [
        " ".join(words[i:i + max_words])
        for i in range(0, len(words), max_words)
    ] or [text]


def call_groq(prompt: str, system: str) -> str:
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content


def summarize_chunk(chunk: str, index: int, total: int, learning_mode: str) -> str:
    """Map step: summarize one transcript chunk.

    Deliberately NOT mode-aware. Simplifying here loses detail the reduce
    step can never recover -- Expert notes came out vaguer than Medium
    because specifics were dropped at this stage. Keep everything; let
    build_notes() decide what the reader sees.
    """
    prompt = (
        f"This is part {index + 1} of {total} of a transcript. Summarize the key "
        f"points concisely. Preserve every proper noun, tool name, version "
        f"number, and figure exactly as stated — later stages depend on "
        f"them:\n\n{chunk}"
    )
    system = (
        "You are an expert note-taker distilling transcripts into concise "
        "bullet-point summaries. You never drop a specific detail in favor of "
        "a general paraphrase."
    )
    return call_groq(prompt, system=system)

def build_notes(chunk_summaries: List[str], learning_mode: str) -> str:
    """Reduce step: combine chunk summaries into final structured Markdown notes.

    The output structure comes from learning_modes, not from here — an
    identical template across all three modes was what made them
    indistinguishable.
    """
    combined = "\n\n".join(chunk_summaries)
    prompt = textwrap.dedent(f"""
        Using the following chunk summaries from a source, produce study notes.

        {notes_structure(learning_mode)}

        Preserve every specific detail the summaries contain — names,
        versions, numbers, tools. Do not restate filler.

        Chunk summaries:
        {combined}
    """).strip()
    system = apply_learning_mode(
        "You produce clean, well-structured Markdown study notes.",
        learning_mode,
    )
    return call_groq(prompt, system=system)


# ---------------------------------------------------------------------------
# Shared pipeline: any source type ends up here once reduced to plain text
# ---------------------------------------------------------------------------

def generate_and_save(
    *,
    text: str,
    title: str,
    source_url: str,
    video_id: Optional[str],
    source_type: str,
    origin: str,
    learning_mode: Optional[str] = None,
) -> dict:
    if not text.strip():
        raise HTTPException(status_code=422, detail="No text content found to summarize.")

    learning_mode = normalize_learning_mode(learning_mode)

    chunks = chunk_text(text)
    chunk_summaries = [
        summarize_chunk(c, i, len(chunks), learning_mode) for i, c in enumerate(chunks)
    ]
    notes_markdown = build_notes(chunk_summaries, learning_mode)

    note_id = db.save_note(
        title=title,
        source_url=source_url,
        video_id=video_id,
        markdown=notes_markdown,
        num_chunks=len(chunks),
        transcript_origin=origin,
        source_type=source_type,
        learning_mode=learning_mode,
    )

    # Embed + store this note's chunks so chat can retrieve them instead of
    # pasting the whole note into every prompt. Indexing failure shouldn't fail
    # note generation itself -- chat falls back to whole-note context.
    try:
        rag.index_note(note_id, title, notes_markdown)
    except Exception as exc:
        print(f"[rag] failed to index note {note_id}: {exc}")

    return {
        "note_id": note_id,
        "video_id": video_id,
        "title": title,
        "num_chunks": len(chunks),
        "transcript_origin": origin,
        "source_type": source_type,
        "notes_markdown": notes_markdown,
        "learning_mode": learning_mode,
    }


# ---------------------------------------------------------------------------
# Notes generation routes
# ---------------------------------------------------------------------------

@app.post("/api/generate-notes")
def generate_notes(req: NotesRequest):
    """Source: pasted YouTube link. Tries captions first, falls back to Whisper."""
    try:
        video_id = extract_video_id(req.video_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    transcript, origin = fetch_transcript(video_id, req.video_url)
    title = fetch_video_title(req.video_url, fallback=video_id)

    return generate_and_save(
        text=transcript,
        title=title,
        source_url=req.video_url,
        video_id=video_id,
        source_type="video",
        origin=origin,
        learning_mode=req.learning_mode,
    )


@app.post("/api/generate-notes/text")
def generate_notes_from_text(req: TextNotesRequest):
    """Source: pasted plain text — no extraction needed, straight into the pipeline."""
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Paste some text first.")

    title = (req.title or "").strip()
    if not title:
        title = text[:60].strip() + ("…" if len(text) > 60 else "")

    return generate_and_save(
        text=text,
        title=title,
        source_url="",
        video_id=None,
        source_type="text",
        origin="pasted_text",
        learning_mode=req.learning_mode,
    )


@app.post("/api/generate-notes/pdf")
async def generate_notes_from_pdf(
    file: UploadFile = File(...),
    learning_mode: str = Form(DEFAULT_LEARNING_MODE),
):
    """Source: uploaded PDF — text extracted directly, no OCR (scanned PDFs won't work)."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a .pdf file.")

    file_bytes = await file.read()
    text = extract_text_from_pdf(file_bytes)
    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail="Couldn't extract any text from that PDF — it may be scanned/image-only.",
        )

    title = os.path.splitext(file.filename)[0]
    return generate_and_save(
        text=text,
        title=title,
        source_url=file.filename,
        video_id=None,
        source_type="pdf",
        origin="pdf_extraction",
        learning_mode=learning_mode,
    )


@app.post("/api/generate-notes/docx")
async def generate_notes_from_docx(
    file: UploadFile = File(...),
    learning_mode: str = Form(DEFAULT_LEARNING_MODE),
):
    """Source: uploaded Word document — plain-paragraph text extraction."""
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Please upload a .docx file.")

    file_bytes = await file.read()
    text = extract_text_from_docx(file_bytes)
    if not text.strip():
        raise HTTPException(status_code=422, detail="Couldn't extract any text from that document.")

    title = os.path.splitext(file.filename)[0]
    return generate_and_save(
        text=text,
        title=title,
        source_url=file.filename,
        video_id=None,
        source_type="docx",
        origin="docx_extraction",
        learning_mode=learning_mode,
    )


@app.post("/api/generate-notes/audio")
async def generate_notes_from_audio(
    file: UploadFile = File(...),
    learning_mode: str = Form(DEFAULT_LEARNING_MODE),
):
    """Source: uploaded audio file — transcribed locally with the same faster-whisper
    model used as the YouTube-captions fallback."""
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="That audio file came through empty.")

    suffix = os.path.splitext(file.filename or "")[1] or ".audio"
    workdir = tempfile.mkdtemp(prefix="v2n_upload_")
    try:
        audio_path = os.path.join(workdir, f"upload{suffix}")
        with open(audio_path, "wb") as f:
            f.write(file_bytes)
        result = transcribe_audio(audio_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if not result.text.strip():
        raise HTTPException(
            status_code=422,
            detail="Whisper produced no speech text for this audio file.",
        )

    title = os.path.splitext(file.filename or "Audio note")[0]
    return generate_and_save(
        text=result.text,
        title=title,
        source_url=file.filename or "",
        video_id=None,
        source_type="audio",
        origin="whisper",
        learning_mode=learning_mode,
    )


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

@app.get("/api/library")
def library():
    """Everything the student has generated so far, newest first."""
    return {"items": db.list_library()}


@app.get("/api/notes/{note_id}")
def get_note(note_id: int):
    note = db.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    return note


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int):
    """Remove a note, its source, its question sets, its chat sessions, and its
    RAG chunks. The DB delete is what must succeed; a failed Chroma cleanup is
    logged rather than raised, so the note still disappears from the library."""
    if not db.delete_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found.")

    try:
        rag.delete_note(note_id)
    except Exception as exc:
        print(f"[rag] failed to remove chunks for note {note_id}: {exc}")

    return {"deleted": note_id}


@app.post("/api/reindex")
def reindex():
    """Manually (re)build the RAG index from every note currently in the
    database. Useful for backfilling notes generated before RAG existed."""
    try:
        return rag.reindex_all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Reindex failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Practice questions
# ---------------------------------------------------------------------------

@app.post("/api/generate-questions")
def generate_questions_endpoint(req: QuestionsRequest):
    """Generate practice questions from an already-generated note. Any source
    format works, because the note itself was built from one."""
    note = db.get_note(req.note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")

    learning_mode = normalize_learning_mode(req.learning_mode)
    count = questions_mod.normalize_count(req.count)
    from learning_modes import question_style   # or add to the top-level imports
    system = (
        f"{questions_mod.QUESTION_SYSTEM}\n\n"
        f"DIFFICULTY CALIBRATION: {question_style(learning_mode)}"
    )
    try:
        items = questions_mod.generate_questions(
            markdown=note["structured_markdown"],
            title=note["title"],
            count=count,
            call_llm=call_groq,
            system_prompt=system,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Question generation failed: {exc}") from exc

    if not items:
        raise HTTPException(
            status_code=422,
            detail="The model didn't return usable questions. Try again.",
        )

    set_id = db.save_question_set(
        note_id=req.note_id,
        requested_count=count,
        learning_mode=learning_mode,
        questions=items,
    )

    return {
        "set_id": set_id,
        "note_id": req.note_id,
        "title": note["title"],
        "requested_count": count,
        "returned_count": len(items),
        "learning_mode": learning_mode,
        "questions": items,
    }


@app.get("/api/question-sets/{set_id}")
def get_question_set(set_id: int):
    data = db.get_question_set(set_id)
    if not data:
        raise HTTPException(status_code=404, detail="Question set not found.")
    return data


# ---------------------------------------------------------------------------
# Chat — general Q&A, or scoped to one note. Both RAG-backed.
# Conversations are sessions, so old threads stay reopenable.
# ---------------------------------------------------------------------------

CHAT_HISTORY_TURNS = 10   # how many past messages to feed back in as context
RAG_TOP_K_SCOPED = 5      # chunks retrieved when chat is scoped to one note
RAG_TOP_K_GENERAL = 6     # chunks retrieved when searching the whole library

GENERAL_CHAT_SYSTEM_PROMPT = (
    "You are a friendly, precise study assistant. Answer clearly and concisely, "
    "and show your reasoning for anything mathematical or technical. If you are "
    "not confident a term means what you think it means — especially acronyms, "
    "tool names, and library names that could refer to several things — say so "
    "and ask which one the student means, rather than guessing."
)

GENERAL_RAG_SYSTEM_PROMPT_TEMPLATE = (
    "You are a study assistant with access to the student's note library. Below are the "
    "passages most relevant to their question, pulled from one or more of their generated "
    "notes. Use them as your primary source of truth, and mention which note(s) a fact came "
    "from when it's useful. If the passages don't cover the question, say so, then answer "
    "from general knowledge.\n\n{context}"
)

NOTE_RAG_SYSTEM_PROMPT_TEMPLATE = (
    "You are a study assistant helping a student understand a specific set of notes titled "
    "\"{title}\". Below are the passages from those notes most relevant to their question. "
    "Answer using them as your primary source of truth. If the question can't be answered "
    "from these passages, say so, then answer from general knowledge.\n\n{context}"
)

# Used only as a fallback when the RAG index has nothing for this note yet
# (e.g. rag deps aren't installed, or indexing failed silently at generation time).
NOTE_WHOLE_NOTE_SYSTEM_PROMPT_TEMPLATE = (
    "You are a study assistant helping a student understand a specific set of notes. "
    "Answer using the notes below as your primary source of truth. If the question can't "
    "be answered from the notes, say so, then answer from general knowledge.\n\n"
    "--- NOTES: {title} ---\n{markdown}\n--- END NOTES ---"
)


def _format_context(hits: list) -> str:
    """Turn retrieved chunks into a labeled context block for the system prompt."""
    blocks = [f"[From \"{hit['title']}\"]\n{hit['chunk']}" for hit in hits]
    return "\n\n---\n\n".join(blocks)


def _dedup_sources(hits: list) -> list:
    """One entry per note_id, in the order it first appeared (best match first)."""
    seen = set()
    sources = []
    for hit in hits:
        if hit["note_id"] in seen:
            continue
        seen.add(hit["note_id"])
        sources.append({"note_id": hit["note_id"], "title": hit["title"]})
    return sources


@app.get("/api/chat/sessions")
def chat_sessions():
    """Every conversation, most-recently-active first (populates the dropdown)."""
    return {"sessions": db.list_chat_sessions()}


@app.post("/api/chat/sessions")
def new_chat_session(scope: str = "general"):
    """Explicitly create an empty session. The frontend doesn't need this —
    POST /api/chat creates one implicitly on the first message — but it's
    useful for testing."""
    return {"session_id": db.create_chat_session(scope)}


@app.get("/api/chat/history")
def chat_history(session_id: int):
    return {"messages": db.get_chat_history(session_id)}


@app.delete("/api/chat/sessions/{session_id}")
def delete_chat_session(session_id: int):
    if not db.delete_chat_session(session_id):
        raise HTTPException(status_code=404, detail="Chat not found.")
    return {"deleted": session_id}


@app.post("/api/chat")
def chat(req: ChatRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message can't be empty.")

    # Resolve the session, creating one on the conversation's first message.
    # The session owns the scope, so req.scope only matters at creation time.
    session_id = req.session_id
    is_new = session_id is None
    if is_new:
        session_id = db.create_chat_session((req.scope or "general").strip())

    session = db.get_chat_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    scope = session["scope"]

    sources: list = []

    if scope.startswith("note:"):
        try:
            note_id = int(scope.split(":", 1)[1])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid chat scope.")

        note = db.get_note(note_id)
        if not note:
            raise HTTPException(status_code=404, detail="Note not found for this chat scope.")

        hits = []
        try:
            hits = rag.query(message, top_k=RAG_TOP_K_SCOPED, note_id=note_id)
        except Exception as exc:
            print(f"[rag] query failed, falling back to whole-note context: {exc}")

        if hits:
            system_prompt = NOTE_RAG_SYSTEM_PROMPT_TEMPLATE.format(
                title=note["title"], context=_format_context(hits)
            )
            sources = _dedup_sources(hits)
        else:
            system_prompt = NOTE_WHOLE_NOTE_SYSTEM_PROMPT_TEMPLATE.format(
                title=note["title"], markdown=note["structured_markdown"]
            )

    else:
        # General chat searches across the WHOLE library via RAG.
        hits = []
        try:
            hits = rag.query(message, top_k=RAG_TOP_K_GENERAL, note_id=None)
        except Exception as exc:
            print(f"[rag] query failed, falling back to plain general chat: {exc}")

        if hits:
            system_prompt = GENERAL_RAG_SYSTEM_PROMPT_TEMPLATE.format(
                context=_format_context(hits)
            )
            sources = _dedup_sources(hits)
        else:
            system_prompt = GENERAL_CHAT_SYSTEM_PROMPT

    learning_mode = normalize_learning_mode(req.learning_mode)
    system_prompt = apply_learning_mode(system_prompt, learning_mode)

    history = db.get_chat_history(session_id)[-CHAT_HISTORY_TURNS:]
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history)
    messages.append({"role": "user", "content": message})

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.4,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Chat request to Groq failed: {exc}") from exc

    reply = response.choices[0].message.content

    db.save_chat_message(session_id, "user", message)
    db.save_chat_message(session_id, "assistant", reply)

    # Name the thread after its opening question so the dropdown is readable.
    if is_new:
        title = message[:60] + ("…" if len(message) > 60 else "")
        db.rename_chat_session(session_id, title)
    else:
        db.touch_chat_session(session_id)

    return {
        "reply": reply,
        "sources": sources,
        "session_id": session_id,
        "learning_mode": learning_mode,
    }


# ---------------------------------------------------------------------------
# Export
#
# NOTE: the questions route is declared FIRST on purpose. FastAPI matches in
# declaration order, and /api/export/{note_id}/docx would otherwise try to
# parse "questions" as an int for a URL like /api/export/questions/3/docx.
# ---------------------------------------------------------------------------

@app.get("/api/export/questions/{set_id}/{fmt}")
def export_questions(set_id: int, fmt: str, answers: bool = False):
    """Download a question set as .docx or .pdf. `answers=true` includes the
    answer key; the frontend passes whatever the Show/Hide toggle is set to."""
    data = db.get_question_set(set_id)
    if not data:
        raise HTTPException(status_code=404, detail="Question set not found.")

    if fmt not in ("docx", "pdf"):
        raise HTTPException(status_code=400, detail="Format must be docx or pdf.")

    markdown = questions_mod.to_markdown(
        title=data["title"],
        items=data["questions"],
        include_answers=answers,
    )

    suffix = "with-answers" if answers else "questions-only"
    filename = f"{_safe_filename(data['title'])}_{suffix}.{fmt}"
    doc_title = f"Practice questions — {data['title']}"

    if fmt == "docx":
        file_bytes = export.markdown_to_docx(markdown, doc_title)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        file_bytes = export.markdown_to_pdf(markdown, doc_title)
        media_type = "application/pdf"

    return Response(
        content=file_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/{note_id}/markdown")
def export_markdown(note_id: int):
    note = db.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    filename = _safe_filename(note["title"]) + ".md"
    return Response(
        content=note["structured_markdown"],
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/{note_id}/docx")
def export_docx(note_id: int):
    note = db.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    filename = _safe_filename(note["title"]) + ".docx"
    file_bytes = export.markdown_to_docx(note["structured_markdown"], note["title"])
    return Response(
        content=file_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/{note_id}/pdf")
def export_pdf(note_id: int):
    note = db.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    filename = _safe_filename(note["title"]) + ".pdf"
    file_bytes = export.markdown_to_pdf(note["structured_markdown"], note["title"])
    return Response(
        content=file_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _safe_filename(title: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")
    return cleaned[:80] or "video-notes"


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Serve the frontend (static/index.html) at http://127.0.0.1:8000/
# Must be mounted LAST so it doesn't shadow the /api routes above.
# ---------------------------------------------------------------------------

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")