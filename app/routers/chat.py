import asyncio
import datetime
import json
import uuid
import time
import re
import base64
from functools import reduce
from operator import add

from app.mcp import MCP_TOOL_CALL_TIMEOUT

from fastapi import APIRouter, HTTPException, File, UploadFile
import shutil
import os

router = APIRouter(prefix="/api")

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to the data/uploads directory."""
    try:
        file_id = f"{uuid.uuid4()}_{file.filename}"
        file_path = os.path.join("data/uploads", file_id)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        return {
            "id": file_id,
            "filename": file.filename,
            "size": os.path.getsize(file_path),
            "content_type": file.content_type,
            "url": f"/uploads/{file_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from pypdf import PdfReader
from app.workflow import is_complex_workflow, MultiAgentWorkflowEngine, load_workflows

from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import tool

from app.config import GROQ_API_KEY
from app.llm import get_llm, generate_title, parse_thinking, _normalize_model, resolve_character_model
from app.mcp import get_pool_tools, init_pool, pool_is_stale
from app.models import ChatRequest
from app.storage import load_prefs, load_chars, load_convos, save_convos
from app.insights import log_generation
from app.memory import save_memory, search_memories, summarize_conversation
from app.tools.memory_tool import get_memory_tool


@tool
def open_url(url: str) -> str:
    """Open a specified URL or URI scheme in a new browser tab or trigger a native app action.
    This supports standard web links (http/https) and native URI schemes:
    - tel:+91XXXXXXXXXX (Phone dialer)
    - mailto:you@gmail.com (Email client)
    - sms:+91XXXXXXXXXX (SMS app)
    - whatsapp://send?phone=91XXXXXXXXXX (WhatsApp)
    - youtube://watch?v=ID (YouTube app)
    - maps:?q=Location (Maps app)
    Use this immediately when the user asks to "call", "mail", "sms", "play", or "open" something.
    """
    return f"Successfully triggered opening of {url}"


def _get_tavily_tool(prefs: dict):
    """Build a search tool (Tavily or SearxNG) from prefs, or None if not configured."""
    try:
        if not prefs.get("web_search_enabled"):
            return None
            
        provider = prefs.get("search_provider") or "tavily"
        
        if provider == "searxng":
            from langchain_core.tools import tool
            import httpx
            import json
            
            searxng_url = prefs.get("searxng_url") or "http://localhost:8080"
            searxng_url = searxng_url.rstrip("/")
            
            @tool("web_search")
            def searxng_search_tool(query: str, max_results: int = 5) -> str:
                """Search the web for up-to-date facts on a specific query, specifying the number of results desired (max_results)."""
                try:
                    resp = httpx.get(
                        f"{searxng_url}/",
                        params={"q": query, "format": "json"},
                        timeout=10.0
                    )
                    if resp.status_code != 200:
                        resp = httpx.get(
                            f"{searxng_url}/search",
                            params={"q": query, "format": "json"},
                            timeout=10.0
                        )
                    if resp.status_code == 200:
                        data = resp.json()
                        raw_results = data.get("results", [])
                        formatted_results = []
                        for r in raw_results[:max_results]:
                            formatted_results.append({
                                "title": r.get("title") or "",
                                "url": r.get("url") or "",
                                "content": r.get("content") or r.get("snippet") or ""
                            })
                        return json.dumps(formatted_results)
                    else:
                        return f"SearxNG query failed with status code {resp.status_code}."
                except Exception as e:
                    return f"SearxNG query failed: {str(e)}."
            
            return searxng_search_tool
            
        from langchain_core.tools import tool
        from langchain_community.tools.tavily_search import TavilySearchResults
        import json
        
        api_key = prefs.get("tavily_api_key") or ""
        if not api_key:
            return None
        import os
        os.environ["TAVILY_API_KEY"] = api_key
        
        @tool("web_search")
        def tavily_search_tool(query: str, max_results: int = 5) -> str:
            """Search the web for up-to-date facts on a specific query, specifying the number of results desired (max_results)."""
            try:
                tavily = TavilySearchResults(max_results=max_results, tavily_api_key=api_key)
                res = tavily.invoke(query)
                return json.dumps(res)
            except Exception as e:
                return f"Tavily search failed: {str(e)}"
                
        return tavily_search_tool
    except Exception:
        return None

def _get_scrape_tool(prefs: dict):
    """Build a scrape_page tool from prefs, or None if not configured."""
    try:
        if not prefs.get("web_scrape_enabled"):
            return None
            
        from langchain_core.tools import tool
        import httpx
        from html.parser import HTMLParser
        
        @tool("scrape_page")
        def scrape_page_tool(url: str) -> str:
            """Scrape a webpage and return clean text content, stripped of JavaScript, CSS, and HTML tags."""
            class TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text_parts = []
                    self.in_ignored_tag = False
                    self.ignored_tags = {"script", "style", "head", "meta", "link", "noscript", "svg"}

                def handle_starttag(self, tag, attrs):
                    if tag in self.ignored_tags:
                        self.in_ignored_tag = True

                def handle_endtag(self, tag):
                    if tag in self.ignored_tags:
                        self.in_ignored_tag = False

                def handle_data(self, data):
                    if not self.in_ignored_tag:
                        clean_data = data.strip()
                        if clean_data:
                            self.text_parts.append(clean_data)

                def get_text(self):
                    return "\n".join(self.text_parts)

            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=15.0)
                if resp.status_code == 200:
                    parser = TextExtractor()
                    parser.feed(resp.text)
                    text = parser.get_text()
                    if len(text) > 12000:
                        text = text[:12000] + "\n\n[Content truncated due to length limits...]"
                    return text if text.strip() else "Webpage returned no extractable text content."
                else:
                    return f"Failed to retrieve page content. Status code: {resp.status_code}"
            except Exception as e:
                return f"Error occurred while scraping the page: {str(e)}"
                
        return scrape_page_tool
    except Exception:
        return None

async def _ensure_pool():
    """Lazily initialize the MCP pool if it's stale or not ready."""
    if pool_is_stale():
        await init_pool()


# ── Non-streaming chat ───────────────────────────────────────────────

