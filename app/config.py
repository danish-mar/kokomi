import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ────────────────────────────────────────────────────────
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GOOGLE_API_KEY: str | None = os.getenv("GOOGLE_API_KEY")

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

os.makedirs(AVATARS_DIR, exist_ok=True)
os.makedirs(SPACES_DIR, exist_ok=True)

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
    "inject_time": False,
    "embedding_model": "models/gemini-embedding-2",
    "whatsapp_enabled": False,
    "whatsapp_character_id": "kokomi",
    "whatsapp_api_url": "http://localhost:3013",
    "whatsapp_show_thinking": True,
}
