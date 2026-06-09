"""
Kokomi Memory Engine v2 — Weighted Decay with Profile Synthesis
═══════════════════════════════════════════════════════════════

Memory atoms are stored in Qdrant with:
  - importance  : float 1.0–5.0 (assigned by extraction LLM)
  - created_at  : timestamp of first insertion
  - last_accessed: timestamp of last retrieval hit
  - access_count : how many times this atom was surfaced
  - source       : "auto" | "manual_entry" | "tool"

Dedup: Before saving, a vector similarity check is performed.
  If cosine similarity > DEDUP_THRESHOLD, the existing atom is
  *merged* (importance bumped, timestamp refreshed) instead of
  creating a duplicate.

Decay: A background sweep (or lazy on-read) halves the importance
  of atoms below DECAY_IMPORTANCE_FLOOR that haven't been accessed
  in DECAY_DAYS. Atoms below PRUNE_THRESHOLD are deleted.

Profile Synthesis: Every PROFILE_REFRESH_INTERVAL conversations,
  all high-importance atoms for a character are consolidated into
  a single structured "relationship profile" paragraph stored as
  a JSON file.
"""

import uuid
import datetime
import json
import os
import time
import threading
from functools import lru_cache
from typing import Dict, List, Optional, Any
from collections import OrderedDict
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue
from app.rag import qdrant, get_embeddings, ensure_collection
from app.llm import get_llm
from langchain_core.messages import HumanMessage, SystemMessage
from app.config import DATA_DIR

# ── Constants ────────────────────────────────────────────────────────
MEMORY_COLLECTION = "user_memories"
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
os.makedirs(PROFILES_DIR, exist_ok=True)

DEDUP_THRESHOLD = 0.85        # Cosine similarity above this = duplicate
DECAY_DAYS = 7                # Days without access before decay kicks in
DECAY_IMPORTANCE_FLOOR = 3.0  # Only atoms below this importance decay
PRUNE_THRESHOLD = 0.5         # Atoms below this importance get deleted
ACCESS_BOOST = 0.1            # Importance bump per retrieval hit
MAX_IMPORTANCE = 5.0
PROFILE_REFRESH_INTERVAL = 10 # Re-synthesize profile every N save_memory calls

# ── In-Memory LRU Cache ─────────────────────────────────────────────
# character_id -> OrderedDict of query_hash -> (results, timestamp)
_memory_cache: Dict[str, OrderedDict] = {}
_cache_lock = threading.Lock()
_CACHE_MAX_SIZE = 50
_CACHE_TTL_SECONDS = 300  # 5 minutes

# Track save counts per character for profile refresh triggers
_save_counts: Dict[str, int] = {}


def _cache_key(query: str) -> str:
    """Simple hash for cache lookup."""
    return str(hash(query))


def _get_cached(character_id: str, query: str) -> Optional[List[str]]:
    """Check the LRU cache for a previous result."""
    with _cache_lock:
        char_cache = _memory_cache.get(character_id)
        if not char_cache:
            return None
        key = _cache_key(query)
        entry = char_cache.get(key)
        if entry is None:
            return None
        results, ts = entry
        if time.time() - ts > _CACHE_TTL_SECONDS:
            del char_cache[key]
            return None
        # Move to end (most recently used)
        char_cache.move_to_end(key)
        return results


def _set_cached(character_id: str, query: str, results: List[str]):
    """Store results in the LRU cache."""
    with _cache_lock:
        if character_id not in _memory_cache:
            _memory_cache[character_id] = OrderedDict()
        char_cache = _memory_cache[character_id]
        key = _cache_key(query)
        char_cache[key] = (results, time.time())
        # Evict oldest if over capacity
        while len(char_cache) > _CACHE_MAX_SIZE:
            char_cache.popitem(last=False)


def invalidate_cache(character_id: str):
    """Clear cache for a character when new memories are saved."""
    with _cache_lock:
        _memory_cache.pop(character_id, None)


# ── Core Memory Operations ──────────────────────────────────────────