@router.post("/chat")
async def chat(req: ChatRequest):
    if is_complex_workflow(req.message):
        run_id = await MultiAgentWorkflowEngine.create_run(req.message)
        asyncio.create_task(MultiAgentWorkflowEngine.execute_run(run_id))
        
        reply = (
            "🌊 **Atlas Intelligence Supervisor Activated**\n\n"
            "I have dynamically routed your complex outcome-based request into our **Multi-Agent Execution Graph**.\n\n"
            f"*   **Run ID**: `{run_id}`\n"
            "*   **Mode**: Parallel Task Execution\n"
            "*   **Engine**: LangGraph & LangChain Supervisor\n\n"
            "You can track real-time task pipelines, logs, downloads, and output statuses directly on the **[Atlas Terminal](/atlas)**."
        )
        
        convos = load_convos()
        conv_id = req.conversation_id or str(uuid.uuid4())[:12]
        if conv_id not in convos:
            convos[conv_id] = {
                "id": conv_id,
                "title": "Atlas Workflow: " + req.message[:20],
                "character_id": req.character_id or "kokomi",
                "updated_at": time.time(),
                "messages": []
            }
        
        now = datetime.datetime.utcnow().isoformat()
        convos[conv_id]["messages"].append({"role": "user", "content": req.message, "timestamp": now})
        convos[conv_id]["messages"].append({
            "role": "assistant", 
            "content": reply, 
            "timestamp": now,
            "metrics": {"tps": 0, "ttft": 0, "total_time": 0}
        })
        save_convos(convos)
        
        return {
            "conversation_id": conv_id,
            "reply": reply,
            "thinking": "Routing to LangGraph workflow..."
        }

    t0 = time.time()
    prefs = load_prefs()
    provider = prefs.get("llm_provider", "groq")

    if provider == "google":
        active_model = _normalize_model(prefs.get("model_name", "gemini-2.5-flash"))
    elif provider == "local":
        active_model = _normalize_model(prefs.get("local_model", "local-model"))
    elif provider == "nvidia":
        active_model = prefs.get("nvidia_model", "nvidia/llama-3.3-nemotron-super-49b-v1")
    else:  # groq
        active_model = _normalize_model(prefs.get("model_name", "llama-3.3-70b-versatile"))

    user_p = prefs.get("user_persona", "")
    chars = load_chars()
    char_id = req.character_id or "kokomi"
    char = chars.get(char_id, chars.get("kokomi"))

    # Resolve model for this character + current provider
    char_model = resolve_character_model(char, provider)
    current_llm = get_llm(prefs, model_override=char_model)

    # Track the actual model used for display
    if char_model and char_model != "default":
        active_model = _normalize_model(char_model)

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
    persona += (
        "\n\nCRITICAL: Always wrap your internal reasoning/thought process inside "
        "<think> and </think> tags before providing your final response."
    )

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
    tool_calls_log: list = []
    all_thinking: list = []
    builtin_tools = {}  # name -> callable LangChain tool

    try:
        # Get tools from the persistent pool (no per-request connections!)
        await _ensure_pool()
        tool_defs, tool_sessions, tool_icons, mcp_errors = get_pool_tools(mcp_server_ids if mcp_server_ids else None)

        if mcp_errors:
            persona += "\n\n⚠️ MCP Connection Warnings:\n" + "\n".join([f"- {e}" for e in mcp_errors])
            persona += "\n(You can inform the user if they ask about tools that are currently unavailable.)"

        if req.space_id:
            from app.rag import get_space_tool
            space_tool = get_space_tool(req.space_id)
            tool_defs.append(space_tool)
            builtin_tools[space_tool.name] = space_tool
            persona += (
                "\n\nYou have access to a Knowledge Space (RAG database). "
                "When the user asks about topics that could be in their uploaded documents, "
                "USE the search_knowledge_base tool FIRST to find relevant information before answering. "
                "IMPORTANT: Synthesize a concise, helpful answer from the retrieved excerpts. "
                "Do NOT dump raw content or list every excerpt — summarize and directly answer the question."
            )

        if req.use_web_search:
            tavily_tool = _get_tavily_tool(prefs)
            if tavily_tool:
                tool_defs.append(tavily_tool)
                builtin_tools[tavily_tool.name] = tavily_tool
                persona += (
                    "\n\nYou have access to a real-time web search tool called 'web_search'. "
                    "USE it to answer questions that require current information, news, facts, or anything you are unsure about."
                )

        scrape_tool = _get_scrape_tool(prefs)
        if scrape_tool:
            tool_defs.append(scrape_tool)
            builtin_tools[scrape_tool.name] = scrape_tool
            persona += (
                "\n\nYou have access to a web scraping tool called 'scrape_page'. "
                "USE it to extract clean text from any URL when you need to read page contents."
            )
        
        if prefs.get("browser_redirect_enabled", True):
            tool_defs.append(open_url)
            builtin_tools[open_url.name] = open_url
            persona += "\n\nYou have access to the 'open_url' tool. If the user asks to 'play' media or 'open' a site, you MUST use this tool to directly open the link for them. Do NOT just print the URL in your message."

        # Re-initialize SystemMessage with updated persona (including MCP/RAG context)
        lc_msgs[0] = SystemMessage(content=persona)

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
                        ui_status_text = tool_args.get("ui_status_text") if isinstance(tool_args, dict) else None
                        session = tool_sessions.get(tool_name)
                        bt = builtin_tools.get(tool_name)
                        if session:
                            actual_args = dict(tool_args)
                            actual_args.pop("ui_status_text", None)
                            print(f"  [DEBUG] Calling MCP Tool: '{tool_name}' with args: {actual_args}")
                            result = await asyncio.wait_for(
                                session.call_tool(tool_name, arguments=actual_args),
                                timeout=MCP_TOOL_CALL_TIMEOUT,
                            )
                            res_txt = "".join([getattr(b, "text", str(b)) for b in result.content])
                        elif bt:
                            print(f"  [DEBUG] Calling built-in Tool: '{tool_name}' with args: {tool_args}")
                            result_obj = await bt.ainvoke(tool_args)
                            if isinstance(result_obj, (dict, list)):
                                res_txt = json.dumps(result_obj, ensure_ascii=False)
                            else:
                                res_txt = str(result_obj)

                        else:
                            res_txt = f"Error: '{tool_name}' not found"
                    except Exception as e:
                        res_txt = f"Error: {e}"
                    tool_calls_log.append({
                        "name": tool_name, 
                        "args": tool_args, 
                        "result": res_txt, 
                        "icon": tool_icons.get(tool_name, "fa-wrench"),
                        "description": ui_status_text
                    })
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
        convos[conv_id] = {
            "title": title, 
            "character_id": char_id, 
            "messages": history, 
            "updated_at": now,
            "is_anonymous": req.is_anonymous
        }
    else:
        convos[conv_id].update({
            "messages": history, 
            "updated_at": now,
            "is_anonymous": req.is_anonymous
        })

    save_convos(convos)

    if prefs.get("debug_mode"):
        t1 = time.time()
        print(f"[DEBUG] /chat non-stream completed in {t1-t0:.2f}s for model {active_model}")

    # Telemetry
    if prefs.get("insights", True):
        # Try to extract token usage if available
        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = response.usage
        elif hasattr(response, "response_metadata"):
            usage = response.response_metadata.get("token_usage", {})
        
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("prompt_token_count")
        completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("candidates_token_count")
        total_tokens = usage.get("total_tokens") or usage.get("total_token_count")
        
        # Fallback: estimate from character count
        if not prompt_tokens and req.message:
            prompt_tokens = len(req.message) // 4
        if not completion_tokens and content:
            completion_tokens = len(content) // 4
        
        if not total_tokens and prompt_tokens and completion_tokens:
            total_tokens = prompt_tokens + completion_tokens
        
        gen_time = time.time() - t0
        tps = (completion_tokens / gen_time) if (completion_tokens and gen_time > 0) else None
        
        asyncio.create_task(log_generation({
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "model": active_model,
            "tps": tps,
            "ttft": None, # Not accurate for non-streaming
            "context_used": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "session_id": conv_id
        }))

    return {
        "conversation_id": conv_id,
        "response": content,
        "thinking": thinking_str,
        "tool_calls": tool_calls_log if tool_calls_log else None,
        "model": active_model,
    }


