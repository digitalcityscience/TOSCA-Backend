# Using the rich-content editor

[User-guide home](README.md) · [GeoStories](geostories.md) · [Events](events.md) · [Footer documents](footer.md#add-footer-documents)

GeoStories, Event content, Event Series defaults, Footer documents, and Layer descriptions use structured Editor.js content. The system validates and normalizes the document before saving.

## Editing content

1. Use the visible editor controls instead of editing stored JSON directly.
2. Break long content into short sections with meaningful headings.
3. Use descriptive link text; avoid “click here.”
4. Add alternative text or equivalent context for informative images.
5. Save as Draft and reopen the object to confirm the content was stored correctly.
6. Preview at narrow and wide screen sizes when possible.

## Content profiles

Full content areas accept the supported Editor.js blocks and safe inline formatting configured by TOSCA. Layer descriptions intentionally use a smaller profile: paragraphs, headings, and lists only, with heading levels 2–4. A level-1 heading is reserved for the page title.

Unsupported blocks, invalid document structure, and unsafe markup are rejected or sanitized. If an older or pasted block fails, recreate it with a supported editor control rather than forcing raw JSON.

## Inheritance in Events

- An Event with a null **Content override** inherits its Event Series **Default content**.
- An explicitly empty Event document suppresses inherited series content.
- Editing one Event's override does not change the Series default or other occurrences.

## Accessibility checklist

- Use headings in order; do not choose a level for visual size alone.
- Give images concise, meaningful alternative text.
- Ensure linked text describes its destination.
- Avoid instructions based only on color or position.
- Use lists for genuine sequences or collections.
- Write dates, times, locations, and access instructions unambiguously.

