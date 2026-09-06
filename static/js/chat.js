/* ===========================================================================
   Chat

   Conversations are server-side sessions. The dropdown holds three kinds of
   option value:
     "new:general"      -> start a fresh general thread
     "new:note:<id>"    -> start a fresh thread scoped to one note
     "<session id>"     -> reopen an existing conversation
   A session isn't created until the first message is actually sent, so
   clicking around never litters the list with empty threads.

   NOTE ON IMPORTS: this file imports setMode from navigation.js (a source
   chip needs to open a fresh chat scoped to that note); navigation.js
   imports loadChatScopes FROM here (switching to the Chat tab needs to
   refresh it). See navigation.js's top comment for why this circular
   import is safe.
   =========================================================================== */

import { state } from './state.js';
import { shortLabel, copyText } from './utils.js';
import { setMode } from './navigation.js';

let chatTranscript = [];   // only ever read/written from within this file

export async function loadChatScopes(preferredSelection) {
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

    if (notes.length) {
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

    if (sessions.length) {
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
  } catch (e) {
    // The "new general chat" option alone still gives a working chat.
  }

  const exists = Array.from(select.options).some(o => o.value === previous);
  select.value = exists ? previous : 'new:general';

  await onScopeChange();
}

function clearChatMessages() {
  chatTranscript = [];
  document.querySelectorAll('#chatMessages .msg-wrap').forEach(el => el.remove());
}

/* Sets bubble content: assistant replies get Markdown-rendered (the model
   writes headings, bold, and bullets), user messages stay plain text. */
function setMessageContent(bubble, role, content) {
  if (role === 'user') {
    bubble.textContent = content;
  } else {
    bubble.innerHTML = marked.parse(content || '');
  }
}

function appendChatMessage(role, content) {
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

  if (role !== 'user' && content !== 'Thinking…') {
    chatTranscript.push({ role, content });
    appendCopyButton(bubble, content);
  } else if (role === 'user') {
    chatTranscript.push({ role, content });
  }

  return bubble;
}

/* Renders "from: <note titles>" chips under an assistant reply — one chip
   per distinct note that RAG retrieval actually pulled a passage from.
   Clicking one opens a fresh thread scoped to that note. */
function appendSourceChips(bubble, sources) {
  if (!sources || !sources.length) return;
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

export async function onScopeChange() {
  const value = document.getElementById('chatScope').value;
  const empty = document.getElementById('chatEmpty');
  const messages = document.getElementById('chatMessages');

  clearChatMessages();
  empty.style.display = 'block';

  if (value.startsWith('new:')) {
    // Nothing to load — the session gets created when the first message sends.
    state.currentSessionId = null;
    state.pendingScope = value.slice(4);          // 'general' or 'note:3'
    empty.textContent = state.pendingScope === 'general'
      ? "New chat — ask anything you're studying."
      : 'New chat about this note — ask your first question.';
    return;
  }

  state.currentSessionId = Number(value);
  state.pendingScope = null;
  empty.textContent = 'Loading…';

  try {
    const res = await fetch(`/api/chat/history?session_id=${state.currentSessionId}`);
    const data = await res.json();
    const history = data.messages || [];

    if (history.length) {
      empty.style.display = 'none';
      history.forEach(m => appendChatMessage(m.role, m.content));
    } else {
      empty.textContent = 'No messages yet — ask your first question.';
    }
  } catch (e) {
    empty.textContent = 'Could not load this conversation.';
  }
  messages.scrollTop = messages.scrollHeight;
}

/* Starts a fresh thread. The previous conversation is kept — it stays in the
   dropdown under "Past conversations". */
export function newChat() {
  const select = document.getElementById('chatScope');
  select.value = 'new:general';
  onScopeChange();
  document.getElementById('chatInput').focus();
}

export async function sendChat() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text) return;

  document.getElementById('chatEmpty').style.display = 'none';
  const messages = document.getElementById('chatMessages');
  const sendBtn = document.querySelector('.chat-input-row .send');

  appendChatMessage('user', text);
  input.value = '';

  const typing = appendChatMessage('assistant', 'Thinking…');
  typing.classList.add('typing');

  sendBtn.disabled = true;
  input.disabled = true;

  const wasNew = state.currentSessionId === null;

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.currentSessionId,
        scope: state.pendingScope || 'general',
        message: text,
        learning_mode: state.learningMode,
      }),
    });
    const data = await res.json().catch(() => null);

    if (!res.ok) {
      throw new Error((data && data.detail) || `Request failed (${res.status}).`);
    }

    state.currentSessionId = data.session_id;
    state.pendingScope = null;

    typing.classList.remove('typing');
    setMessageContent(typing, 'assistant', data.reply);
    chatTranscript.push({ role: 'assistant', content: data.reply });
    appendCopyButton(typing, data.reply);
    appendSourceChips(typing, data.sources);

    // A brand-new thread now exists server-side and is titled after this
    // question, so rebuild the dropdown and keep it selected.
    if (wasNew) await loadChatScopes(String(state.currentSessionId));
  } catch (e) {
    typing.classList.remove('typing');
    typing.textContent = `Couldn't get a reply: ${e.message || 'something went wrong.'}`;
  } finally {
    sendBtn.disabled = false;
    input.disabled = false;
    input.focus();
    messages.scrollTop = messages.scrollHeight;
  }
}

/* Attaches a copy button under one message. Copies the raw text the model
   sent, not the rendered HTML — so Markdown stays intact when pasted into
   a notes app. */
function appendCopyButton(bubble, rawText) {
  const wrap = bubble.wrap || bubble.parentElement;
  let row = wrap.querySelector('.msg-actions');
  if (!row) {
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
export function copyConversation(btn) {
  if (!chatTranscript.length) {
    return copyText('', btn, 'Nothing yet');
  }
  const text = chatTranscript
    .map(m => `**${m.role === 'user' ? 'You' : 'Notewell'}:**\n\n${m.content}`)
    .join('\n\n---\n\n');
  copyText(text, btn, 'Copied thread');
}
