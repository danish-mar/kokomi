import os
from contextlib import AsyncExitStack

from app.storage import load_mcp

# MCP SDK — optional dependency
try:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("⚠️  MCP SDK not installed. Tool calling disabled.")


async def connect_mcp_servers(stack: AsyncExitStack, server_ids: list):
    """Connect to one or more MCP servers; return (tool_defs, tool_sessions, errors)."""
    if not MCP_AVAILABLE or not server_ids:
        return [], {}, []

    servers = load_mcp()
    tool_defs: list = []
    tool_sessions: dict = {}
    errors: list = []

    for sid in server_ids:
        config = servers.get(sid)
        if not config or not config.get("enabled", True):
            continue

        # Use a local stack for each server to ensure partial failures are cleaned up immediately
        async with AsyncExitStack() as local_stack:
            try:
                transport_type = config.get("transport", "stdio")

                if transport_type == "stdio":
                    cmd = config.get("command", "")
                    args = config.get("args", [])
                    env_vars = config.get("env", {})
                    if not cmd:
                        continue
                    merged_env = {**os.environ, **env_vars} if env_vars else None
                    params = StdioServerParameters(
                        command=cmd,
                        args=args if isinstance(args, list) else args.split(),
                        env=merged_env,
                    )
                    read, write = await local_stack.enter_async_context(stdio_client(params))

                elif transport_type == "sse":
                    from mcp.client.sse import sse_client
                    url = config.get("url", "")
                    if not url:
                        continue
                    read, write = await local_stack.enter_async_context(sse_client(url))

                elif transport_type == "streamable-http":
                    from mcp.client.streamable_http import streamable_http_client
                    url = config.get("url", "")
                    if not url:
                        continue
                    streams = await local_stack.enter_async_context(streamable_http_client(url))
                    read, write = streams[0], streams[1]

                else:
                    continue

                session = await local_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()

                result = await session.list_tools()
                for tool in result.tools:
                    tool_defs.append({
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": tool.inputSchema if tool.inputSchema else {
                                "type": "object",
                                "properties": {},
                            },
                        },
                    })
                    tool_sessions[tool.name] = session
                
                # Transfer successfully opened resources to the main stack
                await stack.enter_async_context(local_stack.pop_all())
                print(f"  ✅ MCP '{config['name']}': {len(result.tools)} tools")

            except Exception as e:
                err_msg = f"MCP server '{config.get('name', sid)}' connection failed: {str(e)}"
                errors.append(err_msg)
                print(f"  ❌ {err_msg}")

    return tool_defs, tool_sessions, errors
