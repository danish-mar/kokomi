import asyncio
import re
import datetime
import uuid
import httpx
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from app.storage import load_prefs, save_prefs, load_chars, load_convos, save_convos
from app.llm import get_llm, resolve_character_model
from app.mcp import get_pool_tools, init_pool, pool_is_stale
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

router = APIRouter(prefix="/api/telegram")

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# Tracks the background polling task so we can cancel/restart it.
_poll_task: asyncio.Task | None = None


def _tg_url(token: str, method: str) -> str:
    return TELEGRAM_API.format(token=token, method=method)


async def start_polling():
    """Long-poll getUpdates in a background loop. Safe to call multiple times —
    cancels any existing poll task first so there's never a duplicate."""
    global _poll_task
    if _poll_task and not _poll_task.done():
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
    _poll_task = asyncio.create_task(_poll_loop())


async def stop_polling():
    global _poll_task
    if _poll_task and not _poll_task.done():
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
    _poll_task = None


async def _poll_loop():
    """Background long-polling loop using getUpdates (offset tracking)."""
    offset = 0
    webhook_cleared = False
    print("[Telegram] Polling loop started.")
    async with httpx.AsyncClient(timeout=40) as client:
        while True:
            prefs = load_prefs()
            if not prefs.get("telegram_enabled") or prefs.get("telegram_use_webhook"):
                await asyncio.sleep(5)
                continue
            token = prefs.get("telegram_bot_token", "")
            if not token:
                await asyncio.sleep(5)
                continue

            # getUpdates returns HTTP 409 Conflict if a webhook is still set.
            # Delete it once before we start polling so the two modes don't fight.
            if not webhook_cleared:
                try:
                    await client.post(_tg_url(token, "deleteWebhook"),
                                      json={"drop_pending_updates": False})
                    webhook_cleared = True
                    print("[Telegram] Cleared any existing webhook for polling mode.")
                except Exception as e:
                    print(f"[Telegram] Failed to clear webhook: {e}")

            try:
                resp = await client.get(
                    _tg_url(token, "getUpdates"),
                    params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
                )
                data = resp.json()
                if not data.get("ok"):
                    # Surface the real reason (e.g. 409 conflict, bad token) instead of
                    # silently retrying forever.
                    print(f"[Telegram] getUpdates failed: {data.get('error_code')} {data.get('description')}")
                    await asyncio.sleep(5)
                    continue
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message")
                    if not msg:
                        continue
                    chat_id = msg.get("chat", {}).get("id")
                    text = (msg.get("text") or "").strip()
                    if chat_id and text:
                        print(f"[Telegram] Message from {chat_id}: {text[:60]}")
                        asyncio.create_task(process_telegram_message(chat_id, text))
            except asyncio.CancelledError:
                print("[Telegram] Polling loop stopped.")
                raise
            except Exception as e:
                print(f"[Telegram] Polling error: {e}")
                await asyncio.sleep(5)


@router.post("/set-token")
async def set_token(request: Request):
    """Save only the telegram_bot_token pref — avoids full-prefs race conditions."""
    body = await request.json()
    token = body.get("token", "").strip()
    import re as _re
    if token and not _re.match(r'^\d{5,}:[A-Za-z0-9_-]{20,}$', token):
        return JSONResponse({"ok": False, "error": "Invalid token format"}, status_code=400)
    prefs = load_prefs()
    prefs["telegram_bot_token"] = token
    save_prefs(prefs)
    return {"ok": True}


@router.post("/polling/start")
async def polling_start():
    """Start the background polling loop immediately (no restart needed)."""
    prefs = load_prefs()
    token = prefs.get("telegram_bot_token")
    if not token:
        return JSONResponse({"ok": False, "error": "No bot token configured"}, status_code=400)
    # Polling and webhook are mutually exclusive — force polling mode and clear
    # any webhook so getUpdates won't return 409 Conflict.
    if prefs.get("telegram_use_webhook"):
        prefs["telegram_use_webhook"] = False
        save_prefs(prefs)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(_tg_url(token, "deleteWebhook"), json={"drop_pending_updates": False})
    except Exception as e:
        print(f"[Telegram] deleteWebhook on start failed: {e}")
    await start_polling()
    return {"ok": True, "message": "Polling started"}


@router.post("/polling/stop")
async def polling_stop():
    """Stop the background polling loop."""
    await stop_polling()
    return {"ok": True, "message": "Polling stopped"}


@router.get("/status")
async def telegram_status(request: Request):
    """Return current bot info + webhook status from Telegram."""
    prefs = load_prefs()
    token = prefs.get("telegram_bot_token", "")
    if not token:
        return {"ok": False, "error": "No bot token configured"}
    polling_active = bool(_poll_task and not _poll_task.done())
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            me = (await client.get(_tg_url(token, "getMe"))).json()
            wh = (await client.get(_tg_url(token, "getWebhookInfo"))).json()
            return {
                "ok": True,
                "bot": me.get("result", {}),
                "webhook": wh.get("result", {}),
                "polling_active": polling_active,
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "polling_active": polling_active}


