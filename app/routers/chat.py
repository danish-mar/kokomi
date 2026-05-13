import asyncio
import datetime
import json
import uuid
import time
import re
from functools import reduce
from operator import add

from app.mcp import MCP_TOOL_CALL_TIMEOUT

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import tool

from app.config import GROQ_API_KEY
from app.llm import get_llm, generate_title, parse_thinking, _normalize_model, resolve_character_model
from app.mcp import get_pool_tools, init_pool, pool_is_stale
from app.models import ChatRequest
from app.storage import load_prefs, load_chars, load_convos, save_convos
from app.insights import log_generation


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
    """Build a TavilySearchResults tool from prefs, or None if not configured."""
    try:
        if not prefs.get("web_search_enabled"):
            return None
        from langchain_community.tools.tavily_search import TavilySearchResults
        api_key = prefs.get("tavily_api_key") or ""
        if not api_key:
            return None
        import os
        os.environ["TAVILY_API_KEY"] = api_key
        return TavilySearchResults(max_results=5, name="web_search")
    except ImportError:
        return None


router = APIRouter(prefix="/api")


async def _ensure_pool():
    """Lazily initialize the MCP pool if it's stale or not ready."""
    if pool_is_stale():
        await init_pool()


# ── Non-streaming chat ───────────────────────────────────────────────

@router.post("/chat")
async def chat(req: ChatRequest):
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
    now = datetime.datetime.utcnow().isoformat()
    history.append({"role": "user", "content": req.message, "timestamp": now})

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
                art_versions = {} # Track artifact versions across characters in this turn

                if is_debug:
                    msg = f"=== STARTING STREAM CHAT ===\nConversation: {conv_id}, Participants: {pids}"
                    print(f"\n[DEBUG] {msg}")
                    await queue.put(f"data: {json.dumps({'type': 'debug', 'message': msg})}\n\n")

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
                    if prefs.get("artifacts", True):
                        artifact_instr = (
                            "[ARTIFACTS ENABLED]\n"
                            "When generating standalone code files, configs, scripts, or long documents, "
                            "you MUST wrap the output in <Artifact> XML tags with correct parameters "
                            "BEFORE writing any content. Never use plain code blocks for standalone files. "
                            "The opening <Artifact> tag must appear before the first line of content "
                            "so the UI can render the artifact panel immediately during streaming.\n"
                            "[/ARTIFACTS ENABLED]"
                        )
                        p_persona = (artifact_instr + "\n\n") * 4 + p_persona

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

                    char_model = resolve_character_model(p_char, provider)
                    char_llm = get_llm(prefs, streaming=True, model_override=char_model)
                    target_llm = char_llm.bind_tools(tool_defs) if tool_defs else char_llm

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
                             await queue.put(f"data: {json.dumps({'type': 'artifact_close', 'id': art_id, 'content': art_content})}\n\n")
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
                                                        # Versioning
                                                        v = art_versions.get(art_id, 0) + 1
                                                        art_versions[art_id] = v
                                                        art_meta["version"] = str(v)
                                                        
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
                        "is_anonymous": req.is_anonymous
                    }
                else:
                    convos[conv_id].update({
                        "messages": history, 
                        "updated_at": now, 
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
