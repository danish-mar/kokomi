import json
import datetime
from app.config import (
    CONVOS_FILE,
    CHARS_FILE,
    MCP_FILE,
    USER_PREFS_FILE,
    FOLDERS_FILE,
    SPACES_FILE,
    DEFAULT_PREFS,
)


# ── Low-level helpers ────────────────────────────────────────────────

def _load(path: str) -> dict:
    import os
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return {}


def _save(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Preferences ──────────────────────────────────────────────────────

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


# ── Conversations ────────────────────────────────────────────────────

def load_convos() -> dict:
    return _load(CONVOS_FILE)


def save_convos(d: dict) -> None:
    _save(CONVOS_FILE, d)


# ── Characters ───────────────────────────────────────────────────────

def load_chars() -> dict:
    chars = _load(CHARS_FILE)
    if not chars:
        now = datetime.datetime.utcnow().isoformat()
        chars["kokomi"] = {
            "id": "kokomi",
            "name": "Kokomi",
            "persona": (
                "You are Kokomi, the Divine Priestess of Watatsumi Island. "
                "You are a brilliant strategist and a gentle, thoughtful leader. "
                "You use markdown for formatting. Speak with grace and wisdom."
            ),
            "avatar": None,
            "mcp_servers": [],
            "model": "default",
            "created_at": now,
        }
        chars["nahida"] = {
            "id": "nahida",
            "name": "Nahida",
            "persona": (
                "You are Nahida, the Lesser Lord Kusanali and the Avatar of Irminsul. "
                "You are wise, curious, and speak in beautiful metaphors. "
                "You are deeply knowledgeable about the world and treat everyone with "
                "kindness and a sense of wonder. Use markdown for formatting."
            ),
            "avatar": None,
            "mcp_servers": [],
            "model": "default",
            "created_at": now,
        }
        _save(CHARS_FILE, chars)
    return chars


def save_chars(d: dict) -> None:
    _save(CHARS_FILE, d)


# ── MCP servers ──────────────────────────────────────────────────────

def load_mcp() -> dict:
    return _load(MCP_FILE)


def save_mcp(d: dict) -> None:
    _save(MCP_FILE, d)


# ── Folders ──────────────────────────────────────────────────────────

def load_folders() -> dict:
    return _load(FOLDERS_FILE)


def save_folders(d: dict) -> None:
    _save(FOLDERS_FILE, d)


# ── Spaces (RAG) ─────────────────────────────────────────────────────

def load_spaces() -> dict:
    return _load(SPACES_FILE)

def save_spaces(d: dict) -> None:
    _save(SPACES_FILE, d)
