/* ===========================================================================
   Library — browse, reopen, and delete anything generated in a past
   session.

   NOTE ON IMPORTS: this file imports setMode from navigation.js
   ("Ask a question" on a library note needs to switch to the Chat tab);
   navigation.js imports loadLibrary FROM here (switching to the Library
   tab needs to refresh it). See navigation.js's top comment for why this
   circular import is safe.
   =========================================================================== */

import { state } from './state.js';
import { shortLabel, formatNoteDate } from './utils.js';
import { ORIGIN_LABELS, LEARNING_MODE_LABELS } from './state.js';
import { loadAndRenderSections } from './videoTimeline.js';
import { loadAndRenderEditableSections } from './sectionEdit.js';
import { setMode } from './navigation.js';

let openLibraryNoteId = null;   // only ever read/written from within this file

export async function loadLibrary() {
  const list = document.getElementById('libraryList');
  list.innerHTML = '<div class="library-empty">Loading…</div>';

  let items = [];
  try {
    const res = await fetch('/api/library');
    if (!res.ok) throw new Error();
    const data = await res.json();
    items = data.items || [];
  } catch (e) {
    list.innerHTML = '<div class="library-empty">Could not load your library.</div>';
    return;
  }

  if (!items.length) {
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

async function openLibraryNote(noteId) {
  const card = document.getElementById('libraryNoteCard');
  const body = document.getElementById('libraryNoteBody');
  body.textContent = 'Loading…';
  card.classList.add('active');

  try {
    const res = await fetch(`/api/notes/${noteId}`);
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error((data && data.detail) || 'Could not load that note.');

    openLibraryNoteId = noteId;
    const origin = ORIGIN_LABELS[data.transcript_origin] || data.transcript_origin;
    const mode = LEARNING_MODE_LABELS[data.learning_mode] || data.learning_mode;
    document.getElementById('libraryNoteLabel').textContent =
      `${data.num_chunks} chunk${data.num_chunks === 1 ? '' : 's'} · ${origin} · ${mode} mode`;

    loadAndRenderSections('libraryVideoEmbed', noteId, data.video_id);
    loadAndRenderEditableSections('libraryNoteBody', noteId, data.structured_markdown);

    document.getElementById('libExportMd').href = `/api/export/${noteId}/markdown`;
    document.getElementById('libExportDocx').href = `/api/export/${noteId}/docx`;
    document.getElementById('libExportPdf').href = `/api/export/${noteId}/pdf`;

    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    body.textContent = e.message || 'Could not load that note.';
  }
}

export function closeLibraryNote() {
  openLibraryNoteId = null;
  document.getElementById('libraryNoteCard').classList.remove('active');
}

export function askAboutLibraryNote() {
  if (openLibraryNoteId) setMode('chat', `new:note:${openLibraryNoteId}`);
}

async function deleteLibraryNote(noteId, title) {
  const short = shortLabel(title, 50);
  if (!confirm(`Delete "${short}"?\n\nIts notes, question sets, and chat history go with it. This can't be undone.`)) {
    return;
  }

  try {
    const res = await fetch(`/api/notes/${noteId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`Request failed (${res.status}).`);

    if (openLibraryNoteId === noteId) closeLibraryNote();

    // Both dropdowns can now be offering a note that no longer exists.
    document.getElementById('questionSource').innerHTML = '';
    if (state.currentSessionId === null) state.pendingScope = 'general';

    await loadLibrary();
  } catch (e) {
    alert("Couldn't delete that note.");
  }
}
