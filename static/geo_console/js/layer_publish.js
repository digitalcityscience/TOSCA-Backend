/*
 * layer_publish.js — Publish Layer form interactions
 *
 * Responsibilities:
 *  - Workspace → rebuild store <select> (filtered from embedded JSON)
 *  - Store → fetch PostGIS geometry tables via API
 *  - Table card click → populate hidden fields + geometry display
 *  - CRS preset cards + custom EPSG input → update hidden srid field
 *
 * Data contract (from template):
 *  - <script id="stores-data" type="application/json">[…]</script>
 *    Each entry: { id, workspace, name, store_type }
 *
 * API used:
 *  - GET /api/geoengine/stores/{id}/postgis_tables/
 *    Returns: { tables: [{ table_name, geometry_column, geometry_type, srid }] }
 */

(function () {
  'use strict';

  /* ── DOM refs ────────────────────────────────────────────────────── */
  const wsSelect      = document.getElementById('id_workspace_id');
  const storeSelect   = document.getElementById('id_store_id');
  const pickerPH      = document.getElementById('picker-placeholder');
  const pickerLoading = document.getElementById('picker-loading');
  const pickerEmpty   = document.getElementById('picker-empty');
  const pickerList    = document.getElementById('picker-list');
  const tableNameFld  = document.getElementById('id_table_name');
  const layerNameFld  = document.getElementById('id_layer_name');
  const geomColFld    = document.getElementById('id_geometry_column');
  const geomTypeFld   = document.getElementById('id_geometry_type');
  const geomDisplay   = document.getElementById('geom-display');
  const geomIcon      = document.getElementById('geom-icon');
  const geomTypeLabel = document.getElementById('geom-type-label');
  const geomColLabel  = document.getElementById('geom-col-label');
  const sridHidden    = document.getElementById('id_srid');
  const sridCustom    = document.getElementById('srid-custom');
  const sridApplyBtn  = document.getElementById('srid-custom-apply');
  const crsBadge      = document.getElementById('crs-selected-badge');
  const crsName       = document.getElementById('crs-selected-name');

  /* ── Geometry type metadata ──────────────────────────────────────── */
  const GEOM_META = {
    'Point':              { icon: '●', label: 'Point',             cls: 'geom--point' },
    'MultiPoint':         { icon: '⁘', label: 'Multi-Point',       cls: 'geom--point' },
    'LineString':         { icon: '╌', label: 'LineString',        cls: 'geom--line'  },
    'MultiLineString':    { icon: '≋', label: 'Multi-LineString',  cls: 'geom--line'  },
    'Polygon':            { icon: '▭', label: 'Polygon',           cls: 'geom--poly'  },
    'MultiPolygon':       { icon: '▰', label: 'Multi-Polygon',     cls: 'geom--poly'  },
    'GeometryCollection': { icon: '◈', label: 'Collection',        cls: 'geom--mixed' },
  };
  function geomMeta(t) {
    return GEOM_META[t] || { icon: '?', label: t || '—', cls: '' };
  }

  /* ── CRS preset names ────────────────────────────────────────────── */
  const CRS_NAMES = {
    4326:  'WGS 84',
    3857:  'Web Mercator',
    4258:  'ETRS89',
    25832: 'ETRS89 / UTM 32N',
    25833: 'ETRS89 / UTM 33N',
    27700: 'British National Grid',
  };

  /* ── Store data (embedded JSON from template) ────────────────────── */
  const ALL_STORES = JSON.parse(
    document.getElementById('stores-data')?.textContent || '[]'
  );

  /* ── Step 1a: Workspace → rebuild store <select> ─────────────────── */
  function filterStores() {
    const wsId = wsSelect ? wsSelect.value.trim() : '';
    if (!storeSelect) return;

    // Remove all options except index 0 (placeholder)
    while (storeSelect.options.length > 1) storeSelect.remove(1);

    if (!wsId) {
      storeSelect.options[0].textContent = '— select a workspace first —';
      storeSelect.disabled = true;
      clearPicker();
      return;
    }

    const matching = ALL_STORES.filter(s => String(s.workspace).trim() === wsId);

    if (!matching.length) {
      storeSelect.options[0].textContent = '— no stores in this workspace —';
      storeSelect.disabled = false;
      clearPicker();
      return;
    }

    storeSelect.options[0].textContent = '— select a store —';
    matching.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.store_type ? `${s.name} (${s.store_type})` : s.name;
      storeSelect.appendChild(opt);
    });
    storeSelect.disabled = false;
    clearPicker();
  }

  /* ── Step 1b: Store → fetch PostGIS tables ───────────────────────── */
  let _currentStoreId = null;

  function showState(state) {
    pickerPH.style.display      = state === 'ph'      ? '' : 'none';
    pickerLoading.style.display = state === 'loading' ? '' : 'none';
    pickerEmpty.style.display   = state === 'empty'   ? '' : 'none';
    pickerList.style.display    = state === 'list'    ? '' : 'none';
  }

  function clearPicker() {
    _currentStoreId = null;
    pickerList.innerHTML = '';
    showState('ph');
  }

  function loadTables(storeId) {
    if (!storeId) { clearPicker(); return; }
    if (storeId === _currentStoreId) return;
    _currentStoreId = storeId;
    showState('loading');

    const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
    fetch(`/api/geoengine/stores/${storeId}/postgis_tables/`, {
      headers: { 'X-CSRFToken': csrf, 'Accept': 'application/json' },
      credentials: 'same-origin',
    })
    .then(r => r.ok ? r.json() : r.json().then(d => Promise.reject(d.error || `HTTP ${r.status}`)))
    .then(data => {
      const tables = data.tables || [];
      if (!tables.length) { showState('empty'); return; }

      pickerList.innerHTML = '';
      tables.forEach(t => {
        const m = geomMeta(t.geometry_type);
        const card = document.createElement('div');
        card.className = 'lp-table-card';
        card.dataset.table    = t.table_name;
        card.dataset.geomCol  = t.geometry_column;
        card.dataset.geomType = t.geometry_type;
        card.dataset.srid     = t.srid;
        card.innerHTML = `
          <div class="lp-tc-icon ${m.cls}">${m.icon}</div>
          <div class="lp-tc-body">
            <div class="lp-tc-name">${t.table_name}</div>
            <div class="lp-tc-meta">
              <span class="lp-tc-type">${m.label}</span>
              <span class="lp-tc-sep">·</span>
              <span class="lp-tc-col font-mono">${t.geometry_column}</span>
              <span class="lp-tc-sep">·</span>
              <span class="lp-tc-srid">EPSG:${t.srid}</span>
            </div>
          </div>
          <div class="lp-tc-check">✓</div>
        `;
        card.addEventListener('click', () => selectTable(card));
        pickerList.appendChild(card);
      });
      showState('list');

      // POST-back restore: if table_name was already set (form re-render after
      // a failed submit), highlight the matching card so the user can see their
      // previous selection.  Hidden inputs already hold the correct values from
      // request.POST — we only need to restore the visual state.
      const _prevTable = tableNameFld ? tableNameFld.value.trim() : '';
      if (_prevTable) {
        pickerList.querySelectorAll('.lp-table-card').forEach(c => {
          if (c.dataset.table === _prevTable) c.classList.add('selected');
        });
      }
    })
    .catch(err => {
      pickerPH.textContent = `Could not load tables: ${err}`;
      showState('ph');
    });
  }

  /* ── Table card click ────────────────────────────────────────────── */
  function selectTable(card) {
    document.querySelectorAll('.lp-table-card.selected')
            .forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');

    const tbl  = card.dataset.table;
    const col  = card.dataset.geomCol;
    const type = card.dataset.geomType;
    const srid = parseInt(card.dataset.srid, 10) || 4326;

    if (tableNameFld) tableNameFld.value = tbl;

    // Auto-fill layer name only if user hasn't typed their own
    if (layerNameFld && (!layerNameFld.value || layerNameFld.dataset.autofilled === 'true')) {
      layerNameFld.value = tbl;
      layerNameFld.dataset.autofilled = 'true';
    }

    if (geomColFld)  geomColFld.value  = col;
    if (geomTypeFld) geomTypeFld.value = type;
    updateGeomDisplay(type, col);
    selectCRS(srid);
  }

  /* ── Geometry display ────────────────────────────────────────────── */
  function updateGeomDisplay(type, col) {
    const m = geomMeta(type);
    geomIcon.textContent      = m.icon;
    geomTypeLabel.textContent = m.label;
    geomColLabel.textContent  = col ? `column: ${col}` : '—';
    geomDisplay.className     = `lp-geom-display ${m.cls}`;
  }

  // Re-populate geometry display on POST-back (form validation failure)
  (function initGeomDisplay() {
    const t = geomTypeFld ? geomTypeFld.value : '';
    const c = geomColFld  ? geomColFld.value  : '';
    if (t) updateGeomDisplay(t, c);
  })();

  /* ── CRS picker ──────────────────────────────────────────────────── */
  function selectCRS(epsg) {
    sridHidden.value     = epsg;
    crsBadge.textContent = `EPSG:${epsg}`;
    crsName.textContent  = CRS_NAMES[epsg] || '';

    document.querySelectorAll('.lp-crs-card').forEach(btn => {
      btn.classList.toggle('lp-crs-card--active',
                           parseInt(btn.dataset.epsg, 10) === epsg);
    });

    // Clear custom input if a preset was matched
    sridCustom.value = CRS_NAMES[epsg] ? '' : epsg;
  }

  document.querySelectorAll('.lp-crs-card').forEach(btn => {
    btn.addEventListener('click', () => selectCRS(parseInt(btn.dataset.epsg, 10)));
  });

  function applyCustomCRS() {
    const v = parseInt(sridCustom.value, 10);
    if (v > 0) selectCRS(v);
  }
  if (sridApplyBtn) sridApplyBtn.addEventListener('click', applyCustomCRS);
  if (sridCustom)   sridCustom.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); applyCustomCRS(); }
  });

  // Initialise CRS display from current srid value (handles POST-back)
  selectCRS(parseInt(sridHidden ? sridHidden.value : '4326', 10) || 4326);

  /* ── Prevent layer name overwrite when user has typed their own ──── */
  if (layerNameFld) {
    layerNameFld.addEventListener('input', () => {
      layerNameFld.dataset.autofilled = 'false';
    });
  }

  /* ── Wire events ─────────────────────────────────────────────────── */
  if (wsSelect)    wsSelect.addEventListener('change', filterStores);
  if (storeSelect) storeSelect.addEventListener('change', () => loadTables(storeSelect.value));

  // Capture previously selected store before filterStores rebuilds options
  const _prevStoreId = storeSelect ? storeSelect.value : '';

  filterStores(); // rebuild store options from JSON based on current workspace selection

  // Restore store selection and reload tables after rebuild (POST-back)
  if (_prevStoreId && storeSelect) {
    storeSelect.value = _prevStoreId;
    if (storeSelect.value === _prevStoreId) {
      loadTables(_prevStoreId);
    }
  }

})();
