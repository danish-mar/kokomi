import os
import re
import json
import uuid
import shutil
import datetime
from typing import Optional, List
from contextlib import AsyncExitStack
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from google import genai
from google.genai import types

import uvicorn
import httpx

# MCP imports
try:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("⚠️  MCP SDK not installed. Tool calling disabled.")

load_dotenv()

app = FastAPI(title="Kokomi AI")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="public/static"), name="static")
app.mount("/avatars", StaticFiles(directory="data/avatars"), name="avatars")


# ═══════════════════════════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════════════════════════

DATA_DIR = "data"
CONVOS_FILE = os.path.join(DATA_DIR, "conversations.json")
CHARS_FILE = os.path.join(DATA_DIR, "characters.json")
MCP_FILE = os.path.join(DATA_DIR, "mcp_servers.json")
USER_PREFS_FILE = os.path.join(DATA_DIR, "user_prefs.json")
FOLDERS_FILE = os.path.join(DATA_DIR, "folders.json")
AVATARS_DIR = os.path.join(DATA_DIR, "avatars")
os.makedirs(AVATARS_DIR, exist_ok=True)


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def _save(path: str, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_prefs():
    prefs = _load(USER_PREFS_FILE)
    defaults = {
        "model_name": "qwen-2.5-32b",
        "user_persona": "",
        "theme": "dark",
        "dynamic_suggestions": True,
        "streaming_mode": True,
        "llm_provider": "groq",
        "local_url": "http://localhost:8080/v1",
        "local_model": "local-model",
        "inject_time": False,
    }
    if not prefs:
        prefs = defaults.copy()
        _save(USER_PREFS_FILE, prefs)
        return prefs

    changed = False
    for k, v in defaults.items():
        if k not in prefs:
            prefs[k] = v
            changed = True
    if changed:
        _save(USER_PREFS_FILE, prefs)
    return prefs


def save_prefs(d):
    _save(USER_PREFS_FILE, d)


def load_convos():
    return _load(CONVOS_FILE)


def save_convos(d):
    _save(CONVOS_FILE, d)


def load_chars():
    chars = _load(CHARS_FILE)
    if not chars:
        chars["kokomi"] = {
            "id": "kokomi",
            "name": "Kokomi",
            "persona": "You are Kokomi, the Divine Priestess of Watatsumi Island. You are a brilliant strategist and a gentle, thoughtful leader. You use markdown for formatting. Speak with grace and wisdom.",
            "avatar": None,
            "mcp_servers": [],
            "model": "default",
            "created_at": datetime.datetime.utcnow().isoformat(),
        }
        chars["nahida"] = {
            "id": "nahida",
            "name": "Nahida",
            "persona": "You are Nahida, the Lesser Lord Kusanali and the Avatar of Irminsul. You are wise, curious, and speak in beautiful metaphors. You are deeply knowledgeable about the world and treat everyone with kindness and a sense of wonder. Use markdown for formatting.",
            "avatar": None,
            "mcp_servers": [],
            "model": "default",
            "created_at": datetime.datetime.utcnow().isoformat(),
        }
        _save(CHARS_FILE, chars)
    return chars


def save_chars(d):
    _save(CHARS_FILE, d)


def load_mcp():
    return _load(MCP_FILE)


def save_mcp(d):
    _save(MCP_FILE, d)


def load_folders():
    return _load(FOLDERS_FILE)


def save_folders(d):
    _save(FOLDERS_FILE, d)


# ═══════════════════════════════════════════════════════════════════
# LLM
# ═══════════════════════════════════════════════════════════════════

groq_key = os.getenv("GROQ_API_KEY")
google_key = os.getenv("GOOGLE_API_KEY")

title_llm = ChatGroq(
    model_name="meta-llama/llama-4-scout-17b-16e-instruct",
    temperature=0.3,
    groq_api_key=groq_key,
) if groq_key else None


class GeminiDirectLLM:
    """
    Direct wrapper for google-genai SDK mimicking a LangChain-style interface.

    FIX: The original code created a second async_client with
    http_options={'api_version': 'v1beta'} inside astream(), which caused
    a 404 Not Found for models like gemini-2.5-flash because the v1beta
    endpoint path is different from what the SDK constructs by default.
    We now reuse a single self.async_client (no custom http_options) for
    both ainvoke and astream.
    """

    def __init__(self, model_name: str, api_key: str, temperature: float = 0.7):
        self.model_name = model_name
        self.api_key = api_key
        self.temperature = temperature
        self.tools = None
        # One shared client — no http_options override
        self.client = genai.Client(api_key=api_key)
        self.async_client = genai.Client(api_key=api_key)

    def _convert_messages(self, messages):
        genai_msgs = []
        system_instruction = None
        for m in messages:
            if isinstance(m, SystemMessage):
                system_instruction = m.content
            elif isinstance(m, HumanMessage):
                genai_msgs.append({"role": "user", "parts": [{"text": m.content}]})
            elif isinstance(m, AIMessage):
                genai_msgs.append({"role": "model", "parts": [{"text": m.content}]})
        return genai_msgs, system_instruction

    def _make_config(self, system_instruction):
        return types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=self.temperature,
            tools=self.tools,
        )

    async def astream(self, messages):
        contents, system_instruction = self._convert_messages(messages)
        config = self._make_config(system_instruction)

        class _Chunk:
            def __init__(self, content):
                self.content = content
                self.additional_kwargs = {}

        async for chunk in await self.async_client.aio.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=config,
        ):
            yield _Chunk(chunk.text or "")

    async def ainvoke(self, messages):
        contents, system_instruction = self._convert_messages(messages)
        config = self._make_config(system_instruction)

        response = await self.async_client.aio.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )

        class _Response:
            def __init__(self, content):
                self.content = content

        return _Response(response.text)

    def bind_tools(self, tools):
        self.tools = tools
        return self


