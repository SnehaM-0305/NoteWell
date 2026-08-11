# Learning Mode

A global, user-selectable setting — **Beginner / Medium / Expert** — that shapes
the writing style of every AI-generated response in the app: chat replies,
generated notes, and chunk summaries.

## How it works

**Central definition — `backend/learning_modes.py`**
This is the single source of truth. It defines the three modes' behavioral
instructions and exposes one function every AI call site uses:

```python
apply_learning_mode(base_system_prompt: str, mode: str | None) -> str
```

It appends the selected mode's instructions to whatever system prompt a call
site already builds (RAG context, whole-note context, general chat, etc.), so
mode behavior is defined once and applied everywhere consistently. Adding a
4th mode later is a one-line addition to the `LEARNING_MODES` dict — no other
file needs to change.

`normalize_learning_mode()` validates incoming mode strings and falls back to
**Medium** for anything missing or unrecognized (the "no mode selected"
requirement).

## Where it's wired in (backend — `backend/main.py`)

| Call site | What changed |
|---|---|
| `summarize_chunk()` | System prompt now runs through `apply_learning_mode()` |
| `build_notes()` | Same — the final structured notes reflect the mode |
| `generate_and_save()` | Accepts `learning_mode`, normalizes it, passes it to both steps above, and saves it with the note |
| All 5 `POST /api/generate-notes*` endpoints | Accept `learning_mode` (JSON body for video/text, multipart form field for pdf/docx/audio) and pass it through |
| `POST /api/chat` | Applies the mode to whichever system prompt was selected (RAG-scoped, note-scoped, or general) before calling Groq |

Every endpoint defaults to `medium` if `learning_mode` is omitted or invalid.

## Persistence

- **Per note**: `notes.learning_mode` column in SQLite (`backend/db.py`) — so
  you can always see which mode a given set of notes was generated in
  (`GET /api/notes/{id}` and `GET /api/library` both return it). A lightweight
  migration (`db._migrate`) adds this column to existing databases without
  touching existing data.
- **Per user (frontend)**: the app has no accounts/sessions, so the *currently
  selected* mode lives in the browser's `localStorage`
  (`notewell.learningMode`) and is sent with every request. This is what
  makes the mode "persist between sessions."

## Frontend (`static/index.html`)

- A pill selector ("Beginner / Medium / Expert") sits above the Generate/Chat
  switch, visible from both panels — it's a global preference, not scoped to
  one screen.
- `setLearningMode(mode)` updates the active button, the `learningMode`
  global, and `localStorage`. `initLearningMode()` restores it on page load.
- `buildGenerateRequest()` and `sendChat()` both include `learning_mode` in
  their request payloads, so switching modes takes effect on the very next
  AI call — no page reload needed.
- The generated-notes meta line shows which mode was used, e.g.
  `3 chunks · from YouTube captions · Beginner mode`.

## Testing

`backend/smoke_test.py` (not part of the running app — a one-off verification
script) exercises this end-to-end with the Groq call mocked out (no network
egress to Groq is available in this environment): explicit modes, the
default-to-medium path, invalid-mode normalization, DB round-tripping, and
the chat endpoint, across both JSON and multipart request shapes. All 17
checks pass. It also confirmed the DB migration is non-destructive against
the project's existing `video_to_notes.db`.
