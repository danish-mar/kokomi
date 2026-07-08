#!/usr/bin/env python3
"""
Triton client (Linux) — Kokomi's remote hands on this machine.

Run this on a computer you want Kokomi to reach. It:
  1. finds your Kokomi server (LAN auto-discovery, or --server)
  2. connects via one of two transports:
       • poll (default) — plain HTTPS, works through nginx / Cloudflare / any proxy
       • ws             — a WebSocket, lowest latency, best on a LAN
  3. prints an 8-digit pairing code — enter it in Kokomi → Settings → Triton
  4. once paired, executes ONLY the actions your policy allows

Safety model — this client is the boundary, not the server:
  • Path actions only touch inside --allow directories (default: ~).
  • Reads (list_dir, read_file) and observability (process_list, service_list —
    list only, never kill) are always on.
  • Command execution is OFF by default → --allow-exec (+ optional --allow-cmd).
  • File writes are OFF by default → --allow-write (confined to --allow dirs).
  • Desktop actions (open a URL, screenshot, clipboard) are OFF by default →
    --allow-gui. They expose/act on the live session, so they're opt-in.
  • The device token is stored at ~/.config/kokomi-triton/state.json (chmod 600).

Dependencies:
  poll transport (default):  pip install requests
  ws transport:              pip install websockets
  screenshot/clipboard use system tools (grim/scrot, wl-clipboard/xclip) — no
  extra Python packages needed.
"""
import argparse
import asyncio
import base64
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, urlunparse

DISCOVERY_PORT = 47201
DISCOVERY_MAGIC = "kokomi-triton-server"
STATE_DIR = Path.home() / ".config" / "kokomi-triton"
STATE_FILE = STATE_DIR / "state.json"
MAX_READ_BYTES = 25 * 1024 * 1024      # 25 MB cap on a single file fetch
MAX_OUTPUT_BYTES = 100 * 1024          # 100 KB cap on captured command output (each stream)
MAX_CMD_TIMEOUT = 300                  # hard ceiling on how long a command may run
# Shell operators that could chain past a command whitelist.
_SHELL_METACHARS = re.compile(r"[;&|`\n<>]|\$\(|\$\{|>>")


# ── Policy: what this machine is willing to do ───────────────────────────────
class Policy:
    def __init__(self, allow_roots, exec_enabled=False, allow_cmds=None,
                 write_enabled=False, gui_enabled=False):
        self.allow_roots = allow_roots
        self.exec_enabled = exec_enabled
        # None/empty set => any binary (only reachable when exec_enabled is True).
        self.allow_cmds = set(allow_cmds) if allow_cmds else set()
        self.write_enabled = write_enabled
        self.gui_enabled = gui_enabled

    @property
    def capabilities(self):
        caps = ["list_dir", "read_file", "process_list", "service_list"]
        if self.exec_enabled:
            caps.append("run_command")
        if self.write_enabled:
            caps.append("write_file")
        if self.gui_enabled:
            caps += ["open_url", "screenshot", "clipboard_read", "clipboard_write"]
        return caps

    @property
    def default_cwd(self):
        return str(self.allow_roots[0]) if self.allow_roots else str(Path.home())


# ── Local state ──────────────────────────────────────────────────────────────
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


def print_code(code: str) -> None:
    print("\n" + "─" * 46)
    print("  Pair this machine in Kokomi → Settings → Triton")
    print(f"  8-DIGIT CODE:   {code}")
    print("─" * 46 + "\n")


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
        data, _ = sock.recvfrom(1024)
        text = data.decode(errors="ignore")
        if text.startswith(DISCOVERY_MAGIC + "|"):
            return text.split("|", 1)[1]  # ws://<ip>:<port>/api/triton/agent
    except Exception:
        return None
    finally:
        sock.close()
    return None


# ── Allowlist enforcement + action handlers ──────────────────────────────────
def _resolve_allowed(path: str, allow_roots: list) -> Path:
    p = Path(os.path.expanduser(path)).resolve()
    for root in allow_roots:
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
    raise PermissionError(f"'{path}' is outside the allowed folders")


