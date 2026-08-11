# Video-to-Notes AI — Step 2: Whisper Fallback + Library + Export

Builds on **Step 1's core pipeline** (paste link → captions → Groq notes) with the three
things Step 1's README promised next:

1. **Whisper fallback** — videos with no captions now transcribe locally via `yt-dlp` (audio
   only) + `faster-whisper`, instead of just failing.
2. **Persistence** — every generated note is saved to a small SQLite database
   (`backend/video_to_notes.db`, created automatically on first run).
3. **Library screen** — a new tab in the UI lists everything you've processed, newest first.
4. **Export** — Markdown / DOCX / PDF download buttons on every note, in both the notes view
   and the Library.

PDF/RAG uploads, chat-with-notes, and MCP tools are still later steps (Phases 3–4 of the plan).

```
video-to-notes/
├── backend/
│   ├── main.py              ← FastAPI app + routes (pipeline, library, export)
│   ├── db.py                ← SQLite persistence (new)
│   ├── transcription.py     ← yt-dlp + faster-whisper fallback (new)
│   ├── export.py            ← Markdown → DOCX / PDF conversion (new)
│   ├── requirements.txt     ← Python dependencies (updated)
│   ├── .env.example         ← template for your API key + Whisper settings
│   └── video_to_notes.db    ← created automatically on first run (not in git)
├── static/
│   └── index.html           ← UI: Generate tab + Library tab + export buttons (updated)
└── README.md
```

## 1. Prerequisites

- Python 3.10+ installed
- A free Groq account (for the LLM): https://console.groq.com
- ~1GB free disk space the first time a Whisper model downloads (one-time, cached after that)

## 2. Get a free Groq API key

1. Go to https://console.groq.com and sign up (free).
2. Open **API Keys** in the left sidebar → **Create API Key**.
3. Copy the key — you'll only see it once.

## 3. Install the packages

Open a terminal in the `video-to-notes/backend` folder and run:

```bash
python -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

This now also installs `yt-dlp`, `faster-whisper`, `python-docx`, and `reportlab`.
`faster-whisper` downloads its model from Hugging Face automatically the first time it's
actually used (i.e. the first time you process a video with no captions) — not at install time.

## 4. Add your API key

```bash
cp .env.example .env
```

Open `.env` and paste your key:

```
GROQ_API_KEY=gsk_your_real_key_here
```

The Whisper settings in `.env.example` (`WHISPER_MODEL_SIZE`, `WHISPER_DEVICE`,
`WHISPER_COMPUTE_TYPE`) are optional — the defaults (small / cpu / int8) work out of the box.

## 5. Run it

```bash
uvicorn main:app --reload
```

The first run creates `backend/video_to_notes.db` automatically — nothing else to set up.

## 6. Use it

Open **http://127.0.0.1:8000**:

1. **Generate tab** — paste a video link and click **Generate notes**, same as Step 1.
   - If the video has captions, it works exactly like before (instant).
   - If not, you'll see it take longer the first time — it's downloading audio and
     transcribing locally with Whisper. Subsequent runs are faster once the model is cached.
2. Every generated note is now saved — you'll see **Export .md / .docx / .pdf** buttons
   right under the notes.
3. **Library tab** — see every note you've generated, with the same View/export actions.

## Troubleshooting

- **"Microsoft Visual C++ 14.0 or greater is required" / "Failed building wheel for av"** →
  this happens on newer Python versions (3.13) with old pins. Make sure you're installing from
  the `requirements.txt` in *this* Step 2 package (it uses `faster-whisper>=1.1.0`, not an older
  pinned version) — that alone pulls a version of `av` with a ready-made Windows wheel, so no
  compiler is needed. If it still happens: `pip install --upgrade pip` first, then re-run
  `pip install -r requirements.txt`.
- **"GROQ_API_KEY is not set"** → you skipped step 4, or `.env` isn't in `backend/`.
- **Whisper fallback is slow** → normal on CPU-only machines, especially the first video
  (model download). Drop `WHISPER_MODEL_SIZE` to `base` or `tiny` in `.env` for more speed
  at some accuracy cost — see Section 11 of the plan.
- **"Could not download audio for this video"** → some videos are geo-restricted or
  age-restricted and `yt-dlp` can't fetch them without extra auth; try a different video.
- **Exports look empty/odd** → export parses the Markdown structure the LLM produces
  (`##` headings, `-` bullets, `**bold**` terms) — if you edited the raw notes elsewhere
  into a very different shape, formatting may not carry over perfectly.
- **CORS / connection errors** → open `http://127.0.0.1:8000` (served by the backend), not
  `static/index.html` directly on disk.

## What's next (Step 3 preview)

- PDF / existing-notes upload, chunking, embeddings, and a ChromaDB vector store (Section 5
  of the plan).
- "Chat with your notes" — ask follow-up questions across a video's notes and your own
  documents together.

We'll keep building one working, tested piece at a time.

## Step 4: Real RAG for chat (latest)

Chat no longer pastes the whole note's Markdown into the prompt. Instead:

- `backend/rag.py` chunks every generated note (~220 words per chunk, header-aware, with
  overlap), embeds the chunks locally with `sentence-transformers` (`all-MiniLM-L6-v2`,
  no API calls), and stores them in a local, self-hosted **ChromaDB** collection at
  `backend/chroma_db/` (created automatically, not in git).
- Note-scoped chat (`note:<id>`) retrieves the top-5 chunks most relevant to your question
  from that note, instead of the whole thing.
- **General chat now searches your entire note library** — ask something without picking a
  note first and it'll pull the most relevant passages from any of your generated notes,
  and tell you which note(s) it used (shown as small "from: ..." chips under the reply in
  the UI; click one to jump chat into that note's scope).
- New endpoint: `POST /api/reindex` — rebuilds the RAG index from every note already in
  `video_to_notes.db`. Also runs automatically once in the background on server startup,
  so notes generated before this upgrade get indexed without you doing anything; call it
  by hand if you ever want to force a rebuild.
- If the embedding deps aren't installed yet (`pip install -r requirements.txt` now also
  pulls in `sentence-transformers` + `chromadb`) or the index has nothing for a note yet,
  chat quietly falls back to the old whole-note-as-context behavior — it never breaks, it
  just gets smarter once RAG is available.

First run downloads the small `all-MiniLM-L6-v2` embedding model from Hugging Face once,
then it's cached locally, same as the Whisper model already is.

### What's next after this

- Real progress reporting (SSE/WebSocket) instead of the fake `setTimeout` step indicator.
- React/Vite/Tailwind/shadcn frontend rewrite (same backend API, no changes needed there).
- Auth (Supabase) + Postgres, then deployment.