def save_memory(character_id: str, text: str, metadata: dict = None,
                importance: float = 3.0):
    """
    Save a memory atom with dedup-on-write and importance scoring.
    
    If a semantically similar atom already exists (cosine > DEDUP_THRESHOLD),
    the existing atom is merged instead of creating a duplicate.
    """
    text = text.strip()
    if not text or len(text) < 5:
        return
    
    embeddings_model = get_embeddings(task_type="RETRIEVAL_DOCUMENT")
    vector = embeddings_model.embed_query(text)
    
    ensure_collection(MEMORY_COLLECTION, len(vector))
    
    # ── Dedup Check ──────────────────────────────────────────────
    try:
        existing = qdrant.query_points(
            collection_name=MEMORY_COLLECTION,
            query=vector,
            query_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=character_id))]
            ),
            limit=3,
            score_threshold=DEDUP_THRESHOLD
        )
        
        if existing.points:
            # Merge with the closest match
            best = existing.points[0]
            old_importance = best.payload.get("importance", 3.0)
            old_access_count = best.payload.get("access_count", 0)
            
            # Bump importance slightly and refresh timestamp
            new_importance = min(old_importance + 0.2, MAX_IMPORTANCE)
            
            updated_payload = {
                **best.payload,
                "importance": new_importance,
                "last_accessed": datetime.datetime.now().timestamp(),
                "access_count": old_access_count + 1,
            }
            
            # If the new text is longer/more detailed, replace the text
            old_text = best.payload.get("text", "")
            if len(text) > len(old_text) + 10:
                updated_payload["text"] = text
                # Re-embed with the new text
                vector = embeddings_model.embed_query(text)
            
            qdrant.upsert(
                collection_name=MEMORY_COLLECTION,
                points=[PointStruct(
                    id=best.id,
                    vector=vector,
                    payload=updated_payload
                )]
            )
            invalidate_cache(character_id)
            _maybe_refresh_profile(character_id)
            return
            
    except Exception as e:
        # If dedup check fails, proceed with normal insert
        print(f"Dedup check failed (non-fatal): {e}")
    
    # ── Fresh Insert ─────────────────────────────────────────────
    now = datetime.datetime.now().timestamp()
    point_id = str(uuid.uuid4())
    payload = {
        "user_id": character_id,
        "text": text,
        "importance": min(max(importance, 1.0), MAX_IMPORTANCE),
        "created_at": now,
        "last_accessed": now,
        "access_count": 0,
        "source": (metadata or {}).get("source", "auto"),
        **(metadata or {})
    }
    
    qdrant.upsert(
        collection_name=MEMORY_COLLECTION,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)]
    )
    
    invalidate_cache(character_id)
    _maybe_refresh_profile(character_id)


def search_memories(character_id: str, query: str, limit: int = 5) -> List[str]:
    """
    Semantic search with access-boosted retrieval and LRU caching.
    
    Results are sorted by (cosine_score * importance) to surface
    the most relevant AND important memories first.
    """
    # Check cache first
    cached = _get_cached(character_id, query)
    if cached is not None:
        return cached
    
    try:
        embeddings_model = get_embeddings(task_type="RETRIEVAL_QUERY")
        query_vector = embeddings_model.embed_query(query)
        
        # Fetch more candidates than needed, then re-rank by importance
        results = qdrant.query_points(
            collection_name=MEMORY_COLLECTION,
            query=query_vector,
            query_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=character_id))]
            ),
            limit=limit * 3,  # Over-fetch for re-ranking
            score_threshold=0.30
        )
        
        if not results.points:
            _set_cached(character_id, query, [])
            return []
        
        # Re-rank: weighted score = cosine_score * (importance / 5.0)
        scored = []
        for hit in results.points:
            if not hit.payload:
                continue
            cosine = hit.score
            importance = hit.payload.get("importance", 3.0)
            weighted = cosine * (0.5 + (importance / MAX_IMPORTANCE) * 0.5)
            scored.append((weighted, hit))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        top_hits = scored[:limit]
        
        # Access-boost: update last_accessed and bump importance for retrieved atoms
        _boost_accessed([hit for _, hit in top_hits])
        
        texts = [hit.payload.get("text") for _, hit in top_hits if hit.payload]
        _set_cached(character_id, query, texts)
        return texts
        
    except Exception as e:
        print(f"Memory search error: {e}")
        return []