def _normalize_model(name: str) -> str:
    if name and name.startswith("models/"):
        return name[len("models/"):]
    return name


def get_llm(prefs: dict, streaming: bool = False, model_override: Optional[str] = None):
    provider = prefs.get("llm_provider", "groq")

    if provider == "local":
        base_url = prefs.get("local_url", "http://localhost:8080/v1")
        model = _normalize_model(prefs.get("local_model", "local-model"))
        if model_override and model_override != "default":
            model = _normalize_model(model_override)
        return ChatOpenAI(
            base_url=base_url,
            api_key="sk-no-key-required",
            model_name=model,
            temperature=0.7,
            streaming=streaming,
        )

    elif provider == "google":
        if not google_key:
            raise ValueError("GOOGLE_API_KEY not found in environment")
        active_model = _normalize_model(prefs.get("model_name", "gemini-2.0-flash"))
        if model_override and model_override != "default":
            active_model = _normalize_model(model_override)
        return GeminiDirectLLM(
            model_name=active_model,
            api_key=google_key,
            temperature=0.7,
        )

    else:  # groq (default)
        active_model = _normalize_model(prefs.get("model_name", "qwen-2.5-32b"))
        if model_override and model_override != "default":
            active_model = _normalize_model(model_override)
        return ChatGroq(
            model_name=active_model,
            temperature=0.7,
            groq_api_key=groq_key,
            streaming=streaming,
        )


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def parse_thinking(raw: str):
    m = re.search(r"<think>(.*?)</think>", raw, re.DOTALL)
    if m:
        return raw[: m.start()].strip() + raw[m.end():].strip(), m.group(1).strip()
    return raw.strip(), None


async def generate_title(user_msg: str, ai_msg: str) -> str:
    try:
        prompt = (
            f"Generate a short title (max 4 words, no quotes) for:\n"
            f"User: {user_msg[:150]}\nAssistant: {ai_msg[:150]}\n\nTitle:"
        )
        resp = title_llm.invoke([HumanMessage(content=prompt)])
        title = resp.content.strip().strip("\"'").strip()
        return title[:50] if title else user_msg[:30]
    except Exception:
        return user_msg[:30] + ("..." if len(user_msg) > 30 else "")


