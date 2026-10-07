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
| Display order | Unique order within this Footer; lower numbers appear first. |
| Active | Only active logos are included when published. |

Use consistent visual dimensions and meaningful alt text. Do not use phrases such as “image of” unless they add information.

## Add footer documents

Footer documents are pages such as Privacy, Accessibility, or Imprint.

| Inline field | Meaning and guidance |
|---|---|
| Title | Required public link/page title. |
| Slug | Reserved frontend path without a leading slash, for example `privacy`. Use lowercase letters, numbers, and single hyphens only. Must be unique within the Footer. |
| Content | Rich Editor.js document shown on the footer page. |
| Display order | Unique order within this Footer; lower numbers appear first. |
| Active | Only active documents are included when published. |

## Publish safely

1. Check every active logo, alt text, and destination URL.
2. Open every active document and verify title, slug, headings, links, and content.
3. Confirm display-order values are unique and intentional.
4. Save the Footer before publishing.
5. Publish it and verify the public footer and document routes.

Publishing replaces the currently public Footer snapshot as one operation. Draft edits after publishing do not change the public snapshot until you publish again.

## Suggested screenshots

- `images/footer-editor.png`: Footer name and publication metadata.
- `images/footer-logos.png`: Logo inline fields.
- `images/footer-documents.png`: Document inline with Title, Slug, Content, order, and Active.
- `images/footer-publish.png`: publish action/control, with personal account details hidden.