# ── Streaming chat ───────────────────────────────────────────────────

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    if is_complex_workflow(req.message):
        run_id = await MultiAgentWorkflowEngine.create_run(req.message)
        asyncio.create_task(MultiAgentWorkflowEngine.execute_run(run_id))
        
        reply = (
            "🌊 **Atlas Intelligence Supervisor Activated**\n\n"
            "I have dynamically routed your complex outcome-based request into our **Multi-Agent Execution Graph**.\n\n"
            f"*   **Run ID**: `{run_id}`\n"
            "*   **Mode**: Parallel Task Execution\n"
            "*   **Engine**: LangGraph & LangChain Supervisor\n\n"
            "You can track real-time task pipelines, logs, downloads, and output statuses directly on the **[Atlas Terminal](/atlas)**."
        )
        
        convos = load_convos()
        conv_id = req.conversation_id or str(uuid.uuid4())[:12]
        if conv_id not in convos:
            convos[conv_id] = {
                "id": conv_id,
                "title": "Atlas Workflow: " + req.message[:20],
                "character_id": req.character_id or "kokomi",
                "updated_at": time.time(),
                "messages": []
            }
        
        now = datetime.datetime.utcnow().isoformat()
        convos[conv_id]["messages"].append({"role": "user", "content": req.message, "timestamp": now})
        convos[conv_id]["messages"].append({
            "role": "assistant", 
            "content": reply, 
            "timestamp": now,
            "metrics": {"tps": 0, "ttft": 0, "total_time": 0}
        })
        save_convos(convos)

        async def workflow_stream_generator():
            yield f"data: {json.dumps({'type': 'start'})}\n\n"
            yield f"data: {json.dumps({'type': 'thinking', 'text': 'Routing to LangGraph...' })}\n\n"
            yield f"data: {json.dumps({'type': 'chunk', 'text': reply })}\n\n"
            yield f"data: {json.dumps({'type': 'metrics', 'tps': 0, 'ttft': 0, 'total_time': 0})}\n\n"
            yield "data: [DONE]\n\n"
            
        return StreamingResponse(
            workflow_stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    t0 = time.time()
    prefs = load_prefs()
    provider = prefs.get("llm_provider", "groq")

    if provider == "google":
        active_model = _normalize_model(prefs.get("model_name", "gemini-2.5-flash"))
    elif provider == "local":
        active_model = _normalize_model(prefs.get("local_model", "local-model"))
    elif provider == "nvidia":
        active_model = prefs.get("nvidia_model", "nvidia/llama-3.3-nemotron-super-49b-v1")
    else:  # groq
        active_model = _normalize_model(prefs.get("model_name", "qwen-2.5-32b"))

    user_p = prefs.get("user_persona", "")
    chars = load_chars()
    char_id = req.character_id or "kokomi"
    char = chars.get(char_id, chars.get("kokomi"))

    convos = load_convos()
    conv_id = req.conversation_id
    is_new = conv_id is None or conv_id not in convos

    if is_new:
        conv_id = str(uuid.uuid4())[:12]

    history = [] if (not conv_id or conv_id not in convos) else convos[conv_id].get("messages", [])
    now_iso = datetime.datetime.utcnow().isoformat()
    now = time.time()
    history.append({"role": "user", "content": req.message, "timestamp": now_iso})

    async def event_generator():
        nonlocal history
        queue = asyncio.Queue()

        async def process_chat():
            try:
                await queue.put(f"data: {json.dumps({'type': 'start'})}\n\n")

                pids = req.participants or [char_id]
                all_chars = load_chars()

                all_mcp_ids = list({
                    sid
                    for pid in pids
                    for sid in (all_chars.get(pid) or {}).get("mcp_servers", [])
                })

                # Get tools from the persistent pool — no connections needed!
                await _ensure_pool()
                tool_defs, tool_sessions, tool_icons, mcp_errors = get_pool_tools(all_mcp_ids if all_mcp_ids else None)
                builtin_tools = {}
                
                if req.space_id:
                    from app.rag import get_space_tool
                    space_tool = get_space_tool(req.space_id)
                    tool_defs.append(space_tool)
                    builtin_tools[space_tool.name] = space_tool

                if req.use_web_search:
                    tavily_tool = _get_tavily_tool(prefs)
                    if tavily_tool:
                        tool_defs.append(tavily_tool)
                        builtin_tools[tavily_tool.name] = tavily_tool

                scrape_tool = _get_scrape_tool(prefs)
                if scrape_tool:
                    tool_defs.append(scrape_tool)
                    builtin_tools[scrape_tool.name] = scrape_tool

                if prefs.get("browser_redirect_enabled", True):
                    tool_defs.append(open_url)
                    builtin_tools[open_url.name] = open_url

                for err in mcp_errors:
                    await queue.put(f"data: {json.dumps({'type': 'warning', 'message': err})}\n\n")
                
                mcp_warning_text = ""
                if mcp_errors:
                    mcp_warning_text = "\n\n⚠️ MCP Connection Warnings:\n" + "\n".join([f"- {e}" for e in mcp_errors])
                    mcp_warning_text += "\n(You can inform the user if they ask about tools that are currently unavailable.)"

                is_debug = prefs.get("debug_mode")
                is_debug = prefs.get("debug_mode")

                if is_debug:
                    msg = f"=== STARTING STREAM CHAT ===\nConversation: {conv_id}, Participants: {pids}"
                    print(f"\n[DEBUG] {msg}")
                    await queue.put(f"data: {json.dumps({'type': 'debug', 'message': msg})}\n\n")

                # --- Pre-retrieve Long Term Memory in Parallel ---
                memory_contexts = {}
                mem_tasks = []
                active_mem_pids = []

                if prefs.get("memory_enabled", True):
                    for pid in pids:
                        p_char = all_chars.get(pid)
                        if p_char and p_char.get("memory_enabled", True):
                            active_mem_pids.append(pid)
                            # Define an internal async wrapper to search and send signals
                            async def retrieve_mem(char_id):
                                try:
                                    # Signal start
                                    await queue.put(f"data: {json.dumps({'type': 'tool_start', 'character_id': char_id, 'name': 'memory_search', 'icon': 'fa-brain', 'description': 'Searching memory...'})}\n\n")
                                    
                                    # Perform the actual search (blocking call run in thread pool if needed, but search_memories is usually fast enough)
                                    # For now, we'll keep it simple as search_memories is I/O bound
                                    mems = search_memories(char_id, req.message)
                                    
                                    # Signal end
                                    res_text = f"Found {len(mems)} relevant past interactions" if mems else "No relevant memories found"
                                    await queue.put(f"data: {json.dumps({'type': 'tool_end', 'character_id': char_id, 'name': 'memory_search', 'result': res_text})}\n\n")
                                    
                                    return char_id, mems
                                except Exception as e:
                                    print(f"Parallel memory retrieval failed for {char_id}: {e}")
                                    return char_id, []

                            mem_tasks.append(retrieve_mem(pid))

                if mem_tasks:
                    results = await asyncio.gather(*mem_tasks)
                    for pid_res, mems in results:
                        if mems:
                            memory_contexts[pid_res] = "\n\n[Long-term Memory Context]:\n" + "\n".join([f"- {m}" for m in mems])

                for pid in pids:
                    p_char = all_chars.get(pid)
                    if not p_char:
                        continue

                    char_name = p_char.get("name", pid)
                    p_persona = p_char.get("persona", "")
                    
                    # Add pre-retrieved memory context
                    if pid in memory_contexts:
                        p_persona += memory_contexts[pid]

                    if user_p:
                        p_persona += f"\n\nUser Profile:\n{user_p}"

                    if len(pids) > 1:
                        other_names = [all_chars.get(x, {}).get("name", x) for x in pids if x != pid]
                        p_persona += (
                            f"\n\nGROUP CHAT: You are {char_name} in a group chat. "
                            f"Other participants: {', '.join(other_names)} and the user."
                            "\n\nSTRICT RULES:"
                            f"\n- You are ONLY {char_name}. NEVER write dialogue or responses for "
                            f"{', '.join(other_names)} or any other character."
                            "\n- Do NOT prefix your response with your own name (e.g. no 'Kokomi:' at the start)."
                            "\n- Respond naturally as yourself. Other characters will get their own turn."
                            "\n- If the last message is not directed at you and you have nothing to add, "
                            "respond with exactly: [SKIP]"
                        )

                    if prefs.get("inject_time"):
                        p_persona += f"\n\nCurrent System Date and Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

                    if req.space_id:
                        p_persona += (
                            "\n\nYou have access to a Knowledge Space (RAG database). "
                            "When the user asks about topics that could be in their uploaded documents, "
                            "USE the search_knowledge_base tool FIRST to find relevant information before answering. "
                            "IMPORTANT: Synthesize a concise, helpful answer from the retrieved excerpts. "
                            "Do NOT dump raw content or list every excerpt — summarize and directly answer the question."
                        )

                    if req.use_web_search and "web_search" in builtin_tools:
                        p_persona += (
                            "\n\nYou have access to a real-time web search tool called 'web_search'. "
                            "USE it to answer questions that require current information, news, facts, or anything you are unsure about."
                        )

                    if "scrape_page" in builtin_tools:
                        p_persona += (
                            "\n\nYou have access to a web scraping tool called 'scrape_page'. "
                            "USE it to extract clean text from any URL when you need to read page contents."
                        )

                    if prefs.get("browser_redirect_enabled", True) and "open_url" in builtin_tools:
                        p_persona += (
                            "\n\nYou have access to the 'open_url' tool, which acts as a universal launcher. "
                            "You MUST use it whenever the user asks for actions involving links or communication:"
                            "\n- Call/Dial: Use 'tel:+91XXXXXXXXXX'"
                            "\n- Email: Use 'mailto:email@address.com'"
                            "\n- SMS: Use 'sms:+91XXXXXXXXXX'"
                            "\n- WhatsApp: Use 'whatsapp://send?phone=XXXXXXXXXX'"
                            "\n- Play/Watch: Use 'youtube://watch?v=ID' or 'https://youtube.com/...'"
                            "\n- Navigation/Maps: Use 'maps:?q=LocationName'"
                            "\n- Open Site: Use the standard https URL."
                            "\nDo NOT just print the URL or number; use 'open_url' to trigger the action for the user."
                        )

                    p_persona += (
                        "\n\nIMPORTANT: Always wrap internal reasoning inside <think>...</think> tags before your response."
                    )
                    if mcp_warning_text:
                        p_persona += mcp_warning_text

                    # Artifacts Instruction (x4 Multiplier Rule)
                    # Artifacts Instruction (x10 Multiplier Rule)
                    if prefs.get("artifacts", True):
                        artifact_instr = (
                            "[ARTIFACTS ENABLED]\n"
                            "CRITICAL: You are in ARTIFACT MODE. For any standalone content (code, configs, scripts, long docs), "
                            "you MUST use <Artifact id=\"unique_id\" title=\"Title\" type=\"language\">...</Artifact> tags.\n"
                            "NEVER use markdown code blocks (```) for these files. "
                            "NEVER include the same content in the main response body and an artifact—only use the artifact.\n"
                            "The opening <Artifact> tag MUST be the very first thing you write for that file.\n"
                            "[/ARTIFACTS ENABLED]"
                        )
                        p_persona = (artifact_instr + "\n\n") * 15 + p_persona

                    # Process attachments for the prompt (Vision + Text)
                    text_parts = [req.message]
                    image_blocks = []
                    
                    if req.attachments:
                        for att in req.attachments:
                            filename = att.get("filename")
                            file_id = att.get("id")
                            file_path = os.path.join("data/uploads", file_id)
                            ext = filename.lower().split('.')[-1]
                            
                            try:
                                if os.path.exists(file_path):
                                    if ext in ["jpg", "jpeg", "png", "webp"]:
                                        # Vision Support: Base64 encode images
                                        with open(file_path, "rb") as img_f:
                                            b64_data = base64.b64encode(img_f.read()).decode("utf-8")
                                            mime = att.get("content_type", f"image/{ext}")
                                            image_blocks.append({
                                                "type": "image_url",
                                                "image_url": {"url": f"data:{mime};base64,{b64_data}"}
                                            })
                                    elif ext == "pdf":
                                        # Extract text from PDF
                                        reader = PdfReader(file_path)
                                        pdf_text = f"\n\n[Attached PDF: {filename}]\n"
                                        for page in reader.pages:
                                            pdf_text += page.extract_text() + "\n"
                                        text_parts.append(pdf_text[:15000])
                                    else:
                                        # Standard Text File
                                        with open(file_path, "r", errors="ignore") as f:
                                            content = f.read(10000)
                                            text_parts.append(f"\n\n[Attached File: {filename}]\n{content}")
                            except Exception as e:
                                text_parts.append(f"\n\n[Error reading {filename}: {e}]")
                    
                    # Construct Multimodal Human Message
                    human_content = [{"type": "text", "text": "\n".join(text_parts)}]
                    human_content.extend(image_blocks)

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
                    
                    # Add current multimodal message
                    p_lc_msgs.append(HumanMessage(content=human_content))


                    char_model = resolve_character_model(p_char, provider)
                    char_llm = get_llm(prefs, streaming=True, model_override=char_model)
                    
                    # Bind tools including memory tool if enabled
                    char_tool_defs = tool_defs.copy()
                    if p_char.get("memory_enabled", True) and prefs.get("memory_enabled", True):
                        char_tool_defs.append(get_memory_tool(pid))
                        
                    target_llm = char_llm.bind_tools(char_tool_defs) if char_tool_defs else char_llm

                    ts_start = time.time()
                    ts_first = None
                    ts_last_stats = 0

                    p_active_model = active_model
                    if char_model and char_model != "default":
                        p_active_model = _normalize_model(char_model)

                    f_content = ""
                    collected_chunks = []
                    skipped = False
                    char_tool_calls_log: list = []
                    char_artifacts_log: list = []

                    if is_debug:
                        msg = f"👉 Generating for: {char_name} (Model: {p_active_model})\nPrompt Length: {len(p_persona)} chars. Tools: {len(tool_defs) if tool_defs else 0}"
                        print(f"\n[DEBUG] {msg}")
                        await queue.put(f"data: {json.dumps({'type': 'debug', 'message': msg})}\n\n")
                        print(f"[DEBUG] Streaming chunks...")

                    art_active = False
                    art_id = None
                    art_meta = {}
                    art_content = ""
                    pending_buffer = ""

                    async for chunk in target_llm.astream(p_lc_msgs):
                        collected_chunks.append(chunk)

                        if hasattr(chunk, "reasoning_content") and chunk.reasoning_content:
                            if ts_first is None: ts_first = time.time()
                            await queue.put(f"data: {json.dumps({'type': 'reasoning', 'delta': chunk.reasoning_content, 'character_id': pid, 'model': p_active_model})}\n\n")
                        elif chunk.additional_kwargs and "reasoning_content" in chunk.additional_kwargs:
                            if ts_first is None: ts_first = time.time()
                            await queue.put(f"data: {json.dumps({'type': 'reasoning', 'delta': chunk.additional_kwargs['reasoning_content'], 'character_id': pid, 'model': p_active_model})}\n\n")

                        if chunk.content:
                            if ts_first is None: ts_first = time.time()
                            if not f_content and "[SKIP]" in chunk.content.upper():
                                skipped = True
                                break
                            f_content += chunk.content
                            if is_debug:
                                print(chunk.content, end="", flush=True)

                            # Periodic stats update
                            now_iso = datetime.datetime.utcnow().isoformat()
                            now = time.time()
                            if prefs.get("insights", True) and now - ts_last_stats > 0.4:
                                ts_last_stats = now
                                t_comp = len(f_content) // 4
                                t_tps = (t_comp / (now - ts_start)) if (now - ts_start > 0) else 0
                                await queue.put(f"data: {json.dumps({
                                    'type': 'stats',
                                    'tps': round(t_tps, 1),
                                    'ttft': round((ts_first - ts_start) * 1000) if ts_first else None,
                                    'context': (len(req.message) // 4) + t_comp
                                })}\n\n")

                            if not prefs.get("artifacts", True):
                                await queue.put(f"data: {json.dumps({'type': 'content', 'delta': chunk.content, 'character_id': pid, 'model': p_active_model})}\n\n")
                            else:
                                pending_buffer += chunk.content
                                while True:
                                    if not art_active:
                                        open_idx = pending_buffer.find("<Artifact")
                                        if open_idx != -1:
                                            pre_text = pending_buffer[:open_idx]
                                            if pre_text:
                                                await queue.put(f"data: {json.dumps({'type': 'content', 'delta': pre_text, 'character_id': pid, 'model': p_active_model})}\n\n")
                                            # Slice buffer here to avoid re-sending pre_text
                                            pending_buffer = pending_buffer[open_idx:]
                                            tag_end_idx = pending_buffer.find(">")
                                            if tag_end_idx != -1:
                                                tag_content = pending_buffer[:tag_end_idx+1]
                                                attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag_content))
                                                art_active = True
                                                art_id = attrs.get("id", str(uuid.uuid4())[:8])
                                                art_meta = attrs
                                                art_content = ""
                                                # Send the anchor placeholder to the frontend content so it renders inline during streaming
                                                await queue.put(f"data: {json.dumps({'type': 'content', 'delta': f'\n\n[[ARTIFACT:{art_id}]]\n\n', 'character_id': pid, 'model': p_active_model})}\n\n")
                                                await queue.put(f"data: {json.dumps({'type': 'artifact_open', 'id': art_id, 'metadata': art_meta, 'character_id': pid})}\n\n")
                                                pending_buffer = pending_buffer[tag_end_idx+1:]
                                                continue
                                            else:
                                                break
                                        else:
                                            send_limit = max(0, len(pending_buffer) - 10)
                                            to_send = pending_buffer[:send_limit]
                                            if to_send:
                                                await queue.put(f"data: {json.dumps({'type': 'content', 'delta': to_send, 'character_id': pid, 'model': p_active_model})}\n\n")
                                                pending_buffer = pending_buffer[send_limit:]
                                            break
                                    else:
                                        close_idx = pending_buffer.find("</Artifact>")
                                        if close_idx != -1:
                                            inside_text = pending_buffer[:close_idx]
                                            if inside_text:
                                                art_content += inside_text
                                                await queue.put(f"data: {json.dumps({'type': 'artifact_chunk', 'id': art_id, 'delta': inside_text})}\n\n")
                                            current_art = {**art_meta, "content": art_content, "timestamp": datetime.datetime.utcnow().isoformat()}
                                            char_artifacts_log.append(current_art)
                                            await queue.put(f"data: {json.dumps({'type': 'artifact_close', 'id': art_id, 'content': art_content})}\n\n")
                                            if prefs.get("insights", True):
                                                await queue.put(f"data: {json.dumps({'type': 'debug', 'message': f'Artifact generated: {art_id} ({len(art_content) // 4} tokens)'})}\n\n")
                                            art_active = False
                                            pending_buffer = pending_buffer[close_idx + len("</Artifact>"):]
                                            continue
                                        else:
                                            send_limit = max(0, len(pending_buffer) - 12)
                                            to_send = pending_buffer[:send_limit]
                                            if to_send:
                                                art_content += to_send
                                                await queue.put(f"data: {json.dumps({'type': 'artifact_chunk', 'id': art_id, 'delta': to_send})}\n\n")
                                                pending_buffer = pending_buffer[send_limit:]
                                            break

                    # FINAL FLUSH
                    if pending_buffer:
                        if art_active:
                             art_content += pending_buffer
                             await queue.put(f"data: {json.dumps({'type': 'artifact_chunk', 'id': art_id, 'delta': pending_buffer})}\n\n")
                             # Save to log even on flush
                             final_art = {**art_meta, "content": art_content, "timestamp": datetime.datetime.utcnow().isoformat()}
                             char_artifacts_log.append(final_art)
                             await queue.put(f"data: {json.dumps({'type': 'artifact_close', 'id': art_id, 'content': art_content})}\n\n")
                             art_active = False
                        else:
                             await queue.put(f"data: {json.dumps({'type': 'content', 'delta': pending_buffer, 'character_id': pid, 'model': p_active_model})}\n\n")
                        pending_buffer = ""

                    if is_debug:
                        print("\n[DEBUG] Stream generation finished.")

                    if skipped:
                        continue

                    full_response = reduce(add, collected_chunks) if collected_chunks else None

                    if full_response and getattr(full_response, "tool_calls", None) and tool_defs:
                        curr_resp = full_response
                        for _ in range(3):
                            if not curr_resp.tool_calls:
                                break
                            p_lc_msgs.append(curr_resp)
                            for tc in curr_resp.tool_calls:
                                tname = tc["name"]
                                targs = tc["args"]
                                tid = tc.get("id", str(uuid.uuid4())[:8])
                                ticon = tool_icons.get(tname, "fa-wrench")
                                ui_status = targs.get("ui_status_text") if isinstance(targs, dict) else None
                                await queue.put(f"data: {json.dumps({'type': 'tool_start', 'name': tname, 'icon': ticon, 'description': ui_status, 'character_id': pid})}\n\n")
                                sess = tool_sessions.get(tname)
                                bt = builtin_tools.get(tname)
                                if is_debug:
                                    msg = f"Calling Tool: '{tname}' with args: {targs}"
                                    print(f"  [DEBUG] {msg}")
                                    await queue.put(f"data: {json.dumps({'type': 'debug', 'message': msg})}\n\n")
                                try:
                                    if sess:
                                        actual_args = dict(targs)
                                        actual_args.pop("ui_status_text", None)
                                        tr = await asyncio.wait_for(
                                            sess.call_tool(tname, arguments=actual_args),
                                            timeout=MCP_TOOL_CALL_TIMEOUT,
                                        )
                                    elif bt:
                                        tr = await bt.ainvoke(targs)
                                    else:
                                        tr = "Error: Tool not found"
                                except asyncio.TimeoutError:
                                    print(f"  ⏱️ Tool '{tname}' timed out after {MCP_TOOL_CALL_TIMEOUT}s")
                                    tr = f"Error: Tool '{tname}' timed out after {MCP_TOOL_CALL_TIMEOUT}s"
                                except Exception as e:
                                    print(f"  [DEBUG] Tool '{tname}' execution failed: {e}")
                                    tr = f"Error: {e}"
                                if isinstance(tr, str):
                                    txt = tr
                                elif isinstance(tr, (dict, list)):
                                    txt = json.dumps(tr, ensure_ascii=False)
                                else:
                                    txt = "".join([
                                        getattr(b, "text", str(b))
                                        for b in (tr.content if hasattr(tr, "content") else [])
                                    ]) or str(tr)

                                if is_debug:
                                    msg = f"Tool Result Preview: {txt[:150]}..."
                                    print(f"  [DEBUG] {msg}")
                                    await queue.put(f"data: {json.dumps({'type': 'debug', 'message': msg})}\n\n")

                                await queue.put(f"data: {json.dumps({'type': 'tool_end', 'name': tname, 'result': txt, 'args': targs, 'icon': ticon, 'description': ui_status, 'character_id': pid})}\n\n")
                                p_lc_msgs.append(ToolMessage(content=txt, name=tname, tool_call_id=tid))
                                char_tool_calls_log.append({"name": tname, "args": targs, "result": txt, "icon": ticon, "description": ui_status})

                            fcl = ""
                            inner_chunks = []
                            async for c in target_llm.astream(p_lc_msgs):
                                inner_chunks.append(c)
                                if hasattr(c, "reasoning_content") and c.reasoning_content:
                                    await queue.put(f"data: {json.dumps({'type': 'reasoning', 'delta': c.reasoning_content, 'character_id': pid})}\n\n")
                                elif c.additional_kwargs and "reasoning_content" in c.additional_kwargs:
                                    await queue.put(f"data: {json.dumps({'type': 'reasoning', 'delta': c.additional_kwargs['reasoning_content'], 'character_id': pid})}\n\n")
                                if c.content:
                                    fcl += c.content
                                    if is_debug:
                                        print(c.content, end="", flush=True)
                                    
                                    # Periodic stats update
                                    now_iso = datetime.datetime.utcnow().isoformat()
                                    now = time.time()
                                    if now - ts_last_stats > 0.4:
                                        ts_last_stats = now
                                        temp_comp = len(f_content + fcl) // 4
                                        temp_tps = (temp_comp / (now - ts_start)) if (now - ts_start > 0) else 0
                                        await queue.put(f"data: {json.dumps({
                                            'type': 'stats',
                                            'tps': round(temp_tps, 1),
                                            'ttft': round((ts_first - ts_start)*1000) if ts_first else None,
                                            'context': (len(req.message) // 4) + temp_comp
                                        })}\n\n")

                                    # Artifact Parsing Logic (Inner Loop)
                                    if not prefs.get("artifacts", True):
                                        await queue.put(f"data: {json.dumps({'type': 'content', 'delta': c.content, 'character_id': pid, 'model': p_active_model})}\n\n")
                                    else:
                                        pending_buffer += c.content
                                        while True:
                                            if not art_active:
                                                open_idx = pending_buffer.find("<Artifact")
                                                if open_idx != -1:
                                                    pre_text = pending_buffer[:open_idx]
                                                    if pre_text:
                                                        await queue.put(f"data: {json.dumps({'type': 'content', 'delta': pre_text, 'character_id': pid, 'model': p_active_model})}\n\n")
                                                    tag_end_idx = pending_buffer.find(">", open_idx)
                                                    if tag_end_idx != -1:
                                                        tag_content = pending_buffer[open_idx:tag_end_idx+1]
                                                        attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag_content))
                                                        art_active = True
                                                        art_id = attrs.get("id", str(uuid.uuid4())[:8])
                                                        art_meta = attrs
                                                        art_content = ""
                                                        
                                                        await queue.put(f"data: {json.dumps({'type': 'artifact_open', 'id': art_id, 'metadata': art_meta, 'character_id': pid})}\n\n")
                                                        pending_buffer = pending_buffer[tag_end_idx+1:]
                                                        continue
                                                    else:
                                                        break
                                                else:
                                                    send_limit = max(0, len(pending_buffer) - 10)
                                                    to_send = pending_buffer[:send_limit]
                                                    if to_send:
                                                        await queue.put(f"data: {json.dumps({'type': 'content', 'delta': to_send, 'character_id': pid, 'model': p_active_model})}\n\n")
                                                        pending_buffer = pending_buffer[send_limit:]
                                                    break
                                            else:
                                                close_idx = pending_buffer.find("</Artifact>")
                                                if close_idx != -1:
                                                    inside_text = pending_buffer[:close_idx]
                                                    if inside_text:
                                                        art_content += inside_text
                                                        await queue.put(f"data: {json.dumps({'type': 'artifact_chunk', 'id': art_id, 'delta': inside_text})}\n\n")
                                                    current_art = {**art_meta, "content": art_content, "timestamp": datetime.datetime.utcnow().isoformat()}
                                                    char_artifacts_log.append(current_art)
                                                    await queue.put(f"data: {json.dumps({'type': 'artifact_close', 'id': art_id, 'content': art_content})}\n\n")
                                                    if prefs.get("insights", True):
                                                        v_str = art_meta.get("version", "1")
                                                        msg_dbg = f"Artifact generated: {art_id} v{v_str} ({len(art_content) // 4} tokens)"
                                                        await queue.put(f"data: {json.dumps({'type': 'debug', 'message': msg_dbg})}\n\n")
                                                    art_active = False
                                                    pending_buffer = pending_buffer[close_idx + len("</Artifact>"):]
                                                    continue
                                                else:
                                                    send_limit = max(0, len(pending_buffer) - 12)
                                                    to_send = pending_buffer[:send_limit]
                                                    if to_send:
                                                        art_content += to_send
                                                        await queue.put(f"data: {json.dumps({'type': 'artifact_chunk', 'id': art_id, 'delta': to_send})}\n\n")
                                                        pending_buffer = pending_buffer[send_limit:]
                                                    break

                            if is_debug:
                                print("\n[DEBUG] Inner chunk generation finished.")
                            
                            # Final flush of pending_buffer for this tool response cycle
                            if pending_buffer:
                                await queue.put(f"data: {json.dumps({'type': 'content', 'delta': pending_buffer, 'character_id': pid, 'model': p_active_model})}\n\n")
                                pending_buffer = ""

                            f_content += fcl
                            new_resp = reduce(add, inner_chunks) if inner_chunks else None
                            if new_resp and getattr(new_resp, "tool_calls", None):
                                curr_resp = new_resp
                            else:
                                break
                    
                    # FINAL FINAL flush
                    if pending_buffer:
                        if art_active:
                             art_content += pending_buffer
                             await queue.put(f"data: {json.dumps({'type': 'artifact_chunk', 'id': art_id, 'delta': pending_buffer})}\n\n")
                             await queue.put(f"data: {json.dumps({'type': 'artifact_close', 'id': art_id, 'content': art_content})}\n\n")
                        else:
                             await queue.put(f"data: {json.dumps({'type': 'content', 'delta': pending_buffer, 'character_id': pid, 'model': p_active_model})}\n\n")
                        pending_buffer = ""

                    frw, thk = parse_thinking(f_content)
                    cleaned = frw.strip()
                    # Strip Artifact blocks but leave a small anchor placeholder
                    def art_repl(m):
                        aid = re.search(r'id="([^"]*)"', m.group(0))
                        aid_str = aid.group(1) if aid else str(uuid.uuid4())[:8]
                        return f"\n\n[[ARTIFACT:{aid_str}]]\n\n"
                    
                    cleaned = re.sub(r'<Artifact.*?>.*?(</Artifact>|$)', art_repl, cleaned, flags=re.DOTALL).strip()
                    
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
                        "artifacts": char_artifacts_log if char_artifacts_log else None,
                        "model": p_active_model,
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                    })

                    # Telemetry for this generation
                    if prefs.get("insights", True):
                        usage = {}
                        if full_response:
                            if hasattr(full_response, "usage") and full_response.usage:
                                usage = full_response.usage
                            elif hasattr(full_response, "response_metadata"):
                                usage = full_response.response_metadata.get("token_usage", {})
                        
                        p_prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("prompt_token_count")
                        p_completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("candidates_token_count")
                        p_total_tokens = usage.get("total_tokens") or usage.get("total_token_count")
                        
                        # Fallback: estimate from character count
                        if not p_prompt_tokens and req.message:
                            p_prompt_tokens = len(req.message) // 4
                        if not p_completion_tokens and cleaned:
                            p_completion_tokens = len(cleaned) // 4
                        
                        if not p_total_tokens and p_prompt_tokens and p_completion_tokens:
                            p_total_tokens = p_prompt_tokens + p_completion_tokens
                        
                        p_gen_time = time.time() - ts_start
                        p_tps = (p_completion_tokens / p_gen_time) if (p_completion_tokens and p_gen_time > 0) else None
                        p_ttft = (ts_first - ts_start) * 1000 if (ts_first and ts_start) else None
                        
                        asyncio.create_task(log_generation({
                            "timestamp": datetime.datetime.utcnow().isoformat(),
                            "model": p_active_model,
                            "tps": p_tps,
                            "ttft": p_ttft,
                            "context_used": p_total_tokens,
                            "prompt_tokens": p_prompt_tokens,
                            "completion_tokens": p_completion_tokens,
                            "session_id": conv_id
                        }))

                        # --- Long Term Memory Summarization (Background) ---
                        if p_char.get("memory_enabled", True) and prefs.get("memory_enabled", True):
                            async def background_memory_task(msgs, char_id, p_copy):
                                facts = await summarize_conversation(msgs, p_copy)
                                for f in facts:
                                    save_memory(char_id, f)
                            
                            # Summarize the last few turns to extract new facts
                            asyncio.create_task(background_memory_task(history[-4:], pid, prefs))

                title = None
                if is_new:
                    title_content = history[-1]["content"] if len(history) > 1 else "New Chat"
                    title = await generate_title(req.message, title_content)
                    convos[conv_id] = {
                        "title": title,
                        "character_id": char_id,
                        "messages": history,
                        "updated_at": now_iso,
                        "participants": pids,
                        "is_anonymous": req.is_anonymous
                    }
                else:
                    convos[conv_id].update({
                        "messages": history, 
                        "updated_at": now_iso, 
                        "participants": pids,
                        "is_anonymous": req.is_anonymous
                    })

                save_convos(convos)

                await queue.put(f"data: {json.dumps({
                    'type': 'done', 
                    'conversation_id': conv_id, 
                    'title': title,
                    'metrics': {
                        'tps': round(p_tps, 1) if p_tps else None,
                        'ttft': round(p_ttft) if p_ttft else None,
                        'prompt_tokens': p_prompt_tokens,
                        'completion_tokens': p_completion_tokens,
                        'total_tokens': p_total_tokens,
                        'model': p_active_model,
                        'session_id': conv_id[:8]
                    }
                })}\n\n")
                await queue.put("data: [DONE]\n\n")

                if prefs.get("debug_mode"):
                    t1 = time.time()
                    print(f"[DEBUG] /chat/stream completed in {t1-t0:.2f}s for model {active_model}")

            except Exception as e:
                import traceback
                traceback.print_exc()
                
                err_msg = str(e)
                if hasattr(e, "exceptions"): 
                    msgs = [str(ex) for ex in e.exceptions]
                    err_msg = "Multiple errors: " + " | ".join(msgs)
                
                await queue.put(f"data: {json.dumps({'type': 'error', 'message': err_msg})}\n\n")
                await queue.put("data: [DONE]\n\n")
            finally:
                await queue.put(None) # Sentinel to stop generator

        task = asyncio.create_task(process_chat())

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        except asyncio.CancelledError:
            task.cancel()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Workflow API Endpoints ───────────────────────────────────────────

