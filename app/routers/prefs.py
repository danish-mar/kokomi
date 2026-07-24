from pathlib import Path

import httpx
from fastapi import APIRouter, UploadFile, File

from app.config import GROQ_API_KEY, GOOGLE_API_KEY, NVIDIA_API_KEY
from app.models import PrefsUpdate, CustomModelsRequest
from app.storage import load_prefs, save_prefs

router = APIRouter(prefix="/api")


@router.get("/prefs")
async def get_prefs():
    return load_prefs()


def _sniff_image_type(b: bytes):
    """Guess an image MIME type from magic bytes, for hosts that mislabel content-type."""
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:2] == b"BM":
        return "image/bmp"
    head = b[:256].lstrip().lower()
    if head[:5] == b"<?xml" or head[:4] == b"<svg":
        return "image/svg+xml"
    return None


@router.get("/img")
async def image_proxy(url: str):
    """Proxy a remote image through the server so it loads same-origin. Some image
    hosts set a restrictive Cross-Origin-Resource-Policy (or serve over http on an
    https page); fetching server-side and re-serving from our own origin sidesteps
    CORP, mixed-content, and hotlink protection for AI image galleries."""
    from fastapi import HTTPException
    from fastapi.responses import Response
    from urllib.parse import urlparse

    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid url")

    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        # Many hosts hotlink-protect by Referer; presenting the image's own origin
        # gets us past most of them.
        "Referer": origin + "/",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            r = await client.get(url, headers=headers)
    except Exception:
        raise HTTPException(status_code=502, detail="Fetch failed")

    if r.status_code != 200 or not r.content:
        raise HTTPException(status_code=404, detail="Not available")
    if len(r.content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large")

    content_type = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        content_type = _sniff_image_type(r.content)
        if not content_type:
            raise HTTPException(status_code=415, detail="Not an image")

    return Response(
        content=r.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


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


@router.post("/models/custom")
async def list_custom_provider_models(req: CustomModelsRequest):
    """Query an arbitrary OpenAI-compatible endpoint's own /models route.

    Unlike the other providers above, a "custom" endpoint isn't singular — a user
    can save several presets (see prefs.custom_providers), each pointing at a
    different server. So this can't just read a fixed base_url/api_key out of
    prefs; the frontend passes whichever preset's credentials it wants listed.
    """
    base_url = (req.base_url or "").rstrip("/")
    if not base_url or not req.api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {req.api_key}"},
            )
        if resp.status_code != 200:
            return []
        return [{"id": m["id"], "name": m["id"]} for m in resp.json().get("data", [])]
    except Exception as e:
        print(f"Error fetching custom provider models: {e}")
        return []


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


# Repo root, resolved once, so every git subprocess call below runs against the
# actual checkout regardless of the FastAPI process's current working directory
# (relying on cwd here was the root cause of spurious "not a git repository" /
# "branch does not exist" failures when the app was launched from elsewhere).
_REPO_ROOT = str(Path(__file__).resolve().parents[2])


@router.get("/update/check")
async def check_for_updates():
    import subprocess
    import tomli

    # 1. Get current local version
    from app.config import VERSION
    local_version = VERSION

    # 2. Get git remote URL
    git_url = "https://github.com/danish-mar/kokomi.git"
    try:
        res = subprocess.run(
            ["git", "-c", "safe.directory=*", "config", "--get", "remote.origin.url"],
            cwd=_REPO_ROOT,
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


@router.post("/update/run")
async def run_update():
    from fastapi.responses import StreamingResponse
    import json
    import asyncio
    import subprocess
    import shutil
    import sys
    import os

    async def restart_server():
        await asyncio.sleep(2)
        print("🔄 Restarting Kokomi server process...")
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            print(f"❌ Failed to execv restart: {e}. Exiting process to let supervisor handle it...")
            os._exit(0)

    async def update_generator():
        def format_status(status: str, progress: int, error: str = None) -> str:
            data = {"status": status, "progress": progress}
            if error:
                data["error"] = error
            return f"data: {json.dumps(data)}\n\n"

        def git(*args, timeout=15):
            return subprocess.run(
                ["git", "-c", "safe.directory=*", *args],
                cwd=_REPO_ROOT, capture_output=True, text=True, timeout=timeout
            )

        try:
            yield format_status("Checking local repository status...", 10)
            await asyncio.sleep(0.8)
            if not os.path.isdir(os.path.join(_REPO_ROOT, ".git")):
                yield format_status("Error: Not a git repository.", 10, error="Not a git repository.")
                return

            yield format_status("Stashing any uncommitted local changes...", 25)
            await asyncio.sleep(0.8)
            stash_res = git("stash")
            stashed = "No local changes to save" not in (stash_res.stdout or "")

            # Fetch first so remote-tracking refs (and the remote's default branch)
            # are up to date before we look at branch state — a shallow/CI-baked
            # checkout can otherwise have stale or missing remote refs, which is
            # what produces spurious "branch does not exist" style failures below.
            yield format_status("Fetching latest refs from origin...", 35)
            await asyncio.sleep(0.4)
            fetch_res = git("fetch", "origin", "--prune", timeout=30)
            if fetch_res.returncode != 0:
                err_msg = fetch_res.stderr.strip() or "git fetch failed"
                yield format_status(f"Error fetching from remote: {err_msg}", 35, error=err_msg)
                return

            # Docker images built from a CI checkout are frequently left in a
            # detached-HEAD state (single-SHA / shallow checkout with no local
            # branch ref). `git pull` then fails with "You are not currently on
            # a branch" or, once a branch exists locally without upstream
            # tracking, "There is no tracking information for the current
            # branch" / "couldn't find remote ref" — all of which read to users
            # as "branch doesn't exist". Detect that and recover onto the
            # remote's actual default branch instead of failing.
            branch_res = git("rev-parse", "--abbrev-ref", "HEAD")
            current_branch = (branch_res.stdout or "").strip()

            if branch_res.returncode != 0 or current_branch in ("", "HEAD"):
                yield format_status("Repository is in a detached state, recovering default branch...", 40)
                await asyncio.sleep(0.4)

                # Make sure origin/HEAD -> origin/<default> is actually set (shallow
                # clones frequently omit it), then read the default branch name.
                git("remote", "set-head", "origin", "-a")
                symref_res = git("symbolic-ref", "refs/remotes/origin/HEAD")
                default_branch = "main"
                symref = (symref_res.stdout or "").strip()
                if symref_res.returncode == 0 and "/" in symref:
                    default_branch = symref.rsplit("/", 1)[-1]

                checkout_res = git("checkout", "-B", default_branch, f"origin/{default_branch}")
                if checkout_res.returncode != 0:
                    err_msg = checkout_res.stderr.strip() or f"Could not check out '{default_branch}'."
                    yield format_status(f"Error recovering branch: {err_msg}", 40, error=err_msg)
                    return
                current_branch = default_branch

            # GitHub Actions' checkout bakes an expired auth header into .git/config
            # (http.https://github.com/.extraheader). Because .git ships inside the
            # Docker image, every subsequent `git pull` then sends that dead token,
            # GitHub answers 401, and git falls back to prompting for a username —
            # which fails as "terminal prompts disabled". Strip any such header so the
            # pull goes out anonymously against the public repo and succeeds.
            yield format_status("Clearing stale CI credentials...", 50)
            await asyncio.sleep(0.4)
            try:
                hdrs = git("config", "--local", "--get-regexp", "extraheader")
                for line in hdrs.stdout.splitlines():
                    key = line.split(" ", 1)[0].strip()
                    if key:
                        git("config", "--local", "--unset-all", key)
            except Exception:
                pass

            yield format_status("Pulling changes from GitHub repository...", 55)
            await asyncio.sleep(0.8)
            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"
            # Pull the current branch explicitly from origin rather than a bare
            # `git pull` — a bare pull depends on upstream-tracking config being
            # correctly set, which is exactly what's missing/stale in the
            # detached-HEAD / freshly-recovered-branch cases handled above.
            pull_res = subprocess.run([
                "git",
                "-c", "safe.directory=*",
                "-c", "credential.helper=",
                "-c", "credential.https://github.com.helper=",
                # Override the key inline too, in case it lingers under a different URL form.
                "-c", "http.https://github.com/.extraheader=",
                "pull", "origin", current_branch
            ], cwd=_REPO_ROOT, env=env, capture_output=True, text=True, timeout=30)
            if pull_res.returncode != 0:
                err_msg = pull_res.stderr.strip() or "git pull failed"
                if stashed:
                    git("stash", "pop")
                yield format_status(f"Error pulling changes: {err_msg}", 55, error=err_msg)
                return

            if stashed:
                yield format_status("Restoring stashed local changes...", 60)
                await asyncio.sleep(0.4)
                pop_res = git("stash", "pop")
                if pop_res.returncode != 0:
                    err_msg = pop_res.stderr.strip() or "git stash pop failed (conflicts with pulled changes)"
                    yield format_status(f"Error restoring local changes: {err_msg}", 60, error=err_msg)
                    return

            yield format_status("Synchronizing dependencies & local package...", 80)
            await asyncio.sleep(0.8)
            uv_path = shutil.which("uv")
            if uv_path:
                subprocess.run([uv_path, "sync"], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=60)
            else:
                subprocess.run([sys.executable, "-m", "pip", "install", "-e", "."], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=60)

            yield format_status("Done! Kokomi will restart to apply changes...", 100)
            await asyncio.sleep(0.8)
            asyncio.create_task(restart_server())

        except Exception as e:
            yield format_status(f"Unexpected error: {str(e)}", 100, error=str(e))

    return StreamingResponse(update_generator(), media_type="text/event-stream")

