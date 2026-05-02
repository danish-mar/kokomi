import os
import asyncio
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

# Timeout for connecting to and initializing each MCP server (seconds)
MCP_CONNECT_TIMEOUT = 15
# Timeout for individual tool calls (seconds)
MCP_TOOL_CALL_TIMEOUT = 30


async def _connect_single_server(stack: AsyncExitStack, sid: str, config: dict):
    """Connect to a single MCP server with timeout protection.
    Returns (tool_defs, tool_sessions, tool_icons, error_or_none).
    """
    tool_defs = []
    tool_sessions = {}
    tool_icons = {}

    transport_type = config.get("transport", "stdio")
    name = config.get("name", sid)

    try:
        async with AsyncExitStack() as local_stack:
            # --- Establish transport with timeout ---
            if transport_type == "stdio":
                cmd = config.get("command", "")
                args = config.get("args", [])
                env_vars = config.get("env", {})
                if not cmd:
                    return [], {}, {}, None
                merged_env = {**os.environ, **env_vars} if env_vars else None
                params = StdioServerParameters(
                    command=cmd,
                    args=args if isinstance(args, list) else args.split(),
                    env=merged_env,
                )
                read, write = await asyncio.wait_for(
                    local_stack.enter_async_context(stdio_client(params)),
                    timeout=MCP_CONNECT_TIMEOUT,
                )

            elif transport_type == "sse":
                from mcp.client.sse import sse_client
                url = config.get("url", "")
                if not url:
                    return [], {}, {}, None
                read, write = await asyncio.wait_for(
                    local_stack.enter_async_context(sse_client(url)),
                    timeout=MCP_CONNECT_TIMEOUT,
                )

            elif transport_type == "streamable-http":
                from mcp.client.streamable_http import streamable_http_client
                url = config.get("url", "")
                if not url:
                    return [], {}, {}, None
                streams = await asyncio.wait_for(
                    local_stack.enter_async_context(streamable_http_client(url)),
                    timeout=MCP_CONNECT_TIMEOUT,
                )
                read, write = streams[0], streams[1]

            else:
                return [], {}, {}, None

            # --- Initialize session with timeout ---
            session = await asyncio.wait_for(
                local_stack.enter_async_context(ClientSession(read, write)),
                timeout=MCP_CONNECT_TIMEOUT,
            )
            await asyncio.wait_for(session.initialize(), timeout=MCP_CONNECT_TIMEOUT)

            # --- List tools with timeout ---
            result = await asyncio.wait_for(session.list_tools(), timeout=MCP_CONNECT_TIMEOUT)

            for tool in result.tools:
                schema = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
                if "properties" not in schema:
                    schema["properties"] = {}

                schema["properties"]["ui_status_text"] = {
                    "type": "string",
                    "description": "A short, present-tense, human-readable status message for the UI describing what you are doing (e.g. 'Searching and playing All of us are dead...'). MUST be provided whenever you call this tool."
                }
                if "required" not in schema:
                    schema["required"] = []
                if "ui_status_text" not in schema["required"]:
                    schema["required"].append("ui_status_text")

                tool_defs.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": schema,
                    },
                })
                tool_sessions[tool.name] = session
                tool_icons[tool.name] = config.get("icon") or "fa-plug"

            # Transfer to main stack on success
            await stack.enter_async_context(local_stack.pop_all())
            print(f"  ✅ MCP '{name}': {len(result.tools)} tools")
            return tool_defs, tool_sessions, tool_icons, None

    except asyncio.TimeoutError:
        err = f"MCP server '{name}' timed out after {MCP_CONNECT_TIMEOUT}s"
        print(f"  ⏱️ {err}")
        return [], {}, {}, err
    except Exception as e:
        err = f"MCP server '{name}' connection failed: {str(e)}"
        print(f"  ❌ {err}")
        return [], {}, {}, err


async def connect_mcp_servers(stack: AsyncExitStack, server_ids: list):
    """Connect to one or more MCP servers concurrently with timeouts.
    Returns (tool_defs, tool_sessions, tool_icons, errors).
    """
    if not MCP_AVAILABLE or not server_ids:
        return [], {}, {}, []

    servers = load_mcp()
    all_tool_defs = []
    all_tool_sessions = {}
    all_tool_icons = {}
    all_errors = []

    # Build list of servers to connect
    tasks = []
    for sid in server_ids:
        config = servers.get(sid)
        if not config or not config.get("enabled", True):
            continue
        tasks.append(_connect_single_server(stack, sid, config))

    if not tasks:
        return [], {}, {}, []

    # Connect to all servers concurrently (not sequentially!)
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            all_errors.append(f"Unexpected error: {str(result)}")
            continue
        defs, sessions, icons, err = result
        all_tool_defs.extend(defs)
        all_tool_sessions.update(sessions)
        all_tool_icons.update(icons)
        if err:
            all_errors.append(err)

    return all_tool_defs, all_tool_sessions, all_tool_icons, all_errors
