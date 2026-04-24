import json
import os
import datetime
import uuid
import httpx
from fastapi import APIRouter, Request, BackgroundTasks
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

from app.storage import load_prefs, load_chars, load_convos, save_convos, load_mcp
from app.llm import get_llm, resolve_character_model
from app.mcp import connect_mcp_servers
from app.config import DATA_DIR

router = APIRouter(prefix="/api/whatsapp")

WORKFLOWS_FILE = os.path.join(DATA_DIR, "workflows.json")

def load_workflows():
    if not os.path.exists(WORKFLOWS_FILE):
        return []
    try:
        with open(WORKFLOWS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_workflow(entry):
    workflows = load_workflows()
    workflows.append(entry)
    with open(WORKFLOWS_FILE, "w") as f:
        json.dump(workflows, f, indent=2, default=str)

async def run_agent_task(main_char_id: str, target_char_id: str, task_message: str, workflow_id: str):
    """
    Simulate one character (main) asking another (target) to do something.
    """
    prefs = load_prefs()
    chars = load_chars()
    
    # Case-insensitive lookup
    target_char = None
    target_key = target_char_id.lower()
    for k, v in chars.items():
        if k.lower() == target_key or v.get("name", "").lower() == target_key:
            target_char = v
            target_char_id = v.get("id", k)
            break
            
    if not target_char:
        return f"Error: Character {target_char_id} not found."

    provider = prefs.get("llm_provider", "groq")
    char_model = resolve_character_model(target_char, provider)
    llm = get_llm(prefs, model_override=char_model)

    persona = target_char.get("persona", "")
    # Inject workflow context
    persona += f"\n\nWORKFLOW CONTEXT: You have been deployed by {main_char_id} to handle a specific request: {task_message}"
    
    msgs = [
        SystemMessage(content=persona),
        HumanMessage(content=task_message)
    ]

    mcp_ids = target_char.get("mcp_servers", [])
    from contextlib import AsyncExitStack
    
    async with AsyncExitStack() as stack:
        tool_defs, tool_sessions, _ = await connect_mcp_servers(stack, mcp_ids)
        
        if tool_defs:
            llm_with_tools = llm.bind_tools(tool_defs)
            response = await llm_with_tools.ainvoke(msgs)
            
            rounds = 0
            while response.tool_calls and rounds < 5:
                rounds += 1
                msgs.append(response)
                for tc in response.tool_calls:
                    tname = tc["name"]
                    targs = tc["args"]
                    tid = tc.get("id", str(uuid.uuid4())[:8])
                    
                    session = tool_sessions.get(tname)
                    if session:
                        res = await session.call_tool(tname, arguments=targs)
                        txt = "".join([getattr(b, "text", str(b)) for b in res.content])
                    else:
                        txt = f"Error: Tool {tname} not found"
                    
                    save_workflow({
                        "workflow_id": workflow_id,
                        "type": "tool_execution",
                        "agent": target_char_id,
                        "tool": tname,
                        "args": targs,
                        "result": txt,
                        "timestamp": datetime.datetime.utcnow().isoformat()
                    })
                    msgs.append(ToolMessage(content=txt, tool_call_id=tid))
                
                response = await llm_with_tools.ainvoke(msgs)
            
            final_content = response.content
        else:
            response = await llm.ainvoke(msgs)
            final_content = response.content

    return final_content

@router.post("/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    from_jid = data.get("from")
    body = data.get("body")
    
    prefs = load_prefs()
    if not prefs.get("whatsapp_enabled"):
        return {"status": "disabled"}

    char_id = prefs.get("whatsapp_character_id", "kokomi")
    chars = load_chars()
    char = chars.get(char_id, chars.get("kokomi"))
    
    # Process the message with the assigned character
    background_tasks.add_task(process_whatsapp_message, char, from_jid, body)
    
    return {"status": "ok"}

async def process_whatsapp_message(char, from_jid, body):
    prefs = load_prefs()
    
    # 1. Secret commands: thinking_show=true|false
    if "thinking_show=" in body.lower():
        new_val = "true" in body.lower()
        prefs["whatsapp_show_thinking"] = new_val
        from app.storage import save_prefs
        save_prefs(prefs)
        await send_whatsapp_reply(f"System: thinking_show set to {new_val}", from_jid)
        return

    # 2. Load context (History)
    from app.storage import load_convos, save_convos
    convos = load_convos()
    conv_id = f"whatsapp_{from_jid.replace('@', '_').replace('.', '_')}"
    if conv_id not in convos:
        convos[conv_id] = {
            "id": conv_id,
            "title": f"WhatsApp: {from_jid}",
            "character_id": char['id'],
            "messages": [],
            "last_active": datetime.datetime.utcnow().isoformat()
        }
    
    now = datetime.datetime.utcnow().isoformat()
    convos[conv_id]["messages"].append({"role": "user", "content": body, "timestamp": now})
    history = convos[conv_id]["messages"]

    provider = prefs.get("llm_provider", "groq")
    char_model = resolve_character_model(char, provider)
    llm = get_llm(prefs, model_override=char_model)
    
    workflow_id = str(uuid.uuid4())[:8]
    
    # Custom tools for the WhatsApp agent
    from langchain_core.tools import tool
    
    @tool
    async def deploy_agent(agent_id: str, request: str):
        """Deploy another character (agent) to handle a specific task or question."""
        save_workflow({
            "workflow_id": workflow_id,
            "type": "deployment",
            "from": char['id'],
            "to": agent_id,
            "request": request,
            "timestamp": datetime.datetime.utcnow().isoformat()
        })
        result = await run_agent_task(char['id'], agent_id, request, workflow_id)
        save_workflow({
            "workflow_id": workflow_id,
            "type": "deployment_result",
            "agent": agent_id,
            "response": result,
            "timestamp": datetime.datetime.utcnow().isoformat()
        })
        return result

    tools = [deploy_agent]
    
    # Add character's own MCP tools
    mcp_ids = char.get("mcp_servers", [])
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        tool_defs, tool_sessions, _ = await connect_mcp_servers(stack, mcp_ids)
        
        persona = char.get("persona", "")
        persona += "\n\nWHATSAPP MODE: You are talking directly to a user on WhatsApp. Your response will be sent to them immediately. Use tools only if necessary for tasks."
        
        msgs = [SystemMessage(content=persona)]
        # Add history (last 10 messages)
        for m in history[-10:]:
            if m["role"] == "user":
                msgs.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                content = m["content"]
                if m.get("thinking"):
                    content = f"<thought>\n{m['thinking']}\n</thought>\n\n{content}"
                msgs.append(AIMessage(content=content))
        
        llm_with_tools = llm.bind_tools(tools + tool_defs)
        response = await llm_with_tools.ainvoke(msgs)
        
        # Tool loop
        rounds = 0
        while response.tool_calls and rounds < 5:
            # If AI says something BEFORE tool (e.g. "Okay, I'll ask Kokomi...")
            if response.content:
                mid_text = response.content.strip()
                if not prefs.get("whatsapp_show_thinking", True):
                    import re
                    mid_text = re.sub(r"<(thought|think)>.*?</\1>", "", mid_text, flags=re.DOTALL).strip()
                if mid_text:
                    await send_whatsapp_reply(mid_text, from_jid)
                    # Add to history
                    convos[conv_id]["messages"].append({
                        "role": "assistant",
                        "content": mid_text,
                        "timestamp": datetime.datetime.utcnow().isoformat()
                    })

            rounds += 1
            msgs.append(response)
            for tc in response.tool_calls:
                tname = tc["name"]
                targs = tc["args"]
                tid = tc.get("id", str(uuid.uuid4())[:8])
                
                if tname == "deploy_agent":
                    res_txt = await deploy_agent(**targs)
                else:
                    session = tool_sessions.get(tname)
                    if session:
                        res = await session.call_tool(tname, arguments=targs)
                        res_txt = "".join([getattr(b, "text", str(b)) for b in res.content])
                    else:
                        res_txt = f"Error: Tool {tname} not found"
                
                msgs.append(ToolMessage(content=res_txt, tool_call_id=tid))
            
            response = await llm_with_tools.ainvoke(msgs)

        # 3. Handle response and persistence
        reasoning = response.additional_kwargs.get("reasoning_content", "")
        final_text = response.content.strip()
        
        # Save to history
        now = datetime.datetime.utcnow().isoformat()
        convos[conv_id]["messages"].append({
            "role": "assistant",
            "content": final_text,
            "thinking": reasoning,
            "timestamp": now
        })
        convos[conv_id]["updated_at"] = now
        save_convos(convos)

        # Format final reply for WhatsApp
        show_thinking = prefs.get("whatsapp_show_thinking", True)
        
        # Strip internal thinking tags if they exist in the content
        import re
        clean_text = re.sub(r"<(thought|think)>.*?</\1>", "", final_text, flags=re.DOTALL).strip()
        
        if show_thinking:
            whatsapp_reply = final_text
            # If reasoning_content was separate, add it
            if reasoning and "<thought" not in final_text and "<think" not in final_text:
                whatsapp_reply = f"<thought>\n{reasoning}\n</thought>\n\n{final_text}"
        else:
            whatsapp_reply = clean_text

        if whatsapp_reply:
            await send_whatsapp_reply(whatsapp_reply, from_jid)

async def send_whatsapp_reply(message: str, to_jid: str):
    prefs = load_prefs()
    api_url = prefs.get("whatsapp_api_url", "http://localhost:3013")
    
    if api_url:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(f"{api_url}/send", json={
                    "to": to_jid,
                    "message": message
                })
        except Exception as e:
            print(f"Failed to send WhatsApp reply: {e}")
