import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ────────────────────────────────────────────────────────
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GOOGLE_API_KEY: str | None = os.getenv("GOOGLE_API_KEY")
TAVILY_API_KEY: str | None = os.getenv("TAVILY_API_KEY")
NVIDIA_API_KEY: str | None = os.getenv("NVIDIA_API_KEY")
QDRANT_URL: str = os.getenv("QDRANT_URL", "http://localhost:6333")

# ── Authentication ───────────────────────────────────────────────────
AUTH_USERNAME: str = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD: str = os.getenv("AUTH_PASSWORD", "admin")  # Default password
JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-me")
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440")) # Default 24 hours


# ── Data paths ───────────────────────────────────────────────────────
DATA_DIR = "data"
CONVOS_FILE = os.path.join(DATA_DIR, "conversations.json")
CHARS_FILE = os.path.join(DATA_DIR, "characters.json")
MCP_FILE = os.path.join(DATA_DIR, "mcp_servers.json")
USER_PREFS_FILE = os.path.join(DATA_DIR, "user_prefs.json")
FOLDERS_FILE = os.path.join(DATA_DIR, "folders.json")
SPACES_FILE = os.path.join(DATA_DIR, "spaces.json")
AVATARS_DIR = os.path.join(DATA_DIR, "avatars")
SPACES_DIR = os.path.join(DATA_DIR, "spaces")
INSIGHTS_FILE = os.path.join(DATA_DIR, "insights.jsonl")

os.makedirs(AVATARS_DIR, exist_ok=True)
os.makedirs(SPACES_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# ── Default preferences ──────────────────────────────────────────────
DEFAULT_PREFS: dict = {
    "model_name": "qwen-2.5-32b",
    "user_persona": "",
    "theme": "dark",
    "dynamic_suggestions": True,
    "streaming_mode": True,
    "llm_provider": "groq",
    "local_url": "http://localhost:8080/v1",
    "local_model": "local-model",
    "nvidia_model": "nvidia/llama-3.3-nemotron-super-49b-v1",
    "atlas_llm_provider": "google",
    "atlas_model_name": "gemini-2.5-flash",
    "atlas_nvidia_model": "nvidia/llama-3.3-nemotron-super-49b-v1",
    "atlas_local_url": "http://localhost:8080/v1",
    "atlas_local_model": "local-model",
    "inject_time": False,
    "embedding_model": "models/gemini-embedding-2",
    "whatsapp_enabled": False,
    "whatsapp_character_id": "kokomi",
    "whatsapp_api_url": os.getenv("WHATSAPP_API_URL", "http://localhost:3013"),
    "whatsapp_show_thinking": True,
    "tavily_api_key": os.getenv("TAVILY_API_KEY", ""),
    "web_search_enabled": False,
    "browser_redirect_enabled": True,
    "user_name": "User",
    "user_avatar": None,
    "debug_mode": False,
    "insights": True,
    "artifacts": True,
    "memory_enabled": True
}

