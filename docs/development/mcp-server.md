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
| Redirect URIs | Claude: `https://claude.ai/api/mcp/auth_callback`. ChatGPT: the exact URI its connector page shows |
| Default client scopes | `roles`, the scope that carries `default_organization`, and a new scope with an **Audience mapper** |
| Audience mapper | "Included Custom Audience" = `MCP_PUBLIC_URL`, added to the access token |

Then set `MCP_TOKEN_AUDIENCES` to that audience. Until then the MCP server
accepts the same audiences as the API (`ALLOWED_TOKEN_AUDIENCES`), which
includes `account` by default, i.e. any token of the realm. Fine for a first
test, not for production.

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

Each map tool sends one command; `MAP_COMMANDS` in `tools/map.py` is the
complete list and the frontend's `mapCommands` table must match it (a test on
each side checks its own list).

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
| `list_workspaces`, `search_layers`, `describe_layer` | catalog lookup (paged search; attributes, extent, styles) |
| `connect_map`, `disconnect_map` | pair with / leave the user's tab |
| `get_map_state` | view and layers currently on the map, with filters |
| `set_map_view`, `fit_map_bounds`, `zoom_to_layer` | move the map |
| `add_catalog_layer`, `add_geojson_layer`, `remove_layer` | layers (GeoJSON: 5000 features / 1 MB) |
| `set_layer_visibility`, `set_layer_opacity` | layer display |
| `get_layer_attributes`, `add_attribute_filter`, `remove_attribute_filter` | attribute filters |

All tools have typed inputs and `readOnlyHint` annotations. Map-changing tools
are marked as not read-only: they change only what the user's own tab shows.

## Run locally

```bash
docker compose --profile mcp -f docker-compose-dev.yml --env-file .env.dev up -d mcp
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

1. `.env.prod`: `MCP_PUBLIC_URL`, `MCP_TOKEN_AUDIENCES`, `MCP_ALLOWED_HOSTS`,
   `MCP_BRIDGE_ALLOWED_ORIGINS` (see `.env.example`).
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
