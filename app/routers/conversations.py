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
                if len(cleaned) > 60:
                    return cleaned[:57] + "..."
                return cleaned

    # 2. Fallback to the first user message (e.g. if the assistant hasn't replied yet)
    for m in messages:
        if m.get("role") == "user" and not m.get("tool_calls"):
            cleaned = _clean_content(m.get("content", ""))
            if cleaned:
                if len(cleaned) > 60:
                    return cleaned[:57] + "..."
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
            if len(cleaned) > 60:
                return cleaned[:57] + "..."
            return cleaned

    return ""


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
