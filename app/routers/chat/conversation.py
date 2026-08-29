"""The core conversational endpoints: blocking `/chat` and streaming `/chat/stream`."""
import asyncio
import datetime
import json
import uuid
import time
import re
import base64
from typing import Optional
from functools import reduce
from operator import add

from fastapi import APIRouter, HTTPException
import os

from pypdf import PdfReader
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

from app.mcp import MCP_TOOL_CALL_TIMEOUT, get_pool_tools
from app.llm import get_llm, get_llm_for_tier, active_model_name, generate_title, parse_thinking, _normalize_model, resolve_character_model
from app.models import ChatRequest
from app.storage import load_prefs, load_chars, load_convos, save_convos
from app.insights import log_generation
from app.memory import save_memory, search_memories, summarize_conversation
from app.tools.memory_tool import get_memory_tool

from ._helpers import open_url, _get_tavily_tool, _get_scrape_tool, _get_image_tool, _ensure_pool

router = APIRouter(prefix="/api")


def find_artifact(history: list, artifact_id: str) -> Optional[dict]:
    """Locate an artifact by id anywhere in a conversation's history.

    Artifacts live on the assistant message that produced them. A canvas is
    re-emitted with the same id when updated, so scan newest-first and return
    the most recent version.
    """
    if not artifact_id:
        return None
    for msg in reversed(history or []):
        for art in (msg.get("artifacts") or []):
            if art.get("id") == artifact_id:
                return art
    return None


def _find_latest_canvas_artifact(history: list) -> Optional[dict]:
    """Most recent canvas-type artifact anywhere in history, newest-first."""
    for msg in reversed(history or []):
        for art in reversed(msg.get("artifacts") or []):
            if art.get("type") == "canvas":
                return art
    return None


def _open_canvas_context(history: list, canvas_id: Optional[str]) -> str:
    """System-prompt block showing the canvas the user is (or was last)
    working on.

    The frontend only sends canvas_id while the canvas PANEL is visibly
    open — closing it back to an inline artifact card sends None. But a
    closed panel isn't the user abandoning that artifact; a plain follow-up
    ("add three more records to that") still means it. So when no id is
    given, fall back to the most recent canvas artifact in the conversation
    rather than showing the model nothing and leaving it to reinvent a new
    artifact from scratch.

    The stored content is the source of truth and already includes any edits
    the user made in the editor, so this is what they're actually looking at.
    """
    art = find_artifact(history, canvas_id) if canvas_id else _find_latest_canvas_artifact(history)
    if not art or not (art.get("content") or "").strip():
        return ""

    mode = (art.get("mode") or "code").lower()
    lang = art.get("language") or ""
    label = f"{mode} canvas" + (f" ({lang})" if mode == "code" and lang else "")
    return (
        f"[OPEN CANVAS]\n"
        f"The user's most recent {label} is titled \"{art.get('title') or 'Untitled'}\" "
        f"(id=\"{art.get('id')}\") — the panel may or may not be visibly open right now, but a "
        f"follow-up like \"add three more rows\" or \"change the title\" almost always means "
        f"this one, not a brand new artifact. These are its CURRENT contents, including any "
        f"edits the user made themselves:\n"
        f"--- BEGIN CANVAS ---\n{art['content']}\n--- END CANVAS ---\n"
        f"When they ask you to change it, re-emit the canvas with id=\"{art.get('id')}\" and "
        f"the FULL updated contents. Do not repeat the contents in your chat reply.\n"
        f"[/OPEN CANVAS]"
    )


