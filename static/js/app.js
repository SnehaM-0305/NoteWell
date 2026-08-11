/* ===========================================================================
   Notewell — frontend logic
   Loaded from index.html after marked.min.js. Runs on DOMContentLoaded via
   the bootstrap block at the bottom.
   =========================================================================== */

let currentSource = 'text';
let lastMarkdown = "";
let lastNoteId = null;
let selectedFiles = { pdf: null, docx: null, audio: null };

const ORIGIN_LABELS = {
  captions: 'from YouTube captions',
  whisper: 'transcribed with Whisper',
  pdf_extraction: 'extracted from PDF',
  docx_extraction: 'extracted from Word doc',
  pasted_text: 'from pasted text',
};

const LEARNING_MODE_LABELS = {
  beginner: 'Beginner',
  medium: 'Medium',
  expert: 'Expert',
};

/* ---------------------------------------------------------------------------
   Learning Mode — a single global preference (Beginner / Medium / Expert)
   sent along with every AI request (notes, chat, questions). Persisted in
   localStorage since the app has no user accounts.
   --------------------------------------------------------------------------- */
const LEARNING_MODES = ['beginner', 'medium', 'expert'];
const LEARNING_MODE_STORAGE_KEY = 'notewell.learningMode';
let learningMode = 'medium';

function setLearningMode(mode){
  if(!LEARNING_MODES.includes(mode)) mode = 'medium';
  learningMode = mode;
  localStorage.setItem(LEARNING_MODE_STORAGE_KEY, mode);
  LEARNING_MODES.forEach(m => {
    document.getElementById('learningBtn-' + m).classList.toggle('active', m === mode);
  });
}

function initLearningMode(){
  const stored = localStorage.getItem(LEARNING_MODE_STORAGE_KEY);
  setLearningMode(LEARNING_MODES.includes(stored) ? stored : 'medium');
}

/* ---------------------------------------------------------------------------
   Mode / panel switching

   `preferredSelection` is a chat-dropdown option value, passed through when
   another panel wants to jump into a specific conversation. Values are either
   a session id ("7") or a request for a fresh thread ("new:general",
   "new:note:3").
   --------------------------------------------------------------------------- */
function setMode(mode, preferredSelection){
  document.getElementById('panelGenerate').classList.toggle('active', mode === 'generate');
  document.getElementById('panelChat').classList.toggle('active', mode === 'chat');
  document.getElementById('panelQuestions').classList.toggle('active', mode === 'questions');
  document.getElementById('panelLibrary').classList.toggle('active', mode === 'library');

  document.getElementById('modeGenerateBtn').classList.toggle('active', mode === 'generate');
  document.getElementById('modeChatBtn').classList.toggle('active', mode === 'chat');
  document.getElementById('modeQuestionsBtn').classList.toggle('active', mode === 'questions');
  document.getElementById('modeLibraryBtn').classList.toggle('active', mode === 'library');

  if(mode === 'chat') loadChatScopes(preferredSelection);
  if(mode === 'questions') loadQuestionSources();
  if(mode === 'library') loadLibrary();
}

function setSource(source){
  currentSource = source;
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

function handleFile(input, kind){
  const label = document.getElementById('fileLabel-' + kind);
  const file = input.files.length ? input.files[0] : null;
  selectedFiles[kind] = file;
  label.textContent = file ? file.name : '';
}

/* Truncate long note/chat titles for dropdowns, keeping the full text as a
   tooltip. Native <select> clamps option text to the control width, so this
   at least makes the cut-off deliberate. */
function shortLabel(text, max = 45){
  return text.length > max ? text.slice(0, max) + '…' : text;
}

/* ---------------------------------------------------------------------------
   Notes generation
   --------------------------------------------------------------------------- */
function setStep(id, state){
  const el = document.getElementById(id);
  el.classList.remove('active','done');
  if(state) el.classList.add(state);
}

function resetUI(){
  document.getElementById('progress').classList.add('active');
  document.getElementById('errorBox').classList.remove('active');
  document.getElementById('notesCard').classList.remove('active');
  setStep('step1',''); setStep('step2',''); setStep('step3','');
}

function showError(msg){
  document.getElementById('progress').classList.remove('active');
  const box = document.getElementById('errorBox');
  box.textContent = msg;
  box.classList.add('active');
}

/* Builds the {url, options} for whichever source tab is active, or throws a
   plain Error with a user-facing message if that source isn't ready yet. */
function buildGenerateRequest(){
  if(currentSource === 'video'){
    const videoUrl = document.getElementById('videoInput').value.trim();
    if(!videoUrl) throw new Error('Paste a YouTube link first.');
    return {
      url: '/api/generate-notes',
      options: {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_url: videoUrl, learning_mode: learningMode }),
      },
    };
  }

  if(currentSource === 'text'){
    const text = document.getElementById('textInput').value.trim();
    if(!text) throw new Error('Paste some text first.');
    return {
      url: '/api/generate-notes/text',
      options: {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, learning_mode: learningMode }),
      },
    };
  }

  if(currentSource === 'pdf' || currentSource === 'docx' || currentSource === 'audio'){
    const file = selectedFiles[currentSource];
    if(!file){
      const label = currentSource === 'audio' ? 'an audio file' : `a .${currentSource} file`;
      throw new Error(`Choose ${label} first.`);
    }
    const formData = new FormData();
    formData.append('file', file);
    formData.append('learning_mode', learningMode);
    return {
      url: `/api/generate-notes/${currentSource}`,
      options: { method: 'POST', body: formData },
    };
  }

  throw new Error('Unknown source type.');
}

