/* ===========================================================================
   Shared mutable state, used by more than one feature module.

   Exported as properties on one object (not as separate `export let`
   bindings) on purpose: ES modules give every OTHER file a read-only view of
   an imported `let`/`const` binding — only the module that declares it is
   allowed to reassign it. Several places (e.g. library.js's
   deleteLibraryNote) need to both read AND write values owned by a
   different feature (chat's session state), so a shared object each file
   can mutate a property of (`state.lastNoteId = 5`) is simpler than wiring
   getter/setter functions for every single field.

   Anything used by only ONE file (chatTranscript, openLibraryNoteId,
   question-panel state) stays local to that file instead of living here —
   no need to share what nothing else touches.
   =========================================================================== */

export const state = {
  currentSource: 'text',
  lastMarkdown: '',
  lastNoteId: null,
  selectedFiles: { pdf: null, docx: null, audio: null },
  learningMode: 'medium',
  currentSessionId: null,   // null while a "new:" chat option is selected
  pendingScope: 'general',  // scope to create a chat session with, once sent
};

/* ---------------------------------------------------------------------------
   Cross-file constants/lookup tables.
   --------------------------------------------------------------------------- */

export const ORIGIN_LABELS = {
  captions: 'from YouTube captions',
  whisper: 'transcribed with Whisper',
  // NEW: Groq's hosted Whisper is now tried before the local fallback (see
  // main.py/transcription.py) -- this label was missing before, which meant
  // a Groq-transcribed note's meta line showed the raw "whisper_groq" code
  // instead of a friendly sentence. Added while splitting this file up.
  whisper_groq: 'transcribed with Groq Whisper (fast, high-accuracy)',
  pdf_extraction: 'extracted from PDF',
  docx_extraction: 'extracted from Word doc',
  pasted_text: 'from pasted text',
};

export const LEARNING_MODE_LABELS = {
  beginner: 'Beginner',
  medium: 'Medium',
  expert: 'Expert',
};

export const LEARNING_MODES = ['beginner', 'medium', 'expert'];
export const LEARNING_MODE_STORAGE_KEY = 'notewell.learningMode';
