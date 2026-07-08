"""
app/triton.py — Triton: the AI's remote "hands" on your own machines.

A Triton *mooring* is a lightweight client daemon installed on one of the user's
computers (Linux first). It connects to this Kokomi server, proves itself with
an 8-digit pairing code once, and thereafter executes allowlisted actions the
server dispatches — list a directory, fetch a file, etc. The client owns its own
capability allowlist; the server only routes.

"Atlas plans, Triton acts."

Two transports, same command model underneath:
  • WebSocket (LAN, low latency) — client connects out, we push over the socket.
  • HTTP long-poll (proxy/CDN friendly) — client repeatedly asks for the next
    command; works cleanly through nginx / Cloudflare where WS upgrades are
    finicky. Both share one per-device command queue + result-future registry.

Persistence of paired devices lives in app.storage (SQLite); the browser-facing
REST + client transports live in app.routers.triton.
"""
import asyncio
import datetime
import hashlib
import secrets
import socket
from typing import Any, Dict, Optional

# UDP port the server beacons on so LAN clients can auto-discover it (zero-config).
DISCOVERY_PORT = 47201
DISCOVERY_MAGIC = "kokomi-triton-server"


def _now() -> str:
    return datetime.datetime.now().isoformat()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_device_token() -> str:
    return secrets.token_urlsafe(32)


def new_pairing_code() -> str:
    """A human-typeable 8-digit code, shown by the client, entered in the UI."""
    return f"{secrets.randbelow(10 ** 8):08d}"


class TritonManager:
    """Runtime registry: live moorings, pending pairings, and command routing.

    A device is "online" if it has an entry in _online (updated on connect / each
    poll). Commands for a device are enqueued on its _queues entry; the WS pump or
    the poll endpoint drains it. Results resolve futures in _waiters.
    """

    def __init__(self) -> None:
        self._online: Dict[str, str] = {}                 # device_id -> last_seen iso
        self._queues: Dict[str, asyncio.Queue] = {}       # device_id -> outbound commands
        self._pending: Dict[str, dict] = {}               # conn_id -> pending pairing info
        self._paired_tokens: Dict[str, str] = {}          # device_id -> token awaiting poll pickup
        self._waiters: Dict[str, asyncio.Future] = {}     # "device:req" -> result future
        self._discovery_task: Optional[asyncio.Task] = None

    # ── Pending (discovered-but-unpaired) clients ────────────────────────────
    def add_pending(self, conn_id: str, info: dict) -> None:
        self._pending[conn_id] = {**info, "seen_at": _now()}

    def remove_pending(self, conn_id: str) -> None:
        self._pending.pop(conn_id, None)

    def list_pending(self) -> list:
        """Unpaired clients currently connected — the 'discovered' list, minus codes."""
        return [
            {"conn_id": cid, "device_id": p.get("device_id"), "name": p.get("name"),
             "platform": p.get("platform"), "capabilities": p.get("capabilities", []),
             "transport": p.get("transport", "ws"), "seen_at": p.get("seen_at")}
            for cid, p in self._pending.items()
        ]

    def match_pending_code(self, code: str) -> Optional[str]:
        """Return the conn_id of the pending client whose code matches."""
        code = (code or "").strip()
        for cid, p in self._pending.items():
            if secrets.compare_digest(str(p.get("code", "")), code):
                return cid
        return None

    def get_pending(self, conn_id: str) -> Optional[dict]:
        return self._pending.get(conn_id)

    # ── Online moorings + per-device command queue ───────────────────────────
    def register_online(self, device_id: str) -> None:
        if device_id not in self._queues:
            self._queues[device_id] = asyncio.Queue()
        self._online[device_id] = _now()

    def mark_seen(self, device_id: str) -> None:
        if device_id in self._online:
            self._online[device_id] = _now()

    def unregister_online(self, device_id: str) -> None:
        self._online.pop(device_id, None)
        self._queues.pop(device_id, None)

    def is_online(self, device_id: str) -> bool:
        return device_id in self._online

    def online_ids(self) -> set:
        return set(self._online.keys())

    # ── Token handoff for the poll transport ─────────────────────────────────
    def stash_token(self, device_id: str, token: str) -> None:
        self._paired_tokens[device_id] = token

    def take_token(self, device_id: str) -> Optional[str]:
        return self._paired_tokens.pop(device_id, None)

    # ── Command dispatch (server → mooring, await result) ────────────────────
    async def dispatch(self, device_id: str, action: str, args: dict,
                       timeout: float = 30.0) -> dict:
        """Enqueue a command for a mooring and await its result. Transport-agnostic:
        a WS pump or the poll endpoint delivers whatever is queued here."""
        if device_id not in self._online:
            return {"ok": False, "error": "Device is not online"}
        q = self._queues.get(device_id)
        if q is None:
            return {"ok": False, "error": "Device has no active channel"}

        req_id = secrets.token_hex(8)
        key = f"{device_id}:{req_id}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._waiters[key] = fut
        try:
            await q.put({"type": "command", "req_id": req_id, "action": action, "args": args or {}})
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"Timed out after {timeout:.0f}s"}
        except Exception as e:
            return {"ok": False, "error": f"Dispatch failed: {e}"}
        finally:
            self._waiters.pop(key, None)

    async def next_command(self, device_id: str, timeout: float = 25.0) -> Optional[dict]:
        """Poll/WS-pump side: wait for the next queued command, or None on timeout."""
        q = self._queues.get(device_id)
        if q is None:
            return None
        try:
            return await asyncio.wait_for(q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def resolve_result(self, device_id: str, req_id: str, payload: dict) -> None:
        fut = self._waiters.get(f"{device_id}:{req_id}")
        if fut and not fut.done():
            fut.set_result(payload)

    # ── LAN discovery beacon ─────────────────────────────────────────────────
    async def start_discovery(self, app_port: int = 8000) -> None:
        if self._discovery_task and not self._discovery_task.done():
            return
        self._discovery_task = asyncio.create_task(self._beacon_loop(app_port))

    async def _beacon_loop(self, app_port: int) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
        except Exception:
            return
        while True:
            try:
                lan_ip = _lan_ip()
                msg = f"{DISCOVERY_MAGIC}|ws://{lan_ip}:{app_port}/api/triton/agent".encode()
                sock.sendto(msg, ("255.255.255.255", DISCOVERY_PORT))
            except Exception:
                pass
            await asyncio.sleep(5)

    async def stop_discovery(self) -> None:
        if self._discovery_task:
            self._discovery_task.cancel()
            self._discovery_task = None


def _lan_ip() -> str:
    """Best-effort primary LAN IP (no packets actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# App-wide singleton.
manager = TritonManager()
