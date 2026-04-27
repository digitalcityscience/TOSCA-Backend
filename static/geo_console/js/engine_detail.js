/* engine_detail.js — Sync action for the Engine Detail page */

const API_BASE = '/api/geoengine';

function csrfToken() {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : '';
}

function setBtn(id, html, disabled) {
  const btn = document.getElementById(id);
  if (!btn) return;
  btn.innerHTML = html;
  btn.disabled = disabled;
}

function showResult(panelId, level, titleHtml, bodyHtml) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  panel.className = 'result-panel result--' + level;
  panel.innerHTML =
    '<div class="result-title ' + level + '">' + titleHtml + '</div>' + bodyHtml;
  panel.style.display = 'block';
}

function hideResult(id) {
  const panel = document.getElementById(id);
  if (panel) panel.style.display = 'none';
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function statBox(label, synced, created, deleted) {
  const s = synced  != null ? '<strong>' + synced  + '</strong> synced'  : '';
  const c = created != null ? '<strong>' + created + '</strong> created' : '';
  const d = deleted != null ? '<strong>' + deleted + '</strong> deleted' : '';
  const parts = [s, c, d].filter(Boolean).join(', ');
  return parts ? '<span class="result-stat">' + label + ': ' + parts + '</span>' : '';
}

function collectErrors(groups) {
  const items = groups.flatMap(function (g) { return g.errors || []; });
  if (!items.length) return '';
  return items
    .map(function (e) {
      return '<div class="result-error-item">✕ ' + escHtml(String(e)) + '</div>';
    })
    .join('');
}

async function syncEngine(engineId) {
  setBtn('btn-sync', '<span class="spinner"></span> Syncing…', true);
  hideResult('sync-result');
  try {
    const resp = await fetch(API_BASE + '/engines/' + engineId + '/sync/', {
      method: 'POST',
      headers: {
        'X-CSRFToken': csrfToken(),
        'Content-Type': 'application/json',
      },
    });
    const data = await resp.json();
    if (resp.ok) {
      const ws = data.workspaces || {};
      const st = data.stores     || {};
      const ly = data.layers     || {};
      const errors = collectErrors([ws, st, ly]);
      const level = errors ? 'warn' : 'ok';
      const title = errors ? '⚠ Sync completed with errors' : '✓ Sync successful';
      const statsHtml =
        '<div class="result-stats">' +
          statBox('Workspaces', ws.synced, ws.created, ws.deleted) +
          statBox('Stores',     st.synced, st.created, st.deleted) +
          statBox('Layers',     ly.synced, ly.created, ly.deleted) +
        '</div>';
      showResult('sync-result', level, title, statsHtml + errors);
    } else {
      const detail = data.detail || data.error || JSON.stringify(data);
      showResult(
        'sync-result', 'error', '✕ Sync failed',
        '<div class="result-error-item">' + escHtml(detail) + '</div>'
      );
    }
  } catch (err) {
    showResult(
      'sync-result', 'error', '✕ Request failed',
      '<div class="result-error-item">' + escHtml(String(err)) + '</div>'
    );
  } finally {
    setBtn('btn-sync', 'Sync Now', false);
  }
}
