/* GeoStory scene editor: live MapLibre preview of the scene layer inline and
 * "Capture view" into the camera fields. The preview endpoint renders unsaved
 * rows with the same manifest builder the public story API uses. */
import { Map as MapLibreMap, NavigationControl } from '../vendor/maplibre-gl/maplibre-gl.mjs';

const PREFIX = 'scene_layers';
const REFRESH_DELAY_MS = 300;
const config = JSON.parse(document.getElementById('scene-editor-config').textContent);
const $ = window.django && window.django.jQuery;

const form = document.getElementById('geostoryscene_form');
const panel = document.querySelector('.scene-editor__panel');
const readout = panel.querySelector('[data-scene-readout]');
const status = panel.querySelector('[data-scene-status]');
const legend = panel.querySelector('[data-scene-legend]');

const field = (name) => form.querySelector(`[name="${name}"]`);
const cameraFields = {
    lng: field('center_lng'),
    lat: field('center_lat'),
    zoom: field('zoom'),
    bearing: field('bearing'),
    pitch: field('pitch'),
    bounds: field('bounds'),
};

const map = new MapLibreMap({
    container: 'scene-editor-map',
    style: config.basemap,
    center: [10, 53.55],
    zoom: 3,
    attributionControl: { compact: true },
});
map.addControl(new NavigationControl({ visualizePitch: true }), 'top-right');

let previewLayerIds = [];
let previewSourceIds = [];
let previewSprite = null;
let layerBounds = null;
let refreshTimer = null;
let requestCounter = 0;

// --- Camera -------------------------------------------------------------------

function number(input) {
    if (!input || input.value.trim() === '') return null;
    const value = Number(input.value);
    return Number.isFinite(value) ? value : null;
}

function savedCamera() {
    const lng = number(cameraFields.lng);
    const lat = number(cameraFields.lat);
    const zoom = number(cameraFields.zoom);
    if (lng === null || lat === null || zoom === null) return null;
    return {
        center: [lng, lat],
        zoom,
        bearing: number(cameraFields.bearing) ?? 0,
        pitch: number(cameraFields.pitch) ?? 0,
    };
}

function savedBounds() {
    try {
        const value = JSON.parse(cameraFields.bounds?.value || 'null');
        return Array.isArray(value) && value.length === 4 ? value : null;
    } catch {
        return null;
    }
}

const round = (value, digits) => Number(value.toFixed(digits));
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

function setField(input, value) {
    if (!input) return;
    input.value = value;
    input.classList.add('scene-editor__captured');
}

function captureView() {
    const center = map.getCenter().wrap();
    const viewport = map.getBounds();
    const west = clamp(viewport.getWest(), -180, 180);
    const east = clamp(viewport.getEast(), -180, 180);
    const south = clamp(viewport.getSouth(), -90, 90);
    const north = clamp(viewport.getNorth(), -90, 90);
    setField(cameraFields.lng, round(center.lng, 6));
    setField(cameraFields.lat, round(center.lat, 6));
    setField(cameraFields.zoom, round(map.getZoom(), 2));
    setField(cameraFields.bearing, round(map.getBearing(), 1));
    setField(cameraFields.pitch, round(map.getPitch(), 1));
    const bounds = west < east && south < north
        ? [round(west, 6), round(south, 6), round(east, 6), round(north, 6)]
        : null;
    setField(cameraFields.bounds, bounds ? JSON.stringify(bounds) : '');
    showStatus('View captured — save the scene to keep it.', 'ok');
}

function clearCamera() {
    for (const key of ['lng', 'lat', 'zoom', 'bounds']) setField(cameraFields[key], '');
    setField(cameraFields.bearing, 0);
    setField(cameraFields.pitch, 0);
    showStatus('Camera cleared — readers will see the map fitted to the layers.', 'ok');
    fitToLayers();
}

function fitToLayers() {
    if (!layerBounds) {
        showStatus('The selected layers have no known extent yet.', 'error');
        return;
    }
    map.fitBounds(layerBounds, { padding: 40, bearing: map.getBearing(), pitch: map.getPitch() });
}

function applySavedCamera() {
    const camera = savedCamera();
    if (camera) {
        map.jumpTo(camera);
    } else if (savedBounds()) {
        map.fitBounds(savedBounds(), { animate: false });
    }
}

function updateReadout() {
    const center = map.getCenter().wrap();
    readout.textContent =
        `${center.lat.toFixed(5)}, ${center.lng.toFixed(5)} · zoom ${map.getZoom().toFixed(2)}` +
        ` · bearing ${map.getBearing().toFixed(0)}° · pitch ${map.getPitch().toFixed(0)}°`;
}

// --- Preview ------------------------------------------------------------------

function inlineRows() {
    return Array.from(document.querySelectorAll(`.dynamic-${PREFIX}`)).filter(
        (row) => !row.querySelector('input[name$="-DELETE"]')?.checked,
    );
}

function parseJsonField(row, suffix) {
    const input = row.querySelector(`[name$="-${suffix}"]`);
    if (!input || input.value.trim() === '') return [];
    try {
        return JSON.parse(input.value);
    } catch {
        return input.value; // let the server report the invalid value
    }
}

