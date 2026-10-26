"""
app/storage.py — SQLite-backed storage layer for Kokomi.

All public functions preserve their original call signatures so existing
routers require minimal changes.  The heavy conversation/message functions
are truly async; the small config functions (chars, mcp, prefs, etc.) are
sync wrappers that spin up their own short-lived event loops when called
from synchronous context, or can be awaited when called from async context.

NOTE: _load() and _save() are kept as thin JSON helpers for the modules
that manage their own files (workflow.py → multi_agent_workflows.json,
scheduler.py → scheduled_workflows.json, routers/workflows.py → workflows.json).
Those files are NOT migrated to SQLite because they have custom in-memory caching
and their own async-threadsafe save logic already.
"""

import json
import datetime
import asyncio
import os
from typing import Any

from app.config import USER_PREFS_FILE, DEFAULT_PREFS
from app.db import (
    _session, j_dumps, j_loads,
    ConversationRow, MessageRow, CharacterRow, McpServerRow,
    SpaceRow, AgentTemplateRow, FolderRow, TritonDeviceRow, UserPrefsRow, UserRow
)
from sqlalchemy import select, delete, update, or_


# ─────────────────────────────────────────────────────────────────────────────
# Low-level JSON helpers — kept for workflow.py / scheduler.py
# ─────────────────────────────────────────────────────────────────────────────

def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return {}


def _save(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)          # atomic rename — no partial-write corruption


# ─────────────────────────────────────────────────────────────────────────────
# Preferences (still JSON — see implementation plan note)
# ─────────────────────────────────────────────────────────────────────────────

# def load_prefs() -> dict:
#     prefs = _load(USER_PREFS_FILE)
#     if not prefs:
#         prefs = DEFAULT_PREFS.copy()
#         _save(USER_PREFS_FILE, prefs)
#         return prefs
#     changed = False
#     for k, v in DEFAULT_PREFS.items():
#         if k not in prefs:
#             prefs[k] = v
#             changed = True
#     if changed:
#         _save(USER_PREFS_FILE, prefs)
#     return prefs

_PREFS_CACHE: dict = {}

async def init_prefs_cache() -> None:
    """Load once on server boot. Reads it from the PostgreSQL/SQLite database."""
    global _PREFS_CACHE
    async with _session() as sess:
        row = await sess.get(UserPrefsRow, "default")
        if row and row.data:
            loaded = j_loads(row.data) or {}
            merged = DEFAULT_PREFS.copy()
            merged.update(loaded)
            _PREFS_CACHE = merged
        else:
            # Check if there is an existing user_prefs.json file to import
            existing_file_prefs = _load(USER_PREFS_FILE)
            if existing_file_prefs:
                merged = DEFAULT_PREFS.copy()
                merged.update(existing_file_prefs)
                _PREFS_CACHE = merged
            else:
                _PREFS_CACHE = DEFAULT_PREFS.copy()
            now = datetime.datetime.utcnow().isoformat()
            sess.add(UserPrefsRow(id="default", data=j_dumps(_PREFS_CACHE), updated_at=now))


def load_prefs() -> dict:
    """Instant synchronous read from memory."""
    global _PREFS_CACHE
    if not _PREFS_CACHE:
        # Fallback if accessed before lifespan (e.g. CLI script)
        _PREFS_CACHE = DEFAULT_PREFS.copy()
    return _PREFS_CACHE
async def save_prefs_async(prefs: dict) -> None:
    """Persist preferences safely into PostgreSQL."""
    global _PREFS_CACHE
    _PREFS_CACHE = prefs.copy()
    now = datetime.datetime.utcnow().isoformat()
    
    async with _session() as sess:
        row = await sess.get(UserPrefsRow, "default")
        if row is None:
            sess.add(UserPrefsRow(id="default", data=j_dumps(prefs), updated_at=now))
        else:
            row.data = j_dumps(prefs)
            row.updated_at = now
def save_prefs(prefs: dict) -> None:
    """Sync wrapper for save_prefs_async."""
    global _PREFS_CACHE
    _PREFS_CACHE = prefs.copy()
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(save_prefs_async(prefs))
    except RuntimeError:
        _run_async(save_prefs_async(prefs))


