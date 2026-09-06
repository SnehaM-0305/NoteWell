/* ===========================================================================
   Small, generic helpers with no dependency on shared app state — used by
   several feature modules. Nothing in here should ever need to import from
   state.js or any feature file; if it does, it belongs somewhere else.
   =========================================================================== */

/* Truncate long note/chat titles for dropdowns, keeping the full text as a
   tooltip. Native <select> clamps option text to the control width, so this
   at least makes the cut-off deliberate. */
export function shortLabel(text, max = 45) {
  return text.length > max ? text.slice(0, max) + '…' : text;
}

/* Copies text and flashes confirmation on the button that triggered it.
   Falls back to a textarea + execCommand on http:// origins, where the
   async clipboard API is unavailable outside a secure context. */
export async function copyText(text, btn, label = 'Copied') {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } finally { ta.remove(); }
  }
  if (!btn) return;
  const original = btn.textContent;
  btn.textContent = label;
  btn.classList.add('copied');
  setTimeout(() => {
    btn.textContent = original;
    btn.classList.remove('copied');
  }, 1200);
}

export function formatNoteDate(iso) {
  // generated_at is an ISO-8601 UTC string from db._now()
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

export function formatEta(seconds) {
  if (seconds == null) return '';
  if (seconds < 60) return `~${Math.max(1, Math.round(seconds))}s left`;
  return `~${Math.round(seconds / 60)} min left`;
}
