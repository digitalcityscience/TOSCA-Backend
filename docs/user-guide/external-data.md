# How to offer external data (OGC API and SensorThings)

[User-guide home](README.md) · [Roles and permissions](permissions.md) · [Publishing and visibility](publishing.md) · [Troubleshooting](troubleshooting.md)

TOSCA can list data that other organizations already publish online — for example the
Hamburg OGC API (`https://api.hamburg.de/datasets/v1`) and the Hamburg SensorThings server
(`https://iot.hamburg.de`). You register the service once, load its catalog, and then build
**categories** (such as "Traffic situation" or "Bike & curbside") from its data. Users find
the categories in the Datastores sidebar, separate from TOSCA's own GeoServer data.

TOSCA stores only the description of what to list. The map fetches the data itself directly
from the external service.

> **Visibility is not data protection.** The external data is public at its source.
> "Private" and the switches below control what TOSCA lists and offers, not who can reach
> the data.

## 1. Register a service

1. Open **External catalog → External Services** and choose **Add**.
2. Fill in the fields below and save.

| Field | Meaning |
|---|---|
| Name / Slug | Admin label and stable id. Keep the slug unchanged once categories use the service. |
| Title | Name shown to users. |
| Service type | **OGC API Features** or **SensorThings**. |
| Base URL | The service's address (`https://…`). For a multi-dataset OGC catalog use the catalog root, e.g. `https://api.hamburg.de/datasets/v1`. |
| MQTT URL | SensorThings only: the live-update address (`wss://…`), e.g. `wss://iot.hamburg.de/mqtt`. |
| Attribution / Description | Source credit and text shown to users. |
| Visibility | **Public** (everyone) or **Private** (signed-in users only). New services are private. |
| Is active | Inactive services and their items are not listed to anyone. |

### What users may do

| Switch | Effect |
|---|---|
| Show uncurated | Also offer the service's complete dataset list ("All datasets"), not only your categories. Off by default. |
| Allow full load | Users may load all features of a layer at once. Turn off for very large services. |
| Allow live updates | SensorThings: values update live over MQTT. |
| Allow server filters | OGC: users may choose attributes and filter on the server. |
| Max features | Optional upper limit of features per load. Empty = app default. |

## 2. Load the service's catalog

Before you can pick data, TOSCA copies the list of everything the service offers — titles,
descriptions and themes (Transport, Environment, …) — into its own index.

- In **External Services**, select the service, choose the action **Load catalog**, and run
  it. Or press **Load catalog** inside a category's item picker.
- It runs in the background: Hamburg's OGC API (about 1,600 collections) takes roughly two
  minutes, SensorThings a few seconds. The service page shows the result under **Catalog**.
- Run **Update catalog** when the service has published new data or renamed datasets. Item
  titles in your categories are updated automatically.
- If some datasets could not be read, the load still finishes and lists them as a note.

## 3. Build a category

1. Open **External catalog → External Categories** and choose **Add**.
2. Enter **Title**, **Slug**, an optional **Description** and the **Display order** (lower
   numbers appear first in the sidebar).
3. Use the **Items** picker:
   - Choose the **service**, optionally a **theme**, and type in the search box to narrow
     the list on the left. Entries are grouped by dataset.
   - Select entries (Ctrl/Shift for several) and press **»** to add them. Double-click adds
     one entry.
   - **»»** adds *everything the filters currently show* — for example all collections of
     the Transport theme. You are asked to confirm large additions.
   - Switch to **Show datasets** to add all collections of a dataset in one go.
   - On the right, select items and press **«** to remove them (or **««** for all), and use
     **↑ / ↓** to change their order.
   - Entries marked **+** are added but not saved yet.
4. Set **Visibility** and **Is active**, then **Save**. All item changes are applied when you
   save.

Titles and descriptions of items come from the service and cannot be typed. Only collections
that can be drawn on the map (GeoJSON) are offered.

### Faster: a category from a theme

On the category list, choose **New category from theme**, pick the service and theme (for
example "Hamburg OGC — Transport (314 collections)") and press **Create category**. TOSCA
creates a **private, inactive** category with all of the theme's collections. Remove what
you do not need in its item picker, then make it active.

## 4. Adjust an item's map settings (optional)

Select a single saved item in the picker and follow **Map settings of the selected item**,
or open **External catalog → External Category Items**.

| Field | Meaning |
|---|---|
| Color | Default map colour. |
| Min zoom | Load features only from this zoom level on (useful for large datasets). |
| Default properties | OGC: attributes requested by default, e.g. `["name", "status"]`. Empty = all. |
| Default filter | OGC: initial filter, e.g. `[{"property": "breite", "operator": "gte", "value": 5}]`. Operators: `eq`, `neq`, `lt`, `lte`, `gt`, `gte`, `contains`. |

The source, title and description are read-only here.

## 5. Check availability

External services change without notice. The availability check confirms that every item
still exists and records how many features (OGC) or datastreams (SensorThings) it has.

- Select services, categories or items in their lists and run the action **Check
  availability**. It runs in the background (about half a second per item); reload the item
  list to see the results.
- Administrators can also schedule `manage.py check_external_catalog` (for example nightly).

| State | Meaning | What to do |
|---|---|---|
| Not checked yet | No check has run. | Run a check. |
| Available | The source exists; the count is current. | — |
| Missing at the service | The dataset, collection or layer is gone. | Remove the item, or find its replacement after **Update catalog**. |
| Check failed | The service did not answer or the collection cannot be drawn. Often temporary. | Check again later; read the error on the item. |

## Demo data (development)

Developers can create the two Hamburg services and ten demo categories (traffic, bike,
energy, …) in one step:
`manage.py seed_external_catalog_hamburg --organization <slug>` (add `--dry-run` to preview).
Everything it creates is private; running it again only adds what is missing.

## Who can do what

| Task | Needed |
|---|---|
| View services, categories and items | Reader |
| Add services and categories, add and reorder items, load catalogs, run checks | Writer |
| Remove items from a category, delete services or categories | Administrator |

Everything is limited to your organization: a category can only use services of the same
organization.

## Troubleshooting

| Problem | Check |
|---|---|
| The picker list is empty | Load the service's catalog (step 2). Collections without GeoJSON are never listed. |
| "Loading the catalog failed" | The service may be down or the base URL wrong. Open the base URL in a browser. |
| A category does not appear in the app | It must be active, have at least one item from an active service, and be public — or the user must be signed in. |
| An item shows as missing | The publisher removed or renamed it. Run **Update catalog**, then replace the item. |
| "You may not remove items from categories" | Removing items needs the administrator role. |
