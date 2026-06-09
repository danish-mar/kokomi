import datetime
import uuid
from contextlib import AsyncExitStack

from fastapi import APIRouter, HTTPException

from app.mcp import (
    MCP_AVAILABLE,
    init_pool,
    get_pool_status,
    pool_is_stale,
    test_single_server,
)
from app.models import MCPServerCreate
from app.storage import load_mcp, save_mcp, load_chars, save_chars

router = APIRouter(prefix="/api/mcp-servers")


@router.get("")
async def list_mcp_servers():
    return list(load_mcp().values())


@router.get("/{sid}")
async def get_mcp_server(sid: str):
    servers = load_mcp()
    if sid not in servers:
        raise HTTPException(404, "Not found")
    return servers[sid]


@router.post("")
async def create_mcp_server(config: MCPServerCreate):
    servers = load_mcp()
    sid = str(uuid.uuid4())[:8]
    servers[sid] = {
        "id": sid,
        **config.model_dump(),
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    save_mcp(servers)
    try:
        await init_pool(force=True)
    except Exception as e:
        print(f"[MCP Server Manager] warning refreshing pool: {e}")
    return servers[sid]


@router.put("/{sid}")
async def update_mcp_server(sid: str, config: MCPServerCreate):
    servers = load_mcp()
    if sid not in servers:
        raise HTTPException(404, "Not found")
    servers[sid].update(config.model_dump())
    save_mcp(servers)
    try:
        await init_pool(force=True)
    except Exception as e:
        print(f"[MCP Server Manager] warning refreshing pool: {e}")
    return servers[sid]


@router.delete("/{sid}")
async def delete_mcp_server(sid: str):
    servers = load_mcp()
    if sid not in servers:
        raise HTTPException(404, "Not found")
    del servers[sid]
    save_mcp(servers)

    # Also remove the server from any characters that reference it
    chars = load_chars()
    for c in chars.values():
        if sid in c.get("mcp_servers", []):
            c["mcp_servers"].remove(sid)
    save_chars(chars)

    try:
        await init_pool(force=True)
    except Exception as e:
        print(f"[MCP Server Manager] warning refreshing pool: {e}")
    return {"ok": True}


@router.post("/{sid}/test")
async def test_mcp_server(sid: str):
    """Test a single MCP server in an isolated context (not from pool)."""
    servers = load_mcp()
    if sid not in servers:
        raise HTTPException(404, "Not found")
    if not MCP_AVAILABLE:
        return {"ok": False, "error": "MCP SDK not installed"}
    return await test_single_server(sid)


# ── Pool Management ─────────────────────────────────────────────────

@router.get("/pool/status", name="mcp_pool_status")
async def mcp_pool_status():
    """Return current MCP pool health for the splash screen."""
    return get_pool_status()


@router.post("/pool/init", name="mcp_pool_init")
async def mcp_pool_init(force: bool = False):
    """Initialize or refresh the global MCP session pool.
    Called by the frontend splash screen on first load.
    """
    if not MCP_AVAILABLE:
        return {"ok": True, "message": "MCP not available, nothing to initialize"}

    needs_refresh = pool_is_stale() or force
    if not needs_refresh:
        return {"ok": True, "message": "Pool is fresh", **get_pool_status()}

    await init_pool(force=force)
    return {"ok": True, **get_pool_status()}


@router.get("/pool/tools", name="mcp_pool_tools")
async def mcp_pool_tools():
    """Return list of all tool names currently loaded in the MCP pool."""
    from app.mcp import get_pool_tools
    td, _, _, _ = get_pool_tools()
    return [t["function"]["name"] for t in td]


@router.get("/pool/tools/detailed", name="mcp_pool_tools_detailed")
async def mcp_pool_tools_detailed():
    """Return list of all tool definitions currently loaded in the MCP pool, with their server IDs."""
    from app.mcp import get_pool_tools, _pool
    td, _, _, _ = get_pool_tools()
    tools_list = []
    for t in td:
        tname = t["function"]["name"]
        owner = _pool.get("_tool_to_server", {}).get(tname)
        # If it belongs to appbridge, the tool name itself is the app_id
        actual_server_id = tname if owner == "appbridge" else owner
        tools_list.append({
            "name": tname,
            "description": t["function"].get("description", ""),
            "server_id": actual_server_id
        })
    return tools_list
