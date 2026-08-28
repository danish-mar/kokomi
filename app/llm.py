import re
import uuid
from typing import Optional

from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from google import genai
from google.genai import types

from app.config import GROQ_API_KEY, GOOGLE_API_KEY, NVIDIA_API_KEY

try:
    from langchain_nvidia_ai_endpoints import ChatNVIDIA
except ImportError:
    ChatNVIDIA = None


# ── Title generation LLM (always Groq / lightweight) ─────────────────

title_llm = (
    ChatGroq(
        model_name="meta-llama/llama-4-scout-17b-16e-instruct",
        temperature=0.3,
        groq_api_key=GROQ_API_KEY,
    )
    if GROQ_API_KEY
    else None
)


# ── Gemini direct wrapper ─────────────────────────────────────────────

class GeminiDirectLLM:
    """
    Direct wrapper for google-genai SDK mimicking a LangChain-style interface.

    We reuse a single async_client (no custom http_options) for both
    ainvoke and astream to avoid 404 errors on the v1beta endpoint.
    """

    def __init__(self, model_name: str, api_key: str, temperature: float = 0.7):
        self.model_name = model_name
        self.api_key = api_key
        self.temperature = temperature
        self.tools = None
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
                parts = []
                if m.content:
                    parts.append({"text": m.content})
                if getattr(m, "tool_calls", None):
                    for tc in m.tool_calls:
                        parts.append({"function_call": {"name": tc["name"], "args": tc["args"]}})
                if parts:
                    genai_msgs.append({"role": "model", "parts": parts})
            elif isinstance(m, ToolMessage):
                name = m.name or "unknown_tool"
                genai_msgs.append({"role": "user", "parts": [{"function_response": {"name": name, "response": {"result": m.content}}}]})
        return genai_msgs, system_instruction

    def _make_config(self, system_instruction):
        genai_tools = None
        if self.tools:
            function_declarations = []
            for t in self.tools:
                if isinstance(t, dict) and "function" in t:
                    fn = t["function"]
                    
                    # google-genai Pydantic strictly rejects '$schema' or extra fields in Schema
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

        return types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=self.temperature,
            tools=genai_tools,
        )

    async def astream(self, messages):
        contents, system_instruction = self._convert_messages(messages)
        config = self._make_config(system_instruction)

        class _Chunk:
            def __init__(self, content, tool_calls=None, usage=None):
                self.content = content
                self.tool_calls = tool_calls or []
                self.usage = usage
                self.additional_kwargs = {}
            def __add__(self, other):
                return _Chunk(
                    self.content + other.content,
                    self.tool_calls + getattr(other, "tool_calls", []),
                    other.usage or self.usage
                )

        async for chunk in await self.async_client.aio.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=config,
        ):
            # Extract tool calls if any
            t_calls = []
            if chunk.candidates and chunk.candidates[0].content.parts:
                for part in chunk.candidates[0].content.parts:
                    if part.function_call:
                        t_calls.append({
                            "name": part.function_call.name,
                            "args": part.function_call.args,
                            "id": f"call_{uuid.uuid4().hex[:8]}"
                        })
            
            usage = None
            if chunk.usage_metadata:
                usage = {
                    "prompt_tokens": chunk.usage_metadata.prompt_token_count,
                    "completion_tokens": chunk.usage_metadata.candidates_token_count,
                    "total_tokens": chunk.usage_metadata.total_token_count,
                }
                
            yield _Chunk(chunk.text or "", tool_calls=t_calls, usage=usage)

    async def ainvoke(self, messages):
        contents, system_instruction = self._convert_messages(messages)
        config = self._make_config(system_instruction)

        response = await self.async_client.aio.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )

        class _Response:
            def __init__(self, content, tool_calls=None, usage=None):
                self.content = content
                self.tool_calls = tool_calls or []
                self.usage = usage
            def __add__(self, other):
                return _Response(self.content + other.content, self.tool_calls + getattr(other, "tool_calls", []), other.usage or self.usage)

        # Extract tool calls
        t_calls = []
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    t_calls.append({
                        "name": part.function_call.name,
                        "args": part.function_call.args,
                        "id": f"call_{uuid.uuid4().hex[:8]}"
                    })

        usage = None
        if response.usage_metadata:
            usage = {
                "prompt_tokens": response.usage_metadata.prompt_token_count,
                "completion_tokens": response.usage_metadata.candidates_token_count,
                "total_tokens": response.usage_metadata.total_token_count,
            }

        return _Response(response.text, tool_calls=t_calls, usage=usage)

    def bind_tools(self, tools):
        self.tools = tools
        return self