@router.get("/workflows")
async def get_workflows():
    """Retrieve all multi-agent LangGraph workflow execution runs."""
    return load_workflows()

@router.get("/workflows/{run_id}")
async def get_workflow_details(run_id: str):
    """Retrieve the high-fidelity state graph for a specific workflow run."""
    db = load_workflows()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return db[run_id]

@router.post("/workflows")
async def create_workflow_run(payload: dict):
    """Directly launch a new multi-agent LangGraph workflow execution run."""
    query = payload.get("message")
    if not query:
        raise HTTPException(status_code=400, detail="Query message is required")
    run_id = await MultiAgentWorkflowEngine.create_run(query)
    asyncio.create_task(MultiAgentWorkflowEngine.execute_run(run_id))
    return {"run_id": run_id, "status": "pending"}

@router.post("/workflows/{run_id}/chat")
async def chat_with_workflow_supervisor(run_id: str, payload: dict):
    """Send a collaborative instruction/message directly to the active workflow's supervisor."""
    from app.workflow import load_workflows, save_workflows, get_llm, load_prefs, MultiAgentWorkflowEngine
    from langchain_core.messages import SystemMessage, HumanMessage
    import json
    
    query = payload.get("message")
    if not query:
        raise HTTPException(status_code=400, detail="Query message is required")
        
    db = load_workflows()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow run not found")
        
    state = db[run_id]
    
    # Record user message
    state["notifications"].append(f"💬 User: {query}")
    state["debug_logs"].append(f"Received interactive instruction: {query}")
    
    # Load LLM
    prefs = load_prefs()
    llm = get_llm(prefs, streaming=False)
    
    # Context summary
    tasks_summary = []
    for t in state["tasks"]:
        out_summary = "None"
        if t.get("output"):
            out_summary = json.dumps(t["output"])[:300] + "..." if len(json.dumps(t["output"])) > 300 else json.dumps(t["output"])
        tasks_summary.append({
            "task_id": t["task_id"],
            "title": t["title"],
            "worker_type": t["worker_type"],
            "status": t["status"],
            "output_summary": out_summary
        })
        
    prompt = (
        "You are the Top-Level Workflow Supervisor. The user is collaborating with you on an active workflow.\n"
        f"Workflow Goal: {state['plan'].get('goal')}\n"
        f"Active Tasks State:\n{json.dumps(tasks_summary, indent=2)}\n\n"
        f"User's Instruction: {query}\n\n"
        "You have two options to respond:\n"
        "1. CONVERSATION: If the user is asking a question, asking for status, or requesting a simple clarification, answer them directly. "
        "Format your answer as a clear markdown explanation.\n"
        "2. APPEND_TASKS: If the user is requesting new actions, modifications, or additions that require specialized worker execution (e.g. searching, writing, exporting PDF, emailing, executing shell commands), define the new task nodes to be appended to the workflow.\n\n"
        "Respond ONLY with a valid JSON matching this schema:\n"
        "{\n"
        '  "response_type": "conversation" or "append_tasks",\n'
        '  "conversation_text": "Your markdown answer (if response_type is conversation)",\n'
        '  "new_tasks": [\n'
        "    {\n"
        '      "task_id": "t_new_1",\n'
        '      "title": "Task title",\n'
        '      "description": "Specific dynamic details for this worker",\n'
        '      "worker_type": "researcher" or "writer" or "pdf_worker" or "email_worker" or "code_worker",\n'
        '      "depends_on": [],  # Specify dependencies. You can depend on existing tasks like \"t1\", \"t2\", etc.\n'
        '      "allowed_tools": ["web_search"],\n'
        '      "success_criteria": "Criteria"\n'
        "    }\n"
        "  ]\n"
        "}"
    )
    
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw_text = response.content.strip()
        # Strip <think> tags from reasoning models if present
        if "<think>" in raw_text:
            import re as _re_think
            raw_text = _re_think.sub(r'<think>.*?</think>', '', raw_text, flags=_re_think.DOTALL).strip()
            
        clean_text = raw_text
        if "```json" in clean_text:
            clean_text = clean_text.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_text:
            clean_text = clean_text.split("```")[1].split("```")[0].strip()
            
        try:
            res = json.loads(clean_text)
        except Exception:
            # Fall back gracefully to conversation mode if parsing fails!
            res = {
                "response_type": "conversation",
                "conversation_text": raw_text
            }
        
        if res.get("response_type") == "conversation":
            text = res.get("conversation_text", "")
            state["notifications"].append(f"🤖 Supervisor: {text}")
            if state.get("final_result"):
                state["final_result"] = state["final_result"] + "\n\n---\n\n" + text
            else:
                state["final_result"] = text
            db[run_id] = state
            save_workflows(db)
            return {"status": "conversation", "response": text}
            
        elif res.get("response_type") == "append_tasks" and res.get("new_tasks"):
            new_tasks = res["new_tasks"]
            start_num = len(state["tasks"]) + 1
            for idx, nt in enumerate(new_tasks):
                nt["task_id"] = f"t{start_num + idx}"
                nt["status"] = "pending"
                nt["retries"] = 0
                nt["artifacts"] = []
                state["tasks"].append(nt)
                state["notifications"].append(f"➕ Supervisor added new task: '{nt['title']}' ({nt['worker_type']})")
            
            # Reset workflow status to pending to execute new tasks!
            state["status"] = "pending"
            db[run_id] = state
            save_workflows(db)
            
            asyncio.create_task(MultiAgentWorkflowEngine.execute_run(run_id))
            return {"status": "tasks_appended", "count": len(new_tasks)}
            
    except Exception as e:
        state["notifications"].append(f"⚠️ Supervisor error processing instruction: {str(e)}")
        db[run_id] = state
        save_workflows(db)
        raise HTTPException(status_code=500, detail=str(e))
        
    return {"status": "ignored"}

