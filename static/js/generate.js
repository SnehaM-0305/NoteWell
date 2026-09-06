/* ===========================================================================
   Notes generation — the Generate tab's form, plus the async video-job
   polling/streaming pipeline (Path B). Kept in one file since they're a
   single workflow: generateNotes() is the entry point for every source
   type, and for video sources it hands off straight into
   pollGenerationJob()/showFinishedNote() below.
   =========================================================================== */

import { state, ORIGIN_LABELS, LEARNING_MODE_LABELS } from './state.js';
import { formatEta } from './utils.js';
import { renderVideoTimeline, loadAndRenderSections } from './videoTimeline.js';
import { loadAndRenderEditableSections } from './sectionEdit.js';
import { setMode } from './navigation.js';

/* ---------------------------------------------------------------------------
   Form state / step indicator helpers
   --------------------------------------------------------------------------- */

export function setStep(id, stepState) {
  const el = document.getElementById(id);
  el.classList.remove('active', 'done');
  if (stepState) el.classList.add(stepState);
}

export function resetUI() {
  document.getElementById('progress').classList.add('active');
  document.getElementById('errorBox').classList.remove('active');
  document.getElementById('notesCard').classList.remove('active');
  renderVideoTimeline('notesVideoEmbed', null, []);
  document.getElementById('progressDetail').textContent = '';
  setStep('step1', ''); setStep('step2', ''); setStep('step3', '');
}

export function showError(msg) {
  document.getElementById('progress').classList.remove('active');
  const box = document.getElementById('errorBox');
  box.textContent = msg;
  box.classList.add('active');
}

/* Builds the {url, options} for whichever source tab is active, or throws a
   plain Error with a user-facing message if that source isn't ready yet. */
function buildGenerateRequest() {
  if (state.currentSource === 'video') {
    const videoUrl = document.getElementById('videoInput').value.trim();
    if (!videoUrl) throw new Error('Paste a YouTube link first.');

    const startTime = document.getElementById('videoStartTime').value.trim();
    const endTime = document.getElementById('videoEndTime').value.trim();
    // Require both or neither: the backend only filters when BOTH are given —
    // sending just one would silently be ignored server-side (no error, but
    // also no range applied), which is a confusing "why didn't my start time
    // do anything" bug. Catching it here gives a clear message instead.
    if (startTime && !endTime) throw new Error('Give an end time too, or leave both blank for the whole video.');
    if (endTime && !startTime) throw new Error('Give a start time too, or leave both blank for the whole video.');

    const body = { video_url: videoUrl, learning_mode: state.learningMode };
    if (startTime) body.start_time = startTime;
    if (endTime) body.end_time = endTime;

    return {
      url: '/api/generate-notes',
      options: {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    };
  }

  if (state.currentSource === 'text') {
    const text = document.getElementById('textInput').value.trim();
    if (!text) throw new Error('Paste some text first.');
    return {
      url: '/api/generate-notes/text',
      options: {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, learning_mode: state.learningMode }),
      },
    };
  }

  if (state.currentSource === 'pdf' || state.currentSource === 'docx' || state.currentSource === 'audio') {
    const file = state.selectedFiles[state.currentSource];
    if (!file) {
      const label = state.currentSource === 'audio' ? 'an audio file' : `a .${state.currentSource} file`;
      throw new Error(`Choose ${label} first.`);
    }
    const formData = new FormData();
    formData.append('file', file);
    formData.append('learning_mode', state.learningMode);
    return {
      url: `/api/generate-notes/${state.currentSource}`,
      options: { method: 'POST', body: formData },
    };
  }

  throw new Error('Unknown source type.');
}

export async function generateNotes() {
  const btn = document.getElementById('generateBtn');

  let request;
  try {
    request = buildGenerateRequest();
  } catch (validationError) {
    showError(validationError.message);
    return;
  }

  resetUI();
  btn.disabled = true;
  btn.textContent = "Working…";
  setStep('step1', 'active');

  const isVideoJob = state.currentSource === 'video';

  // Non-video sources are still one synchronous request/response, unchanged
  // from before -- only the video path is job-based now.
  const stepTimer = isVideoJob ? null : setTimeout(() => {
    setStep('step1', 'done'); setStep('step2', 'active');
  }, 1200);

  try {
    const res = await fetch(request.url, request.options);
    const data = await res.json().catch(() => null);

    if (!res.ok) {
      throw new Error((data && data.detail) || `Request failed (${res.status}).`);
    }

    if (isVideoJob) {
      const job = await pollGenerationJob(data.job_id);
      await showFinishedNote(job.note_id);
    } else {
      clearTimeout(stepTimer);
      setStep('step1', 'done'); setStep('step2', 'done'); setStep('step3', 'done');
      state.lastMarkdown = data.notes_markdown;
      state.lastNoteId = data.note_id;
      renderNoteMeta(data);
      wireExportLinks(state.lastNoteId);
      loadAndRenderSections('notesVideoEmbed', state.lastNoteId, data.video_id);
      loadAndRenderEditableSections('notesBody', state.lastNoteId, state.lastMarkdown);
      document.getElementById('notesCard').classList.add('active');
      document.getElementById('progress').classList.remove('active');
    }

    document.getElementById('questionSource').innerHTML = '';
  } catch (e) {
    if (stepTimer) clearTimeout(stepTimer);
    document.getElementById('notesCard').classList.remove('active');
    showError(e.message || 'Something went wrong generating notes.');
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate notes";
  }
}

