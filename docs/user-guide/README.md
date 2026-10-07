# TOSCA administration user guide

This guide is for content editors and geodata administrators working in the Django administration site.

## Start here

1. [Understand roles and permissions](permissions.md).
2. [Create and configure a Campaign](campaigns.md).
3. [Prepare map data, styles, and sprites](geodata.md).
4. [Create and publish a GeoStory](geostories.md).
5. [Create events and recurring event series](events.md).
6. [Configure and publish the footer](footer.md).
7. [Offer external data (OGC API, SensorThings)](external-data.md).

## Task-oriented guides

- [Quick-start recipes](quick-starts.md)
- [Publishing and visibility](publishing.md)
- [Using the rich-content editor](content-editor.md)
- [Event types and taxonomy](event-taxonomy.md)
- [Safe updates and deletion](safe-changes.md)
- [Troubleshooting](troubleshooting.md)
- [Maintaining this documentation](maintenance.md)

## How the features fit together

GeoServer is the source of truth for workspaces, stores, and layers. Synchronize those records into Django first. Styles and sprite assets are managed in Django, assigned to a synchronized layer, and can then be selected by a GeoStory. Events can also reference synchronized layers, but do not pin a style assignment.

```text
GeoServer workspace/layer
        │ synchronize
        ▼
Django layer ── layer/style assignment ── Django style ── optional sprite
        │                                      │
        ├──────── GeoStory layer ──────────────┘
        └──────── Event layer
```

The footer is independent of map content. It has its own draft-and-publish workflow.

External data (OGC API, SensorThings) is separate from GeoServer: you register a service,
load its catalog and build categories from it; the map reads the data from the external
service directly. See [Offer external data](external-data.md).

Campaigns are the visibility and ownership boundary for GeoStories and Events. Read [Publishing and visibility](publishing.md) before making content public.

## Publishing checklist

- Keep new content in **Draft** until links, dates, images, map layers, and accessibility text are checked.
- A layer must be both **Published** and **Public** before it can be attached to a GeoStory or Event.
- Give every image meaningful alternative text.
- Check display order values; lower numbers appear first.
- Save the object, reopen it, and verify inline records before publishing.

## Documentation status

Last verified: **2 October 2026**, against repository revision `cb53324`. See [Maintaining this documentation](maintenance.md) for the update checklist.

## Screenshots

The local admin requires an authenticated Keycloak session. No authenticated session was available when this guide was created, so screenshots are intentionally not committed. When adding them, place sanitized PNG files in `docs/user-guide/images/` using the filenames listed in each guide. Do not capture usernames, email addresses, access tokens, private campaign names, or production URLs.