function rowPayload(row) {
    const value = (suffix) => row.querySelector(`[name$="-${suffix}"]`)?.value ?? '';
    return {
        layer: value('layer'),
        style_assignment: value('style_assignment'),
        render_layer_ids: parseJsonField(row, 'render_layer_ids'),
        display_order: value('display_order') || 0,
        opacity: value('opacity') || 1,
        feature_mode: value('feature_mode') || 'all',
        feature_id_attribute: value('feature_id_attribute'),
        feature_ids: parseJsonField(row, 'feature_ids'),
    };
}

function scheduleRefresh() {
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(refreshPreview, REFRESH_DELAY_MS);
}

async function refreshPreview({ initial = false } = {}) {
    const rows = inlineRows();
    const requestId = ++requestCounter;
    let data;
    try {
        const response = await fetch(config.previewUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': form.querySelector('[name="csrfmiddlewaretoken"]').value,
            },
            body: JSON.stringify({ layers: rows.map(rowPayload) }),
        });
        data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    } catch (error) {
        showStatus(`Preview failed: ${error.message}`, 'error');
        return;
    }
    if (requestId !== requestCounter) return; // a newer refresh is on its way

    layerBounds = data.bounds;
    const failures = applyManifest(data);
    markRowErrors(rows, data.errors);
    renderLegend(data.legend);

    const invalid = Object.keys(data.errors).length;
    if (invalid || failures.length) {
        const parts = [];
        if (invalid) parts.push(`${invalid} layer row(s) need attention`);
        if (failures.length) parts.push(`${failures.length} style rule(s) could not be drawn`);
        showStatus(parts.join(' · '), 'error');
    } else if (!initial) {
        showStatus(`Showing ${data.legend.length} layer(s).`, 'ok');
    }
    if (initial && !savedCamera() && !savedBounds() && layerBounds) {
        map.fitBounds(layerBounds, { padding: 40, animate: false });
    }
}

function applyManifest(data) {
    for (const id of previewLayerIds) {
        if (map.getLayer(id)) map.removeLayer(id);
    }
    previewLayerIds = [];

    const wanted = new Set(Object.keys(data.map.sources));
    for (const id of previewSourceIds) {
        if (!wanted.has(id) && map.getSource(id)) map.removeSource(id);
    }
    for (const [id, source] of Object.entries(data.map.sources)) {
        if (!map.getSource(id)) map.addSource(id, source);
    }
    previewSourceIds = [...wanted];

    // MapLibre resolves unprefixed icon names against the default sprite, so a
    // single referenced sprite is installed as the default one.
    const sprites = Object.values(data.map.sprites);
    const sprite = sprites.length === 1 ? sprites[0].url : null;
    if (sprite !== previewSprite) {
        map.setSprite(sprite);
        previewSprite = sprite;
    }

    const failures = [];
    for (const layer of data.render_layers) {
        try {
            map.addLayer(layer);
            previewLayerIds.push(layer.id);
        } catch (error) {
            failures.push(`${layer.id}: ${error.message}`);
        }
    }
    if (failures.length) console.warn('Scene preview could not draw:', failures);
    return failures;
}

function markRowErrors(rows, errors) {
    rows.forEach((row, index) => {
        row.classList.remove('scene-editor__row-error');
        row.querySelector('.scene-editor__row-message')?.remove();
        const rowErrors = errors[String(index)];
        if (!rowErrors) return;
        row.classList.add('scene-editor__row-error');
        const message = document.createElement('p');
        message.className = 'scene-editor__row-message';
        message.textContent = Object.entries(rowErrors)
            .map(([name, messages]) => `${name.replaceAll('_', ' ')}: ${messages.join(' ')}`)
            .join(' · ');
        row.querySelector('h3')?.after(message);
    });
}

function renderLegend(entries) {
    const list = legend.querySelector('ul');
    list.replaceChildren(
        ...entries.map((entry) => {
            const item = document.createElement('li');
            const title = document.createElement('strong');
            title.textContent = `${entry.title} — ${entry.style.title}`;
            const image = document.createElement('img');
            image.src = entry.graphic_url;
            image.alt = `Legend for ${entry.title}`;
            image.loading = 'lazy';
            item.append(title, image);
            return item;
        }),
    );
    legend.hidden = entries.length === 0;
}

function showStatus(message, kind) {
    status.textContent = message;
    status.dataset.kind = kind;
}

// --- Wiring -----------------------------------------------------------------------

panel.addEventListener('click', (event) => {
    const action = event.target.closest('[data-scene-action]')?.dataset.sceneAction;
    if (action === 'capture') captureView();
    if (action === 'fit') fitToLayers();
    if (action === 'reset') clearCamera();
});

for (const key of ['lng', 'lat', 'zoom', 'bearing', 'pitch']) {
    cameraFields[key]?.addEventListener('change', () => {
        const camera = savedCamera();
        if (camera) map.jumpTo(camera);
    });
}

if ($) {
    // Autocomplete widgets trigger jQuery events, which native listeners miss.
    $(document).on('change input', `#${PREFIX}-group :input`, scheduleRefresh);
    $(document).on('select2:select', `#${PREFIX}-group select`, scheduleRefresh);
}
// Django dispatches formset:added/removed as native CustomEvents.
document.addEventListener('formset:added', onFormsetChange);
document.addEventListener('formset:removed', onFormsetChange);
function onFormsetChange(event) {
    if (event.detail?.formsetName === PREFIX) scheduleRefresh();
}

map.on('move', updateReadout);
map.once('load', () => {
    updateReadout();
    applySavedCamera();
    refreshPreview({ initial: true });
});