async def load_user_prefs_async(user_id: str | None, default_name: str | None = None) -> dict:
    """Load user preferences overlaid onto global preferences."""
    base_prefs = load_prefs().copy()
    base_prefs["user_avatar"] = None
    if not user_id:
        return base_prefs

    async with _session() as sess:
        u = await sess.get(UserRow, user_id)
        if u and u.avatar:
            base_prefs["user_avatar"] = u.avatar

        row = await sess.get(UserPrefsRow, f"user_{user_id}")
        if row and row.data:
            user_data = j_loads(row.data) or {}
            base_prefs.update(user_data)
            if u and u.avatar:
                base_prefs["user_avatar"] = u.avatar
            elif "user_avatar" not in user_data:
                base_prefs["user_avatar"] = None
        else:
            if default_name:
                base_prefs["user_name"] = default_name
    return base_prefs


async def save_user_prefs_async(user_id: str | None, prefs: dict) -> None:
    """Persist user-scoped preferences."""
    if not user_id:
        await save_prefs_async(prefs)
        return
    now = datetime.datetime.utcnow().isoformat()
    async with _session() as sess:
        row = await sess.get(UserPrefsRow, f"user_{user_id}")
        if row is None:
            sess.add(UserPrefsRow(id=f"user_{user_id}", data=j_dumps(prefs), updated_at=now))
        else:
            row.data = j_dumps(prefs)
            row.updated_at = now


# ─────────────────────────────────────────────────────────────────────────────
# Internal async helpers — building the dict shape that callers expect
# ─────────────────────────────────────────────────────────────────────────────

def _conv_row_to_dict(row: ConversationRow, messages: list) -> dict:
    return {
        "id": row.id,
        "user_id": getattr(row, "user_id", None),
        "title": row.title,
        "character_id": row.character_id,
        "participants": j_loads(row.participants) or [],
        "is_anonymous": bool(row.is_anonymous),
        "folder_id": row.folder_id,
        "updated_at": row.updated_at,
        "last_active": row.last_active,
        "branches": j_loads(row.branches) or {},
        "messages": messages,
    }


