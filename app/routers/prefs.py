import httpx
from fastapi import APIRouter, UploadFile, File

from app.config import GROQ_API_KEY, GOOGLE_API_KEY, NVIDIA_API_KEY
from app.models import PrefsUpdate
from app.storage import load_prefs, save_prefs

router = APIRouter(prefix="/api")


@router.get("/prefs")
async def get_prefs():
    return load_prefs()


@router.post("/prefs")
async def update_prefs(p: PrefsUpdate):
    prefs = load_prefs()
    prefs.update(p.model_dump())
    save_prefs(prefs)
    return prefs


@router.get("/models")
async def list_available_models():
    curated = [

        {"id": "llama-3.3-70b-versatile",             "name": "Llama 3.3 70B",             "provider": "groq"},
        {"id": "llama-3.1-8b-instant",                "name": "Llama 3.1 8B",              "provider": "groq"},
        {"id": "mixtral-8x7b-32768",                  "name": "Mixtral 8x7B",              "provider": "groq"},
        {"id": "deepseek-r1-distill-llama-70b",       "name": "DeepSeek R1 70B (Llama)",   "provider": "groq"},
        {"id": "gemini-2.5-flash",                    "name": "Gemini 2.5 Flash",           "provider": "google"},
        {"id": "gemini-2.5-pro",                      "name": "Gemini 2.5 Pro",             "provider": "google"},
        {"id": "gemini-2.0-flash",                    "name": "Gemini 2.0 Flash",           "provider": "google"},
        {"id": "gemini-2.0-flash-lite",               "name": "Gemini 2.0 Flash Lite",      "provider": "google"},
        {"id": "local-model",                         "name": "Local Model",                "provider": "local"},
    ]

    all_models = list(curated)
    curated_ids = {m["id"] for m in curated}

    from app.storage import load_prefs
    prefs = load_prefs()
    groq_key = prefs.get("groq_api_key") or GROQ_API_KEY
    google_key = prefs.get("google_api_key") or GOOGLE_API_KEY
    nvidia_key = prefs.get("nvidia_api_key") or NVIDIA_API_KEY

    async with httpx.AsyncClient() as client:
        if groq_key:
            try:
                resp = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {groq_key}"},
                )
                if resp.status_code == 200:
                    for m in resp.json().get("data", []):
                        if "whisper" not in m["id"] and "vision" not in m["id"]:
                            if m["id"] not in curated_ids:
                                all_models.append({"id": m["id"], "name": m["id"], "provider": "groq"})
            except Exception as e:
                print(f"Error fetching Groq models: {e}")

        if google_key:
            try:
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={google_key}"
                )
                if resp.status_code == 200:
                    for m in resp.json().get("models", []):
                        if "generateContent" in m.get("supportedGenerationMethods", []):
                            clean_id = m["name"].replace("models/", "")
                            display_name = m.get("displayName", clean_id)
                            if clean_id not in curated_ids:
                                all_models.append({"id": clean_id, "name": display_name, "provider": "google"})
            except Exception as e:
                print(f"Error fetching Google models: {e}")

        if nvidia_key:
            try:
                resp = await client.get(
                    "https://integrate.api.nvidia.com/v1/models",
                    headers={"Authorization": f"Bearer {nvidia_key}", "Accept": "application/json"},
                )
                if resp.status_code == 200:
                    for m in resp.json().get("data", []):
                        if m["id"] not in curated_ids:
                            all_models.append({"id": m["id"], "name": m["id"].split("/")[-1], "provider": "nvidia"})
            except Exception as e:
                print(f"Error fetching NVIDIA models: {e}")

    return all_models


@router.post("/prefs/avatar")
async def upload_user_avatar(avatar: UploadFile = File(...)):
    import os
    import shutil
    from app.config import AVATARS_DIR
    
    ext = os.path.splitext(avatar.filename)[1] or ".png"
    fname = f"user_{avatar.filename}" # unique enough for single user
    # or use a fixed name to overwrite
    fname = f"user_profile{ext}"
    
    target_path = os.path.join(AVATARS_DIR, fname)
    with open(target_path, "wb") as f:
        shutil.copyfileobj(avatar.file, f)
    
    prefs = load_prefs()
    prefs["user_avatar"] = f"/avatars/{fname}"
    save_prefs(prefs)
    return {"avatar": prefs["user_avatar"]}


@router.post("/ai/generate")
async def ai_generate(body: dict):
    """Stateless single-shot LLM call. Nothing is saved to conversation history.
    Used for AI-assisted features like the character generator.
    Body: { "prompt": str, "system": str (optional) }
    Returns: { "text": str }
    """
    from app.llm import get_llm
    from app.storage import load_prefs
    from langchain_core.messages import HumanMessage, SystemMessage

    prompt = body.get("prompt", "").strip()
    system = body.get("system", "You are a helpful assistant.").strip()

    if not prompt:
        return {"text": "", "error": "No prompt provided"}

    prefs = load_prefs()
    try:
        llm = get_llm(prefs)
        messages = [SystemMessage(content=system), HumanMessage(content=prompt)]
        result = await llm.ainvoke(messages)
        return {"text": result.content}
    except Exception as e:
        return {"text": "", "error": str(e)}
