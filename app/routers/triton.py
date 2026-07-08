"""
Triton router — browser-facing management REST, plus two client transports.

Management (admin cookie auth, via the global middleware):
  GET    /api/triton/devices               list paired moorings (+ online flag)
  GET    /api/triton/discovered            unpaired clients currently connected
  POST   /api/triton/pair                  {code} -> approve a pending client
  POST   /api/triton/devices/{id}/rename   {name}
  DELETE /api/triton/devices/{id}          revoke (unpair)
  POST   /api/triton/devices/{id}/command  {action,args} -> dispatch & await result

Client transports (own auth, added to public paths in app/__init__.py):
  WS     /api/triton/agent                 LAN / low-latency
  POST   /api/triton/enroll                poll-transport: announce for pairing
  POST   /api/triton/pair-status           poll-transport: pick up token once paired
  POST   /api/triton/poll                  poll-transport: long-wait for next command
  POST   /api/triton/result                poll-transport: return a command result
"""
import asyncio
import datetime

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.triton import (
    manager, hash_token, new_device_token, new_pairing_code,
)
from app.storage import (
    list_triton_devices, get_triton_device, get_triton_token_hash,
    upsert_triton_device, delete_triton_device,
)

router = APIRouter(prefix="/api/triton")


def _now() -> str:
    return datetime.datetime.now().isoformat()


def _touch_last_seen(device_id: str) -> None:
    dev = get_triton_device(device_id)
    if dev:
        upsert_triton_device({**dev, "token_hash": get_triton_token_hash(device_id),
                              "last_seen": _now()})


def _authed(device_id: str, token: str) -> bool:
    stored = get_triton_token_hash(device_id)
    return bool(token and stored and hash_token(token) == stored)


# ─── Management (admin) ──────────────────────────────────────────────────────

@router.get("/devices")
async def triton_devices():
    online = manager.online_ids()
    devices = list_triton_devices()
    for d in devices:
        d["online"] = d["id"] in online
    return {"devices": devices}


@router.get("/discovered")
async def triton_discovered():
    return {"pending": manager.list_pending()}


@router.post("/pair")
async def triton_pair(payload: dict):
    """Approve a pending client by its 8-digit code: mint a token, persist the
    device, and bring the mooring online (over WS, or staged for a poll pickup)."""
    code = (payload or {}).get("code", "")
    conn_id = manager.match_pending_code(code)
    if not conn_id:
        raise HTTPException(status_code=404, detail="No connected client matches that code")
    pending = manager.get_pending(conn_id)
    if not pending:
        raise HTTPException(status_code=409, detail="Client disconnected before pairing")

    device_id = pending["device_id"]
    token = new_device_token()
    device = {
        "id": device_id,
        "name": payload.get("name") or pending.get("name") or device_id,
        "platform": pending.get("platform"),
        "token_hash": hash_token(token),
        "capabilities": pending.get("capabilities", []),
        "paired_at": _now(),
        "last_seen": _now(),
    }
    upsert_triton_device(device)
    manager.remove_pending(conn_id)
    manager.register_online(device_id)

    if pending.get("transport") == "ws" and pending.get("ws") is not None:
        try:
            await pending["ws"].send_json({"type": "paired", "token": token, "device_id": device_id})
        except Exception:
            pass
    else:
        # Poll transport: stash the token for the client's next /pair-status call.
        manager.stash_token(device_id, token)

    return {"ok": True, "device_id": device_id, "name": device["name"]}


@router.post("/devices/{device_id}/rename")
async def triton_rename(device_id: str, payload: dict):
    dev = get_triton_device(device_id)
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")
    name = (payload or {}).get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    upsert_triton_device({**dev, "name": name, "token_hash": get_triton_token_hash(device_id)})
    return {"ok": True}


