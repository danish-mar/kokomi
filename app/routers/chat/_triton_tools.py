"""
Triton chat tools — let the conversational model actually reach the user's
paired computers (moorings). Without these, Kokomi has no idea a paired machine
like "electro" exists and will (correctly) say it has no access.

Always-on tools: list_devices, list_dir, fetch_file, list_processes,
list_services (the last two are read-only — Triton never kills or stops).

Opt-in tools, each enabled per machine by a client flag:
  • run_command                  -> --allow-exec
  • write_file                   -> --allow-write
  • open_url / screenshot /
    clipboard_get / clipboard_set -> --allow-gui

The mooring's own allowlist is the security boundary; these tools just route.
A disabled/blocked action comes back as a clear error, never a silent success.
"""
import base64
import os
import re
import uuid

from langchain_core.tools import tool

from app.triton import manager
from app.storage import list_triton_devices

UPLOADS_DIR = os.path.join("data", "uploads")


def _resolve_device(device: str):
    """Map a model-supplied name/id to a paired device_id, or (None, error)."""
    devices = list_triton_devices()
    if not devices:
        return None, "No computers are paired with Triton yet."
    q = (device or "").strip().lower()
    # exact id, then exact name, then substring name
    for d in devices:
        if d["id"].lower() == q or (d.get("name", "").lower() == q):
            return d["id"], None
    for d in devices:
        if q and q in d.get("name", "").lower():
            return d["id"], None
    names = ", ".join(d.get("name", d["id"]) for d in devices)
    return None, f"No paired computer matches '{device}'. Available: {names}."


