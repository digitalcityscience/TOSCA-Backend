# MCP server

Lets an AI assistant (ChatGPT, Claude) work with TOSCA for a signed-in user:
look up the catalog, and drive the map that is open in the user's browser.

Scope of this first version: MCP endpoint, read-only catalog tools, map tools.
Out of scope: multi-user sessions, shared map state, write tools (publishing,
styles, geostories), fetching external data.

Code: `tosca_api/apps/mcp_server/` (backend), `src/mcp/` and
`src/store/aiBridge.ts` (frontend).

## How it fits together

```
ChatGPT / Claude ──HTTPS──► /mcp  (MCP, Streamable HTTP)      ┐
                                                               ├─ one ASGI process
TOSCA tab ──WebSocket──► /mcp-bridge/ws  (map bridge)          ┘  (uvicorn, 1 worker)
                                   │
                                   └─ Django settings, ORM and services of the API
```

- **Own process, same code base.** The API runs on synchronous gunicorn
  workers, which cannot hold WebSockets. The MCP server is therefore a second
  process (`tosca_api.apps.mcp_server.asgi:application`) that loads the same
  Django project. The API process is not changed.
- **No models, no migrations.** Pairing state lives in the memory of that one
  process. This is why it must run with exactly one worker; see
  [Limits](#limits).

### What is reused

| Need | Existing code used |
|---|---|
| Verify the Keycloak token | `core.jwt_utils.verify_and_decode_token` |
| Roles and organization from the token | `authentication.role_sync` (`extract_roles_from_token`, `extract_org_from_token`, `org_role_level`) |
| What a caller may see | `catalog_api` `CatalogViewer` + `CatalogVisibilityService` (the service behind `/api/v1/catalog/`) |
| Map actions in the browser | the Pinia stores and helpers the UI itself calls (`map`, `filter`, `catalogLayerLoader`) |

The only change to shared code is an optional `audiences` argument on
`verify_and_decode_token`.

## Who may use it

The caller is the Keycloak user of the access token. Nothing is stored: roles
and organization are read from the token on every request, as the API does.

- Needs at least `MCP_MIN_ROLE_LEVEL` (default `READER`) in the user's default
  organization, i.e. a `ROLE_<ORG>_READER|WRITER|ADMIN` realm role.
  `DJANGO_SUPERADMIN` passes, as in the API.
- A user without such a role gets a clear tool error, not a login loop.
- A Keycloak identity linked to a deactivated Django user is refused.
- Unlike the API, a token never creates or changes a Django user here.

Catalog answers follow the catalog API exactly: public layers for everyone,
plus the published layers of the caller's own organization. Another
organization's private layer is "not found", never "forbidden".

## Keycloak setup (required before ChatGPT/Claude can connect)

The server only *verifies* tokens. Signing in happens between the assistant
and Keycloak; the server points clients there with OAuth protected-resource
metadata (RFC 9728) at `/.well-known/oauth-protected-resource/mcp`.

Checked on the `tosca-dev` realm (public metadata, 2026-11-26): PKCE S256 is
offered, authorization-server metadata is served, a dynamic registration
endpoint exists. Resource indicators (RFC 8707) and Client ID Metadata
Documents are not announced.

Recommended: **one pre-registered public client** (for example `tosca-mcp`),
entered by hand in the connector dialog of ChatGPT/Claude. It needs no realm
policy change and keeps roles under control.

| Setting on the client | Value |
|---|---|
| Client authentication | off (public client), standard flow, PKCE S256 |
| Redirect URIs | Claude Code: `http://localhost:<callback port>/callback`. claude.ai: `https://claude.ai/api/mcp/auth_callback`. ChatGPT: the exact URI its connector page shows |
| Default client scopes | `roles` (puts `realm_access.roles` into the token) and a new scope with an **Audience mapper**. A `default_organization` claim is optional: without it the organization comes from the user's `ROLE_<ORG>_*` roles when they are all in one organization (`role_sync._extract_org_from_payloads`) |
| Audience mapper | "Included Custom Audience" = `MCP_PUBLIC_URL`, added to the access token |

Then set `MCP_TOKEN_AUDIENCES` to that audience. Until then the MCP server
accepts the same audiences as the API (`ALLOWED_TOKEN_AUDIENCES`), which
includes `account` by default, i.e. any token of the realm. Fine for a first
test, not for production.

### Step by step

This setup was tested with Claude Code against a production-like realm. The
names below are examples; use your own. Placeholders:

| Placeholder | Meaning | Example |
|---|---|---|
| `<keycloak>` | Keycloak base URL | `https://auth.example.com` |
| `<realm>` | Realm the TOSCA API already uses | `tosca` |
| `<client-id>` | New public client for MCP | `tosca-mcp` |
| `<mcp-url>` | Public MCP endpoint = `MCP_PUBLIC_URL` | local: `http://localhost:8001/mcp`, prod: `https://api.example.com/mcp` |
| `<callback-port>` | Free local port for Claude Code's sign-in callback | `8765` (any free port; 8080 is often taken) |

**1. Create the client.** Keycloak admin console → realm `<realm>` →
*Clients* → *Create client*:

- *Client type*: OpenID Connect. *Client ID*: `<client-id>`. Next.
- *Client authentication*: **Off** (public client, no secret).
- *Authentication flow*: **Standard flow** on. Direct access grants, implicit
  flow and service accounts off. Next.
- *Valid redirect URIs*: one line per client that will sign in:
  - Claude Code: `http://localhost:<callback-port>/callback`
  - claude.ai: `https://claude.ai/api/mcp/auth_callback`
  - ChatGPT: the exact URI shown on its connector page
- *Web origins*: leave empty (the assistants do the token exchange server-side
  or from a local callback, not from a browser page on our origin). Save.

**2. Require PKCE.** Client `<client-id>` → *Advanced* → *Advanced settings*
→ *Proof Key for Code Exchange Code Challenge Method*: **S256**. Save.

**3. Add the audience.** Client `<client-id>` → *Client scopes* →
`<client-id>-dedicated` → *Configure a new mapper* → **Audience**:

- *Name*: `mcp-audience`
- *Included Client Audience*: empty
- *Included Custom Audience*: `<mcp-url>` (exactly as `MCP_PUBLIC_URL`, no
  trailing slash)
- *Add to access token*: **On**. *Add to ID token*: Off. Save.

A separate shared client scope with the same mapper works too, if several
clients need it.

**4. Check the role claims.** Client `<client-id>` → *Client scopes*: `roles`
must be listed as **Default**. Without it the token has no
`realm_access.roles` and every call is refused for missing role. If the realm
maps an organization claim (`default_organization`), add that scope as well;
it is optional (see the table above).

**5. Configure the MCP server.** In the env file of the MCP process:

```bash
MCP_PUBLIC_URL=<mcp-url>
MCP_TOKEN_AUDIENCES=<mcp-url>
```

The Keycloak issuer and JWKS come from the settings the API already uses; no
MCP-specific Keycloak setting is needed. Restart the MCP process.

**6. Point the client at it.**

- Claude Code, project `.mcp.json`:

  ```json
  {
    "mcpServers": {
      "tosca": {
        "type": "http",
        "url": "<mcp-url>",
        "oauth": { "clientId": "<client-id>", "callbackPort": <callback-port> }
      }
    }
  }
  ```

  Then run `/mcp` in Claude Code, choose `tosca` → *Authenticate*. The browser
  opens the Keycloak login page of `<realm>`.
- claude.ai: *Settings → Connectors → Add custom connector*, URL `<mcp-url>`,
  under *Advanced settings* OAuth Client ID `<client-id>` (no secret).
- ChatGPT: same idea in its connector dialog; add its redirect URI to step 1.

**7. Verify.**

- Call the `whoami` tool. It should show your Keycloak user, organization and
  role level.
- If sign-in works but calls are refused, decode the access token (e.g. on a
  local JWT decoder) and check: `aud` contains `<mcp-url>`, `iss` is
  `<keycloak>/realms/<realm>`, `realm_access.roles` contains a
  `ROLE_<ORG>_*` role of at least `MCP_MIN_ROLE_LEVEL`.
- `<mcp-url>` must match in three places: `MCP_PUBLIC_URL`,
  `MCP_TOKEN_AUDIENCES` and the audience mapper. A trailing slash or
  `http`/`https` mismatch is the usual cause of "invalid audience".

Common errors:

| Symptom | Cause |
|---|---|
| Keycloak shows "Invalid parameter: redirect_uri" | The callback URI (port included) is not in *Valid redirect URIs* |
| Claude Code callback fails to start | `<callback-port>` is in use; pick another and update step 1 |
| 401 / invalid audience | Audience mapper missing, not on the access token, or URL differs from `MCP_TOKEN_AUDIENCES` |
| Signed in, but "no role" / forbidden | `roles` scope not Default on the client, or user has no `ROLE_<ORG>_*` role |

Alternative: anonymous dynamic client registration. It needs realm policy
changes (trusted hosts, allowed client scopes), and dynamically registered
clients have "full scope allowed" off, so role claims may be missing from
their tokens. Not recommended.

## Pairing the map

1. The signed-in user chooses **Connect AI assistant** in the account menu.
   The tab opens the WebSocket, sends its access token, and gets a code such as
   `K7QM-3XTP`.
2. The user tells the code to the assistant, which calls `connect_map(code)`.
3. From then on the map tools of that user go to that tab, until the user or
   the assistant disconnects, the tab closes, or the user logs out.

Rules enforced by `bridge.py`:

- a code works **once**, for `MCP_PAIRING_CODE_TTL_SECONDS` (5 min);
- a code works only for **the same Keycloak user** (`sub`) that is signed in
  to the tab. Another user's code is answered like an unknown code;
- five wrong codes lock that caller's pairing for five minutes;
- one paired tab per user; pairing another tab replaces the first;
- the tab must keep a valid access token (it sends refreshed tokens); a tab
  whose token expired gets no commands.

## Commands

Each map tool sends one command. The commands are defined once, in the
frontend: `src/mcp/commandCatalog.ts` (TOSCA-2) holds name, description,
parameter schema (JSON Schema), effect and wait time of every command, and
`mapCommands` in `src/mcp/commands.ts` holds their code (a frontend test keeps
the two in step).

The server reads an export of that list,
`tosca_api/apps/mcp_server/map_commands.json`, and at startup builds one MCP
tool per entry (`tools/map.py`, `command_tools`):

| From the entry | Tool |
|---|---|
| `name` | tool name = snake_case (`setLayerOpacity` → `set_layer_opacity`); the command name on the bridge stays camelCase |
| `title`, `description` | as given |
| `params` | input schema as given; arguments are checked with `jsonschema` before the browser is asked |
| `effect` | `read` → read-only; `view` → not read-only, idempotent, not destructive; `add` → not read-only, not idempotent |
| `timeoutSeconds` | how long the server waits for the browser |

The file is also the bridge's allow-list: a command that is not in it is never
sent. Rules that only the server can enforce are small hooks in
`tools/map.py` (`SERVER_RULES`; today: `addGeoJsonLayer` at most 5000 features
and 1 MB). `connect_map` and `disconnect_map` are written by hand; they control
the bridge and are not map commands.

A missing file, another export `format`, a malformed entry (bad JSON Schema,
unknown effect, duplicate name, ...) or a file without a command that
`tools/map.py` relies on stops the server at startup.

**Sync step.** After changing `commandCatalog.ts`, export it into this repo and
commit the JSON together with the backend change:

```bash
# in the TOSCA-2 checkout
npm run mcp:export -- ../TOSCA-Backend/tosca_api/apps/mcp_server/map_commands.json
```

Then run `tests/test_map_commands.py` and `tests/test_server.py`. A new command
needs a sample call in `test_every_map_command_is_a_tool_that_sends_it`. The
two repositories are not checked against each other by a test; the export is
the only link.

Descriptions in the catalog name other tools by their MCP tool name
(`get_map_state`, `search_layers`), since that is what the assistant sees; a
test on each side refuses camelCase command names in descriptions.

```json
{"type": "tosca:command", "requestId": "…", "method": "setLayerOpacity",
 "params": {"layerId": "bicycle", "opacity": 0.5}, "origin": "mcp"}
{"type": "tosca:result", "requestId": "…", "ok": true, "value": {"…": "…"}}
```

- The answer with the same `requestId` resolves the tool call; the value the
  browser returns is the tool result. An error from the map is a tool error.
- A command is sent once. The server never repeats it. The browser remembers
  recent ids and would answer a repeated id without running it again.
- No answer within the command's timeout: the tool fails and says that the
  command may still have been applied.
- Tab disconnects: waiting commands fail at once. The tab reconnects by itself
  and keeps its pairing for `MCP_BRIDGE_RESUME_SECONDS` (2 min).

The full message list is in the docstring of `bridge_ws.py`.

## Tools

| Tool | Does |
|---|---|
| `whoami` | account, organization, role of this connection |
| `list_geostories` | geostories the user may open (same visibility as `GET /api/v1/stories/`), for `open_geostory` |
| `list_workspaces`, `search_layers`, `describe_layer` | catalog lookup (paged search; attributes, extent, styles) |
| `list_sources`, `search_sources`, `describe_source_item` | further data catalogs (see "Catalog sources") |
| `connect_map`, `disconnect_map` | pair with / leave the user's tab |
| `get_map_state` | view and layers currently on the map, with filters |
| `set_view`, `fit_bounds`, `zoom_to_layer`, `zoom_to_features` | move the map (`set_view` with bearing / pitch; `zoom_to_features`: features matching `attribute == value`, e.g. a district) |
| `set_basemap`, `set_terrain` | background map (ids in `get_map_state`), 3D terrain |
| `add_catalog_layer`, `add_catalog_layer_group`, `add_geo_json_layer`, `remove_layer` | layers (raster layers take a `time`; GeoJSON: 5000 features / 1 MB) |
| `add_ogc_collection`, `set_ogc_layer_query`, `reset_ogc_layer_query` | external OGC API Features collections and their server-side query |
| `list_sensor_things_layers`, `add_sensor_things_layer` | external SensorThings layers (live values) |
| `open_geostory` | open a geostory; it replaces the map |
| `set_layer_visibility`, `set_layer_opacity`, `move_layer`, `set_layer_style`, `set_layer_color`, `set_layer_time` | layer display (style ids in `get_map_state`) |
| `get_layer_legend`, `identify_at`, `get_drawn_features` | legend, what is drawn at a point in view, the user's drawings |
| `create_buffer` | buffer polygon layer around a GeoJSON layer; can be the area of `apply_geometry_filter` |
| `get_layer_attributes`, `get_layer_features` | read a layer (features: catalog layers as drawn in the current view after their filters, GeoJSON layers in full; paged, max 1 MB per page) |
| `add_attribute_filter`, `remove_attribute_filter`, `set_filter_relation`, `clear_layer_filters` | attribute filters (AND / OR as in the filter panel) |
| `apply_geometry_filter`, `remove_geometry_filter` | area filter: the area is a GeoJSON polygon layer (drawn, or sent with `add_geo_json_layer`), whole or its features matching `attribute == value`; the map moves to the area first, since only drawn features count |

Feature tools read only what the browser already holds: GeoJSON layer data and
the vector tiles MapLibre has loaded. Nothing asks GeoServer for features
(no WFS, no WMS GetMap as GeoJSON); TOSCA talks to GeoServer only for map
tiles, and every filter runs in the browser. So for catalog layers the answer
depends on the map view: `zoom_to_features` searches the loaded tiles (and
shows the layer's whole extent once if nothing matches), and
`get_layer_features` returns the features drawn in the current view
(`scope: "view"`). Features cut at tile edges are joined again by their
`gid` / `id`. A catalog layer cannot be the area of a geometry filter: tiles
cut and simplify geometries. The assistant sends the area itself instead,
e.g. a 100 m buffer polygon it computed around a place.

The map commands live only in the frontend's `src/mcp/` folder. They call the
filter store's existing functions and change nothing in the store or the
filter panels; known bugs there are filed as issues instead.

Map tools (all but `connect_map` / `disconnect_map`) come from
`map_commands.json` (see "Commands"); their parameters are camelCase, exactly
what the browser receives (`layerId`, `providerId`, `layerName`, ...).

All tools have typed inputs and `readOnlyHint` annotations. Map-changing tools
are marked as not read-only: they change only what the user's own tab shows.

## Catalog sources

Besides this server's own catalog, the assistant can search further data
catalogs. Each one is registered as a file in
`tosca_api/apps/mcp_server/catalog_sources/`; adding a catalog of a known
protocol needs no code.

**Rules**

1. One source = `<id>.json`, plus an optional `<id>.md`.
2. Field names follow STAC, in plain words. The source describes itself; we only
   say where it is and how to read it.

   | Field | Required | Meaning |
   |---|---|---|
   | `id` | yes | lowercase with hyphens, equal to the file name |
   | `title` | yes | readable name |
   | `description` | yes | one or two sentences: what is there, which area |
   | `url` | yes | entry point; may be `${ENV_VAR}` (source left out while unset) |
   | `protocol` | yes | how to read it: `tosca-catalog`, `ogc-api-features`, `sensorthings` |
   | `extent.bbox` | recommended | covered area, WGS84 `[west, south, east, north]` |
   | `license` | if the source does not state it | SPDX id, e.g. `CC-BY-4.0` |
   | `keywords` | no | a few search words |
   | `providers` | no | STAC providers: `name` + `roles` (producer, licensor, host) |
   | `priority` | no | lower is searched first (default 100) |
   | `agents` | no | link to the source's own `AGENTS.md`, if it has one |

3. Do not copy what the source already publishes (collections, columns, counts,
   example queries). The assistant reads that from the source.
4. `<id>.md` holds only what the source does not say, in the style of a
   Portolan `AGENTS.md`: what it is for, search language, pitfalls, how it joins
   other data. A few lines.
5. Only public catalogs. The server reads only the registered `url`'s host,
   through the guarded client of `external_catalog.remote` (public addresses,
   bounded time and size). Lists are cached for an hour.

New protocols (STAC / Portolan catalogs, STAC API) need an adapter in
`mcp_server/sources.py`.

**Registered now**

| id | protocol | note |
|---|---|---|
| `ngcn` | `tosca-catalog` | the public NGCN TOSCA platform; `url` from `MCP_SOURCE_NGCN_URL`. Leave it unset on that platform itself. Results carry `show_on_map` for `add_catalog_layer` |
| `hamburg-ogc` | `ogc-api-features` | Hamburg's multi-API catalog; a hit is a dataset, `describe_source_item` lists its collections; GeoJSON collections carry `show_on_map` for `add_ogc_collection` (`serviceId` = source id, which must equal the external service's slug) |
| `hamburg-sensorthings` | `sensorthings` | Hamburg's Urban Data Platform (`https://iot.hamburg.de`); a hit is a `serviceName` / `layerName` pair (the external catalog's `sta_layers`, cached a day) with `show_on_map` for `add_sensor_things_layer` (`serviceId` = source id = the external service's slug) |

## Run locally

```bash
ENV_FILE=.env.development docker compose --profile mcp -f docker-compose-dev.yml --env-file .env.development up -d mcp
# or without Docker:
uvicorn tosca_api.apps.mcp_server.asgi:application --port 8001 --ws-max-size 8388608
```

Frontend: `VITE_MCP_BRIDGE_URL="ws://localhost:8001/mcp-bridge/ws"`.
With `DJANGO_DEBUG=true` and no `MCP_BRIDGE_ALLOWED_ORIGINS`, any local origin
may open the bridge.

Try the tools with the MCP Inspector or Claude Code, passing a Keycloak access
token of a test user as `Authorization: Bearer …`.

Tests: `tosca_api/apps/mcp_server/tests/`.

## Deploy

Opt-in, nothing changes until it is switched on:

1. `.env.production`: `MCP_PUBLIC_URL`, `MCP_TOKEN_AUDIENCES`, `MCP_ALLOWED_HOSTS`,
   `MCP_BRIDGE_ALLOWED_ORIGINS`, `MCP_SOURCE_NGCN_URL` (see `.env.production.example`).
2. `docker compose --profile mcp … up -d mcp`.
3. Add the three locations of `docker/nginx/mcp.conf.example` to the public
   proxy (`/mcp`, `/.well-known/oauth-protected-resource/mcp`,
   `/mcp-bridge/ws` with WebSocket upgrade).
4. Frontend build: `VITE_MCP_BRIDGE_URL=wss://<host>/mcp-bridge/ws`.

ChatGPT and Claude call the endpoint from their own servers, so it must be
reachable from the internet over HTTPS, and Keycloak must be reachable too.

## Security notes

- **Tenants:** every catalog answer goes through `CatalogVisibilityService`
  with the caller's organization. Map commands run in the user's browser with
  the user's own session, so they can load only what the user could load.
- **No open proxy:** there is no tool that fetches a URL, so the server cannot
  be used to reach internal addresses. (`fetch_ogc_features` from the plan is
  not part of this version; when it comes it must be limited to the curated
  external services.)
- **Fixed command list:** the browser runs only named commands with validated
  parameters; unknown names are refused on both sides.
- **Prompt injection:** layer titles, descriptions and attribute values are
  written by other people and reach the model as tool output. The server
  instructions tell the model to treat them as data. Because no tool writes
  data or reaches other systems, the worst a hostile text can do is change what
  the user's own map shows. Keep this in mind before adding write tools.
- **Answers carry no secrets:** no store credentials, no internal GeoServer
  addresses (tested).

## Limits

- **One process, one worker.** Pairing state is in memory. With two workers
  the assistant's request and the tab's WebSocket could land in different
  processes. When Redis is available, `bridge.py` is the one module to change.
  A restart of the process drops all pairings; tabs reconnect and show a new
  code.
- **Raster layers** are added without a time selection.
- **Legacy GET stream:** for clients on the older protocol the SDK keeps a
  `GET /mcp` event stream open. It carries nothing here and the proxy closes it
  at its read timeout.

## Verified, and not yet verified

Verified locally (automated tests, plus a run with a real uvicorn server, the
frontend's real bridge client over a real WebSocket and the SDK's own MCP
client): 401 with the metadata pointer, metadata document, initialize, tool
listing, role gate, catalog visibility, pairing rules, command round trips,
20 parallel commands, disconnect and resume.

Not verified, needs the real services:

- sign-in from **claude.ai** and **ChatGPT** against the real Keycloak
  (redirect URIs, scopes, whether role and organization claims arrive in the
  token of the MCP client);
- whether ChatGPT/Claude send an `Origin` header (then it must be listed in
  `MCP_ALLOWED_ORIGINS`);
- which ChatGPT plans may call the map tools (they are not read-only; OpenAI
  documents write tools for Business/Enterprise only);
- the production container and proxy routes (written, not run);
- the user interface in a browser with a real login.