export function wireExportLinks(noteId) {
  // Scoped to the notes card so the questions card's ghost row can't shift the
  // indexes. Order in the HTML is: Export .md, Export .docx, Export .pdf
  const links = document.querySelectorAll('#notesCard .ghost-row a.ghost');
  links[0].href = `/api/export/${noteId}/markdown`;
  links[1].href = `/api/export/${noteId}/docx`;
  links[2].href = `/api/export/${noteId}/pdf`;
}

export function copyNotes(btn) {
  if (!state.lastMarkdown) return;
  navigator.clipboard.writeText(state.lastMarkdown);
  const original = btn.textContent;
  btn.textContent = "Copied";
  setTimeout(() => { btn.textContent = original; }, 1200);
}

export function goChatWithNote() {
  setMode('chat', state.lastNoteId ? `new:note:${state.lastNoteId}` : 'new:general');
}

/* ---------------------------------------------------------------------------
   Async video job polling (Path B) -- video generation returns a job_id
   instead of the finished note; this polls, renders streamed sections as
   they arrive, and shows live stage + ETA.
   --------------------------------------------------------------------------- */

const JOB_STAGE_LABELS = {
  transcribing: 'Transcribing audio',
  writing_sections: 'Writing notes section by section',
  finalizing: 'Finalizing your notes',
  done: 'Done',
};

function renderJobProgress(job) {
  setStep('step1', job.stage === 'transcribing' ? 'active' : 'done');
  setStep('step2',
    job.stage === 'writing_sections' ? 'active' :
      (job.stage === 'finalizing' || job.stage === 'done' ? 'done' : '')
  );
  setStep('step3', job.stage === 'finalizing' ? 'active' : (job.stage === 'done' ? 'done' : ''));

  let detail = JOB_STAGE_LABELS[job.stage] || job.stage;
  if (job.stage === 'writing_sections' && job.total_chunks) {
    detail += ` (${job.completed_chunks || 0}/${job.total_chunks})`;
  }
  const eta = formatEta(job.estimated_seconds_remaining);
  document.getElementById('progressDetail').textContent = eta ? `${detail} · ${eta}` : detail;
}

/* Renders whatever sections have streamed in so far -- real content, not a
   percentage bar. Re-parses the whole joined markdown each poll; cheap
   enough at this size, avoids incremental-DOM-diff complexity. Streamed
   sections are plain (not edit/regenerate-enabled) -- the job isn't done
   yet, so there's nothing durable to edit until showFinishedNote() takes
   over with the real, saved note_id. */
function renderStreamedSections(sections) {
  if (!sections || !sections.length) return;
  const combined = sections.map(s => s.markdown_text).join('\n\n');
  document.getElementById('notesBody').innerHTML = marked.parse(combined);
  document.getElementById('chunkLabel').textContent =
    `Writing notes… ${sections.length} section${sections.length === 1 ? '' : 's'} so far`;
}

function pollGenerationJob(jobId) {
  const POLL_INTERVAL_MS = 2000;
  document.getElementById('notesCard').classList.add('active');   // show it now -- sections stream in below

  return new Promise((resolve, reject) => {
    const poll = async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}`);
        const job = await res.json().catch(() => null);
        if (!res.ok) {
          reject(new Error((job && job.detail) || `Job lookup failed (${res.status}).`));
          return;
        }

        renderJobProgress(job);

        if (job.status === 'failed') {
          reject(new Error(job.error || 'Note generation failed.'));
          return;
        }
        if (job.status === 'done') {
          resolve(job);
          return;
        }

        renderStreamedSections(job.sections);
        setTimeout(poll, POLL_INTERVAL_MS);
      } catch (e) {
        reject(e);
      }
    };
    poll();
  });
}

function renderNoteMeta(data) {
  const originLabel = ORIGIN_LABELS[data.transcript_origin] || data.transcript_origin;
  const modeLabel = LEARNING_MODE_LABELS[data.learning_mode] || 'Medium';
  document.getElementById('chunkLabel').textContent =
    `${data.num_chunks} chunk${data.num_chunks === 1 ? '' : 's'} · ${originLabel} · ${modeLabel} mode`;
}

/* Once a video job finishes, the polished final note (with opening/closing
   sections the streamed job.sections never included) lives in the DB, not
   in the job response -- fetch it the same way the Library panel does. */
async function showFinishedNote(noteId) {
  const res = await fetch(`/api/notes/${noteId}`);
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && data.detail) || 'Could not load the finished note.');

  state.lastMarkdown = data.structured_markdown;
  state.lastNoteId = noteId;
  renderNoteMeta(data);
  wireExportLinks(noteId);
  loadAndRenderSections('notesVideoEmbed', noteId, data.video_id);
  loadAndRenderEditableSections('notesBody', noteId, state.lastMarkdown);
  document.getElementById('notesCard').classList.add('active');
  document.getElementById('progress').classList.remove('active');
}