@router.delete("/workflows/{run_id}")
async def delete_workflow(run_id: str):
    """Delete a workflow run and its storage directory."""
    from app.workflow import load_workflows as _lw, save_workflows as _sw
    db = _lw()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Workflow not found")
    wf = db.pop(run_id)
    _sw(db)
    # Clean storage dir
    sdir = wf.get("storage_dir")
    if sdir and os.path.isdir(sdir):
        import shutil as _shutil
        _shutil.rmtree(sdir, ignore_errors=True)
    return {"status": "deleted"}

# ── Workflow File Explorer ───────────────────────────────────────────

# ── Workflow File Explorer ───────────────────────────────────────────

@router.get("/workflows/{run_id}/files")
async def list_workflow_files(run_id: str, path: str = ""):
    """List all files and subdirectories in a workflow's storage directory relative to path."""
    from app.config import DATA_DIR
    base_dir = os.path.join(DATA_DIR, "workflows", run_id)
    if not os.path.isdir(base_dir):
        os.makedirs(base_dir, exist_ok=True)
        
    # Resolve target directory cleanly
    target_dir = os.path.abspath(os.path.join(base_dir, path.strip("/")))
    if not target_dir.startswith(os.path.abspath(base_dir)):
        raise HTTPException(status_code=400, detail="Directory traversal detected")
        
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir, exist_ok=True)
        
    items = []
    for f in os.listdir(target_dir):
        fpath = os.path.join(target_dir, f)
        is_dir = os.path.isdir(fpath)
        items.append({
            "name": f,
            "is_dir": is_dir,
            "size": 0 if is_dir else os.path.getsize(fpath),
            "modified": datetime.datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat()
        })
    return items

