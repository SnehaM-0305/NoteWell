/* ===========================================================================
   Practice questions — no dependency on any other feature module. State
   here (questionCount, answersVisible, lastQuestions, lastQuestionSetId) is
   only ever touched by these functions, so it stays local instead of
   living in the shared state.js object.
   =========================================================================== */

import { state, LEARNING_MODE_LABELS } from './state.js';
import { shortLabel } from './utils.js';

let questionCount = 10;
let answersVisible = false;
let lastQuestions = [];
let lastQuestionSetId = null;

export function setQuestionCount(n) {
  questionCount = n;
  document.querySelectorAll('#countSwitch .learning-btn').forEach(b =>
    b.classList.toggle('active', Number(b.dataset.count) === n));
}

/* Always refetches — the library changes as notes are generated and deleted,
   and a stale list here means generating questions against a dead note_id. */
export async function loadQuestionSources() {
  const select = document.getElementById('questionSource');
  const previous = select.value;
  select.innerHTML = '';
  select.disabled = true;

  let items = [];
  try {
    const res = await fetch('/api/library');
    if (!res.ok) throw new Error();
    items = (await res.json()).items || [];
  } catch (e) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = 'Could not load your notes';
    select.appendChild(opt);
    return;
  }

  if (!items.length) {
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

  if (Array.from(select.options).some(o => o.value === previous)) {
    select.value = previous;
  }
  select.disabled = false;
}

export async function generateQuestions() {
  const noteId = document.getElementById('questionSource').value;
  const btn = document.getElementById('questionsBtn');
  const err = document.getElementById('questionsError');
  err.classList.remove('active');

  if (!noteId) {
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
        learning_mode: state.learningMode,
      }),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status}).`);

    lastQuestions = data.questions;
    lastQuestionSetId = data.set_id;
    answersVisible = false;
    document.getElementById('answerToggle').textContent = 'Show answers';
    document.getElementById('questionsLabel').textContent =
      `${data.returned_count} questions · ${LEARNING_MODE_LABELS[data.learning_mode]} mode`;
    renderQuestions();
    document.getElementById('questionsCard').classList.add('active');
  } catch (e) {
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
function renderQuestions() {
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
    if (q.difficulty) {
      const tag = document.createElement('code');
      tag.textContent = q.difficulty;
      head.appendChild(tag);
    }
    item.appendChild(head);

    if (q.options && q.options.length) {
      const ul = document.createElement('ul');
      q.options.forEach(o => {
        const li = document.createElement('li');
        li.textContent = o;
        ul.appendChild(li);
      });
      item.appendChild(ul);
    }

    if (answersVisible) {
      const ans = document.createElement('p');
      ans.className = 'question-answer';
      const lbl = document.createElement('strong');
      lbl.textContent = 'Answer: ';
      ans.appendChild(lbl);
      ans.append(q.answer || '—');
      if (q.explanation) {
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

export function toggleAnswers() {
  answersVisible = !answersVisible;
  document.getElementById('answerToggle').textContent =
    answersVisible ? 'Hide answers' : 'Show answers';
  renderQuestions();
}

/* Downloads the current set. What lands in the file follows the on-screen
   Show/Hide answers state — hidden means questions only. */
export function downloadQuestions(fmt) {
  if (!lastQuestionSetId) return;
  window.location.href =
    `/api/export/questions/${lastQuestionSetId}/${fmt}?answers=${answersVisible}`;
}
