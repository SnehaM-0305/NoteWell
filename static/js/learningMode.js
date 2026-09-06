/* ===========================================================================
   Learning Mode — a single global preference (Beginner / Medium / Expert)
   sent along with every AI request (notes, chat, questions). Persisted in
   localStorage since the app has no user accounts.
   =========================================================================== */

import { state, LEARNING_MODES, LEARNING_MODE_STORAGE_KEY } from './state.js';

export function setLearningMode(mode) {
  if (!LEARNING_MODES.includes(mode)) mode = 'medium';
  state.learningMode = mode;
  localStorage.setItem(LEARNING_MODE_STORAGE_KEY, mode);
  LEARNING_MODES.forEach(m => {
    document.getElementById('learningBtn-' + m).classList.toggle('active', m === mode);
  });
}

export function initLearningMode() {
  const stored = localStorage.getItem(LEARNING_MODE_STORAGE_KEY);
  setLearningMode(LEARNING_MODES.includes(stored) ? stored : 'medium');
}
