import datetime
import json
import os
import shutil
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from langchain_core.messages import HumanMessage

from app.config import AVATARS_DIR
from app.llm import get_llm
from app.storage import load_chars, save_chars, load_prefs

router = APIRouter(prefix="/api/characters")

# ── Supported provider model keys ────────────────────────────────────
PROVIDER_MODEL_KEYS = ("groq_model", "google_model", "custom_model", "nvidia_model")


@router.get("")
async def list_characters():
    return list(load_chars().values())


@router.get("/{cid}")
async def get_character(cid: str):
    chars = load_chars()
    if cid not in chars:
        raise HTTPException(404, "Not found")
    return chars[cid]


@router.post("")
async def create_character(
    name: str = Form(...),
    persona: str = Form(...),
    mcp_servers: str = Form(""),
    selected_tools: str = Form(""),
    groq_model: str = Form("default"),
    google_model: str = Form("default"),
    custom_model: str = Form("default"),
    nvidia_model: str = Form("default"),
    voice: str = Form("aoede"),
    memory_enabled: bool = Form(True),
    avatar: Optional[UploadFile] = File(None),
):
    chars = load_chars()
    cid = str(uuid.uuid4())[:8]
    avatar_path = None

    if avatar and avatar.filename:
        ext = os.path.splitext(avatar.filename)[1] or ".png"
        fname = f"{cid}{ext}"
        with open(os.path.join(AVATARS_DIR, fname), "wb") as f:
            shutil.copyfileobj(avatar.file, f)
        avatar_path = f"/avatars/{fname}"

    mcp_list = [s.strip() for s in mcp_servers.split(",") if s.strip()] if mcp_servers else []
    tools_list = [s.strip() for s in selected_tools.split(",") if s.strip()] if selected_tools else []
    chars[cid] = {
        "id": cid,
        "name": name.strip(),
        "persona": persona.strip(),
        "avatar": avatar_path,
        "mcp_servers": mcp_list,
        "selected_tools": tools_list,
        "groq_model": groq_model.strip(),
        "google_model": google_model.strip(),
        "custom_model": custom_model.strip(),
        "nvidia_model": nvidia_model.strip(),
        "voice": voice.strip(),
        "memory_enabled": memory_enabled,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    save_chars(chars)
    return chars[cid]


@router.put("/{cid}")
async def update_character(
    cid: str,
    name: str = Form(...),
    persona: str = Form(...),
    mcp_servers: str = Form(""),
    selected_tools: str = Form(""),
    groq_model: str = Form("default"),
    google_model: str = Form("default"),
    custom_model: str = Form("default"),
    nvidia_model: str = Form("default"),
    voice: str = Form("aoede"),
    memory_enabled: bool = Form(True),
    avatar: Optional[UploadFile] = File(None),
):
    chars = load_chars()
    if cid not in chars:
        raise HTTPException(404, "Not found")

    avatar_path = chars[cid].get("avatar")
    if avatar and avatar.filename:
        ext = os.path.splitext(avatar.filename)[1] or ".png"
        fname = f"{cid}{ext}"
        with open(os.path.join(AVATARS_DIR, fname), "wb") as f:
            shutil.copyfileobj(avatar.file, f)
        avatar_path = f"/avatars/{fname}"

    mcp_list = [s.strip() for s in mcp_servers.split(",") if s.strip()] if mcp_servers else []
    tools_list = [s.strip() for s in selected_tools.split(",") if s.strip()] if selected_tools else []
    chars[cid]["name"] = name.strip()
    chars[cid]["persona"] = persona.strip()
    chars[cid]["avatar"] = avatar_path
    chars[cid]["mcp_servers"] = mcp_list
    chars[cid]["selected_tools"] = tools_list
    chars[cid]["groq_model"] = groq_model.strip()
    chars[cid]["google_model"] = google_model.strip()
    chars[cid]["custom_model"] = custom_model.strip()
    chars[cid]["nvidia_model"] = nvidia_model.strip()
    chars[cid]["voice"] = voice.strip()
    chars[cid]["memory_enabled"] = memory_enabled
    save_chars(chars)
    return chars[cid]


@router.delete("/{cid}")
async def delete_character(cid: str):
    if cid in ["kokomi", "nahida"]:
        raise HTTPException(400, "Cannot delete protected character")
    chars = load_chars()
    if cid not in chars:
        raise HTTPException(404, "Not found")
    av = chars[cid].get("avatar")
    if av:
        p = os.path.join(AVATARS_DIR, os.path.basename(av))
        if os.path.exists(p):
            os.remove(p)
    del chars[cid]
    save_chars(chars)
    return {"ok": True}


@router.get("/{char_id}/suggestions")
async def get_char_suggestions(char_id: str):
    from app.config import GROQ_API_KEY
    if not GROQ_API_KEY:
        return []

    chars = load_chars()
    char = chars.get(char_id)
    if not char:
        raise HTTPException(404, "Character not found")

    prompt = f"""
You are a creative assistant generator for an AI platform.
Analyze this character persona and generate 4 diverse and creative "quick prompts" for a user to start a conversation with them.

Character Name: {char['name']}
Character Persona: {char['persona']}

Return ONLY a JSON array of objects with these fields:
- icon: (FontAwesome class, e.g. 'fa-solid fa-magic')
- label: (Short 2-3 word title)
- text: (The actual message to send)

Ensure the prompts match the character's tone and expertise.
"""
    try:
        prefs = load_prefs()
        llm = get_llm(prefs)
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        return json.loads(content)
    except Exception as e:
        print(f"Error generating suggestions: {e}")
        return [
            {"icon": "fa-solid fa-message", "label": "Say Hello",   "text": f"Hello {char['name']}!"},
            {"icon": "fa-solid fa-question", "label": "Ask Anything", "text": "Tell me something interesting about yourself."},
        ]


@router.get("/{cid}/memories")
async def list_character_memories(cid: str):
    from app.rag import qdrant
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    
    # Check if character exists
    chars = load_chars()
    if cid not in chars:
        raise HTTPException(404, "Character not found")
        
    try:
        collections = qdrant.get_collections().collections
        if not any(c.name == "user_memories" for c in collections):
            return []
            
        results, _ = qdrant.scroll(
            collection_name="user_memories",
            scroll_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=cid))]
            ),
            limit=100
        )
        
        memories = []
        for point in results:
            if point.payload:
                memories.append({
                    "id": point.id,
                    "text": point.payload.get("text"),
                    "importance": point.payload.get("importance", 3.0),
                    "access_count": point.payload.get("access_count", 0),
                    "last_accessed": point.payload.get("last_accessed"),
                    "source": point.payload.get("source", "auto"),
                    "timestamp": point.payload.get("created_at", point.payload.get("timestamp"))
                })
        # Sort by importance descending, then by timestamp
        memories.sort(key=lambda x: (x.get("importance", 0), x.get("timestamp") or 0), reverse=True)
        return memories
    except Exception as e:
        print(f"Error listing memories: {e}")
        return []


