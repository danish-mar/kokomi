import datetime
import uuid
from contextlib import AsyncExitStack

from fastapi import APIRouter, HTTPException

from app.mcp import connect_mcp_servers, MCP_AVAILABLE
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
    return servers[sid]


@router.put("/{sid}")
async def update_mcp_server(sid: str, config: MCPServerCreate):
    servers = load_mcp()
    if sid not in servers:
        raise HTTPException(404, "Not found")
    servers[sid].update(config.model_dump())
    save_mcp(servers)
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
    return {"ok": True}


@router.post("/{sid}/test")
async def test_mcp_server(sid: str):
    servers = load_mcp()
    if sid not in servers:
        raise HTTPException(404, "Not found")
    if not MCP_AVAILABLE:
        return {"ok": False, "error": "MCP SDK not installed"}
    try:
        async with AsyncExitStack() as stack:
            tool_defs, _, errs = await connect_mcp_servers(stack, [sid])
            if errs:
                return {"ok": False, "error": "\n".join(errs)}
            tools = [t["function"]["name"] for t in tool_defs]
            return {"ok": True, "tools": tools, "count": len(tools)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
