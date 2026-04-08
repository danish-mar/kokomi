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
    if not prefs:
        prefs = {
            "model_name": "qwen-2.5-32b",
            "user_persona": "",
            "theme": "dark",
            "dynamic_suggestions": True,
            "streaming_mode": True
        }
        _save(USER_PREFS_FILE, prefs)
    
    # Ensure all keys exist
    changed = False
    if "dynamic_suggestions" not in prefs:
        prefs["dynamic_suggestions"] = True
        changed = True
    if "streaming_mode" not in prefs:
        prefs["streaming_mode"] = True
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
        # Default Kokomi
        chars["kokomi"] = {
            "id": "kokomi",
            "name": "Kokomi",
            "persona": "You are Kokomi, the Divine Priestess of Watatsumi Island. You are a brilliant strategist and a gentle, thoughtful leader. You use markdown for formatting. Speak with grace and wisdom.",
            "avatar": None,
            "mcp_servers": [],
            "created_at": datetime.datetime.utcnow().isoformat(),
        }
        # Bonus: Nahida template
        chars["nahida"] = {
            "id": "nahida",
            "name": "Nahida",
            "persona": "You are Nahida, the Lesser Lord Kusanali and the Avatar of Irminsul. You are wise, curious, and speak in beautiful metaphors. You are deeply knowledgeable about the world and treat everyone with kindness and a sense of wonder. Use markdown for formatting.",
            "avatar": None,
            "mcp_servers": [],
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


# ═══════════════════════════════════════════════════════════════════
# LLM
# ═══════════════════════════════════════════════════════════════════

groq_key = os.getenv("GROQ_API_KEY")

# Global LLM instance is removed in favor of dynamic selection per request
title_llm = ChatGroq(
    model_name="meta-llama/llama-4-scout-17b-16e-instruct",
    temperature=0.3,
    groq_api_key=groq_key,
) if groq_key else None


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════


def parse_thinking(raw: str):
    m = re.search(r"<think>(.*?)</think>", raw, re.DOTALL)
    if m:
        return raw[: m.start()].strip() + raw[m.end() :].strip(), m.group(1).strip()
    return raw.strip(), None


async def generate_title(user_msg: str, ai_msg: str) -> str:
    try:
        prompt = f"Generate a short title (max 4 words, no quotes) for:\nUser: {user_msg[:150]}\nAssistant: {ai_msg[:150]}\n\nTitle:"
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
        return [], {}

    servers = load_mcp()
    tool_defs = []
    tool_sessions = {}

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
                # streamable_http_client returns (read, write, session_id_callback)
                streams = await stack.enter_async_context(streamable_http_client(url))
                read, write = streams[0], streams[1]
            else:
                continue

            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            result = await session.list_tools()
            for tool in result.tools:
                tool_defs.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": tool.inputSchema
                            if tool.inputSchema
                            else {"type": "object", "properties": {}},
                        },
                    }
                )
                tool_sessions[tool.name] = session

            print(f"  ✅ MCP '{config['name']}': {len(result.tools)} tools")

        except Exception as e:
            print(f"  ❌ MCP '{config.get('name', sid)}' failed: {e}")

    return tool_defs, tool_sessions


# ═══════════════════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════════════════


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    character_id: Optional[str] = "kokomi"


class MCPServerCreate(BaseModel):
    name: str
    transport: str = "stdio"
    command: Optional[str] = None
    args: Optional[List[str]] = []
    env: Optional[dict] = {}
    url: Optional[str] = None
    enabled: bool = True


