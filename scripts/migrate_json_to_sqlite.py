#!/usr/bin/env python3
"""
migrate_json_to_sqlite.py — One-time migration from flat JSON files to SQLite.

Run ONCE from the project root:
    python migrate_json_to_sqlite.py

Safe to run multiple times (INSERT OR IGNORE semantics).
JSON files are left untouched as backups — delete manually after verifying.
"""

import asyncio
import json
import os
import sys
import datetime

# ─── Bootstrap path ──────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db, _session, j_dumps
from app.db import (
    ConversationRow, MessageRow, CharacterRow, McpServerRow,
    SpaceRow, AgentTemplateRow, FolderRow, WorkflowRow, InsightRow,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import select

DATA_DIR = "data"
JSON_DIR = os.path.join(DATA_DIR, "json")
CONVOS_FILE        = os.path.join(JSON_DIR, "conversations.json")
CHARS_FILE         = os.path.join(JSON_DIR, "characters.json")
MCP_FILE           = os.path.join(JSON_DIR, "mcp_servers.json")
FOLDERS_FILE       = os.path.join(JSON_DIR, "folders.json")
SPACES_FILE        = os.path.join(JSON_DIR, "spaces.json")
TEMPLATES_FILE     = os.path.join(JSON_DIR, "agent_templates.json")
WORKFLOWS_FILE     = os.path.join(JSON_DIR, "multi_agent_workflows.json")
INSIGHTS_FILE      = os.path.join(JSON_DIR, "insights.jsonl")

COUNTERS = {}


def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except Exception as e:
            print(f"  ⚠ Could not parse {path}: {e}")
            return {}


def _load_jsonl(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


# ─────────────────────────────────────────────────────────────────────────────

async def migrate_conversations():
    convos = _load_json(CONVOS_FILE)
    if not convos:
        print("  conversations.json: empty or missing, skipping.")
        return

    count_convs = 0
    count_msgs = 0

    async with _session() as sess:
        for conv_id, conv in convos.items():
            # Conversations
            stmt = sqlite_insert(ConversationRow).values(
                id=conv_id,
                title=conv.get("title", "Untitled"),
                character_id=conv.get("character_id"),
                participants=j_dumps(conv.get("participants", [])),
                is_anonymous=int(conv.get("is_anonymous", False)),
                folder_id=conv.get("folder_id"),
                updated_at=str(conv.get("updated_at", "")),
                last_active=conv.get("last_active"),
            ).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)
            count_convs += 1

            # Messages — only insert if this conv has none yet
            existing_msgs = await sess.execute(
                select(MessageRow.id).where(MessageRow.conversation_id == conv_id).limit(1)
            )
            if existing_msgs.first() is not None:
                continue  # already migrated

            for msg in conv.get("messages", []):
                sess.add(MessageRow(
                    conversation_id=conv_id,
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    thinking=msg.get("thinking"),
                    tool_calls=j_dumps(msg.get("tool_calls")),
                    artifacts=j_dumps(msg.get("artifacts")),
                    model=msg.get("model"),
                    character_id=msg.get("character_id"),
                    character_name=msg.get("character_name"),
                    metrics=j_dumps(msg.get("metrics")),
                    timestamp=msg.get("timestamp"),
                ))
                count_msgs += 1

    COUNTERS["conversations"] = count_convs
    COUNTERS["messages"] = count_msgs
    print(f"  ✓ conversations: {count_convs} conversations, {count_msgs} messages")


async def migrate_characters():
    chars = _load_json(CHARS_FILE)
    if not chars:
        print("  characters.json: empty or missing, skipping.")
        return
    count = 0
    async with _session() as sess:
        for cid, char in chars.items():
            stmt = sqlite_insert(CharacterRow).values(
                id=cid,
                name=char.get("name", ""),
                persona=char.get("persona", ""),
                avatar=char.get("avatar"),
                voice=char.get("voice"),
                memory_enabled=int(char["memory_enabled"]) if char.get("memory_enabled") is not None else None,
                google_model=char.get("google_model", "default"),
                groq_model=char.get("groq_model", "default"),
                nvidia_model=char.get("nvidia_model"),
                local_model=char.get("local_model", "default"),
                mcp_servers=j_dumps(char.get("mcp_servers", [])),
                created_at=char.get("created_at"),
            ).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)
            count += 1
    COUNTERS["characters"] = count
    print(f"  ✓ characters: {count}")


async def migrate_mcp_servers():
    mcps = _load_json(MCP_FILE)
    if not mcps:
        print("  mcp_servers.json: empty or missing, skipping.")
        return
    count = 0
    async with _session() as sess:
        for sid, s in mcps.items():
            stmt = sqlite_insert(McpServerRow).values(
                id=sid,
                name=s.get("name", ""),
                transport=s.get("transport"),
                command=s.get("command"),
                args=j_dumps(s.get("args", [])),
                env=j_dumps(s.get("env", {})),
                url=s.get("url"),
                icon=s.get("icon"),
                enabled=int(s.get("enabled", True)),
                created_at=s.get("created_at"),
            ).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)
            count += 1
    COUNTERS["mcp_servers"] = count
    print(f"  ✓ mcp_servers: {count}")


