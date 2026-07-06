import datetime
import uuid

from fastapi import APIRouter, HTTPException

from app.models import FolderCreate, ConversationFolderUpdate
from app.storage import load_convos, save_convos, load_folders, save_folders

router = APIRouter(prefix="/api")


def _clean_content(content):
    if not content:
        return ""
    # Skip raw JSON payloads
    content_strip = content.strip()
    if content_strip.startswith("{") and content_strip.endswith("}"):
        return ""
        
    import re
    # Remove think/thought tags (closed or unclosed until end of text)
    cleaned = re.sub(r"<(think|thought)>.*?(</\1>|$)", "", content, flags=re.DOTALL | re.IGNORECASE)
    # Remove markdown code blocks
    cleaned = re.sub(r"```.*?```", "", cleaned, flags=re.DOTALL)
    # Remove markdown heading symbols at start of lines
    cleaned = re.sub(r"^\s*#+\s+", "", cleaned, flags=re.MULTILINE)
    # Remove blockquotes at start of lines
    cleaned = re.sub(r"^\s*>\s+", "", cleaned, flags=re.MULTILINE)
    # Remove list markers at start of lines
    cleaned = re.sub(r"^\s*[-\*+]\s+", "", cleaned, flags=re.MULTILINE)
    # Remove inline formatting / artifacts
    cleaned = re.sub(r"\[\[ARTIFACT:.*?\]\]", "", cleaned)
    cleaned = cleaned.replace("**", "").replace("*", "").replace("`", "").strip()
    # Collapse whitespace/newlines into spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def get_last_message_preview(c):
    messages = c.get("messages", [])
    if not messages:
        return ""

    # 1. Try to find the AI's (assistant) first response
    for m in messages:
        if m.get("role") == "assistant" and not m.get("tool_calls"):
            cleaned = _clean_content(m.get("content", ""))
            if cleaned:
                if len(cleaned) > 180:
                    return cleaned[:177] + "..."
                return cleaned

    # 2. Fallback to the first user message (e.g. if the assistant hasn't replied yet)
    for m in messages:
        if m.get("role") == "user" and not m.get("tool_calls"):
            cleaned = _clean_content(m.get("content", ""))
            if cleaned:
                if len(cleaned) > 180:
                    return cleaned[:177] + "..."
                return cleaned

    # 3. Last resort fallback to any valid message from the end
    for m in reversed(messages):
        role = m.get("role", "user")
        if role not in ("user", "assistant"):
            continue
        if m.get("tool_calls"):
            continue
        cleaned = _clean_content(m.get("content", ""))
        if cleaned:
            if len(cleaned) > 180:
                return cleaned[:177] + "..."
            return cleaned

    return ""


def get_conversation_thumbnail(c):
    """First image URL from the most recent `search_images` result in the chat,
    used as the sidebar card thumbnail. None if the chat has no images."""
    import json as _json
    for m in reversed(c.get("messages", [])):
        if m.get("role") != "assistant":
            continue
        for tc in reversed(m.get("tool_calls") or []):
            if tc.get("name") != "search_images" or not tc.get("result"):
                continue
            try:
                images = (_json.loads(tc["result"]) or {}).get("images") or []
            except Exception:
                continue
            for im in images:
                url = (im.get("thumbnail") or im.get("url")) if isinstance(im, dict) else im
                if url:
                    return url
    return None


# ── Conversations ────────────────────────────────────────────────────

@router.get("/conversations")
async def list_conversations_api():
    convos = load_convos()
    result = [
        {
            "_id": cid,
            "title": c.get("title", "Untitled"),
            "character_id": c.get("character_id", "kokomi"),
            "folder_id": c.get("folder_id", None),
            "updated_at": str(c.get("updated_at", "")),
            "preview": get_last_message_preview(c),
            "thumbnail": get_conversation_thumbnail(c),
        }
        for cid, c in convos.items()
        if not c.get("is_anonymous", False)
    ]

    def sort_key(c):
        val = c.get("updated_at")
        if not val: return 0.0
        if isinstance(val, (int, float)): return float(val)
        try:
            import datetime
            return datetime.datetime.fromisoformat(str(val)).timestamp()
        except:
            return 0.0

    result.sort(key=sort_key, reverse=True)
    return result[:50]


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    convos = load_convos()
    if conv_id not in convos:
        raise HTTPException(404, "Not found")
    c = dict(convos[conv_id])
    _stamp_branch_metadata(c)
    c["_id"] = conv_id
    return c


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    convos = load_convos()
    if conv_id not in convos:
        raise HTTPException(404, "Not found")
    del convos[conv_id]
    save_convos(convos)
    return {"ok": True}