# Guidance the UI's message renderer relies on. The frontend turns plain markdown
# images/tables and two fenced blocks into rich interactive widgets, so the model
# must emit them as RAW markdown — never wrapped in a ```markdown / ```html fence,
# or they render as source code instead of widgets.
MEDIA_WIDGET_GUIDE = (
    "\n\nRICH MESSAGE WIDGETS — your replies render in a UI that upgrades certain "
    "markdown into interactive widgets. Emit these as RAW markdown only; NEVER wrap "
    "them in a ```markdown or ```html code fence (that shows the source instead of "
    "the widget).\n"
    "- Images: write a normal markdown image ![alt](https://url). It renders as a "
    "figure with dimensions and click-to-expand. Do not use HTML <img>.\n"
    "- Video: write a markdown link/image to a direct video file "
    "(.mp4/.webm/.ogg), e.g. ![clip](https://host/clip.mp4) — it becomes a player. "
    "For a poster/title use a ```kokomi-video fenced block whose body is JSON: "
    '{\"src\":\"https://host/clip.mp4\",\"poster\":\"https://host/p.jpg\",\"title\":\"Demo\"}.\n'
    "- Tables: write a normal GFM markdown table — it becomes sortable and "
    "filterable automatically. Image cells become thumbnails that expand.\n"
    "- Action buttons: when offering follow-ups or actions, you MAY emit a "
    "```kokomi-actions fenced block whose body is a JSON array (keep it to ~3-6 "
    "chips). Each item has a \"label\" plus exactly ONE verb:\n"
    '    {\"label\":\"...\",\"send\":\"a message to send as the user\"}\n'
    '    {\"label\":\"...\",\"fill\":\"text to prefill the input box (not sent)\"}\n'
    '    {\"label\":\"...\",\"url\":\"https://...\"}  (opens a link)\n'
    '    {\"label\":\"...\",\"copy\":\"text copied to clipboard\"}\n'
    '    {\"label\":\"...\",\"set\":{\"pref_key\":value}}  (changes a setting, e.g. '
    '{\"llm_provider\":\"google\"} or {\"web_search_enabled\":true})\n'
    "  Optional per-chip: \"icon\" (a Font Awesome class like \"fa-solid fa-bolt\"), "
    "\"variant\" (\"primary\", \"ghost\", or \"danger\"), and \"confirm\" (a yes/no "
    "prompt string shown before the action runs — use it with \"danger\" for any "
    "stateful or irreversible action)."
)


# ── Non-streaming chat ───────────────────────────────────────────────

