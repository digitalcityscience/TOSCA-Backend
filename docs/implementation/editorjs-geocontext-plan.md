# GeoContext Editor.js Integration, Revised

## Summary

- Replace `GeoContext.content` with canonical Editor.js JSON and remove `content_type`. This is an intentional contract change and is acceptable because the app is not yet published.
- Use Editor.js only in Django admin for authoring, but make the backend contract JSON-first everywhere `GeoContext` is exposed through geostories, events, and feedback.
- Limit MVP authoring to `paragraph`, `header`, `list`, `quote`, `delimiter`, and `code`, plus inline `link`, `bold`, `italic`, and `inlineCode`. No image, embed, raw HTML, or upload tools.

## Implementation Changes

- Change `GeoContext.content` from `TextField` to `JSONField` with canonical default `{"blocks": []}`. Persist only the normalized document shape `{ "blocks": [...] }`; accept full Editor.js save payloads on input, but strip `time`, `version`, and block `id` fields during normalization so repeated save/load/save cycles are deterministic.
- Move JSON validation into a new module such as `tosca_api/apps/core/editorjs.py`. Keep `nh3`-based HTML sanitization there only for inline text fragments inside block data, not as the primary document validator.
- Enforce model-level validation by normalizing `GeoContext.content` inside model validation and save flow. `None`, missing values, and empty strings coerce to `{"blocks": []}`. Any non-empty value must normalize to a dict with a `blocks` list.
- Fix the inline HTML whitelist used inside text-bearing fields to: `a`, `strong`, `em`, `code`, and `br`. Normalize `<b>` to `<strong>` and `<i>` to `<em>`. `inlineCode` maps to `<code>`. Reject unsafe URLs and strip scripts/event handlers with `nh3`.
- Validate allowed block schemas exactly:
  - `paragraph`: `data.text`
  - `header`: `data.text`, `data.level` with allowed levels `1-4`
  - `list`: `data.style` in `ordered|unordered`, recursive `items`, and each item limited to `content`, `meta`, `items`; `meta` must be `{}` for MVP
  - `quote`: `data.text` and optional empty `caption`; reject any `alignment` key for deterministic storage
  - `delimiter`: empty `data`
  - `code`: `data.code`
- Build the Django admin editor as progressive enhancement in `geocontext` admin:
  - the form field is a textarea containing canonical JSON by default
  - JS upgrades that textarea into an Editor.js mount and mirrors changes back into the textarea before submit
  - no custom AJAX is used, so standard Django form CSRF handling remains unchanged
  - if JS fails to load, admins can still submit raw JSON through the textarea
  - changelist preview renders extracted plain text from blocks, truncated to 120 characters, with `(empty)` for no visible text
- Vendor Editor.js assets into Django static files and include upstream license files with the vendored sources. Use official packages only. The current upstream split to assume is: core Editor.js is [Apache-2.0](https://github.com/codex-team/editorjs), while official block tools such as [list](https://github.com/editor-js/list) and [paragraph](https://github.com/editor-js/paragraph) are MIT.
- Fix the legacy HTML-to-block migration algorithm:
  1. Release 1 adds `content_json` alongside existing columns.
  2. Backfill converts existing rows into canonical block JSON.
  3. Release 2 switches application reads and writes to `content_json`, but keeps legacy columns in place for rollback safety.
  4. Release 3 drops legacy `content` and `content_type`, then renames `content_json` to `content`.
- Legacy HTML backfill rules are fixed:
  - old `simple` content becomes either `{"blocks": []}` if blank or one `paragraph` block with sanitized inline text
  - old rich content is first passed through `sanitize_rich`, then parsed into blocks
  - `h1`-`h4` become `header` blocks with matching levels
  - `p` becomes `paragraph`, preserving inline markup and `<br>`
  - top-level inline/text nodes outside block tags are folded into `paragraph` blocks
  - `ul` and `ol` become `list` blocks with recursive nested items preserved using the official nested list JSON shape from [Editor.js List](https://github.com/editor-js/list)
  - `blockquote` becomes `quote` with `caption=""`
  - `pre><code` and `pre` become `code`
  - inline `<code>` outside `pre` stays inline inside paragraph/header/quote/list item content
  - unknown non-media tags are handled by the initial `nh3` sanitize pass and then parsed from the sanitized fragment
  - sanitized fragments containing `img`, `figure`, or `figcaption` abort conversion for that row, and the migration aborts with a sorted explicit ID list
- Add a non-mutating preflight command that scans all `GeoContext` rows and prints the exact IDs that would abort migration. The command must be read-only and idempotent. The data migration must use the same detection logic and fail with the same sorted ID list.

## Public Interfaces

- `GeoContext.content` becomes canonical Editor.js JSON with shape `{ "blocks": [...] }` everywhere it is exposed.
- `content_type` is removed from models, admin, serializers, tests, and schema output.
- Read responses never return `null` for `context.content`; empty documents return `{ "blocks": [] }`.
- The backend accepts Editor.js-compatible write payloads but normalizes them to the canonical stored shape described above.
- This plan is based on Editor.js’s official JSON save contract documented at [editorjs.io](https://editorjs.io/saving-data/), but the persisted backend shape is intentionally narrower for deterministic storage.

## Test Plan

- Model/unit tests for valid documents, malformed top-level payloads, unsupported block types, invalid block schemas, unsafe links, and inline XSS attempts such as `<script>` inside paragraph text.
- Normalization tests for `None`, `""`, full Editor.js payloads with `time/version/id`, and canonical output equality after normalize-save-reload-save.
- Migration tests for:
  - simple text to paragraph blocks
  - rich HTML to deterministic block JSON for paragraphs, headers, nested lists, quotes, code blocks, mixed inline formatting, and `<br>` handling
  - exact sorted abort ID list for rows containing legacy media HTML
  - dry-run preflight producing the same abort set as the real migration
  - preflight command remaining read-only and returning the same output across repeated runs
- Admin tests for:
  - widget assets included on the GeoContext admin form
  - textarea fallback visible before JS enhancement
  - editor hydration from stored JSON
  - round-trip save without semantic or canonical JSON drift
  - changelist preview output for long and empty documents
- API tests for geostories, events, and feedback detail serializers confirming `context.content` is JSON and `content_type` is absent.

## Assumptions

- Backward compatibility for published API consumers is out of scope because the app is not yet published.
- No standalone GeoContext CRUD API is added in this work; only admin authoring, model validation, migration, and existing nested read surfaces change.
- Media/image support remains explicitly out of scope for MVP; any row containing legacy media markup blocks the migration until handled manually or in a later media phase.
