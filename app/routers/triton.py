"""
Triton router — browser-facing management REST + the client-facing agent WS.

Management endpoints (admin cookie auth, via the global middleware):
  GET    /api/triton/devices               list paired moorings (+ online flag)
  GET    /api/triton/discovered            unpaired clients currently connected
  POST   /api/triton/pair                  {code} -> approve a pending client
  POST   /api/triton/devices/{id}/rename   {name}
  DELETE /api/triton/devices/{id}          revoke (unpair)
  POST   /api/triton/devices/{id}/command  {action,args} -> dispatch & await result

Client-facing (own auth, added to public paths in app/__init__.py):
  WS     /api/triton/agent                 mooring control channel
"""
import datetime

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.triton import manager, hash_token, new_device_token, new_pairing_code
from app.storage import (
    list_triton_devices, get_triton_device, get_triton_token_hash,
    upsert_triton_device, delete_triton_device,
)

router = APIRouter(prefix="/api/triton")


def _now() -> str:
    return datetime.datetime.now().isoformat()


# ─── Management (admin) ──────────────────────────────────────────────────────

@router.get("/devices")
async def triton_devices():
    """Paired moorings, annotated with live online status."""
    online = manager.online_ids()
    devices = list_triton_devices()
    for d in devices:
        d["online"] = d["id"] in online
    return {"devices": devices}


@router.get("/discovered")
async def triton_discovered():
    """Unpaired clients currently connected and awaiting an 8-digit pairing code."""
    return {"pending": manager.list_pending()}


@router.post("/pair")
async def triton_pair(payload: dict):
    """Approve a pending client by its 8-digit code: mint a token, persist the
    device, and promote its live connection to an authenticated mooring."""
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

    # Hand the token to the waiting client and promote it to online.
    ws = pending.get("ws")
    manager.remove_pending(conn_id)
    if ws is not None:
        try:
            await ws.send_json({"type": "paired", "token": token, "device_id": device_id})
        except Exception:
            pass
        manager.register_online(device_id, ws)

    return {"ok": True, "device_id": device_id, "name": device["name"]}


@router.post("/devices/{device_id}/rename")
async def triton_rename(device_id: str, payload: dict):
    dev = get_triton_device(device_id)
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")
    name = (payload or {}).get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    dev["name"] = name
    upsert_triton_device({**dev, "token_hash": get_triton_token_hash(device_id)})
    return {"ok": True}


@router.delete("/devices/{device_id}")
async def triton_revoke(device_id: str):
    if not get_triton_device(device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    # Close the live connection if any, then forget the device.
    ws = manager._online.get(device_id)
    if ws is not None:
        try:
            await ws.send_json({"type": "revoked"})
            await ws.close()
        except Exception:
            pass
    manager.unregister_online(device_id)
    delete_triton_device(device_id)
    return {"ok": True}


@router.post("/devices/{device_id}/command")
async def triton_command(device_id: str, payload: dict):
    """Dispatch a single allowlisted action to a mooring and return its result.

    Phase-1 actions (enforced by the client's own allowlist): list_dir, read_file.
    """
    if not get_triton_device(device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    if not manager.is_online(device_id):
        raise HTTPException(status_code=409, detail="Device is offline")

    action = (payload or {}).get("action", "")
    args = (payload or {}).get("args", {})
    if action not in ("list_dir", "read_file", "ping"):
        raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")

    result = await manager.dispatch(device_id, action, args,
                                    timeout=float(payload.get("timeout", 30)))
    return result


# ─── Client-facing agent WebSocket ───────────────────────────────────────────

@router.websocket("/agent")
async def triton_agent(ws: WebSocket):
    """A mooring's persistent control channel.

    Handshake: the client sends a `hello`. If it presents a valid device token it
    is authenticated immediately (reconnect). Otherwise it is held as a *pending*
    client (shown in the UI with the 8-digit code it printed locally) until an
    admin approves it via POST /pair. From then on the server pushes `command`
    messages and the client streams back `result`s.
    """
    await ws.accept()
    device_id = None
    conn_id = None
    authed = False
    try:
        hello = await ws.receive_json()
        if hello.get("type") != "hello" or not hello.get("device_id"):
            await ws.send_json({"type": "error", "error": "expected hello"})
            await ws.close()
            return

        device_id = str(hello["device_id"])
        name = hello.get("name") or device_id
        platform = hello.get("platform")
        capabilities = hello.get("capabilities", [])
        token = hello.get("token")

        # Reconnect path: known device + matching token -> authenticated.
        stored_hash = get_triton_token_hash(device_id)
        if token and stored_hash and hash_token(token) == stored_hash:
            authed = True
            manager.register_online(device_id, ws)
            dev = get_triton_device(device_id)
            if dev:
                upsert_triton_device({**dev, "token_hash": stored_hash,
                                      "capabilities": capabilities, "last_seen": _now()})
            await ws.send_json({"type": "ready", "device_id": device_id})
        else:
            # Pairing path: hold as pending with the client-supplied 8-digit code.
            conn_id = f"{device_id}:{id(ws)}"
            manager.add_pending(conn_id, {
                "device_id": device_id, "name": name, "platform": platform,
                "capabilities": capabilities, "code": hello.get("code") or new_pairing_code(),
                "ws": ws,
            })
            await ws.send_json({"type": "pending"})

        # Message loop: results from dispatched commands, or promotion to authed
        # (the /pair endpoint sends `paired` directly and registers this ws online).
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            if mtype == "result":
                # After pairing, the ws is registered online under device_id.
                did = device_id
                manager.resolve_result(did, msg.get("req_id", ""), {
                    "ok": bool(msg.get("ok", False)),
                    "data": msg.get("data"),
                    "error": msg.get("error"),
                })
            elif mtype == "pong":
                pass
            # heartbeat / other client messages are ignored for now

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if conn_id:
            manager.remove_pending(conn_id)
        if device_id and manager._online.get(device_id) is ws:
            manager.unregister_online(device_id)
