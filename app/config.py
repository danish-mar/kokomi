import os
from dotenv import load_dotenv
from pathlib import Path
import tomli
load_dotenv()

# Load project version once at startup
try:
    with open(Path(__file__).parent / "../pyproject.toml", "rb") as f:
        _pyproject_data = tomli.load(f)
        VERSION = _pyproject_data["project"]["version"]
        RELEASE_NAME = _pyproject_data["project"].get("release-name", "")
except Exception:
    VERSION = "5.0.1"
    RELEASE_NAME = "everlasting moonglow"

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
JSON_DIR = os.path.join(DATA_DIR, "json")
os.makedirs(JSON_DIR, exist_ok=True)

CONVOS_FILE = os.path.join(JSON_DIR, "conversations.json")
CHARS_FILE = os.path.join(JSON_DIR, "characters.json")
MCP_FILE = os.path.join(JSON_DIR, "mcp_servers.json")
USER_PREFS_FILE = os.path.join(JSON_DIR, "user_prefs.json")
FOLDERS_FILE = os.path.join(JSON_DIR, "folders.json")
SPACES_FILE = os.path.join(JSON_DIR, "spaces.json")
AVATARS_DIR = os.path.join(DATA_DIR, "avatars")
SPACES_DIR = os.path.join(DATA_DIR, "spaces")
INSIGHTS_FILE = os.path.join(JSON_DIR, "insights.jsonl")

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
    "custom_name": "Custom",
    "custom_base_url": "http://localhost:8080/v1",
    "custom_api_key": "",
    "custom_model": "local-model",
    # Saved custom-provider presets: [{id, name, base_url, api_key, model}, ...].
    # Purely a frontend convenience for switching between several OpenAI-compatible
    # endpoints — picking one just copies its fields onto the plain custom_* prefs
    # above (and the title_/atlas_ equivalents), which is what get_llm() reads.
    "custom_providers": [],
    "active_custom_provider_id": None,
    "nvidia_model": "nvidia/llama-3.3-nemotron-super-49b-v1",
    "atlas_llm_provider": "google",
    "atlas_model_name": "gemini-2.5-flash",
    "atlas_nvidia_model": "nvidia/llama-3.3-nemotron-super-49b-v1",
    "atlas_custom_base_url": "http://localhost:8080/v1",
    "atlas_custom_api_key": "",
    "atlas_custom_model": "local-model",
    "atlas_active_custom_provider_id": None,
    # Model that names conversations. Titles are one short line, so this
    # defaults to a small fast model rather than the conversational one.
    "title_llm_provider": "groq",
    "title_model_name": "meta-llama/llama-4-scout-17b-16e-instruct",
    "title_nvidia_model": "nvidia/llama-3.3-nemotron-super-49b-v1",
    "title_custom_base_url": "http://localhost:8080/v1",
    "title_custom_api_key": "",
    "title_custom_model": "local-model",
    "title_active_custom_provider_id": None,
    "inject_time": False,
    # RAG embeddings. Provider selects which service embeds documents/queries.
    # NOTE: changing the embedding model/provider invalidates existing vectors —
    # spaces must be re-indexed (the app detects the mismatch and offers it).
    "embedding_provider": "google",  # "google" | "nvidia"
    "embedding_model": "gemini-embedding-001",  # stable GA Gemini embedding model
    "nvidia_embedding_model": "nvidia/nv-embedqa-e5-v5",  # NVIDIA NIM retrieval embedding
    "whatsapp_enabled": False,
    "whatsapp_character_id": "kokomi",
    "whatsapp_api_url": os.getenv("WHATSAPP_API_URL", "http://localhost:3013"),
    "whatsapp_show_thinking": True,
    "telegram_enabled": False,
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "telegram_character_id": "kokomi",
    "telegram_show_thinking": False,
    "telegram_allowed_users": [],
    "telegram_history_limit": 10,
    "telegram_use_webhook": False,
    "tavily_api_key": os.getenv("TAVILY_API_KEY", ""),
    "web_search_enabled": False,
    "image_search_enabled": True,
    "browser_redirect_enabled": True,
    "user_name": "User",
    "user_avatar": None,
    "debug_mode": False,
    "insights": True,
    "artifacts": True,
    "memory_enabled": True,
    "execution_engine": "docker",
    "docker_connection": "local",
    "docker_remote_url": "tcp://192.168.1.100:2375",
    "docker_image": "kokomi-agent-base",
    "setup_completed": False,
    "tour_completed": False,
    "groq_api_key": os.getenv("GROQ_API_KEY", ""),
    "google_api_key": os.getenv("GOOGLE_API_KEY", ""),
    "nvidia_api_key": os.getenv("NVIDIA_API_KEY", ""),
    "max_tool_rounds": 8
}

