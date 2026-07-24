"""
app/migration.py — Automatic migration and relocation for legacy JSON files.

Detects if the user has database JSONs in the root of data/, runs the SQLite
migration, and relocates flat JSONs to their proper target directories
(active config to data/json/, historical files to data/json/backups/).
"""
import os
import shutil
import json
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import select

from app.db import (
    _session, j_dumps, j_loads,
    ConversationRow, MessageRow, CharacterRow, McpServerRow,
    SpaceRow, AgentTemplateRow, FolderRow, WorkflowRow, InsightRow
)

def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except Exception:
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


def migrate_embedding_model():
    """One-time: rewrite the old, unstable default embedding model to the stable
    GA model. The previous default `models/gemini-embedding-2` drifts over time,
    silently invalidating stored vectors; `gemini-embedding-001` is stable.
    Idempotent — safe to run every startup."""
    try:
        from app.storage import load_prefs, save_prefs
        prefs = load_prefs()
        if prefs.get("embedding_model") == "models/gemini-embedding-2":
            prefs["embedding_model"] = "gemini-embedding-001"
            save_prefs(prefs)
            print("🔧 Migrated embedding_model → gemini-embedding-001 (stable). "
                  "Existing knowledge spaces will need re-indexing.")
    except Exception as e:
        print(f"Embedding model migration skipped: {e}")


def migrate_local_provider_to_custom():
    """One-time: the "local" provider (llama.cpp/Ollama, no auth) became "custom"
    (any OpenAI-compatible endpoint, requiring a base URL + API key). Carries
    over prefs saved under the old `local_*` keys to the new `custom_*` keys
    for the chat, title and Atlas provider slots. Idempotent — safe to run
    every startup."""
    try:
        from app.storage import load_prefs, save_prefs
        prefs = load_prefs()
        changed = False

        renames = [
            ("llm_provider", "local_url", "local_model", "custom_base_url", "custom_model"),
            ("atlas_llm_provider", "atlas_local_url", "atlas_local_model", "atlas_custom_base_url", "atlas_custom_model"),
            ("title_llm_provider", "title_local_url", "title_local_model", "title_custom_base_url", "title_custom_model"),
        ]
        for provider_key, old_url_key, old_model_key, new_url_key, new_model_key in renames:
            if prefs.get(provider_key) == "local":
                prefs[provider_key] = "custom"
                changed = True
            if old_url_key in prefs:
                prefs.setdefault(new_url_key, prefs[old_url_key])
                del prefs[old_url_key]
                changed = True
            if old_model_key in prefs:
                prefs.setdefault(new_model_key, prefs[old_model_key])
                del prefs[old_model_key]
                changed = True

        if changed:
            save_prefs(prefs)
            print("🔧 Migrated 'local' provider prefs → 'custom' (now requires an API key).")
    except Exception as e:
        print(f"Local-to-custom provider migration skipped: {e}")


async def auto_migrate_and_cleanup():
    # Legacy files in data/
    DATA_DIR = "data"
    legacy_files = {
        "conversations": os.path.join(DATA_DIR, "conversations.json"),
        "characters": os.path.join(DATA_DIR, "characters.json"),
        "mcp_servers": os.path.join(DATA_DIR, "mcp_servers.json"),
        "folders": os.path.join(DATA_DIR, "folders.json"),
        "spaces": os.path.join(DATA_DIR, "spaces.json"),
        "agent_templates": os.path.join(DATA_DIR, "agent_templates.json"),
        "workflows": os.path.join(DATA_DIR, "multi_agent_workflows.json"),
        "insights": os.path.join(DATA_DIR, "insights.jsonl"),
        "simple_workflows": os.path.join(DATA_DIR, "workflows.json"),
        "user_prefs": os.path.join(DATA_DIR, "user_prefs.json"),
    }

    # Check if any legacy database files exist in data/ root
    exists = any(os.path.exists(path) for path in legacy_files.values())
    if not exists:
        return

    print("🌊 Auto-migration: Detecting legacy flat JSON database files in data/ root. Initiating migration...")
    
    # 1. Create target json directories
    JSON_DIR = os.path.join(DATA_DIR, "json")
    BACKUP_DIR = os.path.join(JSON_DIR, "backups")
    os.makedirs(BACKUP_DIR, exist_ok=True)

    async with _session() as sess:
        # Conversations & Messages
        conv_file = legacy_files["conversations"]
        if os.path.exists(conv_file):
            convos = _load_json(conv_file)
            for conv_id, conv in convos.items():
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

                existing_msgs = await sess.execute(
                    select(MessageRow.id).where(MessageRow.conversation_id == conv_id).limit(1)
                )
                if existing_msgs.first() is None:
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

        # Characters
        char_file = legacy_files["characters"]
        if os.path.exists(char_file):
            chars = _load_json(char_file)
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
                    custom_model=char.get("custom_model", char.get("local_model", "default")),
                    mcp_servers=j_dumps(char.get("mcp_servers", [])),
                    selected_tools=j_dumps(char.get("selected_tools", [])),
                    created_at=char.get("created_at"),
                ).on_conflict_do_nothing(index_elements=["id"])
                await sess.execute(stmt)

        # MCP Servers
        mcp_file = legacy_files["mcp_servers"]
        if os.path.exists(mcp_file):
            mcps = _load_json(mcp_file)
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

        # Folders
        folder_file = legacy_files["folders"]
        if os.path.exists(folder_file):
            folders = _load_json(folder_file)
            for fid, f in folders.items():
                stmt = sqlite_insert(FolderRow).values(
                    id=fid,
                    name=f.get("name", ""),
                    icon=f.get("icon"),
                    conversation_ids=j_dumps(f.get("conversation_ids", [])),
                    created_at=f.get("created_at"),
                ).on_conflict_do_nothing(index_elements=["id"])
                await sess.execute(stmt)

        # Spaces
        space_file = legacy_files["spaces"]
        if os.path.exists(space_file):
            spaces = _load_json(space_file)
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

        # Agent Templates
        template_file = legacy_files["agent_templates"]
        if os.path.exists(template_file):
            templates = _load_json(template_file)
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

        # Workflows
        wf_file = legacy_files["workflows"]
        if os.path.exists(wf_file):
            workflows = _load_json(wf_file)
            for run_id, wf in workflows.items():
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

        # Insights
        insights_file = legacy_files["insights"]
        if os.path.exists(insights_file):
            rows = _load_jsonl(insights_file)
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

    # 2. File Relocation to targets
    # A. Active configs go to data/json/
    active_configs = {
        "user_prefs": legacy_files["user_prefs"],
        "simple_workflows": legacy_files["simple_workflows"],
    }
    for name, src in active_configs.items():
        if os.path.exists(src):
            dst = os.path.join(JSON_DIR, os.path.basename(src))
            try:
                shutil.move(src, dst)
            except Exception as move_err:
                print(f"  ⚠ Could not move {src} to {dst}: {move_err}")

    # B. Migrated historical databases go to data/json/backups/
    migrated_files = [
        legacy_files["conversations"],
        legacy_files["characters"],
        legacy_files["mcp_servers"],
        legacy_files["folders"],
        legacy_files["spaces"],
        legacy_files["agent_templates"],
        legacy_files["workflows"],
        legacy_files["insights"],
    ]
    for src in migrated_files:
        if os.path.exists(src):
            dst = os.path.join(BACKUP_DIR, os.path.basename(src))
            try:
                shutil.move(src, dst)
            except Exception as move_err:
                print(f"  ⚠ Could not move {src} to {dst}: {move_err}")

    print("🌊 Auto-migration and JSON cleanup completed successfully!")