async def migrate_folders():
    folders = _load_json(FOLDERS_FILE)
    if not folders:
        print("  folders.json: empty or missing, skipping (0 folders).")
        return
    count = 0
    async with _session() as sess:
        for fid, f in folders.items():
            stmt = sqlite_insert(FolderRow).values(
                id=fid,
                name=f.get("name", ""),
                icon=f.get("icon"),
                conversation_ids=j_dumps(f.get("conversation_ids", [])),
                created_at=f.get("created_at"),
            ).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)
            count += 1
    COUNTERS["folders"] = count
    print(f"  ✓ folders: {count}")


async def migrate_spaces():
    spaces = _load_json(SPACES_FILE)
    if not spaces:
        print("  spaces.json: empty or missing, skipping.")
        return
    count = 0
    async with _session() as sess:
        for sid, s in spaces.items():
            stmt = sqlite_insert(SpaceRow).values(
                id=sid,
                name=s.get("name", ""),
                description=s.get("description", ""),
                icon=s.get("icon"),
                files=j_dumps(s.get("files", [])),
                created_at=s.get("created_at"),
            ).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)
            count += 1
    COUNTERS["spaces"] = count
    print(f"  ✓ spaces: {count}")


async def migrate_agent_templates():
    templates = _load_json(TEMPLATES_FILE)
    if not templates:
        print("  agent_templates.json: empty or missing, skipping.")
        return
    count = 0
    async with _session() as sess:
        for tid, t in templates.items():
            stmt = sqlite_insert(AgentTemplateRow).values(
                id=tid,
                name=t.get("name", tid),
                purpose=t.get("purpose"),
                allowed_tools=j_dumps(t.get("allowed_tools", [])),
                system_prompt_template=t.get("system_prompt_template"),
                expected_output_schema=j_dumps(t.get("expected_output_schema")),
                timeout=t.get("timeout"),
                retry_limit=t.get("retry_limit"),
                max_iterations=t.get("max_iterations"),
            ).on_conflict_do_nothing(index_elements=["id"])
            await sess.execute(stmt)
            count += 1
    COUNTERS["agent_templates"] = count
    print(f"  ✓ agent_templates: {count}")


async def migrate_workflows():
    workflows = _load_json(WORKFLOWS_FILE)
    if not workflows:
        print("  multi_agent_workflows.json: empty or missing, skipping.")
        return
    count = 0
    async with _session() as sess:
        for run_id, wf in workflows.items():
            # Build state_json blob from all heavy nested fields
            state = {
                k: wf.get(k) for k in [
                    "tasks", "debug_logs", "artifacts", "plan",
                    "notifications", "completed_tasks", "failed_tasks",
                    "running_tasks", "ready_queue", "collaborative_chat",
                ]
            }
            stmt = sqlite_insert(WorkflowRow).values(
                run_id=run_id,
                run_title=wf.get("run_title"),
                run_icon=wf.get("run_icon"),
                user_id=wf.get("user_id"),
                user_request=wf.get("user_request"),
                status=wf.get("status"),
                final_result=wf.get("final_result"),
                storage_dir=wf.get("storage_dir"),
                created_at=wf.get("created_at"),
                started_at=wf.get("started_at"),
                completed_at=wf.get("completed_at"),
                state_json=j_dumps(state),
            ).on_conflict_do_nothing(index_elements=["run_id"])
            await sess.execute(stmt)
            count += 1
    COUNTERS["workflows"] = count
    print(f"  ✓ workflows: {count}")


async def migrate_insights():
    rows = _load_jsonl(INSIGHTS_FILE)
    if not rows:
        print("  insights.jsonl: empty or missing, skipping.")
        return
    count = 0
    async with _session() as sess:
        for row in rows:
            sess.add(InsightRow(
                timestamp=row.get("timestamp"),
                model=row.get("model"),
                source=row.get("source"),
                session_id=row.get("session_id"),
                tps=row.get("tps"),
                ttft=row.get("ttft"),
                prompt_tokens=row.get("prompt_tokens"),
                completion_tokens=row.get("completion_tokens"),
                context_used=row.get("context_used"),
            ))
            count += 1
    COUNTERS["insights"] = count
    print(f"  ✓ insights: {count}")


# ─────────────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  Kokomi JSON → SQLite Migration")
    print("=" * 60)
    print(f"\n  DB path: data/database/kokomi.db")
    print("  Initializing database schema...")
    await init_db()
    print("  Schema OK.\n")

    print("  Migrating data:")
    await migrate_conversations()
    await migrate_characters()
    await migrate_mcp_servers()
    await migrate_folders()
    await migrate_spaces()
    await migrate_agent_templates()
    await migrate_workflows()
    await migrate_insights()

    print("\n" + "=" * 60)
    print("  Migration complete!")
    print("=" * 60)
    print("\n  Summary:")
    for table, count in COUNTERS.items():
        print(f"    {table:25s}: {count} records")
    print("\n  ✅ JSON files left untouched as backups.")
    print("  ✅ You can delete them manually after verifying the app works.\n")


if __name__ == "__main__":
    asyncio.run(main())