@router.delete("/devices/{device_id}")
async def triton_revoke(device_id: str):
    if not get_triton_device(device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    manager.unregister_online(device_id)
    delete_triton_device(device_id)
    return {"ok": True}


@router.post("/devices/{device_id}/command")
async def triton_command(device_id: str, payload: dict):
    if not get_triton_device(device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    if not manager.is_online(device_id):
        raise HTTPException(status_code=409, detail="Device is offline")
    action = (payload or {}).get("action", "")
    args = (payload or {}).get("args", {})
    if action not in ("list_dir", "read_file", "ping"):
        raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")
    return await manager.dispatch(device_id, action, args,
                                  timeout=float((payload or {}).get("timeout", 30)))


# ─── Poll transport (proxy / CDN friendly) ───────────────────────────────────

@router.post("/enroll")
async def triton_enroll(payload: dict):
    """A poll-transport client announces itself for pairing. If it already holds a
    valid token it's brought online immediately; otherwise it's held as pending
    with its 8-digit code until an admin approves it."""
    device_id = (payload or {}).get("device_id")
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id required")
    token = payload.get("token")

    if _authed(device_id, token):
        manager.register_online(device_id)
        _touch_last_seen(device_id)
        return {"status": "ready"}

    conn_id = f"poll:{device_id}"
    manager.add_pending(conn_id, {
        "device_id": device_id, "name": payload.get("name") or device_id,
        "platform": payload.get("platform"), "capabilities": payload.get("capabilities", []),
        "code": payload.get("code") or new_pairing_code(), "transport": "poll", "ws": None,
    })
    return {"status": "pending"}


@router.post("/pair-status")
async def triton_pair_status(payload: dict):
    """Poll-transport client checks whether it's been paired yet; returns the token
    exactly once when the admin has approved it."""
    device_id = (payload or {}).get("device_id")
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id required")
    token = manager.take_token(device_id)
    if token:
        return {"status": "paired", "token": token}
    return {"status": "pending"}


@router.post("/poll")
async def triton_poll(payload: dict):
    """Authenticated long-poll: wait (up to ~25s) for the next command for this
    device, or return {} so the client can immediately poll again."""
    device_id = (payload or {}).get("device_id")
    token = (payload or {}).get("token")
    if not _authed(device_id, token):
        raise HTTPException(status_code=401, detail="Invalid device token")
    manager.register_online(device_id)  # idempotent; refreshes online + queue
    _touch_last_seen(device_id)
    cmd = await manager.next_command(device_id, timeout=25.0)
    return cmd or {"type": "idle"}


@router.post("/result")
async def triton_result(payload: dict):
    """Poll-transport client returns a command result."""
    device_id = (payload or {}).get("device_id")
    token = (payload or {}).get("token")
    if not _authed(device_id, token):
        raise HTTPException(status_code=401, detail="Invalid device token")
    manager.resolve_result(device_id, payload.get("req_id", ""), {
        "ok": bool(payload.get("ok", False)),
        "data": payload.get("data"),
        "error": payload.get("error"),
    })
    return {"ok": True}


# ─── WebSocket transport (LAN / low latency) ─────────────────────────────────

@router.websocket("/agent")
async def triton_agent(ws: WebSocket):
    """A mooring's persistent control channel.

    Handshake: the client sends `hello`. Outcomes it must wait for:
      • valid token           -> {"type":"ready"}   (reconnect)
      • token present, invalid -> {"type":"unpaired"} (revoked; client re-hellos w/ code)
      • no token, has code     -> {"type":"pending"}  (client shows the code)
    After pairing (admin POST /pair sends {"type":"paired"}), a pump task drains the
    device's command queue to the socket while the recv loop handles results.
    """
    await ws.accept()
    device_id = None
    conn_id = None
    pump_task = None

    async def start_pump(did: str):
        nonlocal pump_task
        async def _pump():
            while True:
                cmd = await manager.next_command(did, timeout=3600)
                if cmd is not None:
                    await ws.send_json(cmd)
        pump_task = asyncio.create_task(_pump())

    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")

            if mtype == "hello":
                device_id = str(msg.get("device_id") or "")
                if not device_id:
                    await ws.send_json({"type": "error", "error": "device_id required"})
                    continue
                token = msg.get("token")
                if _authed(device_id, token):
                    manager.register_online(device_id)
                    _touch_last_seen(device_id)
                    dev = get_triton_device(device_id)
                    if dev:
                        upsert_triton_device({**dev, "token_hash": get_triton_token_hash(device_id),
                                              "capabilities": msg.get("capabilities", dev.get("capabilities", [])),
                                              "last_seen": _now()})
                    await ws.send_json({"type": "ready", "device_id": device_id})
                    await start_pump(device_id)
                elif token:
                    # Presented a token that no longer matches (revoked/unknown).
                    await ws.send_json({"type": "unpaired"})
                else:
                    conn_id = f"ws:{device_id}:{id(ws)}"
                    manager.add_pending(conn_id, {
                        "device_id": device_id, "name": msg.get("name") or device_id,
                        "platform": msg.get("platform"), "capabilities": msg.get("capabilities", []),
                        "code": msg.get("code") or new_pairing_code(), "transport": "ws", "ws": ws,
                    })
                    await ws.send_json({"type": "pending"})

            elif mtype == "paired-ack":
                # Client acknowledges it saved the token pushed by /pair; start pumping.
                if device_id and manager.is_online(device_id) and pump_task is None:
                    await start_pump(device_id)

            elif mtype == "result":
                manager.resolve_result(device_id or "", msg.get("req_id", ""), {
                    "ok": bool(msg.get("ok", False)),
                    "data": msg.get("data"), "error": msg.get("error"),
                })
            # pong / other messages ignored

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if pump_task:
            pump_task.cancel()
        if conn_id:
            manager.remove_pending(conn_id)
        if device_id and manager.is_online(device_id):
            manager.unregister_online(device_id)