@router.post("/register-webhook")
async def register_webhook(request: Request):
    """Call Telegram's setWebhook using this server's own origin URL."""
    prefs = load_prefs()
    token = prefs.get("telegram_bot_token", "")
    if not token:
        return JSONResponse({"ok": False, "error": "No bot token configured"}, status_code=400)

    # Build the webhook URL from the incoming request's base URL
    base = str(request.base_url).rstrip("/")
    webhook_url = f"{base}/api/telegram/webhook"

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(
                _tg_url(token, "setWebhook"),
                json={"url": webhook_url, "allowed_updates": ["message"]},
            )
            data = r.json()
            return {"ok": data.get("ok"), "description": data.get("description"), "webhook_url": webhook_url}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/sync-profile")
async def sync_bot_profile(request: Request):
    """Push the selected character's name, description and photo to the Telegram bot."""
    prefs = load_prefs()
    token = prefs.get("telegram_bot_token", "")
    if not token:
        return JSONResponse({"ok": False, "error": "No bot token configured"}, status_code=400)

    chars = load_chars()
    char_id = prefs.get("telegram_character_id", "kokomi")
    char = chars.get(char_id) or next(iter(chars.values()), None)
    if not char:
        return JSONResponse({"ok": False, "error": "Character not found"}, status_code=404)

    results = {}
    # Each call is isolated so one failure (e.g. flood-wait) doesn't abort the rest.
    async with httpx.AsyncClient(timeout=10) as client:
        async def _call(key, method, payload):
            try:
                r = await client.post(_tg_url(token, method), json=payload)
                results[key] = r.json()
            except Exception as e:
                results[key] = {"ok": False, "description": str(e)}

        name = char.get("name", "")
        if name:
            await _call("name", "setMyName", {"name": name[:64]})

        persona = char.get("persona", "")
        if persona:
            await _call("description", "setMyDescription", {"description": persona[:512]})
            await _call("short_description", "setMyShortDescription", {"short_description": persona[:120]})

    # NOTE: Telegram's Bot API has NO method to set a bot's profile photo.
    # The avatar can only be changed via @BotFather → /setuserpic. We surface
    # that to the UI so the user knows it's a manual one-time step.
    all_ok = all(v.get("ok") for v in results.values()) if results else False
    return {
        "ok": all_ok,
        "results": results,
        "photo_note": "Bot profile photo must be set manually via @BotFather → /setuserpic (Telegram's API can't do it).",
    }


async def send_telegram_message(chat_id: int | str, text: str, token: str):
    # Telegram caps message length at 4096 chars; split if needed.
    chunks = [text[i:i + 4096] for i in range(0, max(len(text), 1), 4096)]
    async with httpx.AsyncClient(timeout=15) as client:
        for chunk in chunks:
            try:
                await client.post(
                    _tg_url(token, "sendMessage"),
                    json={"chat_id": chat_id, "text": chunk},
                )
            except Exception as e:
                print(f"[Telegram] Failed to send message: {e}")


@router.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """Telegram Bot webhook endpoint. Register with:
    https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-host/api/telegram/webhook
    """
    prefs = load_prefs()
    if not prefs.get("telegram_enabled"):
        return {"ok": True}

    data = await request.json()

    # Only handle plain text messages for now (ignore edited, sticker, etc.)
    message = data.get("message") or data.get("edited_message")
    if not message:
        return {"ok": True}

    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return {"ok": True}

    background_tasks.add_task(process_telegram_message, chat_id, text)
    return {"ok": True}


async def process_telegram_message(chat_id: int | str, text: str):
    """Wrapper that guarantees errors are logged and the user gets a reply,
    since this runs as a fire-and-forget task whose exceptions are otherwise lost."""
    prefs = load_prefs()
    token = prefs.get("telegram_bot_token", "")
    if not token:
        print("[Telegram] No bot token configured.")
        return
    try:
        await _process_telegram_message(prefs, token, chat_id, text)
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            await send_telegram_message(chat_id, f"⚠️ Error: {e}", token)
        except Exception:
            pass


