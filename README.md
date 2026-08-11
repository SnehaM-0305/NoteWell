# Notewell

**Live demo → [notewell-7p6e.onrender.com](https://notewell-7p6e.onrender.com)**

Turn a YouTube link, PDF, Word document, audio file, or pasted text into structured
study notes — then chat with them, generate practice questions, and export everything
to Markdown, DOCX, or PDF.

Built to run on a free stack: Groq for the LLM, local Whisper for transcription, local
embeddings for retrieval, SQLite and ChromaDB for storage. No paid APIs beyond a free
Groq key.

> **About the demo:** it's on Render's free tier, so the first request after a period
> of inactivity takes a minute or so while the server wakes and reloads its models.
> Nothing is persisted between restarts — anything you generate is yours for that
> session only.

> **About accuracy:** notes, chat replies, and practice questions are AI-generated and
> can contain confident mistakes, particularly around tools or terms with similar names.
> Check anything important against your original source.

---

## What it does

**Five input types.** YouTube links (uses captions where available, falls back to local
Whisper transcription where not), PDF, DOCX, audio uploads, and pasted text. Everything
funnels into the same chunk → summarize → structure pipeline.

**Three learning modes.** Beginner, Medium, and Expert produce genuinely different
documents, not the same notes at three lengths. The mode changes the *structure* of the
output, not just the wording: Beginner leads with a concrete example and includes a
glossary, Expert opens with a dense summary, drops the glossary as padding, and adds
sections on trade-offs and open questions. The mode also shapes chat replies and the
difficulty calibration of practice questions.

**Chat with your notes.** RAG-backed retrieval over a local vector store. Scope a
conversation to one note, or leave it general and it searches your whole library,
showing which notes it drew from. Conversations are saved as threads you can reopen.

**Practice questions.** Generate 10, 20, 50, or 100 from any saved note. Long sets are
produced in batches with previously-generated questions fed back in, so the model
doesn't repeat itself. Export to DOCX or PDF, with or without the answer key depending
on whether answers are shown on screen.

**Library.** Browse, reopen, and delete everything you've generated. Deleting a note
removes its question sets, its chat threads, and its vector-store chunks.

---

## Project structure

```
vtn/
├── backend/
│   ├── main.py              FastAPI app — all routes
│   ├── db.py                SQLite persistence + schema migrations
│   ├── rag.py               Chunking, embeddings, ChromaDB retrieval
│   ├── questions.py         Practice-question generation + Markdown rendering
│   ├── learning_modes.py    Beginner/Medium/Expert prompts and note structures
│   ├── extraction.py        PDF and DOCX text extraction
│   ├── transcription.py     yt-dlp + faster-whisper
│   ├── export.py            Markdown → DOCX / PDF
│   ├── requirements.txt
│   ├── .env.example
│   ├── video_to_notes.db    created on first run (gitignored)
│   └── chroma_db/           created on first run (gitignored)
├── static/
│   ├── index.html
│   ├── css/styles.css
│   └── js/app.js
└── README.md
```

---

## Running it locally

### Prerequisites

- Python 3.10+
- A free Groq API key from [console.groq.com](https://console.groq.com)
- ~1 GB free disk space for model downloads (one-time, cached afterwards)

### Setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

Copy the environment template and add your key:

```bash
copy .env.example .env         # Windows
# cp .env.example .env         # macOS / Linux
```

```
GROQ_API_KEY=gsk_your_real_key_here
```

The Whisper settings in `.env.example` (`WHISPER_MODEL_SIZE`, `WHISPER_DEVICE`,
`WHISPER_COMPUTE_TYPE`) are optional — the defaults (small / cpu / int8) work as-is.

### Run

```bash
uvicorn main:app --reload
```

Open **http://127.0.0.1:8000**. The database and vector store are created automatically
on first run.

Two models download from Hugging Face on first use, not at install time:
`all-MiniLM-L6-v2` for embeddings (~90 MB) and a Whisper model the first time you
process something with no captions.

---

## How it works

### The notes pipeline

Any source is reduced to plain text, split into ~900-word chunks, summarized chunk by
chunk (the map step), then assembled into structured Markdown (the reduce step).

The map step is deliberately **not** mode-aware. Simplifying there loses detail the
reduce step can never recover — an early version produced Expert notes that were vaguer
than Medium because specifics had already been dropped. The chunk summarizer preserves
every proper noun and figure; `build_notes()` decides what the reader actually sees.

### Learning modes

Each mode supplies two things: a prose-style block appended to the system prompt, and a
Markdown skeleton injected into the user prompt.

The skeleton is what matters. An earlier version varied only the system prompt and the
three modes came out nearly identical, because a hardcoded output structure in the user
prompt overrides tone guidance in the system prompt. Explicit shape instructions belong
in the user prompt.

### RAG

`rag.py` splits each note into ~220-word overlapping chunks, breaking on headings first
so a chunk doesn't straddle unrelated sections. Chunks are embedded locally with
`sentence-transformers` and stored in ChromaDB, filtered by `note_id` metadata for
note-scoped chat.

If the embedding dependencies are missing or a note hasn't been indexed, chat falls back
to passing the whole note as context. It degrades rather than breaking.

`POST /api/reindex` rebuilds the index from every note in the database. It also runs
once in a background thread at startup, so notes generated before RAG existed get
picked up automatically.

### Question generation

100 questions in one LLM call produces repetition and drift, so sets are generated in
batches of 20 with already-written questions passed back in as things to avoid. Output
is JSON, parsed defensively — models wrap it in code fences despite instructions not to.

---

## API

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/generate-notes` | From a YouTube URL |
| `POST` | `/api/generate-notes/text` | From pasted text |
| `POST` | `/api/generate-notes/pdf` | From an uploaded PDF |
| `POST` | `/api/generate-notes/docx` | From an uploaded Word document |
| `POST` | `/api/generate-notes/audio` | From an uploaded audio file |
| `GET` | `/api/library` | All notes, newest first |
| `GET` | `/api/notes/{id}` | One note |
| `DELETE` | `/api/notes/{id}` | Delete a note and everything derived from it |
| `POST` | `/api/generate-questions` | Practice questions from a note |
| `GET` | `/api/question-sets/{id}` | Retrieve a saved set |
| `GET` | `/api/chat/sessions` | All conversations |
| `GET` | `/api/chat/history?session_id=` | Messages in one conversation |
| `POST` | `/api/chat` | Send a message (creates a session if none given) |
| `DELETE` | `/api/chat/sessions/{id}` | Delete a conversation |
| `POST` | `/api/reindex` | Rebuild the vector index |
| `GET` | `/api/export/{id}/{md\|docx\|pdf}` | Export a note |
| `GET` | `/api/export/questions/{id}/{docx\|pdf}?answers=` | Export a question set |

---

## Deploying

The live demo runs on Render's free tier:

- **Root directory:** `backend`
- **Build:** `pip install -r requirements.txt`
- **Start:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
- **Environment:** `GROQ_API_KEY`

`requirements.txt` pins the CPU-only PyTorch index. Without that line, Linux pulls the
CUDA build — about 2.5 GB of NVIDIA libraries this app never touches, which won't fit in
a free-tier container.

Free tier has no persistent disk, so the SQLite database and ChromaDB store reset on
every restart. Fine for a demo where visitors generate their own notes; a paid plan with
a mounted disk, or an external Postgres, would be needed to keep anything.

---

## Troubleshooting

**"GROQ_API_KEY is not set"** — `.env` is missing, or it isn't inside `backend/`.

**"Failed building wheel for av" / "Microsoft Visual C++ 14.0 required"** — an old
`faster-whisper` pin resolving to an `av` version with no Windows wheel. Run
`pip install --upgrade pip`, then reinstall from this `requirements.txt`.

**Whisper is slow** — expected on CPU, especially the first run while the model
downloads. Drop `WHISPER_MODEL_SIZE` to `base` or `tiny` in `.env` to trade accuracy for
speed.

**"Could not download audio for this video"** — geo- or age-restricted videos need auth
`yt-dlp` doesn't have. Try another video.

**Exports look wrong** — the exporters parse the Markdown the model produces (`##`
headings, `-` bullets, `**bold**`). Heavily hand-edited notes may not convert cleanly.

**CORS or connection errors** — open `http://127.0.0.1:8000`, not `static/index.html`
from disk.

**Chat cites a note you deleted** — the Chroma delete failed silently. Check the console
for a `[rag]` line and run `POST /api/reindex`.

---

## Known limitations

- **Scanned PDFs don't work.** Text extraction only, no OCR.
- **The model invents connections between similarly-named things.** Observed with
  uv/libuv, uv/Uvicorn, and LangChain/LangGraph. Prompt constraints reduce it; nothing
  eliminates it at this model size.
- **Progress reporting is fake.** The step indicator runs on a timer, not real events
  from the server.
- **No authentication.** Single-user by design; the learning-mode preference lives in
  `localStorage`.

## Possible next steps

- Real progress via SSE or WebSocket
- Streaming chat replies
- Interactive quiz mode — answer questions in-app rather than reading a list
- Flashcards generated from the Key Terms section
- Groq's hosted Whisper instead of local, which would cut the container size
  dramatically and make audio viable on free hosting