async function generateNotes(){
  const btn = document.getElementById('generateBtn');

  let request;
  try {
    request = buildGenerateRequest();
  } catch(validationError){
    showError(validationError.message);
    return;
  }

  resetUI();
  btn.disabled = true;
  btn.textContent = "Working…";
  setStep('step1','active');

  // The API returns everything in one response (no progress events), so this
  // just nudges the step indicator along while the request is in flight.
  const stepTimer = setTimeout(() => {
    setStep('step1','done'); setStep('step2','active');
  }, 1200);

  try {
    const res = await fetch(request.url, request.options);
    const data = await res.json().catch(() => null);

    if(!res.ok){
      throw new Error((data && data.detail) || `Request failed (${res.status}).`);
    }

    clearTimeout(stepTimer);
    setStep('step1','done'); setStep('step2','done'); setStep('step3','done');

    lastMarkdown = data.notes_markdown;
    lastNoteId = data.note_id;

    const originLabel = ORIGIN_LABELS[data.transcript_origin] || data.transcript_origin;
    const modeLabel = LEARNING_MODE_LABELS[data.learning_mode] || 'Medium';
    document.getElementById('chunkLabel').textContent =
      `${data.num_chunks} chunk${data.num_chunks === 1 ? '' : 's'} · ${originLabel} · ${modeLabel} mode`;
    document.getElementById('notesBody').innerHTML = marked.parse(lastMarkdown);
    wireExportLinks(lastNoteId);
    document.getElementById('notesCard').classList.add('active');
    document.getElementById('progress').classList.remove('active');

    // Questions dropdown caches itself on first load; clear it so the note
    // we just created shows up next time that panel opens.
    document.getElementById('questionSource').innerHTML = '';
  } catch(e){
    clearTimeout(stepTimer);
    showError(e.message || 'Something went wrong generating notes.');
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate notes";
  }
}

function wireExportLinks(noteId){
  // Scoped to the notes card so the questions card's ghost row can't shift the
  // indexes. Order in the HTML is: Export .md, Export .docx, Export .pdf
  const links = document.querySelectorAll('#notesCard .ghost-row a.ghost');
  links[0].href = `/api/export/${noteId}/markdown`;
  links[1].href = `/api/export/${noteId}/docx`;
  links[2].href = `/api/export/${noteId}/pdf`;
}

function copyNotes(btn){
  if(!lastMarkdown) return;
  navigator.clipboard.writeText(lastMarkdown);
  const original = btn.textContent;
  btn.textContent = "Copied";
  setTimeout(()=>{ btn.textContent = original; }, 1200);
}

function goChatWithNote(){
  setMode('chat', lastNoteId ? `new:note:${lastNoteId}` : 'new:general');
}

/* ---------------------------------------------------------------------------
   Chat

   Conversations are server-side sessions. The dropdown holds three kinds of
   option value:
     "new:general"      -> start a fresh general thread
     "new:note:<id>"    -> start a fresh thread scoped to one note
     "<session id>"     -> reopen an existing conversation
   A session isn't created until the first message is actually sent, so
   clicking around never litters the list with empty threads.
   --------------------------------------------------------------------------- */
let currentSessionId = null;   // null while a "new:" option is selected
let pendingScope = 'general';  // scope to create with, once the user sends

