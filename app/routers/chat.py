import asyncio
import datetime
import json
import uuid
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
    if not GROQ_API_KEY:
        raise HTTPException(500, "GROQ_API_KEY not set")

    prefs = load_prefs()
    provider = prefs.get("llm_provider", "groq")

    if provider == "google":
        active_model = _normalize_model(prefs.get("model_name", "gemini-2.5-flash"))
    elif provider == "local":
        active_model = _normalize_model(prefs.get("local_model", "local-model"))
    else:
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
                "USE the search_knowledge_base tool FIRST to find relevant information before answering."
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
    if not GROQ_API_KEY:
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
                            "USE the search_knowledge_base tool FIRST to find relevant information before answering."
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

                    p_active_model = active_model
                    if char_model and char_model != "default":
                        p_active_model = _normalize_model(char_model)

                    f_content = ""
                    collected_chunks = []
                    skipped = False
                    char_tool_calls_log: list = []

                    async for chunk in target_llm.astream(p_lc_msgs):
                        collected_chunks.append(chunk)

                        if hasattr(chunk, "reasoning_content") and chunk.reasoning_content:
                            await queue.put(f"data: {json.dumps({'type': 'reasoning', 'delta': chunk.reasoning_content, 'character_id': pid, 'model': p_active_model})}\n\n")
                        elif chunk.additional_kwargs and "reasoning_content" in chunk.additional_kwargs:
                            await queue.put(f"data: {json.dumps({'type': 'reasoning', 'delta': chunk.additional_kwargs['reasoning_content'], 'character_id': pid, 'model': p_active_model})}\n\n")

                        if chunk.content:
                            if not f_content and "[SKIP]" in chunk.content.upper():
                                skipped = True
                                break
                            f_content += chunk.content
                            await queue.put(f"data: {json.dumps({'type': 'content', 'delta': chunk.content, 'character_id': pid, 'model': p_active_model})}\n\n")

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
                                print(f"  [DEBUG] Calling Tool: '{tname}' with args: {targs}")
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
                                    await queue.put(f"data: {json.dumps({'type': 'content', 'delta': c.content, 'character_id': pid})}\n\n")
                            f_content += fcl

                            new_resp = reduce(add, inner_chunks) if inner_chunks else None
                            if new_resp and getattr(new_resp, "tool_calls", None):
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
                        "model": p_active_model,
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

                await queue.put(f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id, 'title': title})}\n\n")
                await queue.put("data: [DONE]\n\n")

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
