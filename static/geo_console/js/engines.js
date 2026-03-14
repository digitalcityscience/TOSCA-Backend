function getCsrfToken() {
  return document.cookie.split('; ')
    .find(r => r.startsWith('csrftoken='))
    ?.split('=')[1];
}

/* ── DOM helpers ─────────────────────────────────────────────────── */
function setSyncBadge(engineId, cssClass, text) {
  const el = document.getElementById('status-badge-' + engineId);
  if (!el) return;
  el.className = 'badge sync-state-badge ' + cssClass;
  el.textContent = text;
  el.style.display = '';
}

function showSyncResult(engineId, level, text) {
  const el = document.getElementById('sync-result-' + engineId);
  if (!el) return;
  el.className = 'sync-result ' + level;
  el.textContent = text;
  el.style.display = 'block';
  el.style.opacity = '1';
  el.style.transition = '';
  // auto-fade after 8 s
  clearTimeout(el._fadeTimer);
  el._fadeTimer = setTimeout(() => {
    el.style.transition = 'opacity 1.5s';
    el.style.opacity = '0';
    setTimeout(() => {
      el.style.display = 'none';
      el.style.transition = '';
    }, 1500);
  }, 8000);
}

/* ── Spinner inline HTML ─────────────────────────────────────────── */
const SPINNER = '<span style="display:inline-block;width:8px;height:8px;' +
  'border:2px solid rgba(255,255,255,.25);border-top-color:#fff;' +
  'border-radius:50%;animation:spin .7s linear infinite;' +
  'vertical-align:middle;margin-right:5px"></span>';

/* ── Main action ─────────────────────────────────────────────────── */
async function quickSync(engineId) {
  const btn = document.getElementById('sync-btn-' + engineId);
  const wsEl = document.getElementById('ws-count-' + engineId);
  const lyEl = document.getElementById('ly-count-' + engineId);

  btn.innerHTML = SPINNER + 'Syncing…';
  btn.disabled = true;
  setSyncBadge(engineId, 'badge-neutral', '…');

  try {
    const res  = await fetch('/api/geoengine/engines/' + engineId + '/sync/', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken(), 'Content-Type': 'application/json' },
    });
    const data = await res.json();

    /* ── Collect numbers ─────────────────────────────────────────── */
    const wsC = data.workspaces?.created ?? 0;
    const wsD = data.workspaces?.deleted ?? 0;
    const stC = data.stores?.created     ?? 0;
    const stD = data.stores?.deleted     ?? 0;
    const lyC = data.layers?.created     ?? 0;
    const lyD = data.layers?.deleted     ?? 0;

    const totalCreated = wsC + stC + lyC;
    const totalDeleted = wsD + stD + lyD;

    const allErrors = [
      ...(data.workspaces?.errors ?? []),
      ...(data.stores?.errors     ?? []),
      ...(data.layers?.errors     ?? []),
    ];

    /* ── Decide outcome ──────────────────────────────────────────── */
    let badgeClass, badgeText, resultLevel, resultText;

    if (!res.ok || data.success === false) {
      /* Hard failure — engine unreachable or Django error */
      const reason = data.detail ?? data.error ?? 'engine did not respond';
      badgeClass  = 'badge-red';
      badgeText   = 'Failed';
      resultLevel = 'error';
      resultText  = '✕ Sync failed — ' + reason;

    } else if (allErrors.length > 0) {
      /* Completed with some errors */
      badgeClass  = 'badge-orange';
      badgeText   = 'Partial';
      resultLevel = 'warn';
      resultText  = '⚠ Partial sync — ' + allErrors.length +
                    ' error' + (allErrors.length > 1 ? 's' : '') +
                    '. Open Details for the full log.';

    } else if (totalCreated === 0 && totalDeleted === 0) {
      /* Nothing changed — GeoServer and Django are in sync */
      badgeClass  = 'badge-green';
      badgeText   = 'Up to date';
      resultLevel = 'ok';
      resultText  = '✓ Already up to date — GeoServer matches Django, no changes needed.';

    } else {
      /* Changes were applied */
      const parts = [];
      if (wsC) parts.push(wsC + ' workspace' + (wsC > 1 ? 's' : ''));
      if (stC) parts.push(stC + ' store'     + (stC > 1 ? 's' : ''));
      if (lyC) parts.push(lyC + ' layer'     + (lyC > 1 ? 's' : ''));
      const deletedNote = totalDeleted
        ? ' · ' + totalDeleted + ' removed'
        : '';
      badgeClass  = 'badge-green';
      badgeText   = 'Updated';
      resultLevel = 'ok';
      resultText  = '✓ Imported ' + parts.join(', ') + deletedNote + '.';
    }

    setSyncBadge(engineId, badgeClass, badgeText);
    showSyncResult(engineId, resultLevel, resultText);

    /* ── Update stat counters on card with fresh DB values ───────── */
    if (wsEl && data.db_workspace_count != null) wsEl.textContent = data.db_workspace_count;
    if (lyEl && data.db_layer_count     != null) lyEl.textContent = data.db_layer_count;

  } catch (e) {
    setSyncBadge(engineId, 'badge-red', 'Error');
    showSyncResult(engineId, 'error', '✕ Request failed — ' + e.message +
      '. Check that the server is reachable.');
  }

  btn.textContent = 'Sync Now';
  btn.disabled = false;
}