def _boost_accessed(hits):
    """Background-boost importance and refresh last_accessed for retrieved atoms."""
    try:
        now = datetime.datetime.now().timestamp()
        points_to_update = []
        for hit in hits:
            if not hit.payload:
                continue
            old_importance = hit.payload.get("importance", 3.0)
            new_importance = min(old_importance + ACCESS_BOOST, MAX_IMPORTANCE)
            updated = {
                **hit.payload,
                "importance": new_importance,
                "last_accessed": now,
                "access_count": hit.payload.get("access_count", 0) + 1,
            }
            points_to_update.append((hit.id, updated))
        
        if points_to_update:
            # Use set_payload for efficiency (no vector re-upload needed)
            for pid, payload in points_to_update:
                qdrant.set_payload(
                    collection_name=MEMORY_COLLECTION,
                    payload=payload,
                    points=[pid]
                )
    except Exception as e:
        print(f"Access boost error (non-fatal): {e}")


# ── Decay Engine ────────────────────────────────────────────────────

def run_decay_sweep(character_id: Optional[str] = None):
    """
    Sweep all memory atoms and apply decay rules:
      - Atoms below DECAY_IMPORTANCE_FLOOR that haven't been accessed
        in DECAY_DAYS get their importance halved.
      - Atoms below PRUNE_THRESHOLD are deleted.
    
    Can be run globally or for a specific character.
    """
    try:
        filter_conditions = []
        if character_id:
            filter_conditions.append(
                FieldCondition(key="user_id", match=MatchValue(value=character_id))
            )
        
        scroll_filter = Filter(must=filter_conditions) if filter_conditions else None
        
        results, _ = qdrant.scroll(
            collection_name=MEMORY_COLLECTION,
            scroll_filter=scroll_filter,
            limit=500
        )
        
        now = datetime.datetime.now().timestamp()
        cutoff = now - (DECAY_DAYS * 86400)
        
        to_delete = []
        to_update = []
        
        for point in results:
            if not point.payload:
                continue
                
            importance = point.payload.get("importance", 3.0)
            last_accessed = point.payload.get("last_accessed", point.payload.get("timestamp", now))
            
            # High-importance atoms never decay
            if importance >= DECAY_IMPORTANCE_FLOOR:
                continue
            
            # Check if stale
            if last_accessed < cutoff:
                new_importance = importance * 0.5
                
                if new_importance < PRUNE_THRESHOLD:
                    to_delete.append(point.id)
                else:
                    to_update.append((point.id, {
                        **point.payload,
                        "importance": round(new_importance, 2)
                    }))
        
        # Apply updates
        for pid, payload in to_update:
            qdrant.set_payload(
                collection_name=MEMORY_COLLECTION,
                payload=payload,
                points=[pid]
            )
        
        # Delete pruned atoms
        if to_delete:
            qdrant.delete(
                collection_name=MEMORY_COLLECTION,
                points_selector=to_delete
            )
        
        pruned_chars = set()
        for point in results:
            if point.id in to_delete and point.payload:
                pruned_chars.add(point.payload.get("user_id"))
        for cid in pruned_chars:
            if cid:
                invalidate_cache(cid)
        
        if to_delete or to_update:
            print(f"[Memory Decay] Decayed {len(to_update)} atoms, pruned {len(to_delete)} atoms")
            
    except Exception as e:
        print(f"Decay sweep error: {e}")


# ── Profile Synthesis ───────────────────────────────────────────────

def _maybe_refresh_profile(character_id: str):
    """Trigger profile re-synthesis every N memory saves."""
    _save_counts[character_id] = _save_counts.get(character_id, 0) + 1
    if _save_counts[character_id] % PROFILE_REFRESH_INTERVAL == 0:
        # Run in a background thread to avoid blocking
        threading.Thread(
            target=_synthesize_profile_sync,
            args=(character_id,),
            daemon=True
        ).start()


def _get_profile_path(character_id: str) -> str:
    return os.path.join(PROFILES_DIR, f"{character_id}.json")


