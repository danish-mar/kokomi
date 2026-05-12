"""
MCP Session Pool — persistent, app-level MCP connections.

Sessions are initialized once on first request (or via /api/mcp/init),
cached globally, and automatically refreshed every POOL_TTL_HOURS hours.
Individual tool calls reuse the cached sessions — no per-request overhead.

Key design decisions:
  - All servers connect IN PARALLEL via asyncio.gather for fast startup.
  - Each server runs in its own asyncio.Task so anyio cancel scopes are
    always entered+exited in the same task (no cross-task cancel scope crash).
  - On timeout we cancel the task and give it a short grace period to clean up,
    preventing "Only one SSE stream per session" conflicts on retry.
  - Per-server AsyncExitStack so one failed server never affects others.
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
MCP_CONNECT_TIMEOUT = 15.0     # seconds per server connect+init
MCP_CANCEL_GRACE    = 2.0      # seconds to let a cancelled task clean up
MCP_TOOL_CALL_TIMEOUT = 30.0   # seconds per tool invocation
POOL_TTL_HOURS = 5             # how long before sessions are refreshed

# ── Global Pool State ────────────────────────────────────────────────
_pool_lock = asyncio.Lock()

_pool: dict = {
    "tool_defs":      [],    # list[dict] — OpenAI-style tool definitions
    "tool_sessions":  {},    # dict[tool_name -> ClientSession]
    "tool_icons":     {},    # dict[tool_name -> FA icon class]
    "_tool_to_server": {},   # dict[tool_name -> server_id]
    "server_status":  {},    # dict[server_id -> {name, status, tool_count, error}]
    "initialized_at": 0.0,
    "stacks":         {},    # dict[server_id -> AsyncExitStack]
    "ready":          False,
    "initializing":   False,
}


# ── Public API ───────────────────────────────────────────────────────

def pool_is_stale() -> bool:
    if not _pool["ready"]:
        return True
    return (time.time() - _pool["initialized_at"]) / 3600 >= POOL_TTL_HOURS


def get_pool_status() -> dict:
    return {
        "ready":          _pool["ready"],
        "initializing":   _pool["initializing"],
        "server_status":  _pool["server_status"],
        "tool_count":     len(_pool["tool_defs"]),
        "initialized_at": _pool["initialized_at"],
        "ttl_hours":      POOL_TTL_HOURS,
        "stale":          pool_is_stale(),
    }


def get_pool_tools(server_ids: list | None = None):
    """Return (tool_defs, tool_sessions, tool_icons, errors) from cache."""
    if not _pool["ready"]:
        return [], {}, {}, ["MCP pool not initialized yet"]

    if server_ids is None:
        return (
            list(_pool["tool_defs"]),
            dict(_pool["tool_sessions"]),
            dict(_pool["tool_icons"]),
            [],
        )

    td, ts, ti = [], {}, {}
    for tdef in _pool["tool_defs"]:
        tname = tdef["function"]["name"]
        if tname in _pool["tool_sessions"]:
            owner = _pool["_tool_to_server"].get(tname)
            if owner is None or owner in server_ids:
                td.append(tdef)
                ts[tname] = _pool["tool_sessions"][tname]
                ti[tname] = _pool["tool_icons"].get(tname, "fa-plug")
    return td, ts, ti, []


async def init_pool(force: bool = False):
    """Initialize or refresh the global MCP session pool.
    Connects all servers IN PARALLEL for fast startup.
    Safe to call concurrently — only one init runs at a time.
    """
    if not MCP_AVAILABLE:
        _pool["ready"] = True
        return

    async with _pool_lock:
        if _pool["ready"] and not force and not pool_is_stale():
            return

        _pool["initializing"] = True
        _pool["server_status"] = {}

        # Tear down previous per-server stacks
        for sid, old_stack in list(_pool.get("stacks", {}).items()):
            try:
                await old_stack.aclose()
            except Exception as e:
                print(f"[MCP pool] teardown warning ({sid}): {e}")

        servers = load_mcp()
        enabled = {
            sid: cfg for sid, cfg in servers.items()
            if cfg.get("enabled", True)
        }

        # Mark disabled servers immediately
        for sid, cfg in servers.items():
            if not cfg.get("enabled", True):
                _pool["server_status"][sid] = {
                    "name": cfg.get("name", sid),
                    "status": "disabled",
                    "tool_count": 0,
                    "error": None,
                }

        # Mark all enabled servers as "connecting" before parallel launch
        for sid, cfg in enabled.items():
            _pool["server_status"][sid] = {
                "name": cfg.get("name", sid),
                "status": "connecting",
                "tool_count": 0,
                "error": None,
            }

        # Connect all servers in parallel
        results = await asyncio.gather(
            *[_connect_server_safe(sid, cfg) for sid, cfg in enabled.items()],
            return_exceptions=True,
        )

        tool_defs, tool_sessions, tool_icons, tool_to_server, new_stacks = [], {}, {}, {}, {}

        for (sid, cfg), result in zip(enabled.items(), results):
            name = cfg.get("name", sid)
            if isinstance(result, BaseException):
                _pool["server_status"][sid].update({
                    "status": "error",
                    "error": str(result)[:200],
                })
                print(f"  ❌ MCP '{name}' failed: {result}")
            else:
                td, ts, ti, t2s, stack = result
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

        _pool["tool_defs"]       = tool_defs
        _pool["tool_sessions"]   = tool_sessions
        _pool["tool_icons"]      = tool_icons
        _pool["_tool_to_server"] = tool_to_server
        _pool["stacks"]          = new_stacks
        _pool["initialized_at"]  = time.time()
        _pool["ready"]           = True
        _pool["initializing"]    = False
        print(f"🔧 MCP pool ready — {len(tool_defs)} tools from {len(servers)} servers")


async def teardown_pool():
    """Gracefully close all MCP sessions on app shutdown."""
    for sid, stack in list(_pool.get("stacks", {}).items()):
        try:
            await stack.aclose()
        except Exception as e:
            print(f"[MCP teardown] {sid}: {e}")
    _pool["ready"] = False
    _pool["stacks"] = {}


# ── Single-server test (isolated, NOT from pool) ─────────────────────

async def test_single_server(sid: str):
    """Test a single MCP server in isolation. Does NOT touch the global pool."""
    servers = load_mcp()
    config = servers.get(sid)
    if not config:
        return {"ok": False, "error": "Server not found"}

    try:
        td, ts, ti, t2s, stack = await _connect_server_safe(sid, config)
        tools = [t["function"]["name"] for t in td]
        result = {"ok": True, "tools": tools, "count": len(tools)}
    except asyncio.TimeoutError:
        result = {"ok": False, "error": f"Timed out after {MCP_CONNECT_TIMEOUT}s"}
    except Exception as e:
        result = {"ok": False, "error": str(e)[:200]}
    else:
        # Close the test stack
        try:
            await stack.aclose()
        except Exception:
            pass
    return result


# ── Internal helpers ─────────────────────────────────────────────────

async def _connect_server_safe(sid: str, config: dict):
    """Run _connect_server in its own task with timeout + proper cleanup.

    Using a separate task ensures anyio cancel scopes are always owned
    by the same task that entered them, preventing:
      'Attempted to exit cancel scope in a different task'

    We do NOT use asyncio.shield() because that keeps the inner task alive
    after timeout, which causes "Only one SSE stream per session" conflicts
    when the server retries on the same session ID.
    Instead we cancel the task and give it a grace period to clean up.
    """
    stack = AsyncExitStack()
    await stack.__aenter__()

    task = asyncio.ensure_future(_connect_server(sid, config, stack))
    try:
        return await asyncio.wait_for(task, timeout=MCP_CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        # Cancel the task and let it clean up before we raise
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=MCP_CANCEL_GRACE)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
        try:
            await stack.aclose()
        except Exception:
            pass
        raise asyncio.TimeoutError(f"Timed out after {MCP_CONNECT_TIMEOUT}s")
    except Exception:
        task.cancel()
        try:
            await stack.aclose()
        except Exception:
            pass
        raise


async def _connect_server(
    sid: str, config: dict, stack: AsyncExitStack
) -> tuple[list, dict, dict, dict, AsyncExitStack]:
    """Connect one MCP server — runs entirely inside a single asyncio task.
    Returns (tool_defs, tool_sessions, tool_icons, tool_to_server, stack).
    """
    transport_type = config.get("transport", "stdio")
    name = config.get("name", sid)

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

    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    result = await session.list_tools()

    tool_defs, tool_sessions, tool_icons, tool_to_server = [], {}, {}, {}

    for tool in result.tools:
        schema = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
        if "properties" not in schema:
            schema["properties"] = {}

        schema["properties"]["ui_status_text"] = {
            "type": "string",
            "description": (
                "A short, present-tense, human-readable status message for the UI "
                "describing what you are doing. MUST be provided whenever you call this tool."
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

    return tool_defs, tool_sessions, tool_icons, tool_to_server, stack