@router.post("/workflows/{run_id}/upload")
async def upload_workflow_file(run_id: str, path: str = "", file: UploadFile = File(...)):
    """Upload a file to a workflow's storage directory or subdirectory."""
    from app.config import DATA_DIR
    base_dir = os.path.join(DATA_DIR, "workflows", run_id)
    target_dir = os.path.abspath(os.path.join(base_dir, path.strip("/")))
    if not target_dir.startswith(os.path.abspath(base_dir)):
        raise HTTPException(status_code=400, detail="Invalid path")
        
    os.makedirs(target_dir, exist_ok=True)
    fpath = os.path.join(target_dir, file.filename)
    with open(fpath, "wb") as buf:
        shutil.copyfileobj(file.file, buf)
    return {"name": file.filename, "size": os.path.getsize(fpath)}

@router.get("/workflows/{run_id}/download")
async def download_workflow_file(run_id: str, filepath: str):
    """Download a file from a workflow's storage directory or subdirectory."""
    from app.config import DATA_DIR
    from fastapi.responses import FileResponse
    base_dir = os.path.join(DATA_DIR, "workflows", run_id)
    fpath = os.path.abspath(os.path.join(base_dir, filepath.lstrip("/")))
    if not fpath.startswith(os.path.abspath(base_dir)) or not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(fpath, filename=os.path.basename(fpath))