# ═══════════════════════════════════════════════════════════════════
# MCP RUNTIME
# ═══════════════════════════════════════════════════════════════════

async def connect_mcp_servers(stack: AsyncExitStack, server_ids: list):
    """Connect to MCP servers and return tool definitions + session map."""
    if not MCP_AVAILABLE or not server_ids:
        return [], {}, []

    servers = load_mcp()
    tool_defs = []
    tool_sessions = {}
    errors = []

    for sid in server_ids:
        config = servers.get(sid)
        if not config or not config.get("enabled", True):
            continue

        try:
            transport = config.get("transport", "stdio")

            if transport == "stdio":
                cmd = config.get("command", "")
                args = config.get("args", [])
                env_vars = config.get("env", {})
                if not cmd:
                    continue
                merged_env = {**os.environ, **env_vars} if env_vars else None
                params = StdioServerParameters(
                    command=cmd,
                    args=args if isinstance(args, list) else args.split(),
                    env=merged_env,
                )
                read, write = await stack.enter_async_context(stdio_client(params))

            elif transport == "sse":
                from mcp.client.sse import sse_client
                url = config.get("url", "")
                if not url:
                    continue
                read, write = await stack.enter_async_context(sse_client(url))

            elif transport == "streamable-http":
                from mcp.client.streamable_http import streamable_http_client
                url = config.get("url", "")
                if not url:
                    continue
                streams = await stack.enter_async_context(streamable_http_client(url))
                read, write = streams[0], streams[1]
            else:
                continue

            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            result = await session.list_tools()
            for tool in result.tools:
                tool_defs.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}},
                    },
                })
                tool_sessions[tool.name] = session
            print(f"  ✅ MCP '{config['name']}': {len(result.tools)} tools")

        except BaseException as e:
            err_msg = f"MCP server '{config.get('name', sid)}' connection failed."
            errors.append(err_msg)
            print(f"  ❌ {err_msg}: {e}")

    return tool_defs, tool_sessions, errors


# ═══════════════════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════════════════

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    character_id: Optional[str] = "kokomi"
    participants: Optional[List[str]] = None


class MCPServerCreate(BaseModel):
    name: str
    transport: str = "stdio"
    command: Optional[str] = None
    args: Optional[List[str]] = []
    env: Optional[dict] = {}
    url: Optional[str] = None
    enabled: bool = True


class FolderCreate(BaseModel):
    name: str
    icon: str = "fa-folder"


class ConversationFolderUpdate(BaseModel):
    folder_id: Optional[str] = None


class PrefsUpdate(BaseModel):
    model_name: str
    user_persona: str
    dynamic_suggestions: bool = True
    streaming_mode: bool = True
    inject_time: bool = False
    llm_provider: Optional[str] = "groq"
    local_url: Optional[str] = "http://localhost:8080/v1"
    local_model: Optional[str] = "local-model"


# ═══════════════════════════════════════════════════════════════════
# PREFS & MODELS
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/prefs")
async def get_prefs():
    return load_prefs()


@app.post("/api/prefs")
async def update_prefs(p: PrefsUpdate):
    prefs = load_prefs()
    prefs.update(p.model_dump())
    save_prefs(prefs)
    return prefs


