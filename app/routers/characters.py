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
PROVIDER_MODEL_KEYS = ("groq_model", "google_model", "local_model")


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
    groq_model: str = Form("default"),
    google_model: str = Form("default"),
    local_model: str = Form("default"),
    voice: str = Form("aoede"),
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
    chars[cid] = {
        "id": cid,
        "name": name.strip(),
        "persona": persona.strip(),
        "avatar": avatar_path,
        "mcp_servers": mcp_list,
        "groq_model": groq_model.strip(),
        "google_model": google_model.strip(),
        "local_model": local_model.strip(),
        "voice": voice.strip(),
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
    groq_model: str = Form("default"),
    google_model: str = Form("default"),
    local_model: str = Form("default"),
    voice: str = Form("aoede"),
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
    chars[cid]["name"] = name.strip()
    chars[cid]["persona"] = persona.strip()
    chars[cid]["avatar"] = avatar_path
    chars[cid]["mcp_servers"] = mcp_list
    chars[cid]["groq_model"] = groq_model.strip()
    chars[cid]["google_model"] = google_model.strip()
    chars[cid]["local_model"] = local_model.strip()
    chars[cid]["voice"] = voice.strip()
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
