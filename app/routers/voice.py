"""
Voice call WebSocket router.

Bridges the browser's microphone audio to the Gemini Live (Realtime) API
and streams generated audio back.  The browser sends raw PCM-16kHz chunks
over the WebSocket; the server forwards them to Gemini and relays Gemini's
24kHz PCM audio frames (plus any text transcript) back.
"""

import asyncio
import base64
import json
import traceback
from contextlib import AsyncExitStack

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from google import genai
from google.genai import types

from app.config import GOOGLE_API_KEY
from app.storage import load_chars, load_prefs
from app.mcp import connect_mcp_servers

router = APIRouter()

# Gemini Live expects 16kHz in, returns 24kHz out
INPUT_RATE = 16000
OUTPUT_RATE = 24000

# Voice model
VOICE_MODEL = "gemini-2.5-flash-native-audio-latest"

# Available voice presets
VOICE_PRESETS = {
    "aoede":  "Aoede",
    "puck":   "Puck",
    "charon": "Charon",
    "kore":   "Kore",
    "fenrir": "Fenrir",
}


@router.websocket("/ws/voice/{character_id}")
async def voice_call(ws: WebSocket, character_id: str, space_id: str | None = None):
    """Full-duplex voice chat with a character via Gemini Live."""
    await ws.accept()

    if not GOOGLE_API_KEY:
        await ws.send_json({"type": "error", "message": "GOOGLE_API_KEY not configured"})
        await ws.close()
        return

    # Load character info
    chars = load_chars()
    char = chars.get(character_id, chars.get("kokomi"))
    prefs = load_prefs()

    char_name = char.get("name", "Assistant")
    persona = char.get("persona", "You are a helpful AI assistant.")
    user_p = prefs.get("user_persona", "")
    if user_p:
        persona += f"\n\nInformation about the user:\n{user_p}"

    # Pick voice (stored on character or default)
    voice_key = char.get("voice", "aoede").lower()
    voice_name = VOICE_PRESETS.get(voice_key, "Aoede")

    # Connect to MCP servers if character has them
    mcp_sids = char.get("mcp_servers", [])
    
    try:
        await ws.send_json({"type": "status", "message": "connecting"})
        
        async with AsyncExitStack() as stack:
            tool_defs, tool_sessions, errs = await connect_mcp_servers(stack, mcp_sids)
            
            if space_id:
                from app.rag import get_space_tool
                tool_defs.append(get_space_tool(space_id))
            
            genai_tools = None
            if tool_defs:
                function_declarations = []
                for t in tool_defs:
                    if isinstance(t, dict) and "function" in t:
                        fn = t["function"]
                        params = fn.get("parameters", {})
                        if isinstance(params, dict) and "$schema" in params:
                            params = dict(params)
                            params.pop("$schema", None)
                        
                        function_declarations.append(
                            types.FunctionDeclaration(
                                name=fn.get("name"),
                                description=fn.get("description", ""),
                                parameters=params,
                            )
                        )
                if function_declarations:
                    genai_tools = [types.Tool(function_declarations=function_declarations)]

            # Build Gemini Live config
            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                system_instruction=types.Content(
                    parts=[types.Part(text=persona)],
                    role="user",
                ),
                tools=genai_tools,
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice_name,
                        )
                    )
                ),
            )

            client = genai.Client(api_key=GOOGLE_API_KEY)

            async with client.aio.live.connect(model=VOICE_MODEL, config=config) as session:
                await ws.send_json({"type": "status", "message": "connected"})
                stop_event = asyncio.Event()

                async def forward_browser_audio():
                    """Read PCM from browser WebSocket → send to Gemini."""
                    try:
                        while not stop_event.is_set():
                            raw = await ws.receive()
                            if raw.get("type") == "websocket.disconnect":
                                stop_event.set()
                                return

                            # Binary = raw PCM, JSON = control message
                            if "bytes" in raw:
                                pcm = raw["bytes"]
                                await session.send(
                                    input=types.LiveClientRealtimeInput(
                                        media_chunks=[
                                            types.Blob(
                                                data=pcm,
                                                mime_type="audio/pcm;rate=16000",
                                            )
                                        ]
                                    )
                                )
                            elif "text" in raw:
                                msg = json.loads(raw["text"])
                                if msg.get("type") == "end":
                                    stop_event.set()
                                    return
                    except WebSocketDisconnect:
                        stop_event.set()
                    except Exception as e:
                        print(f"[voice] forward_browser_audio error: {e}")
                        stop_event.set()

                async def forward_gemini_audio():
                    """Read audio from Gemini → send to browser WebSocket."""
                    try:
                        while not stop_event.is_set():
                            async for response in session.receive():
                                if stop_event.is_set():
                                    return
                                    
                                # Handle MCP tool calls
                                if getattr(response, "tool_call", None) and getattr(response.tool_call, "function_calls", None):
                                    function_responses = []
                                    for fc in response.tool_call.function_calls:
                                        await ws.send_json({
                                            "type": "status",
                                            "message": f"Using tool: {fc.name}..."
                                        })
                                        tool_res = {"error": "Tool not found"}
                                        if fc.name in tool_sessions:
                                            try:
                                                mcp_session = tool_sessions[fc.name]
                                                res = await mcp_session.call_tool(fc.name, fc.args or {})
                                                tool_res = {"result": res.content[0].text if res.content else "Success"}
                                            except Exception as e:
                                                tool_res = {"error": str(e)}
                                                
                                        function_responses.append(
                                            types.FunctionResponse(
                                                id=fc.id,
                                                name=fc.name,
                                                response=tool_res
                                            )
                                        )
                                    # Send results back to Gemini Live
                                    await session.send(input=types.LiveClientToolResponse(
                                        function_responses=function_responses
                                    ))
                                    continue

                                if response.server_content and response.server_content.model_turn:
                                    for part in response.server_content.model_turn.parts:
                                        if part.inline_data and part.inline_data.data:
                                            # Audio data
                                            await ws.send_json({
                                                "type": "audio",
                                                "data": base64.b64encode(part.inline_data.data).decode("ascii"),
                                            })
                                        elif part.text:
                                            # Text transcript
                                            await ws.send_json({
                                                "type": "transcript",
                                                "text": part.text,
                                            })

                                # Turn complete signal
                                if (
                                    response.server_content
                                    and response.server_content.turn_complete
                                ):
                                    await ws.send_json({"type": "turn_complete"})

                    except WebSocketDisconnect:
                        pass
                    except Exception as e:
                        print(f"[voice] forward_gemini_audio error: {e}")
                    finally:
                        stop_event.set()

                # Run both directions concurrently
                await asyncio.gather(
                    forward_browser_audio(),
                    forward_gemini_audio(),
                    return_exceptions=True,
                )

    except Exception as e:
        traceback.print_exc()
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


@router.get("/api/voices")
async def list_voices():
    """Return available voice presets."""
    return [{"id": k, "name": v} for k, v in VOICE_PRESETS.items()]