def handle_list_dir(args: dict, policy: Policy) -> dict:
    target = _resolve_allowed(args.get("path", "~"), policy.allow_roots)
    if not target.is_dir():
        return {"ok": False, "error": "Not a directory"}
    entries = []
    for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        try:
            is_dir = child.is_dir()
            entries.append({"name": child.name, "is_dir": is_dir,
                            "size": None if is_dir else child.stat().st_size,
                            "modified": int(child.stat().st_mtime)})
        except Exception:
            continue
    return {"ok": True, "data": {"path": str(target), "entries": entries}}


def handle_read_file(args: dict, policy: Policy) -> dict:
    target = _resolve_allowed(args.get("path", ""), policy.allow_roots)
    if not target.is_file():
        return {"ok": False, "error": "Not a file"}
    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        return {"ok": False, "error": f"File too large ({size} bytes, max {MAX_READ_BYTES})"}
    return {"ok": True, "data": {"name": target.name, "size": size,
                                 "b64": base64.b64encode(target.read_bytes()).decode()}}


def handle_run_command(args: dict, policy: Policy) -> dict:
    if not policy.exec_enabled:
        return {"ok": False, "error": "Command execution is disabled on this machine. "
                                      "Start the Triton client with --allow-exec to enable it."}
    command = (args.get("command") or "").strip()
    if not command:
        return {"ok": False, "error": "No command provided"}

    # Working directory must be inside an allowed folder.
    cwd = args.get("cwd") or policy.default_cwd
    try:
        cwd_path = _resolve_allowed(cwd, policy.allow_roots)
    except PermissionError as pe:
        return {"ok": False, "error": str(pe)}
    if not cwd_path.is_dir():
        return {"ok": False, "error": f"Working directory '{cwd}' does not exist"}

    # If a command whitelist is set, forbid shell chaining and check the binary.
    if policy.allow_cmds:
        if _SHELL_METACHARS.search(command):
            return {"ok": False, "error": "Shell operators (| ; & > < `` $()) are not allowed "
                                          "when a command whitelist is in effect."}
        try:
            tokens = shlex.split(command)
        except ValueError as e:
            return {"ok": False, "error": f"Could not parse command: {e}"}
        if not tokens:
            return {"ok": False, "error": "Empty command"}
        binary = os.path.basename(tokens[0])
        if binary not in policy.allow_cmds:
            allowed = ", ".join(sorted(policy.allow_cmds))
            return {"ok": False, "error": f"'{binary}' is not in this machine's allowed commands. "
                                          f"Allowed: {allowed}"}

    timeout = args.get("timeout")
    try:
        timeout = min(int(timeout), MAX_CMD_TIMEOUT) if timeout else 60
    except (TypeError, ValueError):
        timeout = 60
    if timeout <= 0:
        timeout = 60

    try:
        proc = subprocess.run(command, shell=True, cwd=str(cwd_path),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    out = proc.stdout or ""
    err = proc.stderr or ""
    truncated = False
    if len(out) > MAX_OUTPUT_BYTES:
        out = out[:MAX_OUTPUT_BYTES]; truncated = True
    if len(err) > MAX_OUTPUT_BYTES:
        err = err[:MAX_OUTPUT_BYTES]; truncated = True
    return {"ok": True, "data": {"exit_code": proc.returncode, "stdout": out,
                                 "stderr": err, "cwd": str(cwd_path), "truncated": truncated}}


def handle_write_file(args: dict, policy: Policy) -> dict:
    if not policy.write_enabled:
        return {"ok": False, "error": "File writing is disabled on this machine. "
                                      "Start the Triton client with --allow-write to enable it."}
    raw_path = args.get("path", "")
    if not raw_path:
        return {"ok": False, "error": "No path provided"}
    target = _resolve_allowed(raw_path, policy.allow_roots)
    # The parent must also land inside the allowlist (guards against odd traversal).
    _resolve_allowed(str(target.parent), policy.allow_roots)
    if target.is_dir():
        return {"ok": False, "error": "Path is a directory"}

    content = args.get("content", "")
    try:
        raw = base64.b64decode(content) if args.get("b64") else str(content).encode("utf-8")
    except Exception as e:
        return {"ok": False, "error": f"Bad content encoding: {e}"}
    if len(raw) > MAX_READ_BYTES:
        return {"ok": False, "error": f"Content too large ({len(raw)} bytes, max {MAX_READ_BYTES})"}

    append = bool(args.get("append"))
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "ab" if append else "wb") as f:
        f.write(raw)
    return {"ok": True, "data": {"path": str(target), "bytes": len(raw), "appended": append}}


