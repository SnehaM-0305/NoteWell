/* ===========================================================================
   Notewell — entry point.

   This file's only jobs:
     1. Import every feature module (which is what actually runs their code
        and wires up their internal event listeners).
     2. Expose the handful of functions index.html still calls via inline
        onclick="..."/onchange="..." attributes. Those attributes look up a
        plain GLOBAL function by name -- a function declared inside an ES
        module is NOT automatically global, so each one needed here has to
        be attached to `window` explicitly. Everything else (buttons built
        dynamically in JS, e.g. the section Edit/Regenerate buttons, or the
        Library delete button) attaches its own listener directly in code
        and needs nothing added here.
     3. Kick off the one bit of real startup logic: reading the saved
        Learning Mode preference from localStorage.

   All the actual feature code lives in the other files in this folder.
   =========================================================================== */

import { initLearningMode, setLearningMode } from './learningMode.js';
import { setMode, setSource, handleFile } from './navigation.js';
import { generateNotes, copyNotes, goChatWithNote } from './generate.js';
import { onScopeChange, newChat, sendChat, copyConversation } from './chat.js';
import { setQuestionCount, generateQuestions, toggleAnswers, downloadQuestions } from './questions.js';
import { closeLibraryNote, askAboutLibraryNote } from './library.js';

Object.assign(window, {
  setLearningMode,
  setMode,
  setSource,
  handleFile,
  generateNotes,
  copyNotes,
  goChatWithNote,
  onScopeChange,
  newChat,
  sendChat,
  copyConversation,
  setQuestionCount,
  generateQuestions,
  toggleAnswers,
  downloadQuestions,
  closeLibraryNote,
  askAboutLibraryNote,
});

document.addEventListener('DOMContentLoaded', initLearningMode);
