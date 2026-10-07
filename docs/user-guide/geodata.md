# How to prepare geodata, styles, and sprites

[User-guide home](README.md) · [Permissions](permissions.md) · [Use a layer in a GeoStory](geostories.md#add-map-layers) · [Use a layer in an Event](events.md#add-map-layers) · [Safe changes](safe-changes.md)

## Intended workflow

Add and publish workspaces, stores, and layers in GeoServer. In Django, configure the GeoServer connection and run synchronization to import them. Do not manually recreate provider-owned layer records. Create style and sprite content in Django, then attach a style to a layer with a Layer Style Assignment.

## 1. Configure the geodata engine

Open **Geodata providers → Geodata engines**. Normally an administrator configures this once.

| Field | What it means | What to enter |
|---|---|---|
| Name | Label used inside Django. | A recognizable name such as `Production GeoServer`. Must be unique. |
| Description | Internal explanation of the service. | Purpose, environment, and ownership. |
| Engine type | Kind of provider. | Select **GeoServer** for this workflow. |
| Base URL | Internal URL Django uses to contact the service. | The backend/container URL, not the browser-facing URL. |
| Public URL | URL exposed to frontend clients. | The externally reachable GeoServer base URL. |
| Admin username | Provider account used for synchronization. | A GeoServer service/admin user. |
| Admin password | Password for that account. | The current secret; it is encrypted when stored. |
| API key | Token for engines that require one. | Leave blank for a normal username/password GeoServer setup. |
| Active | Whether Django may use this engine. | Enable for an operational engine. |
| Default | Preferred engine when no engine is specified. | Enable on only the intended default. |
| Organizations | Limits which organizations may see the engine. | Leave empty for unrestricted platform use, or select allowed organizations. |
| Created by | Audit owner. | Usually filled automatically from the logged-in user. |

Keep the Base URL, Public URL, and credentials distinct. A container hostname may work from Django but not from a user's browser.

## 2. Synchronize GeoServer

1. In GeoServer, finish creating and publishing the workspace, store, and layer.
2. In Django, open the configured geodata engine.
3. Run the available synchronization action for the engine. Synchronize workspaces before relying on layers.
4. Review the imported **Workspaces**, **Stores**, and **Layers**.
5. Resolve any failed sync state or provider error before using the layer in content.

Synchronization imports provider state. Re-running it is the normal way to pick up GeoServer changes. Django-authored layer descriptions and style relationships are preserved rather than replaced by provider text.

### Workspace fields

| Field | Meaning |
|---|---|
| Geodata engine | Provider from which this workspace was synchronized. |
| Organization | Owning organization; also drives GeoServer access-control roles. |
| Name | GeoServer namespace. It must be unique within the engine. |
| Description | Human-readable purpose of the workspace. |
| Visibility | **Private** means owner organization only. **Public** allows anonymous read and owner-organization write. |
| Created by | Audit owner, normally set automatically. |

## 3. Review and configure a layer

Open **Geodata providers → Layers**, then select the synchronized layer.

| Field | Meaning and guidance |
|---|---|
| Name | Provider layer identifier. Treat it as provider-owned. |
| Title | Human-readable public label. |
| Description content | Rich public description authored in TOSCA. |
| Description | Generated plain-text form of the rich description. |
| Provider description | Last text seen in GeoServer; read-only and never overwrites authored content. |
| Workspace | Namespace containing the layer. |
| Store | Backing store. It must belong to the selected workspace. |
| Table name | Backing PostGIS table or view name. |
| Geometry column | Geometry field, normally `geom`. |
| Geometry type | Point, line, polygon, multi-geometry, or collection type. |
| SRID | Coordinate reference identifier, commonly `4326`. |
| Publishing state | Draft, Published, Failed, or Unpublished provider state. |
| Public | Allows unauthenticated listing and retrieval. Required for GeoStory/Event use. |
| Queryable | Allows WMS feature queries. |
| Opaque | Tells WMS clients the layer fully covers layers below it. Enable only when true. |
| Published URL | WMS/WFS endpoint recorded after publication. |
| Publishing error | Last provider publication error. Fix it before attaching the layer. |
| Published at | Last successful publication time. |
| Created by | Audit owner. |

Only a layer that is both **Published** and **Public** can be selected in a GeoStory or Event.

## 4. Add a sprite asset when an MBStyle uses icons or patterns

Open **Geodata providers → Sprite assets → Add**.

| Field | Meaning and guidance |
|---|---|
| Geodata engine | Engine with which the sprite will be used. |
| Workspace | Optional scope. Leave blank for a global sprite; otherwise it must belong to the same engine. |
| Name | Unique name within the engine and scope. |
| Image | Required 1× PNG sprite sheet. |
| Index content | Required non-empty MapLibre sprite-index JSON. Each entry needs `width`, `height`, `x`, `y`, and `pixelRatio: 1`. |
| Image 2x | Optional high-resolution PNG. |
| Index content 2x | Required when Image 2x is supplied, with matching sprite names and `pixelRatio: 2`. |
| Validation state/errors | System-maintained result of validation. |
| Created by | Audit owner. |

Sprite names cannot contain `:`. Index rectangles must stay within the PNG bounds. The 1× and 2× indexes must contain the same names and logical dimensions.

## 5. Add a style in Django

Open **Geodata providers → Styles → Add**.

| Field | Meaning and guidance |
|---|---|
| Geodata engine | Target provider. |
| Workspace | Optional workspace scope; blank creates a global engine style. |
| Name | GeoServer style identifier. |
| Title | Human-readable label shown to editors. |
| Description | Explain the visual purpose and intended datasets. |
| Format | **SLD** or **MBStyle**. |
| File name | Original uploaded filename, when local style content exists. |
| File content | Raw SLD XML or MBStyle JSON. |
| Sprite asset | Optional for MBStyle, forbidden for SLD. Required when the MBStyle uses icons or patterns. |
| Validation state/errors | Whether the content passed validation and any problems found. |
| Remote state/error | Whether upload/synchronization to the provider succeeded. |
| Remote uploaded/verified at | System timestamps for provider state. |
| Created by | Audit owner. |

The style and sprite must use the same engine. A workspace-scoped style may use a global sprite or a sprite from that same workspace. The sprite index must contain every static icon/pattern name referenced by the MBStyle.

## 6. Link the style to a layer

Open **Geodata providers → Layer Style Assignments → Add**, or use the assignment inline on a layer when available.

| Field | Meaning and guidance |
|---|---|
| Layer | Synchronized data layer to render. |
| Style | Valid style to apply. |
| Role | **Default** is used automatically; **Alternate** offers another rendering choice. |
| Active | Whether the assignment is available. |
| Style layer IDs | For MBStyle, ordered rule IDs from the style document that render this data layer. Leave empty for SLD. |
| Created by | Audit owner. |

A layer may have only one active default assignment. Invalid styles cannot be assigned. For a simple MBStyle, Django may infer rule IDs; for a multi-source style, select the applicable rule IDs explicitly.

After saving, return to the layer and verify the default assignment. It will become the automatic choice when a GeoStory layer is added without an explicit style.

## Troubleshooting

- **Layer is absent from GeoStory/Event choices:** confirm sync completed, Publishing state is Published, and Public is enabled.
- **Style is unavailable:** fix validation errors and confirm engine/workspace scope matches the layer.
- **MBStyle refuses to save:** attach a compatible sprite and check every referenced image name.
- **A second default fails:** change the old assignment to Alternate or inactive before making the new one Default.
- **GeoServer changes do not appear:** rerun engine synchronization and review the sync/provider error fields.

## Suggested screenshots

- `images/geodata-engine-sync.png`: engine change page with synchronization controls, with credentials redacted.
- `images/layer-publication.png`: layer page showing Publishing state and Public.
- `images/style-and-sprite.png`: MBStyle page showing Sprite asset.
- `images/layer-style-assignment.png`: assignment inline showing Default/Alternate.
