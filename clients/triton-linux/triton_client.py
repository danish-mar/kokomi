#!/usr/bin/env python3
"""
Triton client (Linux) — Kokomi's remote hands on this machine.

Run this on a computer you want Kokomi to reach. It:
  1. auto-discovers your Kokomi server on the LAN (UDP beacon), or use --server
  2. connects out to the server over a WebSocket (works behind NAT)
  3. prints an 8-digit pairing code — enter it in Kokomi → Settings → Triton
  4. once paired, executes ONLY allowlisted, read-only actions the server sends

Safety model — this client is the boundary, not the server:
  • It will only read paths inside --allow directories (default: ~).
  • Phase 1 actions are read-only: list_dir, read_file. No writes, no shell.
  • The device token is stored at ~/.config/kokomi-triton/state.json (chmod 600).

Dependencies: pip install websockets
"""
import argparse
import asyncio
import base64
import json
import os
import secrets
import socket
import stat
import sys
import uuid
from pathlib import Path

try:
    import websockets
except ImportError:
    sys.exit("Missing dependency. Install it with:  pip install websockets")

DISCOVERY_PORT = 47201
DISCOVERY_MAGIC = "kokomi-triton-server"
STATE_DIR = Path.home() / ".config" / "kokomi-triton"
STATE_FILE = STATE_DIR / "state.json"
MAX_READ_BYTES = 25 * 1024 * 1024  # 25 MB cap on a single file fetch

CAPABILITIES = ["list_dir", "read_file"]


# ── Local state (device id + token persist across restarts) ──────────────────
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    try:
        os.chmod(STATE_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600
    except Exception:
        pass


# ── LAN discovery: listen for the server's UDP beacon ────────────────────────
def discover_server(timeout: float = 8.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", DISCOVERY_PORT))
    except Exception as e:
        print(f"! Could not bind discovery port {DISCOVERY_PORT}: {e}")
        return None
    sock.settimeout(timeout)
    try:
        data, _addr = sock.recvfrom(1024)
        text = data.decode(errors="ignore")
        if text.startswith(DISCOVERY_MAGIC + "|"):
            return text.split("|", 1)[1]  # ws://<ip>:<port>/api/triton/agent
    except socket.timeout:
        return None
    except Exception:
        return None
    finally:
        sock.close()
    return None


# ── Allowlist enforcement ────────────────────────────────────────────────────
def _resolve_allowed(path: str, allow_roots: list) -> Path:
    """Resolve a requested path and confirm it sits inside an allowed root."""
    p = Path(os.path.expanduser(path)).resolve()
    for root in allow_roots:
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
    raise PermissionError(f"'{path}' is outside the allowed folders")


# ── Command handlers (read-only, Phase 1) ────────────────────────────────────
def handle_list_dir(args: dict, allow_roots: list) -> dict:
    target = _resolve_allowed(args.get("path", "~"), allow_roots)
    if not target.is_dir():
        return {"ok": False, "error": "Not a directory"}
    entries = []
    for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        try:
            is_dir = child.is_dir()
            entries.append({
                "name": child.name,
                "is_dir": is_dir,
                "size": None if is_dir else child.stat().st_size,
                "modified": int(child.stat().st_mtime),
            })
        except Exception:
            continue
    return {"ok": True, "data": {"path": str(target), "entries": entries}}


def handle_read_file(args: dict, allow_roots: list) -> dict:
    target = _resolve_allowed(args.get("path", ""), allow_roots)
    if not target.is_file():
        return {"ok": False, "error": "Not a file"}
    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        return {"ok": False, "error": f"File too large ({size} bytes, max {MAX_READ_BYTES})"}
    raw = target.read_bytes()
    return {"ok": True, "data": {
        "name": target.name,
        "size": size,
        "b64": base64.b64encode(raw).decode(),
    }}


def dispatch(action: str, args: dict, allow_roots: list) -> dict:
    if action == "ping":
        return {"ok": True, "data": {"pong": True}}
    if action == "list_dir":
        return handle_list_dir(args, allow_roots)
    if action == "read_file":
        return handle_read_file(args, allow_roots)
    return {"ok": False, "error": f"Unsupported action: {action}"}


# ── Main connection loop ─────────────────────────────────────────────────────
async def run(server_url: str, allow_roots: list):
    state = load_state()
    device_id = state.get("device_id") or f"linux-{uuid.uuid4().hex[:10]}"
    state["device_id"] = device_id
    save_state(state)

    token = state.get("token")
    code = None if token else f"{secrets.randbelow(10**8):08d}"

    hello = {
        "type": "hello",
        "device_id": device_id,
        "name": state.get("name") or socket.gethostname(),
        "platform": "linux",
        "capabilities": CAPABILITIES,
    }
    if token:
        hello["token"] = token
    else:
        hello["code"] = code

    async with websockets.connect(server_url, max_size=None) as ws:
        await ws.send(json.dumps(hello))

        if not token:
            print("\n" + "─" * 46)
            print("  Pair this machine in Kokomi → Settings → Triton")
            print(f"  8-DIGIT CODE:   {code}")
            print("─" * 46 + "\n")
        else:
            print(f"✓ Reconnected as {hello['name']} ({device_id})")

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")

            if mtype == "paired":
                token = msg.get("token")
                state["token"] = token
                state["name"] = hello["name"]
                save_state(state)
                print("✓ Paired! This machine is now a Kokomi mooring.")
            elif mtype == "ready":
                print("✓ Online and ready.")
            elif mtype == "revoked":
                state.pop("token", None)
                save_state(state)
                print("✗ This device was revoked from the server. Re-run to pair again.")
                return
            elif mtype == "command":
                req_id = msg.get("req_id")
                action = msg.get("action", "")
                args = msg.get("args", {})
                try:
                    result = dispatch(action, args, allow_roots)
                except PermissionError as pe:
                    result = {"ok": False, "error": str(pe)}
                except Exception as e:
                    result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                print(f"→ {action}({args.get('path','')}) => {'ok' if result.get('ok') else result.get('error')}")
                await ws.send(json.dumps({
                    "type": "result", "req_id": req_id,
                    "ok": result.get("ok", False),
                    "data": result.get("data"),
                    "error": result.get("error"),
                }))
            elif mtype == "ping":
                await ws.send(json.dumps({"type": "pong"}))


async def main():
    ap = argparse.ArgumentParser(description="Triton client — Kokomi's remote hands on this machine.")
    ap.add_argument("--server", default=os.getenv("KOKOMI_SERVER"),
                    help="ws://host:port/api/triton/agent (default: auto-discover on LAN)")
    ap.add_argument("--allow", action="append", default=None,
                    help="A folder Triton may read (repeatable). Default: your home directory.")
    args = ap.parse_args()

    allow_roots = [Path(os.path.expanduser(a)).resolve() for a in (args.allow or ["~"])]
    print("Allowed folders:", ", ".join(str(r) for r in allow_roots))

    server = args.server
    if not server:
        print("Discovering Kokomi server on the LAN…")
        server = discover_server()
        if not server:
            sys.exit("No server found. Pass --server ws://<host>:8000/api/triton/agent")
        print(f"Found server: {server}")

    while True:
        try:
            await run(server, allow_roots)
        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"! Connection lost ({e}); retrying in 5s…")
        await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBye.")
