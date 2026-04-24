import sys

file_path = '/home/keqing/git/MyWorld/kokomi/server.py'
with open(file_path, 'r') as f:
    lines = f.readlines()

new_content = """@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    if not groq_key:
        raise HTTPException(500, "GROQ_API_KEY not set")

    prefs = load_prefs()
    active_model = prefs.get("model_name") if prefs.get("llm_provider") == "groq" else prefs.get("local_model")
    user_p = prefs.get("user_persona", "")
    current_llm = get_llm(prefs, streaming=True)

    chars = load_chars()
    char_id = req.character_id or "kokomi"
    
    convos = load_convos()
    conv_id = req.conversation_id
    is_new = conv_id is None or conv_id not in convos
    
    if is_new:
        conv_id = str(uuid.uuid4())[:12]

    history = [] if (not conv_id or conv_id not in convos) else convos[conv_id].get("messages", [])
    now = datetime.datetime.utcnow().isoformat()
    
    user_msg = {"role": "user", "content": req.message, "timestamp": now}
    history.append(user_msg)

    async def event_generator():
        nonlocal history
        tool_calls_log = []
        
        try:
            yield f"data: {json.dumps({'type': 'start'})}\\n\\n"
            
            pids = req.participants or [char_id]
            all_chars = load_chars()
            
            all_mcp_ids = []
            for pid in pids:
                p_char = all_chars.get(pid)
                if p_char: all_mcp_ids.extend(p_char.get("mcp_servers", []))
            all_mcp_ids = list(set(all_mcp_ids))

            async with AsyncExitStack() as stack:
                tool_defs, tool_sessions, mcp_errors = await connect_mcp_servers(stack, all_mcp_ids)
                for err in mcp_errors:
                    yield f"data: {json.dumps({'type': 'warning', 'message': err})}\\n\\n"
                
                for pid in pids:
                    p_char = all_chars.get(pid)
                    if not p_char: continue
                    
                    p_persona = p_char.get("persona", "")
                    if user_p: p_persona += f"\\n\\nUser Profile:\\n{user_p}"
                    
                    if len(pids) > 1:
                        p_persona += f"\\n\\nROOM CONTEXT: You are in a group chat with: {', '.join([all_chars.get(x, {}).get('name', x) for x in pids if x != pid])}."
                        p_persona += "\\nDECISION: If you feel you don't need to respond to the last message, start your response with ONLY the word [SKIP]. Otherwise, respond normally."
                    
                    p_persona += "\\n\\nCRITICAL: Always wrap reasoning inside <think>...</think>."
                    
                    p_lc_msgs = [SystemMessage(content=p_persona)]
                    for m in history[-12:]:
                        sender = "user" if m["role"] == "user" else m.get('character_name', 'Assistant')
                        p_lc_msgs.append(HumanMessage(content=f"[{sender}]: {m['content']}") if m["role"] == "user" else AIMessage(content=f"[{sender}]: {m['content']}"))
                    
                    target_llm = current_llm
                    if tool_defs:
                        target_llm = current_llm.bind_tools(tool_defs)
                    
                    f_content = ""
                    collected_chunks = []
                    skipped = False
                    
                    async for chunk in target_llm.astream(p_lc_msgs):
                        if not chunk.content and not hasattr(chunk, "reasoning_content"): continue
                        if not f_content and chunk.content and "[SKIP]" in chunk.content.upper():
                            skipped = True
                            break
                        if hasattr(chunk, "reasoning_content") and chunk.reasoning_content:
                            yield f"data: {json.dumps({'type': 'reasoning', 'delta': chunk.reasoning_content, 'character_id': pid})}\\n\\n"
                        elif chunk.additional_kwargs and "reasoning_content" in chunk.additional_kwargs:
                            yield f"data: {json.dumps({'type': 'reasoning', 'delta': chunk.additional_kwargs['reasoning_content'], 'character_id': pid})}\\n\\n"
                        if chunk.content:
                            f_content += chunk.content
                            yield f"data: {json.dumps({'type': 'content', 'delta': chunk.content, 'character_id': pid})}\\n\\n"
                        collected_chunks.append(chunk)

                    if skipped: continue
                    
                    from functools import reduce
                    from operator import add
                    full_response = reduce(add, collected_chunks) if collected_chunks else None
                    
                    if full_response and hasattr(full_response, 'tool_calls') and full_response.tool_calls and tool_defs:
                        curr_resp = full_response
                        for _ in range(3):
                            if not curr_resp.tool_calls: break
                            p_lc_msgs.append(curr_resp)
                            for tc in curr_resp.tool_calls:
                                tname = tc["name"]
                                targs = tc["args"]
                                tid = tc.get("id", str(uuid.uuid4())[:8])
                                yield f"data: {json.dumps({'type': 'tool_start', 'name': tname, 'character_id': pid})}\\n\\n"
                                sess = tool_sessions.get(tname)
                                tr = await sess.call_tool(tname, arguments=targs) if sess else "Error: Tool not found"
                                txt = "".join([getattr(b, "text", str(b)) for b in (tr.content if hasattr(tr, 'content') else [])]) or str(tr)
                                p_lc_msgs.append(ToolMessage(content=txt, tool_call_id=tid))
                            
                            fcl = ""
                            async for c in target_llm.astream(p_lc_msgs):
                                if c.content:
                                    fcl += c.content
                                    yield f"data: {json.dumps({'type': 'content', 'delta': c.content, 'character_id': pid})}\\n\\n"
                            f_content += fcl
                            break 

                    frw, thk = parse_thinking(f_content)
                    history.append({
                        "role": "assistant",
                        "character_id": pid,
                        "character_name": p_char.get("name"),
                        "content": frw.strip(),
                        "thinking": thk,
                        "model": active_model,
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                    })
                
                title = None
                if is_new:
                    title_content = history[-1]["content"] if len(history) > 1 else "New Chat"
                    title = await generate_title(req.message, title_content)
                    convos[conv_id] = {"title": title, "character_id": char_id, "messages": history, "updated_at": now, "participants": pids}
                else:
                    convos[conv_id].update({"messages": history, "updated_at": now, "participants": pids})
                
                save_convos(convos)
                yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id, 'title': title})}\\n\\n"
                yield "data: [DONE]\\n\\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\\n\\n"

    return StreamingResponse(
        event_generator(), 
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
"""

# Find the start of @app.post("/api/chat/stream")
start_idx = -1
for i, line in enumerate(lines):
    if '@app.post("/api/chat/stream")' in line:
        start_idx = i
        break

if start_idx != -1:
    lines = lines[:start_idx]
    with open(file_path, 'w') as f:
        f.writelines(lines)
        f.write(new_content)
    print("Successfully updated chat_stream")
else:
    print("Could not find chat_stream")
    sys.exit(1)
