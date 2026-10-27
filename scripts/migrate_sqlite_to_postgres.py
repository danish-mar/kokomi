"""
scripts/migrate_sqlite_to_postgres.py — Migrate all existing SQLite data to PostgreSQL.

Reads:  data/database/kokomi.db (SQLite)
Writes: PostgreSQL (configured by DATABASE_URL in .env)

Idempotent: Uses ON CONFLICT DO NOTHING so it is safe to run multiple times.
Updates PostgreSQL sequences for autoincrement ID columns (messages, insights).
"""
import os
import sys
import asyncio
import sqlite3

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from app.db import (
    engine, _session, init_db,
    ConversationRow, MessageRow, CharacterRow, McpServerRow,
    WorkflowRow, InsightRow, SpaceRow, AgentTemplateRow,
    FolderRow, TritonDeviceRow, UserPrefsRow,
    UserRow, SharedApiKeyRow
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text


SQLITE_PATH = os.path.join("data", "database", "kokomi.db")


async def migrate():
    if not os.path.exists(SQLITE_PATH):
        print(f"❌ SQLite database not found at {SQLITE_PATH}")
        return

    if engine.dialect.name != "postgresql":
        print(f"❌ DATABASE_URL is not pointing to PostgreSQL (dialect: {engine.dialect.name}).")
        print("Please set DATABASE_URL in .env, e.g.:")
        print("DATABASE_URL=postgresql://kokomi:kokomi@localhost:5432/kokomi")
        return

    print("🚀 Initializing PostgreSQL tables...")
    await init_db()

    print(f"📦 Reading SQLite database from {SQLITE_PATH}...")
    s_conn = sqlite3.connect(SQLITE_PATH)
    s_conn.row_factory = sqlite3.Row
    s_cur = s_conn.cursor()

    def get_rows(table_name):
        try:
            return s_cur.execute(f"SELECT * FROM {table_name}").fetchall()
        except sqlite3.OperationalError:
            return []

    def filter_row(row_dict, model_cls):
        valid = {c.name for c in model_cls.__table__.columns}
        return {k: v for k, v in row_dict.items() if k in valid}

    async with _session() as sess:
        # 1. Users (must come first — folders/conversations FK to users.id)
        user_rows = get_rows("users")
        print(f"  • Users: migrating {len(user_rows)} rows...")
        for r in user_rows:
            d = filter_row(dict(r), UserRow)
            stmt = pg_insert(UserRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

        # 2. User Prefs
        prefs_rows = get_rows("user_prefs")
        print(f"  • User Prefs: migrating {len(prefs_rows)} rows...")
        for r in prefs_rows:
            d = filter_row(dict(r), UserPrefsRow)
            stmt = pg_insert(UserPrefsRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

        # 3. Shared API Keys
        key_rows = get_rows("shared_api_keys")
        print(f"  • Shared API Keys: migrating {len(key_rows)} rows...")
        for r in key_rows:
            d = filter_row(dict(r), SharedApiKeyRow)
            stmt = pg_insert(SharedApiKeyRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

        # 4. Characters
        char_rows = get_rows("characters")
        print(f"  • Characters: migrating {len(char_rows)} rows...")
        for r in char_rows:
            d = filter_row(dict(r), CharacterRow)
            stmt = pg_insert(CharacterRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

        # 5. Folders (FK → users.id)
        folder_rows = get_rows("folders")
        print(f"  • Folders: migrating {len(folder_rows)} rows...")
        for r in folder_rows:
            d = filter_row(dict(r), FolderRow)
            stmt = pg_insert(FolderRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

        # 6. Conversations (FK → users.id)
        conv_rows = get_rows("conversations")
        print(f"  • Conversations: migrating {len(conv_rows)} rows...")
        for r in conv_rows:
            d = filter_row(dict(r), ConversationRow)
            stmt = pg_insert(ConversationRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

        # 7. Messages (autoincrement ID, FK → conversations.id)
        msg_rows = get_rows("messages")
        print(f"  • Messages: migrating {len(msg_rows)} rows...")
        for r in msg_rows:
            d = filter_row(dict(r), MessageRow)
            stmt = pg_insert(MessageRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

        # 8. MCP Servers
        mcp_rows = get_rows("mcp_servers")
        print(f"  • MCP Servers: migrating {len(mcp_rows)} rows...")
        for r in mcp_rows:
            d = filter_row(dict(r), McpServerRow)
            stmt = pg_insert(McpServerRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

        # 9. Workflows
        wf_rows = get_rows("workflows")
        print(f"  • Workflows: migrating {len(wf_rows)} rows...")
        for r in wf_rows:
            d = filter_row(dict(r), WorkflowRow)
            stmt = pg_insert(WorkflowRow).values(**d).on_conflict_do_nothing(index_elements=["run_id"])
            await sess.execute(stmt)

        # 10. Spaces
        space_rows = get_rows("spaces")
        print(f"  • Spaces: migrating {len(space_rows)} rows...")
        for r in space_rows:
            d = filter_row(dict(r), SpaceRow)
            stmt = pg_insert(SpaceRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

        # 11. Agent Templates
        tmpl_rows = get_rows("agent_templates")
        print(f"  • Agent Templates: migrating {len(tmpl_rows)} rows...")
        for r in tmpl_rows:
            d = filter_row(dict(r), AgentTemplateRow)
            stmt = pg_insert(AgentTemplateRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

        # 12. Triton Devices
        triton_rows = get_rows("triton_devices")
        print(f"  • Triton Devices: migrating {len(triton_rows)} rows...")
        for r in triton_rows:
            d = filter_row(dict(r), TritonDeviceRow)
            stmt = pg_insert(TritonDeviceRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

        # 13. Insights (autoincrement ID)
        insight_rows = get_rows("insights")
        print(f"  • Insights: migrating {len(insight_rows)} rows...")
        for r in insight_rows:
            d = filter_row(dict(r), InsightRow)
            stmt = pg_insert(InsightRow).values(**d).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)

    s_conn.close()

    # Reset postgres sequences so new inserts don't collide with existing IDs
    async with engine.begin() as conn:
        print("🔧 Updating PostgreSQL autoincrement sequences...")
        try:
            await conn.execute(text("SELECT setval(pg_get_serial_sequence('messages', 'id'), coalesce(max(id), 1)) FROM messages"))
        except Exception as e:
            print(f"  ⚠ Could not set sequence for messages: {e}")

        try:
            await conn.execute(text("SELECT setval(pg_get_serial_sequence('insights', 'id'), coalesce(max(id), 1)) FROM insights"))
        except Exception as e:
            print(f"  ⚠ Could not set sequence for insights: {e}")

    print("🎉 SQLite to PostgreSQL migration finished successfully!")


if __name__ == "__main__":
    asyncio.run(migrate())
