"""
Video-to-Notes AI Platform — STEP 4: Real RAG for chat
Replaces the "paste the whole note into the prompt" approach with proper
embedding-based retrieval, per Priority 1 of next-steps.md.

Pipeline:
  1. Whenever a note is generated (or an old one is re-indexed), its Markdown
     is split into overlapping chunks and embedded with a local
     sentence-transformers model (all-MiniLM-L6-v2 — small, fast, free,
     runs on CPU, no API calls).
  2. Chunks + embeddings are stored in a local, self-hosted ChromaDB
     collection (one collection for everything, filtered by note_id metadata
     for note-scoped chat — see Section notes in next-steps.md).
  3. On each chat turn, the question is embedded and the top-k most similar
     chunks are retrieved and used as the LLM's context, instead of the
     entire note. For "general" chat this searches *across every note in
     the library*, which is the "ask across all my notes" feature the plan
     called out as a future extension.

Everything here runs locally — no embeddings API, no external vector DB.
The Chroma index lives at backend/chroma_db/ (created automatically).
"""

from __future__ import annotations

import os
import re
import threading
from typing import List, Optional, TypedDict

# CHROMA_DIR = os.path.join(os.path.dirname(__file__), "chroma_db")
DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(__file__))
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
COLLECTION_NAME = "notes_chunks"
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")

# Chunking knobs — tuned for typical study-notes Markdown (headings + bullets).
CHUNK_WORDS = 220
CHUNK_OVERLAP_WORDS = 40


class RetrievedChunk(TypedDict):
    chunk: str
    note_id: int
    title: str
    chunk_index: int
    distance: float


# ---------------------------------------------------------------------------
# Lazy singletons — the embedding model and Chroma client are both a bit slow
# to initialize, so we only pay that cost once, on first use, not at import
# time (keeps `uvicorn main:app --reload` snappy).
# ---------------------------------------------------------------------------

_model = None
_client = None
_collection = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise RuntimeError(
                        "sentence-transformers is not installed. Run "
                        "'pip install -r requirements.txt' inside backend/ and try again."
                    ) from exc
                # Downloads the model from Hugging Face on first use only, then caches it
                # locally (same pattern as faster-whisper in transcription.py).
                _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def _get_collection():
    global _client, _collection
    if _collection is None:
        with _lock:
            if _collection is None:
                try:
                    import chromadb
                    from chromadb.config import Settings
                except ImportError as exc:
                    raise RuntimeError(
                        "chromadb is not installed. Run "
                        "'pip install -r requirements.txt' inside backend/ and try again."
                    ) from exc
                os.makedirs(CHROMA_DIR, exist_ok=True)
                _client = chromadb.PersistentClient(
                    path=CHROMA_DIR,
                    settings=Settings(anonymized_telemetry=False),
                )
                _collection = _client.get_or_create_collection(
                    name=COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"},
                )
    return _collection


def is_available() -> bool:
    """True once the embedding model + Chroma collection can be loaded without
    raising. Callers use this to fall back gracefully (e.g. to the old
    whole-note-as-context behavior) if the RAG deps aren't installed yet."""
    try:
        _get_model()
        _get_collection()
        return True
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _split_into_chunks(markdown_text: str) -> List[str]:
    """Split notes Markdown into overlapping ~CHUNK_WORDS-word pieces, preferring
    to break on H1-H3 headings first so a chunk doesn't straddle two unrelated
    sections. Short sections are kept whole; long ones get a sliding window."""
    text = markdown_text.strip()
    if not text:
        return []

    # Keep each heading attached to the section that follows it.
    sections = re.split(r"\n(?=#{1,3}\s)", text)

    chunks: List[str] = []
    for section in sections:
        words = section.split()
        if not words:
            continue
        if len(words) <= CHUNK_WORDS:
            chunks.append(section.strip())
            continue

        start = 0
        while start < len(words):
            end = start + CHUNK_WORDS
            piece = " ".join(words[start:end]).strip()
            if piece:
                chunks.append(piece)
            if end >= len(words):
                break
            start = end - CHUNK_OVERLAP_WORDS

    return chunks or [text]


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def index_note(note_id: int, title: str, markdown_text: str) -> int:
    """(Re-)index one note's chunks. Safe to call again for the same note —
    ids are deterministic (note{id}-chunk{i}) so this upserts rather than
    duplicating. Returns the number of chunks written."""
    chunks = _split_into_chunks(markdown_text)
    if not chunks:
        return 0

    model = _get_model()
    collection = _get_collection()

    embeddings = model.encode(chunks, show_progress_bar=False, convert_to_numpy=True).tolist()
    ids = [f"note{note_id}-chunk{i}" for i in range(len(chunks))]
    metadatas = [
        {"note_id": note_id, "title": title, "chunk_index": i} for i in range(len(chunks))
    ]

    collection.upsert(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
    return len(chunks)


def delete_note(note_id: int) -> None:
    """Remove all indexed chunks for a note (e.g. if it's ever deleted)."""
    collection = _get_collection()
    collection.delete(where={"note_id": note_id})


def reindex_all() -> dict:
    """Backfill: (re)index every note currently in SQLite. Used on startup for
    notes that existed before RAG was added, and exposed via POST /api/reindex
    so it can be re-run on demand."""
    import db  # local import: avoids any import-order surprises at module load

    items = db.list_library()
    chunks_indexed = 0
    for item in items:
        note = db.get_note(item["note_id"])
        if not note:
            continue
        chunks_indexed += index_note(note["note_id"], note["title"], note["structured_markdown"])

    return {"notes_indexed": len(items), "chunks_indexed": chunks_indexed}


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def query(query_text: str, top_k: int = 5, note_id: Optional[int] = None) -> List[RetrievedChunk]:
    """Embed `query_text` and return the top_k most similar chunks. When
    note_id is given, results are restricted to that note (note-scoped chat);
    otherwise the whole library is searched (general chat -> ask across all
    your notes)."""
    collection = _get_collection()
    if collection.count() == 0:
        return []

    model = _get_model()
    query_embedding = model.encode([query_text], convert_to_numpy=True).tolist()

    where = {"note_id": note_id} if note_id is not None else None
    n_results = min(top_k, collection.count())

    result = collection.query(
        query_embeddings=query_embedding,
        n_results=n_results,
        where=where,
    )

    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]

    hits: List[RetrievedChunk] = []
    for doc, meta, dist in zip(docs, metas, dists):
        hits.append(
            {
                "chunk": doc,
                "note_id": meta.get("note_id"),
                "title": meta.get("title"),
                "chunk_index": meta.get("chunk_index"),
                "distance": dist,
            }
        )
    return hits
