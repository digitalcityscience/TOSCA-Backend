"""ASGI entry point of the MCP server.

Run it as ONE process with ONE worker: pairing state is kept in memory
(see ``bridge.py``).

    uvicorn tosca_api.apps.mcp_server.asgi:application --host 0.0.0.0 --port 8001 \\
        --proxy-headers --ws-max-size 8388608
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tosca_api.settings.production")
django.setup()

from .server import build_app  # noqa: E402  (Django must be set up first)

application = build_app()