def handle_open_url(args: dict, policy: Policy) -> dict:
    if not policy.gui_enabled:
        return {"ok": False, "error": "Desktop actions are disabled. Start the client with --allow-gui."}
    url = (args.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return {"ok": False, "error": "Only http(s) URLs may be opened."}
    try:
        opened = webbrowser.open(url)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "data": {"url": url, "opened": bool(opened)}}


def handle_screenshot(args: dict, policy: Policy) -> dict:
    if not policy.gui_enabled:
        return {"ok": False, "error": "Desktop actions are disabled. Start the client with --allow-gui."}
    tmp = Path(tempfile.gettempdir()) / f"triton_shot_{uuid.uuid4().hex[:8]}.png"
    # Try the common Wayland/X11 grabbers in order; use whatever is installed.
    candidates = [
        ["grim", str(tmp)],                           # Wayland
        ["scrot", "-o", str(tmp)],                    # X11
        ["maim", str(tmp)],                           # X11
        ["gnome-screenshot", "-f", str(tmp)],
        ["spectacle", "-b", "-n", "-o", str(tmp)],    # KDE
        ["import", "-window", "root", str(tmp)],      # ImageMagick
    ]
    tried = []
    try:
        for cmd in candidates:
            if not shutil.which(cmd[0]):
                continue
            tried.append(cmd[0])
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=20)
            except Exception:
                continue
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                data = tmp.read_bytes()
                return {"ok": True, "data": {"name": "screenshot.png", "tool": cmd[0],
                                             "b64": base64.b64encode(data).decode()}}
        if not tried:
            return {"ok": False, "error": "No screenshot tool found. Install one of: "
                                          "grim (Wayland), scrot/maim (X11), gnome-screenshot, spectacle, imagemagick."}
        return {"ok": False, "error": f"Screenshot failed (tried: {', '.join(tried)}). "
                                      "On Wayland grim needs a compositor that supports wlr-screencopy."}
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


