import os
import sys
import json
import shutil
import uuid
import datetime
import subprocess
import requests
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.storage import load_mcp, save_mcp, load_chars, save_chars
from app.mcp import init_pool

router = APIRouter(prefix="/api/app-store", tags=["app-store"])

GITHUB_MANIFEST_URL = "https://raw.githubusercontent.com/danish-mar/kokomi-appstore/main/manifest.json"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/danish-mar/kokomi-appstore/main"

class InstallPayload(BaseModel):
    id: str
    type: str  # 'mcp' (app) or 'character' (persona)
    path: str

@router.get("/catalog")
async def get_catalog():
    """Fetch app store catalog from GitHub and cross-reference with installed apps."""
    try:
        resp = requests.get(GITHUB_MANIFEST_URL, timeout=10)
        if resp.status_code != 200:
            raise HTTPException(502, f"Failed to fetch manifest from GitHub: HTTP {resp.status_code}")
        catalog = resp.json()
    except Exception as e:
        raise HTTPException(502, f"Failed to reach GitHub App Store repository: {str(e)}")

    apps_list = catalog.get("apps", [])
    personas_list = catalog.get("personas", [])

    # Format for Alpine frontend compatibility
    for app in apps_list:
        app["type"] = "mcp"
    for p in personas_list:
        p["type"] = "character"

    # Identify installed IDs
    installed_ids = []
    
    # Heal database if appbridge is saved with env string
    mcp_servers = load_mcp()
    if "appbridge" in mcp_servers:
        if isinstance(mcp_servers["appbridge"].get("env"), str):
            mcp_servers["appbridge"]["env"] = {}
            save_mcp(mcp_servers)
            try:
                await init_pool(force=True)
            except Exception as e:
                print(f"[App Store Manager] error auto-healing pool: {e}")

    # Check apps inside data/apps/
    apps_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "apps"))
    if os.path.exists(apps_dir):
        for app_id in os.listdir(apps_dir):
            if os.path.isdir(os.path.join(apps_dir, app_id)):
                # If directory exists and contains manifest, it is installed
                if os.path.exists(os.path.join(apps_dir, app_id, "manifest.json")):
                    installed_ids.append(app_id)

    # Check personas inside SQLite characters database
    chars = load_chars()
    for p in personas_list:
        p_id = p.get("id")
        p_name = p.get("name", "").lower().strip()
        # Match by ID or Name
        exists = (p_id in chars) or any(c.get("name", "").lower().strip() == p_name for c in chars.values())
        if exists:
            installed_ids.append(p_id)

    # Pick first app as featuredApp
    featured_app = apps_list[0] if apps_list else None

    return {
        "apps": apps_list,
        "personas": personas_list,
        "featuredApp": featured_app,
        "installedIds": installed_ids,
        "catalogBaseUrl": GITHUB_RAW_BASE
    }