async def _process_telegram_message(prefs: dict, token: str, chat_id: int | str, text: str):

    # ── Allowlist check ───────────────────────────────────────────────────
    allowed = prefs.get("telegram_allowed_users", [])
    if allowed and str(chat_id) not in [str(u) for u in allowed]:
        await send_telegram_message(chat_id, "Sorry, you are not authorized to use this bot.", token)
        return

    # ── Admin commands ────────────────────────────────────────────────────
    if text.lower().startswith("/thinking "):
        val = text.split(None, 1)[1].strip().lower() in ("true", "on", "1", "yes")
        prefs["telegram_show_thinking"] = val
        save_prefs(prefs)
        await send_telegram_message(chat_id, f"System: thinking_show set to {val}", token)
        return

    # ── Load / create conversation ────────────────────────────────────────
    convos = load_convos()
    conv_id = f"telegram_{chat_id}"
    if conv_id not in convos:
        convos[conv_id] = {
            "id": conv_id,
            "title": f"Telegram: {chat_id}",
            "character_id": prefs.get("telegram_character_id", "kokomi"),
            "messages": [],
            "last_active": datetime.datetime.utcnow().isoformat(),
        }

    chars = load_chars()
    char_id = prefs.get("telegram_character_id", "kokomi")
    char = chars.get(char_id) or next(iter(chars.values()), None)
    if not char:
        await send_telegram_message(chat_id, "Error: no character configured.", token)
        return

    now = datetime.datetime.utcnow().isoformat()
    convos[conv_id]["messages"].append({"role": "user", "content": text, "timestamp": now})
    history = convos[conv_id]["messages"]

    # ── Build LLM ─────────────────────────────────────────────────────────
    provider = prefs.get("llm_provider", "groq")
    char_model = resolve_character_model(char, provider)
    llm = get_llm(prefs, model_override=char_model)

    persona = char.get("persona", "")
    persona += (
        "\n\nTELEGRAM MODE: You are talking directly to a user on Telegram. "
        "Your response will be sent to them immediately. Keep replies concise. "
        "Use tools only if necessary for tasks."
    )

    history_limit = max(1, int(prefs.get("telegram_history_limit", 10)))
    msgs = [SystemMessage(content=persona)]
    for m in history[-history_limit:]:
        if m["role"] == "user":
            msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            content = m["content"]
            if m.get("thinking"):
                content = f"<thought>\n{m['thinking']}\n</thought>\n\n{content}"
            msgs.append(AIMessage(content=content))

    # ── MCP tools ─────────────────────────────────────────────────────────
    mcp_ids = char.get("mcp_servers", [])
    if pool_is_stale():
        await init_pool()
    tool_defs, tool_sessions, _, _ = get_pool_tools(mcp_ids if mcp_ids else None)

    selected_tools = char.get("selected_tools", [])
    if selected_tools:
        tool_defs = [t for t in tool_defs if t["function"]["name"] in selected_tools]
        tool_sessions = {k: v for k, v in tool_sessions.items() if k in selected_tools}

    llm_with_tools = llm.bind_tools(tool_defs) if tool_defs else llm
    response = await llm_with_tools.ainvoke(msgs)

    # ── Tool loop ─────────────────────────────────────────────────────────
    max_rounds = max(1, min(100, int(prefs.get("max_tool_rounds", 8))))
    rounds = 0
    while getattr(response, "tool_calls", None) and rounds < max_rounds:
        if response.content:
            mid = response.content.strip()
            mid = re.sub(r"<(thought|think)>.*?</\1>", "", mid, flags=re.DOTALL).strip()
            if mid:
                await send_telegram_message(chat_id, mid, token)

        rounds += 1
        clean_tool_calls = [
            {
                "name": tc["name"],
                "args": tc["args"],
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                "type": "tool_call",
            }
            for tc in response.tool_calls
        ]
        msgs.append(AIMessage(content=response.content or "", tool_calls=clean_tool_calls))

        for tc in clean_tool_calls:
            session = tool_sessions.get(tc["name"])
            if session:
                res = await session.call_tool(tc["name"], arguments=tc["args"])
                res_txt = "".join([getattr(b, "text", str(b)) for b in res.content])
            else:
                res_txt = f"Error: Tool {tc['name']} not found"
            msgs.append(ToolMessage(content=res_txt, tool_call_id=tc["id"]))

        response = await llm_with_tools.ainvoke(msgs)

    # ── Finalize and persist ───────────────────────────────────────────────
    reasoning = response.additional_kwargs.get("reasoning_content", "")
    final_text = response.content.strip()

    now = datetime.datetime.utcnow().isoformat()
    convos[conv_id]["messages"].append({
        "role": "assistant",
        "content": final_text,
        "thinking": reasoning,
        "timestamp": now,
    })
    convos[conv_id]["updated_at"] = now
    save_convos(convos)

    show_thinking = prefs.get("telegram_show_thinking", False)
    clean_text = re.sub(r"<(thought|think)>.*?</\1>", "", final_text, flags=re.DOTALL).strip()

    if show_thinking and reasoning and "<thought" not in final_text:
        reply = f"💭 {reasoning}\n\n{final_text}"
    elif show_thinking:
        reply = final_text
    else:
        reply = clean_text

    if reply:
        await send_telegram_message(chat_id, reply, token)