class PrefsUpdate(BaseModel):
    model_name: str
    user_persona: str
    dynamic_suggestions: bool = True
    streaming_mode: bool = True


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
    # Curated Groq list + fetch if possible
    curated = [
        {"id": "qwen-2.5-32b", "name": "Qwen 2.5 32B"},
        {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B"},
        {"id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B"},
        {"id": "mixtral-8x7b-32768", "name": "Mixtral 8x7B"},
        {"id": "deepseek-r1-distill-llama-70b", "name": "DeepSeek R1 70B (Llama)"},
    ]
    try:
        import httpx
        api_key = os.getenv("GROQ_API_KEY")
        headers = {"Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                # Merge or replace? Let's return the real list but keep curated for better naming if match
                real_ids = [m["id"] for m in models]
                # Filter useful ones
                filtered = [m for m in models if "whisper" not in m["id"] and "vision" not in m["id"]]
                return filtered
    except:
        pass
    return curated


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
            tool_defs, _ = await connect_mcp_servers(stack, [sid])
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
        llm = ChatGroq(model_name="llama-3.3-70b-versatile", temperature=0.7, groq_api_key=groq_key)
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        # Extract JSON from response
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
        result.append(
            {
                "_id": cid,
                "title": c.get("title", "Untitled"),
                "character_id": c.get("character_id", "kokomi"),
                "updated_at": c.get("updated_at", ""),
            }
        )
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


# ═══════════════════════════════════════════════════════════════════
# CHAT
# ═══════════════════════════════════════════════════════════════════


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not groq_key:
        raise HTTPException(500, "GROQ_API_KEY not set")

    prefs = load_prefs()
    active_model = prefs.get("model_name", "qwen-2.5-32b")
    user_p = prefs.get("user_persona", "")

    # Create dynamic LLM for this request
    current_llm = ChatGroq(model_name=active_model, temperature=0.7, groq_api_key=groq_key)

    chars = load_chars()
    char_id = req.character_id or "kokomi"
    char = chars.get(char_id, chars.get("kokomi"))

    convos = load_convos()
    conv_id = req.conversation_id
    is_new = conv_id is None or conv_id not in convos
    history = [] if is_new else convos[conv_id].get("messages", [])

    now = datetime.datetime.utcnow().isoformat()
    history.append({"role": "user", "content": req.message, "timestamp": now})

    persona = char.get("persona", "You are a helpful AI assistant.")
    if user_p:
        persona += f"\n\nInformation about the user (User Persona):\n{user_p}"
    
    persona += "\n\nCRITICAL: Always wrap your internal reasoning/thought process inside <think> and </think> tags before providing your final response."
    lc_msgs = [SystemMessage(content=persona)]
    for m in history[-12:]:
        if m["role"] == "user":
            lc_msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            # Combine content and thinking for context
            comb = m["content"]
            if m.get("thinking"):
                comb = f"<think>{m['thinking']}</think>\n\n{comb}"
            lc_msgs.append(AIMessage(content=comb))

    mcp_server_ids = char.get("mcp_servers", [])
    tool_calls_log = []
    all_thinking = []

    try:
        async with AsyncExitStack() as stack:
            tool_defs, tool_sessions = await connect_mcp_servers(stack, mcp_server_ids)

            if tool_defs:
                llm_with_tools = current_llm.bind_tools(tool_defs)
                response = await llm_with_tools.ainvoke(lc_msgs)
                
                # Check for thinking in first response
                final_content, t = parse_thinking(response.content)
                if t: all_thinking.append(t)

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
                                result = await session.call_tool(tool_name, arguments=tool_args)
                                res_txt = "".join(
                                    [getattr(b, "text", str(b)) for b in result.content]
                                )
                            else:
                                res_txt = f"Error: '{tool_name}' not found"
                        except Exception as e:
                            res_txt = f"Error: {e}"

                        tool_calls_log.append({"name": tool_name, "args": tool_args, "result": res_txt})
                        lc_msgs.append(ToolMessage(content=res_txt, tool_call_id=tool_call_id))
                    
                    response = await llm_with_tools.ainvoke(lc_msgs)
                    final_content, t = parse_thinking(response.content)
                    if t: all_thinking.append(t)
                
                raw_content = final_content
            else:
                response = await current_llm.ainvoke(lc_msgs)
                raw_content, t = parse_thinking(response.content)
                if t: all_thinking.append(t)
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
        "model": active_model
    }


@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.get("/settings")
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {"request": request})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    if not groq_key:
        raise HTTPException(500, "GROQ_API_KEY not set")

    prefs = load_prefs()
    active_model = prefs.get("model_name", "qwen-2.5-32b")
    user_p = prefs.get("user_persona", "")
    current_llm = ChatGroq(model_name=active_model, temperature=0.7, groq_api_key=groq_key, streaming=True)

    chars = load_chars()
    char_id = req.character_id or "kokomi"
    char = chars.get(char_id, chars.get("kokomi"))

    convos = load_convos()
    conv_id = req.conversation_id
    is_new = conv_id is None or conv_id not in convos
    
    # Generate ID early for streaming
    if is_new:
        conv_id = str(uuid.uuid4())[:12]

    history = [] if (not conv_id or conv_id not in convos) else convos[conv_id].get("messages", [])
    now = datetime.datetime.utcnow().isoformat()
    
    user_msg = {"role": "user", "content": req.message, "timestamp": now}
    history.append(user_msg)

    persona = char.get("persona", "You are a helpful AI assistant.")
    if user_p:
        persona += f"\n\nInformation about the user (User Persona):\n{user_p}"
    persona += "\n\nCRITICAL: Always wrap your internal reasoning/thought process inside <think> and </think> tags before providing your final response."
    
    lc_msgs = [SystemMessage(content=persona)]
    for m in history[-12:]:
        if m["role"] == "user":
            lc_msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            # Combine content and thinking for context
            comb = m["content"]
            if m.get("thinking"):
                comb = f"<think>{m['thinking']}</think>\n\n{comb}"
            lc_msgs.append(AIMessage(content=comb))

    mcp_server_ids = char.get("mcp_servers", [])

    async def event_generator():
        nonlocal history
        full_content = ""
        tool_calls_log = []
        
        try:
            yield f"data: {json.dumps({'type': 'start'})}\n\n"
            
            async with AsyncExitStack() as stack:
                tool_defs, tool_sessions = await connect_mcp_servers(stack, mcp_server_ids)
                
                target_llm = current_llm
                if tool_defs:
                    target_llm = current_llm.bind_tools(tool_defs)
                
                # Stream the first response token-by-token
                collected_chunks = []
                async for chunk in target_llm.astream(lc_msgs):
                    collected_chunks.append(chunk)
                    if chunk.content:
                        full_content += chunk.content
                        yield f"data: {json.dumps({'type': 'content', 'delta': chunk.content})}\n\n"
                
                # Check if the streamed response included tool calls
                # Merge all chunks to get the full AIMessage
                from functools import reduce
                from operator import add
                if collected_chunks:
                    full_response = reduce(add, collected_chunks)
                else:
                    full_response = None
                
                # Handle tool calls if present
                if full_response and hasattr(full_response, 'tool_calls') and full_response.tool_calls and tool_defs:
                    rounds = 0
                    response = full_response
                    while response.tool_calls and rounds < 5:
                        rounds += 1
                        lc_msgs.append(response)
                        for tc in response.tool_calls:
                            tool_name = tc["name"]
                            tool_args = tc["args"]
                            tool_call_id = tc.get("id", str(uuid.uuid4())[:8])
                            
                            yield f"data: {json.dumps({'type': 'tool_start', 'name': tool_name})}\n\n"
                            
                            try:
                                session = tool_sessions.get(tool_name)
                                res_txt = ""
                                if session:
                                    result = await session.call_tool(tool_name, arguments=tool_args)
                                    res_txt = "".join([getattr(b, "text", str(b)) for b in result.content])
                                else:
                                    res_txt = f"Error: Tool {tool_name} not found"
                            except Exception as e:
                                res_txt = f"Error: {e}"
                            
                            tool_calls_log.append({"name": tool_name, "args": tool_args, "result": res_txt})
                            lc_msgs.append(ToolMessage(content=res_txt, tool_call_id=tool_call_id))
                            yield f"data: {json.dumps({'type': 'tool_end', 'name': tool_name, 'result': res_txt})}\n\n"
                        
                        # Stream the follow-up response after tool results
                        full_content = ""  # Reset for the new response
                        collected_chunks = []
                        async for chunk in target_llm.astream(lc_msgs):
                            collected_chunks.append(chunk)
                            if chunk.content:
                                full_content += chunk.content
                                yield f"data: {json.dumps({'type': 'content', 'delta': chunk.content})}\n\n"
                        
                        if collected_chunks:
                            response = reduce(add, collected_chunks)
                        else:
                            break

            final_raw, thinking = parse_thinking(full_content)
            
            history.append({
                "role": "assistant",
                "content": final_raw.strip(),
                "thinking": thinking,
                "tool_calls": tool_calls_log if tool_calls_log else None,
                "model": active_model,
                "timestamp": datetime.datetime.utcnow().isoformat(),
            })

            title = None
            if is_new:
                title = await generate_title(req.message, final_raw)
                convos[conv_id] = {"title": title, "character_id": char_id, "messages": history, "updated_at": now}
            else:
                convos[conv_id].update({"messages": history, "updated_at": now})
            
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
            "X-Accel-Buffering": "no"
        }
    )
