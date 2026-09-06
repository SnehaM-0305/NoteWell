/* ===========================================================================
   Section-level edit & regenerate (Phase 2)

   Renders a finished note as one wrapped <div class="note-section"> per
   H1/H2 boundary (from GET /api/notes/{id}/editable-sections), each with a
   hover "✏️ Edit" / "🔄 Regenerate" affordance. Both actions replace ONLY
   that section's rendered HTML on success -- no full-note re-render, no
   full-page reload, and the rest of the note is provably untouched because
   the backend only ever splices one section back in.

   Shared by all three places a finished note gets rendered: fresh
   generation (generate.js's non-video branch), a finished video job
   (showFinishedNote in generate.js), and reopening a saved note
   (library.js's openLibraryNote) -- same function, different
   bodyElementId/noteId, exactly like videoTimeline.js's pair.

   Depends on `state` only for one thing: keeping `lastMarkdown` (used by
   the Generate tab's "Copy Markdown" button) in sync after an edit to the
   note that's currently open there. Everything else here is self-contained.
   =========================================================================== */

import { state } from './state.js';

export async function loadAndRenderEditableSections(bodyElementId, noteId, fallbackMarkdown) {
  const body = document.getElementById(bodyElementId);
  if (!body) return;
  try {
    const res = await fetch(`/api/notes/${noteId}/editable-sections`);
    const data = await res.json();
    const sections = data.sections || [];
    if (!sections.length) {
      // No H1/H2 boundaries found (shouldn't normally happen -- every mode
      // skeleton produces at least one heading) -- fall back to a single
      // plain render so the note is never just blank.
      body.innerHTML = `<div class="note-section-static">${marked.parse(fallbackMarkdown || '')}</div>`;
      return;
    }
    renderEditableSections(bodyElementId, noteId, sections);
  } catch (e) {
    body.innerHTML = `<div class="note-section-static">${marked.parse(fallbackMarkdown || '')}</div>`;
  }
}

export function renderEditableSections(bodyElementId, noteId, sections) {
  const body = document.getElementById(bodyElementId);
  body.innerHTML = '';

  sections.forEach(sec => {
    const wrap = document.createElement('div');
    wrap.className = 'note-section';

    const content = document.createElement('div');
    content.className = 'section-content';
    content.innerHTML = marked.parse(sec.markdown_text);
    wrap.appendChild(content);

    const actions = document.createElement('div');
    actions.className = 'section-actions';

    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'section-action-btn';
    editBtn.textContent = '✏️ Edit';
    editBtn.onclick = () => startEditSection(noteId, wrap, sec);
    actions.appendChild(editBtn);

    const regenBtn = document.createElement('button');
    regenBtn.type = 'button';
    regenBtn.className = 'section-action-btn';
    regenBtn.textContent = '🔄 Regenerate';
    regenBtn.onclick = () => startRegenSection(noteId, wrap, sec);
    actions.appendChild(regenBtn);

    wrap.appendChild(actions);
    body.appendChild(wrap);
  });
}

/* Tears down whatever editor row is open on a section and re-renders its
   (possibly just-updated) markdown_text, restoring the normal hover state.
   Not exported -- only used by the two start*Section() functions below. */
function _restoreSectionView(wrap, sec) {
  const editorRow = wrap.querySelector('.section-editor-row');
  if (editorRow) editorRow.remove();
  wrap.querySelector('.section-content').innerHTML = marked.parse(sec.markdown_text);
  wrap.querySelector('.section-actions').style.display = '';
}

/* If the note currently open in the Generate tab is the one that just got
   edited, refresh lastMarkdown too -- otherwise Copy Markdown would keep
   copying stale pre-edit text even though the screen shows the new text.
   (Export buttons don't need this: they hit /api/export/{id}/..., which
   reads straight from the DB, so they're already correct.) */
function _syncNoteMarkdownIfCurrent(noteId, notesMarkdown) {
  if (noteId === state.lastNoteId) state.lastMarkdown = notesMarkdown;
}

function startEditSection(noteId, wrap, sec) {
  wrap.querySelector('.section-content').style.display = 'none';
  wrap.querySelector('.section-actions').style.display = 'none';

  const row = document.createElement('div');
  row.className = 'section-editor-row';

  const textarea = document.createElement('textarea');
  textarea.className = 'section-editor';
  textarea.value = sec.markdown_text;
  row.appendChild(textarea);

  const btnRow = document.createElement('div');
  btnRow.className = 'section-editor-actions';

  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'ghost';
  saveBtn.textContent = 'Save';
  saveBtn.onclick = async () => {
    const newText = textarea.value.trim();
    if (!newText) { alert("Section text can't be empty."); return; }

    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';
    try {
      const res = await fetch(`/api/notes/${noteId}/sections/${sec.index}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ markdown_text: newText }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status}).`);

      sec.markdown_text = data.markdown_text;
      _syncNoteMarkdownIfCurrent(noteId, data.notes_markdown);
      wrap.querySelector('.section-content').style.display = '';
      _restoreSectionView(wrap, sec);
    } catch (e) {
      alert(e.message || "Couldn't save that edit.");
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save';
    }
  };
  btnRow.appendChild(saveBtn);

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'ghost';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.onclick = () => {
    wrap.querySelector('.section-content').style.display = '';
    _restoreSectionView(wrap, sec);
  };
  btnRow.appendChild(cancelBtn);

  row.appendChild(textarea);
  row.appendChild(btnRow);
  wrap.appendChild(row);
}

function startRegenSection(noteId, wrap, sec) {
  wrap.querySelector('.section-actions').style.display = 'none';

  const row = document.createElement('div');
  row.className = 'section-editor-row';

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'section-regen-input';
  input.placeholder = 'Optional instruction — e.g. "make this shorter" or "add an example"';
  row.appendChild(input);

  const btnRow = document.createElement('div');
  btnRow.className = 'section-editor-actions';

  const goBtn = document.createElement('button');
  goBtn.type = 'button';
  goBtn.className = 'ghost';
  goBtn.textContent = 'Regenerate';
  goBtn.onclick = async () => {
    goBtn.disabled = true;
    goBtn.textContent = 'Regenerating…';
    try {
      const res = await fetch(`/api/notes/${noteId}/sections/${sec.index}/regenerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction: input.value.trim() || null }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status}).`);

      sec.markdown_text = data.markdown_text;
      _syncNoteMarkdownIfCurrent(noteId, data.notes_markdown);
      row.remove();
      _restoreSectionView(wrap, sec);
    } catch (e) {
      alert(e.message || "Couldn't regenerate that section.");
      goBtn.disabled = false;
      goBtn.textContent = 'Regenerate';
    }
  };
  btnRow.appendChild(goBtn);

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'ghost';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.onclick = () => {
    row.remove();
    wrap.querySelector('.section-actions').style.display = '';
  };
  btnRow.appendChild(cancelBtn);

  row.appendChild(btnRow);
  wrap.appendChild(row);
}
