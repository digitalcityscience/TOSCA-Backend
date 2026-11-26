"""MCP server for TOSCA.

One MCP endpoint (Streamable HTTP) plus the WebSocket bridge to the user's open
map. It runs as its own ASGI process (``asgi.py``) because the API is served by
synchronous gunicorn workers, which cannot hold WebSockets.

Reused from the existing backend, not reimplemented here:

* Keycloak token verification: ``core.jwt_utils.verify_and_decode_token``;
* roles and organization from the token: ``authentication.role_sync``;
* what a caller may see: ``catalog_api`` ``CatalogViewer`` /
  ``CatalogVisibilityService`` (same rule as the public catalog API).

No models and no migrations: pairing state lives in the process memory.
"""