def get_triton_tools():
    """Return (tools, prompt_note). Empty when no machines are paired."""
    devices = list_triton_devices()
    if not devices:
        return [], ""

    online = manager.online_ids()
    lines = []
    for d in devices:
        state = "online" if d["id"] in online else "offline"
        lines.append(f"{d.get('name', d['id'])} ({d.get('platform', '?')}, {state})")
    roster = "; ".join(lines)

    @tool("triton_list_devices")
    async def triton_list_devices() -> str:
        """List the user's paired computers (Triton moorings) and whether each is online right now."""
        devs = list_triton_devices()
        on = manager.online_ids()
        if not devs:
            return "No computers are paired with Triton."
        return "\n".join(
            f"- {d.get('name', d['id'])} — {d.get('platform', '?')} — "
            f"{'online' if d['id'] in on else 'offline'}"
            for d in devs
        )

    @tool("triton_list_dir")
    async def triton_list_dir(device: str, path: str = "~") -> str:
        """List files and folders in a directory on one of the user's paired computers.

        device: the computer's name (e.g. 'electro') or id.
        path: directory to list, e.g. '~/Downloads'. Must be within a folder the
        client allows; a permission error means that folder isn't shared.
        """
        device_id, err = _resolve_device(device)
        if err:
            return err
        res = await manager.dispatch(device_id, "list_dir", {"path": path})
        if not res.get("ok"):
            return f"Couldn't list '{path}' on {device}: {res.get('error')}"
        data = res.get("data", {})
        entries = data.get("entries", [])
        if not entries:
            return f"{data.get('path', path)} is empty."
        out = [f"Contents of {data.get('path', path)} on {device}:"]
        for e in entries[:200]:
            tag = "📁" if e.get("is_dir") else "📄"
            size = "" if e.get("is_dir") or e.get("size") is None else f" ({e['size']} bytes)"
            out.append(f"{tag} {e['name']}{size}")
        return "\n".join(out)

    @tool("triton_fetch_file")
    async def triton_fetch_file(device: str, path: str) -> str:
        """Fetch a file from one of the user's paired computers and attach it to the
        chat as a download link. Use this to bring a file from the user's machine
        into the conversation (e.g. 'grab my presentation from Downloads').

        device: the computer's name or id.  path: full path to the file.
        Returns a markdown download link to include in your reply.
        """
        device_id, err = _resolve_device(device)
        if err:
            return err
        res = await manager.dispatch(device_id, "read_file", {"path": path}, timeout=60)
        if not res.get("ok"):
            return f"Couldn't fetch '{path}' from {device}: {res.get('error')}"
        data = res.get("data", {})
        try:
            raw = base64.b64decode(data.get("b64", ""))
        except Exception:
            return "The file came back corrupted; try again."
        name = os.path.basename(data.get("name") or path) or "file"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "file"
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        stored = f"triton_{uuid.uuid4().hex[:8]}_{safe}"
        with open(os.path.join(UPLOADS_DIR, stored), "wb") as f:
            f.write(raw)
        kb = max(1, len(raw) // 1024)
        return (f"Fetched **{name}** ({kb} KB) from {device}. "
                f"Give the user this download link: [{name}](/uploads/{stored})")

    @tool("triton_run_command")
    async def triton_run_command(device: str, command: str, cwd: str = "") -> str:
        """Run a shell command on one of the user's paired computers and return its output.

        This only works if the machine's owner started the Triton client with command
        execution enabled (--allow-exec). The machine may further restrict which binaries
        are allowed and which folders commands may run in; if so, a blocked command comes
        back as a permission error — relay it and suggest the user widen the client's
        --allow-cmd / --allow list.

        device: the computer's name (e.g. 'electro') or id.
        command: the shell command to run, e.g. 'git status' or 'ls -la'.
        cwd: optional working directory (must be within a shared folder). Defaults to the
        machine's first allowed folder.
        """
        device_id, err = _resolve_device(device)
        if err:
            return err
        cargs = {"command": command}
        if cwd:
            cargs["cwd"] = cwd
        res = await manager.dispatch(device_id, "run_command", cargs, timeout=310)
        if not res.get("ok"):
            return f"Couldn't run `{command}` on {device}: {res.get('error')}"
        data = res.get("data", {})
        code = data.get("exit_code", "?")
        out = (data.get("stdout") or "").rstrip()
        errout = (data.get("stderr") or "").rstrip()
        parts = [f"`{command}` on {device} exited with code {code} (cwd: {data.get('cwd', cwd or '~')})."]
        if out:
            parts.append(f"\nstdout:\n```\n{out}\n```")
        if errout:
            parts.append(f"\nstderr:\n```\n{errout}\n```")
        if not out and not errout:
            parts.append("\n(no output)")
        if data.get("truncated"):
            parts.append("\n(output was truncated)")
        return "".join(parts)

    @tool("triton_write_file")
    async def triton_write_file(device: str, path: str, content: str, append: bool = False) -> str:
        """Write text to a file on one of the user's paired computers.

        Only works if the machine's owner started the client with --allow-write, and the
        path must be inside a shared (--allow) folder. Creates the file (and parent folders)
        if needed. Use append=True to add to the end instead of overwriting.

        device: the computer's name or id.  path: full destination path.
        content: the text to write.  append: append instead of overwrite.
        """
        device_id, err = _resolve_device(device)
        if err:
            return err
        res = await manager.dispatch(device_id, "write_file",
                                     {"path": path, "content": content, "append": append}, timeout=60)
        if not res.get("ok"):
            return f"Couldn't write '{path}' on {device}: {res.get('error')}"
        data = res.get("data", {})
        verb = "Appended" if data.get("appended") else "Wrote"
        return f"{verb} {data.get('bytes', 0)} bytes to {data.get('path', path)} on {device}."

    @tool("triton_open_url")
    async def triton_open_url(device: str, url: str) -> str:
        """Open an http(s) URL in the default web browser on one of the user's paired computers
        (e.g. 'pull up my calendar on electro'). Requires the client to be started with
        --allow-gui. device: the computer's name or id.  url: the http(s) address to open.
        """
        device_id, err = _resolve_device(device)
        if err:
            return err
        res = await manager.dispatch(device_id, "open_url", {"url": url}, timeout=30)
        if not res.get("ok"):
            return f"Couldn't open the URL on {device}: {res.get('error')}"
        return f"Opened {url} in the browser on {device}."

    @tool("triton_screenshot")
    async def triton_screenshot(device: str) -> str:
        """Take a screenshot of the current desktop on one of the user's paired computers and
        attach it to the chat as an image. Requires the client to be started with --allow-gui.
        device: the computer's name or id. Returns markdown to embed the image in your reply.
        """
        device_id, err = _resolve_device(device)
        if err:
            return err
        res = await manager.dispatch(device_id, "screenshot", {}, timeout=45)
        if not res.get("ok"):
            return f"Couldn't screenshot {device}: {res.get('error')}"
        data = res.get("data", {})
        try:
            raw = base64.b64decode(data.get("b64", ""))
        except Exception:
            return "The screenshot came back corrupted; try again."
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        stored = f"triton_{uuid.uuid4().hex[:8]}_screenshot.png"
        with open(os.path.join(UPLOADS_DIR, stored), "wb") as f:
            f.write(raw)
        return (f"Captured the screen on {device}. "
                f"Show the user this screenshot: ![screenshot on {device}](/uploads/{stored})")

    @tool("triton_clipboard_get")
    async def triton_clipboard_get(device: str) -> str:
        """Read the clipboard contents of one of the user's paired computers. Requires the
        client to be started with --allow-gui. device: the computer's name or id.
        """
        device_id, err = _resolve_device(device)
        if err:
            return err
        res = await manager.dispatch(device_id, "clipboard_read", {}, timeout=30)
        if not res.get("ok"):
            return f"Couldn't read the clipboard on {device}: {res.get('error')}"
        text = res.get("data", {}).get("text", "")
        if not text:
            return f"The clipboard on {device} is empty."
        return f"Clipboard on {device}:\n```\n{text[:8000]}\n```"

    @tool("triton_clipboard_set")
    async def triton_clipboard_set(device: str, text: str) -> str:
        """Set the clipboard contents on one of the user's paired computers. Requires the
        client to be started with --allow-gui. device: the computer's name or id.
        text: the text to place on the clipboard.
        """
        device_id, err = _resolve_device(device)
        if err:
            return err
        res = await manager.dispatch(device_id, "clipboard_write", {"text": text}, timeout=30)
        if not res.get("ok"):
            return f"Couldn't set the clipboard on {device}: {res.get('error')}"
        return f"Copied {res.get('data', {}).get('chars', len(text))} characters to the clipboard on {device}."

    @tool("triton_list_processes")
    async def triton_list_processes(device: str, filter: str = "", limit: int = 40) -> str:
        """List running processes on one of the user's paired computers (read-only — Triton
        cannot kill processes). Sorted by CPU usage.

        device: the computer's name or id.
        filter: optional substring to match against the process name/command.
        limit: max processes to return (default 40).
        """
        device_id, err = _resolve_device(device)
        if err:
            return err
        res = await manager.dispatch(device_id, "process_list",
                                     {"filter": filter, "limit": limit}, timeout=30)
        if not res.get("ok"):
            return f"Couldn't list processes on {device}: {res.get('error')}"
        procs = res.get("data", {}).get("processes", [])
        if not procs:
            return f"No matching processes on {device}."
        lines = [f"Processes on {device} (by CPU):", "PID    USER            %CPU  %MEM  COMMAND"]
        for p in procs:
            lines.append(f"{p.get('pid',''):<6} {p.get('user',''):<15} "
                         f"{p.get('cpu',''):>4}  {p.get('mem',''):>4}  {p.get('name','')}")
        return "\n".join(lines)

    @tool("triton_list_services")
    async def triton_list_services(device: str, filter: str = "", limit: int = 60) -> str:
        """List systemd services and their state on one of the user's paired computers
        (read-only — Triton cannot start/stop services).

        device: the computer's name or id.
        filter: optional substring to match against the unit name/description.
        limit: max services to return (default 60).
        """
        device_id, err = _resolve_device(device)
        if err:
            return err
        res = await manager.dispatch(device_id, "service_list",
                                     {"filter": filter, "limit": limit}, timeout=30)
        if not res.get("ok"):
            return f"Couldn't list services on {device}: {res.get('error')}"
        svcs = res.get("data", {}).get("services", [])
        if not svcs:
            return f"No matching services on {device}."
        lines = [f"Services on {device}:", "UNIT                                ACTIVE    SUB"]
        for s in svcs:
            lines.append(f"{s.get('unit',''):<35} {s.get('active',''):<9} {s.get('sub','')}")
        return "\n".join(lines)

    def _cap_devices(cap):
        return [d.get("name", d["id"]) for d in devices if cap in (d.get("capabilities") or [])]

    def _gated_line(desc, cap, flag):
        on = _cap_devices(cap)
        tail = (f"Enabled on: {', '.join(on)}." if on
                else f"No paired machine has this enabled yet; calling it returns a "
                     f"'disabled' error the user fixes by restarting the client with {flag}.")
        return f"- {desc} {tail} A disabled/blocked action returns a permission error — never assume it succeeded.\n"

    note = (
        "\n\n[TRITON ENABLED]\n"
        f"The user has paired computer(s) with Triton — you CAN reach them: {roster}. "
        "When they ask you to do something on their computer, USE the Triton tools. "
        "Only 'online' machines can be reached; path actions are confined to folders the "
        "client shares (--allow), and a permission error means the user must widen that.\n"
        "Always-on tools:\n"
        "- triton_list_devices — see which machines are paired/online.\n"
        "- triton_list_dir(device, path) — browse a folder.\n"
        "- triton_fetch_file(device, path) — pull a file into the chat; include the returned "
        "markdown download link in your reply.\n"
        "- triton_list_processes(device, filter) — read-only process snapshot (cannot kill).\n"
        "- triton_list_services(device, filter) — read-only systemd service states (cannot start/stop).\n"
        "Opt-in tools (each machine must enable them):\n"
        + _gated_line("triton_run_command(device, command, cwd) — run a shell command.",
                      "run_command", "--allow-exec")
        + _gated_line("triton_write_file(device, path, content, append) — write a file.",
                      "write_file", "--allow-write")
        + _gated_line("triton_open_url(device, url) — open a URL in the browser.",
                      "open_url", "--allow-gui")
        + _gated_line("triton_screenshot(device) — capture the screen (embed the returned image).",
                      "screenshot", "--allow-gui")
        + _gated_line("triton_clipboard_get(device) / triton_clipboard_set(device, text) — read/set the clipboard.",
                      "clipboard_read", "--allow-gui")
        + "[/TRITON ENABLED]"
    )
    return [triton_list_devices, triton_list_dir, triton_fetch_file, triton_run_command,
            triton_write_file, triton_open_url, triton_screenshot, triton_clipboard_get,
            triton_clipboard_set, triton_list_processes, triton_list_services], note