@router.post("/workflows/{run_id}/mkdir")
async def make_workflow_dir(run_id: str, data: dict):
    """Create a new folder inside the workflow's storage folder."""
    from app.config import DATA_DIR
    path = data.get("path", "")
    folder_name = data.get("name", "").strip()
    if not folder_name:
        raise HTTPException(status_code=400, detail="Folder name is required")
        
    base_dir = os.path.join(DATA_DIR, "workflows", run_id)
    target_dir = os.path.abspath(os.path.join(base_dir, path.strip("/"), folder_name))
    if not target_dir.startswith(os.path.abspath(base_dir)):
        raise HTTPException(status_code=400, detail="Invalid path")
        
    os.makedirs(target_dir, exist_ok=True)
    return {"status": "success", "path": path}

@router.post("/workflows/{run_id}/touch")
async def touch_workflow_file(run_id: str, data: dict):
    """Create an empty file inside the workflow's storage folder."""
    from app.config import DATA_DIR
    path = data.get("path", "")
    filename = data.get("name", "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")
        
    base_dir = os.path.join(DATA_DIR, "workflows", run_id)
    target_file = os.path.abspath(os.path.join(base_dir, path.strip("/"), filename))
    if not target_file.startswith(os.path.abspath(base_dir)):
        raise HTTPException(status_code=400, detail="Invalid path")
        
    os.makedirs(os.path.dirname(target_file), exist_ok=True)
    with open(target_file, "w") as f:
        f.write("")
    return {"status": "success", "path": path}


