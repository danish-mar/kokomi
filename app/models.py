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
    model_name: str
    user_persona: str
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
    tavily_api_key: Optional[str] = ""
    web_search_enabled: bool = False
    browser_redirect_enabled: bool = True
    user_name: Optional[str] = "User"
    user_avatar: Optional[str] = None
    debug_mode: bool = False
    insights: bool = True
    artifacts: bool = True