@router.delete("/{cid}/memories/{mid}")
async def delete_character_memory(cid: str, mid: str):
    from app.rag import qdrant
    try:
        qdrant.delete(
            collection_name="user_memories",
            points_selector=[mid]
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"Failed to delete memory: {e}")


from pydantic import BaseModel
class ManualMemoryRequest(BaseModel):
    text: str


@router.post("/{cid}/memories")
async def add_character_memory(cid: str, req: ManualMemoryRequest):
    from app.memory import save_memory
    chars = load_chars()
    if cid not in chars:
        raise HTTPException(404, "Character not found")
    if not req.text.strip():
        raise HTTPException(400, "Memory text cannot be empty")
    try:
        save_memory(cid, req.text.strip(), metadata={"source": "manual_entry"}, importance=4.5)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"Failed to add memory: {e}")


@router.delete("/{cid}/memories")
async def clear_character_memories(cid: str):
    from app.rag import qdrant
    from app.memory import invalidate_cache
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    try:
        qdrant.delete(
            collection_name="user_memories",
            points_selector=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=cid))]
            )
        )
        invalidate_cache(cid)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"Failed to clear memories: {e}")


@router.get("/{cid}/profile")
async def get_character_profile_endpoint(cid: str):
    """Get the AI-synthesized relationship profile for a character."""
    from app.memory import get_character_profile
    chars = load_chars()
    if cid not in chars:
        raise HTTPException(404, "Character not found")
    profile = get_character_profile(cid)
    return {"character_id": cid, "profile": profile}


@router.post("/{cid}/memories/decay")
async def trigger_decay(cid: str):
    """Manually trigger memory decay sweep for a character."""
    from app.memory import run_decay_sweep
    chars = load_chars()
    if cid not in chars:
        raise HTTPException(404, "Character not found")
    run_decay_sweep(cid)
    return {"ok": True, "message": f"Decay sweep completed for {cid}"}

