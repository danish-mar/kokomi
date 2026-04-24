import httpx
from fastapi import APIRouter

from app.config import GROQ_API_KEY, GOOGLE_API_KEY
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

    async with httpx.AsyncClient() as client:
        if GROQ_API_KEY:
            try:
                resp = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                )
                if resp.status_code == 200:
                    for m in resp.json().get("data", []):
                        if "whisper" not in m["id"] and "vision" not in m["id"]:
                            if m["id"] not in curated_ids:
                                all_models.append({"id": m["id"], "name": m["id"], "provider": "groq"})
            except Exception as e:
                print(f"Error fetching Groq models: {e}")

        if GOOGLE_API_KEY:
            try:
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={GOOGLE_API_KEY}"
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

    return all_models
