# How to configure and publish the footer

[User-guide home](README.md) · [Publishing rules](publishing.md) · [Safe changes](safe-changes.md)

The footer uses an explicit publishing workflow. Editors change a Footer draft and its inline logos/documents; publishing stores an immutable public snapshot. Only one Footer can be published at a time.

## Create or edit a Footer

1. Open **Footer → Footers**.
2. Add a Footer or open an existing draft.
3. Enter a unique **Name**. This is the editor-facing identity of the footer.
4. Add Logo and Document inline rows.
5. Save and review the draft.
6. Use the admin publishing action/control to publish the complete footer.

### Footer fields

| Field | Meaning |
|---|---|
| Name | Unique editor-facing footer name. |
| Is published | Read-only indicator for the currently public Footer. |
| Publication version | Read-only revision number incremented by publishing. |
| Published snapshot | Read-only immutable payload currently served publicly. |
| Published at | Read-only publication time. |
| Published by | Read-only user who performed publication. |

A published Footer cannot be deleted. Publish another Footer first if the old one must be removed.

## Add logos

| Inline field | Meaning and guidance |
|---|---|
| Image | PNG, JPG/JPEG, or WebP logo file. |
| Alt text | Required accessible name. Identify the organization or meaningful logo purpose. |
| Destination URL | Optional link opened when the logo is selected. Use a complete HTTPS URL. |
| Display order | Position within this Footer; lower numbers appear first. Changing one row's number moves it there and shifts the others. |
| Active | Only active logos are included when published. |

Use consistent visual dimensions and meaningful alt text. Do not use phrases such as “image of” unless they add information.

## Add footer documents

Footer documents are pages such as Privacy, Accessibility, or Imprint.

| Inline field | Meaning and guidance |
|---|---|
| Title | Required public link/page title. |
| Slug | Reserved frontend path without a leading slash, for example `privacy`. Use lowercase letters, numbers, and single hyphens only. Must be unique within the Footer. |
| Content | Rich Editor.js document shown on the footer page. |
| Display order | Position within this Footer; lower numbers appear first. Changing one row's number moves it there and shifts the others. |
| Active | Only active documents are included when published. |

## How many logos and documents?

There is no fixed limit. Display order decides what stays visible when space runs out:

- **Logos** wrap onto more rows on narrow screens. Put the most important partners first.
- **Documents** show as links on one row while they fit. The last ones by display order move into a **More** menu on smaller screens.

Above 8 active logos or 6 active documents the admin shows a warning when you save and on the publish confirmation page. The warning never blocks publishing.

## Publish safely

1. Check every active logo, alt text, and destination URL.
2. Open every active document and verify title, slug, headings, links, and content.
3. Confirm the display order puts the most important items first.
4. Save the Footer before publishing.
5. Publish it and verify the public footer and document routes.

Publishing replaces the currently public Footer snapshot as one operation. Draft edits after publishing do not change the public snapshot until you publish again.

## Suggested screenshots

- `images/footer-editor.png`: Footer name and publication metadata.
- `images/footer-logos.png`: Logo inline fields.
- `images/footer-documents.png`: Document inline with Title, Slug, Content, order, and Active.
- `images/footer-publish.png`: publish action/control, with personal account details hidden.
