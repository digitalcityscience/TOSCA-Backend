/**
 * console.js — Shared behaviour for all Geo Console templates
 *
 * Loaded by geo_console/base.html unconditionally (after DOM ready).
 * Handles cross-cutting patterns so templates stay logic-free:
 *
 *   [data-autosubmit]   — <select> submits its parent form on change
 *   [data-confirm]      — <form> asks for confirmation before submit
 *   [data-confirm-click]— <button/a> asks for confirmation before click
 *
 * Pattern: behaviour is declared in HTML via data attributes; no
 * onclick / onchange / onsubmit attributes are needed in templates.
 */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {

    /* ── Auto-submit selects ───────────────────────────────────────── */
    /* Usage: <select data-autosubmit> — submits the enclosing <form>  */
    document.querySelectorAll('select[data-autosubmit]').forEach(function (sel) {
      sel.addEventListener('change', function () {
        var form = sel.closest('form');
        if (form) form.submit();
      });
    });

    /* ── Confirm before form submit ────────────────────────────────── */
    /* Usage: <form data-confirm="Are you sure?">                      */
    /* Use &#10; in HTML attr for a newline in the confirm message.    */
    document.querySelectorAll('form[data-confirm]').forEach(function (form) {
      form.addEventListener('submit', function (e) {
        var msg = form.dataset.confirm || 'Are you sure?';
        if (!window.confirm(msg)) {
          e.preventDefault();
        }
      });
    });

    /* ── Confirm before button / link click ────────────────────────── */
    /* Usage: <button data-confirm-click="Really delete?">             */
    document.querySelectorAll('[data-confirm-click]').forEach(function (el) {
      el.addEventListener('click', function (e) {
        var msg = el.dataset.confirmClick || 'Are you sure?';
        if (!window.confirm(msg)) {
          e.preventDefault();
        }
      });
    });

  });

})();
