from typing import Optional, List
from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    character_id: Optional[str] = "kokomi"
    participants: Optional[List[str]] = None
    space_id: Optional[str] = None
    is_anonymous: bool = False
    use_web_search: bool = False
    attachments: Optional[List[dict]] = None



class MCPServerCreate(BaseModel):
    name: str
    transport: str = "stdio"
    command: Optional[str] = None
    args: Optional[List[str]] = []
    env: Optional[dict] = {}
    url: Optional[str] = None
    icon: Optional[str] = "fa-plug"
    enabled: bool = True


class FolderCreate(BaseModel):
    name: str
    icon: str = "fa-folder"


class ConversationFolderUpdate(BaseModel):
    folder_id: Optional[str] = None


class PrefsUpdate(BaseModel):
    model_name: Optional[str] = "llama-3.3-70b-versatile"
    user_persona: Optional[str] = ""
    dynamic_suggestions: bool = True
    streaming_mode: bool = True
    inject_time: bool = False
    llm_provider: Optional[str] = "groq"
    local_url: Optional[str] = "http://localhost:8080/v1"
    local_model: Optional[str] = "local-model"
    nvidia_model: Optional[str] = "nvidia/llama-3.3-nemotron-super-49b-v1"
    embedding_model: Optional[str] = "models/gemini-embedding-2"
    whatsapp_enabled: bool = False
    whatsapp_character_id: str = "kokomi"
    whatsapp_api_url: str = "http://localhost:3013"
    whatsapp_show_thinking: bool = True
    telegram_enabled: bool = False
    # NOTE: telegram_bot_token is intentionally NOT here — it is written only via
    # the dedicated /api/telegram/set-token endpoint so bulk prefs saves can never
    # clobber the saved token with an empty/stale value.
    telegram_character_id: str = "kokomi"
    telegram_show_thinking: bool = False
    telegram_allowed_users: Optional[list] = []
    telegram_history_limit: int = 10
    telegram_use_webhook: bool = False
    max_tool_rounds: int = 8
    tavily_api_key: Optional[str] = ""
    web_search_enabled: bool = False
    search_provider: Optional[str] = "tavily"
    searxng_url: Optional[str] = "http://localhost:8080"
    web_scrape_enabled: bool = False
    browser_redirect_enabled: bool = True
    user_name: Optional[str] = "User"
    user_avatar: Optional[str] = None
    debug_mode: bool = False
    insights: bool = True
    artifacts: bool = True
    memory_enabled: bool = True
    theme: Optional[str] = "dark"
    custom_accent: Optional[str] = "#505081"
    custom_wallpaper: Optional[str] = ""
    custom_blur: Optional[str] = "0"
    swatch_midnight: Optional[str] = "#272757"
    swatch_lavender: Optional[str] = "#8686AC"
    swatch_indigo: Optional[str] = "#505081"
    swatch_obsidian: Optional[str] = "#0F0E47"
    
    # Atlas-specific LLM settings
    atlas_llm_provider: Optional[str] = "google"
    atlas_model_name: Optional[str] = "gemini-2.5-flash"
    atlas_nvidia_model: Optional[str] = "nvidia/llama-3.3-nemotron-super-49b-v1"
    atlas_local_url: Optional[str] = "http://localhost:8080/v1"
    atlas_local_model: Optional[str] = "local-model"
    setup_completed: bool = False
    groq_api_key: Optional[str] = ""
    google_api_key: Optional[str] = ""
    nvidia_api_key: Optional[str] = ""
    admin_username: Optional[str] = "admin"
    admin_password: Optional[str] = "admin"
    tour_completed: bool = False
    custom_themes: Optional[list] = []