# ── Agent Templates API Endpoints ────────────────────────────────────

@router.get("/workflow/tools")
async def get_available_tools():
    """Retrieve all available system tools dynamically from the backend."""
    tools = [
        {"id": "web_search", "name": "Web Search", "description": "Search the web using Tavily API for general public information."},
        {"id": "scrape_page", "name": "Scrape Page", "description": "Scrape raw text and table contents from web page URLs."},
        {"id": "fetch_url", "name": "Fetch URL", "description": "Read content directly from standard HTTP URLs."},
        {"id": "memory_search", "name": "Memory Search", "description": "Semantic search inside the active memory space vector databases."},
        {"id": "artifact_read", "name": "Artifact Read", "description": "Read content of existing file drafts in the working directories."},
        {"id": "file_write", "name": "File Write", "description": "Write final output contents or chapters to disk files."},
        {"id": "directory_create", "name": "Directory Create", "description": "Create isolated working folders for data compilation."},
        {"id": "pdf_export", "name": "PDF Export", "description": "Compile markdown contents into ReportLab-styled high-fidelity PDF booklets."},
        {"id": "docx_export", "name": "DOCX Export", "description": "Render structured markdown text into styled Word DOCX documents."},
        {"id": "pptx_export", "name": "PPTX Export", "description": "Compile markdowns into curated, minimalist PowerPoint presentation slides."},
        {"id": "excel_export", "name": "Excel Export", "description": "Convert tabular JSON or CSV dataset tables into styled Excel spreadsheets."},
        {"id": "send_email", "name": "Send Email", "description": "Send summary files, PDFs, or compiled doc suites to the user's inbox."},
        {"id": "browser_automation", "name": "Browser Automation", "description": "Execute browser automation tasks and navigation flows."},
        {"id": "shell_exec", "name": "Shell Exec", "description": "Execute sandboxed CLI calculations or python commands."}
    ]
    
    try:
        from app.mcp import get_pool_tools
        pool_tools = get_pool_tools()
        for t in pool_tools:
            tools.append({
                "id": t.name,
                "name": f"MCP: {t.name.replace('_', ' ').title()}",
                "description": t.description or "MCP-registered external service tool."
            })
    except Exception as e:
        print(f"[Dynamic Tools] Could not load MCP pool tools: {e}")
        
    return tools


@router.get("/workflow/templates")
async def get_agent_templates():
    """Retrieve all customized worker templates."""
    from app.workflow import load_templates
    return load_templates()

@router.post("/workflow/templates")
async def create_agent_template(payload: dict):
    """Add a new custom worker template dynamically."""
    from app.workflow import load_templates, save_templates
    name = payload.get("name")
    worker_id = payload.get("id")
    purpose = payload.get("purpose")
    system_prompt = payload.get("system_prompt_template")
    allowed_tools = payload.get("allowed_tools", [])
    
    if not name or not worker_id or not purpose or not system_prompt:
        raise HTTPException(status_code=400, detail="Missing required template properties")
        
    templates = load_templates()
    templates[worker_id] = {
        "name": name,
        "purpose": purpose,
        "allowed_tools": allowed_tools,
        "system_prompt_template": system_prompt,
        "expected_output_schema": payload.get("expected_output_schema", {"result": "string"}),
        "timeout": int(payload.get("timeout", 60)),
        "retry_limit": int(payload.get("retry_limit", 2))
    }
    save_templates(templates)
    return {"status": "success", "template_id": worker_id}

@router.delete("/workflow/templates/{template_id}")
async def delete_agent_template(template_id: str):
    """Delete a custom worker template."""
    from app.workflow import load_templates, save_templates
    templates = load_templates()
    if template_id not in templates:
        raise HTTPException(status_code=404, detail="Template not found")
    del templates[template_id]
    save_templates(templates)
    return {"status": "deleted"}

@router.post("/workflow/templates/generate")
async def ai_generate_template(payload: dict):
    """Use the active LLM to generate a worker template from a natural language description."""
    description = payload.get("description", "")
    if not description:
        raise HTTPException(status_code=400, detail="Description is required")
    
    from app.llm import get_llm
    from app.storage import load_prefs
    from langchain_core.messages import HumanMessage
    
    prefs = load_prefs()
    llm = get_llm(prefs, streaming=False)
    
    prompt = (
        "Generate a JSON worker agent template for a multi-agent workflow system.\n"
        f"User description: {description}\n\n"
        "Available tools: web_search, fetch_url, memory_search, artifact_read, file_write, pdf_export, send_email, browser_automation, shell_exec\n\n"
        "Respond ONLY with valid JSON (no markdown fencing):\n"
        '{"id": "snake_case_id", "name": "Human Name", "purpose": "one-liner", '
        '"system_prompt_template": "You are... Task-specific context: {task_description}", '
        '"allowed_tools": ["tool1"], "expected_output_schema": {"result": "string"}, "timeout": 90, "retry_limit": 2}'
    )
    
    try:
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = resp.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        return json.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {str(e)}")
