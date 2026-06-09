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
    SpaceRow, AgentTemplateRow, FolderRow,
)
from sqlalchemy import select, delete, update


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

def load_prefs() -> dict:
    prefs = _load(USER_PREFS_FILE)
    if not prefs:
        prefs = DEFAULT_PREFS.copy()
        _save(USER_PREFS_FILE, prefs)
        return prefs
    changed = False
    for k, v in DEFAULT_PREFS.items():
        if k not in prefs:
            prefs[k] = v
            changed = True
    if changed:
        _save(USER_PREFS_FILE, prefs)
    return prefs


def save_prefs(d: dict) -> None:
    _save(USER_PREFS_FILE, d)


# ─────────────────────────────────────────────────────────────────────────────
# Internal async helpers — building the dict shape that callers expect
# ─────────────────────────────────────────────────────────────────────────────

def _conv_row_to_dict(row: ConversationRow, messages: list) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "character_id": row.character_id,
        "participants": j_loads(row.participants) or [],
        "is_anonymous": bool(row.is_anonymous),
        "folder_id": row.folder_id,
        "updated_at": row.updated_at,
        "last_active": row.last_active,
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
    }


# ─────────────────────────────────────────────────────────────────────────────
# Conversations — ASYNC
# ─────────────────────────────────────────────────────────────────────────────

async def load_convos_async() -> dict:
    """Load ALL conversations with their messages as a dict keyed by conv id."""
    async with _session() as sess:
        conv_result = await sess.execute(select(ConversationRow))
        conv_rows = conv_result.scalars().all()

        msg_result = await sess.execute(
            select(MessageRow).order_by(MessageRow.id.asc())
        )
        msg_rows = msg_result.scalars().all()

    # Group messages by conversation_id
    msgs_by_conv: dict[str, list] = {}
    for m in msg_rows:
        msgs_by_conv.setdefault(m.conversation_id, []).append(_msg_row_to_dict(m))

    result = {}
    for row in conv_rows:
        result[row.id] = _conv_row_to_dict(row, msgs_by_conv.get(row.id, []))
    return result


async def save_convos_async(convos: dict) -> None:
    """
    Upsert all conversations and messages.
    Existing messages for a conversation are deleted then re-inserted
    (keeps ordering correct and handles deleted messages).
    """
    async with _session() as sess:
        conv_ids = list(convos.keys())

        # Delete orphaned conversations (ones removed from the dict)
        all_rows = await sess.execute(select(ConversationRow.id))
        existing_ids = {r for (r,) in all_rows}
        to_delete = existing_ids - set(conv_ids)
        if to_delete:
            await sess.execute(
                delete(ConversationRow).where(ConversationRow.id.in_(to_delete))
            )

        for conv_id, conv in convos.items():
            now = datetime.datetime.utcnow().isoformat()

            # Upsert conversation row
            existing = await sess.get(ConversationRow, conv_id)
            if existing is None:
                sess.add(ConversationRow(
                    id=conv_id,
                    title=conv.get("title", "Untitled"),
                    character_id=conv.get("character_id"),
                    participants=j_dumps(conv.get("participants", [])),
                    is_anonymous=int(conv.get("is_anonymous", False)),
                    folder_id=conv.get("folder_id"),
                    updated_at=conv.get("updated_at") or now,
                    last_active=conv.get("last_active"),
                ))
            else:
                existing.title = conv.get("title", existing.title)
                existing.character_id = conv.get("character_id", existing.character_id)
                existing.participants = j_dumps(conv.get("participants", []))
                existing.is_anonymous = int(conv.get("is_anonymous", False))
                existing.folder_id = conv.get("folder_id")
                existing.updated_at = conv.get("updated_at") or now
                existing.last_active = conv.get("last_active")

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
        "name": row.name,
        "persona": row.persona or "",
        "avatar": row.avatar,
        "voice": row.voice,
        "memory_enabled": bool(row.memory_enabled) if row.memory_enabled is not None else None,
        "google_model": row.google_model or "default",
        "groq_model": row.groq_model or "default",
        "nvidia_model": row.nvidia_model,
        "local_model": row.local_model or "default",
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
            "groq_model": "default", "nvidia_model": None, "local_model": "default",
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
            "groq_model": "default", "nvidia_model": None, "local_model": "default",
            "voice": "aoede", "memory_enabled": True, "created_at": now,
        },
    }


