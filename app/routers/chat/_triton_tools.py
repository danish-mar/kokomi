"""
Triton chat tools — let the conversational model actually reach the user's
paired computers (moorings). Without these, Kokomi has no idea a paired machine
like "electro" exists and will (correctly) say it has no access.

Exposed actions:
  • triton_list_devices — what machines are paired and online
  • triton_list_dir     — list a folder on a machine
  • triton_fetch_file   — pull a file back into the chat as a download link
  • triton_run_command  — run a shell command (only if the machine enabled exec)

The mooring's own allowlist is the security boundary; these tools just route.
Command execution is opt-in per machine (client --allow-exec) and may be further
restricted to specific binaries and folders — a disabled/blocked command comes
back as a clear error, never as a silent success.
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

    exec_devices = [d.get("name", d["id"]) for d in devices
                    if "run_command" in (d.get("capabilities") or [])]
    exec_line = (
        "- triton_run_command(device, command, cwd) — run a shell command. "
        + (f"Enabled on: {', '.join(exec_devices)}. " if exec_devices
           else "No paired machine has enabled command execution yet; calling this returns a "
                "'disabled' error the user can fix by restarting the client with --allow-exec. ")
        + "Blocked/disallowed commands return a permission error — never assume a command ran.\n"
    )

    note = (
        "\n\n[TRITON ENABLED]\n"
        f"The user has paired computer(s) with Triton — you CAN reach them: {roster}. "
        "When they ask you to find, grab, check, or send a file from their computer "
        "(e.g. 'grab the presentation from my Downloads', 'what's in my home folder', "
        "'send me the file I worked on'), USE the Triton tools:\n"
        "- triton_list_devices — see which machines are paired/online.\n"
        "- triton_list_dir(device, path) — browse a folder to find the file.\n"
        "- triton_fetch_file(device, path) — pull a file in; include the returned "
        "markdown download link in your reply so the user can download it.\n"
        + exec_line +
        "Only 'online' machines can be reached. If the target folder isn't shared "
        "you'll get a permission error — tell the user to widen the client's --allow "
        "list.\n"
        "[/TRITON ENABLED]"
    )
    return [triton_list_devices, triton_list_dir, triton_fetch_file, triton_run_command], note
