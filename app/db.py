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

class ConversationRow(Base):
    __tablename__ = "conversations"

    id          = Column(Text, primary_key=True)
    title       = Column(Text, nullable=False, default="Untitled")
    character_id = Column(Text, nullable=True)
    participants = Column(Text, nullable=False, default="[]")  # JSON list
    is_anonymous = Column(Integer, nullable=False, default=0)  # 0=False 1=True
    folder_id   = Column(Text, nullable=True)
    updated_at  = Column(Text, nullable=True)
    last_active = Column(Text, nullable=True)


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

    __table_args__ = (
        Index("ix_messages_conversation_id", "conversation_id"),
    )


class CharacterRow(Base):
    __tablename__ = "characters"

    id             = Column(Text, primary_key=True)
    name           = Column(Text, nullable=False)
    persona        = Column(Text, nullable=True, default="")
    avatar         = Column(Text, nullable=True)
    voice          = Column(Text, nullable=True)
    memory_enabled = Column(Integer, nullable=True)   # 1/0/NULL
    google_model   = Column(Text, nullable=True, default="default")
    groq_model     = Column(Text, nullable=True, default="default")
    nvidia_model   = Column(Text, nullable=True)
    local_model    = Column(Text, nullable=True, default="default")
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
    name             = Column(Text, nullable=False)
    icon             = Column(Text, nullable=True)
    conversation_ids = Column(Text, nullable=False, default="[]")  # JSON list
    created_at       = Column(Text, nullable=True)


# ─── Lifecycle ────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables and enable WAL mode on first boot. Idempotent."""
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)
        
        # Schema migration: Ensure selected_tools column exists in characters table
        try:
            await conn.execute(text("ALTER TABLE characters ADD COLUMN selected_tools TEXT DEFAULT '[]'"))
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
