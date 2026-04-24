from typing import Optional, List
from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    character_id: Optional[str] = "kokomi"
    participants: Optional[List[str]] = None
    space_id: Optional[str] = None


class MCPServerCreate(BaseModel):
    name: str
    transport: str = "stdio"
    command: Optional[str] = None
    args: Optional[List[str]] = []
    env: Optional[dict] = {}
    url: Optional[str] = None
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
    embedding_model: Optional[str] = "models/gemini-embedding-2"
    whatsapp_enabled: bool = False
    whatsapp_character_id: str = "kokomi"
    whatsapp_api_url: str = "http://localhost:3013"
    whatsapp_show_thinking: bool = True
