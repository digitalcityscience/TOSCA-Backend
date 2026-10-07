# How to create and publish a GeoStory

[User-guide home](README.md) · [Configure a Campaign](campaigns.md) · [Prepare layers and styles](geodata.md) · [Publishing rules](publishing.md) · [Create Events](events.md)

## Before you start

You need a Campaign, an author account, and any map layers you plan to use. Layers must already be synchronized from GeoServer and marked **Published** and **Public**. If the story needs a particular appearance, create and assign the style first: [How to prepare geodata, styles, and sprites](geodata.md).

## Create the story

1. Open **GeoStories → GeoStories → Add GeoStory**.
2. Fill the editorial fields below.
3. Keep **Status** as **Draft** while editing.
4. Add map layers in the GeoStory Layer inline.
5. Save, reopen, and review the rendered content.
6. Change Status to **Published** only after the campaign and media visibility are correct.

## GeoStory fields

| Field | Meaning | How to fill it |
|---|---|---|
| Title | Public headline. | Use a short, specific title. HTML is sanitized. |
| Summary | Short introduction used in listings/previews. | Explain the story in one or two sentences. |
| About the Author/Transparency (Public) | Public context about the author and viewpoint. | State relevant occupation, location, perspective, and narrative background. Do not add private contact information. |
| Content | Main story body in the rich Editor.js editor. | Structure it with headings, paragraphs, media, and links; preview before publishing. |
| Hero image | Main image for cards and the story header. | Upload a suitable image you are permitted to publish. Storage visibility follows story and campaign status. |
| Hero image alt | Accessible description of the hero image. | Required whenever a hero image is present. Describe the meaningful visual content, not the filename. |
| Status | Editorial lifecycle. | **Draft** hides unfinished work; **Published** makes eligible content public; **Archived** retires it. |
| Campaign | Owning campaign and visibility boundary. | Select the campaign the story belongs to. A story is public only when both the campaign is public and the story is published. |
| Author | Creator/owner displayed or recorded for the story. | Select the responsible user. |

The UUID, storage alias, created timestamp, and updated timestamp are system-managed.

## Add map layers

Use the **GeoStory Layers** inline at the bottom of the story form.

| Inline field | Meaning | How to fill it |
|---|---|---|
| Layer | Map dataset shown with the story. | Choose a synchronized Published + Public layer. Each layer can be added only once per story. |
| Style assignment | Exact layer/style link to use. | Leave blank to use the layer's active default; select an assignment to pin a specific style. It must belong to the selected layer. |
| Display order | Drawing/list order. | Use `0`, `1`, `2`, and so on. Lower values come first. New rows may auto-increment. |
| Delete | Removes this relationship, not the underlying layer. | Enable only when the story should stop using the layer. |

If the desired layer or style is missing, do not create a substitute in the story. Fix the source configuration in [geodata administration](geodata.md), then return to the story.

## Publishing checks

- Preview all rich-content blocks and links.
- Confirm the hero image has useful alt text.
- Verify the Campaign is the intended visibility boundary.
- Verify every map layer loads and the intended default or pinned style is applied.
- Check layer order, especially when opaque polygon/raster layers could cover other layers.
- Publish the story only when it is ready for its audience.

## Suggested screenshots

- `images/geostory-editor.png`: main GeoStory fields and content editor.
- `images/geostory-layers.png`: layer inline with Layer, Style assignment, and Display order.
