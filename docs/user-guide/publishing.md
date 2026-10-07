# Publishing and visibility

[User-guide home](README.md) · [Campaigns](campaigns.md) · [GeoStories](geostories.md) · [Events](events.md) · [Footer](footer.md)

Publishing is controlled at more than one level. A child object marked Published is not necessarily public if its Campaign or Layer remains private.

## Visibility matrix

| Object | Public requirements | Safe way to withdraw it |
|---|---|---|
| Campaign | Status and Visibility must be appropriate for public use. | Set Visibility to Private or Status to Archived. |
| GeoStory | GeoStory Status is Published **and** its Campaign is Public. | Change the GeoStory to Draft/Archived or make the Campaign Private. |
| Event | Event Status is Published **and** its Campaign is Public. Campaign visibility is authoritative. | Change the Event to Draft/Cancelled or make the Campaign Private. |
| Event Series | Series controls generation/grouping; occurrence publication is checked on Events. | Stop or edit generation and update affected Events. |
| Layer | Publishing state is Published **and** Public is enabled. | Disable Public or unpublish through the supported geodata workflow. |
| Style | Must be valid, compatible, assigned, and successfully synchronized where remote support is required. | Deactivate/change the assignment; do not delete a style still in use. |
| Footer | The Footer must be explicitly published; the public API serves its saved snapshot. | Publish a replacement Footer. |

## Recommended release sequence

1. Keep the Campaign Private while building content.
2. Synchronize and verify map layers.
3. Validate styles and sprites and configure default assignments.
4. Review GeoStories and Events in Draft.
5. Publish the intended child objects.
6. Make the Campaign Public only after the complete collection is reviewed.
7. Verify the public result while signed out or using an approved public test session.

## Important distinctions

- **Status** describes lifecycle; **Visibility** controls audience.
- Event's legacy Visibility field is not an independent authorization control; Campaign visibility wins.
- A GeoStory's hero image becomes public only when the Campaign is Public and the GeoStory is Published.
- Footer draft edits do not affect the live footer until it is published again.
- Public Layer is not enough: its publishing state must also be Published.

