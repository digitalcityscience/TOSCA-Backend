/*
 * store_form.js — Add/Clone Store form interactions
 *
 * Responsibility:
 *  - Toggle the PostGIS connection section visible/hidden based on
 *    the selected store_type value.
 *
 * Data contract (from template):
 *  - <div id="postgis-section" data-store-type-id="...">
 *    The data-store-type-id attribute carries the exact DOM id of the
 *    store_type <select> rendered by Django, so this script never
 *    hard-codes form field ids.
 */

(function () {
  'use strict';

  var section = document.getElementById('postgis-section');
  if (!section) return;

  var typeId     = section.dataset.storeTypeId;
  var typeSelect = typeId ? document.getElementById(typeId) : null;
  if (!typeSelect) return;

  function togglePostgisSection() {
    section.style.display = typeSelect.value === 'postgis' ? '' : 'none';
  }

  // Run on page load (handles pre-selected value on clone / validation error)
  togglePostgisSection();

  typeSelect.addEventListener('change', togglePostgisSection);

})();