@router.post("/conversations/{conv_id}/save")
async def save_temporary_conversation(conv_id: str):
    """Promote a temporary chat to permanent history by clearing the is_anonymous flag."""
    convos = load_convos()
    if conv_id not in convos:
        raise HTTPException(404, "Not found")
    convos[conv_id]["is_anonymous"] = False
    save_convos(convos)
    return {"ok": True}


@router.post("/conversations/{conv_id}/pop")
async def pop_last_messages(conv_id: str):
    convos = load_convos()
    if conv_id not in convos:
        raise HTTPException(404, "Not found")
    msgs = convos[conv_id].get("messages", [])
    last_user_idx = next(
        (i for i in range(len(msgs) - 1, -1, -1) if msgs[i]["role"] == "user"),
        -1,
    )
    if last_user_idx != -1:
        convos[conv_id]["messages"] = msgs[:last_user_idx]
    save_convos(convos)
    return {"ok": True, "count": len(convos[conv_id]["messages"])}


@router.delete("/conversations/{conv_id}/messages/{msg_index}")
async def delete_specific_message(conv_id: str, msg_index: int):
    convos = load_convos()
    if conv_id not in convos:
        raise HTTPException(404, "Not found")
    msgs = convos[conv_id].get("messages", [])
    if 0 <= msg_index < len(msgs):
        msgs.pop(msg_index)
        convos[conv_id]["messages"] = msgs
        save_convos(convos)
        return {"ok": True}
    raise HTTPException(400, "Invalid index")


# ── ChatGPT-style edit/regenerate branching ──────────────────────────
#
# Editing a user message or regenerating an assistant reply both boil down to
# the same operation: archive everything from `index` onward as a "variant" of
# a branch group, then truncate storage back to `index` so the existing
# send/regenerate flow can refill it — no changes needed to the (complex,
# heavily-tested) streaming/tool-calling code path. Switching branches later
# just swaps a stored variant back in; it's pure bookkeeping, no LLM call.

def _branch_meta(conv: dict, group_id: str) -> dict:
    g = conv.get("branches", {}).get(group_id) or {}
    variants = g.get("variants", [])
    return {"group_id": group_id, "branch_index": g.get("active_index", 0) + 1, "branch_count": len(variants)}


def _stamp_branch_metadata(conv: dict) -> None:
    """branch_index/branch_count are only ever computed on the fly (never
    persisted onto the message itself), so any code path that hands `messages`
    to the frontend must call this first — otherwise a switched-to or
    reloaded branch message is missing the fields the UI needs to show its
    `< i/N >` nav arrows, and they silently vanish."""
    msgs = conv.get("messages", [])
    for group_id, g in conv.get("branches", {}).items():
        idx = g.get("anchor_index")
        if idx is not None and 0 <= idx < len(msgs) and msgs[idx].get("group_id") == group_id:
            msgs[idx]["branch_index"] = g.get("active_index", 0) + 1
            msgs[idx]["branch_count"] = len(g.get("variants", []))


@router.post("/conversations/{conv_id}/messages/{index}/branch")
async def branch_at_message(conv_id: str, index: int):
    """Archive messages[index:] as a branch variant and truncate storage to
    `index`. Used by both 'edit message' and 'regenerate response' — the
    caller resends via the normal chat flow to refill the truncated tail."""
    convos = load_convos()
    if conv_id not in convos:
        raise HTTPException(404, "Conversation not found")
    conv = convos[conv_id]
    msgs = conv.get("messages", [])
    if not (0 <= index < len(msgs)):
        raise HTTPException(400, "Invalid index")

    import copy
    anchor = msgs[index]
    group_id = anchor.get("group_id")
    branches = conv.setdefault("branches", {})
    suffix = copy.deepcopy(msgs[index:])

    if group_id and group_id in branches:
        g = branches[group_id]
        g["variants"][g["active_index"]] = suffix  # freeze latest content of the outgoing variant
        g["variants"].append([])                    # placeholder for the new variant, filled by the next turn
        g["active_index"] = len(g["variants"]) - 1
    else:
        group_id = f"br_{uuid.uuid4().hex[:8]}"
        branches[group_id] = {"anchor_index": index, "variants": [suffix, []], "active_index": 1}

    conv["messages"] = msgs[:index]
    save_convos(convos)
    return {"ok": True, **_branch_meta(conv, group_id)}


