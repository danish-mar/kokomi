"""
MCP Session Pool — persistent, app-level MCP connections.

Sessions are initialized once on first request (or via /api/mcp/init),
cached globally, and automatically refreshed every POOL_TTL_HOURS hours.
Individual tool calls reuse the cached sessions — no per-request overhead.

Key design: each server is connected inside its own asyncio.Task so that
anyio cancel scopes are always entered and exited in the *same* task,
avoiding the "Attempted to exit cancel scope in a different task" error.
"""

import os
import asyncio
import time
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

# ── Timeouts ─────────────────────────────────────────────────────────
MCP_CONNECT_TIMEOUT = 12.0     # seconds per server connect+init
MCP_TOOL_CALL_TIMEOUT = 30.0   # seconds per tool invocation
POOL_TTL_HOURS = 5             # how long before sessions are refreshed

# ── Global Pool State ────────────────────────────────────────────────
_pool_lock = asyncio.Lock()

_pool: dict = {
    "tool_defs":     [],     # list[dict] — OpenAI-style tool definitions
    "tool_sessions": {},     # dict[tool_name -> ClientSession]
    "tool_icons":    {},     # dict[tool_name -> FA icon class]
    "server_status": {},     # dict[server_id -> {name, status, tool_count, error}]
    "initialized_at": 0.0,  # epoch timestamp
    "stacks": {},            # dict[server_id -> AsyncExitStack] — one per server
    "ready": False,
    "initializing": False,
}


# ── Public API ───────────────────────────────────────────────────────

def pool_is_stale() -> bool:
    """Check if the pool needs a refresh."""
    if not _pool["ready"]:
        return True
    age_hours = (time.time() - _pool["initialized_at"]) / 3600
    return age_hours >= POOL_TTL_HOURS


def get_pool_status() -> dict:
    """Return a JSON-safe snapshot of pool health for the frontend."""
    return {
        "ready": _pool["ready"],
        "initializing": _pool["initializing"],
        "server_status": _pool["server_status"],
        "tool_count": len(_pool["tool_defs"]),
        "initialized_at": _pool["initialized_at"],
        "ttl_hours": POOL_TTL_HOURS,
        "stale": pool_is_stale(),
    }


def get_pool_tools(server_ids: list | None = None):
    """Retrieve cached tool defs/sessions/icons for a set of server IDs.
    If server_ids is None, returns ALL tools. Otherwise filters to only
    tools belonging to the requested servers.
    Returns (tool_defs, tool_sessions, tool_icons, errors).
    """
    if not _pool["ready"]:
        return [], {}, {}, ["MCP pool not initialized yet"]

    if server_ids is None:
        return (
            list(_pool["tool_defs"]),
            dict(_pool["tool_sessions"]),
            dict(_pool["tool_icons"]),
            [],
        )

    # Filter: we need to know which tools belong to which server
    td, ts, ti = [], {}, {}
    for tdef in _pool["tool_defs"]:
        tname = tdef["function"]["name"]
        if tname in _pool["tool_sessions"]:
            tool_server_id = _pool.get("_tool_to_server", {}).get(tname)
            if tool_server_id is None or tool_server_id in server_ids:
                td.append(tdef)
                ts[tname] = _pool["tool_sessions"][tname]
                ti[tname] = _pool["tool_icons"].get(tname, "fa-plug")

    return td, ts, ti, []


async def init_pool(force: bool = False):
    """Initialize or refresh the global MCP session pool.
    Safe to call concurrently — only one init runs at a time.
    """
    if not MCP_AVAILABLE:
        _pool["ready"] = True
        return

    async with _pool_lock:
        if _pool["ready"] and not force and not pool_is_stale():
            return  # already good

        _pool["initializing"] = True
        _pool["server_status"] = {}

        # Tear down all existing per-server stacks
        old_stacks = _pool.get("stacks", {})
        for old_sid, old_stack in old_stacks.items():
            try:
                await old_stack.aclose()
            except Exception as e:
                print(f"[MCP pool] old stack teardown warning ({old_sid}): {e}")

        servers = load_mcp()
        tool_defs = []
        tool_sessions = {}
        tool_icons = {}
        tool_to_server = {}
        new_stacks = {}

        for sid, config in servers.items():
            if not config.get("enabled", True):
                _pool["server_status"][sid] = {
                    "name": config.get("name", sid),
                    "status": "disabled",
                    "tool_count": 0,
                    "error": None,
                }
                continue

            name = config.get("name", sid)
            _pool["server_status"][sid] = {
                "name": name,
                "status": "connecting",
                "tool_count": 0,
                "error": None,
            }

            # Run connection inside its own isolated task so anyio
            # cancel scopes never cross task boundaries.
            stack = AsyncExitStack()
            await stack.__aenter__()
            try:
                task = asyncio.ensure_future(
                    _connect_server(sid, config, stack)
                )
                td, ts, ti, t2s = await asyncio.wait_for(
                    asyncio.shield(task), timeout=MCP_CONNECT_TIMEOUT
                )
                tool_defs.extend(td)
                tool_sessions.update(ts)
                tool_icons.update(ti)
                tool_to_server.update(t2s)
                new_stacks[sid] = stack

                _pool["server_status"][sid].update({
                    "status": "connected",
                    "tool_count": len(td),
                })
                print(f"  ✅ MCP '{name}': {len(td)} tools")

            except asyncio.TimeoutError:
                task.cancel()
                _pool["server_status"][sid].update({
                    "status": "timeout",
                    "error": f"Timed out after {MCP_CONNECT_TIMEOUT}s",
                })
                print(f"  ⏱️ MCP '{name}' timed out")
                try:
                    await stack.aclose()
                except Exception:
                    pass
            except Exception as e:
                _pool["server_status"][sid].update({
                    "status": "error",
                    "error": str(e)[:200],
                })
                print(f"  ❌ MCP '{name}' failed: {e}")
                try:
                    await stack.aclose()
                except Exception:
                    pass

        _pool["tool_defs"] = tool_defs
        _pool["tool_sessions"] = tool_sessions
        _pool["tool_icons"] = tool_icons
        _pool["_tool_to_server"] = tool_to_server
        _pool["stacks"] = new_stacks
        _pool["initialized_at"] = time.time()
        _pool["ready"] = True
        _pool["initializing"] = False
        print(f"🔧 MCP pool ready — {len(tool_defs)} tools from {len(servers)} servers")