@app.get("/api/models")
async def list_available_models():
    curated = [
        {"id": "qwen-2.5-32b", "name": "Qwen 2.5 32B", "provider": "groq"},
        {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B", "provider": "groq"},
        {"id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B", "provider": "groq"},
        {"id": "mixtral-8x7b-32768", "name": "Mixtral 8x7B", "provider": "groq"},
        {"id": "deepseek-r1-distill-llama-70b", "name": "DeepSeek R1 70B (Llama)", "provider": "groq"},
        {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "provider": "google"},
        {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "provider": "google"},
        {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "provider": "google"},
        {"id": "gemini-2.0-flash-lite", "name": "Gemini 2.0 Flash Lite", "provider": "google"},
        {"id": "local-model", "name": "Local Model", "provider": "local"},
    ]

    all_models = list(curated)
    curated_ids = {m["id"] for m in curated}

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

    return all_models


# ═══════════════════════════════════════════════════════════════════
# MCP SERVER CRUD
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/mcp-servers")
async def list_mcp_servers():
    return list(load_mcp().values())


@app.get("/api/mcp-servers/{sid}")
async def get_mcp_server(sid: str):
    servers = load_mcp()
    if sid not in servers:
        raise HTTPException(404, "Not found")
    return servers[sid]


@app.post("/api/mcp-servers")
async def create_mcp_server(config: MCPServerCreate):
    servers = load_mcp()
    sid = str(uuid.uuid4())[:8]
    servers[sid] = {
        "id": sid,
        **config.model_dump(),
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    save_mcp(servers)
    return servers[sid]


@app.put("/api/mcp-servers/{sid}")
async def update_mcp_server(sid: str, config: MCPServerCreate):
    servers = load_mcp()
    if sid not in servers:
        raise HTTPException(404, "Not found")
    servers[sid].update(config.model_dump())
    save_mcp(servers)
    return servers[sid]


@app.delete("/api/mcp-servers/{sid}")
async def delete_mcp_server(sid: str):
    servers = load_mcp()
    if sid not in servers:
        raise HTTPException(404, "Not found")
    del servers[sid]
    save_mcp(servers)
    chars = load_chars()
    for c in chars.values():
        if sid in c.get("mcp_servers", []):
            c["mcp_servers"].remove(sid)
    save_chars(chars)
    return {"ok": True}


@app.post("/api/mcp-servers/{sid}/test")
async def test_mcp_server(sid: str):
    servers = load_mcp()
    if sid not in servers:
        raise HTTPException(404, "Not found")
    if not MCP_AVAILABLE:
        return {"ok": False, "error": "MCP SDK not installed"}
    try:
        async with AsyncExitStack() as stack:
            tool_defs, _, _errs = await connect_mcp_servers(stack, [sid])
            tools = [t["function"]["name"] for t in tool_defs]
            return {"ok": True, "tools": tools, "count": len(tools)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# CHARACTER CRUD
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/characters")
async def list_characters():
    return list(load_chars().values())


@app.get("/api/characters/{cid}")
async def get_character(cid: str):
    chars = load_chars()
    if cid not in chars:
        raise HTTPException(404, "Not found")
    return chars[cid]


@app.post("/api/characters")
async def create_character(
    name: str = Form(...),
    persona: str = Form(...),
    mcp_servers: str = Form(""),
    model: str = Form("default"),
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
        "model": model.strip(),
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    save_chars(chars)
    return chars[cid]


@app.put("/api/characters/{cid}")
async def update_character(
    cid: str,
    name: str = Form(...),
    persona: str = Form(...),
    mcp_servers: str = Form(""),
    model: str = Form("default"),
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
    chars[cid]["model"] = model.strip()
    save_chars(chars)
    return chars[cid]


@app.delete("/api/characters/{cid}")
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


# ═══════════════════════════════════════════════════════════════════
# CONVERSATION CRUD
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/characters/{char_id}/suggestions")
async def get_char_suggestions(char_id: str):
    if not groq_key:
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
            {"icon": "fa-solid fa-message", "label": "Say Hello", "text": f"Hello {char['name']}!"},
            {"icon": "fa-solid fa-question", "label": "Ask Anything", "text": "Tell me something interesting about yourself."},
        ]


@app.get("/api/conversations")
async def list_conversations_api():
    convos = load_convos()
    result = []
    for cid, c in convos.items():
        result.append({
            "_id": cid,
            "title": c.get("title", "Untitled"),
            "character_id": c.get("character_id", "kokomi"),
            "folder_id": c.get("folder_id", None),
            "updated_at": c.get("updated_at", ""),
        })
    result.sort(key=lambda x: x["updated_at"], reverse=True)
    return result[:50]


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    convos = load_convos()
    if conv_id not in convos:
        raise HTTPException(404, "Not found")
    c = dict(convos[conv_id])
    c["_id"] = conv_id
    return c


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    convos = load_convos()
    if conv_id not in convos:
        raise HTTPException(404, "Not found")
    del convos[conv_id]
    save_convos(convos)
    return {"ok": True}


@app.post("/api/conversations/{conv_id}/pop")
async def pop_last_messages(conv_id: str):
    convos = load_convos()
    if conv_id not in convos:
        raise HTTPException(404, "Not found")
    msgs = convos[conv_id].get("messages", [])
    last_user_idx = -1
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i]["role"] == "user":
            last_user_idx = i
            break
    if last_user_idx != -1:
        convos[conv_id]["messages"] = msgs[:last_user_idx]
    save_convos(convos)
    return {"ok": True, "count": len(convos[conv_id]["messages"])}


@app.delete("/api/conversations/{conv_id}/messages/{msg_index}")
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


# ═══════════════════════════════════════════════════════════════════
# FOLDER API
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/folders")
async def list_folders_api():
    return list(load_folders().values())


@app.post("/api/folders")
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


@app.put("/api/folders/{fid}")
async def update_folder(fid: str, req: FolderCreate):
    folders = load_folders()
    if fid not in folders:
        raise HTTPException(404, "Folder not found")
    folders[fid]["name"] = req.name
    folders[fid]["icon"] = req.icon
    save_folders(folders)
    return folders[fid]


@app.delete("/api/folders/{fid}")
async def delete_folder(fid: str):
    folders = load_folders()
    if fid in folders:
        del folders[fid]
        save_folders(folders)
        convos = load_convos()
        for c in convos.values():
            if c.get("folder_id") == fid:
                c["folder_id"] = None
        save_convos(convos)
    return {"ok": True}


@app.put("/api/conversations/{cid}/folder")
async def assign_conversation_to_folder(cid: str, req: ConversationFolderUpdate):
    convos = load_convos()
    if cid not in convos:
        raise HTTPException(404, "Conversation not found")
    convos[cid]["folder_id"] = req.folder_id
    save_convos(convos)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════
# CHAT (non-streaming)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not groq_key:
        raise HTTPException(500, "GROQ_API_KEY not set")

    prefs = load_prefs()
    provider = prefs.get("llm_provider", "groq")

    if provider == "google":
        active_model = _normalize_model(prefs.get("model_name", "gemini-2.5-flash"))
    elif provider == "local":
        active_model = _normalize_model(prefs.get("local_model", "local-model"))
    else:
        active_model = _normalize_model(prefs.get("model_name", "qwen-2.5-32b"))

    user_p = prefs.get("user_persona", "")
    chars = load_chars()
    char_id = req.character_id or "kokomi"
    char = chars.get(char_id, chars.get("kokomi"))

    p_model_override = char.get("model", "default")
    current_llm = get_llm(prefs, model_override=p_model_override)

    convos = load_convos()
    conv_id = req.conversation_id
    is_new = conv_id is None or conv_id not in convos
    history = [] if is_new else convos[conv_id].get("messages", [])

    now = datetime.datetime.utcnow().isoformat()
    history.append({"role": "user", "content": req.message, "timestamp": now})

    persona = char.get("persona", "You are a helpful AI assistant.")
    if user_p:
        persona += f"\n\nInformation about the user (User Persona):\n{user_p}"
    if prefs.get("inject_time"):
        persona += f"\n\nCurrent System Date and Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    persona += "\n\nCRITICAL: Always wrap your internal reasoning/thought process inside <think> and </think> tags before providing your final response."

    lc_msgs = [SystemMessage(content=persona)]
    for m in history[-12:]:
        if m["role"] == "user":
            lc_msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            comb = m["content"]
            if m.get("thinking"):
                comb = f"<think>{m['thinking']}</think>\n\n{comb}"
            lc_msgs.append(AIMessage(content=comb))

    mcp_server_ids = char.get("mcp_servers", [])
    tool_calls_log = []
    all_thinking = []

    try:
        async with AsyncExitStack() as stack:
            tool_defs, tool_sessions, _ = await connect_mcp_servers(stack, mcp_server_ids)

            if tool_defs:
                llm_with_tools = current_llm.bind_tools(tool_defs)
                response = await llm_with_tools.ainvoke(lc_msgs)
                final_content, t = parse_thinking(response.content)
                if t:
                    all_thinking.append(t)

                rounds = 0
                while response.tool_calls and rounds < 5:
                    rounds += 1
                    lc_msgs.append(response)
                    for tc in response.tool_calls:
                        tool_name = tc["name"]
                        tool_args = tc["args"]
                        tool_call_id = tc.get("id", str(uuid.uuid4())[:8])
                        try:
                            session = tool_sessions.get(tool_name)
                            if session:
                                print(f"  [DEBUG] Calling MCP Tool: '{tool_name}' with args: {tool_args}")
                                result = await session.call_tool(tool_name, arguments=tool_args)
                                res_txt = "".join([getattr(b, "text", str(b)) for b in result.content])
                            else:
                                res_txt = f"Error: '{tool_name}' not found"
                        except Exception as e:
                            res_txt = f"Error: {e}"
                        tool_calls_log.append({"name": tool_name, "args": tool_args, "result": res_txt})
                        lc_msgs.append(ToolMessage(content=res_txt, tool_call_id=tool_call_id))

                    response = await llm_with_tools.ainvoke(lc_msgs)
                    final_content, t = parse_thinking(response.content)
                    if t:
                        all_thinking.append(t)

                raw_content = final_content
            else:
                response = await current_llm.ainvoke(lc_msgs)
                raw_content, t = parse_thinking(response.content)
                if t:
                    all_thinking.append(t)
    except Exception as e:
        raise HTTPException(500, f"LLM/MCP error: {e}")

    thinking_str = "\n\n".join(all_thinking) if all_thinking else None
    content = raw_content.strip()
    history.append({
        "role": "assistant",
        "content": content,
        "thinking": thinking_str,
        "tool_calls": tool_calls_log if tool_calls_log else None,
        "model": active_model,
        "timestamp": now,
    })

    if is_new:
        conv_id = str(uuid.uuid4())[:12]
        title = await generate_title(req.message, content)
        convos[conv_id] = {"title": title, "character_id": char_id, "messages": history, "updated_at": now}
    else:
        convos[conv_id].update({"messages": history, "updated_at": now})

    save_convos(convos)
    return {
        "conversation_id": conv_id,
        "response": content,
        "thinking": thinking_str,
        "tool_calls": tool_calls_log if tool_calls_log else None,
        "model": active_model,
    }


# ═══════════════════════════════════════════════════════════════════
# CHAT (streaming)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    if not groq_key:
        raise HTTPException(500, "GROQ_API_KEY not set")

    prefs = load_prefs()
    provider = prefs.get("llm_provider", "groq")

    if provider == "google":
        active_model = _normalize_model(prefs.get("model_name", "gemini-2.5-flash"))
    elif provider == "local":
        active_model = _normalize_model(prefs.get("local_model", "local-model"))
    else:
        active_model = _normalize_model(prefs.get("model_name", "qwen-2.5-32b"))

    user_p = prefs.get("user_persona", "")
    chars = load_chars()
    char_id = req.character_id or "kokomi"
    char = chars.get(char_id, chars.get("kokomi"))
    p_model_override = char.get("model", "default")

    convos = load_convos()
    conv_id = req.conversation_id
    is_new = conv_id is None or conv_id not in convos

    if is_new:
        conv_id = str(uuid.uuid4())[:12]

    history = [] if (not conv_id or conv_id not in convos) else convos[conv_id].get("messages", [])
    now = datetime.datetime.utcnow().isoformat()
    history.append({"role": "user", "content": req.message, "timestamp": now})

    async def event_generator():
        nonlocal history
        try:
            yield f"data: {json.dumps({'type': 'start'})}\n\n"

            pids = req.participants or [char_id]
            all_chars = load_chars()

            all_mcp_ids = []
            for pid in pids:
                p_char = all_chars.get(pid)
                if p_char:
                    all_mcp_ids.extend(p_char.get("mcp_servers", []))
            all_mcp_ids = list(set(all_mcp_ids))

            async with AsyncExitStack() as stack:
                tool_defs, tool_sessions, mcp_errors = await connect_mcp_servers(stack, all_mcp_ids)
                for err in mcp_errors:
                    yield f"data: {json.dumps({'type': 'warning', 'message': err})}\n\n"

                for pid in pids:
                    p_char = all_chars.get(pid)
                    if not p_char:
                        continue

                    char_name = p_char.get("name", pid)
                    p_persona = p_char.get("persona", "")
                    if user_p:
                        p_persona += f"\n\nUser Profile:\n{user_p}"

                    if len(pids) > 1:
                        other_names = [all_chars.get(x, {}).get("name", x) for x in pids if x != pid]
                        p_persona += f"\n\nGROUP CHAT: You are {char_name} in a group chat. Other participants: {', '.join(other_names)} and the user."
                        p_persona += "\n\nSTRICT RULES:"
                        p_persona += f"\n- You are ONLY {char_name}. NEVER write dialogue or responses for {', '.join(other_names)} or any other character."
                        p_persona += "\n- Do NOT prefix your response with your own name (e.g. no 'Kokomi:' at the start)."
                        p_persona += "\n- Respond naturally as yourself. Other characters will get their own turn."
                        p_persona += "\n- If the last message is not directed at you and you have nothing to add, respond with exactly: [SKIP]"

                    if prefs.get("inject_time"):
                        p_persona += f"\n\nCurrent System Date and Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

                    p_persona += "\n\nIMPORTANT: Always wrap internal reasoning inside <think>...</think> tags before your response."

                    p_lc_msgs = [SystemMessage(content=p_persona)]
                    for m in history[-12:]:
                        if m["role"] == "user":
                            p_lc_msgs.append(HumanMessage(content=m["content"]))
                        elif m["role"] == "assistant":
                            sender = m.get("character_name", "Assistant")
                            if sender == char_name:
                                p_lc_msgs.append(AIMessage(content=m["content"]))
                            else:
                                p_lc_msgs.append(HumanMessage(content=f"({sender} said): {m['content']}"))

                    char_p_model_override = p_char.get("model", "default")
                    char_llm = get_llm(prefs, streaming=True, model_override=char_p_model_override)

                    target_llm = char_llm.bind_tools(tool_defs) if tool_defs else char_llm

                    f_content = ""
                    collected_chunks = []
                    skipped = False
                    char_tool_calls_log = []

                    async for chunk in target_llm.astream(p_lc_msgs):
                        collected_chunks.append(chunk)

                        if hasattr(chunk, "reasoning_content") and chunk.reasoning_content:
                            yield f"data: {json.dumps({'type': 'reasoning', 'delta': chunk.reasoning_content, 'character_id': pid})}\n\n"
                        elif chunk.additional_kwargs and "reasoning_content" in chunk.additional_kwargs:
                            yield f"data: {json.dumps({'type': 'reasoning', 'delta': chunk.additional_kwargs['reasoning_content'], 'character_id': pid})}\n\n"

                        if chunk.content:
                            if not f_content and "[SKIP]" in chunk.content.upper():
                                skipped = True
                                break
                            f_content += chunk.content
                            yield f"data: {json.dumps({'type': 'content', 'delta': chunk.content, 'character_id': pid})}\n\n"

                    if skipped:
                        continue

                    from functools import reduce
                    from operator import add

                    full_response = reduce(add, collected_chunks) if collected_chunks else None

                    if full_response and hasattr(full_response, "tool_calls") and full_response.tool_calls and tool_defs:
                        curr_resp = full_response
                        for _ in range(3):
                            if not curr_resp.tool_calls:
                                break
                            p_lc_msgs.append(curr_resp)
                            for tc in curr_resp.tool_calls:
                                tname = tc["name"]
                                targs = tc["args"]
                                tid = tc.get("id", str(uuid.uuid4())[:8])
                                yield f"data: {json.dumps({'type': 'tool_start', 'name': tname, 'character_id': pid})}\n\n"
                                sess = tool_sessions.get(tname)
                                print(f"  [DEBUG] Calling stream MCP Tool: '{tname}' with args: {targs}")
                                try:
                                    tr = await sess.call_tool(tname, arguments=targs) if sess else "Error: Tool not found"
                                except Exception as e:
                                    print(f"  [DEBUG] Tool '{tname}' execution failed: {e}")
                                    tr = f"Error: {e}"
                                txt = "".join([
                                    getattr(b, "text", str(b))
                                    for b in (tr.content if hasattr(tr, "content") else [])
                                ]) or str(tr)
                                yield f"data: {json.dumps({'type': 'tool_end', 'name': tname, 'result': txt, 'character_id': pid})}\n\n"
                                p_lc_msgs.append(ToolMessage(content=txt, tool_call_id=tid))
                                char_tool_calls_log.append({"name": tname, "args": targs, "result": txt})

                            fcl = ""
                            inner_chunks = []
                            async for c in target_llm.astream(p_lc_msgs):
                                inner_chunks.append(c)
                                if hasattr(c, "reasoning_content") and c.reasoning_content:
                                    yield f"data: {json.dumps({'type': 'reasoning', 'delta': c.reasoning_content, 'character_id': pid})}\n\n"
                                elif c.additional_kwargs and "reasoning_content" in c.additional_kwargs:
                                    yield f"data: {json.dumps({'type': 'reasoning', 'delta': c.additional_kwargs['reasoning_content'], 'character_id': pid})}\n\n"
                                if c.content:
                                    fcl += c.content
                                    yield f"data: {json.dumps({'type': 'content', 'delta': c.content, 'character_id': pid})}\n\n"
                            f_content += fcl

                            new_resp = reduce(add, inner_chunks) if inner_chunks else None
                            if new_resp and hasattr(new_resp, "tool_calls") and new_resp.tool_calls:
                                curr_resp = new_resp
                            else:
                                break

                    frw, thk = parse_thinking(f_content)

                    cleaned = frw.strip()
                    for prefix_pattern in [f"[{char_name}]:", f"{char_name}:", f"[{char_name}] "]:
                        while cleaned.startswith(prefix_pattern):
                            cleaned = cleaned[len(prefix_pattern):].strip()

                    history.append({
                        "role": "assistant",
                        "character_id": pid,
                        "character_name": char_name,
                        "content": cleaned,
                        "thinking": thk,
                        "tool_calls": char_tool_calls_log if char_tool_calls_log else None,
                        "model": active_model,
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                    })

                title = None
                if is_new:
                    title_content = history[-1]["content"] if len(history) > 1 else "New Chat"
                    title = await generate_title(req.message, title_content)
                    convos[conv_id] = {
                        "title": title,
                        "character_id": char_id,
                        "messages": history,
                        "updated_at": now,
                        "participants": pids,
                    }
                else:
                    convos[conv_id].update({"messages": history, "updated_at": now, "participants": pids})

                save_convos(convos)
                yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id, 'title': title})}\n\n"
                yield "data: [DONE]\n\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════════════════════
# PAGES
# ═══════════════════════════════════════════════════════════════════

@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.get("/settings")
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {"request": request})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)