@router.post("/conversations/{conv_id}/messages/{index}/attach-group")
async def attach_branch_group(conv_id: str, index: int, payload: dict):
    """Called once a turn that refilled a truncated branch has finished
    streaming: stamps the branch marker onto the new message and freezes its
    now-complete content into the branch store so switching away and back
    doesn't lose it."""
    group_id = payload.get("group_id")
    if not group_id:
        raise HTTPException(400, "group_id is required")

    convos = load_convos()
    if conv_id not in convos:
        raise HTTPException(404, "Conversation not found")
    conv = convos[conv_id]
    msgs = conv.get("messages", [])
    if not (0 <= index < len(msgs)):
        raise HTTPException(400, "Invalid index")
    if group_id not in conv.get("branches", {}):
        raise HTTPException(404, "Unknown branch group")

    import copy
    msgs[index]["group_id"] = group_id
    g = conv["branches"][group_id]
    g["variants"][g["active_index"]] = copy.deepcopy(msgs[index:])
    save_convos(convos)
    return {"ok": True, **_branch_meta(conv, group_id)}


@router.post("/conversations/{conv_id}/messages/{index}/switch-branch")
async def switch_branch(conv_id: str, index: int, payload: dict):
    """Instantly swap in an adjacent branch variant — no LLM call."""
    direction = payload.get("direction")
    if direction not in ("prev", "next"):
        raise HTTPException(400, "direction must be 'prev' or 'next'")

    convos = load_convos()
    if conv_id not in convos:
        raise HTTPException(404, "Conversation not found")
    conv = convos[conv_id]
    msgs = conv.get("messages", [])
    if not (0 <= index < len(msgs)):
        raise HTTPException(400, "Invalid index")

    group_id = msgs[index].get("group_id")
    if not group_id or group_id not in conv.get("branches", {}):
        raise HTTPException(400, "Message is not part of a branch")

    import copy
    g = conv["branches"][group_id]
    anchor = g["anchor_index"]
    variants = g["variants"]
    active = g["active_index"]

    target = active + (1 if direction == "next" else -1)
    if not (0 <= target < len(variants)):
        raise HTTPException(400, "No branch in that direction")

    variants[active] = copy.deepcopy(msgs[anchor:])  # freeze outgoing variant's latest content
    msgs[anchor:] = copy.deepcopy(variants[target])
    if msgs[anchor:]:
        msgs[anchor]["group_id"] = group_id
    g["active_index"] = target
    conv["messages"] = msgs
    save_convos(convos)
    _stamp_branch_metadata(conv)  # so the returned messages carry branch_index/branch_count too
    return {"ok": True, "messages": msgs, **_branch_meta(conv, group_id)}


@router.put("/conversations/{cid}/folder")
async def assign_conversation_to_folder(cid: str, req: ConversationFolderUpdate):
    convos = load_convos()
    if cid not in convos:
        raise HTTPException(404, "Conversation not found")
    convos[cid]["folder_id"] = req.folder_id
    save_convos(convos)
    return {"ok": True}


# ── Folders ──────────────────────────────────────────────────────────

@router.get("/folders")
async def list_folders_api():
    return list(load_folders().values())


@router.post("/folders")
async def create_folder(req: FolderCreate):
    folders = load_folders()
    fid = str(uuid.uuid4())[:8]
    folders[fid] = {
        "id": fid,
        "name": req.name,
        "icon": req.icon,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    save_folders(folders)
    return folders[fid]


@router.put("/folders/{fid}")
async def update_folder(fid: str, req: FolderCreate):
    folders = load_folders()
    if fid not in folders:
        raise HTTPException(404, "Folder not found")
    folders[fid]["name"] = req.name
    folders[fid]["icon"] = req.icon
    save_folders(folders)
    return folders[fid]


@router.delete("/folders/{fid}")
async def delete_folder(fid: str):
    folders = load_folders()
    if fid in folders:
        del folders[fid]
        save_folders(folders)
        # Unlink any conversations that were in this folder
        convos = load_convos()
        for c in convos.values():
            if c.get("folder_id") == fid:
                c["folder_id"] = None
        save_convos(convos)
    return {"ok": True}