# ── Helpers ───────────────────────────────────────────────────────────

def _normalize_model(name: str) -> str:
    """Strip known prefixes that don't belong in the raw API call."""
    if not name:
        return name
    if name.startswith("models/"):
        name = name[len("models/"):]
    return name


def resolve_character_model(char: dict, provider: str) -> str:
    """Pick the model from a character's per-provider slots.

    Characters store groq_model, google_model, custom_model, nvidia_model.
    Falls back to 'default' which means use the global setting.
    """
    key = f"{provider}_model"              # e.g. "nvidia_model"
    value = char.get(key, "default")
    if value and value != "default":
        return value
    return "default"


def parse_thinking(raw: str):
    """Extract all <think>…</think> blocks from raw LLM output, combining them if multiple exist.
    Also handles unclosed <think> tag at the end.
    """
    thinking_parts = []
    content_parts = []
    
    current = raw
    while True:
        m = re.search(r"<think>(.*?)</think>", current, re.DOTALL)
        if m:
            content_parts.append(current[:m.start()])
            thinking_parts.append(m.group(1))
            current = current[m.end():]
        else:
            break
            
    # Check if there is an unclosed <think> tag in the remaining text
    if "<think>" in current:
        parts = current.split("<think>", 1)
        content_parts.append(parts[0])
        thinking_parts.append(parts[1])
    else:
        content_parts.append(current)
        
    cleaned_content = "".join(content_parts).strip()
    thinking_content = "\n\n".join([p.strip() for p in thinking_parts if p.strip()]).strip()
    
    return cleaned_content, (thinking_content if thinking_content else None)



# ── LLM factory ───────────────────────────────────────────────────────

def get_llm(
    prefs: dict,
    streaming: bool = False,
    model_override: Optional[str] = None,
):
    """Return the appropriate LangChain-compatible LLM based on user prefs."""
    provider = prefs.get("llm_provider", "groq")

    groq_key = prefs.get("groq_api_key") or GROQ_API_KEY
    google_key = prefs.get("google_api_key") or GOOGLE_API_KEY
    nvidia_key = prefs.get("nvidia_api_key") or NVIDIA_API_KEY

    if provider == "custom":
        custom_key = prefs.get("custom_api_key")
        if not custom_key:
            raise ValueError("Custom provider API key not found in preferences")
        base_url = prefs.get("custom_base_url", "http://localhost:8080/v1")
        model = _normalize_model(prefs.get("custom_model", "local-model"))
        if model_override and model_override != "default":
            model = _normalize_model(model_override)
        return ChatOpenAI(
            base_url=base_url,
            api_key=custom_key,
            model_name=model,
            temperature=0.7,
            streaming=streaming,
        )

    elif provider == "nvidia":
        if not nvidia_key:
            raise ValueError("NVIDIA_API_KEY not found in preferences or environment")
        if ChatNVIDIA is None:
            raise ValueError("langchain-nvidia-ai-endpoints package is not installed.")
        
        model = prefs.get("nvidia_model", "nvidia/llama-3.3-nemotron-super-49b-v1")
        if model_override and model_override != "default":
            model = model_override
        return ChatNVIDIA(
            model=model,
            api_key=nvidia_key,
            temperature=0.6,
            # 4096 was cutting off long-form requests (a multi-page document, a long
            # story) well before the model was done — most NIM-hosted models support
            # much larger completions.
            max_completion_tokens=16384,
        )

    elif provider == "google":
        if not google_key:
            raise ValueError("GOOGLE_API_KEY not found in preferences or environment")
        active_model = _normalize_model(prefs.get("model_name", "gemini-2.5-flash"))
        if model_override and model_override != "default":
            active_model = _normalize_model(model_override)
        if "gemini" not in active_model:
            active_model = "gemini-2.5-flash"
            
        return GeminiDirectLLM(
            model_name=active_model,
            api_key=google_key,
            temperature=0.7,
        )

    else:  # groq (default)
        if not groq_key:
            raise ValueError("GROQ_API_KEY not found in preferences or environment")
        active_model = _normalize_model(prefs.get("model_name", "llama-3.3-70b-versatile"))
        if model_override and model_override != "default":
            active_model = _normalize_model(model_override)
        if "gemini" in active_model:
            active_model = "llama-3.3-70b-versatile"
            
        return ChatGroq(
            model_name=active_model,
            temperature=0.7,
            groq_api_key=groq_key,
            streaming=streaming,
        )