def _msg_row_to_dict(row: MessageRow) -> dict:
    return {
        "role": row.role,
        "content": row.content,
        "thinking": row.thinking,
        "tool_calls": j_loads(row.tool_calls),
        "artifacts": j_loads(row.artifacts),
        "model": row.model,
        "character_id": row.character_id,
        "character_name": row.character_name,
        "metrics": j_loads(row.metrics),
        "timestamp": row.timestamp,
        "group_id": row.group_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Conversations — ASYNC
# ─────────────────────────────────────────────────────────────────────────────

async def load_convos_async(user_id: str | None = None) -> dict:
    """Load conversations with their messages as a dict keyed by conv id.
    If user_id is provided, only loads conversations belonging to that user.
    """
    async with _session() as sess:
        query = select(ConversationRow)
        if user_id:
            query = query.where(ConversationRow.user_id == user_id)
        conv_result = await sess.execute(query)
        conv_rows = conv_result.scalars().all()
        conv_ids = [r.id for r in conv_rows]

        if conv_ids:
            msg_result = await sess.execute(
                select(MessageRow).where(MessageRow.conversation_id.in_(conv_ids)).order_by(MessageRow.id.asc())
            )
            msg_rows = msg_result.scalars().all()
        else:
            msg_rows = []

    # Group messages by conversation_id
    msgs_by_conv: dict[str, list] = {}
    for m in msg_rows:
        msgs_by_conv.setdefault(m.conversation_id, []).append(_msg_row_to_dict(m))

    result = {}
    for row in conv_rows:
        result[row.id] = _conv_row_to_dict(row, msgs_by_conv.get(row.id, []))
    return result


async def save_convos_async(convos: dict, user_id: str | None = None) -> None:
    """
    Upsert conversations and messages.
    If user_id is provided, only deletes orphaned conversations belonging to that user.
    """
    async with _session() as sess:
        conv_ids = list(convos.keys())

        # Delete orphaned conversations (ones removed from the dict for this user)
        if user_id:
            all_rows = await sess.execute(
                select(ConversationRow.id).where(ConversationRow.user_id == user_id)
            )
            existing_ids = {r for (r,) in all_rows}
            to_delete = existing_ids - set(conv_ids)
            if to_delete:
                await sess.execute(
                    delete(ConversationRow).where(
                        ConversationRow.user_id == user_id,
                        ConversationRow.id.in_(to_delete)
                    )
                )

        for conv_id, conv in convos.items():
            now = datetime.datetime.utcnow().isoformat()
            c_user_id = conv.get("user_id") or user_id

            # Upsert conversation row
            existing = await sess.get(ConversationRow, conv_id)
            if existing is None:
                sess.add(ConversationRow(
                    id=conv_id,
                    user_id=c_user_id,
                    title=conv.get("title", "Untitled"),
                    character_id=conv.get("character_id"),
                    participants=j_dumps(conv.get("participants", [])),
                    is_anonymous=int(conv.get("is_anonymous", False)),
                    folder_id=conv.get("folder_id"),
                    updated_at=conv.get("updated_at") or now,
                    last_active=conv.get("last_active"),
                    branches=j_dumps(conv.get("branches", {})),
                ))
            else:
                if c_user_id and not existing.user_id:
                    existing.user_id = c_user_id
                existing.title = conv.get("title", existing.title)
                existing.character_id = conv.get("character_id", existing.character_id)
                existing.participants = j_dumps(conv.get("participants", []))
                existing.is_anonymous = int(conv.get("is_anonymous", False))
                existing.folder_id = conv.get("folder_id")
                existing.updated_at = conv.get("updated_at") or now
                existing.last_active = conv.get("last_active")
                existing.branches = j_dumps(conv.get("branches", {}))

            # Replace all messages for this conv (simplest correct approach)
            await sess.execute(
                delete(MessageRow).where(MessageRow.conversation_id == conv_id)
            )
            for msg in conv.get("messages", []):
                sess.add(MessageRow(
                    conversation_id=conv_id,
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    thinking=msg.get("thinking"),
                    tool_calls=j_dumps(msg.get("tool_calls")),
                    artifacts=j_dumps(msg.get("artifacts")),
                    model=msg.get("model"),
                    character_id=msg.get("character_id"),
                    character_name=msg.get("character_name"),
                    metrics=j_dumps(msg.get("metrics")),
                    timestamp=msg.get("timestamp"),
                    group_id=msg.get("group_id"),
                ))


# ─── Sync shims (for callers that can't easily be made async) ─────────────────

def _run_async(coro):
    """Run an async coroutine from sync context safely."""
    try:
        loop = asyncio.get_running_loop()
        # We're inside an async context — schedule and block via a thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        # No running loop — just run directly
        return asyncio.run(coro)


def load_convos() -> dict:
    return _run_async(load_convos_async())


def save_convos(d: dict) -> None:
    _run_async(save_convos_async(d))


# ─────────────────────────────────────────────────────────────────────────────
# Characters
# ─────────────────────────────────────────────────────────────────────────────

def _char_row_to_dict(row: CharacterRow) -> dict:
    return {
        "id": row.id,
        "user_id": getattr(row, "user_id", None),
        "name": row.name,
        "persona": row.persona or "",
        "avatar": row.avatar,
        "voice": row.voice,
        "memory_enabled": bool(row.memory_enabled) if row.memory_enabled is not None else None,
        "google_model": row.google_model or "default",
        "groq_model": row.groq_model or "default",
        "nvidia_model": row.nvidia_model,
        "custom_model": row.custom_model or "default",
        "mcp_servers": j_loads(row.mcp_servers) or [],
        "selected_tools": j_loads(row.selected_tools) or [],
        "created_at": row.created_at,
    }


def _default_chars() -> dict:
    now = datetime.datetime.utcnow().isoformat()
    return {
        "kokomi": {
            "id": "kokomi", "name": "Kokomi",
            "persona": (
                "You are Kokomi, the Divine Priestess of Watatsumi Island. "
                "You are a brilliant strategist and a gentle, thoughtful leader. "
                "You use markdown for formatting. Speak with grace and wisdom."
            ),
            "avatar": None, "mcp_servers": [], "selected_tools": [], "google_model": "default",
            "groq_model": "default", "nvidia_model": None, "custom_model": "default",
            "voice": "kore", "memory_enabled": True, "created_at": now,
        },
        "nahida": {
            "id": "nahida", "name": "Nahida",
            "persona": (
                "You are Nahida, the Lesser Lord Kusanali and the Avatar of Irminsul. "
                "You are wise, curious, and speak in beautiful metaphors. "
                "You are deeply knowledgeable about the world and treat everyone with "
                "kindness and a sense of wonder. Use markdown for formatting."
            ),
            "avatar": None, "mcp_servers": [], "selected_tools": [], "google_model": "default",
            "groq_model": "default", "nvidia_model": None, "custom_model": "default",
            "voice": "aoede", "memory_enabled": True, "created_at": now,
        },
    }


async def _load_chars_async(user_id: str | None = None) -> dict:
    async with _session() as sess:
        query = select(CharacterRow)
        if user_id:
            query = query.where(or_(CharacterRow.user_id == None, CharacterRow.user_id == user_id))
        rows = (await sess.execute(query)).scalars().all()
    if not rows:
        defaults = _default_chars()
        await _save_chars_async(defaults)
        return defaults
    return {r.id: _char_row_to_dict(r) for r in rows}


async def _save_chars_async(chars: dict, user_id: str | None = None) -> None:
    async with _session() as sess:
        for cid, char in chars.items():
            existing = await sess.get(CharacterRow, cid)
            c_user_id = char.get("user_id") or user_id
            if existing is None:
                sess.add(CharacterRow(
                    id=cid,
                    user_id=c_user_id,
                    name=char.get("name", ""),
                    persona=char.get("persona", ""),
                    avatar=char.get("avatar"),
                    voice=char.get("voice"),
                    memory_enabled=int(char["memory_enabled"]) if char.get("memory_enabled") is not None else None,
                    google_model=char.get("google_model", "default"),
                    groq_model=char.get("groq_model", "default"),
                    nvidia_model=char.get("nvidia_model"),
                    custom_model=char.get("custom_model", "default"),
                    mcp_servers=j_dumps(char.get("mcp_servers", [])),
                    selected_tools=j_dumps(char.get("selected_tools", [])),
                    created_at=char.get("created_at"),
                ))
            else:
                if c_user_id and not existing.user_id:
                    existing.user_id = c_user_id
                existing.name = char.get("name", existing.name)
                existing.persona = char.get("persona", existing.persona)
                existing.avatar = char.get("avatar", existing.avatar)
                existing.voice = char.get("voice", existing.voice)
                existing.memory_enabled = int(char["memory_enabled"]) if char.get("memory_enabled") is not None else None
                existing.google_model = char.get("google_model", "default")
                existing.groq_model = char.get("groq_model", "default")
                existing.nvidia_model = char.get("nvidia_model")
                existing.custom_model = char.get("custom_model", "default")
                existing.mcp_servers = j_dumps(char.get("mcp_servers", []))
                existing.selected_tools = j_dumps(char.get("selected_tools", []))

        # Delete custom characters removed from dict (never delete system characters or other users' characters)
        if user_id:
            user_rows = await sess.execute(
                select(CharacterRow.id).where(CharacterRow.user_id == user_id)
            )
            existing_user_ids = {r for (r,) in user_rows}
            to_remove = existing_user_ids - set(chars.keys())
            for rid in to_remove:
                row = await sess.get(CharacterRow, rid)
                if row:
                    await sess.delete(row)


load_chars_async = _load_chars_async
save_chars_async = _save_chars_async

def load_chars() -> dict:
    return _run_async(_load_chars_async())


def save_chars(d: dict) -> None:
    _run_async(_save_chars_async(d))


# ─────────────────────────────────────────────────────────────────────────────
# MCP Servers
# ─────────────────────────────────────────────────────────────────────────────

def _mcp_row_to_dict(row: McpServerRow) -> dict:
    return {
        "id": row.id, "name": row.name, "transport": row.transport,
        "command": row.command, "args": j_loads(row.args) or [],
        "env": j_loads(row.env) or {}, "url": row.url, "icon": row.icon,
        "enabled": bool(row.enabled), "created_at": row.created_at,
    }


async def _load_mcp_async() -> dict:
    async with _session() as sess:
        rows = (await sess.execute(select(McpServerRow))).scalars().all()
    return {r.id: _mcp_row_to_dict(r) for r in rows}


async def _save_mcp_async(servers: dict) -> None:
    async with _session() as sess:
        existing_ids_res = await sess.execute(select(McpServerRow.id))
        existing_ids = {r for (r,) in existing_ids_res}
        for sid, s in servers.items():
            if sid not in existing_ids:
                sess.add(McpServerRow(
                    id=sid, name=s.get("name", ""), transport=s.get("transport"),
                    command=s.get("command"), args=j_dumps(s.get("args", [])),
                    env=j_dumps(s.get("env", {})), url=s.get("url"),
                    icon=s.get("icon"), enabled=int(s.get("enabled", True)),
                    created_at=s.get("created_at"),
                ))
            else:
                row = await sess.get(McpServerRow, sid)
                row.name = s.get("name", row.name)
                row.transport = s.get("transport", row.transport)
                row.command = s.get("command", row.command)
                row.args = j_dumps(s.get("args", []))
                row.env = j_dumps(s.get("env", {}))
                row.url = s.get("url", row.url)
                row.icon = s.get("icon", row.icon)
                row.enabled = int(s.get("enabled", True))
        for rid in existing_ids - set(servers.keys()):
            row = await sess.get(McpServerRow, rid)
            if row:
                await sess.delete(row)


load_mcp_async = _load_mcp_async
save_mcp_async = _save_mcp_async

def load_mcp() -> dict:
    return _run_async(_load_mcp_async())


def save_mcp(d: dict) -> None:
    _run_async(_save_mcp_async(d))


# ─────────────────────────────────────────────────────────────────────────────
# Folders
# ─────────────────────────────────────────────────────────────────────────────

def _folder_row_to_dict(row: FolderRow) -> dict:
    return {
        "id": row.id,
        "user_id": getattr(row, "user_id", None),
        "name": row.name,
        "icon": row.icon,
        "conversation_ids": j_loads(row.conversation_ids) or [],
        "created_at": row.created_at,
    }


async def _load_folders_async(user_id: str | None = None) -> dict:
    async with _session() as sess:
        query = select(FolderRow)
        if user_id:
            query = query.where(FolderRow.user_id == user_id)
        rows = (await sess.execute(query)).scalars().all()
    return {r.id: _folder_row_to_dict(r) for r in rows}


async def _save_folders_async(folders: dict, user_id: str | None = None) -> None:
    async with _session() as sess:
        query = select(FolderRow.id)
        if user_id:
            query = query.where(FolderRow.user_id == user_id)
        existing_ids_res = await sess.execute(query)
        existing_ids = {r for (r,) in existing_ids_res}
        for fid, f in folders.items():
            f_user_id = f.get("user_id") or user_id
            if fid not in existing_ids:
                sess.add(FolderRow(
                    id=fid,
                    user_id=f_user_id,
                    name=f.get("name", ""),
                    icon=f.get("icon"),
                    conversation_ids=j_dumps(f.get("conversation_ids", [])),
                    created_at=f.get("created_at"),
                ))
            else:
                row = await sess.get(FolderRow, fid)
                if row:
                    if f_user_id and not row.user_id:
                        row.user_id = f_user_id
                    row.name = f.get("name", row.name)
                    row.icon = f.get("icon", row.icon)
                    row.conversation_ids = j_dumps(f.get("conversation_ids", []))
        for rid in existing_ids - set(folders.keys()):
            row = await sess.get(FolderRow, rid)
            if row:
                await sess.delete(row)


load_folders_async = _load_folders_async
save_folders_async = _save_folders_async

def load_folders() -> dict:
    return _run_async(_load_folders_async())


def save_folders(d: dict) -> None:
    _run_async(_save_folders_async(d))


# ─────────────────────────────────────────────────────────────────────────────
# Spaces
# ─────────────────────────────────────────────────────────────────────────────

def _space_row_to_dict(row: SpaceRow) -> dict:
    return {
        "id": row.id, "name": row.name, "description": row.description or "",
        "icon": row.icon, "files": j_loads(row.files) or [],
        "created_at": row.created_at,
    }


async def _load_spaces_async() -> dict:
    async with _session() as sess:
        rows = (await sess.execute(select(SpaceRow))).scalars().all()
    return {r.id: _space_row_to_dict(r) for r in rows}


async def _save_spaces_async(spaces: dict) -> None:
    async with _session() as sess:
        existing_ids_res = await sess.execute(select(SpaceRow.id))
        existing_ids = {r for (r,) in existing_ids_res}
        for sid, s in spaces.items():
            if sid not in existing_ids:
                sess.add(SpaceRow(
                    id=sid, name=s.get("name", ""), description=s.get("description", ""),
                    icon=s.get("icon"), files=j_dumps(s.get("files", [])),
                    created_at=s.get("created_at"),
                ))
            else:
                row = await sess.get(SpaceRow, sid)
                row.name = s.get("name", row.name)
                row.description = s.get("description", row.description)
                row.icon = s.get("icon", row.icon)
                row.files = j_dumps(s.get("files", []))
        for rid in existing_ids - set(spaces.keys()):
            row = await sess.get(SpaceRow, rid)
            if row:
                await sess.delete(row)


load_spaces_async = _load_spaces_async
save_spaces_async = _save_spaces_async

def load_spaces() -> dict:
    return _run_async(_load_spaces_async())


def save_spaces(d: dict) -> None:
    _run_async(_save_spaces_async(d))


# ─────────────────────────────────────────────────────────────────────────────
# Triton devices (paired remote client machines)
# ─────────────────────────────────────────────────────────────────────────────

def _triton_row_to_dict(row: TritonDeviceRow) -> dict:
    return {
        "id": row.id, "name": row.name, "platform": row.platform,
        "description": row.description or "",
        "capabilities": j_loads(row.capabilities) or [],
        "paired_at": row.paired_at, "last_seen": row.last_seen,
    }


async def _list_triton_devices_async() -> list:
    async with _session() as sess:
        rows = (await sess.execute(select(TritonDeviceRow))).scalars().all()
    return [_triton_row_to_dict(r) for r in rows]


async def _get_triton_device_async(device_id: str) -> dict | None:
    async with _session() as sess:
        row = await sess.get(TritonDeviceRow, device_id)
        return _triton_row_to_dict(row) if row else None


async def _get_triton_token_hash_async(device_id: str) -> str | None:
    async with _session() as sess:
        row = await sess.get(TritonDeviceRow, device_id)
        return row.token_hash if row else None


async def _upsert_triton_device_async(device: dict) -> None:
    async with _session() as sess:
        row = await sess.get(TritonDeviceRow, device["id"])
        if row is None:
            sess.add(TritonDeviceRow(
                id=device["id"], name=device.get("name", ""),
                platform=device.get("platform"), description=device.get("description"),
                token_hash=device["token_hash"],
                capabilities=j_dumps(device.get("capabilities", [])),
                paired_at=device.get("paired_at"), last_seen=device.get("last_seen"),
            ))
        else:
            row.name = device.get("name", row.name)
            row.platform = device.get("platform", row.platform)
            if "description" in device:
                row.description = device.get("description")
            if device.get("token_hash"):
                row.token_hash = device["token_hash"]
            if "capabilities" in device:
                row.capabilities = j_dumps(device.get("capabilities", []))
            if device.get("last_seen"):
                row.last_seen = device["last_seen"]


async def _delete_triton_device_async(device_id: str) -> None:
    async with _session() as sess:
        row = await sess.get(TritonDeviceRow, device_id)
        if row:
            await sess.delete(row)


list_triton_devices_async = _list_triton_devices_async
get_triton_device_async = _get_triton_device_async
get_triton_token_hash_async = _get_triton_token_hash_async
upsert_triton_device_async = _upsert_triton_device_async
delete_triton_device_async = _delete_triton_device_async


def list_triton_devices() -> list:
    return _run_async(_list_triton_devices_async())


def get_triton_device(device_id: str) -> dict | None:
    return _run_async(_get_triton_device_async(device_id))


def get_triton_token_hash(device_id: str) -> str | None:
    return _run_async(_get_triton_token_hash_async(device_id))


def upsert_triton_device(device: dict) -> None:
    _run_async(_upsert_triton_device_async(device))


def delete_triton_device(device_id: str) -> None:
    _run_async(_delete_triton_device_async(device_id))