async function loadChatScopes(preferredSelection){
  const select = document.getElementById('chatScope');
  const previous = preferredSelection || select.value || 'new:general';
  select.innerHTML = '';

  const newGeneral = document.createElement('option');
  newGeneral.value = 'new:general';
  newGeneral.textContent = '＋ New general chat';
  select.appendChild(newGeneral);

  try {
    const [sesRes, libRes] = await Promise.all([
      fetch('/api/chat/sessions'),
      fetch('/api/library'),
    ]);
    const sessions = (await sesRes.json()).sessions || [];
    const notes = (await libRes.json()).items || [];

    if(notes.length){
      const group = document.createElement('optgroup');
      group.label = 'Start a chat about a note';
      notes.forEach(n => {
        const opt = document.createElement('option');
        opt.value = `new:note:${n.note_id}`;
        opt.textContent = `＋ ${shortLabel(n.title, 40)}`;
        opt.title = n.title;
        group.appendChild(opt);
      });
      select.appendChild(group);
    }

    if(sessions.length){
      const group = document.createElement('optgroup');
      group.label = 'Past conversations';
      sessions.forEach(s => {
        const opt = document.createElement('option');
        opt.value = String(s.id);
        opt.textContent = shortLabel(s.title);
        opt.title = s.title;
        group.appendChild(opt);
      });
      select.appendChild(group);
    }
  } catch(e){
    // The "new general chat" option alone still gives a working chat.
  }

  const exists = Array.from(select.options).some(o => o.value === previous);
  select.value = exists ? previous : 'new:general';

  await onScopeChange();
}

function clearChatMessages(){
  chatTranscript = [];
  document.querySelectorAll('#chatMessages .msg-wrap').forEach(el => el.remove());
}
/* Sets bubble content: assistant replies get Markdown-rendered (the model
   writes headings, bold, and bullets), user messages stay plain text. */
function setMessageContent(bubble, role, content){
  if(role === 'user'){
    bubble.textContent = content;
  } else {
    bubble.innerHTML = marked.parse(content || '');
  }
}

function appendChatMessage(role, content){
  const messages = document.getElementById('chatMessages');
  const wrap = document.createElement('div');
  wrap.className = `msg-wrap ${role === 'user' ? 'user' : 'assistant'}`;

  const bubble = document.createElement('div');
  bubble.className = `msg ${role === 'user' ? 'user' : 'assistant'}`;
  setMessageContent(bubble, role, content);

  wrap.appendChild(bubble);
  messages.appendChild(wrap);
  messages.scrollTop = messages.scrollHeight;

  bubble.wrap = wrap;
  bubble.rawText = content;

  if(role !== 'user' && content !== 'Thinking…'){
    chatTranscript.push({ role, content });
    appendCopyButton(bubble, content);
  } else if(role === 'user'){
    chatTranscript.push({ role, content });
  }

  return bubble;
}

/* Renders "from: <note titles>" chips under an assistant reply — one chip
   per distinct note that RAG retrieval actually pulled a passage from.
   Clicking one opens a fresh thread scoped to that note. */
function appendSourceChips(bubble, sources){
  if(!sources || !sources.length) return;
  const wrap = bubble.wrap || bubble.parentElement;
  const row = document.createElement('div');
  row.className = 'msg-sources';

  const label = document.createElement('span');
  label.className = 'msg-sources-label';
  label.textContent = 'from:';
  row.appendChild(label);

  sources.forEach(src => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'msg-source-chip';
    chip.textContent = src.title;
    chip.title = 'Start a chat about this note';
    chip.onclick = () => setMode('chat', `new:note:${src.note_id}`);
    row.appendChild(chip);
  });

  wrap.appendChild(row);
}

async function onScopeChange(){
  const value = document.getElementById('chatScope').value;
  const empty = document.getElementById('chatEmpty');
  const messages = document.getElementById('chatMessages');

  clearChatMessages();
  empty.style.display = 'block';

  if(value.startsWith('new:')){
    // Nothing to load — the session gets created when the first message sends.
    currentSessionId = null;
    pendingScope = value.slice(4);          // 'general' or 'note:3'
    empty.textContent = pendingScope === 'general'
      ? "New chat — ask anything you're studying."
      : 'New chat about this note — ask your first question.';
    return;
  }

  currentSessionId = Number(value);
  pendingScope = null;
  empty.textContent = 'Loading…';

  try {
    const res = await fetch(`/api/chat/history?session_id=${currentSessionId}`);
    const data = await res.json();
    const history = data.messages || [];

    if(history.length){
      empty.style.display = 'none';
      history.forEach(m => appendChatMessage(m.role, m.content));
    } else {
      empty.textContent = 'No messages yet — ask your first question.';
    }
  } catch(e){
    empty.textContent = 'Could not load this conversation.';
  }
  messages.scrollTop = messages.scrollHeight;
}

