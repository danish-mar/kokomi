import datetime
import uuid

from fastapi import APIRouter, HTTPException

from app.models import FolderCreate, ConversationFolderUpdate
from app.storage import load_convos, save_convos, load_folders, save_folders

router = APIRouter(prefix="/api")


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
            "updated_at": c.get("updated_at", ""),
        }
        for cid, c in convos.items()
    ]
    result.sort(key=lambda x: x["updated_at"], reverse=True)
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