def handle_clipboard_read(args: dict, policy: Policy) -> dict:
    if not policy.gui_enabled:
        return {"ok": False, "error": "Desktop actions are disabled. Start the client with --allow-gui."}
    for cmd in (["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except Exception:
            continue
        if r.returncode == 0:
            return {"ok": True, "data": {"text": r.stdout}}
    return {"ok": False, "error": "No clipboard tool found. Install wl-clipboard (Wayland) or xclip/xsel (X11)."}


def handle_clipboard_write(args: dict, policy: Policy) -> dict:
    if not policy.gui_enabled:
        return {"ok": False, "error": "Desktop actions are disabled. Start the client with --allow-gui."}
    text = str(args.get("text", ""))
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, input=text, text=True, capture_output=True, timeout=10)
        except Exception:
            continue
        if r.returncode == 0:
            return {"ok": True, "data": {"chars": len(text)}}
    return {"ok": False, "error": "No clipboard tool found. Install wl-clipboard (Wayland) or xclip/xsel (X11)."}


def handle_process_list(args: dict, policy: Policy) -> dict:
    """Read-only snapshot of running processes. Never kills anything."""
    if not shutil.which("ps"):
        return {"ok": False, "error": "'ps' is not available on this machine."}
    try:
        r = subprocess.run(["ps", "-eo", "pid,ppid,user:20,pcpu,pmem,comm,args", "--sort=-pcpu"],
                           capture_output=True, text=True, timeout=15)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    lines = r.stdout.splitlines()
    filt = (args.get("filter") or "").lower()
    try:
        limit = min(int(args.get("limit", 50) or 50), 500)
    except (TypeError, ValueError):
        limit = 50
    procs = []
    for line in lines[1:]:
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        pid, ppid, user, cpu, mem, comm, cmdline = parts
        if filt and filt not in comm.lower() and filt not in cmdline.lower():
            continue
        procs.append({"pid": pid, "ppid": ppid, "user": user, "cpu": cpu,
                      "mem": mem, "name": comm, "cmd": cmdline[:200]})
        if len(procs) >= limit:
            break
    return {"ok": True, "data": {"processes": procs}}


def handle_service_list(args: dict, policy: Policy) -> dict:
    """Read-only snapshot of systemd services. Never starts/stops anything."""
    if not shutil.which("systemctl"):
        return {"ok": False, "error": "systemctl is not available (no systemd on this machine)."}
    try:
        r = subprocess.run(["systemctl", "list-units", "--type=service", "--all",
                            "--no-pager", "--no-legend", "--plain"],
                           capture_output=True, text=True, timeout=15)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    filt = (args.get("filter") or "").lower()
    try:
        limit = min(int(args.get("limit", 100) or 100), 500)
    except (TypeError, ValueError):
        limit = 100
    svcs = []
    for line in r.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        desc = parts[4] if len(parts) > 4 else ""
        if filt and filt not in unit.lower() and filt not in desc.lower():
            continue
        svcs.append({"unit": unit, "load": load, "active": active, "sub": sub, "description": desc})
        if len(svcs) >= limit:
            break
    return {"ok": True, "data": {"services": svcs}}


def run_action(action: str, args: dict, policy: Policy) -> dict:
    try:
        if action == "ping":
            return {"ok": True, "data": {"pong": True}}
        if action == "list_dir":
            return handle_list_dir(args, policy)
        if action == "read_file":
            return handle_read_file(args, policy)
        if action == "run_command":
            return handle_run_command(args, policy)
        if action == "write_file":
            return handle_write_file(args, policy)
        if action == "open_url":
            return handle_open_url(args, policy)
        if action == "screenshot":
            return handle_screenshot(args, policy)
        if action == "clipboard_read":
            return handle_clipboard_read(args, policy)
        if action == "clipboard_write":
            return handle_clipboard_write(args, policy)
        if action == "process_list":
            return handle_process_list(args, policy)
        if action == "service_list":
            return handle_service_list(args, policy)
        return {"ok": False, "error": f"Unsupported action: {action}"}
    except PermissionError as pe:
        return {"ok": False, "error": str(pe)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _summarize(action: str, args: dict) -> str:
    if action == "run_command":
        return (args.get("command", "") or "")[:80]
    if action == "open_url":
        return args.get("url", "") or ""
    if action in ("process_list", "service_list"):
        return args.get("filter", "") or "all"
    if action in ("clipboard_read", "clipboard_write", "screenshot"):
        return ""
    return args.get("path", "") or ""


# ── Transport: HTTP long-poll (default, proxy/CDN friendly) ──────────────────
def http_base(server: str) -> str:
    """Normalize any server URL to the HTTP(S) origin (drops ws scheme / path)."""
    u = urlparse(server)
    scheme = {"ws": "http", "wss": "https"}.get(u.scheme, u.scheme or "http")
    return urlunparse((scheme, u.netloc or u.path, "", "", "", "")).rstrip("/")


def run_poll(server: str, policy: Policy):
    try:
        import requests
    except ImportError:
        sys.exit("Missing dependency for poll transport. Install:  pip install requests")

    base = http_base(server)
    state = load_state()
    device_id = state.get("device_id") or f"linux-{uuid.uuid4().hex[:10]}"
    state["device_id"] = device_id
    save_state(state)
    name = state.get("name") or socket.gethostname()
    token = state.get("token")

    def post(path, body, timeout):
        return requests.post(base + path, json=body, timeout=timeout)

    # Ensure we have a valid token (enroll + wait for admin approval if not).
    while not token:
        code = f"{secrets.randbelow(10**8):08d}"
        try:
            r = post("/api/triton/enroll", {"device_id": device_id, "name": name,
                                            "platform": "linux", "capabilities": policy.capabilities,
                                            "code": code}, 15)
            status = r.json().get("status")
        except Exception as e:
            print(f"! enroll failed ({e}); retrying in 5s…"); time.sleep(5); continue
        if status == "ready":
            break
        print_code(code)
        # Poll for approval.
        while True:
            time.sleep(3)
            try:
                ps = post("/api/triton/pair-status", {"device_id": device_id}, 15).json()
            except Exception:
                continue
            if ps.get("status") == "paired":
                token = ps["token"]
                state["token"] = token; state["name"] = name; save_state(state)
                print("✓ Paired! This machine is now a Kokomi mooring.")
                break
        break

    print(f"✓ Online via poll transport as {name} ({device_id}). Waiting for requests…")
    # Long-poll loop.
    while True:
        try:
            r = post("/api/triton/poll", {"device_id": device_id, "token": token}, 35)
            if r.status_code == 401:
                print("✗ Token rejected (device revoked?). Clearing and re-pairing.")
                state.pop("token", None); save_state(state)
                return run_poll(server, policy)
            cmd = r.json()
        except Exception as e:
            print(f"! poll error ({e}); retrying in 3s…"); time.sleep(3); continue

        if cmd.get("type") != "command":
            continue
        result = run_action(cmd.get("action", ""), cmd.get("args", {}), policy)
        print(f"→ {cmd.get('action')}({_summarize(cmd.get('action',''), cmd.get('args',{}))}) => "
              f"{'ok' if result.get('ok') else result.get('error')}")
        try:
            post("/api/triton/result", {"device_id": device_id, "token": token,
                                        "req_id": cmd.get("req_id"), "ok": result.get("ok", False),
                                        "data": result.get("data"), "error": result.get("error")}, 30)
        except Exception as e:
            print(f"! could not return result ({e})")


# ── Transport: WebSocket (LAN / low latency) ─────────────────────────────────
async def run_ws(server: str, policy: Policy):
    try:
        import websockets
    except ImportError:
        sys.exit("Missing dependency for ws transport. Install:  pip install websockets")

    # Ensure a ws:// or wss:// URL ending in /api/triton/agent.
    u = urlparse(server)
    scheme = u.scheme if u.scheme in ("ws", "wss") else ("wss" if u.scheme == "https" else "ws")
    path = u.path if u.path and u.path != "/" else "/api/triton/agent"
    ws_url = urlunparse((scheme, u.netloc or u.path, path, "", "", ""))

    state = load_state()
    device_id = state.get("device_id") or f"linux-{uuid.uuid4().hex[:10]}"
    state["device_id"] = device_id
    save_state(state)
    name = state.get("name") or socket.gethostname()
    token = state.get("token")
    code = f"{secrets.randbelow(10**8):08d}"

    async with websockets.connect(ws_url, max_size=None) as ws:
        hello = {"type": "hello", "device_id": device_id, "name": name,
                 "platform": "linux", "capabilities": policy.capabilities}
        if token:
            hello["token"] = token
        else:
            hello["code"] = code
        await ws.send(json.dumps(hello))

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")

            if mtype == "ready":
                print(f"✓ Online via WebSocket as {name} ({device_id}).")
            elif mtype == "unpaired":
                # Stored token no longer valid — re-announce fresh with a code.
                token = None
                state.pop("token", None); save_state(state)
                code = f"{secrets.randbelow(10**8):08d}"
                await ws.send(json.dumps({"type": "hello", "device_id": device_id, "name": name,
                                          "platform": "linux", "capabilities": policy.capabilities,
                                          "code": code}))
            elif mtype == "pending":
                print_code(code)
            elif mtype == "paired":
                token = msg.get("token")
                state["token"] = token; state["name"] = name; save_state(state)
                print("✓ Paired! This machine is now a Kokomi mooring.")
                await ws.send(json.dumps({"type": "paired-ack"}))
            elif mtype == "revoked":
                state.pop("token", None); save_state(state)
                print("✗ This device was revoked. Re-run to pair again.")
                return
            elif mtype == "command":
                result = run_action(msg.get("action", ""), msg.get("args", {}), policy)
                print(f"→ {msg.get('action')}({_summarize(msg.get('action',''), msg.get('args',{}))}) => "
                      f"{'ok' if result.get('ok') else result.get('error')}")
                await ws.send(json.dumps({"type": "result", "req_id": msg.get("req_id"),
                                          "ok": result.get("ok", False),
                                          "data": result.get("data"), "error": result.get("error")}))
            elif mtype == "ping":
                await ws.send(json.dumps({"type": "pong"}))


# ── Entrypoint ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Triton client — Kokomi's remote hands on this machine.")
    ap.add_argument("--server", default=os.getenv("KOKOMI_SERVER"),
                    help="Server URL, e.g. https://kokomi.example.com or ws://192.168.1.67:8780 "
                         "(default: auto-discover on LAN)")
    ap.add_argument("--transport", choices=["poll", "ws"], default=os.getenv("KOKOMI_TRANSPORT", "poll"),
                    help="poll (default; works through nginx/Cloudflare) or ws (LAN, low latency)")
    ap.add_argument("--allow", action="append", default=None,
                    help="A folder Triton may read / run commands in (repeatable). Default: your home directory.")
    ap.add_argument("--allow-exec", action="store_true",
                    default=os.getenv("KOKOMI_ALLOW_EXEC", "").lower() in ("1", "true", "yes"),
                    help="Enable remote command execution on this machine (OFF by default).")
    ap.add_argument("--allow-cmd", action="append", default=None, metavar="BINARY",
                    help="Restrict --allow-exec to this binary, e.g. --allow-cmd git --allow-cmd ls "
                         "(repeatable). Omit to allow any command (only with --allow-exec).")
    ap.add_argument("--allow-write", action="store_true",
                    default=os.getenv("KOKOMI_ALLOW_WRITE", "").lower() in ("1", "true", "yes"),
                    help="Allow writing files inside the --allow folders (OFF by default).")
    ap.add_argument("--allow-gui", action="store_true",
                    default=os.getenv("KOKOMI_ALLOW_GUI", "").lower() in ("1", "true", "yes"),
                    help="Allow desktop actions: open a URL, screenshot, clipboard (OFF by default).")
    args = ap.parse_args()

    allow_roots = [Path(os.path.expanduser(a)).resolve() for a in (args.allow or ["~"])]
    env_cmds = [c for c in re.split(r"[,\s]+", os.getenv("KOKOMI_ALLOW_CMD", "")) if c]
    allow_cmds = (args.allow_cmd or []) + env_cmds
    policy = Policy(allow_roots, exec_enabled=args.allow_exec, allow_cmds=allow_cmds,
                    write_enabled=args.allow_write, gui_enabled=args.allow_gui)

    print("Allowed folders:", ", ".join(str(r) for r in allow_roots))
    print(f"Transport: {args.transport}")
    print("Reads: on   |   Process/service watch: on (list only, never kill)")
    if policy.exec_enabled:
        if policy.allow_cmds:
            print("Command execution: ENABLED, restricted to:", ", ".join(sorted(policy.allow_cmds)))
        else:
            print("Command execution: ENABLED for ANY command (no --allow-cmd whitelist). "
                  "Anything Kokomi runs will run as your user — use --allow-cmd to narrow this.")
    else:
        print("Command execution: disabled (pass --allow-exec to enable).")
    print(f"File writes: {'ENABLED (inside allowed folders)' if policy.write_enabled else 'disabled (pass --allow-write)'}")
    print(f"Desktop (browser/screenshot/clipboard): "
          f"{'ENABLED' if policy.gui_enabled else 'disabled (pass --allow-gui)'}")

    server = args.server
    if not server:
        print("Discovering Kokomi server on the LAN…")
        server = discover_server()
        if not server:
            sys.exit("No server found. Pass --server https://your-host  (or ws://host:port)")
        print(f"Found server: {server}")

    while True:
        try:
            if args.transport == "ws":
                asyncio.run(run_ws(server, policy))
            else:
                run_poll(server, policy)
        except KeyboardInterrupt:
            print("\nBye."); return
        except Exception as e:
            print(f"! Connection lost ({e}); retrying in 5s…")
        time.sleep(5)


if __name__ == "__main__":
    main()