def get_title_llm(prefs: dict):
    """Return the LLM used to name conversations, from its own prefs slots.

    Mirrors get_atlas_llm: the title_* keys are remapped onto the generic
    provider keys that get_llm understands, so titles can use a different
    (usually smaller/cheaper) model than the conversational one.
    """
    title_prefs = prefs.copy()
    if "title_llm_provider" in prefs:
        title_prefs["llm_provider"] = prefs["title_llm_provider"]
    if "title_model_name" in prefs:
        title_prefs["model_name"] = prefs["title_model_name"]
    if "title_nvidia_model" in prefs:
        title_prefs["nvidia_model"] = prefs["title_nvidia_model"]
    if "title_custom_base_url" in prefs:
        title_prefs["custom_base_url"] = prefs["title_custom_base_url"]
    if "title_custom_model" in prefs:
        title_prefs["custom_model"] = prefs["title_custom_model"]
    if "title_custom_api_key" in prefs:
        title_prefs["custom_api_key"] = prefs["title_custom_api_key"]
    return get_llm(title_prefs)


def get_atlas_llm(
    prefs: dict,
    streaming: bool = False,
    model_override: Optional[str] = None,
):
    """Return the Atlas-specific LLM using remapped key overrides."""
    atlas_prefs = prefs.copy()
    if "atlas_llm_provider" in prefs:
        atlas_prefs["llm_provider"] = prefs["atlas_llm_provider"]
    if "atlas_model_name" in prefs:
        atlas_prefs["model_name"] = prefs["atlas_model_name"]
    if "atlas_nvidia_model" in prefs:
        atlas_prefs["nvidia_model"] = prefs["atlas_nvidia_model"]
    if "atlas_custom_base_url" in prefs:
        atlas_prefs["custom_base_url"] = prefs["atlas_custom_base_url"]
    if "atlas_custom_model" in prefs:
        atlas_prefs["custom_model"] = prefs["atlas_custom_model"]
    if "atlas_custom_api_key" in prefs:
        atlas_prefs["custom_api_key"] = prefs["atlas_custom_api_key"]
    return get_llm(atlas_prefs, streaming=streaming, model_override=model_override)


# ── Model-tier selection (the composer's brain-icon slider) ───────────

def _tier_prefs(prefs: dict, tier: str) -> dict:
    """Remap fast_*/smart_* keys onto the generic keys get_llm() reads.

    Mirrors get_title_llm/get_atlas_llm's remap; "normal" just passes prefs
    through unchanged since it already uses the generic keys.
    """
    if tier not in ("fast", "smart"):
        return prefs
    prefix = f"{tier}_"
    remapped = prefs.copy()
    for generic_key in (
        "llm_provider", "model_name", "nvidia_model",
        "custom_base_url", "custom_model", "custom_api_key",
    ):
        tiered_key = prefix + generic_key
        if tiered_key in prefs:
            remapped[generic_key] = prefs[tiered_key]
    return remapped


def get_llm_for_tier(
    prefs: dict,
    tier: str = "normal",
    streaming: bool = False,
    model_override: Optional[str] = None,
):
    """Build the LLM for a composer model tier: "fast", "normal", or "smart"."""
    return get_llm(_tier_prefs(prefs, tier), streaming=streaming, model_override=model_override)


def active_model_name(prefs: dict, tier: str = "normal") -> str:
    """The display model name get_llm_for_tier(tier) would actually use."""
    p = _tier_prefs(prefs, tier)
    provider = p.get("llm_provider", "groq")
    if provider == "google":
        return _normalize_model(p.get("model_name", "gemini-2.5-flash"))
    if provider == "custom":
        return _normalize_model(p.get("custom_model", "local-model"))
    if provider == "nvidia":
        return p.get("nvidia_model", "nvidia/llama-3.3-nemotron-super-49b-v1")
    return _normalize_model(p.get("model_name", "llama-3.3-70b-versatile"))


# ── Title generation helper ───────────────────────────────────────────

async def generate_title(user_msg: str, ai_msg: str, prefs: Optional[dict] = None) -> str:
    """Name a conversation.

    Uses the model configured in the title_* prefs when prefs are supplied,
    otherwise the built-in lightweight Groq model.
    """
    try:
        prompt = (
            f"Generate a short title (max 4 words, no quotes) for:\n"
            f"User: {user_msg[:150]}\nAssistant: {ai_msg[:150]}\n\nTitle:"
        )
        llm = get_title_llm(prefs) if prefs else title_llm
        if llm is None:
            raise ValueError("No title model available")
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        title = resp.content.strip().strip("\"'").strip()
        return title[:50] if title else user_msg[:30]
    except Exception:
        return user_msg[:30] + ("..." if len(user_msg) > 30 else "")