@router.post("/install")
async def install_item(payload: InstallPayload):
    """Download and install app or persona from GitHub repository."""
    
    # ── INSTALL APP ──────────────────────────────────────────────────
    if payload.type == "mcp":
        apps_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "apps"))
        app_dest = os.path.join(apps_dir, payload.id)
        os.makedirs(app_dest, exist_ok=True)

        # Download manifest.json, main.py, requirements.txt
        for filename in ["manifest.json", "main.py", "requirements.txt"]:
            url = f"{GITHUB_RAW_BASE}/{payload.path}/{filename}"
            try:
                res = requests.get(url, timeout=10)
                if res.status_code == 200:
                    with open(os.path.join(app_dest, filename), "wb") as f:
                        f.write(res.content)
            except Exception as e:
                # Clean up and fail
                shutil.rmtree(app_dest, ignore_errors=True)
                raise HTTPException(502, f"Failed to download {filename} from GitHub: {str(e)}")

        # Install dependencies
        req_file = os.path.join(app_dest, "requirements.txt")
        if os.path.exists(req_file):
            uv_bin = shutil.which("uv")
            if uv_bin:
                cmd = [uv_bin, "pip", "install", "-r", req_file]
            else:
                cmd = [sys.executable, "-m", "pip", "install", "-r", req_file]
            
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as err:
                shutil.rmtree(app_dest, ignore_errors=True)
                raise HTTPException(500, f"Failed to install dependencies: {err.stderr.decode().strip()}")

        # Ensure Stdio Apps Bridge is registered in mcp_servers
        mcp_servers = load_mcp()
        bridge_exists = any("app/mcp_app_bridge.py" in str(s.get("args", [])) for s in mcp_servers.values())
        if not bridge_exists:
            sid = "appbridge"
            mcp_servers[sid] = {
                "id": sid,
                "name": "Kokomi Apps Bridge",
                "transport": "stdio",
                "command": sys.executable,
                "args": ["app/mcp_app_bridge.py"],
                "env": {},  # Set env dictionary correctly instead of a string to prevent mapping errors
                "url": "",
                "icon": "fa-puzzle-piece",
                "enabled": 1,
                "created_at": datetime.datetime.utcnow().isoformat()
            }
            save_mcp(mcp_servers)

        # Enable this app for all characters by default
        try:
            chars = load_chars()
            for cid, char in chars.items():
                if "mcp_servers" not in char:
                    char["mcp_servers"] = []
                if payload.id not in char["mcp_servers"]:
                    char["mcp_servers"].append(payload.id)
            save_chars(chars)
        except Exception as e:
            print(f"[App Store Manager] warning enabling app for characters: {e}")

        # Refresh MCP Pool
        try:
            await init_pool(force=True)
        except Exception as e:
            print(f"[App Store Manager] warning refreshing pool: {e}")

        return {"ok": True, "message": "App installed and bridge server loaded."}

    # ── INSTALL PERSONA ──────────────────────────────────────────────
    elif payload.type == "character":
        # Download manifest and prompt
        try:
            manifest_res = requests.get(f"{GITHUB_RAW_BASE}/{payload.path}/manifest.json", timeout=10)
            prompt_res = requests.get(f"{GITHUB_RAW_BASE}/{payload.path}/prompt.txt", timeout=10)
            
            if manifest_res.status_code != 200 or prompt_res.status_code != 200:
                raise HTTPException(502, "Failed to download persona files from GitHub.")
                
            manifest = manifest_res.json()
            prompt_text = prompt_res.text
        except Exception as e:
            raise HTTPException(502, f"Failed to fetch persona: {str(e)}")

        # Download avatar image if present
        avatar_path = None
        avatar_filename = manifest.get("avatar")
        possible_avatars = [avatar_filename] if avatar_filename else ["profile.png", "profile.jpg", "profile.jpeg", "profile.webp"]
        
        avatars_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "avatars"))
        os.makedirs(avatars_dir, exist_ok=True)
        
        for filename in possible_avatars:
            if not filename:
                continue
            avatar_dest = f"{payload.id}_{filename}"
            try:
                res = requests.get(f"{GITHUB_RAW_BASE}/{payload.path}/{filename}", timeout=10)
                if res.status_code == 200:
                    with open(os.path.join(avatars_dir, avatar_dest), "wb") as f:
                        f.write(res.content)
                    avatar_path = f"/avatars/{avatar_dest}"
                    break
            except Exception:
                pass

        # Add to characters database
        chars = load_chars()
        mcp_list = ["appbridge"] if manifest.get("config", {}).get("mcp_servers") else []
        
        chars[payload.id] = {
            "id": payload.id,
            "name": manifest.get("name", "New Persona").strip(),
            "persona": prompt_text.strip(),
            "avatar": avatar_path,
            "mcp_servers": mcp_list,
            "groq_model": manifest.get("config", {}).get("groq_model", "default"),
            "google_model": manifest.get("config", {}).get("google_model", "default"),
            "local_model": manifest.get("config", {}).get("local_model", "default"),
            "nvidia_model": manifest.get("config", {}).get("nvidia_model", "default"),
            "voice": manifest.get("config", {}).get("voice", "aoede"),
            "memory_enabled": manifest.get("config", {}).get("memory_enabled", True),
            "created_at": datetime.datetime.utcnow().isoformat(),
        }
        save_chars(chars)
        return {"ok": True, "message": "Persona installed successfully."}

    raise HTTPException(400, "Invalid item type.")


