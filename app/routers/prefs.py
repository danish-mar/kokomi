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


@router.get("/update/check")
async def check_for_updates():
    import subprocess
    import tomli
    from pathlib import Path

    # 1. Get current local version
    from app.config import VERSION
    local_version = VERSION
    
    # 2. Get git remote URL
    git_url = "https://github.com/danish-mar/kokomi.git"
    try:
        res = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"], 
            capture_output=True, 
            text=True, 
            timeout=3
        )
        if res.returncode == 0 and res.stdout.strip():
            git_url = res.stdout.strip()
    except Exception:
        pass
    
    # Helper to parse raw github url
    def parse_github_raw_url(url_str: str, filename: str) -> str:
        if url_str.endswith(".git"):
            url_str = url_str[:-4]
        # SSH format
        if url_str.startswith("git@github.com:"):
            path = url_str[len("git@github.com:"):]
            return f"https://raw.githubusercontent.com/{path}/main/{filename}"
        # HTTP/HTTPS format
        if "github.com" in url_str:
            parts = url_str.split("github.com/")
            if len(parts) > 1:
                path = parts[1]
                return f"https://raw.githubusercontent.com/{path}/main/{filename}"
        return f"https://raw.githubusercontent.com/danish-mar/kokomi/main/{filename}"

    # Helper to parse changelog
    def extract_version_changelog(changelog_text: str, version: str) -> str:
        clean_version = version.lstrip("v").strip("[]")
        lines = changelog_text.splitlines()
        changelog_lines = []
        found_section = False
        
        for line in lines:
            if line.startswith("## "):
                if found_section:
                    break
                if clean_version in line:
                    found_section = True
                    changelog_lines.append(line)
            elif found_section:
                changelog_lines.append(line)
                
        return "\n".join(changelog_lines).strip()

    # 3. Construct raw URLs for remote files
    remote_toml_url = parse_github_raw_url(git_url, "pyproject.toml")
    remote_changelog_url = parse_github_raw_url(git_url, "CHANGELOG.md")
    
    remote_version = None
    remote_release_name = ""
    changelog = ""
    update_available = False
    
    # 4. Fetch remote pyproject.toml
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(remote_toml_url, timeout=5)
            # If main branch returns 404, fall back to master
            if resp.status_code == 404 and "/main/" in remote_toml_url:
                remote_toml_url = remote_toml_url.replace("/main/", "/master/")
                remote_changelog_url = remote_changelog_url.replace("/main/", "/master/")
                resp = await client.get(remote_toml_url, timeout=5)
                
            if resp.status_code == 200:
                remote_data = tomli.loads(resp.text)
                remote_version = remote_data["project"]["version"]
                remote_release_name = remote_data["project"].get("release-name", "")
            else:
                return {
                    "ok": False,
                    "error": f"Failed to fetch remote version metadata (HTTP {resp.status_code})"
                }
    except Exception as e:
        return {"ok": False, "error": f"Failed to connect to remote repository: {str(e)}"}
        
    # Check if remote version is newer
    try:
        local_parts = [int(x) for x in local_version.split(".")]
        remote_parts = [int(x) for x in remote_version.split(".")]
        update_available = remote_parts > local_parts
    except Exception:
        update_available = remote_version != local_version
        
    # 5. Get changelog for target version
    target_version_for_changelog = remote_version if update_available else local_version
    changelog_text = ""
    
    if update_available:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(remote_changelog_url, timeout=5)
                if resp.status_code == 200:
                    changelog_text = resp.text
        except Exception:
            pass
            
    # Fallback to local changelog if remote fails or not update_available
    if not changelog_text:
        try:
            local_changelog_path = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
            if local_changelog_path.exists():
                with open(local_changelog_path, "r", encoding="utf-8") as f:
                    changelog_text = f.read()
        except Exception:
            pass
            
    if changelog_text:
        changelog = extract_version_changelog(changelog_text, target_version_for_changelog)
        
    return {
        "ok": True,
        "current_version": local_version,
        "latest_version": remote_version,
        "latest_release_name": remote_release_name,
        "update_available": update_available,
        "changelog": changelog
    }

