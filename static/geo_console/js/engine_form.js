/* engine_form.js — Test Connection action for the Engine Create form */

(function () {
  const createBtn = document.getElementById('btn-create');

  // Re-disable Create button if connection fields are edited after a successful test
  ['id_base_url', 'id_admin_username', 'id_admin_password', 'id_engine_type'].forEach(function (id) {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('input', function () {
        if (createBtn) createBtn.disabled = true;
        hideResult('test-result');
      });
    }
  });

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

  window.testConnection = async function () {
    const baseUrl    = (document.getElementById('id_base_url')?.value     || '').trim();
    const username   = (document.getElementById('id_admin_username')?.value || '').trim();
    const password   =  document.getElementById('id_admin_password')?.value || '';
    const engineType =  document.getElementById('id_engine_type')?.value    || 'geoserver';

    if (!baseUrl) {
      showResult(
        'test-result', 'error', '✕ Base URL required',
        '<div class="result-error-item">Please fill in the Base URL field first.</div>'
      );
      return;
    }

    setBtn('btn-test-connection', '<span class="spinner"></span> Testing…', true);
    hideResult('test-result');

    const t0 = performance.now();
    try {
      const resp = await fetch('/api/geoengine/engines/test_connection/', {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrfToken(),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          base_url:       baseUrl,
          admin_username: username,
          admin_password: password,
          engine_type:    engineType,
        }),
      });
      const latency = Math.round(performance.now() - t0);
      const data = await resp.json();

      if (resp.ok && data.success) {
        const versionPart = data.version
          ? '<span class="result-stat"><strong>' + escHtml(data.version) + '</strong> version</span>'
          : '';
        showResult(
          'test-result', 'ok', '✓ Connection successful',
          '<div class="result-stats">' +
            '<span class="result-stat"><strong>' + latency + ' ms</strong> latency</span>' +
            versionPart +
          '</div>'
        );
        if (createBtn) createBtn.disabled = false;
      } else {
        const detail = data.error || data.detail || JSON.stringify(data);
        showResult(
          'test-result', 'error', '✕ Connection failed',
          '<div class="result-error-item">' + escHtml(detail) + '</div>'
        );
      }
    } catch (err) {
      showResult(
        'test-result', 'error', '✕ Request failed',
        '<div class="result-error-item">' + escHtml(String(err)) + '</div>'
      );
    } finally {
      setBtn('btn-test-connection', 'Test Connection', false);
    }
  };
}());