/* Starts a fresh thread. The previous conversation is kept — it stays in the
   dropdown under "Past conversations". */
function newChat(){
  const select = document.getElementById('chatScope');
  select.value = 'new:general';
  onScopeChange();
  document.getElementById('chatInput').focus();
}

async function sendChat(){
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if(!text) return;

  document.getElementById('chatEmpty').style.display = 'none';
  const messages = document.getElementById('chatMessages');
  const sendBtn = document.querySelector('.chat-input-row .send');

  appendChatMessage('user', text);
  input.value = '';

  const typing = appendChatMessage('assistant', 'Thinking…');
  typing.classList.add('typing');

  sendBtn.disabled = true;
  input.disabled = true;

  const wasNew = currentSessionId === null;

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: currentSessionId,
        scope: pendingScope || 'general',
        message: text,
        learning_mode: learningMode,
      }),
    });
    const data = await res.json().catch(() => null);

    if(!res.ok){
      throw new Error((data && data.detail) || `Request failed (${res.status}).`);
    }

    currentSessionId = data.session_id;
    pendingScope = null;

   typing.classList.remove('typing');
    setMessageContent(typing, 'assistant', data.reply);
    chatTranscript.push({ role: 'assistant', content: data.reply });
    appendCopyButton(typing, data.reply);
    appendSourceChips(typing, data.sources);

    // A brand-new thread now exists server-side and is titled after this
    // question, so rebuild the dropdown and keep it selected.
    if(wasNew) await loadChatScopes(String(currentSessionId));
  } catch(e){
    typing.classList.remove('typing');
    typing.textContent = `Couldn't get a reply: ${e.message || 'something went wrong.'}`;
  } finally {
    sendBtn.disabled = false;
    input.disabled = false;
    input.focus();
    messages.scrollTop = messages.scrollHeight;
  }
}

/* ---------------------------------------------------------------------------
   Practice questions
   --------------------------------------------------------------------------- */
let questionCount = 10;
let answersVisible = false;
let lastQuestions = [];
let lastQuestionSetId = null;

function setQuestionCount(n){
  questionCount = n;
  document.querySelectorAll('#countSwitch .learning-btn').forEach(b =>
    b.classList.toggle('active', Number(b.dataset.count) === n));
}

/* Always refetches — the library changes as notes are generated and deleted,
   and a stale list here means generating questions against a dead note_id. */
