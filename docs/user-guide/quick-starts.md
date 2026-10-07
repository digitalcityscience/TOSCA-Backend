# Quick-start recipes

[User-guide home](README.md) · [Troubleshooting](troubleshooting.md)

These recipes summarize the common paths. Follow the linked full guide when a field or error needs explanation.

## Publish a GeoStory with a styled layer

1. Create a private Draft [Campaign](campaigns.md).
2. Add and publish the workspace/layer in GeoServer.
3. [Synchronize GeoServer](geodata.md#2-synchronize-geoserver).
4. Confirm the Layer is Published and Public.
5. Add the sprite if needed, create the Style, and create a Default Layer Style Assignment.
6. [Create the GeoStory](geostories.md), add the Layer, and leave Style assignment blank to use the default—or pin the intended assignment.
7. Add hero-image alt text and review content.
8. Publish the GeoStory, then make the Campaign Public when the collection is ready.

## Create a recurring Event series

1. Create or select the [Campaign](campaigns.md).
2. Confirm the [Event Type and taxonomy](event-taxonomy.md).
3. Create an Event Series with Series mode **Recurring**.
4. Set the recurrence type, interval, start date/time, timezone, and exactly one stopping condition.
5. For weekly recurrence, select weekdays. For monthly recurrence, complete the matching monthly rule.
6. Review generated occurrences, location mode, contact information, and inherited content.
7. Publish only the intended Events and then verify Campaign visibility.

## Replace the public Footer

1. Open or create a Footer draft.
2. Add active logos and documents with unique display orders.
3. Validate alt text, HTTPS destinations, document slugs, and content.
4. Save and review the draft.
5. Publish the replacement Footer.
6. Verify the live footer and document routes before considering deletion of the old Footer.

## Update a GeoServer layer safely

1. Check where the Layer is used and note its GeoStories, Events, and feedback relationships.
2. Make the provider change in GeoServer.
3. Run synchronization in Django.
4. Review publishing state, title/description, URL, styles, and errors.
5. Test representative GeoStories and Events.
6. If the change is incompatible, restore the provider configuration or use a new Layer rather than deleting a widely used one.