async def _load_chars_async() -> dict:
    async with _session() as sess:
        rows = (await sess.execute(select(CharacterRow))).scalars().all()
    if not rows:
        defaults = _default_chars()
        await _save_chars_async(defaults)
        return defaults
    return {r.id: _char_row_to_dict(r) for r in rows}


async def _save_chars_async(chars: dict) -> None:
    async with _session() as sess:
        for cid, char in chars.items():
            existing = await sess.get(CharacterRow, cid)
            if existing is None:
                sess.add(CharacterRow(
                    id=cid,
                    name=char.get("name", ""),
                    persona=char.get("persona", ""),
                    avatar=char.get("avatar"),
                    voice=char.get("voice"),
                    memory_enabled=int(char["memory_enabled"]) if char.get("memory_enabled") is not None else None,
                    google_model=char.get("google_model", "default"),
                    groq_model=char.get("groq_model", "default"),
                    nvidia_model=char.get("nvidia_model"),
                    local_model=char.get("local_model", "default"),
                    mcp_servers=j_dumps(char.get("mcp_servers", [])),
                    selected_tools=j_dumps(char.get("selected_tools", [])),
                    created_at=char.get("created_at"),
                ))
            else:
                existing.name = char.get("name", existing.name)
                existing.persona = char.get("persona", existing.persona)
                existing.avatar = char.get("avatar", existing.avatar)
                existing.voice = char.get("voice", existing.voice)
                existing.memory_enabled = int(char["memory_enabled"]) if char.get("memory_enabled") is not None else None
                existing.google_model = char.get("google_model", "default")
                existing.groq_model = char.get("groq_model", "default")
                existing.nvidia_model = char.get("nvidia_model")
                existing.local_model = char.get("local_model", "default")
                existing.mcp_servers = j_dumps(char.get("mcp_servers", []))
                existing.selected_tools = j_dumps(char.get("selected_tools", []))
        # Delete characters removed from dict
        existing_ids_res = await sess.execute(select(CharacterRow.id))
        existing_ids = {r for (r,) in existing_ids_res}
        to_remove = existing_ids - set(chars.keys())
        for rid in to_remove:
            row = await sess.get(CharacterRow, rid)
            if row:
                await sess.delete(row)


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


def load_mcp() -> dict:
    return _run_async(_load_mcp_async())


def save_mcp(d: dict) -> None:
    _run_async(_save_mcp_async(d))


# ─────────────────────────────────────────────────────────────────────────────
# Folders
# ─────────────────────────────────────────────────────────────────────────────

def _folder_row_to_dict(row: FolderRow) -> dict:
    return {
        "id": row.id, "name": row.name, "icon": row.icon,
        "conversation_ids": j_loads(row.conversation_ids) or [],
        "created_at": row.created_at,
    }


async def _load_folders_async() -> dict:
    async with _session() as sess:
        rows = (await sess.execute(select(FolderRow))).scalars().all()
    return {r.id: _folder_row_to_dict(r) for r in rows}


async def _save_folders_async(folders: dict) -> None:
    async with _session() as sess:
        existing_ids_res = await sess.execute(select(FolderRow.id))
        existing_ids = {r for (r,) in existing_ids_res}
        for fid, f in folders.items():
            if fid not in existing_ids:
                sess.add(FolderRow(
                    id=fid, name=f.get("name", ""), icon=f.get("icon"),
                    conversation_ids=j_dumps(f.get("conversation_ids", [])),
                    created_at=f.get("created_at"),
                ))
            else:
                row = await sess.get(FolderRow, fid)
                row.name = f.get("name", row.name)
                row.icon = f.get("icon", row.icon)
                row.conversation_ids = j_dumps(f.get("conversation_ids", []))
        for rid in existing_ids - set(folders.keys()):
            row = await sess.get(FolderRow, rid)
            if row:
                await sess.delete(row)


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


def load_spaces() -> dict:
    return _run_async(_load_spaces_async())


def save_spaces(d: dict) -> None:
    _run_async(_save_spaces_async(d))