async function loadQuestionSources(){
  const select = document.getElementById('questionSource');
  const previous = select.value;
  select.innerHTML = '';
  select.disabled = true;

  let items = [];
  try {
    const res = await fetch('/api/library');
    if(!res.ok) throw new Error();
    items = (await res.json()).items || [];
  } catch(e){
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = 'Could not load your notes';
    select.appendChild(opt);
    return;
  }

  if(!items.length){
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = 'No notes yet — generate some first';
    select.appendChild(opt);
    return;
  }

  items.forEach(item => {
    const opt = document.createElement('option');
    opt.value = item.note_id;
    opt.textContent = `${shortLabel(item.title)} — ${item.type} notes`;
    opt.title = item.title;
    select.appendChild(opt);
  });

  if(Array.from(select.options).some(o => o.value === previous)){
    select.value = previous;
  }
  select.disabled = false;
}
async function generateQuestions(){
  const noteId = document.getElementById('questionSource').value;
  const btn = document.getElementById('questionsBtn');
  const err = document.getElementById('questionsError');
  err.classList.remove('active');

  if(!noteId){
    err.textContent = 'Generate notes for something first — questions are built from your notes.';
    err.classList.add('active');
    return;
  }

  btn.disabled = true;
  btn.textContent = `Writing ${questionCount} questions…`;
  document.getElementById('questionsCard').classList.remove('active');

  try {
    const res = await fetch('/api/generate-questions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        note_id: Number(noteId),
        count: questionCount,
        learning_mode: learningMode,
      }),
    });
    const data = await res.json().catch(() => null);
    if(!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status}).`);

    lastQuestions = data.questions;
    lastQuestionSetId = data.set_id;
    answersVisible = false;
    document.getElementById('answerToggle').textContent = 'Show answers';
    document.getElementById('questionsLabel').textContent =
      `${data.returned_count} questions · ${LEARNING_MODE_LABELS[data.learning_mode]} mode`;
    renderQuestions();
    document.getElementById('questionsCard').classList.add('active');
  } catch(e){
    err.textContent = e.message || 'Something went wrong.';
    err.classList.add('active');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generate questions';
  }
}

/* Model output is untrusted text, so build the DOM with textContent rather
   than innerHTML — a stray < in a question would otherwise break the page
   (or worse, inject markup). */
function renderQuestions(){
  const body = document.getElementById('questionsBody');
  body.innerHTML = '';

  lastQuestions.forEach((q, i) => {
    const item = document.createElement('div');
    item.className = 'question-item';

    const head = document.createElement('p');
    const num = document.createElement('strong');
    num.textContent = `${i + 1}.`;
    head.appendChild(num);
    head.append(` ${q.question} `);
    if(q.difficulty){
      const tag = document.createElement('code');
      tag.textContent = q.difficulty;
      head.appendChild(tag);
    }
    item.appendChild(head);

    if(q.options && q.options.length){
      const ul = document.createElement('ul');
      q.options.forEach(o => {
        const li = document.createElement('li');
        li.textContent = o;
        ul.appendChild(li);
      });
      item.appendChild(ul);
    }

    if(answersVisible){
      const ans = document.createElement('p');
      ans.className = 'question-answer';
      const lbl = document.createElement('strong');
      lbl.textContent = 'Answer: ';
      ans.appendChild(lbl);
      ans.append(q.answer || '—');
      if(q.explanation){
        ans.appendChild(document.createElement('br'));
        const exp = document.createElement('span');
        exp.className = 'explanation';
        exp.textContent = q.explanation;
        ans.appendChild(exp);
      }
      item.appendChild(ans);
    }

    body.appendChild(item);
  });
}

function toggleAnswers(){
  answersVisible = !answersVisible;
  document.getElementById('answerToggle').textContent =
    answersVisible ? 'Hide answers' : 'Show answers';
  renderQuestions();
}

/* Downloads the current set. What lands in the file follows the on-screen
   Show/Hide answers state — hidden means questions only. */
function downloadQuestions(fmt){
  if(!lastQuestionSetId) return;
  window.location.href =
    `/api/export/questions/${lastQuestionSetId}/${fmt}?answers=${answersVisible}`;
}

/* ---------------------------------------------------------------------------
   Library — browse, reopen, and delete anything generated in a past session.
   --------------------------------------------------------------------------- */
let openLibraryNoteId = null;

function formatNoteDate(iso){
  // generated_at is an ISO-8601 UTC string from db._now()
  const d = new Date(iso);
  if(isNaN(d)) return iso;
  return d.toLocaleDateString(undefined, { day:'numeric', month:'short', year:'numeric' });
}

async function loadLibrary(){
  const list = document.getElementById('libraryList');
  list.innerHTML = '<div class="library-empty">Loading…</div>';

  let items = [];
  try {
    const res = await fetch('/api/library');
    if(!res.ok) throw new Error();
    const data = await res.json();
    items = data.items || [];
  } catch(e){
    list.innerHTML = '<div class="library-empty">Could not load your library.</div>';
    return;
  }

  if(!items.length){
    list.innerHTML =
      '<div class="library-empty">Nothing here yet — generate some notes and they\'ll show up.</div>';
    return;
  }

  list.innerHTML = '';
  items.forEach(item => {
    const row = document.createElement('div');
    row.className = 'library-item';
    row.onclick = () => openLibraryNote(item.note_id);

    const main = document.createElement('div');
    main.className = 'li-main';

    const title = document.createElement('div');
    title.className = 'li-title';
    title.textContent = item.title;          // filenames and video titles: not ours
    title.title = item.title;

    const meta = document.createElement('div');
    meta.className = 'li-meta';
    meta.textContent =
      `${item.type} · ${item.num_chunks} chunk${item.num_chunks === 1 ? '' : 's'} · ` +
      `${item.learning_mode} · ${formatNoteDate(item.generated_at)}`;

    main.appendChild(title);
    main.appendChild(meta);

    const actions = document.createElement('div');
    actions.className = 'li-actions';

    const del = document.createElement('button');
    del.className = 'li-delete';
    del.textContent = 'Delete';
    del.onclick = (ev) => {
      ev.stopPropagation();               // don't also open the note
      deleteLibraryNote(item.note_id, item.title);
    };
    actions.appendChild(del);

    row.appendChild(main);
    row.appendChild(actions);
    list.appendChild(row);
  });
}

async function openLibraryNote(noteId){
  const card = document.getElementById('libraryNoteCard');
  const body = document.getElementById('libraryNoteBody');
  body.textContent = 'Loading…';
  card.classList.add('active');

  try {
    const res = await fetch(`/api/notes/${noteId}`);
    const data = await res.json().catch(() => null);
    if(!res.ok) throw new Error((data && data.detail) || 'Could not load that note.');

    openLibraryNoteId = noteId;
    const origin = ORIGIN_LABELS[data.transcript_origin] || data.transcript_origin;
    const mode = LEARNING_MODE_LABELS[data.learning_mode] || data.learning_mode;
    document.getElementById('libraryNoteLabel').textContent =
      `${data.num_chunks} chunk${data.num_chunks === 1 ? '' : 's'} · ${origin} · ${mode} mode`;

    body.innerHTML = marked.parse(data.structured_markdown);

    document.getElementById('libExportMd').href   = `/api/export/${noteId}/markdown`;
    document.getElementById('libExportDocx').href = `/api/export/${noteId}/docx`;
    document.getElementById('libExportPdf').href  = `/api/export/${noteId}/pdf`;

    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch(e){
    body.textContent = e.message || 'Could not load that note.';
  }
}

function closeLibraryNote(){
  openLibraryNoteId = null;
  document.getElementById('libraryNoteCard').classList.remove('active');
}

function askAboutLibraryNote(){
  if(openLibraryNoteId) setMode('chat', `new:note:${openLibraryNoteId}`);
}

async function deleteLibraryNote(noteId, title){
  const short = shortLabel(title, 50);
  if(!confirm(`Delete "${short}"?\n\nIts notes, question sets, and chat history go with it. This can't be undone.`)){
    return;
  }

  try {
    const res = await fetch(`/api/notes/${noteId}`, { method: 'DELETE' });
    if(!res.ok) throw new Error(`Request failed (${res.status}).`);

    if(openLibraryNoteId === noteId) closeLibraryNote();

    // Both dropdowns can now be offering a note that no longer exists.
    document.getElementById('questionSource').innerHTML = '';
    if(currentSessionId === null) pendingScope = 'general';

    await loadLibrary();
  } catch(e){
    alert("Couldn't delete that note.");
  }
}
/* Copies text and flashes confirmation on the button that triggered it.
   Falls back to a textarea + execCommand on http:// origins, where the
   async clipboard API is unavailable outside a secure context. */