class TogglePayload(BaseModel):
    id: str
    enabled: bool

def get_dir_size(path):
    total = 0
    if not os.path.exists(path):
        return 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                total += os.path.getsize(fp)
    return total

@router.get("/installed-apps")
async def get_installed_apps():
    """List all installed apps from data/apps with metadata, sizes, and active states."""
    apps_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "apps"))
    installed = []
    if not os.path.exists(apps_dir):
        return installed
    for app_id in os.listdir(apps_dir):
        app_path = os.path.join(apps_dir, app_id)
        if os.path.isdir(app_path):
            manifest_path = os.path.join(app_path, "manifest.json")
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r") as f:
                        manifest = json.load(f)
                    size_bytes = get_dir_size(app_path)
                    
                    # Size formatting
                    if size_bytes < 1024:
                        size_formatted = f"{size_bytes} Bytes"
                    elif size_bytes < 1024 * 1024:
                        size_formatted = f"{size_bytes / 1024:.1f} KB"
                    else:
                        size_formatted = f"{size_bytes / (1024 * 1024):.1f} MB"
                        
                    installed.append({
                        "id": app_id,
                        "name": manifest.get("name", app_id).title(),
                        "developer": manifest.get("developer", "Kokomi Developer"),
                        "description": manifest.get("description", "No description provided."),
                        "entrypoint": manifest.get("entrypoint", "main.py"),
                        "enabled": manifest.get("enabled", True),
                        "size_formatted": size_formatted,
                        "version": manifest.get("version", "1.0.0")
                    })
                except Exception:
                    pass
    return installed

@router.post("/toggle")
async def toggle_app(payload: TogglePayload):
    """Enable or disable an installed app by rewriting its manifest.json config."""
    apps_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "apps"))
    manifest_path = os.path.join(apps_dir, payload.id, "manifest.json")
    if not os.path.exists(manifest_path):
        raise HTTPException(404, "App not found")
        
    try:
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
        manifest["enabled"] = payload.enabled
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
            
        # Refresh pool so the tool is dynamically enabled or disabled
        await init_pool(force=True)
        return {"ok": True, "enabled": payload.enabled}
    except Exception as e:
        raise HTTPException(500, f"Failed to toggle app: {str(e)}")

@router.delete("/uninstall/{app_id}")
async def uninstall_app(app_id: str):
    """Uninstall an app or persona by deleting its files/database entry."""
    # 1. Try to see if it is a character
    chars = load_chars()
    if app_id in chars:
        char = chars[app_id]
        avatar = char.get("avatar")
        if avatar and avatar.startswith("/avatars/"):
            avatar_file = avatar.split("/avatars/")[-1]
            avatars_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "avatars"))
            avatar_path_full = os.path.join(avatars_dir, avatar_file)
            if os.path.exists(avatar_path_full):
                try:
                    os.remove(avatar_path_full)
                except Exception as e:
                    print(f"[App Store Manager] error deleting avatar: {e}")
        del chars[app_id]
        save_chars(chars)
        return {"ok": True, "message": "Persona uninstalled successfully."}

    # 2. Try to see if it is an app
    apps_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "apps"))
    app_dest = os.path.join(apps_dir, app_id)
    if not os.path.exists(app_dest):
        raise HTTPException(404, "App or Persona not found")
        
    try:
        shutil.rmtree(app_dest, ignore_errors=True)
        
        # Clean up character references
        try:
            chars = load_chars()
            for cid, char in chars.items():
                if "mcp_servers" in char and app_id in char["mcp_servers"]:
                    char["mcp_servers"].remove(app_id)
            save_chars(chars)
        except Exception as e:
            print(f"[App Store Manager] warning cleaning character references: {e}")

        # Refresh pool so the tool disappears
        await init_pool(force=True)
        return {"ok": True, "message": "App uninstalled successfully."}
    except Exception as e:
        raise HTTPException(500, f"Failed to uninstall app: {str(e)}")

