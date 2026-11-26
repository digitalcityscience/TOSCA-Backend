from mcp.server.mcpserver import MCPServer

from . import catalog, map


def register_tools(server: MCPServer) -> None:
    catalog.register(server)
    map.register(server)