async function copyText(text, btn, label = 'Copied'){
  try {
    await navigator.clipboard.writeText(text);
  } catch(e){
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } finally { ta.remove(); }
  }
  if(!btn) return;
  const original = btn.textContent;
  btn.textContent = label;
  btn.classList.add('copied');
  setTimeout(() => {
    btn.textContent = original;
    btn.classList.remove('copied');
  }, 1200);
}

/* Attaches a copy button under one message. Copies the raw text the model
   sent, not the rendered HTML — so Markdown stays intact when pasted into
   a notes app. */
   let chatTranscript = [];
function appendCopyButton(bubble, rawText){
  const wrap = bubble.wrap || bubble.parentElement;
  let row = wrap.querySelector('.msg-actions');
  if(!row){
    row = document.createElement('div');
    row.className = 'msg-actions';
    wrap.appendChild(row);
  }
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'msg-copy';
  btn.textContent = 'Copy';
  btn.title = 'Copy this reply';
  btn.onclick = () => copyText(rawText, btn);
  row.appendChild(btn);
}

/* Copies the whole visible thread as Markdown, with speaker labels. */
function copyConversation(btn){
  if(!chatTranscript.length){
    return copyText('', btn, 'Nothing yet');
  }
  const text = chatTranscript
    .map(m => `**${m.role === 'user' ? 'You' : 'Notewell'}:**\n\n${m.content}`)
    .join('\n\n---\n\n');
  copyText(text, btn, 'Copied thread');
}
/* ---------------------------------------------------------------------------
   Bootstrap
   --------------------------------------------------------------------------- */
document.addEventListener('DOMContentLoaded', initLearningMode);