async def teardown_pool():
    """Gracefully close all MCP sessions. Called on app shutdown."""
    stacks = _pool.get("stacks", {})
    for sid, stack in stacks.items():
        try:
            await stack.aclose()
        except Exception as e:
            print(f"[MCP pool teardown] {sid}: {e}")
    _pool["ready"] = False
    _pool["stacks"] = {}


# ── Single-server test (isolated, NOT from pool) ─────────────────────

async def test_single_server(sid: str):
    """Connect to a single MCP server in an isolated context for testing.
    Returns {ok, tools, count, error}. Does NOT touch the global pool.
    """
    servers = load_mcp()
    config = servers.get(sid)
    if not config:
        return {"ok": False, "error": "Server not found"}

    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        task = asyncio.ensure_future(_connect_server(sid, config, stack))
        td, ts, ti, t2s = await asyncio.wait_for(
            asyncio.shield(task), timeout=MCP_CONNECT_TIMEOUT
        )
        tools = [t["function"]["name"] for t in td]
        result = {"ok": True, "tools": tools, "count": len(tools)}
    except asyncio.TimeoutError:
        task.cancel()
        result = {"ok": False, "error": f"Timed out after {MCP_CONNECT_TIMEOUT}s"}
    except Exception as e:
        result = {"ok": False, "error": str(e)[:200]}
    finally:
        try:
            await stack.aclose()
        except Exception:
            pass
    return result


# ── Internal: connect a single server into a stack ───────────────────

async def _connect_server(
    sid: str, config: dict, stack: AsyncExitStack
) -> tuple[list, dict, dict, dict]:
    """Connect one MCP server and register its resources in `stack`.
    This coroutine must run entirely inside a single asyncio task so
    that anyio cancel scopes are always owned by the same task.
    Returns (tool_defs, tool_sessions, tool_icons, tool_to_server).
    """
    transport_type = config.get("transport", "stdio")
    name = config.get("name", sid)

    # --- Establish transport ---
    if transport_type == "stdio":
        cmd = config.get("command", "")
        args = config.get("args", [])
        env_vars = config.get("env", {})
        if not cmd:
            raise ValueError(f"No command specified for stdio server '{name}'")
        merged_env = {**os.environ, **env_vars} if env_vars else None
        params = StdioServerParameters(
            command=cmd,
            args=args if isinstance(args, list) else args.split(),
            env=merged_env,
        )
        read, write = await stack.enter_async_context(stdio_client(params))

    elif transport_type == "sse":
        from mcp.client.sse import sse_client
        url = config.get("url", "")
        if not url:
            raise ValueError(f"No URL specified for SSE server '{name}'")
        read, write = await stack.enter_async_context(sse_client(url))

    elif transport_type == "streamable-http":
        from mcp.client.streamable_http import streamable_http_client
        url = config.get("url", "")
        if not url:
            raise ValueError(f"No URL for streamable-http server '{name}'")
        streams = await stack.enter_async_context(streamable_http_client(url))
        read, write = streams[0], streams[1]

    else:
        raise ValueError(f"Unknown transport '{transport_type}' for '{name}'")

    # --- Initialize session ---
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()

    # --- Enumerate tools ---
    result = await session.list_tools()

    tool_defs = []
    tool_sessions = {}
    tool_icons = {}
    tool_to_server = {}

    for tool in result.tools:
        schema = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
        if "properties" not in schema:
            schema["properties"] = {}

        schema["properties"]["ui_status_text"] = {
            "type": "string",
            "description": (
                "A short, present-tense, human-readable status message for the UI "
                "describing what you are doing (e.g. 'Searching and playing All of us "
                "are dead...'). MUST be provided whenever you call this tool."
            ),
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
        tool_to_server[tool.name] = sid

    return tool_defs, tool_sessions, tool_icons, tool_to_server
