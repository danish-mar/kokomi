"""
app/db.py — SQLite async database engine and table definitions.

All tables are created on first boot via init_db().
The _session() context manager provides a managed async session for use in
synchronous-style code (callers await it directly; FastAPI route deps use
get_db_session as a dependency).
"""
import json
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import (
    Column, Integer, Text, Boolean, Float, DateTime,
    ForeignKey, Index, text,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# ─── Engine ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # If the user passed postgres:// or postgresql://, async SQLAlchemy requires postgresql+asyncpg://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+asyncpg://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(DATABASE_URL, echo=False)

    AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
else:
    # Uses the local SQLite database as fallback
    DB_DIR = os.path.join("data", "database")
    os.makedirs(DB_DIR, exist_ok=True)
    DB_PATH = os.path.join(DB_DIR, "kokomi.db")

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
        # WAL mode: dramatically better concurrent read/write performance
        echo=False,
    )

    AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


# ─── Base ─────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ─── Tables ───────────────────────────────────────────────────────────────────

# ─── UserRow & SharedApiKeyRow ──────────────────────────────────────────────────
class UserRow(Base):
    __tablename__ = "users"

    id            = Column(Text, primary_key=True)
    username      = Column(Text, unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    role          = Column(Text, nullable=False, default="Standard user") # "Endministrator" or "Standard user"
    display_name  = Column(Text, nullable=True)
    avatar        = Column(Text, nullable=True)
    created_at    = Column(Text, nullable=True)
    updated_at    = Column(Text, nullable=True)


class SharedApiKeyRow(Base):
    __tablename__ = "shared_api_keys"

    id             = Column(Text, primary_key=True)
    owner_username = Column(Text, nullable=False, index=True)
    key_name       = Column(Text, nullable=False)
    provider       = Column(Text, nullable=False) # groq, google, nvidia, openai, tavily, custom
    api_key        = Column(Text, nullable=False)
    shared_with    = Column(Text, nullable=False, default="[]") # JSON list: ["*"] for all, or ["user1", "user2"]
    created_at     = Column(Text, nullable=True)
    updated_at     = Column(Text, nullable=True)


# ─── UserPrefersRow ───────────────────────────────────────────────────────────
class UserPrefsRow(Base):
    __tablename__ = "user_prefs"

    id = Column(Text, primary_key=True, default="default")
    data = Column(Text, nullable=False, default="{}") # holds the full json blob
    updated_at = Column(Text, nullable=True)

class ConversationRow(Base):
    __tablename__ = "conversations"

    id          = Column(Text, primary_key=True)
    user_id     = Column(Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    title       = Column(Text, nullable=False, default="Untitled")
    character_id = Column(Text, nullable=True)
    participants = Column(Text, nullable=False, default="[]")  # JSON list
    is_anonymous = Column(Integer, nullable=False, default=0)  # 0=False 1=True
    folder_id   = Column(Text, nullable=True)
    updated_at  = Column(Text, nullable=True)
    last_active = Column(Text, nullable=True)
    branches    = Column(Text, nullable=True)  # JSON {group_id: {anchor_index, variants, active_index}}


class MessageRow(Base):
    __tablename__ = "messages"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Text, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role            = Column(Text, nullable=False)          # user / assistant
    content         = Column(Text, nullable=False, default="")
    thinking        = Column(Text, nullable=True)           # extended reasoning chain
    tool_calls      = Column(Text, nullable=True)           # JSON list [{name,args,result}]
    artifacts       = Column(Text, nullable=True)           # JSON list [{id,type,title,content,timestamp}]
    model           = Column(Text, nullable=True)           # e.g. "qwen/qwen3-32b"
    character_id    = Column(Text, nullable=True)
    character_name  = Column(Text, nullable=True)
    metrics         = Column(Text, nullable=True)           # JSON {tps,ttft,...} — kept for compat
    timestamp       = Column(Text, nullable=True)
    group_id        = Column(Text, nullable=True)           # ChatGPT-style edit/regenerate branch group

    __table_args__ = (
        Index("ix_messages_conversation_id", "conversation_id"),
    )


class CharacterRow(Base):
    __tablename__ = "characters"

    id             = Column(Text, primary_key=True)
    user_id        = Column(Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name           = Column(Text, nullable=False)
    persona        = Column(Text, nullable=True, default="")
    avatar         = Column(Text, nullable=True)
    voice          = Column(Text, nullable=True)
    memory_enabled = Column(Integer, nullable=True)   # 1/0/NULL
    google_model   = Column(Text, nullable=True, default="default")
    groq_model     = Column(Text, nullable=True, default="default")
    nvidia_model   = Column(Text, nullable=True)
    custom_model   = Column(Text, nullable=True, default="default")
    mcp_servers    = Column(Text, nullable=False, default="[]")  # JSON list
    selected_tools = Column(Text, nullable=False, default="[]")  # JSON list
    created_at     = Column(Text, nullable=True)


class McpServerRow(Base):
    __tablename__ = "mcp_servers"

    id          = Column(Text, primary_key=True)
    name        = Column(Text, nullable=False)
    transport   = Column(Text, nullable=True)
    command     = Column(Text, nullable=True)
    args        = Column(Text, nullable=False, default="[]")  # JSON list
    env         = Column(Text, nullable=False, default="{}")  # JSON dict
    url         = Column(Text, nullable=True)
    icon        = Column(Text, nullable=True)
    enabled     = Column(Integer, nullable=False, default=1)
    created_at  = Column(Text, nullable=True)


class WorkflowRow(Base):
    __tablename__ = "workflows"

    run_id       = Column(Text, primary_key=True)
    run_title    = Column(Text, nullable=True)
    run_icon     = Column(Text, nullable=True)
    user_id      = Column(Text, nullable=True)
    user_request = Column(Text, nullable=True)
    status       = Column(Text, nullable=True)      # running/complete/failed
    final_result = Column(Text, nullable=True)
    storage_dir  = Column(Text, nullable=True)
    created_at   = Column(Text, nullable=True)
    started_at   = Column(Text, nullable=True)
    completed_at = Column(Text, nullable=True)
    # All heavy nested runtime state packed into one blob
    state_json   = Column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_workflows_status", "status"),
    )


class InsightRow(Base):
    __tablename__ = "insights"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    timestamp         = Column(Text, nullable=True)
    model             = Column(Text, nullable=True)
    source            = Column(Text, nullable=True)   # "chat" / "workflow" / NULL
    session_id        = Column(Text, nullable=True)
    tps               = Column(Float, nullable=True)
    ttft              = Column(Float, nullable=True)
    prompt_tokens     = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    context_used      = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_insights_timestamp", "timestamp"),
        Index("ix_insights_model", "model"),
    )


class SpaceRow(Base):
    __tablename__ = "spaces"

    id          = Column(Text, primary_key=True)
    name        = Column(Text, nullable=False)
    description = Column(Text, nullable=True, default="")
    icon        = Column(Text, nullable=True)
    files       = Column(Text, nullable=False, default="[]")  # JSON list [{id,filename,size,uploaded_at}]
    created_at  = Column(Text, nullable=True)


class AgentTemplateRow(Base):
    __tablename__ = "agent_templates"

    id                      = Column(Text, primary_key=True)
    name                    = Column(Text, nullable=False)
    purpose                 = Column(Text, nullable=True)
    allowed_tools           = Column(Text, nullable=False, default="[]")  # JSON list
    system_prompt_template  = Column(Text, nullable=True)
    expected_output_schema  = Column(Text, nullable=True)  # JSON blob
    timeout                 = Column(Integer, nullable=True)
    retry_limit             = Column(Integer, nullable=True)
    max_iterations          = Column(Integer, nullable=True)


class FolderRow(Base):
    __tablename__ = "folders"

    id               = Column(Text, primary_key=True)
    user_id          = Column(Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name             = Column(Text, nullable=False)
    icon             = Column(Text, nullable=True)
    conversation_ids = Column(Text, nullable=False, default="[]")  # JSON list
    created_at       = Column(Text, nullable=True)


class TritonDeviceRow(Base):
    """A paired Triton client machine (the AI's remote 'hands' on a user device)."""
    __tablename__ = "triton_devices"

    id           = Column(Text, primary_key=True)  # stable device_id chosen by the client
    name         = Column(Text, nullable=False)     # human label (hostname by default)
    platform     = Column(Text, nullable=True)       # "linux" | "windows" | ...
    description  = Column(Text, nullable=True)        # user-written note so the AI knows what this machine is
    token_hash   = Column(Text, nullable=False)      # sha256 of the device token (client keeps raw)
    capabilities = Column(Text, nullable=False, default="[]")  # JSON list advertised by client
    paired_at    = Column(Text, nullable=True)
    last_seen    = Column(Text, nullable=True)


class InteractiveStateRow(Base):
    """Persisted checkbox/interactive state keyed by stable content hash (iid)."""
    __tablename__ = "interactive_states"

    iid        = Column(Text, primary_key=True)   # contentHash(title+subtitle+inner)
    state_json = Column(Text, nullable=False, default="[]")  # JSON [checked_idx, ...]
    updated_at = Column(Text, nullable=True)


# ─── Lifecycle ────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables and enable WAL mode on first boot. Idempotent."""
    async with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))
            await conn.execute(text("PRAGMA foreign_keys=ON"))
        
        await conn.run_sync(Base.metadata.create_all)
        
        if engine.dialect.name == "sqlite":
            # Legacy SQLite schema migrations (Postgres has these columns already via create_all)
            try:
                await conn.execute(text("ALTER TABLE characters ADD COLUMN selected_tools TEXT DEFAULT '[]'"))
            except Exception:
                pass

            try:
                await conn.execute(text("ALTER TABLE conversations ADD COLUMN branches TEXT"))
            except Exception:
                pass
            try:
                await conn.execute(text("ALTER TABLE messages ADD COLUMN group_id TEXT"))
            except Exception:
                pass

            try:
                await conn.execute(text("ALTER TABLE triton_devices ADD COLUMN last_seen TEXT"))
            except Exception:
                pass

            try:
                await conn.execute(text("ALTER TABLE triton_devices ADD COLUMN description TEXT"))
            except Exception:
                pass

            try:
                await conn.execute(text("ALTER TABLE characters ADD COLUMN custom_model TEXT DEFAULT 'default'"))
            except Exception:
                pass
            try:
                await conn.execute(text(
                    "UPDATE characters SET custom_model = local_model "
                    "WHERE (custom_model IS NULL OR custom_model = 'default') "
                    "AND local_model IS NOT NULL AND local_model != 'default'"
                ))
            except Exception:
                pass

        # Multi-user schema updates and migrations (compatible with SQLite and PostgreSQL)
        for tbl in ["conversations", "folders", "characters"]:
            try:
                await conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS user_id TEXT"))
            except Exception:
                try:
                    await conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN user_id TEXT"))
                except Exception:
                    pass
            try:
                await conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{tbl}_user_id ON {tbl}(user_id)"))
            except Exception:
                pass

        # Backfill existing conversations and folders without user_id to the primary admin (denizuh)
        try:
            await conn.execute(text(
                "UPDATE conversations SET user_id = (SELECT id FROM users WHERE username = 'denizuh' LIMIT 1) "
                "WHERE user_id IS NULL"
            ))
        except Exception:
            pass

        try:
            await conn.execute(text(
                "UPDATE folders SET user_id = (SELECT id FROM users WHERE username = 'denizuh' LIMIT 1) "
                "WHERE user_id IS NULL"
            ))
        except Exception:
            pass


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession, None]:
    """Managed async session: commits on success, rolls back on error."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ─── Helpers ─────────────────────────────────────────────────────────────────

def j_dumps(obj) -> str:
    """Serialize to JSON string for storage. None -> NULL."""
    if obj is None:
        return None
    return json.dumps(obj, default=str)


def j_loads(s: str | None):
    """Deserialize JSON string from storage. None -> None."""
    if s is None:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None