def get_character_profile(character_id: str) -> Optional[str]:
    """Read the synthesized profile paragraph for a character."""
    path = _get_profile_path(character_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("profile")
    except Exception:
        return None


def _synthesize_profile_sync(character_id: str):
    """
    Consolidate all high-importance atoms for a character into
    a structured relationship profile paragraph.
    """
    try:
        # Fetch all atoms for this character
        results, _ = qdrant.scroll(
            collection_name=MEMORY_COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=character_id))]
            ),
            limit=200
        )
        
        if not results:
            return
        
        # Sort by importance descending, take top atoms
        atoms = []
        for point in results:
            if not point.payload:
                continue
            atoms.append({
                "text": point.payload.get("text", ""),
                "importance": point.payload.get("importance", 3.0),
                "access_count": point.payload.get("access_count", 0),
            })
        
        atoms.sort(key=lambda a: a["importance"], reverse=True)
        
        # Take top 30 most important atoms
        top_atoms = atoms[:30]
        if not top_atoms:
            return
        
        atoms_text = "\n".join(
            f"- [{a['importance']:.1f}★] {a['text']}" for a in top_atoms
        )
        
        from app.storage import load_prefs
        prefs = load_prefs()
        
        import asyncio
        
        async def _do_synthesis():
            llm = get_llm(prefs)
            prompt = f"""You are a relationship intelligence engine. Based on these memory atoms about a user (sorted by importance, rated 1-5★), synthesize a concise, structured profile paragraph.

MEMORY ATOMS:
{atoms_text}

Write a single cohesive paragraph (max 200 words) that captures:
1. Who the user is (identity, role, expertise)
2. Their key preferences and communication style
3. Important personal details worth remembering
4. The nature and depth of the relationship

Output ONLY the profile paragraph, no headers or formatting. Write in third person ("The user...").
If there's limited data, write a shorter paragraph with what's available."""

            response = await llm.ainvoke([
                SystemMessage(content="You are a memory consolidation engine. Output only the requested profile paragraph."),
                HumanMessage(content=prompt)
            ])
            return (response.content or "").strip()
        
        # Run the async synthesis
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    profile_text = pool.submit(
                        asyncio.run, _do_synthesis()
                    ).result(timeout=30)
            else:
                profile_text = asyncio.run(_do_synthesis())
        except Exception:
            profile_text = asyncio.run(_do_synthesis())
        
        if profile_text and len(profile_text) > 20:
            profile_data = {
                "character_id": character_id,
                "profile": profile_text,
                "atom_count": len(atoms),
                "top_importance": top_atoms[0]["importance"] if top_atoms else 0,
                "synthesized_at": datetime.datetime.now().isoformat(),
            }
            
            path = _get_profile_path(character_id)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(profile_data, f, indent=2)
            
            print(f"[Profile] Synthesized profile for character '{character_id}' ({len(atoms)} atoms → {len(profile_text)} chars)")
    
    except Exception as e:
        print(f"Profile synthesis error: {e}")


# ── Conversation Fact Extraction ────────────────────────────────────

async def summarize_conversation(messages: list, prefs: dict = None):
    """
    Extract key facts from chat history WITH importance scoring.
    Returns list of dicts: [{"fact": str, "importance": float}, ...]
    """
    if not messages or len(messages) < 2:
        return []
        
    llm = get_llm(prefs or {})
    
    chat_text = ""
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        chat_text += f"{role.upper()}: {content}\n\n"
        
    prompt = f"""Analyze the following conversation and extract persistent facts about the user.
Focus ONLY on information that would be useful for future conversations.

For each fact, assign an importance score from 1.0 to 5.0:
  5.0 = Critical identity/safety info (allergies, medical, name, profession)
  4.0 = Strong preferences or important life details (relationships, pets, hobbies)
  3.0 = Notable preferences (favorite tools, communication style)
  2.0 = Casual mentions worth remembering (mentioned a trip, current project)
  1.0 = Trivial/ephemeral (mentioned the weather, passing comment)

CONVERSATION:
{chat_text}

Return a JSON array of objects: [{{"fact": "...", "importance": 4.2}}, ...]
If no important facts were shared, return [].
Only extract genuinely useful facts, not conversation mechanics.""" 
    
    try:
        response = await llm.ainvoke([
            SystemMessage(content="You are a memory extraction engine. Return ONLY a JSON array of objects with 'fact' and 'importance' keys."),
            HumanMessage(content=prompt)
        ])
        
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
            
        facts = json.loads(content)
        if not isinstance(facts, list):
            return []
        
        # Validate and normalize
        validated = []
        for item in facts:
            if isinstance(item, dict) and "fact" in item:
                imp = float(item.get("importance", 3.0))
                imp = min(max(imp, 1.0), 5.0)
                validated.append({"fact": item["fact"], "importance": imp})
            elif isinstance(item, str):
                # Backward compat: plain string facts get default importance
                validated.append({"fact": item, "importance": 3.0})
        
        return validated
        
    except Exception as e:
        print(f"Summarization error: {e}")
        return []
