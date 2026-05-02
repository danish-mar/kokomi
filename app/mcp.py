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

# Fast timeout for connecting to and initializing each MCP server sequentially (seconds)
MCP_CONNECT_TIMEOUT = 3.0
# Timeout for individual tool calls (seconds)
MCP_TOOL_CALL_TIMEOUT = 30.0


async def connect_mcp_servers(stack: AsyncExitStack, server_ids: list):
    """Connect to one or more MCP servers sequentially with fast timeouts.
    Returns (tool_defs, tool_sessions, tool_icons, errors).
    """
    if not MCP_AVAILABLE or not server_ids:
        return [], {}, {}, []

    servers = load_mcp()
    tool_defs: list = []
    tool_sessions: dict = {}
    tool_icons: dict = {}
    errors: list = []

async def _connect_one(sid: str, servers: dict):
    config = servers.get(sid)
    if not config or not config.get("enabled", True):
        return None, [], {}, {}, [], config.get("name", sid)

    transport_type = config.get("transport", "stdio")
    name = config.get("name", sid)
    
    tool_defs = []
    tool_sessions = {}
    tool_icons = {}
    errors = []

    async with AsyncExitStack() as local_stack:
        try:
            # --- Establish transport with timeout ---
            if transport_type == "stdio":
                cmd = config.get("command", "")
                args = config.get("args", [])
                env_vars = config.get("env", {})
                if not cmd:
                    return None, [], {}, {}, [], name
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
                    return None, [], {}, {}, [], name
                read, write = await asyncio.wait_for(
                    local_stack.enter_async_context(sse_client(url)),
                    timeout=MCP_CONNECT_TIMEOUT,
                )

            elif transport_type == "streamable-http":
                from mcp.client.streamable_http import streamable_http_client
                url = config.get("url", "")
                if not url:
                    return None, [], {}, {}, [], name
                streams = await asyncio.wait_for(
                    local_stack.enter_async_context(streamable_http_client(url)),
                    timeout=MCP_CONNECT_TIMEOUT,
                )
                read, write = streams[0], streams[1]

            else:
                return None, [], {}, {}, [], name

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
            
            # Return the populated exit stack to be transferred
            return local_stack.pop_all(), tool_defs, tool_sessions, tool_icons, errors, name

        except asyncio.TimeoutError:
            err_msg = f"MCP server '{name}' timed out after {MCP_CONNECT_TIMEOUT}s"
            errors.append(err_msg)
            print(f"  ⏱️ {err_msg}")
        except Exception as e:
            err_msg = f"MCP server '{name}' connection failed: {str(e)}"
            errors.append(err_msg)
            print(f"  ❌ {err_msg}")

    return None, [], {}, {}, errors, name


async def connect_mcp_servers(stack: AsyncExitStack, server_ids: list):
    """Connect to one or more MCP servers in parallel with fast timeouts.
    Returns (tool_defs, tool_sessions, tool_icons, errors).
    """
    if not MCP_AVAILABLE or not server_ids:
        return [], {}, {}, []

    servers = load_mcp()
    tool_defs: list = []
    tool_sessions: dict = {}
    tool_icons: dict = {}
    errors: list = []

    # Connect in parallel to save time
    results = await asyncio.gather(
        *[_connect_one(sid, servers) for sid in server_ids],
        return_exceptions=True
    )

    for res in results:
        if isinstance(res, tuple):
            popped_stack, td, ts, ti, er, name = res
            if popped_stack:
                try:
                    await stack.enter_async_context(popped_stack)
                    print(f"  ✅ MCP '{name}': {len(td)} tools")
                except* Exception as eg:
                    # Suppress teardown ExceptionGroups from streamable-http sessions
                    errors.append(f"MCP '{name}' teardown warning (non-fatal): {eg}")
            
            tool_defs.extend(td)
            tool_sessions.update(ts)
            tool_icons.update(ti)
            errors.extend(er)
        elif isinstance(res, BaseException):
            print(f"  ❌ Unexpected error during parallel MCP connect: {res}")

    return tool_defs, tool_sessions, tool_icons, errors
