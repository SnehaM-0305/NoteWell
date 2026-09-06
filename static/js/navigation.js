/* ===========================================================================
   Mode / panel switching + the Generate tab's source-type tabs.

   NOTE ON IMPORTS: this file imports loadChatScopes/loadQuestionSources/
   loadLibrary so switching TO a panel can refresh its data. chat.js and
   library.js, in turn, import setMode FROM here (e.g. clicking a chat
   "source" chip, or "Ask a question" on a library note, needs to switch
   panels). That's a circular import between this file and those two.
   This is safe: every one of these calls happens inside a click handler
   (i.e. after the whole module graph has already finished loading), never
   while the modules are first being evaluated -- which is the only case
   where a circular ES module import would actually break.
   =========================================================================== */

import { state } from './state.js';
import { loadChatScopes } from './chat.js';
import { loadQuestionSources } from './questions.js';
import { loadLibrary } from './library.js';

/* `preferredSelection` is a chat-dropdown option value, passed through when
   another panel wants to jump into a specific conversation. Values are
   either a session id ("7") or a request for a fresh thread ("new:general",
   "new:note:3"). */
export function setMode(mode, preferredSelection) {
  document.getElementById('panelGenerate').classList.toggle('active', mode === 'generate');
  document.getElementById('panelChat').classList.toggle('active', mode === 'chat');
  document.getElementById('panelQuestions').classList.toggle('active', mode === 'questions');
  document.getElementById('panelLibrary').classList.toggle('active', mode === 'library');

  document.getElementById('modeGenerateBtn').classList.toggle('active', mode === 'generate');
  document.getElementById('modeChatBtn').classList.toggle('active', mode === 'chat');
  document.getElementById('modeQuestionsBtn').classList.toggle('active', mode === 'questions');
  document.getElementById('modeLibraryBtn').classList.toggle('active', mode === 'library');

  if (mode === 'chat') loadChatScopes(preferredSelection);
  if (mode === 'questions') loadQuestionSources();
  if (mode === 'library') loadLibrary();
}

export function setSource(source) {
  state.currentSource = source;
  document.querySelectorAll('.source-tab').forEach(el =>
    el.classList.toggle('active', el.dataset.source === source));
  document.querySelectorAll('.source-input').forEach(el => el.style.display = 'none');
  document.getElementById('input-' + source).style.display = 'block';

  const step1Labels = {
    text: 'Reading pasted text',
    pdf: 'Extracting text from PDF',
    docx: 'Extracting text from Word document',
    audio: 'Transcribing audio locally',
    video: 'Fetching transcript from captions',
  };
  document.getElementById('step1Label').textContent = step1Labels[source];
}

export function handleFile(input, kind) {
  const label = document.getElementById('fileLabel-' + kind);
  const file = input.files.length ? input.files[0] : null;
  state.selectedFiles[kind] = file;
  label.textContent = file ? file.name : '';
}
