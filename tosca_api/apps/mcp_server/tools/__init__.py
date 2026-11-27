from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.tools import Tool

from ..map_commands import MapCommand
from . import catalog, external_data, geostories, map, publishing, sources


def command_tools(map_commands: dict[str, MapCommand]) -> list[Tool]:
    """Tools built from data files; they go to ``MCPServer(tools=...)``."""
    return map.command_tools(map_commands)


def register_tools(server: MCPServer, map_commands: dict[str, MapCommand]) -> None:
    """Tools written as Python functions."""
    catalog.register(server)
    geostories.register(server, map_commands)
    sources.register(server)
    publishing.register(server)
    external_data.register(server, map_commands)
    map.register(server, map_commands)
