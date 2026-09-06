/* ===========================================================================
   Video timeline — clickable timestamp chips that open the video in a new
   tab, already seeked via YouTube's own ?t= parameter. No embedded player,
   no iframe API, no ad-blocker fragility, no player lifecycle to manage.
   Used by both the freshly-generated notes card and the reopened library
   note card, via the shared renderVideoTimeline()/loadAndRenderSections()
   pair below (containerId differs, everything else is identical).
   =========================================================================== */

export function formatChipTime(seconds) {
  seconds = Math.round(seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m);
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/* Renders the clickable chip row for one note. videoId=null, or a video
   with no timed sections, both just clear/hide the container -- there's
   nothing else to render without an embedded player. */
export function renderVideoTimeline(containerId, videoId, sections) {
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!videoId || !sections || !sections.length) {
    container.innerHTML = '';
    container.style.display = 'none';
    return;
  }

  container.style.display = 'block';
  container.innerHTML = `<div class="section-chips" id="${containerId}-chips"></div>`;
  const chipsRow = document.getElementById(`${containerId}-chips`);

  sections.forEach(s => {
    const chip = document.createElement('a');
    chip.className = 'section-chip';
    chip.href = `https://www.youtube.com/watch?v=${videoId}&t=${Math.round(s.start_seconds)}s`;
    chip.target = '_blank';
    chip.rel = 'noopener noreferrer';
    chip.textContent = `${formatChipTime(s.start_seconds)} · ${s.heading}`;
    chipsRow.appendChild(chip);
  });
}

export async function loadAndRenderSections(containerId, noteId, videoId) {
  if (!videoId) { renderVideoTimeline(containerId, null, []); return; }
  try {
    const res = await fetch(`/api/notes/${noteId}/sections`);
    const data = await res.json();
    renderVideoTimeline(containerId, videoId, data.sections || []);
  } catch (e) {
    renderVideoTimeline(containerId, null, []);
  }
}