@router.post("/chat")
async def chat(req: ChatRequest):
    t0 = time.time()
    prefs = load_prefs()
    provider = prefs.get("llm_provider", "groq")
    tier = req.model_tier or "normal"

    active_model = active_model_name(prefs, tier)

    user_p = prefs.get("user_persona", "")
    chars = load_chars()
    char_id = req.character_id or "kokomi"
    char = chars.get(char_id, chars.get("kokomi"))

    # Per-character model pinning only applies to the "normal" tier — the
    # fast/smart slider is an explicit override for this one message.
    char_model = resolve_character_model(char, provider) if tier == "normal" else None
    current_llm = get_llm_for_tier(prefs, tier, model_override=char_model)

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
    # Inject AI-synthesized relationship profile
    try:
        from app.memory import get_character_profile
        synth_profile = get_character_profile(char_id)
        if synth_profile:
            persona += f"\n\n[Relationship Context — AI Synthesized]:\n{synth_profile}"
    except Exception:
        pass
    if prefs.get("inject_time"):
        persona += f"\n\nCurrent System Date and Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    persona += (
        "\n\nCRITICAL: Always wrap your internal reasoning/thought process inside "
        "<think> and </think> tags before providing your final response."
    )
    persona += (
        "\n\nTOOL EXECUTION RULE: When the user's request requires MULTIPLE tool calls "
        "(e.g., 'set budget for X AND create category Y'), you MUST execute ALL required "
        "tool calls in sequence before providing your final text response. Do NOT respond "
        "with a text summary between tool calls — complete ALL actions first, then summarize "
        "all results together in one final response."
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

        selected_tools = char.get("selected_tools", [])
        if selected_tools:
            tool_defs = [t for t in tool_defs if t["function"]["name"] in selected_tools]
            tool_sessions = {k: v for k, v in tool_sessions.items() if k in selected_tools}
            tool_icons = {k: v for k, v in tool_icons.items() if k in selected_tools}

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

        image_tool = _get_image_tool(prefs)
        if image_tool:
            tool_defs.append(image_tool)
            builtin_tools[image_tool.name] = image_tool
            persona += (
                "\n\n[IMAGES] You can show the user real photos via the 'search_images' tool (results render "
                "automatically as a gallery). PROACTIVELY call it — without waiting to be asked, and BEFORE "
                "writing your answer — whenever the topic is something visual: a place/city/country/landmark/"
                "travel destination, a product or gadget, a dish or food, an animal or plant, a person or "
                "character, a building, a vehicle, or an artwork. Example: 'tell me about Aurangabad' → first "
                "call search_images('Aurangabad'), then write your reply. Skip it only for abstract topics "
                "(code, math, feelings, definitions) where a photo wouldn't help. Never paste raw image URLs "
                "or markdown image tags in your message."
            )

        if prefs.get("browser_redirect_enabled", True):
            tool_defs.append(open_url)
            builtin_tools[open_url.name] = open_url
            persona += (
                "\n\nYou have access to the 'open_url' tool, which launches a link in the "
                "user's device/browser. Use it ONLY when the user clearly wants to leave "
                "the chat to perform an action — e.g. start a phone call, email, navigate a "
                "map, or explicitly says 'open/launch this in my browser'. Do NOT use it "
                "merely to show, embed, preview, or display media inline — for images, "
                "video files and tables just write the markdown (see widget guidance) so it "
                "renders inside the chat."
            )

        # Triton: reach the user's paired computers (read-only file access)
        from ._triton_tools import get_triton_tools
        triton_tools, triton_note = get_triton_tools()
        for t in triton_tools:
            tool_defs.append(t)
            builtin_tools[t.name] = t
        if triton_note:
            persona += triton_note

        persona += MEDIA_WIDGET_GUIDE

        # Re-initialize SystemMessage with updated persona (including MCP/RAG context)
        lc_msgs[0] = SystemMessage(content=persona)

        if tool_defs:
            llm_with_tools = current_llm.bind_tools(tool_defs)
            response = await llm_with_tools.ainvoke(lc_msgs)
            final_content, t = parse_thinking(response.content)
            if t:
                all_thinking.append(t)

            max_rounds = max(1, min(100, int(prefs.get("max_tool_rounds", 8))))
            rounds = 0
            while response.tool_calls and rounds < max_rounds:
                rounds += 1

                # Clean tool calls to guarantee valid and matching IDs in history
                clean_tool_calls = []
                for tc in response.tool_calls:
                    clean_tool_calls.append({
                        "name": tc["name"],
                        "args": tc["args"],
                        "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        "type": "tool_call"
                    })

                lc_msgs.append(AIMessage(
                    content=response.content or "",
                    tool_calls=clean_tool_calls
                ))

                for tc in clean_tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["args"]
                    tool_call_id = tc["id"]
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
        title = await generate_title(req.message, content, prefs)
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
    tier = req.model_tier or "normal"

    active_model = active_model_name(prefs, tier)

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
            now_iso = datetime.datetime.utcnow().isoformat()
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

                all_selected_tools = set()
                has_explicit_tools = False
                for pid in pids:
                    pc = all_chars.get(pid) or {}
                    st = pc.get("selected_tools", [])
                    if st:
                        has_explicit_tools = True
                        all_selected_tools.update(st)
                if has_explicit_tools:
                    tool_defs = [t for t in tool_defs if t["function"]["name"] in all_selected_tools]
                    tool_sessions = {k: v for k, v in tool_sessions.items() if k in all_selected_tools}
                    tool_icons = {k: v for k, v in tool_icons.items() if k in all_selected_tools}

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

                image_tool = _get_image_tool(prefs)
                if image_tool:
                    tool_defs.append(image_tool)
                    builtin_tools[image_tool.name] = image_tool

                if prefs.get("browser_redirect_enabled", True):
                    tool_defs.append(open_url)
                    builtin_tools[open_url.name] = open_url

                # Triton: reach the user's paired computers (read-only file access)
                from ._triton_tools import get_triton_tools
                triton_tools, triton_note = get_triton_tools()
                for t in triton_tools:
                    tool_defs.append(t)
                    builtin_tools[t.name] = t

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

                                    # search_memories is fully synchronous: it makes an
                                    # embedding HTTP call and then a Qdrant query. Calling
                                    # it directly would block the event loop — stalling SSE
                                    # delivery and every other request — and would make the
                                    # gather() below run these one after another instead of
                                    # concurrently. Hand it to a worker thread.
                                    mems = await asyncio.to_thread(search_memories, char_id, req.message)

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

                    # Inject AI-synthesized relationship profile
                    try:
                        from app.memory import get_character_profile
                        synth_profile = get_character_profile(pid)
                        if synth_profile:
                            p_persona += f"\n\n[Relationship Context — AI Synthesized]:\n{synth_profile}"
                    except Exception:
                        pass

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

                    if "search_images" in builtin_tools:
                        p_persona += (
                            "\n\n[IMAGES] You can show the user real photos via the 'search_images' tool "
                            "(results render automatically as a gallery). PROACTIVELY call it — without waiting "
                            "to be asked, and BEFORE writing your answer — whenever the topic is something "
                            "visual: a place/city/country/landmark/travel destination, a product or gadget, a "
                            "dish or food, an animal or plant, a person or character, a building, a vehicle, or "
                            "an artwork. Example: 'tell me about Aurangabad' → first call search_images("
                            "'Aurangabad'), then write your reply referencing the photos naturally. Skip it only "
                            "for abstract topics (code, math, feelings, definitions) where a photo wouldn't help. "
                            "Never paste raw image URLs or markdown image tags in your message."
                        )

                    if prefs.get("browser_redirect_enabled", True) and "open_url" in builtin_tools:
                        p_persona += (
                            "\n\nYou have access to the 'open_url' tool, a universal launcher. "
                            "Use it ONLY when the user wants to leave the chat to perform an action "
                            "involving a link or communication:"
                            "\n- Call/Dial: Use 'tel:+91XXXXXXXXXX'"
                            "\n- Email: Use 'mailto:email@address.com'"
                            "\n- SMS: Use 'sms:+91XXXXXXXXXX'"
                            "\n- WhatsApp: Use 'whatsapp://send?phone=XXXXXXXXXX'"
                            "\n- Play/Watch externally: Use 'youtube://watch?v=ID' or 'https://youtube.com/...'"
                            "\n- Navigation/Maps: Use 'maps:?q=LocationName'"
                            "\n- Open Site: Use the standard https URL."
                            "\nDo NOT use 'open_url' to show, embed, preview or display images, video "
                            "files or tables inline — for those, just write the markdown (see widget "
                            "guidance) so it renders inside the chat."
                        )

                    if triton_note:
                        p_persona += triton_note

                    p_persona += MEDIA_WIDGET_GUIDE

                    p_persona += (
                        "\n\nIMPORTANT: Always wrap internal reasoning inside <think>...</think> tags before your response."
                    )
                    p_persona += (
                        "\n\nTOOL EXECUTION RULE: When the user's request requires MULTIPLE tool calls "
                        "(e.g., 'set budget for X AND create category Y'), you MUST execute ALL required "
                        "tool calls in sequence before providing your final text response. Do NOT respond "
                        "with a text summary between tool calls — complete ALL actions first, then summarize "
                        "all results together in one final response."
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
                            # Carried inside the repeated block on purpose: stated once
                            # elsewhere, this rule loses to the 15 copies above and the
                            # model tags canvases type=\"cpp\" instead of type=\"canvas\".
                            "EXCEPTION — if the user wants an editable CANVAS to work in, the tag is "
                            "type=\"canvas\" mode=\"code\" language=\"cpp\" (or mode=\"document\" or "
                            "mode=\"spreadsheet\"). type is then the literal word canvas, NEVER the language.\n"
                            "[/ARTIFACTS ENABLED]"
                        )
                        # NOTE: this repetition is load-bearing. Dropping it to x2 to
                        # save tokens measurably stopped the model emitting artifacts
                        # at all — models weight a rule by how much of the prompt it
                        # occupies, so one copy competes with ~10k chars of other
                        # instructions. Costs ~6900 chars (~1730 tokens) per request;
                        # if that budget ever needs reclaiming, trim the other
                        # capability blocks first, not this.
                        p_persona = (artifact_instr + "\n\n") * 15 + p_persona

                        # Chart capability: charts are a special artifact type rendered
                        # live with Chart.js on the frontend (auto-themed — do NOT specify
                        # colors). Added once (no multiplier) to keep the prompt lean.
                        chart_instr = (
                            "[CHARTS ENABLED]\n"
                            "To visualize quantitative data, emit a CHART as a special artifact: "
                            "<Artifact id=\"unique_id\" title=\"Chart Title\" type=\"chart\">{...json...}</Artifact>.\n"
                            "The body MUST be a single valid JSON object (no markdown, no comments) with this schema:\n"
                            "{\"type\": \"bar\"|\"line\"|\"pie\"|\"doughnut\"|\"radar\"|\"polarArea\", "
                            "\"labels\": [\"A\",\"B\",...], "
                            "\"datasets\": [{\"label\": \"Series name\", \"data\": [1,2,...]}], "
                            "\"stacked\": false}\n"
                            "Do NOT set colors or styling — the UI themes the chart to match the app automatically. "
                            "Use a chart only when it genuinely aids understanding; otherwise answer normally.\n"
                            "[/CHARTS ENABLED]"
                        )
                        p_persona = chart_instr + "\n\n" + p_persona

                        # Diagram capability: Mermaid diagrams rendered live & themed on
                        # the frontend. Added once (no multiplier).
                        diagram_instr = (
                            "[DIAGRAMS ENABLED]\n"
                            "To show processes, architectures, relationships, or timelines, emit a DIAGRAM "
                            "as a special artifact: <Artifact id=\"unique_id\" title=\"Diagram Title\" type=\"mermaid\">...mermaid code...</Artifact>.\n"
                            "The body MUST be valid Mermaid syntax (e.g. 'graph TD', 'sequenceDiagram', 'erDiagram', "
                            "'flowchart LR', 'gantt', 'mindmap', 'classDiagram'). No markdown fences, no extra prose inside.\n"
                            "Do NOT set colors/themes — the UI themes the diagram to match the app automatically. "
                            "Use a diagram only when it genuinely clarifies; otherwise answer normally.\n"
                            "[/DIAGRAMS ENABLED]"
                        )
                        p_persona = diagram_instr + "\n\n" + p_persona

                        # PDF capability: a real, paginated PDF rendered live (ReportLab)
                        # from markdown — for documents the user genuinely wants to open,
                        # print, or share (reports, resumes, letters, invoices), not for
                        # ordinary conversational replies. Added once (no multiplier).
                        pdf_instr = (
                            "[PDF ENABLED]\n"
                            "When the user wants an actual DOCUMENT they would open, print, share, or attach "
                            "(a report, resume, cover letter, invoice, proposal, certificate, formatted "
                            "handout, etc.) — not just a conversational answer — emit it as a PDF artifact: "
                            "<Artifact id=\"unique_id\" title=\"Document Title\" type=\"pdf\">...markdown...</Artifact>.\n"
                            "The body MUST be well-structured markdown: '# ' for the document title, '## '/'### ' "
                            "for sections, '- '/'1. ' for lists, '| a | b |' tables, '> ' quotes, and fenced "
                            "```code``` blocks. It renders as a real, paginated PDF the user can view or download "
                            "— do NOT also paste the same content as a normal chat message.\n"
                            "Use plain conversational text (no PDF artifact) for short answers, explanations, or "
                            "anything that isn't meant to be a standalone document.\n"
                            "LENGTH: if the user asks for a specific page count (e.g. 'a 10-page story'), that "
                            "count means PHYSICAL PRINTED PAGES once rendered — roughly 400-500 words each. A "
                            "'10-page story' means ~4000-5000 words of real prose, not 10 short section headers "
                            "with a one-liner under each. Write FULL paragraphs — scene-setting, dialogue, "
                            "sensory detail — under every heading, at a length proportional to the requested page "
                            "count, not a compressed outline. If you genuinely cannot produce that much in one "
                            "reply, write as much as you can and say so explicitly rather than silently handing "
                            "back a short version.\n"
                            "[/PDF ENABLED]"
                        )
                        p_persona = pdf_instr + "\n\n" + p_persona

                        # Canvas: an editable side-by-side working surface (the chat
                        # shrinks to 40%, the canvas takes 60%). "code" mode opens a
                        # real VS Code editor; "document" mode a Word-style page;
                        # "spreadsheet" mode an Excel-like grid. Use it for content the
                        # user will actually work ON, as opposed to a read-only artifact
                        # card they just look at.
                        canvas_instr = (
                            "[CANVAS ENABLED]\n"
                            "When the user wants to WORK ON something with you — write and iterate on a "
                            "program, draft and revise a document, refactor a file, build a table of "
                            "data — open a CANVAS: <Artifact id=\"unique_id\" title=\"Title\" "
                            "type=\"canvas\" mode=\"code\" language=\"python\">...</Artifact>.\n"
                            "ATTRIBUTE RULE — this overrides the general artifact rule above: "
                            "for a canvas, type MUST be the literal string \"canvas\". Do NOT put "
                            "the language in type. WRONG: type=\"java\" / type=\"code\" / "
                            "type=\"python\". RIGHT: type=\"canvas\" mode=\"code\" language=\"java\". "
                            "The language always goes in the separate 'language' attribute.\n"
                            "'mode' MUST be \"code\", \"document\" or \"spreadsheet\":\n"
                            "  • mode=\"code\" — opens a full code editor. Also set language=\"...\" "
                            "(python, javascript, typescript, html, css, sql, go, rust, java, cpp, "
                            "shell, yaml, json, markdown, ...). The body is RAW SOURCE CODE ONLY: no "
                            "markdown fences, no prose, no explanation inside the tag.\n"
                            "  • mode=\"document\" — opens a Word-style page editor. The body is "
                            "MARKDOWN ('# ' title, '## ' sections, '- ' lists, '**bold**', tables), "
                            "which is converted to a formatted, editable document.\n"
                            "  • mode=\"spreadsheet\" — opens an Excel-like grid. The body is RAW CSV "
                            "(comma-separated, one row per line, no markdown table syntax, no code "
                            "fences). The first row is usually a header row. A cell can hold a formula "
                            "by starting with \"=\" (e.g. \"=SUM(A2:A10)\", \"=B2*C2\") — the grid "
                            "evaluates it like a real spreadsheet. Use this whenever the user wants "
                            "tabular/numeric data they can sort, edit cell-by-cell, or run formulas "
                            "over, rather than a markdown table that's just read.\n"
                            "The user can EDIT the canvas directly, and their edits are saved. When they "
                            "ask for a change, you will be shown the CURRENT contents (including their "
                            "edits) — re-emit the canvas with the SAME id to update it in place, and "
                            "send the FULL new contents, never a diff or fragment.\n"
                            "Prefer a canvas over a plain artifact whenever the content is something to "
                            "be revised rather than just read. Keep your chat message short — the "
                            "content belongs in the canvas, don't repeat it in the reply.\n"
                            "[/CANVAS ENABLED]"
                        )
                        p_persona = canvas_instr + "\n\n" + p_persona

                        # If a canvas is open, show the model its CURRENT contents (which
                        # include any edits the user made) so "change X" works against what
                        # they're actually looking at, not the version originally generated.
                        canvas_ctx = _open_canvas_context(history, getattr(req, "canvas_id", None))
                        if canvas_ctx:
                            p_persona = canvas_ctx + "\n\n" + p_persona

                    # Question capability: an interactive QUESTION card the user answers by
                    # tapping an option instead of typing a reply. This is independent of the
                    # Artifacts toggle (it's a UI affordance, not a document/code artifact) —
                    # always available, and parsed regardless of prefs.artifacts below.
                    question_instr = (
                        "[QUESTIONS ENABLED]\n"
                        "When you genuinely need the user to choose between options or clarify "
                        "something before you can give a good answer, DON'T write a long paragraph "
                        "of questions — emit an interactive QUESTION card as a special artifact:\n"
                        "<Artifact id=\"unique_id\" title=\"Quick question\" type=\"question\">{...json...}</Artifact>.\n"
                        "For a SINGLE question, the body MUST be a single valid JSON object (no markdown, "
                        "no comments) with this schema:\n"
                        "{\"question\": \"The question text\", "
                        "\"options\": [\"First choice\", \"Second choice\", \"Third choice\"], "
                        "\"allowOther\": true, \"allowSkip\": true}\n"
                        "For SEVERAL related clarifying questions at once (e.g. gathering multiple "
                        "requirements before starting a task), use the batch form instead — the user "
                        "sees them as tabs and answers one at a time:\n"
                        "{\"questions\": [ "
                        "{\"title\": \"Audience\", \"question\": \"Who is this for?\", \"options\": [...], \"allowOther\": true, \"allowSkip\": true}, "
                        "{\"title\": \"Style\", \"question\": \"What look and feel?\", \"options\": [...]} "
                        "]}\n"
                        "\"title\" is a short 1-3 word label shown on the tab (not the full question) — "
                        "required in the batch form. Give 2–5 short, distinct options per question. Set "
                        "allowOther:true to offer a free-text 'something else' row, allowSkip:true to offer "
                        "a Skip button (both default true). Prefer a single question unless you genuinely "
                        "need several distinct pieces of information — don't pad with more than 4-5. After "
                        "emitting the card, STOP — do not keep talking; wait for the user's answer(s), which "
                        "arrive as their next message once every question is answered or skipped. Only use "
                        "this when the answer(s) actually change what you do; if you can reasonably proceed, "
                        "just answer. This works even if the user has Artifacts turned off.\n"
                        "QUIZ MODE: when the user explicitly wants a quiz/trivia/Kahoot-style game (there IS "
                        "one objectively correct option, not just a preference), add \"quiz\": true and "
                        "\"correctIndex\": <0-based index into options> to that question object (works in "
                        "both the single and batch forms; optionally add \"explanation\": \"why\" — shown "
                        "after the user answers). The UI will instantly reveal correct/incorrect on their "
                        "pick before continuing, so you don't need to say 'correct!' yourself — the user's "
                        "reply message will already say whether they got it right. NEVER set quiz/correctIndex "
                        "for ordinary clarifying questions — only for genuine quiz games.\n"
                        "MULTIPLE CHOICE (select several): if the user may reasonably want MORE THAN ONE of "
                        "the options at once (not a single pick), you MUST do BOTH of these — neither alone "
                        "is enough: (1) add \"multiSelect\": true to that question object, AND (2) end the "
                        "question text itself with '(select all that apply)' so the user knows without seeing "
                        "the JSON. If the question text says 'select all that apply' the UI treats it as "
                        "multi-select either way — but always set the JSON flag too. The UI shows checkboxes "
                        "and a Continue button instead of answering on the first tap. Don't combine multiSelect "
                        "with quiz/correctIndex (quiz mode is single-choice only).\n"
                        "[/QUESTIONS ENABLED]"
                    )
                    p_persona = question_instr + "\n\n" + p_persona

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


                    char_model = resolve_character_model(p_char, provider) if tier == "normal" else None
                    char_llm = get_llm_for_tier(prefs, tier, streaming=True, model_override=char_model)

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
                    f_reasoning = ""
                    collected_chunks = []
                    skipped = False
                    char_tool_calls_log: list = []
                    char_artifacts_log: list = []

                    if is_debug:
                        msg = f"👉 Generating for: {char_name} (Model: {p_active_model})\nPrompt Length: {len(p_persona)} chars. Tools: {len(tool_defs) if tool_defs else 0}"
                        print(f"\n[DEBUG] {msg}")
                        await queue.put(f"data: {json.dumps({'type': 'debug', 'message': msg})}\n\n")
                        # Time spent before we even ask the model — memory search,
                        # tool loading, prompt assembly. Printed so a slow first
                        # token can be attributed to setup vs. the model itself.
                        print(f"[DEBUG] Setup took {time.time() - t0:.2f}s (pre-LLM)")
                        print(f"[DEBUG] Streaming chunks...")

                    t_llm_start = time.time()

                    art_active = False
                    art_id = None
                    art_meta = {}
                    art_content = ""
                    pending_buffer = ""

                    async for chunk in target_llm.astream(p_lc_msgs):
                        collected_chunks.append(chunk)

                        if hasattr(chunk, "reasoning_content") and chunk.reasoning_content:
                            if ts_first is None: ts_first = time.time()
                            f_reasoning += chunk.reasoning_content
                            await queue.put(f"data: {json.dumps({'type': 'reasoning', 'delta': chunk.reasoning_content, 'character_id': pid, 'model': p_active_model})}\n\n")
                        elif chunk.additional_kwargs and "reasoning_content" in chunk.additional_kwargs:
                            if ts_first is None: ts_first = time.time()
                            f_reasoning += chunk.additional_kwargs["reasoning_content"]
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

                            # Tag parsing always runs, regardless of the Artifacts toggle: this is what
                            # lets <Artifact type="question"> cards keep working even when Artifacts is
                            # off (the model only emits other artifact types when prefs.artifacts is True).
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
                        _ttft = f"{(ts_first - t_llm_start):.2f}s" if ts_first else "n/a (tool-call only)"
                        print(f"\n[DEBUG] Stream generation finished. "
                              f"TTFT {_ttft}, generation {time.time() - t_llm_start:.2f}s")

                    if skipped:
                        continue

                    full_response = reduce(add, collected_chunks) if collected_chunks else None

                    if full_response and getattr(full_response, "tool_calls", None) and tool_defs:
                        curr_resp = full_response
                        if "<think>" in f_content and "</think>" not in f_content:
                            f_content += "\n</think>\n"
                            await queue.put(f"data: {json.dumps({'type': 'content', 'delta': '\n</think>\n', 'character_id': pid, 'model': p_active_model})}\n\n")
                        max_rounds = max(1, min(100, int(prefs.get("max_tool_rounds", 8))))
                        for _ in range(max_rounds):
                            if not curr_resp.tool_calls:
                                break

                            # Clean tool calls to guarantee valid and matching IDs in history
                            clean_tool_calls = []
                            for tc in curr_resp.tool_calls:
                                clean_tool_calls.append({
                                    "name": tc["name"],
                                    "args": tc["args"],
                                    "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                                    "type": "tool_call"
                                })

                            p_lc_msgs.append(AIMessage(
                                content=curr_resp.content or "",
                                tool_calls=clean_tool_calls
                            ))

                            for tc in clean_tool_calls:
                                tname = tc["name"]
                                targs = tc["args"]
                                tid = tc["id"]
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
                                    f_reasoning += c.reasoning_content
                                    await queue.put(f"data: {json.dumps({'type': 'reasoning', 'delta': c.reasoning_content, 'character_id': pid})}\n\n")
                                elif c.additional_kwargs and "reasoning_content" in c.additional_kwargs:
                                    f_reasoning += c.additional_kwargs["reasoning_content"]
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
                                    # Tag parsing always runs, regardless of the Artifacts toggle: this is what
                                    # lets <Artifact type="question"> cards keep working even when Artifacts is
                                    # off (the model only emits other artifact types when prefs.artifacts is True).
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
                            if new_resp and getattr(new_resp, "tool_calls", None) and len(new_resp.tool_calls) > 0:
                                curr_resp = new_resp
                                if "<think>" in f_content and "</think>" not in f_content:
                                    f_content += "\n</think>\n"
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

                    if f_reasoning.strip():
                        thk = f_reasoning.strip()
                        cleaned = f_content.strip()
                    else:
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
                                for item in facts:
                                    # save_memory is synchronous — an embedding call plus a
                                    # Qdrant dedup query and upsert. This task already runs
                                    # off the request via create_task, but its own body still
                                    # executes ON the event loop, so calling save_memory
                                    # directly would freeze the whole app for every user
                                    # for the duration of that round-trip, on every turn.
                                    if isinstance(item, dict):
                                        await asyncio.to_thread(
                                            save_memory, char_id, item["fact"],
                                            importance=item.get("importance", 3.0),
                                        )
                                    else:
                                        await asyncio.to_thread(save_memory, char_id, str(item))

                            # Summarize the last few turns to extract new facts
                            asyncio.create_task(background_memory_task(history[-4:], pid, prefs))

                title = None
                if is_new:
                    title_content = history[-1]["content"] if len(history) > 1 else "New Chat"
                    title = await generate_title(req.message, title_content, prefs)
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

                # Rate limits otherwise surface as a mysterious long hang: the
                # provider client retries with backoff internally, so the user
                # just sees nothing happen. Name it explicitly.
                low = err_msg.lower()
                if "rate limit" in low or "429" in low or "tokens per minute" in low:
                    err_msg = (
                        "Rate limited by the model provider (tokens-per-minute cap). "
                        "The request was retried and still hit the limit. Large prompts "
                        "consume the per-minute budget quickly — wait a moment and retry, "
                        "or upgrade the provider tier.\n\n" + err_msg
                    )

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
