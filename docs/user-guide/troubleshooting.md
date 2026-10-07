# Troubleshooting

[User-guide home](README.md) · [Permissions](permissions.md) · [Geodata](geodata.md) · [Publishing](publishing.md)

## Content is saved but not public

Check the full chain rather than only the child object's status:

- Campaign is Public and in the intended lifecycle state.
- GeoStory/Event is Published.
- Referenced Layer is Published and Public.
- Footer was explicitly published after the last edits.
- You are testing the intended environment and URL.

## An admin section or object is missing

- Sign out and sign in to refresh Keycloak-derived permissions.
- Confirm the correct organization and account.
- Ask an administrator to check `DJANGO_STAFF`/`DJANGO_SUPERADMIN`, model permissions, organization scope, and engine organization restrictions.

## A layer is unavailable in a GeoStory or Event

- Synchronize the GeoServer engine.
- Confirm the Layer is both Published and Public.
- Resolve publishing or sync errors.
- Confirm the Store belongs to the Layer's Workspace.

## A style cannot be selected or saved

- Fix validation errors first.
- Confirm style, sprite, workspace, and Layer use the same engine/scope.
- For MBStyle, provide applicable Style layer IDs when inference is ambiguous.
- Ensure the sprite index contains every statically referenced icon or pattern.
- Ensure only one active Default assignment exists for a Layer.

## Sprite validation fails

- Use PNG images.
- Supply a non-empty JSON index with width, height, x, y, and pixelRatio.
- Keep every indexed rectangle inside the image bounds.
- Upload both 2× image and index, or neither.
- Use identical sprite names and logical dimensions at 1× and 2×.

## Event or recurrence validation fails

- End must be after start.
- Apply the Location-mode rules in [Events](events.md#location-mode-rules).
- Recurring Series need recurrence type, timezone, and exactly one stopping condition.
- Weekly rules need weekdays; monthly rules need a complete monthly rule.
- Manual Batch must not contain recurrence-only fields.
- Language note requires `other` in Language.

## Rich content fails to save

- Remove or recreate unsupported blocks using visible editor controls.
- For Layer descriptions, use only paragraphs, headings 2–4, and lists.
- Avoid pasting raw Editor.js JSON or unsafe markup.

## Reporting a problem

Include environment, page URL without secrets, object UUID/name, intended action, exact visible error, time, and safe reproduction steps. Redact usernames, emails, credentials, tokens, private URLs, and confidential content from screenshots.

