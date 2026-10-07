# How to create and manage a Campaign

[User-guide home](README.md) · [Publishing and visibility](publishing.md) · [Create a GeoStory](geostories.md) · [Create Events](events.md) · [Safe deletion](safe-changes.md)

A Campaign groups related GeoStories, Events, Event Series, and feedback under one organization and one visibility boundary. Create the Campaign before creating its content.

## Create a Campaign

1. Open **Campaigns → Campaigns → Add Campaign**.
2. Select the owning Organization.
3. Enter the Title and Summary.
4. Start with **Status: Draft** and **Visibility: Private**.
5. Save the Campaign.
6. Add and review its GeoStories and Events.
7. When the campaign is ready, change it to **Active** and set the intended visibility.

## Fields

| Field | Meaning | How to fill it |
|---|---|---|
| Organization | Organization that owns the Campaign. | Select the organization responsible for the content. It cannot be deleted while the Campaign refers to it. |
| Title | Public campaign name. | Use a clear, unique human-readable title. HTML is sanitized. |
| Summary | Longer introduction or purpose. | Explain the topic, geographic scope, audience, and time period. HTML is sanitized. |
| Status | Campaign lifecycle: Draft, Active, or Archived. | Use Draft during preparation, Active for current work, and Archived when the initiative is retained but no longer current. |
| Visibility | Public or Private access boundary. | Private restricts campaign-owned content; Public permits public access when the child object is also published. |
| Created by | Owner/creator recorded for auditing. | Normally set to the responsible editor or automatically from the current user. |

The UUID and created/updated timestamps are system-managed.

## How Campaign visibility affects child content

Campaign visibility is authoritative for Events and is part of the public-visibility decision for GeoStories. Setting an Event's legacy Visibility field does not override a private Campaign.

Changing a Campaign from Public to Private is the fastest safe way to withdraw all campaign-owned public content without deleting it. Review [Publishing and visibility](publishing.md) for the complete matrix.

## Before activating or making public

- Confirm the Organization is correct; moving ownership later can affect who may edit the Campaign.
- Review all child GeoStories, Events, Event Series, and feedback.
- Confirm draft child objects are still draft.
- Check public contact information and linked media.
- Verify map layers do not disclose restricted data.

## Deletion warning

Campaign deletion is permanent and cascades to its Events, Event Series, GeoStories, and feedback. The confirmation page shows dependent counts. Stop if any count is unexpected. Prefer **Archived** or **Private** when the content may be needed later. See [Safe updates and deletion](safe-changes.md).

## Suggested screenshots

- `images/campaign-editor.png`: Organization, Title, Status, and Visibility.
- `images/campaign-delete-warning.png`: sanitized dependency-count warning.

